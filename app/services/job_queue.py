"""
job_queue.py — Queue adapter interface for OCR job processing.
Enables transitions, status updates, and caches, preparing the app for durable queue integration.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from fastapi import Request
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.enums import FileStatus, JobStatus, LogEventType, LogLevel
from app.models.file import UploadedFile
from app.models.job import OCRJob
from app.models.extracted_data import ExtractedData
from app.services.audit_service import write_audit_log
from app.api.v1.routers.upload_helpers import (
    OCR_DEMO_JOBS,
    _file_category_for_mime,
    _empty_ocr_response,
    _refresh_batch_counters,
)

STATUS_QUEUED = JobStatus.QUEUED.value
STATUS_PROCESSING = JobStatus.PROCESSING.value
STATUS_COMPLETED = JobStatus.COMPLETED.value
STATUS_FAILED = JobStatus.FAILED.value
STATUS_REVIEW_REQUIRED = "review_required"
STATUS_CANCELLED = JobStatus.CANCELLED.value

PUBLIC_JOB_STATUSES = {
    STATUS_QUEUED,
    STATUS_PROCESSING,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_REVIEW_REQUIRED,
    STATUS_CANCELLED,
}

IDEMPOTENT_ACTIVE_STATUSES = {
    STATUS_QUEUED,
    STATUS_PROCESSING,
    STATUS_COMPLETED,
    STATUS_REVIEW_REQUIRED,
}

STEP_QUEUED = "queued"
STEP_VALIDATING_UPLOAD = "validating_upload"
STEP_EXTRACTING_TEXT = "extracting_text"
STEP_RUNNING_OCR = "running_ocr"
STEP_MAPPING_FIELDS = "mapping_fields"
STEP_APPLYING_TRAINING = "applying_training"
STEP_VALIDATING_FIELDS = "validating_fields"
STEP_BUILDING_RESPONSE = "building_response"
STEP_COMPLETED = "completed"
STEP_FAILED = "failed"

PUBLIC_JOB_STEPS = {
    STEP_QUEUED,
    STEP_VALIDATING_UPLOAD,
    STEP_EXTRACTING_TEXT,
    STEP_RUNNING_OCR,
    STEP_MAPPING_FIELDS,
    STEP_APPLYING_TRAINING,
    STEP_VALIDATING_FIELDS,
    STEP_BUILDING_RESPONSE,
    STEP_COMPLETED,
    STEP_FAILED,
}

DEFAULT_MAX_RETRIES = 3


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_idempotency_key(user_id: str | uuid.UUID, checksum: str, context: str = "upload") -> str:
    return f"{context}:{uuid.UUID(str(user_id))}:{checksum}"


def sanitize_error_message(error_code: str, message: str | None = None) -> str:
    safe_messages = {
        "OCR_TIMEOUT": "OCR processing timed out. Please retry.",
        "CORRUPTED_FILE": "The uploaded file could not be processed. Please upload a valid, unencrypted file.",
        "RESOURCE_EXHAUSTED": "OCR processing exceeded available resources.",
        "OCR_FAILED": "OCR processing failed.",
    }
    return safe_messages.get(error_code, "OCR processing failed.")


def failure_payload(
    *,
    error_code: str,
    message: str | None,
    retryable: bool,
    failed_at: str | None = None,
    current_step: str = STEP_FAILED,
) -> dict[str, Any]:
    safe_message = sanitize_error_message(error_code, message)
    return {
        "status": STATUS_FAILED,
        "progress_percent": 100,
        "current_step": current_step,
        "retryable": bool(retryable),
        "error_code": error_code,
        "error_message": safe_message,
        "error": safe_message,
        "failed_at": failed_at or utc_now_iso(),
    }


def lifecycle_metadata(
    *,
    job_id: str | uuid.UUID,
    status: str,
    current_step: str,
    progress_percent: int,
    retry_count: int = 0,
    max_retries: int = DEFAULT_MAX_RETRIES,
    document_name: str | None = None,
    document_type: str | None = None,
    ocr_engine: str | None = None,
    created_at: str | None = None,
    started_at: str | None = None,
    completed_at: str | None = None,
    failed_at: str | None = None,
    updated_at: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    retryable: bool = False,
) -> dict[str, Any]:
    return {
        "job_id": str(job_id),
        "status": status,
        "created_at": created_at,
        "started_at": started_at,
        "completed_at": completed_at,
        "failed_at": failed_at,
        "updated_at": updated_at or utc_now_iso(),
        "progress_percent": int(progress_percent),
        "current_step": current_step,
        "retry_count": int(retry_count or 0),
        "max_retries": int(max_retries or 0),
        "error_code": error_code,
        "error_message": error_message,
        "retryable": bool(retryable),
        "document_name": document_name,
        "document_type": document_type,
        "ocr_engine": ocr_engine,
    }


async def enqueue(
    job_id: str | uuid.UUID,
    file_id: str | uuid.UUID,
    created_by_id: str | uuid.UUID,
    org_id: str | uuid.UUID | None,
    filename: str,
    mime_type: str,
    file_size: int,
    checksum: str,
    db: AsyncSession,
    storage_path: Optional[str] = None,
    stored_filename: Optional[str] = None,
    upload_save_ms: float = 0.0,
    request: Optional[Request] = None,
) -> OCRJob:
    """Enqueue a new job, instantiating UploadedFile and OCRJob records in the DB and caching in OCR_DEMO_JOBS."""
    job_uuid = uuid.UUID(str(job_id))
    file_uuid = uuid.UUID(str(file_id))
    user_uuid = uuid.UUID(str(created_by_id))
    org_uuid = uuid.UUID(str(org_id)) if org_id else None

    file_category = _file_category_for_mime(mime_type)

    uploaded_file = UploadedFile(
        id=file_uuid,
        uploaded_by_id=user_uuid,
        org_id=org_uuid,
        original_filename=filename,
        stored_filename=stored_filename,
        storage_path=storage_path,
        mime_type=mime_type,
        file_category=file_category,
        file_size=file_size,
        checksum_sha256=checksum,
        status=FileStatus.PROCESSING.value,
        extra={"demo_job_id": str(job_id)},
    )
    db.add(uploaded_file)
    await db.flush()

    ocr_job = OCRJob(
        id=job_uuid,
        file_id=file_uuid,
        created_by_id=user_uuid,
        org_id=org_uuid,
        status=JobStatus.QUEUED.value,
        queued_at=datetime.now(timezone.utc),
        ocr_config={
            "demo": True,
            "mime_type": mime_type,
            "progress_percent": 0,
            "current_step": "queued",
            "retryable": False,
            "error_code": None,
        },
    )
    db.add(ocr_job)
    await db.flush()

    await write_audit_log(
        db,
        action="file.upload",
        event_type=LogEventType.UPLOAD,
        status="success",
        user_id=user_uuid,
        org_id=org_uuid,
        resource_type="uploaded_file",
        resource_id=file_uuid,
        message="File uploaded",
        metadata={
            "filename": filename,
            "mime_type": mime_type,
            "file_size": file_size,
            "ocr_job_id": str(job_id),
        },
        request=request,
    )
    await db.commit()

    created_at = datetime.now(timezone.utc).isoformat()
    metadata = lifecycle_metadata(
        job_id=job_id,
        status=STATUS_QUEUED,
        current_step=STEP_QUEUED,
        progress_percent=0,
        max_retries=ocr_job.max_retries,
        document_name=filename,
        created_at=created_at,
        updated_at=created_at,
    )
    ocr_cfg = dict(ocr_job.ocr_config or {})
    ocr_cfg.update(metadata)
    ocr_cfg["idempotency_key"] = build_idempotency_key(user_uuid, checksum)
    ocr_job.ocr_config = ocr_cfg
    db.add(ocr_job)
    await db.commit()

    OCR_DEMO_JOBS[str(job_id)] = {
        **metadata,
        "job_id": str(job_id),
        "success": True,
        "owner_user_id": str(created_by_id),
        "uploaded_file_id": str(file_uuid),
        "filename": filename,
        "stored_as": stored_filename,
        "mime_type": mime_type,
        "file_size": file_size,
        "checksum_sha256": checksum,
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
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
        "idempotency_key": ocr_cfg["idempotency_key"],
    }
    return ocr_job


async def mark_processing(job_id: str | uuid.UUID, step: str, db: AsyncSession) -> None:
    """Transition job status to processing in DB and update metadata."""
    job_uuid = uuid.UUID(str(job_id))
    job = await db.get(OCRJob, job_uuid)
    if job:
        job.status = JobStatus.PROCESSING.value
        started_at = datetime.now(timezone.utc)
        job.started_at = job.started_at or started_at
        ocr_config = dict(job.ocr_config or {})
        ocr_config.update({
            "progress_percent": 10,
            "current_step": step,
            "started_at": job.started_at.isoformat() if job.started_at else started_at.isoformat(),
            "updated_at": started_at.isoformat(),
        })
        job.ocr_config = ocr_config
        db.add(job)
        await db.commit()

    job_id_str = str(job_id)
    if job_id_str in OCR_DEMO_JOBS:
        OCR_DEMO_JOBS[job_id_str].update({
            "status": "processing",
            "current_step": step,
            "progress_percent": 10,
            "started_at": OCR_DEMO_JOBS[job_id_str].get("started_at") or datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })


async def update_progress(job_id: str | uuid.UUID, percent: int, step: str, db: AsyncSession) -> None:
    """Update progress percentage and current processing step."""
    job_uuid = uuid.UUID(str(job_id))
    job = await db.get(OCRJob, job_uuid)
    if job:
        ocr_config = dict(job.ocr_config or {})
        ocr_config.update({
            "progress_percent": percent,
            "current_step": step,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        job.ocr_config = ocr_config
        db.add(job)
        await db.commit()

    job_id_str = str(job_id)
    if job_id_str in OCR_DEMO_JOBS:
        OCR_DEMO_JOBS[job_id_str].update({
            "progress_percent": percent,
            "current_step": step,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })


async def mark_completed(
    job_id: str | uuid.UUID,
    result: dict,
    ocr_response: dict,
    db: AsyncSession,
    *,
    extracted_data_payload: Optional[dict] = None,
    uploaded_file_payload: Optional[dict] = None,
    demo_job_payload: Optional[dict] = None,
    **kwargs
) -> None:
    """Transition job status to completed, persist ExtractedData, update UploadedFile, write logs, and cache."""
    job_uuid = uuid.UUID(str(job_id))
    job = await db.get(OCRJob, job_uuid)
    if not job:
        raise RuntimeError(f"OCRJob {job_id} not found")

    completed_at = datetime.now(timezone.utc)

    # 1. Extracted Data Persistence
    extracted = None
    if extracted_data_payload:
        extracted = ExtractedData(
            job_id=job.id,
            file_id=job.file_id,
            template_id=job.template_id,
            org_id=job.org_id,
            **extracted_data_payload
        )
        db.add(extracted)
        await db.flush()

    # 2. Uploaded File Update
    uploaded_file = await db.get(UploadedFile, job.file_id)
    if uploaded_file and uploaded_file_payload:
        for k, v in uploaded_file_payload.items():
            setattr(uploaded_file, k, v)
        db.add(uploaded_file)

    # 3. OCR Job Update
    job.status = JobStatus.COMPLETED.value
    job.started_at = job.started_at or completed_at
    job.completed_at = completed_at
    job.processing_ms = int(result.get("processing_ms", 0) or result.get("ocr_wall_ms", 0) or 0)
    job.page_count = int(result.get("page_count", 0) or 0)
    job.avg_confidence = float(result.get("avg_confidence", 0.0) or 0.0)
    text = result.get("text", "") or ocr_response.get("text", "")
    job.total_words = len(text.split())

    ocr_config = dict(job.ocr_config or {})
    ocr_config.update({
        "progress_percent": 100,
        "current_step": "completed",
        "retryable": False,
        "error_code": None,
        "error_message": None,
        "completed_at": completed_at.isoformat(),
        "updated_at": completed_at.isoformat(),
    })
    job.ocr_config = ocr_config
    db.add(job)
    await db.flush()

    # 4. Audit Logging
    await write_audit_log(
        db,
        action="ocr.complete",
        event_type=LogEventType.OCR_JOB,
        status="success",
        user_id=job.created_by_id,
        org_id=job.org_id,
        resource_type="ocr_job",
        resource_id=job.id,
        message="OCR completed",
        metadata={
            "extracted_data_id": str(extracted.id) if extracted else None,
            "page_count": job.page_count,
            "avg_confidence": job.avg_confidence,
            "fields_extracted": extracted.fields_extracted if extracted else 0,
        },
    )

    if extracted:
        validation_valid = kwargs.get("validation_valid", True)
        await write_audit_log(
            db,
            action="validation.complete",
            event_type=LogEventType.EXTRACTION,
            status="success" if validation_valid else "failure",
            user_id=job.created_by_id,
            org_id=job.org_id,
            resource_type="extracted_data",
            resource_id=extracted.id,
            level=LogLevel.INFO if validation_valid else LogLevel.WARNING,
            message="Validation completed",
            metadata={
                "field_count": extracted.fields_extracted,
                "error_count": len(extracted_data_payload.get("validation_errors", [])),
                "errors": [e.get("error") for e in extracted_data_payload.get("validation_errors", []) if "error" in e],
            },
        )

    ai_fallback = kwargs.get("ai_fallback")
    if ai_fallback and extracted:
        await write_audit_log(
            db,
            action="ai.fallback.used",
            event_type=LogEventType.EXTRACTION,
            status="success",
            user_id=job.created_by_id,
            org_id=job.org_id,
            resource_type="extracted_data",
            resource_id=extracted.id,
            level=LogLevel.WARNING if ai_fallback.get("needs_review") else LogLevel.INFO,
            message="AI fallback used",
            metadata=ai_fallback,
        )

    await _refresh_batch_counters(db, job.batch_id)
    await db.commit()

    # 5. Caching & In-Memory updates
    job_id_str = str(job_id)
    if job_id_str in OCR_DEMO_JOBS:
        if demo_job_payload:
            OCR_DEMO_JOBS[job_id_str].update(demo_job_payload)
        OCR_DEMO_JOBS[job_id_str].update({
            "status": "completed",
            "completed_at": completed_at.isoformat(),
            "extracted_data_id": str(extracted.id) if extracted else None,
            "progress_percent": 100,
            "current_step": "completed",
            "retryable": False,
            "error_code": None,
            "error_message": None,
            "updated_at": completed_at.isoformat(),
        })


async def mark_failed(
    job_id: str | uuid.UUID,
    error_code: str,
    message: str,
    retryable: bool,
    db: AsyncSession,
) -> None:
    """Transition job status to failed with retry-ready metadata.

    Phase 5B deliberately records retryability without scheduling a retry worker.
    A durable queue can consume retryable failures in a later phase.
    """
    job_uuid = uuid.UUID(str(job_id))
    job = await db.get(OCRJob, job_uuid)
    if not job:
        logger.error(f"Job not found for mark_failed: {job_id}")
        return

    failed_at = datetime.now(timezone.utc)
    ocr_config = dict(job.ocr_config or {})

    safe_message = sanitize_error_message(error_code, message)
    job.status = JobStatus.FAILED.value
    job.completed_at = failed_at
    job.error_message = safe_message
    
    uploaded_file = await db.get(UploadedFile, job.file_id)
    if uploaded_file:
        uploaded_file.status = FileStatus.FAILED.value
        db.add(uploaded_file)

    ocr_config.update(failure_payload(
        error_code=error_code,
        message=safe_message,
        retryable=retryable,
        failed_at=failed_at.isoformat(),
    ))
    ocr_config["updated_at"] = failed_at.isoformat()
    job.ocr_config = ocr_config
    db.add(job)

    await write_audit_log(
        db,
        action="ocr.failure",
        event_type=LogEventType.OCR_JOB,
        status="failure",
        user_id=job.created_by_id,
        org_id=job.org_id,
        resource_type="ocr_job",
        resource_id=job.id,
        level=LogLevel.ERROR,
        message="OCR failed",
        metadata={"error_code": error_code, "retryable": bool(retryable)},
        error=safe_message,
    )
    await _refresh_batch_counters(db, job.batch_id)
    await db.commit()

    job_id_str = str(job_id)
    if job_id_str in OCR_DEMO_JOBS:
        from app.api.v1.routers.upload_helpers import OCR_FAILURE_TEXT
        OCR_DEMO_JOBS[job_id_str].update({
            **failure_payload(
                error_code=error_code,
                message=safe_message,
                retryable=retryable,
                failed_at=failed_at.isoformat(),
            ),
            "warnings": [safe_message],
            "updated_at": failed_at.isoformat(),
            "text": OCR_FAILURE_TEXT,
            "raw_text": OCR_FAILURE_TEXT,
            "extracted_text": OCR_FAILURE_TEXT,
            "full_text": OCR_FAILURE_TEXT,
            "raw_text_length": len(OCR_FAILURE_TEXT),
            "detected_line_count": 1,
            "raw_text_preview": OCR_FAILURE_TEXT,
            "fields": {},
            "confidence": {},
        })


async def get_job(job_id: str | uuid.UUID, db: AsyncSession) -> Optional[OCRJob]:
    """Retrieve OCRJob database model by UUID."""
    try:
        job_uuid = uuid.UUID(str(job_id))
    except ValueError:
        return None
    return await db.get(OCRJob, job_uuid)
