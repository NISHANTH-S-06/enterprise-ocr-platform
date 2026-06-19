"""
upload_helpers.py — Helper constants, schemas, cache, and validation functions.
Extracted from upload.py for maintainability.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import aiofiles
from fastapi import HTTPException
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.batch import OCRBatch
from app.models.enums import BatchStatus, ExtractionStatus, FileCategory, FileStatus, JobStatus, UserRole
from app.models.extracted_data import ExtractedData
from app.models.file import UploadedFile
from app.models.job import OCRJob
from app.models.user import Organisation
from app.services.ownership_service import get_owned_ocr_batch, get_owned_ocr_job, is_superadmin
from app.services.tenant_limits import (
    DEFAULT_MAX_BATCH_FILES,
    DEFAULT_MAX_BATCH_TOTAL_MB,
    is_ai_fallback_enabled_for_user,
)

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
MAX_UPLOAD_PDF_PAGES = settings.MAX_PDF_PAGES
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


async def _prepare_upload_file(file: Any) -> dict[str, Any]:
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
        batch.completed_at = batch.completed_at or datetime.now(timezone.utc)
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
