"""
Upload router — secure file upload with validation and OCR trigger.
Integrates with ocr_service and feeds results into extracted_data table.
Delegates batch and background processing to helper modules.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Annotated, Any, Optional
from pydantic import BaseModel

from fastapi import APIRouter, BackgroundTasks, Body, Depends, File, HTTPException, Request, UploadFile, status
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import require_roles
from app.models.enums import FileStatus, JobStatus, LogEventType, UserRole, ExtractionStatus
from app.models.extracted_data import ExtractedData
from app.models.file import UploadedFile
from app.models.job import OCRJob
from app.services.audit_service import write_audit_log
from app.services.ownership_service import get_owned_ocr_job, is_superadmin, is_org_admin
from app.services.correction_memory import (
    apply_user_corrections,
    build_correction_memory_records,
    recompute_final_decision_after_corrections,
    resolve_company_for_correction_memory,
)
from app.services.source_trace import calculate_review_summary

import zipfile

# Re-export constants and helper functions used by tests and other modules
from app.api.v1.routers.upload_helpers import (  # noqa: F401
    ALLOWED_MIMES,
    cleanup_expired_batch_zips,
    _safe_zip_stem,
    _build_detection_report,
    _ai_fallback_audit_metadata,
    _ai_fallback_target_fields,
    get_owned_ocr_batch,
    _batch_zip_payload,
    _tenant_limit_source,
    _batch_child_rows,
    _refresh_batch_counters,
    _should_attempt_ai_fallback,
    MAX_BATCH_FILES,
    # Caches and internal helpers
    OCR_DEMO_JOBS,
    _prepare_upload_file,
    _store_prepared_upload,
    _file_category_for_mime,
    _empty_ocr_response,
    _get_memory_job_or_404,
    _require_canonical_fields,
    _review_payload,
    _apply_final_decision_to_job,
)
from app.api.v1.routers.upload_batch import (  # noqa: F401
    upload_batch,
    get_upload_batch,
    export_batch_zip,
    download_batch_zip,
)
from app.api.v1.routers.upload_background import _ocr_background  # noqa: F401

router = APIRouter(prefix="/upload", tags=["Upload"])
settings = get_settings()


@router.post(
    "/",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload a PDF or image and trigger async OCR",
    description=(
        "Validates file via magic bytes (not extension), stores it, "
        "and triggers async OCR.\n\n"
        "**Supported:** PDF, PNG, JPEG, TIFF, BMP, WebP\n"
        f"**Max size:** {settings.MAX_FILE_SIZE_MB} MB"
    ),
)
async def upload_file(
    background_tasks: BackgroundTasks,
    file: Annotated[UploadFile, File(description="PDF or image file")],
    request: Request,
    current_user: dict = Depends(require_roles(UserRole.OPERATOR)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.services import job_queue

    prepared = await _prepare_upload_file(file)
    data = prepared["data"]
    safe_name = prepared["safe_name"]
    detected = prepared["detected"]
    logger.info(
        "Upload received: user={} filename={} content_type={} detected_mime={} size={}",
        current_user["user_id"],
        safe_name,
        prepared["content_type"],
        detected,
        len(data),
    )

    stored_info = await _store_prepared_upload(prepared)
    checksum = stored_info["checksum"]
    stored = stored_info["stored"]
    dest = stored_info["dest"]
    upload_save_ms = stored_info["upload_save_ms"]

    user_id = uuid.UUID(current_user["user_id"])
    org_id = uuid.UUID(current_user["org_id"]) if current_user.get("org_id") else None

    idempotency_key = job_queue.build_idempotency_key(user_id, checksum)

    # Idempotency check:
    stmt = (
        select(OCRJob)
        .join(UploadedFile, OCRJob.file_id == UploadedFile.id)
        .where(
            UploadedFile.checksum_sha256 == checksum,
            UploadedFile.uploaded_by_id == user_id,
            OCRJob.status.in_([
                JobStatus.QUEUED.value,
                JobStatus.PROCESSING.value,
                JobStatus.COMPLETED.value,
            ])
        )
        .order_by(OCRJob.queued_at.desc())
    )
    res = await db.execute(stmt)
    existing_job = res.scalars().first()
    if existing_job:
        existing_job_id = str(existing_job.id)
        logger.info("Idempotency hit: job={} matches checksum={} for user={}", existing_job_id, checksum[:16], user_id)
        return await get_upload_job(existing_job_id, current_user, db)

    job_uuid = uuid.uuid4()
    job_id = str(job_uuid)
    file_uuid = uuid.uuid4()
    
    await job_queue.enqueue(
        job_id=job_uuid,
        file_id=file_uuid,
        created_by_id=user_id,
        org_id=org_id,
        filename=safe_name,
        mime_type=detected,
        file_size=len(data),
        checksum=checksum,
        db=db,
        storage_path=str(dest),
        stored_filename=stored,
        upload_save_ms=upload_save_ms,
        request=request,
    )

    background_tasks.add_task(_ocr_background, str(dest), detected, job_id, checksum)

    logger.info(f"Upload accepted: user={current_user['user_id']} file={safe_name} job={job_id} upload_save_ms={upload_save_ms}")
    
    job_data = OCR_DEMO_JOBS[job_id]
    return {
        "job_id":          job_id,
        "status":          job_data["status"],
        "success":         True,
        "filename":        safe_name,
        "stored_as":       stored,
        "mime_type":       detected,
        "file_size":       len(data),
        "checksum_sha256": checksum,
        "uploaded_at":     job_data["uploaded_at"],
        "created_at":      job_data.get("created_at"),
        "updated_at":      job_data.get("updated_at"),
        "progress_percent": job_data.get("progress_percent", 0),
        "current_step":    job_data.get("current_step", "queued"),
        "retry_count":     job_data.get("retry_count", 0),
        "max_retries":     job_data.get("max_retries", 3),
        "error_code":      job_data.get("error_code"),
        "error_message":   job_data.get("error_message"),
        "retryable":       job_data.get("retryable", False),
        "idempotency_key": idempotency_key,
        "message":         "File accepted. OCR running in background.",
        "result_url":      f"/api/v1/upload/jobs/{job_id}",
        "text":            "",
        "raw_text_length":  0,
        "detected_line_count": 0,
        "raw_text_preview": "",
        "fields":          {},
        "confidence":      {},
        "warnings":        [],
        "duplicate_detection": {
            "status": "not_duplicate",
            "confidence": 0.0,
            "matched_job_id": None,
            "matched_document_name": None,
            "matched_created_at": None,
            "reasons": [],
            "blocking": False,
            "review_required": False
        },
        "ocr_response":    _empty_ocr_response(),
    }


@router.get("/jobs/{job_id}/review", summary="Get OCR review fields")
async def get_upload_job_review(
    job_id: str,
    current_user: dict = Depends(require_roles(UserRole.OPERATOR, UserRole.REVIEWER)),
) -> dict:
    job = _get_memory_job_or_404(job_id, current_user)
    _require_canonical_fields(job)
    return _review_payload(job_id, job)


@router.post("/jobs/{job_id}/corrections", summary="Apply OCR field corrections")
async def apply_upload_job_corrections(
    job_id: str,
    body: dict[str, Any] = Body(...),
    current_user: dict = Depends(require_roles(UserRole.OPERATOR, UserRole.REVIEWER)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    job = _get_memory_job_or_404(job_id, current_user)
    canonical_fields_before = _require_canonical_fields(job)

    corrections = body.get("corrections") if isinstance(body, dict) else None
    if not isinstance(corrections, dict) or not corrections:
        raise HTTPException(status_code=400, detail="corrections must be a non-empty object")

    applied = apply_user_corrections(canonical_fields_before, corrections)
    if applied["unknown_fields"]:
        raise HTTPException(
            status_code=400,
            detail={"unknown_fields": applied["unknown_fields"]},
        )

    decision = recompute_final_decision_after_corrections(applied["canonical_fields"])
    _apply_final_decision_to_job(job, decision)
    job["correction_applied_fields"] = applied["applied_fields"]
    job["correction_invalid_fields"] = applied["invalid_fields"]
    job["correction_updated_at"] = datetime.now(timezone.utc).isoformat()

    warnings: list[str] = []
    memory_saved_count = 0
    if bool(body.get("save_to_memory", False)):
        try:
            db_job = await get_owned_ocr_job(db, uuid.UUID(job_id), current_user)
            detected_company = await resolve_company_for_correction_memory(db, job, canonical_fields_before)
            if isinstance(detected_company, dict):
                warnings.extend(str(w) for w in detected_company.get("warnings", []) if w)
            company_id = detected_company.get("company_id")
            if not company_id:
                warnings.append("Correction memory was not saved: company_id unavailable")
            else:
                records = build_correction_memory_records(
                    company_id=company_id,
                    template_id=db_job.template_id,
                    document_type=job.get("document_type"),
                    canonical_fields_before=canonical_fields_before,
                    corrections=corrections,
                    evidence_text=job.get("raw_text_preview") or job.get("raw_text", "")[:300],
                )
                if records:
                    db.add_all(records)
                    await db.commit()
                    memory_saved_count = len(records)
                else:
                    warnings.append("Correction memory was not saved: no valid records built")
        except Exception as exc:
            await db.rollback()
            warnings.append(f"Correction memory save failed: {exc}")

    response = _review_payload(job_id, job)
    response.update({
        "corrected_fields": applied["applied_fields"],
        "invalid_fields": applied["invalid_fields"],
        "memory_saved_count": memory_saved_count,
        "warnings": list(dict.fromkeys(warnings)),
        "detected_company": job.get("detected_company", {}),
    })
    return response


@router.post("/jobs/{job_id}/revalidate", summary="Revalidate OCR review fields")
async def revalidate_upload_job(
    job_id: str,
    current_user: dict = Depends(require_roles(UserRole.OPERATOR, UserRole.REVIEWER)),
) -> dict:
    job = _get_memory_job_or_404(job_id, current_user)
    canonical_fields = _require_canonical_fields(job)
    decision = recompute_final_decision_after_corrections(canonical_fields)
    _apply_final_decision_to_job(job, decision)
    return _review_payload(job_id, job)


@router.get("/jobs/{job_id}", summary="Get demo OCR job status/result")
async def get_upload_job(
    job_id: str,
    current_user: dict = Depends(require_roles(UserRole.OPERATOR, UserRole.REVIEWER)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.services import job_queue

    job = OCR_DEMO_JOBS.get(job_id)
    if job:
        if not is_superadmin(current_user) and job.get("owner_user_id") != current_user["user_id"]:
            raise HTTPException(status_code=404, detail="Upload job not found")
        dup_det = job.get("duplicate_detection") or {
            "status": "not_duplicate",
            "confidence": 0.0,
            "matched_job_id": None,
            "matched_document_name": None,
            "matched_created_at": None,
            "reasons": [],
            "blocking": False,
            "review_required": False
        }
        erp_intel = job.get("erp_intelligence")
        job["review_summary"] = calculate_review_summary(
            job.get("source_traces") or {},
            duplicate_detection=dup_det,
            erp_intelligence=erp_intel
        )
        
        # Expose standardized metrics and map completed -> review_required if needed
        progress_percent = job.get("progress_percent")
        if progress_percent is None:
            if job.get("status") in ("completed", "failed"):
                progress_percent = 100
            elif job.get("status") == "processing":
                progress_percent = 50
            else:
                progress_percent = 0

        current_step = job.get("current_step") or job.get("status") or "queued"
        retryable = job.get("retryable", False)
        error_code = job.get("error_code")
        error_message = job.get("error_message") or job.get("error")

        response = dict(job)
        response["progress_percent"] = progress_percent
        response["current_step"] = current_step
        response["retryable"] = retryable
        response["error_code"] = error_code
        response["error_message"] = error_message
        response["retry_count"] = int(response.get("retry_count", 0) or 0)
        response["max_retries"] = int(response.get("max_retries", 3) or 0)
        response["updated_at"] = response.get("updated_at") or datetime.now(timezone.utc).isoformat()

        if dup_det.get("blocking") or (erp_intel and erp_intel.get("blocking")):
            response["export_allowed"] = False
        if response.get("status") == "completed" and not response.get("export_allowed", True):
            response["status"] = "review_required"
        return response

    try:
        job_uuid = uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Invalid job_id format")

    db_job = await get_owned_ocr_job(db, job_uuid, current_user)

    extracted: ExtractedData | None = None
    if db_job.status in (JobStatus.COMPLETED.value, "completed"):
        result = await db.execute(
            select(ExtractedData).where(ExtractedData.job_id == job_uuid)
        )
        extracted = result.scalar_one_or_none()

    text = (extracted.raw_text or "") if extracted else ""
    mapped_fields: dict[str, Any] = {}
    if extracted and extracted.validated_fields:
        for k, v in extracted.validated_fields.items():
            if isinstance(v, dict):
                mapped_fields[k] = v
            else:
                mapped_fields[k] = {"value": str(v), "confidence": 0.5}
    extracted_fields = extracted.extracted_fields if extracted and isinstance(extracted.extracted_fields, dict) else {}
    canonical_fields = mapped_fields or extracted_fields
    line_items = extracted.line_items if extracted and isinstance(extracted.line_items, list) else []
    validation_items = extracted.validation_errors if extracted and isinstance(extracted.validation_errors, list) else []
    validation_errors = [e.get("error", "") for e in validation_items if isinstance(e, dict) and "error" in e]
    validation_warnings = [e.get("warning", "") for e in validation_items if isinstance(e, dict) and "warning" in e]
    low_confidence_fields = [
        key for key, value in canonical_fields.items()
        if isinstance(value, dict) and value.get("confidence") is not None and float(value.get("confidence") or 0) < 0.5
    ]
    missing_fields = [
        key for key, value in canonical_fields.items()
        if isinstance(value, dict) and not str(value.get("value") or "").strip()
    ]
    review_fields = [
        {"field_name": key, **value} if isinstance(value, dict) else {"field_name": key, "value": value}
        for key, value in canonical_fields.items()
    ]
    
    source_traces = {}
    if extracted and extracted.raw_ocr_json and isinstance(extracted.raw_ocr_json, dict):
        source_traces = extracted.raw_ocr_json.get("source_traces") or {}
    if extracted and not source_traces:
        from app.services.source_trace import build_source_traces
        trace_meta = {
            "ocr_engine": "restored_from_db",
            "training_template_id": extracted.raw_ocr_json.get("training_template_id"),
            "training_template_name": extracted.raw_ocr_json.get("training_template_name"),
            "training_conflicts": extracted.raw_ocr_json.get("training_conflicts") or [],
        }
        source_traces = build_source_traces(
            mapped_fields=extracted.extracted_fields or {},
            validated_fields=canonical_fields,
            validation_report={"errors": validation_errors, "warnings": validation_warnings},
            raw_mapped_fields=extracted.extracted_fields or {},
            extra_meta=trace_meta
        )
    # Retrieve duplicate detection from db_job config or extracted raw OCR json
    duplicate_detection_from_db = None
    if getattr(db_job, "ocr_config", None) and isinstance(db_job.ocr_config, dict):
        duplicate_detection_from_db = db_job.ocr_config.get("duplicate_detection")
    if not duplicate_detection_from_db and extracted and extracted.raw_ocr_json and isinstance(extracted.raw_ocr_json, dict):
        duplicate_detection_from_db = extracted.raw_ocr_json.get("duplicate_detection")
    if not duplicate_detection_from_db:
        duplicate_detection_from_db = {
            "status": "not_duplicate",
            "confidence": 0.0,
            "matched_job_id": None,
            "matched_document_name": None,
            "matched_created_at": None,
            "reasons": [],
            "blocking": False,
            "review_required": False
        }

    # Retrieve erp intelligence from db_job config or extracted raw OCR json
    erp_intelligence_from_db = None
    if getattr(db_job, "ocr_config", None) and isinstance(db_job.ocr_config, dict):
        erp_intelligence_from_db = db_job.ocr_config.get("erp_intelligence")
    if not erp_intelligence_from_db and extracted and extracted.raw_ocr_json and isinstance(extracted.raw_ocr_json, dict):
        erp_intelligence_from_db = extracted.raw_ocr_json.get("erp_intelligence")
    if not erp_intelligence_from_db:
        erp_intelligence_from_db = {
            "master_match": {
                "party_type": "unknown",
                "status": "no_master_match",
                "confidence": 0.0,
                "selected_master_id": "",
                "selected_master_name": "",
                "candidates": [],
                "reasons": ["Master matching skipped"],
                "review_required": False,
                "blocking": False
            },
            "po_grn_invoice_match": {
                "status": "not_applicable",
                "confidence": 0.0,
                "matched_job_id": None,
                "matched_document_name": None,
                "reasons": [],
                "review_required": False,
                "blocking": False
            },
            "tax_validation": {
                "status": "pass",
                "warnings": [],
                "export_blockers": [],
                "review_required": False
            },
            "review_required": False,
            "blocking": False,
            "warnings": [],
            "export_blockers": []
        }

    export_allowed = bool(extracted and extracted.status == ExtractionStatus.VALIDATED.value and not validation_errors)
    if duplicate_detection_from_db.get("blocking") or erp_intelligence_from_db.get("blocking"):
        export_allowed = False

    document_type = "general"
    if extracted and extracted.raw_ocr_json and isinstance(extracted.raw_ocr_json, dict):
        document_type = (
            extracted.raw_ocr_json.get("document_type")
            or extracted.raw_ocr_json.get("doc_type")
            or document_type
        )

    # Expose standardized metrics and map completed -> review_required if needed
    ocr_config = getattr(db_job, "ocr_config", None) or {}
    progress_percent = ocr_config.get("progress_percent")
    if progress_percent is None:
        if db_job.status in (JobStatus.COMPLETED.value, "completed"):
            progress_percent = 100
        elif db_job.status in (JobStatus.FAILED.value, "failed"):
            progress_percent = 100
        elif db_job.status in (JobStatus.PROCESSING.value, "processing"):
            progress_percent = 50
        else:
            progress_percent = 0

    current_step = ocr_config.get("current_step") or db_job.status or "queued"
    retryable = ocr_config.get("retryable", False)
    error_code = ocr_config.get("error_code")
    error_message = ocr_config.get("error_message") or getattr(db_job, "error_message", None)
    failed_at = ocr_config.get("failed_at")

    response_status = db_job.status
    if response_status in (JobStatus.COMPLETED.value, "completed") and not export_allowed:
        response_status = "review_required"
    if response_status in (JobStatus.FAILED.value, "failed") and not error_message:
        failure = job_queue.failure_payload(
            error_code=error_code or "OCR_FAILED",
            message=None,
            retryable=bool(retryable),
            failed_at=failed_at,
        )
        error_message = failure["error_message"]
        current_step = failure["current_step"]

    dup_warnings = []
    if duplicate_detection_from_db.get("status") != "not_duplicate":
        dup_reasons = ", ".join(duplicate_detection_from_db["reasons"])
        dup_warnings.append(f"Possible duplicate document detected: {dup_reasons}")

    return {
        "job_id": job_id,
        "status": response_status,
        "created_at": db_job.created_at.isoformat() if getattr(db_job, "created_at", None) else None,
        "started_at": db_job.started_at.isoformat() if getattr(db_job, "started_at", None) else None,
        "completed_at": db_job.completed_at.isoformat() if getattr(db_job, "completed_at", None) else None,
        "failed_at": failed_at,
        "updated_at": db_job.updated_at.isoformat() if getattr(db_job, "updated_at", None) else None,
        "progress_percent": progress_percent,
        "current_step": current_step,
        "retry_count": int(getattr(db_job, "retry_count", 0) or 0),
        "max_retries": int(getattr(db_job, "max_retries", 0) or 0),
        "retryable": retryable,
        "error_code": error_code,
        "error_message": error_message,
        "success": True,
        "filename": None,
        "extracted_data_id": str(extracted.id) if extracted else None,
        "ocr_engine": "restored_from_db",
        "raw_text": text,
        "text": text,
        "extracted_text": text,
        "full_text": text,
        "raw_text_length": len(text),
        "raw_text_preview": text[:300],
        "detected_line_count": len([l for l in text.splitlines() if l.strip()]),
        "mapped_fields": mapped_fields,
        "canonical_fields": canonical_fields,
        "review_fields": review_fields,
        "missing_fields": missing_fields,
        "low_confidence_fields": low_confidence_fields,
        "fields": {
            key: (value.get("value", "") if isinstance(value, dict) else value)
            for key, value in canonical_fields.items()
        },
        "confidence": {
            key: float(value.get("confidence", 0.0) or 0.0)
            for key, value in canonical_fields.items()
            if isinstance(value, dict)
        },
        "warnings": ["Job state restored from database (server was restarted)", *validation_warnings] + dup_warnings,
        "extraction_status": extracted.status if extracted else None,
        "validation_errors": validation_errors,
        "validation_warnings": validation_warnings,
        "document_type": document_type,
        "items": line_items,
        "line_items": line_items,
        "export_allowed": export_allowed,
        "blocked_fields": [] if export_allowed else [*missing_fields, *validation_errors] + (["duplicate_document"] if duplicate_detection_from_db.get("blocking") else []),
        "export_block_reasons": [] if export_allowed else [*missing_fields, *validation_errors] + (["Duplicate document blocks export"] if duplicate_detection_from_db.get("blocking") else []),
        "training_template_matched": extracted.raw_ocr_json.get("training_template_matched", False) if extracted else False,
        "training_template_name": extracted.raw_ocr_json.get("training_template_name") if extracted else None,
        "training_match_score": extracted.raw_ocr_json.get("training_match_score", 0.0) if extracted else 0.0,
        "training_template_id": extracted.raw_ocr_json.get("training_template_id") if extracted else None,
        "training_applied_fields": extracted.raw_ocr_json.get("training_applied_fields", []) if extracted else [],
        "training_conflicts": extracted.raw_ocr_json.get("training_conflicts", []) if extracted else [],
        "ocr_response": {
            "text": text,
            "fields": {
                key: (value.get("value", "") if isinstance(value, dict) else value)
                for key, value in canonical_fields.items()
            },
            "confidence": {
                key: float(value.get("confidence", 0.0) or 0.0)
                for key, value in canonical_fields.items()
                if isinstance(value, dict)
            },
            "warnings": validation_warnings + dup_warnings,
            "missing_fields": missing_fields,
            "low_confidence_fields": low_confidence_fields,
            "items": line_items,
            "source_traces": source_traces,
            "duplicate_detection": duplicate_detection_from_db,
            "erp_intelligence": erp_intelligence_from_db,
        },
        "source_traces": source_traces,
        "duplicate_detection": duplicate_detection_from_db,
        "erp_intelligence": erp_intelligence_from_db,
        "review_summary": calculate_review_summary(source_traces, duplicate_detection=duplicate_detection_from_db, erp_intelligence=erp_intelligence_from_db),
        "memory_applied_count": 0,
        "memory_suggestion_count": 0,
        "memory_applied_fields": [],
        "memory_suggestion_fields": [],
        "memory_suggestions": {},
        "memory_warnings": [],
        "_source": "db_fallback",
    }


class FieldActionRequest(BaseModel):
    field_name: str
    reviewer_note: Optional[str] = None

class FieldCorrectionRequest(BaseModel):
    field_name: str
    corrected_value: str
    is_reusable: bool = False
    reviewer_note: Optional[str] = None

async def get_extracted_data_and_demo_job(db: AsyncSession, job_id: str, current_user: dict):
    job = _get_memory_job_or_404(job_id, current_user)
    extracted = None
    try:
        job_uuid = uuid.UUID(job_id)
        result = await db.execute(select(ExtractedData).where(ExtractedData.job_id == job_uuid))
        extracted = result.scalar_one_or_none()
    except Exception:
        pass
    return job, extracted

async def apply_human_review_action(
    db: AsyncSession,
    job_id: str,
    field_name: str,
    user_action: str,
    reviewer_note: Optional[str] = None,
    corrected_value: Optional[str] = None,
    is_reusable: bool = False,
    current_user: dict = None,
):
    from app.services.source_trace import is_field_dynamic, is_field_reusable, build_source_traces, calculate_review_summary
    from app.services.correction_memory import recompute_final_decision_after_corrections, resolve_company_for_correction_memory
    from app.models.company import CorrectionMemory

    job, extracted = await get_extracted_data_and_demo_job(db, job_id, current_user)
    canonical_fields = _require_canonical_fields(job)
    
    if field_name not in canonical_fields:
        raise HTTPException(status_code=400, detail=f"Field '{field_name}' not found in canonical fields.")

    if user_action == "correct" and is_reusable:
        if is_field_dynamic(field_name):
            raise HTTPException(status_code=400, detail="Dynamic transaction fields cannot be saved as reusable training defaults.")
        if not is_field_reusable(field_name):
            raise HTTPException(status_code=400, detail="Reusable fields require explicit opt-in to be saved.")

    original_val = canonical_fields[field_name].get("value", "")
    if user_action == "correct":
        canonical_fields[field_name]["value"] = corrected_value
        canonical_fields[field_name]["method"] = "manual_correction"
        canonical_fields[field_name]["source"] = "user_correction"
        canonical_fields[field_name]["status"] = "AUTO_FILLED"
    elif user_action in ("reject", "not_available"):
        canonical_fields[field_name]["value"] = ""
        canonical_fields[field_name]["status"] = "MISSING"
        canonical_fields[field_name]["method"] = "manual_correction"
        canonical_fields[field_name]["source"] = "user_correction"

    user_reviews = job.setdefault("user_reviews", {})
    proposed_val = user_reviews.get(field_name, {}).get("proposed_value")
    if proposed_val is None:
        proposed_val = original_val
        
    user_reviews[field_name] = {
        "user_action": user_action,
        "reviewer_note": reviewer_note or "",
        "proposed_value": proposed_val,
        "final_value": canonical_fields[field_name]["value"]
    }
    
    if user_action == "correct" and is_reusable:
        company_res = await resolve_company_for_correction_memory(db, job, canonical_fields)
        company_id = company_res.get("company_id")
        if company_id:
            corr = CorrectionMemory(
                company_id=uuid.UUID(str(company_id)),
                template_id=uuid.UUID(str(extracted.template_id)) if extracted and extracted.template_id else None,
                document_type=job.get("document_type"),
                field_name=field_name,
                old_value=str(proposed_val) if proposed_val not in (None, "") else None,
                correct_value=corrected_value,
                evidence_text=job.get("raw_text_preview") or job.get("raw_text", "")[:300],
                rule_suggestion="manual_correction",
            )
            db.add(corr)

    decision = recompute_final_decision_after_corrections(canonical_fields)
    _apply_final_decision_to_job(job, decision)

    raw_ocr = extracted.raw_ocr_json if extracted else job.get("ocr_result", {})
    raw_mapped = extracted.extracted_fields if extracted else job.get("mapped_fields", {})
    validation_report = {
        "errors": decision.get("export_block_reasons", []),
        "warnings": decision.get("warnings", []),
    }
    trace_meta = {
        "ocr_engine": raw_ocr.get("ocr_engine") or raw_ocr.get("engine") or "paddleocr",
        "training_template_id": raw_ocr.get("training_template_id"),
        "training_template_name": raw_ocr.get("training_template_name"),
        "training_conflicts": raw_ocr.get("training_conflicts") or [],
        "user_reviews": user_reviews,
        "document_type": job.get("document_type"),
    }
    
    source_traces = build_source_traces(
        mapped_fields=raw_mapped,
        validated_fields=canonical_fields,
        validation_report=validation_report,
        raw_mapped_fields=raw_mapped,
        extra_meta=trace_meta
    )
    
    job["source_traces"] = source_traces
    if "ocr_response" in job and isinstance(job["ocr_response"], dict):
        job["ocr_response"]["source_traces"] = source_traces
        
    if extracted:
        extracted.validated_fields = canonical_fields
        extracted.mapped_output = {k: v.get("value", "") if isinstance(v, dict) else v for k, v in canonical_fields.items()}
        raw_ocr_db = dict(extracted.raw_ocr_json or {})
        raw_ocr_db["source_traces"] = source_traces
        raw_ocr_db["user_reviews"] = user_reviews
        extracted.raw_ocr_json = raw_ocr_db
        if decision.get("export_allowed"):
            extracted.status = ExtractionStatus.VALIDATED.value
        else:
            extracted.status = ExtractionStatus.NEEDS_REVIEW.value
        db.add(extracted)
        await db.commit()
    else:
        raw_ocr_mem = job.setdefault("ocr_result", {})
        raw_ocr_mem["source_traces"] = source_traces
        raw_ocr_mem["user_reviews"] = user_reviews
        
    return {
        "success": True,
        "review_summary": calculate_review_summary(source_traces),
        "source_traces": source_traces,
        "canonical_fields": canonical_fields,
        "export_allowed": decision.get("export_allowed", False),
    }

@router.get("/jobs/{job_id}/review-summary", summary="Get review summary for a job")
async def get_job_review_summary(
    job_id: str,
    current_user: dict = Depends(require_roles(UserRole.OPERATOR, UserRole.REVIEWER)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.services.source_trace import calculate_review_summary
    job, extracted = await get_extracted_data_and_demo_job(db, job_id, current_user)
    source_traces = job.get("source_traces") or {}
    return calculate_review_summary(source_traces)

@router.post("/jobs/{job_id}/review/accept", summary="Accept a field")
async def accept_job_field(
    job_id: str,
    req: FieldActionRequest,
    current_user: dict = Depends(require_roles(UserRole.OPERATOR, UserRole.REVIEWER)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await apply_human_review_action(
        db=db,
        job_id=job_id,
        field_name=req.field_name,
        user_action="accept",
        reviewer_note=req.reviewer_note,
        current_user=current_user,
    )

@router.post("/jobs/{job_id}/review/correct", summary="Correct a field")
async def correct_job_field(
    job_id: str,
    req: FieldCorrectionRequest,
    current_user: dict = Depends(require_roles(UserRole.OPERATOR, UserRole.REVIEWER)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await apply_human_review_action(
        db=db,
        job_id=job_id,
        field_name=req.field_name,
        user_action="correct",
        reviewer_note=req.reviewer_note,
        corrected_value=req.corrected_value,
        is_reusable=req.is_reusable,
        current_user=current_user,
    )

@router.post("/jobs/{job_id}/review/reject", summary="Reject a field")
async def reject_job_field(
    job_id: str,
    req: FieldActionRequest,
    current_user: dict = Depends(require_roles(UserRole.OPERATOR, UserRole.REVIEWER)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await apply_human_review_action(
        db=db,
        job_id=job_id,
        field_name=req.field_name,
        user_action="reject",
        reviewer_note=req.reviewer_note,
        current_user=current_user,
    )

@router.post("/jobs/{job_id}/review/not-available", summary="Mark field as not available")
async def not_available_job_field(
    job_id: str,
    req: FieldActionRequest,
    current_user: dict = Depends(require_roles(UserRole.OPERATOR, UserRole.REVIEWER)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await apply_human_review_action(
        db=db,
        job_id=job_id,
        field_name=req.field_name,
        user_action="not_available",
        reviewer_note=req.reviewer_note,
        current_user=current_user,
    )

@router.get("/supported", summary="List accepted file types")
async def supported_types() -> dict:
    return {
        "mime_types":         sorted(ALLOWED_MIMES),
        "max_file_size_mb":   settings.MAX_FILE_SIZE_MB,
    }


@router.get("/archive", summary="List and filter processed documents")
async def list_archive(
    query: Optional[str] = None,
    vendor_name: Optional[str] = None,
    customer_name: Optional[str] = None,
    invoice_number: Optional[str] = None,
    document_type: Optional[str] = None,
    status: Optional[str] = None,
    review_required: Optional[bool] = None,
    duplicate_status: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    min_amount: Optional[float] = None,
    max_amount: Optional[float] = None,
    export_ready: Optional[bool] = None,
    current_user: dict = Depends(require_roles(UserRole.OPERATOR, UserRole.REVIEWER)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    from sqlalchemy.orm import selectinload

    # Base query for OCRJobs
    stmt = (
        select(OCRJob)
        .options(selectinload(OCRJob.file), selectinload(OCRJob.extracted_data))
    )

    # Ownership scope
    user_id = uuid.UUID(current_user["user_id"])
    org_id = uuid.UUID(current_user["org_id"]) if current_user.get("org_id") else None

    if not is_superadmin(current_user):
        if org_id:
            stmt = stmt.where(OCRJob.org_id == org_id)
        else:
            stmt = stmt.where(OCRJob.org_id.is_(None))
        if not is_org_admin(current_user):
            stmt = stmt.where(OCRJob.created_by_id == user_id)

    stmt = stmt.order_by(OCRJob.created_at.desc())
    res = await db.execute(stmt)
    db_jobs = res.scalars().all()

    # Helper to map a job to archive item
    def _map_job_to_archive_item(job: OCRJob) -> dict[str, Any]:
        file = job.file
        extracted = job.extracted_data

        item = {
            "job_id": str(job.id),
            "document_name": file.original_filename if file else None,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "document_type": file.document_type if file else "general",
            "supplier_name": "",
            "customer_name": "",
            "invoice_number": "",
            "date": "",
            "total_amount": None,
            "duplicate_status": "not_duplicate",
            "master_match_status": "no_master_match",
            "tax_validation_status": "pass",
            "review_required": False,
            "export_ready": False,
            "status": job.status,
        }

        dup_det = {
            "status": "not_duplicate",
            "confidence": 0.0,
            "matched_job_id": None,
            "matched_document_name": None,
            "matched_created_at": None,
            "reasons": [],
            "blocking": False,
            "review_required": False
        }
        erp_intel = {
            "master_match": {
                "party_type": "unknown",
                "status": "no_master_match",
                "confidence": 0.0,
                "selected_master_id": "",
                "selected_master_name": "",
                "candidates": [],
                "reasons": ["Master matching skipped"],
                "review_required": False,
                "blocking": False
            },
            "po_grn_invoice_match": {
                "status": "not_applicable",
                "confidence": 0.0,
                "matched_job_id": None,
                "matched_document_name": None,
                "reasons": [],
                "review_required": False,
                "blocking": False
            },
            "tax_validation": {
                "status": "pass",
                "warnings": [],
                "export_blockers": [],
                "review_required": False
            },
            "review_required": False,
            "blocking": False,
            "warnings": [],
            "export_blockers": []
        }

        if job.ocr_config and isinstance(job.ocr_config, dict):
            dup_det = job.ocr_config.get("duplicate_detection") or dup_det
            erp_intel = job.ocr_config.get("erp_intelligence") or erp_intel

        if extracted:
            val_fields = extracted.validated_fields or {}
            ext_fields = extracted.extracted_fields or {}
            fields = val_fields or ext_fields

            def get_val(keys: list[str]) -> str:
                for k in keys:
                    if k in fields:
                        v = fields[k]
                        if isinstance(v, dict):
                            return str(v.get("value") or "")
                        return str(v)
                return ""

            item["supplier_name"] = get_val(["vendor_name", "supplier_name"])
            item["customer_name"] = get_val(["customer_name", "buyer_name"])
            item["invoice_number"] = get_val(["invoice_no", "invoice_number", "purchase_order_number", "po_number", "po_no", "grn_number"])
            item["date"] = get_val(["invoice_date", "po_date", "grn_date", "due_date"])

            amt_str = get_val(["total_amount", "grand_total"])
            if amt_str:
                try:
                    cleaned_amt = "".join(c for c in amt_str if c.isdigit() or c in {".", "-"})
                    item["total_amount"] = float(cleaned_amt)
                except (ValueError, TypeError):
                    item["total_amount"] = None

            if extracted.raw_ocr_json and isinstance(extracted.raw_ocr_json, dict):
                dup_det = extracted.raw_ocr_json.get("duplicate_detection") or dup_det
                erp_intel = extracted.raw_ocr_json.get("erp_intelligence") or erp_intel
                if not item["document_type"]:
                    item["document_type"] = extracted.raw_ocr_json.get("document_type") or "general"

        item["duplicate_status"] = dup_det.get("status") or "not_duplicate"
        item["master_match_status"] = erp_intel.get("master_match", {}).get("status") or "no_master_match"
        item["tax_validation_status"] = erp_intel.get("tax_validation", {}).get("status") or "pass"

        validation_errors = []
        if extracted:
            validation_items = extracted.validation_errors if isinstance(extracted.validation_errors, list) else []
            validation_errors = [e.get("error", "") for e in validation_items if isinstance(e, dict) and "error" in e]

        export_allowed = bool(extracted and extracted.status == ExtractionStatus.VALIDATED.value and not validation_errors)
        if dup_det.get("blocking") or erp_intel.get("blocking"):
            export_allowed = False

        item["export_ready"] = export_allowed and job.status == "completed"
        item["review_required"] = (job.status == "completed" and not export_allowed) or (extracted and extracted.status == ExtractionStatus.NEEDS_REVIEW.value)

        if job.status == "completed" and not export_allowed:
            item["status"] = "review_required"

        return item

    items = []
    seen_ids = set()
    for db_job in db_jobs:
        item = _map_job_to_archive_item(db_job)
        items.append(item)
        seen_ids.add(item["job_id"])

    # Merge memory jobs
    for job_id, mem_job in OCR_DEMO_JOBS.items():
        if job_id in seen_ids:
            continue
        if not is_superadmin(current_user):
            if mem_job.get("owner_user_id") != current_user["user_id"]:
                continue

        item = {
            "job_id": job_id,
            "document_name": mem_job.get("filename"),
            "created_at": mem_job.get("created_at") or mem_job.get("uploaded_at"),
            "document_type": mem_job.get("document_type") or "general",
            "supplier_name": "",
            "customer_name": "",
            "invoice_number": "",
            "date": "",
            "total_amount": None,
            "duplicate_status": "not_duplicate",
            "master_match_status": "no_master_match",
            "tax_validation_status": "pass",
            "review_required": False,
            "export_ready": False,
            "status": mem_job.get("status"),
        }

        dup_det = mem_job.get("duplicate_detection") or {}
        erp_intel = mem_job.get("erp_intelligence") or {}

        fields = mem_job.get("canonical_fields") or mem_job.get("fields") or {}
        def get_val_mem(keys: list[str]) -> str:
            for k in keys:
                if k in fields:
                    v = fields[k]
                    if isinstance(v, dict):
                        return str(v.get("value") or "")
                    return str(v)
            return ""

        item["supplier_name"] = get_val_mem(["vendor_name", "supplier_name"])
        item["customer_name"] = get_val_mem(["customer_name", "buyer_name"])
        item["invoice_number"] = get_val_mem(["invoice_no", "invoice_number", "purchase_order_number", "po_number", "po_no", "grn_number"])
        item["date"] = get_val_mem(["invoice_date", "po_date", "grn_date", "due_date"])

        amt_str = get_val_mem(["total_amount", "grand_total"])
        if amt_str:
            try:
                cleaned_amt = "".join(c for c in amt_str if c.isdigit() or c in {".", "-"})
                item["total_amount"] = float(cleaned_amt)
            except (ValueError, TypeError):
                item["total_amount"] = None

        item["duplicate_status"] = dup_det.get("status") or "not_duplicate"
        item["master_match_status"] = erp_intel.get("master_match", {}).get("status") or "no_master_match"
        item["tax_validation_status"] = erp_intel.get("tax_validation", {}).get("status") or "pass"

        validation_errors = mem_job.get("validation_errors") or []
        export_allowed = bool(mem_job.get("export_allowed"))
        if dup_det.get("blocking") or erp_intel.get("blocking"):
            export_allowed = False

        item["export_ready"] = export_allowed and mem_job.get("status") == "completed"
        item["review_required"] = (mem_job.get("status") == "completed" and not export_allowed) or (mem_job.get("extraction_status") == ExtractionStatus.NEEDS_REVIEW.value)

        if mem_job.get("status") == "completed" and not export_allowed:
            item["status"] = "review_required"

        items.append(item)

    # Sort items descending by created_at
    def parse_dt(iso_str):
        if not iso_str:
            return datetime.min
        try:
            return datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        except Exception:
            return datetime.min

    items.sort(key=lambda x: parse_dt(x["created_at"]), reverse=True)

    # Filter items
    filtered = []
    for item in items:
        if query:
            q = query.lower()
            doc_name = (item["document_name"] or "").lower()
            vend = (item["supplier_name"] or "").lower()
            cust = (item["customer_name"] or "").lower()
            inv = (item["invoice_number"] or "").lower()
            if q not in doc_name and q not in vend and q not in cust and q not in inv:
                continue

        if vendor_name and vendor_name.lower() not in (item["supplier_name"] or "").lower():
            continue
        if customer_name and customer_name.lower() not in (item["customer_name"] or "").lower():
            continue
        if invoice_number and invoice_number.lower() not in (item["invoice_number"] or "").lower():
            continue
        if document_type and document_type.lower() != (item["document_type"] or "").lower():
            continue
        if status and status.lower() != (item["status"] or "").lower():
            continue
        if review_required is not None and item["review_required"] != review_required:
            continue
        if duplicate_status and duplicate_status.lower() != (item["duplicate_status"] or "").lower():
            continue
        if export_ready is not None and item["export_ready"] != export_ready:
            continue

        if start_date or end_date:
            item_date_str = item["created_at"]
            if item_date_str:
                try:
                    item_dt = datetime.fromisoformat(item_date_str.split("T")[0])
                    if start_date:
                        start_dt = datetime.fromisoformat(start_date)
                        if item_dt < start_dt:
                            continue
                    if end_date:
                        end_dt = datetime.fromisoformat(end_date)
                        if item_dt > end_dt:
                            continue
                except Exception:
                    pass

        if min_amount is not None or max_amount is not None:
            amt = item["total_amount"]
            if amt is None:
                continue
            if min_amount is not None and amt < min_amount:
                continue
            if max_amount is not None and amt > max_amount:
                continue

        filtered.append(item)

    return {"documents": filtered, "total": len(filtered)}


class ExportSelectedRequest(BaseModel):
    job_ids: list[str]
    format: str = "json"


@router.post("/export/selected", summary="Export selected job summaries as CSV/JSON")
async def export_selected_jobs(
    req: ExportSelectedRequest,
    current_user: dict = Depends(require_roles(UserRole.OPERATOR)),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy.orm import selectinload
    import csv
    import io
    from fastapi.responses import StreamingResponse

    job_uuids = []
    for jid in req.job_ids:
        try:
            job_uuids.append(uuid.UUID(jid))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid job_id format: {jid}")

    stmt = (
        select(OCRJob)
        .options(selectinload(OCRJob.file), selectinload(OCRJob.extracted_data))
        .where(OCRJob.id.in_(job_uuids))
    )

    # Ownership scope
    user_id = uuid.UUID(current_user["user_id"])
    org_id = uuid.UUID(current_user["org_id"]) if current_user.get("org_id") else None

    if not is_superadmin(current_user):
        if org_id:
            stmt = stmt.where(OCRJob.org_id == org_id)
        else:
            stmt = stmt.where(OCRJob.org_id.is_(None))
        if not is_org_admin(current_user):
            stmt = stmt.where(OCRJob.created_by_id == user_id)

    res = await db.execute(stmt)
    jobs = res.scalars().all()

    items = []
    for job in jobs:
        file = job.file
        extracted = job.extracted_data

        item = {
            "job_id": str(job.id),
            "document_name": file.original_filename if file else None,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "document_type": file.document_type if file else "general",
            "supplier_name": "",
            "customer_name": "",
            "invoice_number": "",
            "date": "",
            "total_amount": None,
            "duplicate_status": "not_duplicate",
            "master_match_status": "no_master_match",
            "tax_validation_status": "pass",
            "review_required": False,
            "export_ready": False,
            "status": job.status,
        }

        dup_det = {
            "status": "not_duplicate",
            "confidence": 0.0,
            "matched_job_id": None,
            "matched_document_name": None,
            "matched_created_at": None,
            "reasons": [],
            "blocking": False,
            "review_required": False
        }
        erp_intel = {
            "master_match": {
                "party_type": "unknown",
                "status": "no_master_match",
                "confidence": 0.0,
                "selected_master_id": "",
                "selected_master_name": "",
                "candidates": [],
                "reasons": ["Master matching skipped"],
                "review_required": False,
                "blocking": False
            },
            "po_grn_invoice_match": {
                "status": "not_applicable",
                "confidence": 0.0,
                "matched_job_id": None,
                "matched_document_name": None,
                "reasons": [],
                "review_required": False,
                "blocking": False
            },
            "tax_validation": {
                "status": "pass",
                "warnings": [],
                "export_blockers": [],
                "review_required": False
            },
            "review_required": False,
            "blocking": False,
            "warnings": [],
            "export_blockers": []
        }

        if job.ocr_config and isinstance(job.ocr_config, dict):
            dup_det = job.ocr_config.get("duplicate_detection") or dup_det
            erp_intel = job.ocr_config.get("erp_intelligence") or erp_intel

        if extracted:
            val_fields = extracted.validated_fields or {}
            ext_fields = extracted.extracted_fields or {}
            fields = val_fields or ext_fields

            def get_val(keys: list[str]) -> str:
                for k in keys:
                    if k in fields:
                        v = fields[k]
                        if isinstance(v, dict):
                            return str(v.get("value") or "")
                        return str(v)
                return ""

            item["supplier_name"] = get_val(["vendor_name", "supplier_name"])
            item["customer_name"] = get_val(["customer_name", "buyer_name"])
            item["invoice_number"] = get_val(["invoice_no", "invoice_number", "purchase_order_number", "po_number", "po_no", "grn_number"])
            item["date"] = get_val(["invoice_date", "po_date", "grn_date", "due_date"])

            amt_str = get_val(["total_amount", "grand_total"])
            if amt_str:
                try:
                    cleaned_amt = "".join(c for c in amt_str if c.isdigit() or c in {".", "-"})
                    item["total_amount"] = float(cleaned_amt)
                except (ValueError, TypeError):
                    item["total_amount"] = None

            if extracted.raw_ocr_json and isinstance(extracted.raw_ocr_json, dict):
                dup_det = extracted.raw_ocr_json.get("duplicate_detection") or dup_det
                erp_intel = extracted.raw_ocr_json.get("erp_intelligence") or erp_intel
                if not item["document_type"]:
                    item["document_type"] = extracted.raw_ocr_json.get("document_type") or "general"

        item["duplicate_status"] = dup_det.get("status") or "not_duplicate"
        item["master_match_status"] = erp_intel.get("master_match", {}).get("status") or "no_master_match"
        item["tax_validation_status"] = erp_intel.get("tax_validation", {}).get("status") or "pass"

        validation_errors = []
        if extracted:
            validation_items = extracted.validation_errors if isinstance(extracted.validation_errors, list) else []
            validation_errors = [e.get("error", "") for e in validation_items if isinstance(e, dict) and "error" in e]

        export_allowed = bool(extracted and extracted.status == ExtractionStatus.VALIDATED.value and not validation_errors)
        if dup_det.get("blocking") or erp_intel.get("blocking"):
            export_allowed = False

        item["export_ready"] = export_allowed and job.status == "completed"
        item["review_required"] = (job.status == "completed" and not export_allowed) or (extracted and extracted.status == ExtractionStatus.NEEDS_REVIEW.value)

        if job.status == "completed" and not export_allowed:
            item["status"] = "review_required"

        items.append(item)

    if req.format.lower() == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "Job ID", "Document Name", "Created At", "Status", "Document Type",
            "Supplier Name", "Customer Name", "Invoice/PO/GRN Number", "Date",
            "Total Amount", "Duplicate Status", "Master Match Status", "Tax Validation Status",
            "Review Required", "Export Ready"
        ])
        for item in items:
            writer.writerow([
                item["job_id"],
                item["document_name"],
                item["created_at"],
                item["status"],
                item["document_type"],
                item["supplier_name"],
                item["customer_name"],
                item["invoice_number"],
                item["date"],
                item["total_amount"] if item["total_amount"] is not None else "",
                item["duplicate_status"],
                item["master_match_status"],
                item["tax_validation_status"],
                "true" if item["review_required"] else "false",
                "true" if item["export_ready"] else "false"
            ])

        csv_data = output.getvalue().encode("utf-8")
        filename = f"ocr-export-selected-{uuid.uuid4().hex[:8]}.csv"
        return StreamingResponse(
            io.BytesIO(csv_data),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    return {"documents": items, "total": len(items)}


# ── Register batch sub-router ──────────────────────────────────────────────────

from app.api.v1.routers.upload_batch import batch_router  # noqa: E402

router.include_router(batch_router)
