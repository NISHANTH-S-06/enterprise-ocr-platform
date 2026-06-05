"""
Upload router — secure file upload with validation and OCR trigger.
Integrates with ocr_service and feeds results into extracted_data table.
"""
from __future__ import annotations

import hashlib
import asyncio
import json
import re
import time
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any, Optional

import aiofiles
from fastapi import APIRouter, BackgroundTasks, Body, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal, get_db
from app.core.security import require_roles
from sqlalchemy import select
from app.models.batch import OCRBatch
from app.models.enums import BatchStatus, ExtractionStatus, FileCategory, FileStatus, JobStatus, LogEventType, LogLevel, UserRole
from app.models.extracted_data import ExtractedData
from app.models.file import UploadedFile
from app.models.job import OCRJob
from app.models.user import Organisation
from app.services.ownership_service import get_owned_ocr_batch, get_owned_ocr_job, is_superadmin
from app.services.audit_service import write_audit_log
from app.services.gemini_service import gemini_enrich_missing_fields
from app.services.tenant_limits import (
    DEFAULT_MAX_BATCH_FILES,
    DEFAULT_MAX_BATCH_TOTAL_MB,
    check_batch_limits_for_user,
    is_ai_fallback_enabled_for_user,
)
from app.services.validation_service import validate_mapped_fields
from app.services.validation_engine import run_validation
from app.services.confidence_engine import score_fields, build_field_response
from app.services.final_field_normalizer import build_final_ocr_decision
from app.services.correction_memory import (
    apply_user_corrections,
    build_correction_memory_records,
    recompute_final_decision_after_corrections,
    resolve_company_for_correction_memory,
)
from app.services.company_resolver import resolve_company_id
from app.services.memory_apply_engine import (
    apply_memory_to_canonical_fields,
    load_company_correction_memory,
)

router = APIRouter(prefix="/upload", tags=["Upload"])
settings = get_settings()
OCR_DEMO_JOBS: dict[str, dict[str, Any]] = {}
OCR_RESULT_CACHE: dict[str, dict[str, Any]] = {}
OCR_FAILURE_TEXT = "OCR extraction failed"

ALLOWED_MIMES = {
    "application/pdf", "image/png", "image/jpeg", "image/jpg",
    "image/tiff", "image/bmp", "image/webp",
}
MIME_EXTENSIONS = {
    "application/pdf": {".pdf"},
    "image/png": {".png"},
    "image/jpeg": {".jpg", ".jpeg"},
    "image/jpg": {".jpg", ".jpeg"},
    "image/tiff": {".tif", ".tiff"},
    "image/bmp": {".bmp"},
    "image/webp": {".webp"},
}
CANONICAL_EXTENSION = {
    "application/pdf": ".pdf",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/tiff": ".tiff",
    "image/bmp": ".bmp",
    "image/webp": ".webp",
}
WINDOWS_RESERVED_NAMES = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}
MAX_IMAGE_PIXELS = 25_000_000
MAX_UPLOAD_PDF_PAGES = 100
MAX_BATCH_FILES = DEFAULT_MAX_BATCH_FILES
MAX_BATCH_TOTAL_BYTES = DEFAULT_MAX_BATCH_TOTAL_MB * 1024 * 1024
MAGIC_BYTES = {
    b"%PDF": "application/pdf",
    b"\x89PNG": "image/png",
    b"\xff\xd8\xff": "image/jpeg",
    b"II*\x00": "image/tiff",
    b"MM\x00*": "image/tiff",
    b"BM": "image/bmp",
    b"RIFF": "image/webp",
}
SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._ -]+")
ZIP_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _detect_mime(data: bytes) -> str:
    header = data[:8]
    for sig, mime in MAGIC_BYTES.items():
        if header[:len(sig)] == sig:
            if sig == b"RIFF" and data[8:12] == b"WEBP":
                return "image/webp"
            return mime
    return "application/octet-stream"


def _safe_filename(filename: str | None) -> str:
    raw = (filename or "upload").replace("\\", "/").split("/")[-1].strip()
    if "\x00" in raw:
        raise HTTPException(status_code=400, detail="Invalid filename")
    cleaned = SAFE_FILENAME_RE.sub("_", raw).strip(" .")
    if not cleaned:
        cleaned = "upload"
    stem = Path(cleaned).stem.lower().rstrip(" .")
    if stem in WINDOWS_RESERVED_NAMES:
        raise HTTPException(status_code=400, detail="Reserved filename is not allowed")
    return cleaned[:180]


def _validate_extension(filename: str, detected_mime: str) -> str:
    suffixes = [s.lower() for s in Path(filename).suffixes]
    if not suffixes:
        raise HTTPException(status_code=415, detail="File extension is required")
    ext = suffixes[-1]
    allowed = MIME_EXTENSIONS.get(detected_mime, set())
    if ext not in allowed:
        raise HTTPException(status_code=415, detail=f"File extension does not match content type: {ext}")
    blocked = {".exe", ".bat", ".cmd", ".com", ".msi", ".ps1", ".scr", ".vbs", ".js", ".jar"}
    if any(s in blocked for s in suffixes):
        raise HTTPException(status_code=415, detail="Executable file extensions are not allowed")
    return CANONICAL_EXTENSION[detected_mime]


def _validate_file_content(data: bytes, detected_mime: str) -> None:
    if detected_mime == "application/pdf":
        try:
            import fitz  # type: ignore
            doc = fitz.open(stream=data, filetype="pdf")
            try:
                if doc.page_count <= 0:
                    raise HTTPException(status_code=400, detail="PDF has no pages")
                if doc.page_count > MAX_UPLOAD_PDF_PAGES:
                    raise HTTPException(status_code=413, detail=f"PDF exceeds {MAX_UPLOAD_PDF_PAGES} pages")
            finally:
                doc.close()
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Corrupted PDF: {exc}")
        return

    try:
        from PIL import Image, UnidentifiedImageError
        import io
        with Image.open(io.BytesIO(data)) as img:
            width, height = img.size
            if width <= 0 or height <= 0:
                raise HTTPException(status_code=400, detail="Invalid image dimensions")
            if width * height > MAX_IMAGE_PIXELS:
                raise HTTPException(status_code=413, detail="Image dimensions exceed safety limit")
            img.verify()
    except HTTPException:
        raise
    except UnidentifiedImageError:
        raise HTTPException(status_code=400, detail="Corrupted image")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Corrupted image: {exc}")


async def _prepare_upload_file(file: UploadFile) -> dict[str, Any]:
    max_upload_bytes = settings.MAX_FILE_SIZE_MB * 1024 * 1024
    data = await file.read(max_upload_bytes + 1)
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(data) > max_upload_bytes:
        raise HTTPException(status_code=413, detail=f"File exceeds {settings.MAX_FILE_SIZE_MB} MB")

    safe_name = _safe_filename(file.filename)
    detected = _detect_mime(data)
    if detected == "image/jpeg" and (Path(safe_name).suffix.lower() in {".jpg", ".jpeg"}):
        detected = "image/jpeg"
    if detected not in ALLOWED_MIMES:
        raise HTTPException(status_code=415, detail=f"Unsupported file type: {detected}")
    ext = _validate_extension(safe_name, detected)
    _validate_file_content(data, detected)
    return {
        "data": data,
        "safe_name": safe_name,
        "detected": detected,
        "ext": ext,
        "content_type": file.content_type,
    }


async def _store_prepared_upload(prepared: dict[str, Any]) -> dict[str, Any]:
    data = prepared["data"]
    checksum = hashlib.sha256(data).hexdigest()
    stored = f"{checksum[:16]}_{uuid.uuid4().hex}{prepared['ext']}"
    dest = settings.upload_path / stored
    tmp_dest = settings.upload_path / f".{stored}.tmp"

    upload_save_t0 = time.monotonic()
    async with aiofiles.open(tmp_dest, "wb") as f_out:
        await f_out.write(data)
    tmp_dest.replace(dest)
    upload_save_ms = int((time.monotonic() - upload_save_t0) * 1000)
    return {
        "checksum": checksum,
        "stored": stored,
        "dest": dest,
        "upload_save_ms": upload_save_ms,
    }


def _file_category_for_mime(detected: str) -> str:
    return FileCategory.PDF.value if detected == "application/pdf" else FileCategory.IMAGE.value


async def _tenant_limit_source(db: AsyncSession, current_user: dict[str, Any]) -> Any:
    org_id = current_user.get("org_id")
    if not org_id or not hasattr(db, "get"):
        return current_user
    org = await db.get(Organisation, uuid.UUID(str(org_id)))
    return org or current_user


def _safe_zip_stem(filename: str | None) -> str:
    stem = Path((filename or "document").replace("\\", "/").split("/")[-1]).stem
    cleaned = ZIP_NAME_RE.sub("_", stem).strip("._-")
    return cleaned[:80] or "document"


def _validated_export_payload(validated_fields: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for key, value in (validated_fields or {}).items():
        if key == "item_rows":
            continue
        if isinstance(value, dict):
            fields[key] = value.get("value", "")
        else:
            fields[key] = value
    return {"fields": fields}


async def _batch_child_rows(db: AsyncSession, batch: OCRBatch) -> tuple[list[UploadedFile], dict[uuid.UUID, OCRJob], dict[uuid.UUID, ExtractedData]]:
    files_result = await db.execute(
        select(UploadedFile).where(UploadedFile.batch_id == batch.id).order_by(UploadedFile.created_at)
    )
    uploaded_files = files_result.scalars().all()
    jobs_result = await db.execute(select(OCRJob).where(OCRJob.batch_id == batch.id))
    jobs = jobs_result.scalars().all()
    jobs_by_file = {job.file_id: job for job in jobs}
    job_ids = [job.id for job in jobs]
    extractions_by_job: dict[uuid.UUID, ExtractedData] = {}
    if job_ids:
        extracted_result = await db.execute(select(ExtractedData).where(ExtractedData.job_id.in_(job_ids)))
        extractions_by_job = {item.job_id: item for item in extracted_result.scalars().all()}
    return uploaded_files, jobs_by_file, extractions_by_job


async def _batch_zip_payload(db: AsyncSession, batch: OCRBatch) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    uploaded_files, jobs_by_file, extractions_by_job = await _batch_child_rows(db, batch)

    manifest_files: list[dict[str, Any]] = []
    exports: list[dict[str, Any]] = []
    used_names: set[str] = set()
    for index, uploaded in enumerate(uploaded_files, start=1):
        job = jobs_by_file.get(uploaded.id)
        extraction = extractions_by_job.get(job.id) if job else None
        included = bool(job and job.status == JobStatus.COMPLETED.value and extraction)
        skip_reason = None
        if not job:
            skip_reason = "missing_job"
        elif job.status != JobStatus.COMPLETED.value:
            skip_reason = job.status
        elif not extraction:
            skip_reason = "missing_extraction"

        entry = {
            "filename": uploaded.original_filename,
            "file_id": str(uploaded.id),
            "job_id": str(job.id) if job else None,
            "status": job.status if job else uploaded.status,
            "extraction_id": str(extraction.id) if extraction else None,
            "field_count": int(extraction.fields_extracted or 0) if extraction else 0,
            "warnings_count": len(extraction.validation_errors or []) if extraction else 0,
            "included": included,
            "skip_reason": skip_reason,
        }
        manifest_files.append(entry)
        if included and extraction:
            stem = _safe_zip_stem(uploaded.original_filename)
            name = f"{stem}.json"
            if name in used_names:
                name = f"{stem}-{index}-{str(job.id)[:8]}.json"
            used_names.add(name)
            exports.append({
                "zip_name": name,
                "payload": _validated_export_payload(extraction.validated_fields),
            })
    return manifest_files, exports, len(uploaded_files)


def _build_demo_extraction(result: dict[str, Any]) -> dict[str, Any]:
    text = result.get("raw_text") or result.get("extracted_text") or result.get("text") or result.get("full_text") or ""
    confidence = float(result.get("avg_confidence", 0.0) or 0.0)
    return {
        "raw_text": text,
        "page_count": int(result.get("page_count", 0) or 0),
        "avg_confidence": confidence,
        "fields": {
            "full_text": {
                "value": text,
                "confidence": confidence,
                "method": "ocr",
            },
            "page_count": {
                "value": int(result.get("page_count", 0) or 0),
                "confidence": 1.0,
                "method": "metadata",
            },
            "avg_confidence": {
                "value": confidence,
                "confidence": confidence,
                "method": "metadata",
            },
        },
    }


def _extract_text_from_result(result: dict[str, Any]) -> str:
    nested = result.get("ocr_result") if isinstance(result.get("ocr_result"), dict) else {}
    return (
        result.get("raw_text")
        or result.get("extracted_text")
        or result.get("text")
        or result.get("full_text")
        or nested.get("raw_text")
        or nested.get("extracted_text")
        or nested.get("text")
        or nested.get("full_text")
        or ""
    )


def _flatten_fields(mapped_fields: dict[str, Any]) -> tuple[dict[str, Any], dict[str, float]]:
    fields: dict[str, Any] = {}
    confidence: dict[str, float] = {}
    try:
        from app.services.company_form_mapper import normalize_mapped_fields
        mapped_fields = normalize_mapped_fields(mapped_fields or {})
    except Exception:
        mapped_fields = mapped_fields or {}
    aliases = {
        "supplier_name": "vendor_name",
        "customer_name": "buyer_name",
        "billing_address": "address",
        "unit_price": "rate",
        "subtotal": "taxable_value",
        "tax_amount": "total_tax",
        "gst_number": "gstin",
        "invoice_number": "invoice_no",
        "purchase_order_number": "po_no",
        "po_number": "po_no",
        "account_number": "account_no",
        "igst_amount": "igst",
        "cgst_amount": "cgst",
        "sgst_amount": "sgst",
    }
    for key, data in (mapped_fields or {}).items():
        if isinstance(data, dict):
            value = data.get("value", "")
            conf = float(data.get("confidence", 0.0) or 0.0)
        else:
            value = data
            conf = 0.0
        fields[key] = value
        confidence[key] = conf
        if key in aliases and aliases[key] not in fields:
            fields[aliases[key]] = value
            confidence[aliases[key]] = conf
    for required in (
        "invoice_no", "invoice_number", "invoice_date", "po_no", "po_date",
        "vendor_name", "supplier_name", "buyer_name", "consignee_name", "gstin",
        "buyer_gstin", "vendor_gstin", "gst_number", "address", "phone", "email",
        "item_name", "item_description", "hsn_code", "quantity", "rate",
        "taxable_value", "cgst", "sgst", "igst", "total_tax", "total_amount",
        "amount_in_words", "bank_name", "account_no", "ifsc", "document_type",
    ):
        fields.setdefault(required, "")
        confidence.setdefault(required, 0.0)
    return fields, confidence


def _ai_fallback_target_fields(mapped_result: dict[str, Any]) -> list[str]:
    mapped_fields = mapped_result.get("mapped_fields", {}) if mapped_result else {}
    targets = list(mapped_result.get("missing_fields", []) or [])
    for field, data in mapped_fields.items():
        if not isinstance(data, dict):
            continue
        try:
            confidence = float(data.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        if confidence < settings.AI_FALLBACK_REGEX_PROTECT_CONFIDENCE:
            targets.append(field)
    return list(dict.fromkeys(targets))


def _ai_fallback_audit_metadata(mapped_result: dict[str, Any], validation_warnings: list[str] | None = None) -> dict[str, Any] | None:
    fallback = mapped_result.get("ai_fallback") if mapped_result else None
    if not isinstance(fallback, dict):
        return None
    rejected = list(fallback.get("fields_rejected", []) or [])
    needs_review = bool(fallback.get("needs_review")) or bool(rejected) or bool(validation_warnings)
    return {
        "provider": fallback.get("provider", "gemini"),
        "model": fallback.get("model", settings.AI_FALLBACK_MODEL),
        "fields_requested": list(fallback.get("fields_requested", []) or []),
        "fields_recovered": list(fallback.get("fields_recovered", []) or []),
        "fields_rejected": rejected,
        "needs_review": needs_review,
        "reason": fallback.get("reason", "used"),
    }


def _should_attempt_ai_fallback(ai_targets: list[str], tenant_source: Any) -> bool:
    return bool(settings.AI_FALLBACK_ENABLED and ai_targets and is_ai_fallback_enabled_for_user(tenant_source))


def _build_detection_report(mapped_fields: dict[str, Any], validation: dict[str, Any]) -> dict[str, Any]:
    """Build a safe, redacted per-field detection summary for the UI review panel."""
    validated = validation.get("validated_fields", {})
    report: dict[str, Any] = {
        "field_count": validation.get("field_count", 0),
        "missing_count": 0,
        "weak_count": 0,
        "ai_recovered_count": 0,
        "needs_review_count": len(validation.get("warnings", [])),
        "fields": {},
    }
    all_keys = set(mapped_fields.keys()) | set(validated.keys())
    for field in all_keys:
        if field == "item_rows":
            continue
        vf = validated.get(field)
        mf = mapped_fields.get(field, {})
        if not vf:
            report["missing_count"] += 1
            report["fields"][field] = {"status": "missing", "method": "—", "confidence": 0.0}
            continue
        val = vf.get("value", "") if isinstance(vf, dict) else str(vf)
        conf = float(vf.get("confidence", 0.5) if isinstance(vf, dict) else 0.5)
        method = vf.get("method", "regex") if isinstance(vf, dict) else "regex"
        evidence_raw = vf.get("source_evidence", "") if isinstance(vf, dict) else ""
        # Truncate evidence to ≤80 chars — safe snippet, no full OCR text
        evidence_snip = (evidence_raw[:80] + "…") if len(evidence_raw) > 80 else evidence_raw
        if method == "gemini":
            report["ai_recovered_count"] += 1
        if conf < 0.65:
            report["weak_count"] += 1
        status = "ai_recovered" if method == "gemini" else ("weak" if conf < 0.65 else "ok")
        report["fields"][field] = {
            "status": status,
            "method": method,
            "confidence": round(conf, 3),
            "evidence": evidence_snip,
        }
    return report


def _build_ocr_response(result: dict[str, Any], mapped_result: dict[str, Any], warnings: list[str] | None = None) -> dict[str, Any]:
    text = _extract_text_from_result(result)
    if text == OCR_FAILURE_TEXT:
        return {
            "success": True,
            "text": OCR_FAILURE_TEXT,
            "raw_text_length": len(OCR_FAILURE_TEXT),
            "detected_line_count": 1,
            "raw_text_preview": OCR_FAILURE_TEXT,
            "fields": {},
            "confidence": {},
            "warnings": list(warnings or []) + [str(w) for w in result.get("warnings", []) if w],
        }
    mapped_fields = mapped_result.get("mapped_fields", {}) if mapped_result else {}
    fields, confidence = _flatten_fields(mapped_fields)
    out_warnings = list(warnings or [])
    if isinstance(result.get("warnings"), list):
        out_warnings.extend(str(w) for w in result.get("warnings", []) if w)
    for key in ("warning", "weak_ocr_message", "install_hint"):
        if result.get(key):
            out_warnings.append(str(result[key]))
    return {
        "success": bool(result.get("success", True)),
        "text": text,
        "raw_text_length": int(result.get("raw_text_length", len(text)) or 0),
        "detected_line_count": int(result.get("detected_line_count", len([l for l in text.splitlines() if l.strip()])) or 0),
        "raw_text_preview": result.get("raw_text_preview", text[:300]),
        "fields": fields,
        "confidence": confidence,
        "warnings": out_warnings,
    }


def _empty_ocr_response(warnings: list[str] | None = None) -> dict[str, Any]:
    return {
        "success": True,
        "text": "",
        "raw_text_length": 0,
        "detected_line_count": 0,
        "raw_text_preview": "",
        "fields": {},
        "confidence": {},
        "warnings": list(warnings or []),
    }


async def _refresh_batch_counters(db: AsyncSession, batch_id: uuid.UUID | None) -> None:
    if not batch_id:
        return
    batch = await db.get(OCRBatch, batch_id)
    if not batch:
        return

    _uploaded_files, jobs_by_file, extractions_by_job = await _batch_child_rows(db, batch)
    jobs = list(jobs_by_file.values())
    completed_count = sum(1 for job in jobs if job.status == JobStatus.COMPLETED.value)
    failed_count = sum(1 for job in jobs if job.status == JobStatus.FAILED.value)
    total_pages = sum(int(job.page_count or 0) for job in jobs)

    extractions = list(extractions_by_job.values())
    needs_review_count = sum(1 for item in extractions if item.status == ExtractionStatus.NEEDS_REVIEW.value)
    validated_count = sum(1 for item in extractions if item.status == ExtractionStatus.VALIDATED.value)

    batch.completed_count = completed_count
    batch.failed_count = failed_count
    batch.needs_review_count = needs_review_count
    batch.validated_count = validated_count
    batch.total_pages = total_pages

    finished_count = completed_count + failed_count
    if batch.file_count and finished_count >= batch.file_count:
        batch.completed_at = batch.completed_at or datetime.utcnow()
        if failed_count == batch.file_count:
            batch.status = BatchStatus.FAILED.value
        elif failed_count:
            batch.status = BatchStatus.PARTIAL_FAILED.value
        else:
            batch.status = BatchStatus.COMPLETED.value
    else:
        batch.status = BatchStatus.PROCESSING.value


def cleanup_expired_batch_zips(
    base_dir: Path | None = None,
    *,
    retention_days: int | None = None,
    max_total_mb: int | None = None,
    active_paths: set[str] | None = None,
) -> dict[str, Any]:
    root = base_dir or (settings.output_path / "batches")
    retention_days = settings.BATCH_ZIP_RETENTION_DAYS if retention_days is None else retention_days
    max_total_mb = settings.BATCH_ZIP_MAX_MB if max_total_mb is None else max_total_mb
    active = {str(Path(path).resolve()) for path in (active_paths or set())}
    summary = {"deleted_count": 0, "deleted_bytes": 0, "kept_count": 0, "errors": []}
    if not root.exists():
        return summary

    now = datetime.now(timezone.utc)
    zip_paths = [path for path in root.rglob("*.zip") if path.is_file()]
    kept: list[Path] = []
    for path in zip_paths:
        resolved = str(path.resolve())
        if resolved in active:
            summary["kept_count"] += 1
            kept.append(path)
            continue
        try:
            stat = path.stat()
            age = now - datetime.fromtimestamp(stat.st_mtime, timezone.utc)
            if age > timedelta(days=retention_days):
                size = stat.st_size
                path.unlink()
                summary["deleted_count"] += 1
                summary["deleted_bytes"] += size
            else:
                kept.append(path)
                summary["kept_count"] += 1
        except Exception as exc:
            summary["errors"].append(str(exc))

    max_bytes = max_total_mb * 1024 * 1024
    total_size = sum(path.stat().st_size for path in kept if path.exists())
    for path in sorted(kept, key=lambda item: item.stat().st_mtime if item.exists() else 0):
        if total_size <= max_bytes:
            break
        resolved = str(path.resolve())
        if resolved in active or not path.exists():
            continue
        try:
            size = path.stat().st_size
            path.unlink()
            total_size -= size
            summary["deleted_count"] += 1
            summary["deleted_bytes"] += size
            summary["kept_count"] = max(0, summary["kept_count"] - 1)
        except Exception as exc:
            summary["errors"].append(str(exc))
    return summary


async def _ocr_background(file_path: str, mime_type: str, job_id: str, checksum: str) -> None:
    """Background OCR task — runs after upload response is returned."""
    job_t0 = time.monotonic()
    try:
        from app.services.ocr_service import process_file
        async with aiofiles.open(file_path, "rb") as f:
            data = await f.read()
        logger.info(f"OCR background input: job_id={job_id} path={file_path} mime={mime_type} size={len(data)}")
        async with AsyncSessionLocal() as db:
            job = await db.get(OCRJob, uuid.UUID(job_id))
            await write_audit_log(
                db,
                action="ocr.start",
                event_type=LogEventType.OCR_JOB,
                status="success",
                user_id=job.created_by_id if job else None,
                org_id=job.org_id if job else None,
                resource_type="ocr_job",
                resource_id=job_id,
                message="OCR started",
                metadata={"mime_type": mime_type, "file_size": len(data)},
            )
            await db.commit()
        cached = OCR_RESULT_CACHE.get(checksum)
        if cached:
            result = dict(cached)
            result["cache_hit"] = True
            logger.info("OCR cache hit: job_id={} checksum={}", job_id, checksum[:16])
        else:
            ocr_t0 = time.monotonic()
            result = await asyncio.wait_for(process_file(data, mime_type), timeout=settings.OCR_TIMEOUT_SECONDS)
            result["ocr_wall_ms"] = int((time.monotonic() - ocr_t0) * 1000)
            OCR_RESULT_CACHE[checksum] = dict(result)
        extracted_demo = _build_demo_extraction(result)
        text = _extract_text_from_result(result)
        
        from app.services.company_form_mapper import auto_fill, map_sales_invoice_fields, classify_document, missing_fields_for_doc, normalize_mapped_fields

        # ── Document classification ───────────────────────────────────────────
        cls_result = classify_document(text, filename=Path(file_path).name)
        document_type = "sales" if cls_result["mapper"] == "sales" else "general"
        logger.info("Classifier: job_id={} doc_type={} mapper={} score={} rule={}",
                    job_id, cls_result["doc_type"], cls_result["mapper"],
                    cls_result["score"], cls_result["matched_rule"])

        mapper_t0 = time.monotonic()
        if not text:
            mapped_result = {
                "success": bool(result.get("success", True)),
                "mapped_fields": {},
                "missing_fields": [],
                "message": "No OCR text returned.",
            }
        else:
            if document_type == "sales":
                mapped_result = map_sales_invoice_fields(text)
                from app.services.company_form_mapper import flatten_item_rows_for_legacy_fields
                mapped_result = flatten_item_rows_for_legacy_fields(mapped_result, cls_result["doc_type"], text)
            else:
                # P3: pass doc_type so missing_fields is filtered to relevant fields only
                mapped_result = auto_fill(text, doc_type=cls_result["doc_type"].lower())
        mapped_result["mapped_fields"] = normalize_mapped_fields(mapped_result.get("mapped_fields", {}))
        regex_fields = dict(mapped_result.get("mapped_fields", {}))
        effective_doc_type = (mapped_result.get("document_type") or cls_result["doc_type"] or document_type or "general").lower()
        mapped_result["document_type"] = effective_doc_type
        mapped_result["missing_fields"] = missing_fields_for_doc(mapped_result["mapped_fields"], effective_doc_type)
        non_doc_values = [
            v.get("value") if isinstance(v, dict) else v
            for k, v in mapped_result["mapped_fields"].items()
            if k not in {"document_type", "doc_type", "item_rows"}
        ]
        if text and not any(str(v or "").strip() for v in non_doc_values):
            mapped_result["weak_mapping"] = True
            mapped_result["message"] = "OCR text detected but mapping failed"
            mapped_result["raw_text_preview"] = text[:500]
        logger.info(
            "AutoFillDebug: job_id={} document_type={} text_len={} preview={} extracted_keys={} missing_fields={}",
            job_id,
            effective_doc_type,
            len(text or ""),
            (text or "")[:500].replace("\n", " "),
            sorted(mapped_result.get("mapped_fields", {}).keys()),
            mapped_result.get("missing_fields", []),
        )
        
        mapper_ms = int((time.monotonic() - mapper_t0) * 1000)
        ai_targets = _ai_fallback_target_fields(mapped_result)
        tenant_ai_source: Any = None
        async with AsyncSessionLocal() as db:
            job = await db.get(OCRJob, uuid.UUID(job_id))
            if job and job.org_id:
                tenant_ai_source = await db.get(Organisation, job.org_id)
        if _should_attempt_ai_fallback(ai_targets, tenant_ai_source):
            mapped_result = await gemini_enrich_missing_fields(
                mapped_result,
                text,
                mapped_result.get("document_type", document_type),
                missing_fields=mapped_result.get("missing_fields", []),
            )
            mapped_result["mapped_fields"] = normalize_mapped_fields(mapped_result.get("mapped_fields", {}))
            mapped_result["missing_fields"] = missing_fields_for_doc(
                mapped_result["mapped_fields"],
                mapped_result.get("document_type", effective_doc_type),
            )
        ocr_response = _build_ocr_response(result, mapped_result)

        # ── Task 2+3: Validate mapped fields before DB write ─────────────────
        mapped_for_validation = mapped_result.get("mapped_fields", {})
        validation = validate_mapped_fields(
            mapped_for_validation,
            raw_text=text,
            doc_type=mapped_result.get("document_type", document_type),
        )

        # ── Phase 2: Company detection + extended validation + confidence ────
        detected_company_info: dict[str, Any] = {}
        company_resolution_warnings: list[str] = []
        p2_field_response: list[dict] = []
        p2_summary_counts: dict[str, Any] = {}
        final_ocr_decision: dict[str, Any] = {}
        memory_apply_result: dict[str, Any] = {
            "memory_suggestions": {},
            "memory_applied_fields": [],
            "memory_suggestion_fields": [],
            "memory_applied_count": 0,
            "memory_suggestion_count": 0,
            "warnings": [],
        }
        memory_warnings: list[str] = []
        try:
            from app.services.company_detector import detect_company
            async with AsyncSessionLocal() as _det_db:
                det = await detect_company(text, _det_db)
            detected_company_info = {
                "company_id":    str(det.company_id) if det.company_id else None,
                "company_name":  det.company_name,
                "match_method":  det.match_method,
                "confidence":    det.confidence,
                "is_known":      det.is_known,
                "matched_value": det.matched_value,
            }

            # Extended validation (10 rules)
            ext_val = run_validation(
                mapped_for_validation,
                raw_text=text,
                doc_type=mapped_result.get("document_type", document_type),
            )
            # Merge ext_val warnings into existing validation warnings (deduplicate)
            existing_w = set(validation["warnings"])
            for w in ext_val.warnings:
                if w not in existing_w:
                    validation["warnings"].append(w)
                    existing_w.add(w)

            # Confidence scoring
            conf_summary = score_fields(
                mapped_for_validation,
                validation_errors=ext_val.errors + validation["errors"],
            )
            p2_field_response  = build_field_response(conf_summary)
            p2_summary_counts  = {
                "auto_filled_count":      conf_summary.auto_filled_count,
                "need_review_count":      conf_summary.need_review_count,
                "missing_count":          conf_summary.missing_count,
                "validation_error_count": conf_summary.validation_error_count,
                "export_allowed":         conf_summary.export_allowed,
                "overall_confidence":     conf_summary.overall_confidence,
            }
            final_ocr_decision = build_final_ocr_decision(p2_field_response)
        except Exception as _p2_exc:
            logger.warning("Phase 2 enrichment failed (non-fatal): job_id={} err={}", job_id, _p2_exc)
        # ── End Phase 2 ───────────────────────────────────────────────────────
        if isinstance(final_ocr_decision.get("canonical_fields"), dict):
            try:
                async with AsyncSessionLocal() as _resolve_db:
                    resolved_company = await resolve_company_id(
                        _resolve_db,
                        text,
                        final_ocr_decision["canonical_fields"],
                        detected_company_info,
                    )
                if isinstance(resolved_company, dict):
                    existing_company = detected_company_info if isinstance(detected_company_info, dict) else {}
                    company_resolution_warnings = list(resolved_company.get("warnings") or [])
                    detected_company_info = {
                        **existing_company,
                        **resolved_company,
                        "warnings": list(dict.fromkeys(
                            list(existing_company.get("warnings") or []) + company_resolution_warnings
                        )),
                    }
            except Exception as _resolver_exc:
                company_resolution_warnings.append(f"Company resolution failed: {_resolver_exc}")
                logger.warning("Company resolver failed (non-fatal): job_id={} err={}", job_id, _resolver_exc)
            try:
                company_id = (
                    detected_company_info.get("company_id")
                    if isinstance(detected_company_info, dict)
                    else None
                )
                if company_id:
                    async with AsyncSessionLocal() as _memory_db:
                        memories = await load_company_correction_memory(
                            _memory_db,
                            company_id,
                            mapped_result.get("document_type") or document_type,
                        )
                    memory_apply_result = apply_memory_to_canonical_fields(
                        final_ocr_decision["canonical_fields"],
                        memories,
                    )
                    memory_warnings = [
                        str(w) for w in memory_apply_result.get("warnings", []) if w
                    ]
                    final_ocr_decision = recompute_final_decision_after_corrections(
                        memory_apply_result.get("canonical_fields", {})
                    )
                else:
                    memory_warnings.append("Correction memory skipped: company_id unavailable")
            except Exception as _memory_exc:
                memory_warnings.append(f"Correction memory apply failed: {_memory_exc}")
                logger.warning("Correction memory apply failed (non-fatal): job_id={} err={}", job_id, _memory_exc)
        ai_audit_metadata = _ai_fallback_audit_metadata(mapped_result, validation["warnings"])
        ocr_warnings = list(ocr_response.get("warnings", []))
        ocr_warnings.extend(validation["warnings"])
        ocr_warnings.extend(company_resolution_warnings)
        ocr_warnings.extend(memory_warnings)
        # Task 5: Mock OCR regression — mark as needs_review, add hard warning
        needs_ai_review = bool(ai_audit_metadata and ai_audit_metadata.get("needs_review"))
        is_mock = bool(result.get("is_mock_ocr") or "Mock OCR" in text or not validation["valid"] or needs_ai_review)
        extraction_status = ExtractionStatus.VALIDATED.value if (validation["valid"] and not is_mock) \
            else ExtractionStatus.NEEDS_REVIEW.value

        async with AsyncSessionLocal() as db:
            job_uuid = uuid.UUID(job_id)
            job = await db.get(OCRJob, job_uuid)
            if not job:
                raise RuntimeError(f"OCRJob {job_id} not found")

            completed_at = datetime.utcnow()
            fields = extracted_demo["fields"]
            extracted = ExtractedData(
                job_id=job.id,
                file_id=job.file_id,
                template_id=job.template_id,
                org_id=job.org_id,
                raw_ocr_json=result,
                raw_text=text,
                page_count=int(result.get("page_count", 0) or 0),
                extracted_fields=fields,
                validated_fields=validation["validated_fields"],
                mapped_output={k: v["value"] if isinstance(v, dict) else v
                               for k, v in validation["validated_fields"].items()},
                line_items=mapped_for_validation.get("item_rows", []) or [],
                overall_confidence=float(result.get("avg_confidence", 0.0) or 0.0),
                validation_errors=[{"error": e} for e in validation["errors"]]
                                 + [{"warning": w} for w in validation["warnings"]],
                fields_extracted=validation["field_count"],
                fields_high_confidence=sum(
                    1 for v in validation["validated_fields"].values()
                    if isinstance(v, dict) and float(v.get("confidence", 0) or 0) >= 0.8
                ),
                fields_needing_review=len(validation["warnings"]),
                status=extraction_status,
            )
            db.add(extracted)

            uploaded_file = await db.get(UploadedFile, job.file_id)
            if uploaded_file:
                uploaded_file.status = FileStatus.DONE.value
                uploaded_file.page_count = int(result.get("page_count", 0) or 0)

            job.status = JobStatus.COMPLETED.value
            job.started_at = job.started_at or completed_at
            job.completed_at = completed_at
            job.processing_ms = int(result.get("processing_ms", 0) or 0)
            job.page_count = int(result.get("page_count", 0) or 0)
            job.avg_confidence = float(result.get("avg_confidence", 0.0) or 0.0)
            job.total_words = len(text.split())

            await db.flush()
            extracted_data_id = str(extracted.id)
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
                    "extracted_data_id": extracted_data_id,
                    "page_count": job.page_count,
                    "avg_confidence": job.avg_confidence,
                    "fields_extracted": extracted.fields_extracted,
                },
            )
            await write_audit_log(
                db,
                action="validation.complete",
                event_type=LogEventType.EXTRACTION,
                status="success" if validation["valid"] else "failure",
                user_id=job.created_by_id,
                org_id=job.org_id,
                resource_type="extracted_data",
                resource_id=extracted.id,
                level=LogLevel.INFO if validation["valid"] else LogLevel.WARNING,
                message="Validation completed",
                metadata={
                    "field_count": validation["field_count"],
                    "error_count": len(validation["errors"]),
                    "warning_count": len(validation["warnings"]),
                    "errors": validation["errors"],
                },
            )
            if ai_audit_metadata:
                await write_audit_log(
                    db,
                    action="ai.fallback.used",
                    event_type=LogEventType.EXTRACTION,
                    status="success",
                    user_id=job.created_by_id,
                    org_id=job.org_id,
                    resource_type="extracted_data",
                    resource_id=extracted.id,
                    level=LogLevel.WARNING if ai_audit_metadata["needs_review"] else LogLevel.INFO,
                    message="AI fallback used",
                    metadata=ai_audit_metadata,
                )
            await _refresh_batch_counters(db, job.batch_id)
            await db.commit()

        OCR_DEMO_JOBS[job_id].update({
            "status": "completed",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "extracted_data_id": extracted_data_id,
            "ocr_engine": result.get("ocr_engine") or result.get("engine") or "unknown",
            "raw_text": text,
            "text": text,
            "extracted_text": text,
            "full_text": text,
            "raw_text_length": len(text),
            "raw_text_preview": text[:300],
            "detected_line_count": int(result.get("detected_line_count", 0) or 0),
            "weak_ocr": bool(result.get("weak_ocr", len(text.strip()) < 20)),
            "weak_ocr_message": result.get("weak_ocr_message", ""),
            "mapped_fields": mapped_result.get("mapped_fields", {}),
            "regex_fields": regex_fields,
            "gemini_fields": mapped_result.get("gemini_fields", {}),
            "weak_mapping": bool(mapped_result.get("weak_mapping", False)),
            "items": (
                (mapped_result.get("mapped_fields", {}).get("items") or mapped_result.get("mapped_fields", {}).get("item_rows") or {}).get("value", [])
                if isinstance((mapped_result.get("mapped_fields", {}).get("items") or mapped_result.get("mapped_fields", {}).get("item_rows") or {}), dict)
                else (mapped_result.get("mapped_fields", {}).get("items") or mapped_result.get("mapped_fields", {}).get("item_rows") or [])
            ),
            "missing_fields": mapped_result.get("missing_fields", []),
            "mapper_message": mapped_result.get("message", ""),
            "document_type": mapped_result.get("document_type") or cls_result["doc_type"].lower(),
            "sales_original_text_preview": mapped_result.get("sales_original_text_preview", ""),
            "sales_mapping_debug": mapped_result.get("sales_mapping_debug", {}),
            "missing_sales_fields": mapped_result.get("missing_sales_fields", []),
            "rejected_sales_values": mapped_result.get("rejected_sales_values", []),
            "field_confidence": mapped_result.get("field_confidence", {}),
            "success": True,
            "fields": ocr_response["fields"],
            "confidence": ocr_response["confidence"],
            "warnings": ocr_warnings,
            "validation_errors": validation["errors"],
            "validation_warnings": validation["warnings"],
            "ai_fallback": ai_audit_metadata,
            "detection_report": _build_detection_report(mapped_for_validation, validation),
            "extraction_status": extraction_status,
            "ocr_response": ocr_response,
            "ocr_result": result,
            "extracted_demo": extracted_demo,
            "document_type": mapped_result.get("document_type") or cls_result["doc_type"].lower(),
            "classifier": {"doc_type": cls_result["doc_type"], "score": cls_result["score"],
                           "rule": cls_result["matched_rule"]},
            # ── Phase 2: enriched response fields (backward-compatible additions) ──
            "detected_company":      detected_company_info,
            "company_id":            detected_company_info.get("company_id"),
            "quality":               {
                "page_count":  int(result.get("page_count", 0) or 0),
                "avg_confidence": float(result.get("avg_confidence", 0.0) or 0.0),
                "weak_ocr":    bool(result.get("weak_ocr", len(text.strip()) < 20)),
            },
            "p2_fields":             p2_field_response,
            "auto_filled_count":     p2_summary_counts.get("auto_filled_count", 0),
            "need_review_count":     p2_summary_counts.get("need_review_count", 0),
            "missing_count":         p2_summary_counts.get("missing_count", 0),
            "validation_error_count":p2_summary_counts.get("validation_error_count", 0),
            "export_allowed":        p2_summary_counts.get("export_allowed", True),
            "overall_confidence":    p2_summary_counts.get("overall_confidence", 0.0),
            "memory_applied_count":   memory_apply_result.get("memory_applied_count", 0),
            "memory_suggestion_count":memory_apply_result.get("memory_suggestion_count", 0),
            "memory_applied_fields":  memory_apply_result.get("memory_applied_fields", []),
            "memory_suggestion_fields": memory_apply_result.get("memory_suggestion_fields", []),
            "memory_suggestions":     memory_apply_result.get("memory_suggestions", {}),
            "memory_warnings":        memory_warnings,
            **final_ocr_decision,
        })
        if len(text.strip()) < 20:
            OCR_DEMO_JOBS[job_id].update({
                "weak_ocr": True,
                "weak_ocr_message": "OCR text is weak. Use clearer image or paste text manually.",
            })
        if result.get("is_mock_ocr") or "Mock OCR" in text:
            OCR_DEMO_JOBS[job_id].update({
                "warning": "Real OCR engine not installed. Install paddlepaddle+paddleocr.",
                "install_hint": "pip install paddlepaddle paddleocr",
                "extraction_status": ExtractionStatus.NEEDS_REVIEW.value,
                "is_mock_ocr": True,
            })
        if validation["errors"]:
            # Hard validation errors — surface to frontend
            OCR_DEMO_JOBS[job_id]["warning"] = " | ".join(validation["errors"])
        logger.info(
            "Background OCR complete: job_id={} engine={} pages={} conf={:.0%} text_len={} lines={} mapped_fields={} mapper_status={} pdf_text_ms={} ocr_ms={} mapper_ms={} total_job_ms={} cache_hit={}",
            job_id,
            result.get("ocr_engine") or result.get("engine") or "unknown",
            result.get("page_count", 0),
            float(result.get("avg_confidence", 0.0) or 0.0),
            len(text),
            int(result.get("detected_line_count", 0) or 0),
            len(mapped_result.get("mapped_fields", {}) or {}),
            mapped_result.get("message", ""),
            int(result.get("pdf_text_extraction_ms", 0) or 0),
            int(result.get("ocr_ms", result.get("processing_ms", 0)) or 0),
            mapper_ms,
            int((time.monotonic() - job_t0) * 1000),
            bool(result.get("cache_hit")),
        )
    except Exception as exc:
        async with AsyncSessionLocal() as db:
            try:
                job = await db.get(OCRJob, uuid.UUID(job_id))
                if job:
                    job.status = JobStatus.FAILED.value
                    job.completed_at = datetime.utcnow()
                    job.error_message = str(exc)
                    uploaded_file = await db.get(UploadedFile, job.file_id)
                    if uploaded_file:
                        uploaded_file.status = FileStatus.FAILED.value
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
                        metadata={"error": str(exc)},
                        error=str(exc),
                    )
                    await _refresh_batch_counters(db, job.batch_id)
                    await db.commit()
            except Exception as db_exc:
                await db.rollback()
                logger.error(f"Background OCR failure status update failed: job_id={job_id} error={db_exc}")
        error_text = str(exc)
        failed_response = {
            "success": True,
            "text": OCR_FAILURE_TEXT,
            "raw_text_length": len(OCR_FAILURE_TEXT),
            "detected_line_count": 1,
            "raw_text_preview": OCR_FAILURE_TEXT,
            "fields": {},
            "confidence": {},
            "warnings": [error_text],
        }
        OCR_DEMO_JOBS.setdefault(job_id, {}).update({
            "status": "failed",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "success": True,
            "text": OCR_FAILURE_TEXT,
            "raw_text": OCR_FAILURE_TEXT,
            "extracted_text": OCR_FAILURE_TEXT,
            "full_text": OCR_FAILURE_TEXT,
            "raw_text_length": len(OCR_FAILURE_TEXT),
            "detected_line_count": 1,
            "raw_text_preview": OCR_FAILURE_TEXT,
            "fields": {},
            "confidence": {},
            "warnings": [error_text],
            "ocr_response": failed_response,
            "error": error_text,
        })
        logger.exception(f"Background OCR failed: job_id={job_id} error={exc}")


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
        queued_at=datetime.utcnow(),
        started_at=datetime.utcnow(),
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


@router.post(
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
    tenant_limits_source = await _tenant_limit_source(db, current_user)
    check_batch_limits_for_user(tenant_limits_source, len(files), 0)

    prepared_files: list[dict[str, Any]] = []
    total_bytes = 0
    for upload in files:
        prepared = await _prepare_upload_file(upload)
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
        started_at=datetime.utcnow(),
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
        stored_info = await _store_prepared_upload(prepared)
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
            file_category=_file_category_for_mime(detected),
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
            queued_at=datetime.utcnow(),
            started_at=datetime.utcnow(),
            ocr_config={"demo": True, "mime_type": detected, "batch_id": str(batch.id)},
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
            "file_size": len(data),
            "checksum": checksum,
            "uploaded_at": uploaded_at,
            "upload_save_ms": upload_save_ms,
            "dest": dest,
        })

    await write_audit_log(
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
            "file_size": child["file_size"],
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
            "ocr_response": _empty_ocr_response(),
        }
        background_tasks.add_task(
            _ocr_background,
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


@router.get("/batches/{batch_id}", summary="Get OCR batch status")
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

    batch = await get_owned_ocr_batch(db, batch_uuid, current_user)
    if refresh:
        await _refresh_batch_counters(db, batch.id)
        await db.flush()
    files, jobs_by_file, extractions_by_job = await _batch_child_rows(db, batch)

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


@router.post("/batches/{batch_id}/export/zip", summary="Generate batch ZIP export")
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

    batch = await get_owned_ocr_batch(db, batch_uuid, current_user)
    manifest_files, exports, file_count = await _batch_zip_payload(db, batch)
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
        with zipfile.ZipFile(tmp_zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for item in exports:
                zf.writestr(item["zip_name"], json.dumps(item["payload"], ensure_ascii=True, indent=2))
            zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=True, indent=2))
        tmp_zip_path.replace(zip_path)
    except Exception:
        if tmp_zip_path.exists():
            tmp_zip_path.unlink(missing_ok=True)
        raise

    batch.output_zip_path = str(zip_path)
    await write_audit_log(
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


@router.get("/batches/{batch_id}/export/zip", summary="Download batch ZIP export")
async def download_batch_zip(
    batch_id: str,
    current_user: dict = Depends(require_roles(UserRole.OPERATOR, UserRole.REVIEWER)),
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    try:
        batch_uuid = uuid.UUID(batch_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Invalid batch_id format")

    batch = await get_owned_ocr_batch(db, batch_uuid, current_user)
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


def _get_memory_job_or_404(job_id: str, current_user: dict[str, Any]) -> dict[str, Any]:
    try:
        uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Invalid job_id format")

    job = OCR_DEMO_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Upload job not found")
    if not is_superadmin(current_user) and job.get("owner_user_id") != current_user["user_id"]:
        raise HTTPException(status_code=404, detail="Upload job not found")
    return job


def _require_canonical_fields(job: dict[str, Any]) -> dict[str, Any]:
    canonical_fields = job.get("canonical_fields")
    if not isinstance(canonical_fields, dict) or not canonical_fields:
        raise HTTPException(status_code=409, detail="No canonical_fields available for review")
    return canonical_fields


def _review_payload(job_id: str, job: dict[str, Any]) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "canonical_fields": job.get("canonical_fields", {}),
        "review_fields": job.get("review_fields", []),
        "blocked_fields": job.get("blocked_fields", []),
        "export_allowed": bool(job.get("export_allowed", False)),
        "export_block_reasons": job.get("export_block_reasons", []),
        "detected_company": job.get("detected_company", {}),
        "memory_suggestions": job.get("memory_suggestions", {}),
        "memory_applied_fields": job.get("memory_applied_fields", []),
        "memory_suggestion_fields": job.get("memory_suggestion_fields", []),
        "memory_applied_count": int(job.get("memory_applied_count", 0) or 0),
        "memory_suggestion_count": int(job.get("memory_suggestion_count", 0) or 0),
    }


def _apply_final_decision_to_job(job: dict[str, Any], decision: dict[str, Any]) -> None:
    job.update(decision)


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
    # Fast path — in-memory cache
    job = OCR_DEMO_JOBS.get(job_id)
    if job:
        if not is_superadmin(current_user) and job.get("owner_user_id") != current_user["user_id"]:
            raise HTTPException(status_code=404, detail="Upload job not found")
        return job

    # Task 1: DB fallback — server restart / memory eviction
    try:
        job_uuid = uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Invalid job_id format")

    db_job = await get_owned_ocr_job(db, job_uuid, current_user)

    # Reconstruct minimal response from DB
    extracted: ExtractedData | None = None
    if db_job.status == JobStatus.COMPLETED.value:
        result = await db.execute(
            select(ExtractedData).where(ExtractedData.job_id == job_uuid)
        )
        extracted = result.scalar_one_or_none()

    text = (extracted.raw_text or "") if extracted else ""
    mapped_fields = {}
    if extracted and extracted.validated_fields:
        # validated_fields shape: {field: {value, confidence}} or {field: raw_value}
        for k, v in extracted.validated_fields.items():
            if isinstance(v, dict):
                mapped_fields[k] = v
            else:
                mapped_fields[k] = {"value": str(v), "confidence": 0.5}

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
        "missing_fields": [],
        "fields": {},
        "confidence": {},
        "warnings": ["Job state restored from database (server was restarted)"],
        "extraction_status": extracted.status if extracted else None,
        "validation_errors": [e.get("error", "") for e in (extracted.validation_errors or [])
                              if "error" in e] if extracted else [],
        "validation_warnings": [e.get("warning", "") for e in (extracted.validation_errors or [])
                                if "warning" in e] if extracted else [],
        "document_type": "general",
        "ocr_response": {"text": text, "fields": {}, "confidence": {}, "warnings": []},
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
