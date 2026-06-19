"""
upload_batch.py — Batch routes for bulk file upload and OCR tracking.
Extracted from upload.py for maintainability.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import require_roles
from app.models.batch import OCRBatch
from app.models.enums import BatchStatus, FileStatus, JobStatus, LogEventType, UserRole
from app.models.file import UploadedFile
from app.models.job import OCRJob
from app.services.audit_service import write_audit_log
from app.services.tenant_limits import (
    check_batch_limits_for_user,
)

from app.api.v1.routers.upload_helpers import OCR_DEMO_JOBS

settings = get_settings()

batch_router = APIRouter()


def _get_upload_mod() -> Any:
    from app.api.v1.routers import upload
    return upload


@batch_router.post(
    "/batches",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload multiple files and trigger OCR batch processing",
)
async def upload_batch(
    background_tasks: BackgroundTasks,
    files: Annotated[list[UploadFile], File(description="PDF or image files")],
    request: Request,
    current_user: dict = Depends(require_roles(UserRole.OPERATOR)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not files:
        raise HTTPException(status_code=400, detail="Batch must include at least one file")
    
    upload_mod = _get_upload_mod()
    tenant_limits_source = await upload_mod._tenant_limit_source(db, current_user)
    check_batch_limits_for_user(tenant_limits_source, len(files), 0)

    prepared_files: list[dict[str, Any]] = []
    total_bytes = 0
    for upload_file_obj in files:
        prepared = await upload_mod._prepare_upload_file(upload_file_obj)
        total_bytes += len(prepared["data"])
        check_batch_limits_for_user(tenant_limits_source, len(files), total_bytes)
        prepared_files.append(prepared)

    user_id = uuid.UUID(current_user["user_id"])
    org_id = uuid.UUID(current_user["org_id"]) if current_user.get("org_id") else None
    batch = OCRBatch(
        created_by_id=user_id,
        org_id=org_id,
        status=BatchStatus.PROCESSING.value,
        file_count=len(prepared_files),
        total_bytes=total_bytes,
        started_at=datetime.now(timezone.utc),
        metadata_={"source": "upload.batches"},
    )
    db.add(batch)
    await db.flush()

    response_files: list[dict[str, str]] = []
    job_ids: list[str] = []
    created_children: list[dict[str, Any]] = []

    for prepared in prepared_files:
        data = prepared["data"]
        safe_name = prepared["safe_name"]
        detected = prepared["detected"]
        stored_info = await upload_mod._store_prepared_upload(prepared)
        checksum = stored_info["checksum"]
        stored = stored_info["stored"]
        dest = stored_info["dest"]
        upload_save_ms = stored_info["upload_save_ms"]
        job_uuid = uuid.uuid4()
        job_id = str(job_uuid)
        uploaded_at = datetime.now(timezone.utc).isoformat()

        uploaded_file = UploadedFile(
            uploaded_by_id=user_id,
            batch_id=batch.id,
            org_id=org_id,
            original_filename=safe_name,
            stored_filename=stored,
            storage_path=str(dest),
            mime_type=detected,
            file_category=upload_mod._file_category_for_mime(detected),
            file_size=len(data),
            checksum_sha256=checksum,
            status=FileStatus.PROCESSING.value,
            extra={"demo_job_id": job_id, "batch_id": str(batch.id)},
        )
        db.add(uploaded_file)
        await db.flush()

        ocr_job = OCRJob(
            id=job_uuid,
            file_id=uploaded_file.id,
            batch_id=batch.id,
            created_by_id=user_id,
            org_id=org_id,
            status=JobStatus.PROCESSING.value,
            queued_at=datetime.now(timezone.utc),
            started_at=datetime.now(timezone.utc),
            ocr_config={"demo": True, "mime_type": detected, "batch_id": str(batch.id)},
        )
        db.add(ocr_job)
        await db.flush()

        await upload_mod.write_audit_log(
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
                "batch_id": str(batch.id),
            },
            request=request,
        )

        response_files.append({
            "filename": safe_name,
            "file_id": str(uploaded_file.id),
            "job_id": job_id,
            "status": ocr_job.status,
        })
        job_ids.append(job_id)
        created_children.append({
            "job_id": job_id,
            "user_id": user_id,
            "uploaded_file_id": str(uploaded_file.id),
            "safe_name": safe_name,
            "stored": stored,
            "detected": detected,
            "file_size": child_size if "child_size" in dir() else len(data),  # fallback
            "checksum": checksum,
            "uploaded_at": uploaded_at,
            "upload_save_ms": upload_save_ms,
            "dest": dest,
            "actual_len": len(data),
        })

    await upload_mod.write_audit_log(
        db,
        action="batch.upload.created",
        event_type=LogEventType.UPLOAD,
        status="success",
        user_id=user_id,
        org_id=org_id,
        resource_type="ocr_batch",
        resource_id=batch.id,
        message="OCR batch uploaded",
        metadata={
            "batch_id": str(batch.id),
            "file_count": len(prepared_files),
            "total_bytes": total_bytes,
            "accepted_count": len(prepared_files),
            "rejected_count": 0,
        },
        request=request,
    )
    await db.commit()

    for child in created_children:
        OCR_DEMO_JOBS[child["job_id"]] = {
            "job_id": child["job_id"],
            "batch_id": str(batch.id),
            "status": "processing",
            "success": True,
            "owner_user_id": str(child["user_id"]),
            "uploaded_file_id": child["uploaded_file_id"],
            "filename": child["safe_name"],
            "stored_as": child["stored"],
            "mime_type": child["detected"],
            "file_size": child["actual_len"],
            "checksum_sha256": child["checksum"],
            "uploaded_at": child["uploaded_at"],
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
            "upload_save_ms": child["upload_save_ms"],
            "ocr_response": upload_mod._empty_ocr_response(),
        }
        background_tasks.add_task(
            upload_mod._ocr_background,
            str(child["dest"]),
            child["detected"],
            child["job_id"],
            child["checksum"],
        )

    logger.info(
        "Batch upload accepted: user={} batch={} files={} total_bytes={}",
        current_user["user_id"],
        batch.id,
        len(prepared_files),
        total_bytes,
    )
    return {
        "batch_id": str(batch.id),
        "status": batch.status,
        "file_count": len(prepared_files),
        "job_ids": job_ids,
        "files": response_files,
    }


@batch_router.get("/batches/{batch_id}", summary="Get OCR batch status")
async def get_upload_batch(
    batch_id: str,
    refresh: bool = False,
    current_user: dict = Depends(require_roles(UserRole.OPERATOR, UserRole.REVIEWER)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        batch_uuid = uuid.UUID(batch_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Invalid batch_id format")

    upload_mod = _get_upload_mod()
    batch = await upload_mod.get_owned_ocr_batch(db, batch_uuid, current_user)
    if refresh:
        await upload_mod._refresh_batch_counters(db, batch.id)
        await db.flush()
    files, jobs_by_file, extractions_by_job = await upload_mod._batch_child_rows(db, batch)

    return {
        "batch_id": str(batch.id),
        "status": batch.status,
        "file_count": batch.file_count,
        "completed_count": batch.completed_count,
        "failed_count": batch.failed_count,
        "needs_review_count": batch.needs_review_count,
        "validated_count": batch.validated_count,
        "total_bytes": batch.total_bytes,
        "total_pages": batch.total_pages,
        "files": [
            {
                "file_id": str(uploaded.id),
                "job_id": str(jobs_by_file[uploaded.id].id) if uploaded.id in jobs_by_file else None,
                "filename": uploaded.original_filename,
                "status": uploaded.status,
                "job_status": jobs_by_file[uploaded.id].status if uploaded.id in jobs_by_file else None,
                "extraction_status": (
                    extractions_by_job[jobs_by_file[uploaded.id].id].status
                    if uploaded.id in jobs_by_file and jobs_by_file[uploaded.id].id in extractions_by_job
                    else None
                ),
                "error_message": jobs_by_file[uploaded.id].error_message if uploaded.id in jobs_by_file else None,
                "completed_at": (
                    jobs_by_file[uploaded.id].completed_at.isoformat()
                    if uploaded.id in jobs_by_file and jobs_by_file[uploaded.id].completed_at
                    else None
                ),
                "warnings_count": (
                    len(extractions_by_job[jobs_by_file[uploaded.id].id].validation_errors or [])
                    if uploaded.id in jobs_by_file and jobs_by_file[uploaded.id].id in extractions_by_job
                    else 0
                ),
                "field_count": (
                    int(extractions_by_job[jobs_by_file[uploaded.id].id].fields_extracted or 0)
                    if uploaded.id in jobs_by_file and jobs_by_file[uploaded.id].id in extractions_by_job
                    else 0
                ),
                "mime_type": uploaded.mime_type,
                "file_size": uploaded.file_size,
                "page_count": uploaded.page_count,
            }
            for uploaded in files
        ],
    }


@batch_router.post("/batches/{batch_id}/export/zip", summary="Generate batch ZIP export")
async def export_batch_zip(
    batch_id: str,
    request: Request,
    current_user: dict = Depends(require_roles(UserRole.OPERATOR)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        batch_uuid = uuid.UUID(batch_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Invalid batch_id format")

    upload_mod = _get_upload_mod()
    batch = await upload_mod.get_owned_ocr_batch(db, batch_uuid, current_user)
    manifest_files, exports, file_count = await upload_mod._batch_zip_payload(db, batch)
    included_count = len(exports)
    skipped_count = file_count - included_count
    if included_count == 0:
        raise HTTPException(status_code=409, detail="No completed child results available for ZIP export")

    generated_at = datetime.now(timezone.utc).isoformat()
    manifest = {
        "batch_id": str(batch.id),
        "generated_at": generated_at,
        "file_count": file_count,
        "included_count": included_count,
        "skipped_count": skipped_count,
        "files": manifest_files,
    }

    batch_dir = settings.output_path / "batches" / str(batch.id)
    batch_dir.mkdir(parents=True, exist_ok=True)
    zip_path = batch_dir / f"batch-{batch.id}.zip"
    tmp_zip_path = batch_dir / f"batch-{batch.id}.zip.tmp"
    try:
        with upload_mod.zipfile.ZipFile(tmp_zip_path, "w", compression=upload_mod.zipfile.ZIP_DEFLATED) as zf:
            for item in exports:
                zf.writestr(item["zip_name"], json.dumps(item["payload"], ensure_ascii=True, indent=2))
            zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=True, indent=2))
        tmp_zip_path.replace(zip_path)
    except Exception:
        if tmp_zip_path.exists():
            tmp_zip_path.unlink(missing_ok=True)
        raise

    batch.output_zip_path = str(zip_path)
    await upload_mod.write_audit_log(
        db,
        action="batch.export.zip",
        event_type=LogEventType.DATA_EXPORT,
        status="success",
        user_id=current_user.get("user_id"),
        org_id=current_user.get("org_id"),
        resource_type="ocr_batch",
        resource_id=batch.id,
        message="Batch ZIP export generated",
        metadata={
            "batch_id": str(batch.id),
            "included_count": included_count,
            "skipped_count": skipped_count,
            "zip_filename": zip_path.name,
        },
        request=request,
    )
    await db.commit()
    return {
        "batch_id": str(batch.id),
        "zip_ready": True,
        "download_url": f"/api/v1/upload/batches/{batch.id}/export/zip",
        "included_count": included_count,
        "skipped_count": skipped_count,
    }


@batch_router.get("/batches/{batch_id}/export/zip", summary="Download batch ZIP export")
async def download_batch_zip(
    batch_id: str,
    current_user: dict = Depends(require_roles(UserRole.OPERATOR, UserRole.REVIEWER)),
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    try:
        batch_uuid = uuid.UUID(batch_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Invalid batch_id format")

    upload_mod = _get_upload_mod()
    batch = await upload_mod.get_owned_ocr_batch(db, batch_uuid, current_user)
    if not batch.output_zip_path:
        raise HTTPException(status_code=404, detail="Batch ZIP export has not been generated")
    zip_path = Path(batch.output_zip_path)
    if not zip_path.exists() or not zip_path.is_file():
        raise HTTPException(status_code=410, detail="Batch ZIP export is no longer available")
    return FileResponse(
        path=str(zip_path),
        media_type="application/zip",
        filename=zip_path.name,
    )
