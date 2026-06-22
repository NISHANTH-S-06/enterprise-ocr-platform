"""
Upload router — secure file upload with validation and OCR trigger.
Integrates with ocr_service and feeds results into extracted_data table.
Delegates batch and background processing to helper modules.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Annotated, Any

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
from app.services.ownership_service import get_owned_ocr_job, is_superadmin
from app.services.correction_memory import (
    apply_user_corrections,
    build_correction_memory_records,
    recompute_final_decision_after_corrections,
    resolve_company_for_correction_memory,
)

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

    job_uuid = uuid.uuid4()
    job_id = str(job_uuid)
    uploaded_at = datetime.now(timezone.utc).isoformat()
    user_id = uuid.UUID(current_user["user_id"])
    org_id = uuid.UUID(current_user["org_id"]) if current_user.get("org_id") else None
    file_category = _file_category_for_mime(detected)

    uploaded_file = UploadedFile(
        uploaded_by_id=user_id,
        org_id=org_id,
        original_filename=safe_name,
        stored_filename=stored,
        storage_path=str(dest),
        mime_type=detected,
        file_category=file_category,
        file_size=len(data),
        checksum_sha256=checksum,
        status=FileStatus.PROCESSING.value,
        extra={"demo_job_id": job_id},
    )
    db.add(uploaded_file)
    await db.flush()

    ocr_job = OCRJob(
        id=job_uuid,
        file_id=uploaded_file.id,
        created_by_id=user_id,
        org_id=org_id,
        status=JobStatus.PROCESSING.value,
        queued_at=datetime.now(timezone.utc),
        started_at=datetime.now(timezone.utc),
        ocr_config={"demo": True, "mime_type": detected},
    )
    db.add(ocr_job)
    await db.flush()
    await write_audit_log(
        db,
        action="file.upload",
        event_type=LogEventType.UPLOAD,
        status="success",
        user_id=user_id,
        org_id=org_id,
        resource_type="uploaded_file",
        resource_id=uploaded_file.id,
        message="File uploaded",
        metadata={
            "filename": safe_name,
            "mime_type": detected,
            "file_size": len(data),
            "ocr_job_id": job_id,
        },
        request=request,
    )
    await db.commit()

    OCR_DEMO_JOBS[job_id] = {
        "job_id": job_id,
        "status": "processing",
        "success": True,
        "owner_user_id": str(user_id),
        "uploaded_file_id": str(uploaded_file.id),
        "filename": safe_name,
        "stored_as": stored,
        "mime_type": detected,
        "file_size": len(data),
        "checksum_sha256": checksum,
        "uploaded_at": uploaded_at,
        "text": "",
        "raw_text": "",
        "extracted_text": "",
        "full_text": "",
        "raw_text_length": 0,
        "detected_line_count": 0,
        "raw_text_preview": "",
        "fields": {},
        "confidence": {},
        "warnings": [],
        "upload_save_ms": upload_save_ms,
        "ocr_response": _empty_ocr_response(),
    }
    background_tasks.add_task(_ocr_background, str(dest), detected, job_id, checksum)

    logger.info(f"Upload accepted: user={current_user['user_id']} file={safe_name} job={job_id} upload_save_ms={upload_save_ms}")
    return {
        "job_id":          job_id,
        "status":          "processing",
        "success":         True,
        "filename":        safe_name,
        "stored_as":       stored,
        "mime_type":       detected,
        "file_size":       len(data),
        "checksum_sha256": checksum,
        "uploaded_at":     uploaded_at,
        "message":         "File accepted. OCR running in background.",
        "result_url":      f"/api/v1/upload/jobs/{job_id}",
        "text":            "",
        "raw_text_length":  0,
        "detected_line_count": 0,
        "raw_text_preview": "",
        "fields":          {},
        "confidence":      {},
        "warnings":        [],
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
    job = OCR_DEMO_JOBS.get(job_id)
    if job:
        if not is_superadmin(current_user) and job.get("owner_user_id") != current_user["user_id"]:
            raise HTTPException(status_code=404, detail="Upload job not found")
        return job

    try:
        job_uuid = uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Invalid job_id format")

    db_job = await get_owned_ocr_job(db, job_uuid, current_user)

    extracted: ExtractedData | None = None
    if db_job.status == JobStatus.COMPLETED.value:
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
    export_allowed = bool(extracted and extracted.status == ExtractionStatus.VALIDATED.value and not validation_errors)
    document_type = "general"
    if extracted and extracted.raw_ocr_json and isinstance(extracted.raw_ocr_json, dict):
        document_type = (
            extracted.raw_ocr_json.get("document_type")
            or extracted.raw_ocr_json.get("doc_type")
            or document_type
        )

    return {
        "job_id": job_id,
        "status": db_job.status,
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
        "warnings": ["Job state restored from database (server was restarted)", *validation_warnings],
        "extraction_status": extracted.status if extracted else None,
        "validation_errors": validation_errors,
        "validation_warnings": validation_warnings,
        "document_type": document_type,
        "items": line_items,
        "line_items": line_items,
        "export_allowed": export_allowed,
        "blocked_fields": [] if export_allowed else [*missing_fields, *validation_errors],
        "export_block_reasons": [] if export_allowed else [*missing_fields, *validation_errors],
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
            "warnings": validation_warnings,
            "missing_fields": missing_fields,
            "low_confidence_fields": low_confidence_fields,
            "items": line_items,
            "source_traces": source_traces,
        },
        "source_traces": source_traces,
        "memory_applied_count": 0,
        "memory_suggestion_count": 0,
        "memory_applied_fields": [],
        "memory_suggestion_fields": [],
        "memory_suggestions": {},
        "memory_warnings": [],
        "_source": "db_fallback",
    }


@router.get("/supported", summary="List accepted file types")
async def supported_types() -> dict:
    return {
        "mime_types":         sorted(ALLOWED_MIMES),
        "max_file_size_mb":   settings.MAX_FILE_SIZE_MB,
    }

# ── Register batch sub-router ──────────────────────────────────────────────────

from app.api.v1.routers.upload_batch import batch_router  # noqa: E402

router.include_router(batch_router)
