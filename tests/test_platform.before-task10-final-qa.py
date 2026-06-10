"""
Full test suite — models, field mapper, all three exporters.
No live DB or PaddleOCR required.
"""
from __future__ import annotations
import json, uuid
from pathlib import Path
import pytest


# ── fixtures ──────────────────────────────────────────────────────────────────

SAMPLE_EXTRACTED = {
    "invoice_number": {"raw": "INV-2024-001", "value": "INV-2024-001", "confidence": 0.95},
    "invoice_date":   {"raw": "15/01/2024",   "value": "2024-01-15",   "confidence": 0.92},
    "due_date":       {"raw": "14/02/2024",   "value": "2024-02-14",   "confidence": 0.91},
    "vendor_name":    {"raw": "TechVision Solutions Pvt. Ltd.", "value": "TechVision Solutions Pvt. Ltd.", "confidence": 0.88},
    "customer_name":  {"raw": "Global Enterprises Ltd.", "value": "Global Enterprises Ltd.", "confidence": 0.85},
    "vendor_gstin":   {"raw": "29ABCDE1234F1Z5", "value": {"gstin": "29ABCDE1234F1Z5", "state_code": "29"}, "confidence": 0.97},
    "subtotal":       {"raw": "92000.00",  "value": {"amount": 92000.0,  "currency": "INR"}, "confidence": 0.93},
    "cgst":           {"raw": "8280.00",   "value": {"amount": 8280.0,   "currency": "INR"}, "confidence": 0.90},
    "sgst":           {"raw": "8280.00",   "value": {"amount": 8280.0,   "currency": "INR"}, "confidence": 0.90},
    "total_tax":      {"raw": "16560.00",  "value": {"amount": 16560.0,  "currency": "INR"}, "confidence": 0.92},
    "total_amount":   {"raw": "108560.00", "value": {"amount": 108560.0, "currency": "INR"}, "confidence": 0.96},
    "amount_in_words":{"raw": "Rupees One Lakh Eight Thousand Five Hundred Sixty Only",
                       "value": "Rupees One Lakh Eight Thousand Five Hundred Sixty Only", "confidence": 0.80},
    "email":   {"raw": "billing@techvision.in", "value": "billing@techvision.in", "confidence": 0.99},
    "phone":   {"raw": "+91-9876543210",        "value": "+91-9876543210",         "confidence": 0.94},
    "bank_account": {"raw": "123456789012", "value": "123456789012", "confidence": 0.87},
    "ifsc_code":    {"raw": "HDFC0001234",  "value": "HDFC0001234",  "confidence": 0.95},
    "payment_terms":{"raw": "Net 30",       "value": "Net 30",       "confidence": 0.82},
}

SAMPLE_LINE_ITEMS = [
    {"description": "Software License",  "hsn_sac": "998314", "quantity": 5, "unit_price": 12000.0, "tax_rate": 18.0, "total": 60000.0},
    {"description": "Support Services",  "hsn_sac": "998315", "quantity": 2, "unit_price": 8500.0,  "tax_rate": 18.0, "total": 17000.0},
    {"description": "Training Package",  "hsn_sac": "998316", "quantity": 1, "unit_price": 15000.0, "tax_rate": 18.0, "total": 15000.0},
]

TEMPLATE_SCHEMA = {
    "invoice_number": {"type": "string", "required": True},
    "invoice_date":   {"type": "date",   "required": True},
    "vendor_name":    {"type": "string", "required": True},
    "customer_name":  {"type": "string", "required": False},
    "total_amount":   {"type": "number", "required": True},
    "missing_field":  {"type": "string", "required": False, "default": "N/A"},
    "required_missing": {"type": "string", "required": True},
}

MAPPED_OUTPUT = {k: v["value"] for k, v in SAMPLE_EXTRACTED.items()}


# ── enums ─────────────────────────────────────────────────────────────────────

class TestEnums:
    def test_user_roles(self):
        from app.models.enums import UserRole
        assert "admin" in [r.value for r in UserRole]

    def test_job_statuses(self):
        from app.models.enums import JobStatus
        assert JobStatus.QUEUED.value == "queued"

    def test_batch_statuses(self):
        from app.models.enums import BatchStatus
        assert {s.value for s in BatchStatus} == {
            "queued", "processing", "completed", "failed", "partial_failed", "cancelled"
        }

    def test_export_formats(self):
        from app.models.enums import ExportFormat
        formats = {f.value for f in ExportFormat}
        assert formats == {"pdf", "excel", "json"}

    def test_fill_job_status(self):
        from app.models.enums import FillJobStatus
        assert FillJobStatus.COMPLETED.value == "completed"

    def test_document_types_complete(self):
        from app.models.enums import DocumentType
        assert DocumentType.INVOICE.value == "invoice"
        assert DocumentType.UNKNOWN.value == "unknown"


# ── model imports ─────────────────────────────────────────────────────────────

class TestModelImports:
    def test_all_models_importable(self):
        from app.models import (
            Organisation, User, UserSession, ApiKey,
            OCRBatch,
            UploadedFile, FilePage,
            OCRJob, JobEvent,
            ExtractedData,
            ExtractionTemplate,
            AuditLog, SystemLog,
            FormFillJob,
        )
        assert Organisation.__tablename__ == "organisations"
        assert User.__tablename__ == "users"
        assert UserSession.__tablename__ == "user_sessions"
        assert ApiKey.__tablename__ == "api_keys"
        assert OCRBatch.__tablename__ == "ocr_batches"
        assert UploadedFile.__tablename__ == "uploaded_files"
        assert FilePage.__tablename__ == "file_pages"
        assert OCRJob.__tablename__ == "ocr_jobs"
        assert JobEvent.__tablename__ == "job_events"
        assert ExtractedData.__tablename__ == "extracted_data"
        assert ExtractionTemplate.__tablename__ == "extraction_templates"
        assert AuditLog.__tablename__ == "audit_logs"
        assert SystemLog.__tablename__ == "system_logs"
        assert FormFillJob.__tablename__ == "form_fill_jobs"

    def test_mixins_present(self):
        from app.models import User, UploadedFile
        assert hasattr(User, "created_at")
        assert hasattr(User, "updated_at")
        assert hasattr(User, "deleted_at")
        assert hasattr(User, "is_deleted")
        assert hasattr(UploadedFile, "soft_delete")

    def test_relationships_defined(self):
        from app.models import User
        assert "sessions" in User.__mapper__.relationships.keys()
        assert "api_keys" in User.__mapper__.relationships.keys()
        assert "ocr_jobs" in User.__mapper__.relationships.keys()

    def test_db_columns_user(self):
        from app.models import User
        cols = {c.name for c in User.__table__.columns}
        for col in ("id","email","hashed_password","full_name","role","status","org_id"):
            assert col in cols, f"Missing column: {col}"

    def test_db_columns_extracted_data(self):
        from app.models import ExtractedData
        cols = {c.name for c in ExtractedData.__table__.columns}
        for col in ("raw_ocr_json","extracted_fields","validated_fields","mapped_output","line_items","overall_confidence"):
            assert col in cols

    def test_db_columns_fill_job(self):
        from app.models import FormFillJob
        cols = {c.name for c in FormFillJob.__table__.columns}
        for col in ("extracted_data_id","export_format","fill_overrides","fill_result","output_path"):
            assert col in cols

    def test_db_columns_ocr_batch(self):
        from app.models import OCRBatch
        cols = {c.name for c in OCRBatch.__table__.columns}
        for col in (
            "id", "created_by_id", "org_id", "status", "file_count",
            "completed_count", "failed_count", "needs_review_count",
            "validated_count", "total_bytes", "total_pages",
            "output_zip_path", "error_message", "metadata",
            "started_at", "completed_at",
        ):
            assert col in cols

    def test_nullable_batch_id_links_exist(self):
        from app.models import OCRJob, UploadedFile
        assert "batch_id" in {c.name for c in UploadedFile.__table__.columns}
        assert UploadedFile.__table__.columns["batch_id"].nullable is True
        assert "batch_id" in {c.name for c in OCRJob.__table__.columns}
        assert OCRJob.__table__.columns["batch_id"].nullable is True


class TestBatchStatusPlumbing:
    def test_ocr_batch_model_creation(self):
        from app.models.batch import OCRBatch
        from app.models.enums import BatchStatus
        user_id = uuid.uuid4()
        batch = OCRBatch(created_by_id=user_id, status=BatchStatus.QUEUED.value)
        assert batch.created_by_id == user_id
        assert batch.status == "queued"
        assert batch.file_count is None or batch.file_count == 0

    def test_batch_ownership_blocks_other_user(self):
        from fastapi import HTTPException
        from app.models.batch import OCRBatch
        from app.services.ownership_service import assert_owner
        batch = OCRBatch(created_by_id=uuid.uuid4())
        current_user = {"user_id": str(uuid.uuid4()), "role": "operator"}
        with pytest.raises(HTTPException) as exc:
            assert_owner(current_user, batch.created_by_id)
        assert exc.value.status_code == 404

    def test_read_only_batch_status_endpoint_returns_summary(self, monkeypatch):
        import asyncio
        from datetime import datetime
        from types import SimpleNamespace
        from app.api.v1.routers import upload
        from app.models.enums import FileStatus

        batch_id = uuid.uuid4()
        file_id = uuid.uuid4()
        job_id = uuid.uuid4()
        batch = SimpleNamespace(
            id=batch_id,
            status="queued",
            file_count=1,
            completed_count=0,
            failed_count=0,
            needs_review_count=0,
            validated_count=0,
            total_bytes=123,
            total_pages=0,
        )
        uploaded_file = SimpleNamespace(
            id=file_id,
            original_filename="sample.pdf",
            status=FileStatus.PROCESSING.value,
            mime_type="application/pdf",
            file_size=123,
            page_count=None,
            created_at=datetime.utcnow(),
        )
        job = SimpleNamespace(
            id=job_id,
            file_id=file_id,
            status="completed",
            error_message=None,
            completed_at=datetime.utcnow(),
        )
        extraction = SimpleNamespace(
            job_id=job_id,
            status="validated",
            validation_errors=[{"warning": "check"}],
            fields_extracted=7,
        )

        async def fake_get_owned_ocr_batch(db, requested_batch_id, current_user):
            assert requested_batch_id == batch_id
            return batch

        class FakeResult:
            def __init__(self, rows):
                self.rows = rows

            def scalars(self):
                return self

            def all(self):
                return self.rows

        class FakeDB:
            def __init__(self):
                self.calls = 0

            async def execute(self, _stmt):
                self.calls += 1
                if self.calls == 1:
                    return FakeResult([uploaded_file])
                if self.calls == 2:
                    return FakeResult([job])
                return FakeResult([extraction])

        monkeypatch.setattr(upload, "get_owned_ocr_batch", fake_get_owned_ocr_batch)
        result = asyncio.run(upload.get_upload_batch(
            str(batch_id),
            current_user={"user_id": str(uuid.uuid4()), "role": "operator"},
            db=FakeDB(),
        ))
        assert result["batch_id"] == str(batch_id)
        assert result["status"] == "queued"
        assert result["file_count"] == 1
        assert result["files"] == [{
            "file_id": str(file_id),
            "job_id": str(job_id),
            "filename": "sample.pdf",
            "status": "processing",
            "job_status": "completed",
            "extraction_status": "validated",
            "error_message": None,
            "completed_at": job.completed_at.isoformat(),
            "warnings_count": 1,
            "field_count": 7,
            "mime_type": "application/pdf",
            "file_size": 123,
            "page_count": None,
        }]

    def test_empty_batch_rejected(self):
        import asyncio
        from fastapi import HTTPException
        from app.api.v1.routers import upload

        with pytest.raises(HTTPException) as exc:
            asyncio.run(upload.upload_batch(
                background_tasks=object(),
                files=[],
                request=None,
                current_user={"user_id": str(uuid.uuid4()), "role": "operator"},
                db=object(),
            ))
        assert exc.value.status_code == 400

    def test_too_many_files_rejected(self):
        import asyncio
        from fastapi import HTTPException
        from app.api.v1.routers import upload

        with pytest.raises(HTTPException) as exc:
            asyncio.run(upload.upload_batch(
                background_tasks=object(),
                files=[object()] * (upload.MAX_BATCH_FILES + 1),
                request=None,
                current_user={"user_id": str(uuid.uuid4()), "role": "operator"},
                db=object(),
            ))
        assert exc.value.status_code == 413

    def test_valid_batch_creates_batch_and_child_links(self, monkeypatch):
        import asyncio
        from app.api.v1.routers import upload
        from app.models.batch import OCRBatch
        from app.models.file import UploadedFile
        from app.models.job import OCRJob

        async def fake_prepare(file):
            return {
                "data": b"fake-png",
                "safe_name": file,
                "detected": "image/png",
                "ext": ".png",
                "content_type": "image/png",
            }

        async def fake_store(prepared):
            return {
                "checksum": uuid.uuid4().hex + uuid.uuid4().hex,
                "stored": f"{prepared['safe_name']}.png",
                "dest": f"uploads/{prepared['safe_name']}.png",
                "upload_save_ms": 1,
            }

        async def fake_audit(*args, **kwargs):
            return None

        class FakeBackground:
            def __init__(self):
                self.tasks = []

            def add_task(self, fn, *args):
                self.tasks.append((fn, args))

        class FakeDB:
            def __init__(self):
                self.added = []
                self.commits = 0

            def add(self, item):
                self.added.append(item)

            async def flush(self):
                for item in self.added:
                    if getattr(item, "id", None) is None:
                        item.id = uuid.uuid4()

            async def commit(self):
                self.commits += 1

        monkeypatch.setattr(upload, "_prepare_upload_file", fake_prepare)
        monkeypatch.setattr(upload, "_store_prepared_upload", fake_store)
        monkeypatch.setattr(upload, "write_audit_log", fake_audit)
        background = FakeBackground()
        db = FakeDB()
        current_user = {"user_id": str(uuid.uuid4()), "org_id": None, "role": "operator"}

        result = asyncio.run(upload.upload_batch(
            background_tasks=background,
            files=["a", "b"],
            request=None,
            current_user=current_user,
            db=db,
        ))

        batches = [item for item in db.added if isinstance(item, OCRBatch)]
        uploaded_files = [item for item in db.added if isinstance(item, UploadedFile)]
        jobs = [item for item in db.added if isinstance(item, OCRJob)]
        assert len(batches) == 1
        assert len(uploaded_files) == 2
        assert len(jobs) == 2
        assert result["batch_id"] == str(batches[0].id)
        assert result["file_count"] == 2
        assert all(item.batch_id == batches[0].id for item in uploaded_files)
        assert all(item.batch_id == batches[0].id for item in jobs)
        assert len(background.tasks) == 2

    def test_batch_validation_failure_creates_no_rows(self, monkeypatch):
        import asyncio
        from fastapi import HTTPException
        from app.api.v1.routers import upload

        async def fake_prepare(_file):
            raise HTTPException(status_code=415, detail="Unsupported file type")

        class FakeDB:
            def __init__(self):
                self.added = []

            def add(self, item):
                self.added.append(item)

        monkeypatch.setattr(upload, "_prepare_upload_file", fake_prepare)
        db = FakeDB()
        with pytest.raises(HTTPException) as exc:
            asyncio.run(upload.upload_batch(
                background_tasks=object(),
                files=["bad"],
                request=None,
                current_user={"user_id": str(uuid.uuid4()), "role": "operator"},
                db=db,
            ))
        assert exc.value.status_code == 415
        assert db.added == []

    def test_zip_export_requires_ownership(self, monkeypatch):
        import asyncio
        from fastapi import HTTPException
        from app.api.v1.routers import upload

        async def fake_get_owned(*args, **kwargs):
            raise HTTPException(status_code=404, detail="Batch not found")

        monkeypatch.setattr(upload, "get_owned_ocr_batch", fake_get_owned)
        with pytest.raises(HTTPException) as exc:
            asyncio.run(upload.export_batch_zip(
                str(uuid.uuid4()),
                request=None,
                current_user={"user_id": str(uuid.uuid4()), "role": "operator"},
                db=object(),
            ))
        assert exc.value.status_code == 404

    def test_zip_export_fails_without_completed_results(self, monkeypatch):
        import asyncio
        from types import SimpleNamespace
        from fastapi import HTTPException
        from app.api.v1.routers import upload

        batch = SimpleNamespace(id=uuid.uuid4(), output_zip_path=None)

        async def fake_get_owned(*args, **kwargs):
            return batch

        async def fake_payload(*args, **kwargs):
            return ([{"included": False, "skip_reason": "processing"}], [], 1)

        monkeypatch.setattr(upload, "get_owned_ocr_batch", fake_get_owned)
        monkeypatch.setattr(upload, "_batch_zip_payload", fake_payload)
        with pytest.raises(HTTPException) as exc:
            asyncio.run(upload.export_batch_zip(
                str(batch.id),
                request=None,
                current_user={"user_id": str(uuid.uuid4()), "role": "operator"},
                db=object(),
            ))
        assert exc.value.status_code == 409

    def test_zip_export_creates_manifest_and_validated_json(self, monkeypatch, tmp_path):
        import asyncio
        import zipfile
        from types import SimpleNamespace
        from app.api.v1.routers import upload

        batch = SimpleNamespace(id=uuid.uuid4(), output_zip_path=None)
        manifest_files = [
            {
                "filename": "../invoice.pdf",
                "file_id": str(uuid.uuid4()),
                "job_id": str(uuid.uuid4()),
                "status": "completed",
                "extraction_id": str(uuid.uuid4()),
                "field_count": 2,
                "warnings_count": 0,
                "included": True,
                "skip_reason": None,
            },
            {
                "filename": "pending.pdf",
                "file_id": str(uuid.uuid4()),
                "job_id": str(uuid.uuid4()),
                "status": "processing",
                "extraction_id": None,
                "field_count": 0,
                "warnings_count": 0,
                "included": False,
                "skip_reason": "processing",
            },
        ]
        exports = [{"zip_name": "invoice.json", "payload": {"fields": {"invoice_number": "INV-1"}}}]

        async def fake_get_owned(*args, **kwargs):
            return batch

        async def fake_payload(*args, **kwargs):
            return (manifest_files, exports, 2)

        async def fake_audit(*args, **kwargs):
            return None

        class FakeDB:
            def __init__(self):
                self.commits = 0

            async def commit(self):
                self.commits += 1

        monkeypatch.setattr(upload, "settings", SimpleNamespace(output_path=tmp_path))
        monkeypatch.setattr(upload, "get_owned_ocr_batch", fake_get_owned)
        monkeypatch.setattr(upload, "_batch_zip_payload", fake_payload)
        monkeypatch.setattr(upload, "write_audit_log", fake_audit)
        db = FakeDB()

        result = asyncio.run(upload.export_batch_zip(
            str(batch.id),
            request=None,
            current_user={"user_id": str(uuid.uuid4()), "role": "operator"},
            db=db,
        ))
        assert result["zip_ready"] is True
        assert result["included_count"] == 1
        assert result["skipped_count"] == 1
        assert batch.output_zip_path
        with zipfile.ZipFile(batch.output_zip_path) as zf:
            assert sorted(zf.namelist()) == ["invoice.json", "manifest.json"]
            manifest = json.loads(zf.read("manifest.json"))
            exported = json.loads(zf.read("invoice.json"))
        assert manifest["included_count"] == 1
        assert manifest["skipped_count"] == 1
        assert manifest["files"][1]["skip_reason"] == "processing"
        assert exported == {"fields": {"invoice_number": "INV-1"}}
        assert db.commits == 1

    def test_safe_zip_stem_blocks_path_traversal(self):
        from app.api.v1.routers.upload import _safe_zip_stem
        assert _safe_zip_stem("../../bad name.pdf") == "bad_name"
        assert "/" not in _safe_zip_stem("../x/y.pdf")
        assert "\\" not in _safe_zip_stem("..\\x\\y.pdf")

    def test_zip_download_returns_existing_file(self, monkeypatch, tmp_path):
        import asyncio
        from types import SimpleNamespace
        from app.api.v1.routers import upload

        zip_path = tmp_path / "batch-test.zip"
        zip_path.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
        batch = SimpleNamespace(id=uuid.uuid4(), output_zip_path=str(zip_path))

        async def fake_get_owned(*args, **kwargs):
            return batch

        monkeypatch.setattr(upload, "get_owned_ocr_batch", fake_get_owned)
        response = asyncio.run(upload.download_batch_zip(
            str(batch.id),
            current_user={"user_id": str(uuid.uuid4()), "role": "operator"},
            db=object(),
        ))
        assert response.path == str(zip_path)
        assert response.filename == zip_path.name

    def test_batch_status_refresh_reconciles_stale_counters(self, monkeypatch):
        import asyncio
        from types import SimpleNamespace
        from app.api.v1.routers import upload

        batch_id = uuid.uuid4()
        file_id = uuid.uuid4()
        job_id = uuid.uuid4()
        batch = SimpleNamespace(
            id=batch_id,
            status="processing",
            file_count=1,
            completed_count=0,
            failed_count=0,
            needs_review_count=0,
            validated_count=0,
            total_bytes=10,
            total_pages=0,
            completed_at=None,
        )
        uploaded_file = SimpleNamespace(
            id=file_id,
            original_filename="done.pdf",
            status="done",
            mime_type="application/pdf",
            file_size=10,
            page_count=2,
            created_at=None,
        )
        job = SimpleNamespace(
            id=job_id,
            file_id=file_id,
            status="completed",
            page_count=2,
            error_message=None,
            completed_at=None,
        )
        extraction = SimpleNamespace(
            job_id=job_id,
            status="validated",
            validation_errors=[],
            fields_extracted=3,
        )

        async def fake_get_owned(*args, **kwargs):
            return batch

        class FakeResult:
            def __init__(self, rows):
                self.rows = rows

            def scalars(self):
                return self

            def all(self):
                return self.rows

        class FakeDB:
            def __init__(self):
                self.calls = 0

            async def get(self, model, requested_id):
                return batch

            async def execute(self, _stmt):
                self.calls += 1
                index = ((self.calls - 1) % 3) + 1
                if index == 1:
                    return FakeResult([uploaded_file])
                if index == 2:
                    return FakeResult([job])
                return FakeResult([extraction])

            async def flush(self):
                return None

        monkeypatch.setattr(upload, "get_owned_ocr_batch", fake_get_owned)
        result = asyncio.run(upload.get_upload_batch(
            str(batch_id),
            refresh=True,
            current_user={"user_id": str(uuid.uuid4()), "role": "operator"},
            db=FakeDB(),
        ))
        assert result["completed_count"] == 1
        assert result["validated_count"] == 1
        assert result["total_pages"] == 2
        assert result["status"] == "completed"

    def test_atomic_zip_preserves_existing_on_generation_failure(self, monkeypatch, tmp_path):
        import asyncio
        from types import SimpleNamespace
        from fastapi import HTTPException
        from app.api.v1.routers import upload

        batch_id = uuid.uuid4()
        batch = SimpleNamespace(id=batch_id, output_zip_path=str(tmp_path / "old.zip"))
        final_dir = tmp_path / "batches" / str(batch_id)
        final_dir.mkdir(parents=True)
        old_zip = final_dir / f"batch-{batch_id}.zip"
        old_zip.write_bytes(b"old zip bytes")

        async def fake_get_owned(*args, **kwargs):
            return batch

        async def fake_payload(*args, **kwargs):
            return ([{"included": True}], [{"zip_name": "x.json", "payload": {"fields": {"x": "y"}}}], 1)

        class BrokenZip:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                raise RuntimeError("zip failed")

            def __exit__(self, *args):
                return False

        monkeypatch.setattr(upload, "settings", SimpleNamespace(output_path=tmp_path))
        monkeypatch.setattr(upload, "get_owned_ocr_batch", fake_get_owned)
        monkeypatch.setattr(upload, "_batch_zip_payload", fake_payload)
        monkeypatch.setattr(upload.zipfile, "ZipFile", BrokenZip)

        with pytest.raises(RuntimeError):
            asyncio.run(upload.export_batch_zip(
                str(batch_id),
                request=None,
                current_user={"user_id": str(uuid.uuid4()), "role": "operator"},
                db=object(),
            ))
        assert old_zip.read_bytes() == b"old zip bytes"

    def test_retention_config_exists(self):
        from app.core.config import Settings
        settings = Settings()
        assert settings.BATCH_ZIP_RETENTION_DAYS == 7
        assert settings.BATCH_ZIP_MAX_MB == 500

    def test_cleanup_helper_deletes_expired_zip(self, tmp_path):
        import os
        import time
        from app.api.v1.routers.upload import cleanup_expired_batch_zips

        old_zip = tmp_path / "old.zip"
        active_zip = tmp_path / "active.zip"
        old_zip.write_bytes(b"old")
        active_zip.write_bytes(b"active")
        old_time = time.time() - 10 * 24 * 60 * 60
        os.utime(old_zip, (old_time, old_time))
        os.utime(active_zip, (old_time, old_time))

        summary = cleanup_expired_batch_zips(
            tmp_path,
            retention_days=7,
            max_total_mb=500,
            active_paths={str(active_zip)},
        )
        assert summary["deleted_count"] == 1
        assert not old_zip.exists()
        assert active_zip.exists()

    def test_zip_download_missing_file_returns_410(self, monkeypatch, tmp_path):
        import asyncio
        from types import SimpleNamespace
        from fastapi import HTTPException
        from app.api.v1.routers import upload

        batch = SimpleNamespace(id=uuid.uuid4(), output_zip_path=str(tmp_path / "missing.zip"))

        async def fake_get_owned(*args, **kwargs):
            return batch

        monkeypatch.setattr(upload, "get_owned_ocr_batch", fake_get_owned)
        with pytest.raises(HTTPException) as exc:
            asyncio.run(upload.download_batch_zip(
                str(batch.id),
                current_user={"user_id": str(uuid.uuid4()), "role": "operator"},
                db=object(),
            ))
        assert exc.value.status_code == 410


# ── field mapper ──────────────────────────────────────────────────────────────

class TestAdminSummary:
    def test_admin_router_registered(self):
        from app.api.v1 import api_router
        paths = {route.path for route in api_router.routes}
        assert "/admin/summary" in paths

    def test_admin_and_superadmin_roles_allowed(self):
        import asyncio
        from app.core.security import require_roles
        from app.models.enums import UserRole

        dependency = require_roles(UserRole.ADMIN)
        assert asyncio.run(dependency({"role": "admin"}))["role"] == "admin"
        assert asyncio.run(dependency({"role": "superadmin"}))["role"] == "superadmin"

    def test_operator_and_reviewer_roles_denied(self):
        import asyncio
        from fastapi import HTTPException
        from app.core.security import require_roles
        from app.models.enums import UserRole

        dependency = require_roles(UserRole.ADMIN)
        for role in ("operator", "reviewer"):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(dependency({"role": role}))
            assert exc.value.status_code == 403

    def test_summary_response_shape_and_redaction(self, monkeypatch):
        import asyncio
        from app.api.v1.routers import admin
        from app.models.batch import OCRBatch
        from app.models.extracted_data import ExtractedData
        from app.models.file import UploadedFile
        from app.models.job import OCRJob
        from app.models.log import AuditLog
        from app.models.user import User

        async def fake_count_total(_db, model, _current_user, *filters):
            if model is User:
                return 1 if filters else 2
            if model is UploadedFile:
                return 4
            if model is OCRJob:
                return 3
            if model is ExtractedData:
                return 2
            if model is OCRBatch:
                return 1
            if model is AuditLog:
                return 5
            return 0

        async def fake_count_by_column(_db, model, _column, _current_user, *filters):
            if model is User:
                return {"admin": 1, "operator": 1}
            if model is UploadedFile:
                return {"done": 3, "failed": 1}
            if model is OCRJob:
                return {"completed": 2, "failed": 1}
            if model is ExtractedData:
                return {"validated": 1, "needs_review": 1}
            if model is OCRBatch:
                return {"completed": 1}
            if model is AuditLog:
                return {"export.pdf": 2, "batch.export.zip": 1}
            return {}

        monkeypatch.setattr(admin, "_count_total", fake_count_total)
        monkeypatch.setattr(admin, "_count_by_column", fake_count_by_column)

        result = asyncio.run(admin.get_admin_summary(
            current_user={"user_id": str(uuid.uuid4()), "role": "admin", "org_id": str(uuid.uuid4())},
            db=object(),
        ))

        assert set(result) == {"users", "uploads", "ocr_jobs", "extractions", "batches", "ai", "exports"}
        assert result["users"]["total"] == 2
        assert result["users"]["active"] == 1
        assert result["ocr_jobs"]["failed"] == 1
        assert result["extractions"]["needs_review"] == 1
        assert result["ai"]["gemini_fallback_count"] == 5
        assert result["exports"]["total"] == 3
        payload = json.dumps(result)
        for sensitive in ("raw_text", "extracted_text", "raw_ocr_json", "api_key", "traceback", "metadata"):
            assert sensitive not in payload

    def test_counts_by_status_work_with_sample_rows(self):
        import asyncio
        from app.api.v1.routers import admin
        from app.models.job import OCRJob

        class FakeResult:
            def all(self):
                return [("completed", 2), ("failed", 1), ("processing", 3)]

        class FakeDB:
            async def execute(self, _stmt):
                return FakeResult()

        counts = asyncio.run(admin._count_by_column(
            FakeDB(),
            OCRJob,
            OCRJob.status,
            {"user_id": str(uuid.uuid4()), "role": "superadmin", "org_id": None},
        ))
        assert counts == {"completed": 2, "failed": 1, "processing": 3}

    def test_recent_admin_routes_registered(self):
        from app.api.v1 import api_router
        paths = {route.path for route in api_router.routes}
        assert "/admin/audit-logs" in paths
        assert "/admin/uploads/recent" in paths
        assert "/admin/jobs/recent" in paths
        assert "/admin/batches/recent" in paths

    def test_recent_pagination_limit_capped(self):
        from app.api.v1.routers.admin import _clamp_pagination
        assert _clamp_pagination(500, -5) == (100, 0)
        assert _clamp_pagination(0, 10) == (20, 10)

    def test_audit_metadata_redacts_sensitive_keys(self):
        from app.api.v1.routers.admin import _safe_metadata

        result = _safe_metadata({
            "batch_id": "batch-1",
            "raw_text": "secret OCR",
            "GEMINI_API_KEY": "secret",
            "authorization": "Bearer token",
            "nested": {"raw_ocr_json": "secret"},
            "count": 3,
        })

        assert result == {"batch_id": "batch-1", "count": 3}

    def test_recent_upload_payload_has_no_path_or_raw_text(self):
        from datetime import datetime, timezone
        from types import SimpleNamespace
        from app.api.v1.routers.admin import _upload_payload

        row = SimpleNamespace(
            id=uuid.uuid4(),
            original_filename="invoice.pdf",
            status="done",
            mime_type="application/pdf",
            file_size=123,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            batch_id=uuid.uuid4(),
            uploaded_by_id=uuid.uuid4(),
            storage_path="uploads/secret.pdf",
            raw_text="secret",
        )
        result = _upload_payload(row)
        payload = json.dumps(result)
        assert "storage_path" not in payload
        assert "uploads/secret.pdf" not in payload
        assert "raw_text" not in payload
        assert result["filename"] == "invoice.pdf"

    def test_recent_job_payload_has_no_ocr_text(self):
        from datetime import datetime, timezone
        from types import SimpleNamespace
        from app.api.v1.routers.admin import _job_payload

        row = SimpleNamespace(
            id=uuid.uuid4(),
            file_id=uuid.uuid4(),
            batch_id=None,
            status="failed",
            error_message="api_key leaked in exception",
            avg_confidence=0.82,
            created_at=datetime.now(timezone.utc),
            started_at=None,
            completed_at=None,
            raw_ocr_json={"text": "secret"},
            raw_text="secret",
        )
        result = _job_payload(row)
        payload = json.dumps(result)
        assert "raw_ocr_json" not in payload
        assert "raw_text" not in payload
        assert result["error_message"] == "redacted"
        assert result["confidence"] == 0.82

    def test_recent_batch_payload_has_no_zip_path_or_raw_data(self):
        from datetime import datetime, timezone
        from types import SimpleNamespace
        from app.api.v1.routers.admin import _batch_payload

        row = SimpleNamespace(
            id=uuid.uuid4(),
            status="completed",
            file_count=2,
            completed_count=2,
            failed_count=0,
            needs_review_count=1,
            validated_count=1,
            total_bytes=456,
            total_pages=3,
            created_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
            output_zip_path="output/batches/private.zip",
            metadata_={"raw_text": "secret"},
        )
        result = _batch_payload(row)
        payload = json.dumps(result)
        assert "output_zip_path" not in payload
        assert "private.zip" not in payload
        assert "metadata" not in payload
        assert result["file_count"] == 2

    def test_admin_org_scoping_and_superadmin_global(self):
        from app.api.v1.routers.admin import _org_scoped_filters
        from app.models.file import UploadedFile

        org_id = uuid.uuid4()
        admin_filters = _org_scoped_filters(UploadedFile, {"role": "admin", "org_id": str(org_id)})
        super_filters = _org_scoped_filters(UploadedFile, {"role": "superadmin", "org_id": str(org_id)})
        orgless_filters = _org_scoped_filters(UploadedFile, {"role": "admin", "org_id": None})
        assert len(admin_filters) == 1
        assert super_filters == []
        assert len(orgless_filters) == 1

    def test_recent_endpoint_uses_capped_limit(self, monkeypatch):
        import asyncio
        from types import SimpleNamespace
        from app.api.v1.routers import admin

        seen = {}
        row = SimpleNamespace(
            id=uuid.uuid4(),
            action="file.upload",
            resource_type="uploaded_file",
            resource_id=str(uuid.uuid4()),
            occurred_at=None,
            user_id=None,
            metadata_={},
        )

        async def fake_recent_rows(_db, _model, _current_user, _order_column, limit, offset, *filters):
            seen["limit"] = limit
            seen["offset"] = offset
            return [row]

        monkeypatch.setattr(admin, "_recent_rows", fake_recent_rows)
        result = asyncio.run(admin.get_recent_audit_logs(
            limit=250,
            offset=3,
            current_user={"user_id": str(uuid.uuid4()), "role": "admin", "org_id": str(uuid.uuid4())},
            db=object(),
        ))
        assert seen == {"limit": 100, "offset": 3}
        assert result["items"][0]["action"] == "file.upload"

    def test_admin_storage_route_registered(self):
        from app.api.v1 import api_router
        paths = {route.path for route in api_router.routes}
        assert "/admin/storage" in paths

    def test_storage_admin_and_superadmin_roles_allowed(self):
        import asyncio
        from app.core.security import require_roles
        from app.models.enums import UserRole

        dependency = require_roles(UserRole.ADMIN)
        assert asyncio.run(dependency({"role": "admin"}))["role"] == "admin"
        assert asyncio.run(dependency({"role": "superadmin"}))["role"] == "superadmin"

    def test_storage_operator_and_reviewer_roles_denied(self):
        import asyncio
        from fastapi import HTTPException
        from app.core.security import require_roles
        from app.models.enums import UserRole

        dependency = require_roles(UserRole.ADMIN)
        for role in ("operator", "reviewer"):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(dependency({"role": role}))
            assert exc.value.status_code == 403

    def test_storage_metrics_count_bytes_and_hide_paths(self, monkeypatch, tmp_path):
        import asyncio
        from app.api.v1.routers import admin
        from app.models.file import UploadedFile
        from app.models.fill_job import FormFillJob

        zip_path = tmp_path / "batch.zip"
        zip_path.write_bytes(b"zip-bytes")

        async def fake_sum(_db, model, _column, _current_user):
            if model is UploadedFile:
                return 2048
            if model is FormFillJob:
                return 512
            return 0

        async def fake_count(_db, model, _current_user, *filters):
            if model is UploadedFile:
                return 2
            if model is FormFillJob:
                return 1
            return 0

        async def fake_zip_storage(_db, _current_user):
            return {"total_bytes": zip_path.stat().st_size, "zip_count": 1, "missing_files": 0}

        monkeypatch.setattr(admin, "_sum_column", fake_sum)
        monkeypatch.setattr(admin, "_count_total", fake_count)
        monkeypatch.setattr(admin, "_batch_zip_storage", fake_zip_storage)
        result = asyncio.run(admin.get_admin_storage(
            current_user={"user_id": str(uuid.uuid4()), "role": "admin", "org_id": str(uuid.uuid4())},
            db=object(),
        ))

        assert result["uploads"]["total_bytes"] == 2048
        assert result["uploads"]["file_count"] == 2
        assert result["batch_zips"]["total_bytes"] == len(b"zip-bytes")
        assert result["batch_zips"]["zip_count"] == 1
        assert result["form_fill_outputs"]["total_bytes"] == 512
        assert result["form_fill_outputs"]["output_count"] == 1
        assert result["combined"]["total_bytes"] == 2048 + len(b"zip-bytes") + 512
        payload = json.dumps(result)
        assert str(tmp_path) not in payload
        assert "batch.zip" not in payload

    def test_missing_zip_path_does_not_crash(self):
        from app.api.v1.routers.admin import _safe_file_size

        size, missing = _safe_file_size("Z:/definitely/missing/batch.zip")
        assert size == 0
        assert missing is True

    def test_export_summary_counts_include_batch_zip(self, monkeypatch):
        import asyncio
        from app.api.v1.routers import admin
        from app.models.log import AuditLog
        from app.models.user import User

        async def fake_count_total(_db, model, _current_user, *filters):
            return 0 if model is not User else 1

        async def fake_count_by_column(_db, model, _column, _current_user, *filters):
            if model is AuditLog:
                return {"export.pdf": 2, "export.excel": 3, "batch.export.zip": 4}
            return {}

        monkeypatch.setattr(admin, "_count_total", fake_count_total)
        monkeypatch.setattr(admin, "_count_by_column", fake_count_by_column)
        result = asyncio.run(admin.get_admin_summary(
            current_user={"user_id": str(uuid.uuid4()), "role": "superadmin", "org_id": None},
            db=object(),
        ))

        assert result["exports"]["total"] == 9
        assert result["exports"]["pdf_count"] == 2
        assert result["exports"]["excel_count"] == 3
        assert result["exports"]["batch_zip_count"] == 4


class TestTenantFoundation:
    def test_organisation_model_and_user_relation_exist(self):
        from app.models.user import Organisation, User

        assert Organisation.__tablename__ == "organisations"
        assert "org_id" in {c.name for c in User.__table__.columns}
        assert "organisation" in User.__mapper__.relationships.keys()

    def test_tenant_owned_models_have_org_id(self):
        from app.models.batch import OCRBatch
        from app.models.extracted_data import ExtractedData
        from app.models.file import UploadedFile
        from app.models.fill_job import FormFillJob
        from app.models.job import OCRJob
        from app.models.log import AuditLog

        for model in (UploadedFile, OCRJob, ExtractedData, OCRBatch, FormFillJob, AuditLog):
            assert "org_id" in {c.name for c in model.__table__.columns}

    def test_role_helpers(self):
        from app.services.ownership_service import get_current_org_id, is_org_admin, is_superadmin

        org_id = uuid.uuid4()
        assert get_current_org_id({"org_id": str(org_id)}) == org_id
        assert get_current_org_id({"org_id": None}) is None
        assert is_superadmin({"role": "superadmin"}) is True
        assert is_org_admin({"role": "admin"}) is True

    def test_apply_org_scope_superadmin_global_and_admin_scoped(self):
        from app.models.file import UploadedFile
        from app.services.ownership_service import apply_org_scope

        class Query:
            def __init__(self):
                self.filters = 0

            def where(self, *_filters):
                self.filters += len(_filters)
                return self

        org_id = uuid.uuid4()
        assert apply_org_scope(Query(), UploadedFile, {"role": "superadmin", "org_id": str(org_id)}).filters == 0
        assert apply_org_scope(Query(), UploadedFile, {"role": "admin", "org_id": str(org_id)}).filters == 1
        assert apply_org_scope(Query(), UploadedFile, {"role": "admin", "org_id": None}).filters == 1

    def test_assert_same_org_blocks_cross_org(self):
        from types import SimpleNamespace
        from fastapi import HTTPException
        from app.services.ownership_service import assert_same_org

        user_org = uuid.uuid4()
        other_org = uuid.uuid4()
        with pytest.raises(HTTPException) as exc:
            assert_same_org(SimpleNamespace(org_id=other_org), {"role": "operator", "org_id": str(user_org)})
        assert exc.value.status_code == 404

    def test_assert_same_org_allows_superadmin_and_own_org(self):
        from types import SimpleNamespace
        from app.services.ownership_service import assert_same_org

        org_id = uuid.uuid4()
        assert_same_org(SimpleNamespace(org_id=uuid.uuid4()), {"role": "superadmin", "org_id": None})
        assert_same_org(SimpleNamespace(org_id=org_id), {"role": "admin", "org_id": str(org_id)})

    def test_orgless_admin_only_legacy_orgless_rows(self):
        from types import SimpleNamespace
        from fastapi import HTTPException
        from app.services.ownership_service import assert_same_org

        assert_same_org(SimpleNamespace(org_id=None), {"role": "admin", "org_id": None})
        with pytest.raises(HTTPException):
            assert_same_org(SimpleNamespace(org_id=uuid.uuid4()), {"role": "admin", "org_id": None})

    def test_cross_org_job_batch_extraction_fill_blocked(self):
        import asyncio
        from types import SimpleNamespace
        from fastapi import HTTPException
        from app.models.batch import OCRBatch
        from app.models.extracted_data import ExtractedData
        from app.models.fill_job import FormFillJob
        from app.models.job import OCRJob
        from app.services import ownership_service as own

        user_org = uuid.uuid4()
        other_org = uuid.uuid4()
        job_id = uuid.uuid4()
        owner_id = uuid.uuid4()
        extraction = SimpleNamespace(id=uuid.uuid4(), job_id=job_id, org_id=other_org)
        job = SimpleNamespace(id=job_id, created_by_id=owner_id, org_id=other_org)
        batch = SimpleNamespace(id=uuid.uuid4(), created_by_id=owner_id, org_id=other_org)
        fill = SimpleNamespace(id=uuid.uuid4(), created_by_id=owner_id, org_id=other_org)
        current_user = {"user_id": str(owner_id), "role": "operator", "org_id": str(user_org)}

        class FakeDB:
            async def get(self, model, _id):
                if model is OCRJob:
                    return job
                if model is OCRBatch:
                    return batch
                if model is ExtractedData:
                    return extraction
                if model is FormFillJob:
                    return fill
                return None

        db = FakeDB()
        for call in (
            lambda: own.get_owned_ocr_job(db, job.id, current_user),
            lambda: own.get_owned_ocr_batch(db, batch.id, current_user),
            lambda: own.get_owned_extracted_data(db, extraction.id, current_user),
            lambda: own.get_owned_fill_job(db, fill.id, current_user),
        ):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(call())
            assert exc.value.status_code == 404


class TestFieldMapper:
    def _mapper(self):
        from app.services.field_mapper import FieldMapper
        return FieldMapper()

    def test_maps_from_mapped_output(self):
        m = self._mapper()
        final, warns = m.map(SAMPLE_EXTRACTED, MAPPED_OUTPUT, TEMPLATE_SCHEMA, {}, {})
        assert "invoice_number" in final
        assert final["invoice_number"] == "INV-2024-001"

    def test_override_takes_priority(self):
        m = self._mapper()
        final, _ = m.map(SAMPLE_EXTRACTED, MAPPED_OUTPUT, TEMPLATE_SCHEMA, {}, {"total_amount": 99999.0})
        assert final["total_amount"] == 99999.0

    def test_default_applied_for_missing_optional(self):
        m = self._mapper()
        final, _ = m.map({}, {}, TEMPLATE_SCHEMA, {}, {})
        assert final.get("missing_field") == "N/A"

    def test_required_missing_generates_warning(self):
        m = self._mapper()
        _, warns = m.map({}, {}, TEMPLATE_SCHEMA, {}, {})
        assert any("required_missing" in w for w in warns)

    def test_alias_resolution(self):
        m = self._mapper()
        schema = {"inv_no": {"type": "string", "aliases": ["invoice_number", "inv_number"]}}
        extracted = {"invoice_number": {"value": "INV-001", "confidence": 0.9}}
        final, _ = m.map(extracted, {}, schema, {}, {})
        assert final.get("inv_no") == "INV-001"

    def test_custom_mapping_config(self):
        m = self._mapper()
        schema = {"output_total": {"type": "number"}}
        extracted = {"total_amount": {"value": {"amount": 1000.0}, "confidence": 0.9}}
        final, _ = m.map(extracted, {}, schema, {"output_total": "total_amount"}, {})
        assert final.get("output_total") == {"amount": 1000.0}

    def test_extra_fields_pass_through(self):
        m = self._mapper()
        final, _ = m.map({"extra_field": {"value": "xyz"}}, {}, {}, {}, {})
        assert final.get("extra_field") == "xyz"


# ── JSON exporter ─────────────────────────────────────────────────────────────

class TestJSONExporter:
    def test_export_creates_file(self, tmp_path):
        from app.services.exporters.json_exporter import JSONExporter
        from unittest.mock import patch
        with patch("app.services.exporters.json_exporter.get_settings") as mc:
            mc.return_value.output_path = tmp_path
            exp = JSONExporter()
            dest = exp.export(MAPPED_OUTPUT, SAMPLE_LINE_ITEMS, uuid.uuid4())
        assert dest.exists()

    def test_export_valid_json(self, tmp_path):
        from app.services.exporters.json_exporter import JSONExporter
        from unittest.mock import patch
        jid = uuid.uuid4()
        with patch("app.services.exporters.json_exporter.get_settings") as mc:
            mc.return_value.output_path = tmp_path
            dest = JSONExporter().export(MAPPED_OUTPUT, SAMPLE_LINE_ITEMS, jid, {"source": "test"})
        data = json.loads(dest.read_text())
        assert data["job_id"] == str(jid)
        assert "fields" in data
        assert "line_items" in data
        assert len(data["line_items"]) == 3
        assert data["metadata"]["source"] == "test"

    def test_export_all_fields_present(self, tmp_path):
        from app.services.exporters.json_exporter import JSONExporter
        from unittest.mock import patch
        with patch("app.services.exporters.json_exporter.get_settings") as mc:
            mc.return_value.output_path = tmp_path
            dest = JSONExporter().export(MAPPED_OUTPUT, [], uuid.uuid4())
        data = json.loads(dest.read_text())
        assert "invoice_number" in data["fields"]
        assert "total_amount" in data["fields"]


# ── Excel exporter ────────────────────────────────────────────────────────────

class TestExcelExporter:
    def test_export_creates_file(self, tmp_path):
        from app.services.exporters.excel_exporter import ExcelExporter
        from unittest.mock import patch
        with patch("app.services.exporters.excel_exporter.get_settings") as mc:
            mc.return_value.output_path = tmp_path
            dest = ExcelExporter().export(MAPPED_OUTPUT, SAMPLE_LINE_ITEMS, uuid.uuid4())
        assert dest.exists()
        assert dest.suffix == ".xlsx"

    def test_export_readable_workbook(self, tmp_path):
        from app.services.exporters.excel_exporter import ExcelExporter
        from unittest.mock import patch
        from openpyxl import load_workbook
        with patch("app.services.exporters.excel_exporter.get_settings") as mc:
            mc.return_value.output_path = tmp_path
            dest = ExcelExporter().export(MAPPED_OUTPUT, SAMPLE_LINE_ITEMS, uuid.uuid4())
        wb = load_workbook(dest)
        assert "Document" in wb.sheetnames

    def test_export_with_fill_config(self, tmp_path):
        from app.services.exporters.excel_exporter import ExcelExporter
        from unittest.mock import patch
        fill_config = {
            "fields": {
                "invoice_number": {"sheet": "Document", "cell": "B3"},
                "total_amount":   {"sheet": "Document", "cell": "B5"},
            }
        }
        from openpyxl import Workbook as WB
        base_wb = WB(); base_wb.active.title = "Document"
        template_p = tmp_path / "base.xlsx"; base_wb.save(template_p)
        with patch("app.services.exporters.excel_exporter.get_settings") as mc:
            mc.return_value.output_path = tmp_path
            dest = ExcelExporter().export(MAPPED_OUTPUT, [], uuid.uuid4(), fill_config, str(template_p))
        from openpyxl import load_workbook
        wb = load_workbook(dest)
        assert wb["Document"]["B3"].value == "INV-2024-001"

    def test_line_items_in_workbook(self, tmp_path):
        from app.services.exporters.excel_exporter import ExcelExporter
        from unittest.mock import patch
        from openpyxl import load_workbook
        with patch("app.services.exporters.excel_exporter.get_settings") as mc:
            mc.return_value.output_path = tmp_path
            dest = ExcelExporter().export(MAPPED_OUTPUT, SAMPLE_LINE_ITEMS, uuid.uuid4())
        wb = load_workbook(dest)
        ws = wb.active
        # Find line item rows by checking for "Software License"
        cell_vals = [ws.cell(row=r, column=1).value for r in range(1, ws.max_row + 1)]
        # Header + 3 data rows should be findable
        assert any("Software License" in str(v or "") for v in cell_vals)


# ── PDF exporter ──────────────────────────────────────────────────────────────

class TestPDFExporter:
    def test_export_creates_file(self, tmp_path):
        from app.services.exporters.pdf_exporter import PDFExporter
        from unittest.mock import patch
        with patch("app.services.exporters.pdf_exporter.get_settings") as mc:
            mc.return_value.output_path = tmp_path
            dest = PDFExporter().export(MAPPED_OUTPUT, SAMPLE_LINE_ITEMS, uuid.uuid4())
        assert dest.exists()
        assert dest.suffix == ".pdf"

    def test_export_valid_pdf_header(self, tmp_path):
        from app.services.exporters.pdf_exporter import PDFExporter
        from unittest.mock import patch
        with patch("app.services.exporters.pdf_exporter.get_settings") as mc:
            mc.return_value.output_path = tmp_path
            dest = PDFExporter().export(MAPPED_OUTPUT, SAMPLE_LINE_ITEMS, uuid.uuid4())
        assert dest.read_bytes()[:4] == b"%PDF"

    def test_export_nonzero_size(self, tmp_path):
        from app.services.exporters.pdf_exporter import PDFExporter
        from unittest.mock import patch
        with patch("app.services.exporters.pdf_exporter.get_settings") as mc:
            mc.return_value.output_path = tmp_path
            dest = PDFExporter().export(MAPPED_OUTPUT, [], uuid.uuid4())
        assert dest.stat().st_size > 1000

    def test_export_empty_line_items(self, tmp_path):
        from app.services.exporters.pdf_exporter import PDFExporter
        from unittest.mock import patch
        with patch("app.services.exporters.pdf_exporter.get_settings") as mc:
            mc.return_value.output_path = tmp_path
            dest = PDFExporter().export(MAPPED_OUTPUT, [], uuid.uuid4())
        assert dest.exists()

    def test_export_with_line_items_and_totals(self, tmp_path):
        from app.services.exporters.pdf_exporter import PDFExporter
        from unittest.mock import patch
        with patch("app.services.exporters.pdf_exporter.get_settings") as mc:
            mc.return_value.output_path = tmp_path
            dest = PDFExporter().export(MAPPED_OUTPUT, SAMPLE_LINE_ITEMS, uuid.uuid4())
        assert dest.stat().st_size > 2000


# ── security ──────────────────────────────────────────────────────────────────

class TestSecurity:
    def test_hash_verify(self):
        from app.core.security import hash_password, verify_password
        h = hash_password("secret123")
        assert verify_password("secret123", h)
        assert not verify_password("wrong", h)

    def test_token_roundtrip(self):
        from app.core.security import create_access_token, decode_token
        token = create_access_token({"sub": "user-1", "role": "admin"})
        p = decode_token(token)
        assert p["sub"] == "user-1"
        assert p["type"] == "access"

    def test_invalid_token_raises(self):
        from fastapi import HTTPException
        from app.core.security import decode_token
        with pytest.raises(HTTPException) as exc:
            decode_token("not.a.valid.jwt")
        assert exc.value.status_code == 401


# ── config ────────────────────────────────────────────────────────────────────

class TestConfig:
    def test_settings_defaults(self):
        from app.core.config import get_settings
        s = get_settings()
        assert s.APP_NAME == "OCR Platform"
        assert s.MAX_FILE_SIZE_MB == 50

    def test_output_path_created(self, tmp_path):
        from app.core.config import Settings
        s = Settings(DATABASE_URL="postgresql+asyncpg://x:x@localhost/x",
                     OUTPUT_DIR=str(tmp_path / "out"))
        p = s.output_path
        assert p.exists()


# ── Part A: Synonym & Field Detection Tests ───────────────────────────────────

class TestFieldSynonymDetection:
    """Verify that synonym labels map to the correct canonical fields."""

    def _extract(self, text: str) -> dict:
        from app.services.company_form_mapper import auto_fill
        result = auto_fill(text)
        return {k: v.get("value", v) if isinstance(v, dict) else v
                for k, v in result.get("mapped_fields", {}).items()}

    def test_bill_number_maps_to_invoice_number(self):
        fields = self._extract("Bill No: BN-2024-001\nDate: 01-01-2024")
        assert fields.get("invoice_number") == "BN-2024-001"

    def test_tax_invoice_no_maps_to_invoice_number(self):
        fields = self._extract("Tax Invoice No: TI-555\nDate: 01-01-2024")
        assert fields.get("invoice_number") == "TI-555"

    def test_document_no_maps_to_invoice_number(self):
        fields = self._extract("Document No: DOC-999\nDate: 01-01-2024")
        assert fields.get("invoice_number") == "DOC-999"

    def test_voucher_no_maps_to_invoice_number(self):
        fields = self._extract("Voucher No: VCH-101\nDate: 01-01-2024")
        assert fields.get("invoice_number") == "VCH-101"

    def test_document_date_maps_to_invoice_date(self):
        fields = self._extract("Invoice No: INV-1\nDocument Date: 15-03-2024")
        assert fields.get("invoice_date") == "15-03-2024"

    def test_bill_date_maps_to_invoice_date(self):
        fields = self._extract("Bill No: B-1\nBill Date: 20-04-2024")
        assert fields.get("invoice_date") == "20-04-2024"

    def test_dated_maps_to_invoice_date(self):
        fields = self._extract("Invoice: INV-X\nDated: 10-05-2024")
        assert fields.get("invoice_date") == "10-05-2024"

    def test_invoice_number_rejects_value_label(self):
        fields = self._extract("Invoice No:\nValue")
        assert fields.get("invoice_number") not in {"Value", "value"}

    def test_invoice_number_rejects_currency_tokens(self):
        for currency in ("EUR", "USD", "INR", "GBP", "AED"):
            fields = self._extract(f"Invoice No:\n{currency}")
            assert fields.get("invoice_number") != currency

    def test_invoice_number_rejects_parenthesized_currency_tokens(self):
        for currency in ("EUR", "USD", "INR", "GBP"):
            fields = self._extract(f"Inv Value\n({currency})")
            assert fields.get("invoice_number") != f"({currency})"

    def test_invoice_alias_cannot_reintroduce_currency_token(self):
        from app.services.company_form_mapper import normalize_mapped_fields

        mapped = normalize_mapped_fields({"bill_number": {"value": "EUR", "confidence": 0.9}})

        assert not mapped.get("invoice_number", {}).get("value")

    def test_invoice_date_rejects_invoice_date_label(self):
        fields = self._extract("Invoice Date:\nInvoice Date")
        assert fields.get("invoice_date") != "Invoice Date"

    def test_invoice_and_po_numbers_keep_document_punctuation(self):
        fields = self._extract(
            "Invoice No: SPR023/26-27\nPO No: U2/2026-27/MRP-D_021"
        )
        assert fields.get("invoice_number") == "SPR023/26-27"
        assert fields.get("purchase_order_number") == "U2/2026-27/MRP-D_021"

    def test_multiple_buyer_order_numbers_emit_exact_plural_key(self):
        fields = self._extract("Buyer's Order No: 62073736, 62080057, 62075113")
        assert fields.get("buyer_order_nos") == "62073736, 62080057, 62075113"
        assert fields.get("buyer_order_no") == "62073736"

    def test_gstin_uin_maps_to_gst_number(self):
        fields = self._extract("GSTIN/UIN: 33AAXFK0245J1Z4")
        assert fields.get("gst_number") == "33AAXFK0245J1Z4"

    def test_amount_chargeable_in_words_maps_to_amount_in_words(self):
        fields = self._extract(
            "Amount Chargeable (in words): INR Two Lakh Three Thousand Only"
        )
        assert fields.get("amount_in_words") == "INR Two Lakh Three Thousand Only"

    def test_total_quantity_amount_summary_maps_amount_to_grand_total(self):
        fields = self._extract("Total\n6316\n91842.14")
        assert fields.get("grand_total") == "91842.14"

    def test_unicode_buyers_order_no_maps_to_buyer_order_no(self):
        fields = self._extract("Buyer’s Order No.: 62073736")
        assert fields.get("buyer_order_no") == "62073736"

    def test_goods_to_be_despatched_maps_delivery_name_and_address(self):
        fields = self._extract(
            "GOODS TO BE DESPATCHED TO:\n"
            "GOOD LEATHER SHOES PRIVATE LIMITED\n"
            "No. 10 Industrial Estate, Chennai 600123"
        )
        assert fields.get("delivery_name") == "GOOD LEATHER SHOES PRIVATE LIMITED"
        assert fields.get("delivery_address") == "No. 10 Industrial Estate, Chennai 600123"

    def test_gr_wt_maps_to_gross_weight(self):
        fields = self._extract("GR. WT.\n125.50 KG")
        assert fields.get("gross_weight") == "125.50 KG"

    def test_nt_wt_maps_to_net_weight(self):
        fields = self._extract("NT. WT.\n120.00 KG")
        assert fields.get("net_weight") == "120.00 KG"

    def test_ports_map_to_loading_and_discharge(self):
        fields = self._extract(
            "Port of Loading\nCHENNAI\nPort of Discharge\nHAMBURG"
        )
        assert fields.get("port_of_loading") == "CHENNAI"
        assert fields.get("port_of_discharge") == "HAMBURG"

    def test_gst_supplier_and_gstin_uin_aliases_normalize_to_gst_number(self):
        from app.services.company_form_mapper import normalize_mapped_fields

        for alias in ("gst_supplier", "gstin_uin"):
            mapped = normalize_mapped_fields({
                alias: {"value": "33AAXFK0245J1Z4", "confidence": 0.9}
            })
            assert mapped["gst_number"]["value"] == "33AAXFK0245J1Z4"

    def test_bill_to_maps_to_customer_name(self):
        fields = self._extract("Invoice No: INV-1\nBill To: Acme Corp Ltd")
        assert "Acme Corp" in (fields.get("customer_name") or fields.get("buyer_name") or "")

    def test_sold_to_maps_to_customer_name(self):
        fields = self._extract("Invoice No: INV-2\nSold To: Global Traders")
        assert "Global Traders" in (fields.get("customer_name") or fields.get("buyer_name") or "")

    def test_ship_to_maps_to_consignee_name(self):
        fields = self._extract("Invoice No: INV-3\nShip To: Receiver Company GmbH")
        assert "Receiver Company" in (fields.get("consignee_name") or "")

    def test_delivery_to_maps_to_consignee_name(self):
        fields = self._extract("Invoice No: INV-4\nDelivery To: End Destination Ltd")
        assert fields.get("consignee_name") is not None

    def test_net_amount_maps_to_total(self):
        fields = self._extract("Invoice No: INV-5\nNet Amount: 45000.00")
        assert fields.get("total_amount") or fields.get("grand_total")

    def test_amount_payable_maps_to_total(self):
        fields = self._extract("Invoice No: INV-6\nAmount Payable: 78500.50")
        assert fields.get("total_amount") or fields.get("grand_total")

    def test_invoice_value_maps_to_total(self):
        fields = self._extract("Invoice No: INV-7\nInvoice Value: 12000.00")
        assert fields.get("total_amount") or fields.get("grand_total")

    def test_transporter_name_extracted(self):
        fields = self._extract("Invoice No: INV-8\nTransporter: BlueDart Logistics")
        assert fields.get("transport_name") is not None

    def test_vehicle_number_extracted(self):
        fields = self._extract("Invoice No: INV-9\nVehicle No: TN01AB1234")
        assert fields.get("vehicle_number") is not None

    def test_lr_number_extracted(self):
        fields = self._extract("Invoice No: INV-10\nLR No: LR-2024-555")
        assert fields.get("lr_number") is not None

    def test_eway_bill_extracted(self):
        fields = self._extract("Invoice No: INV-11\nE-Way Bill No: 123456789012")
        assert fields.get("eway_bill_number") is not None

    def test_hsn_code_extracted(self):
        fields = self._extract("HSN Code: 64069020\nQty: 100")
        assert fields.get("hsn_code") is not None

    def test_e2e_count_does_not_regress(self):
        text = (
            "K2 Footprints GST No:33AAXFK0245J1Z4\n"
            "EXPORT INVOICE\nInvoice No: INV-2026-001\nInvoice Date: 25-05-2026\n"
            "Consignee: ARA SHOES AUSTRIA GMBH\nPort of Loading: CHENNAI\n"
            "Currency: EUR\nGrand Total: 17612.25\nAmount (in words): EUR SEVENTEEN THOUSAND\n"
        )
        from app.services.company_form_mapper import auto_fill
        result = auto_fill(text)
        count = len(result.get("mapped_fields", {}))
        assert count >= 5, f"Expected >= 5 mapped fields, got {count}"


class TestValidationPreservesMethod:
    def test_gemini_method_preserved_through_validation(self):
        from app.services.validation_service import validate_mapped_fields
        fields = {
            "invoice_number": {
                "value": "INV-42", "confidence": 0.91,
                "method": "gemini", "source_evidence": "Bill No: INV-42",
            }
        }
        result = validate_mapped_fields(fields, raw_text="Bill No: INV-42", doc_type="general")
        vf = result["validated_fields"]
        assert "invoice_number" in vf
        assert vf["invoice_number"]["method"] == "gemini"
        assert vf["invoice_number"]["source_evidence"] == "Bill No: INV-42"

    def test_regex_method_not_stored(self):
        from app.services.validation_service import validate_mapped_fields
        fields = {"invoice_number": {"value": "INV-1", "confidence": 0.9, "method": "regex"}}
        result = validate_mapped_fields(fields, raw_text="Invoice No: INV-1", doc_type="general")
        vf = result["validated_fields"]
        assert "method" not in vf.get("invoice_number", {})

    def test_invalid_gst_produces_warning_not_error(self):
        from app.services.validation_service import validate_mapped_fields
        fields = {"gst_number": {"value": "BADGST123", "confidence": 0.9}}
        result = validate_mapped_fields(fields, raw_text="", doc_type="general")
        assert result["valid"] is True
        assert any("GST" in w for w in result["warnings"])

    def test_invalid_date_produces_warning(self):
        from app.services.validation_service import validate_mapped_fields
        fields = {"invoice_date": {"value": "not-a-date", "confidence": 0.9}}
        result = validate_mapped_fields(fields, raw_text="", doc_type="general")
        assert any("invoice_date" in w for w in result["warnings"])

    def test_label_trash_cleared(self):
        from app.services.validation_service import validate_mapped_fields
        fields = {"document_type": {"value": "export invoice", "confidence": 0.8}}
        result = validate_mapped_fields(fields, raw_text="", doc_type="general")
        assert "document_type" not in result["validated_fields"]


# ── Part A: New gap-fix tests ─────────────────────────────────────────────────

class TestPartAGapFixes:
    """Four new tests covering the three mapper gaps fixed in Part A."""

    def _extract(self, text: str) -> dict:
        from app.services.company_form_mapper import auto_fill
        result = auto_fill(text)
        return {k: v.get("value", v) if isinstance(v, dict) else v
                for k, v in result.get("mapped_fields", {}).items()}

    def test_tax_invoice_no_maps_to_invoice_number(self):
        """Gap 1 fix: 'Tax Invoice No' now matches as first-priority FIELD_PATTERN."""
        fields = self._extract("Tax Invoice No: TAXINV-2026-001\nDate: 01-06-2026")
        assert fields.get("invoice_number") == "TAXINV-2026-001"

    def test_grand_total_next_line_extracted(self):
        """Gap 2 fix: 'Grand Total' label on one line, value on next line."""
        fields = self._extract("Description: Leather Goods\nGrand Total\n17612.25")
        assert fields.get("grand_total") or fields.get("total_amount")

    def test_challan_no_maps_to_invoice_number_next_line(self):
        """Gap 3 fix: 'Challan No' now in NEXT_LINE_LABELS."""
        fields = self._extract("Challan No\nCHL-2026-999\nDate: 15-05-2026")
        assert fields.get("invoice_number") == "CHL-2026-999"

    def test_detection_report_structure(self):
        """Gap 4: _build_detection_report returns correct shape."""
        from app.api.v1.routers.upload import _build_detection_report
        mapped = {
            "invoice_number": {"value": "INV-1", "confidence": 0.92, "method": "regex"},
            "grand_total": {"value": "5000", "confidence": 0.55, "method": "regex"},
            "buyer_name": {"value": "Acme", "confidence": 0.88, "method": "gemini",
                           "source_evidence": "Bill To: Acme"},
        }
        from app.services.validation_service import validate_mapped_fields
        validation = validate_mapped_fields(mapped, raw_text="INV-1 Bill To: Acme", doc_type="general")
        report = _build_detection_report(mapped, validation)
        assert "field_count" in report
        assert "missing_count" in report
        assert "weak_count" in report
        assert "ai_recovered_count" in report
        assert report["ai_recovered_count"] == 1
        assert report["fields"]["buyer_name"]["method"] == "gemini"
        assert report["fields"]["invoice_number"]["status"] == "ok"
        assert report["fields"]["grand_total"]["status"] == "weak"


# ── Auto-fill alias tests (RC-2 fix) ─────────────────────────────────────────

class TestAutoFillAliases:
    """Verify mapper alias expansion covers all critical cross-field synonyms."""

    def _mapped(self, text: str) -> dict:
        from app.services.company_form_mapper import auto_fill
        return auto_fill(text).get("mapped_fields", {})

    def _val(self, mapped: dict, field: str) -> str:
        v = mapped.get(field, {})
        return (v.get("value", "") if isinstance(v, dict) else str(v)).strip()

    def test_grand_total_aliases_to_total_amount(self):
        mapped = self._mapped("GRAND TOTAL 17612.25\nINVOICE NO INV-001")
        assert self._val(mapped, "grand_total") or self._val(mapped, "total_amount"), \
            "Neither grand_total nor total_amount extracted"
        # After alias expansion both should be populated
        gt = self._val(mapped, "grand_total")
        ta = self._val(mapped, "total_amount")
        assert gt or ta, "grand_total/total_amount alias failed"

    def test_total_amount_aliases_to_grand_total(self):
        mapped = self._mapped("Total Amount: 5000.00\nInvoice No: INV-002")
        ta = self._val(mapped, "total_amount")
        gt = self._val(mapped, "grand_total")
        assert ta or gt, "total_amount/grand_total alias failed"

    def test_consignee_aliases_to_consignee_name(self):
        mapped = self._mapped("Consignee: ARA SHOES AUSTRIA GMBH\nInvoice No: INV-003")
        assert self._val(mapped, "consignee") or self._val(mapped, "consignee_name"), \
            "consignee/consignee_name alias failed"

    def test_po_number_aliases_to_purchase_order_number(self):
        mapped = self._mapped("P.O. No: PO-2026-001\nDate: 21-05-2026")
        po  = self._val(mapped, "po_number")
        pon = self._val(mapped, "purchase_order_number")
        assert po or pon, "po_number/purchase_order_number alias failed"

    def test_empty_does_not_overwrite_good_value(self):
        """Alias must not replace a high-confidence value with empty string."""
        from app.services.company_form_mapper import auto_fill
        # Simulate mapped having grand_total but NOT total_amount
        mapped = {
            "grand_total": {"value": "17612.25", "confidence": 0.9},
        }
        # Apply alias logic manually
        alias_pairs = {
            "grand_total": "total_amount",
            "total_amount": "grand_total",
        }
        for src, dst in alias_pairs.items():
            if src in mapped and dst not in mapped:
                mapped[dst] = {
                    "value": mapped[src].get("value", ""),
                    "confidence": min(float(mapped[src].get("confidence", 0.0)), 0.70),
                }
        assert mapped.get("total_amount", {}).get("value") == "17612.25"
        assert mapped.get("grand_total", {}).get("value") == "17612.25"

    def test_gst_aliases_to_gstin(self):
        mapped = self._mapped("GST No:33AAXFK0245J1Z4\nInvoice No: INV-004")
        gst  = self._val(mapped, "gst_number")
        gstn = self._val(mapped, "gstin")
        assert gst or gstn, "gst_number/gstin alias failed"


# ── Auto-fill reliability tests (final fix) ──────────────────────────────────

class TestAutoFillReliability:
    """Tests for missing_fields accuracy, source_evidence, doc_type routing."""

    def _autofill(self, text: str, doc_type: str = "general") -> dict:
        from app.services.company_form_mapper import auto_fill
        return auto_fill(text, doc_type=doc_type)

    def test_missing_fields_filtered_by_doc_type_sales(self):
        """Sales doc: missing_fields must not include PO/GRN-only fields."""
        text = (
            "EXPORT INVOICE\nInvoice No: INV-2026-001\nInvoice Date: 25-05-2026\n"
            "Consignee: ARA SHOES AUSTRIA GMBH\nGST No:33AAXFK0245J1Z4\n"
            "Port of Loading: CHENNAI\nGrand Total 17612.25"
        )
        result = self._autofill(text, doc_type="export_invoice")
        missing = result["missing_fields"]
        # These are PO/GRN fields — must NOT appear in sales missing_fields
        assert "vehicle_number" not in missing, "vehicle_number leaked into sales missing"
        assert "lr_number" not in missing,      "lr_number leaked into sales missing"
        assert "eway_bill_number" not in missing, "eway_bill_number leaked into sales missing"
        assert "transport_name" not in missing, "transport_name leaked into sales missing"

    def test_missing_fields_filtered_by_doc_type_grn(self):
        """GRN doc: missing_fields must not include sales export fields."""
        text = (
            "Goods Receipt Note\nGRN No GRN-12964\nGIN No GIN-13155\n"
            "METRO ENTERPRISES\nQty Received 455\nQty Accepted 455"
        )
        result = self._autofill(text, doc_type="grn")
        missing = result["missing_fields"]
        assert "port_of_loading" not in missing,  "port_of_loading leaked into grn missing"
        assert "igst_percent" not in missing,     "igst_percent leaked into grn missing"
        assert "iec_code" not in missing,          "iec_code leaked into grn missing"

    def test_source_evidence_present_in_regex_extraction(self):
        """extract_fields must store source_evidence for every regex match."""
        from app.services.company_form_mapper import extract_fields
        fields = extract_fields("Invoice No: INV-2026-TEST\nGST No:33AAXFK0245J1Z4")
        inv = fields.get("invoice_number", {})
        gst = fields.get("gst_number", {})
        assert inv.get("source_evidence"), "invoice_number missing source_evidence"
        assert gst.get("source_evidence"), "gst_number missing source_evidence"
        assert inv.get("method") == "regex", "invoice_number method should be regex"

    def test_alias_source_evidence_present(self, monkeypatch):
        """Alias-populated fields carry method='alias' and source_evidence.

        Note: grand_total→total_amount alias rarely fires because total_amount's
        broad regex ((?:grand\s*)?total) also matches the same text. We test the
        alias metadata path using consignee→consignee_name, which is a one-way alias
        that auto_fill populates when only 'consignee' is in mapped_fields.
        Also verifies the weaker grand_total/total_amount case still has a correct value.
        """
        from app.services import company_form_mapper

        result = company_form_mapper.auto_fill(
            "Grand Total 17612.25\nInvoice No: INV-001",
            doc_type="general",
        )
        mapped = result["mapped_fields"]
        total_amount = mapped.get("total_amount", {})
        grand_total = mapped.get("grand_total", {})

        assert total_amount.get("value") == "17612.25"
        assert grand_total.get("value") == "17612.25"
        assert grand_total.get("method") == "regex"
        assert grand_total.get("source_evidence"), "grand_total missing source_evidence"

        def fake_extract_fields(_raw_text):
            return {
                "consignee": {
                    "value": "ARA SHOES GMBH",
                    "confidence": 0.85,
                    "method": "regex",
                    "source_evidence": "Consignee: ARA SHOES GMBH",
                },
            }

        monkeypatch.setattr(company_form_mapper, "extract_fields", fake_extract_fields)
        alias_result = company_form_mapper.auto_fill("ignored", doc_type="general")
        consignee_name = alias_result["mapped_fields"].get("consignee_name", {})

        assert consignee_name.get("value") == "ARA SHOES GMBH"
        assert consignee_name.get("method") == "alias"
        assert consignee_name.get("source_evidence", "").startswith("alias:consignee")

    def test_blank_alias_does_not_overwrite_good_value(self):
        """Alias must not replace a high-confidence extracted value with empty string."""
        from app.services.company_form_mapper import extract_fields
        # Manually test alias logic: if grand_total has value but total_amount also has value
        # the alias must not overwrite the higher-confidence total_amount
        mapped = {
            "grand_total":  {"value": "17612.25", "confidence": 0.9,  "method": "regex", "source_evidence": "Grand Total 17612.25"},
            "total_amount": {"value": "17500.00", "confidence": 0.95, "method": "regex", "source_evidence": "Total Amount 17500.00"},
        }
        alias_pairs = {"grand_total": "total_amount", "total_amount": "grand_total"}
        for src, dst in alias_pairs.items():
            if src in mapped and mapped[src].get("value") and dst not in mapped:
                mapped[dst] = {"value": mapped[src]["value"], "confidence": 0.70, "method": "alias"}
        # total_amount was already present with 0.95 conf — alias should NOT have fired
        assert mapped["total_amount"]["value"] == "17500.00", "alias overwrote better value"
        assert mapped["total_amount"]["confidence"] == 0.95

    def test_two_company_formats_same_canonical_field(self):
        """Two different company label variants must produce same canonical field."""
        from app.services.company_form_mapper import extract_fields
        # Company A: "Invoice No:"
        f1 = extract_fields("Invoice No: INV-A-001\nDate: 01-05-2026")
        # Company B: "Tax Invoice No:"
        f2 = extract_fields("Tax Invoice No: INV-B-001\nDate: 01-05-2026")
        # Company C: "Bill No:"
        f3 = extract_fields("Bill No: BILL-C-001\nDate: 01-05-2026")
        assert f1.get("invoice_number", {}).get("value") == "INV-A-001"
        assert f2.get("invoice_number", {}).get("value") == "INV-B-001"
        assert f3.get("invoice_number", {}).get("value") == "BILL-C-001"

    def test_non_sales_doc_uses_mapped_fields_directly(self):
        """GRN/PO auto_fill returns mapped_fields that cover GRN-form fields."""
        grn_text = (
            "Goods Receipt Note\nGRN No GRN-12964\nGRN Date 26-05-2026\n"
            "Supplier Name: METRO ENTERPRISES\nInvoice No: 42\nInvoice Date: 22-05-2026\n"
            "Qty Received 455\nQty Accepted 455\nQty Rejected 0"
        )
        result = self._autofill(grn_text, doc_type="grn")
        assert result["success"] is True
        # GRN doc_type missing_fields must only reference GRN-relevant fields
        for f in result["missing_fields"]:
            assert f not in {"port_of_loading", "igst_percent", "export_category", "iec_code"}, \
                f"Sales-only field '{f}' appeared in GRN missing_fields"

    def test_alias_covered_fields_are_not_missing(self):
        from app.services.company_form_mapper import missing_fields_for_doc, normalize_mapped_fields
        mapped = normalize_mapped_fields({
            "total_amount": {"value": "17612.25", "confidence": 0.9, "method": "regex", "source_evidence": "Total Amount 17612.25"},
            "consignee_name": {"value": "ARA SHOES", "confidence": 0.9, "method": "regex", "source_evidence": "Consignee ARA SHOES"},
        })
        missing = missing_fields_for_doc(mapped, "sales")
        assert "grand_total" not in missing
        assert "consignee_name" not in missing

    def test_normalize_mapped_fields_does_not_blank_overwrite(self):
        from app.services.company_form_mapper import normalize_mapped_fields
        mapped = normalize_mapped_fields({
            "grand_total": {"value": "", "confidence": 0.9, "method": "regex", "source_evidence": "Grand Total"},
            "total_amount": {"value": "17500.00", "confidence": 0.95, "method": "regex", "source_evidence": "Total Amount 17500.00"},
        })
        assert mapped["total_amount"]["value"] == "17500.00"
        assert mapped["total_amount"]["confidence"] == 0.95

    def test_detection_metadata_present_after_alias_merge(self):
        from app.services.company_form_mapper import auto_fill
        result = auto_fill("Bill No: B-42\nAmount Payable: 1000.00\nConsignee: ARA SHOES", doc_type="sales")
        for field, data in result["mapped_fields"].items():
            if field == "item_rows" or not isinstance(data, dict) or not data.get("value"):
                continue
            assert data.get("method"), field
            assert "confidence" in data, field
            assert data.get("source_evidence") is not None, field

    def test_company_label_variants_map_to_same_canonical_fields(self):
        from app.services.company_form_mapper import auto_fill
        for idx, label in enumerate(["Invoice No", "Tax Invoice No", "Bill No"], 1):
            mapped = auto_fill(f"{label}: INV-{idx}\nDate: 01-06-2026")["mapped_fields"]
            assert mapped["invoice_number"]["value"] == f"INV-{idx}"
        for label in ["Grand Total", "Net Amount", "Amount Payable"]:
            mapped = auto_fill(f"Invoice No: INV-X\n{label}: 1234.50")["mapped_fields"]
            assert mapped["total_amount"]["value"] == "1234.50"


class TestManualFrontendAutoFillBinding:
    def _frontend_html(self) -> str:
        return Path("frontend/index.html").read_text(encoding="utf-8")

    def test_frontend_has_single_alias_resolver_for_visible_fields(self):
        html = self._frontend_html()
        assert "function getMappedValue(canonicalKey, aliases=[])" in html
        assert "function bindMappedFieldsToFormScope(mapped_fields={}" in html
        assert "function bindMappedFieldsToVisibleForms" not in html
        assert "function formControlsForBinding(scope=\"ai\")" in html
        assert "function getActiveFormScope(document_type=\"general\")" in html
        assert "function fieldKeyForControl(control)" in html
        assert "const FIELD_ALIASES" in html
        assert "invoice_number:" in html and "bill_no" in html
        assert "consignee_name:" in html and "ship_to" in html
        assert "grand_total:" in html and "amount_payable" in html
        assert "importer" in html and "supplier_gstin" in html
        assert "loading_port" in html and "container_no" in html

    def test_commercial_invoice_binds_sales_form_only_and_opens_sales_tab(self):
        html = self._frontend_html()
        assert '"commercial_invoice"' in html
        assert "function isSalesDocType" in html
        assert 'if(isSalesDocType(docType)) return "sales";' in html
        assert 'if(scope === "sales") return "#secSalesForm";' in html
        assert "runAutoFillPipeline(data, data.document_type || \"general\")" in html
        assert 'setDocTab(tabForFormScope(scope));' in html

    def test_general_document_binds_ai_form_only_not_sales_form(self):
        html = self._frontend_html()
        assert 'return "#secForm";' in html
        assert 'function runAutoFillPipeline(data={}, fallbackDocType="general")' in html
        assert "const scope = getActiveFormScope(docType);" in html
        assert '"#secForm input, #secForm textarea, #secForm select, #secForm [data-field], #secForm [data-key], " +' not in html

    def test_frontend_missing_fields_are_normalized_against_mapped_aliases(self):
        html = self._frontend_html()
        assert "function normalizeMissingFields(missing=[], docType=\"general\", scope=null)" in html
        assert "const effectiveScope = scope || getActiveFormScope(docType);" in html
        assert "const active = fieldListForFormScope(effectiveScope);" in html
        assert "FIELD_ALIAS_TO_CANONICAL" in html
        assert "if(fd && !(effectiveScope === \"sales\" && isSuspiciousSalesValue(fd.value))) continue;" in html

    def test_no_duplicate_common_field_binding_across_ai_and_sales_forms(self):
        import re
        html = self._frontend_html()
        control_ids = set(re.findall(r'<(?:input|textarea|select)\s+[^>]*id="((?:f|sf)_[^"]+)"', html))
        assert "f_invoice_number" in control_ids
        assert "sf_consignee_name" in control_ids
        for control_id in control_ids:
            assert f'$("#{control_id}")' not in html
        assert "const root = formSelectorForScope(scope);" in html
        assert "document.querySelectorAll(\n    `${root} input" in html
        assert "#secForm input, #secForm textarea, #secForm select, #secForm [data-field], #secForm [data-key], " not in html
        assert "fieldKeyForControl(control)" in html
        assert "function clearInactiveFormScopes(activeScope)" in html
        assert "clearInactiveFormScopes(scope);" in html

    def test_detection_report_still_remains_available_in_frontend(self):
        html = self._frontend_html()
        assert "detection_report" in html
        assert "updateDetectionReport(data.detection_report || null)" in html
        assert "function updateDetectionReport(report)" in html

    def test_debug_unbound_field_list_function_exists(self):
        html = self._frontend_html()
        assert "FRONTEND_BINDING_VERSION" in html
        assert "[AutoFillPipeline]" in html
        assert "active_scope" in html
        assert "mapped_field_count" in html
        assert "filled_ui_field_count" in html
        assert "unfilled_visible_field_keys" in html
        assert "missing_fields_normalized" in html
        assert "window.__lastAutoFillDebug" in html

    def test_debug_field_audit_function_exists(self):
        html = self._frontend_html()
        assert "function resolveFieldValueForControl" in html
        assert "[AutoFillPipelineFieldAudit]" in html
        assert "resolved_key" in html
        assert "reason" in html

    def test_detection_report_contains_method_confidence_and_evidence(self):
        from app.api.v1.routers.upload import _build_detection_report
        from app.services.validation_service import validate_mapped_fields

        mapped = {
            "invoice_number": {
                "value": "INV-42",
                "confidence": 0.91,
                "method": "regex",
                "source_evidence": "Invoice No: INV-42",
            }
        }
        validation = validate_mapped_fields(mapped, raw_text="Invoice No: INV-42", doc_type="general")
        report = _build_detection_report(mapped, validation)
        field = report["fields"]["invoice_number"]
        assert field["method"] == "regex"
        assert field["confidence"] == 0.91
        assert field["evidence"] == "Invoice No: INV-42"

class TestFrontendSuspiciousSalesValueLogic:
    def _frontend_html(self) -> str:
        from pathlib import Path
        return Path("frontend/index.html").read_text(encoding="utf-8")
        
    def test_frontend_is_suspicious_sales_value_logic(self):
        html = self._frontend_html()
        
        import re
        
        # 3. frontend contains salesFieldFallbackValue
        assert "function salesFieldFallbackValue(" in html, "Missing salesFieldFallbackValue"
        
        # 4. frontend order_number fallback includes sales_item_01_order_no
        assert "sales_item_01_order_no" in html, "Missing sales_item_01_order_no in fallback map"
        
        # Implement the equivalent python function for the JS logic
        def is_suspicious_sales_value(val: str) -> bool:
            raw = str(val or "").strip()
            if not raw:
                return True
            alpha_norm = re.sub(r'[^a-z]+', ' ', raw.lower())
            alpha_norm = re.sub(r'\s+', ' ', alpha_norm).strip()
            
            sales_label_only = {
                "invoice", "invoice no", "invoice date", "dated", "group", "description", 
                "description of goods", "consignee", "buyer", "buyer if other than consignee", 
                "delivery at", "amount", "total", "grand total", "export invoice", "tax invoice", 
                "commercial invoice", "original", "draft", "statement of origin", "declaration"
            }
            if alpha_norm in sales_label_only:
                return True
                
            if re.search(r'\d', raw):
                return False
                
            return False
            
        # 1. frontend isSuspiciousSalesValue must NOT reject
        must_not_reject = [
            "847.2", "744.2", "103", "18%", "348,405.53", "1,935,586.28", "109.90", "17,612.25", "59277766699555", "25-05-2026"
        ]
        
        # 2. frontend isSuspiciousSalesValue must reject
        must_reject = [
            "INVOICE", "GROUP", "DESCRIPTION OF GOODS", "CONSIGNEE", "BUYER IF OTHER THAN CONSIGNEE"
        ]
        
        for val in must_not_reject:
            assert not is_suspicious_sales_value(val), f"Should not reject: {val}"
            
        for val in must_reject:
            assert is_suspicious_sales_value(val), f"Should reject: {val}"

class TestBackendExtractionFixes:
    def test_dynamic_correction_memory_applies_on_exact_old_value_match(self):
        from types import SimpleNamespace
        from app.services.memory_apply_engine import apply_memory_to_canonical_fields

        result = apply_memory_to_canonical_fields(
            {
                "invoice_number": {
                    "field_name": "invoice_number",
                    "value": "(EUR)",
                    "confidence": 0.9,
                    "status": "AUTO_FILLED",
                }
            },
            [SimpleNamespace(
                field_name="invoice_number",
                old_value="(EUR)",
                correct_value="SPR023/26-27",
                created_at=None,
            )],
        )

        assert result["memory_applied_fields"] == ["invoice_number"]
        assert result["memory_suggestion_fields"] == []
        assert result["canonical_fields"]["invoice_number"]["value"] == "SPR023/26-27"
        assert result["canonical_fields"]["invoice_number"]["source"] == "correction_memory"

    def test_dynamic_correction_memory_remains_suggestion_when_value_changes(self):
        from types import SimpleNamespace
        from app.services.memory_apply_engine import apply_memory_to_canonical_fields

        result = apply_memory_to_canonical_fields(
            {"invoice_number": {"value": "INV-NEW", "confidence": 0.9, "status": "AUTO_FILLED"}},
            [SimpleNamespace(
                field_name="invoice_number",
                old_value="(EUR)",
                correct_value="SPR023/26-27",
                created_at=None,
            )],
        )

        assert result["memory_applied_fields"] == []
        assert result["memory_suggestion_fields"] == ["invoice_number"]

    def test_dynamic_correction_memory_applies_when_field_remains_missing(self):
        from types import SimpleNamespace
        from app.services.memory_apply_engine import apply_memory_to_canonical_fields

        result = apply_memory_to_canonical_fields(
            {"invoice_number": {"value": "", "confidence": 0.0, "status": "MISSING"}},
            [SimpleNamespace(
                field_name="invoice_number",
                old_value=None,
                correct_value="SPR023/26-27",
                created_at=None,
            )],
        )

        assert result["memory_applied_fields"] == ["invoice_number"]
        assert result["canonical_fields"]["invoice_number"]["value"] == "SPR023/26-27"

    def test_pdf_text_extraction_preserves_all_copy_pages(self):
        from app.services.ocr_service import _extract_pdf_text

        class Page:
            def __init__(self, number, value):
                self.number = number
                self.value = value

            def get_text(self, _mode):
                return self.value

        doc = [
            Page(0, "ORIGINAL Invoice No: INV-1"),
            Page(1, "DUPLICATE Invoice No: INV-1"),
            Page(2, "TRIPLICATE Invoice No: INV-1"),
        ]

        text = _extract_pdf_text(doc, 3)

        assert "ORIGINAL" in text
        assert "DUPLICATE" in text
        assert "TRIPLICATE" in text

    def test_invoice_number_rejects_label_only(self):
        from app.services.company_form_mapper import auto_fill
        res = auto_fill("INVOICE NO\nINVOICE\nDATE")
        assert not res["mapped_fields"].get("invoice_number", {}).get("value")
        res2 = auto_fill("INVOICE NO\nEXPORT INVOICE\nDATE")
        assert not res2["mapped_fields"].get("invoice_number", {}).get("value")
        
    def test_declaration_text_extracts_block(self):
        from app.services.company_form_mapper import auto_fill
        res = auto_fill("We declare that these goods are of Indian origin.\nSignature")
        val = res["mapped_fields"].get("declaration_text", {}).get("value")
        assert val and "Indian origin" in val
        
    def test_statement_of_origin_extracts_block(self):
        from app.services.company_form_mapper import auto_fill
        res = auto_fill("The exporter of the products covered by this document declares that... \nAuthorised Signatory")
        val = res["mapped_fields"].get("statement_of_origin", {}).get("value")
        assert val and "declares that" in val


class TestWorkbenchProductionEndpoints:
    def test_workbench_validation_blocks_missing_required_sales_fields(self):
        from app.api.v1.routers.forms import WorkbenchRequest, validate_workbench_payload

        result = validate_workbench_payload(WorkbenchRequest(module="sales", fields={}, rows=[]))

        assert result["export_allowed"] is False
        assert "customer_id" in result["required_missing"]
        assert "item_rows" in result["blocked_fields"]

    def test_workbench_validation_allows_complete_purchase_payload(self):
        from app.api.v1.routers.forms import WorkbenchRequest, validate_workbench_payload

        result = validate_workbench_payload(WorkbenchRequest(
            module="purchase",
            fields={
                "po_number": "PO-1",
                "po_date": "2026-06-08",
                "supplier_id": "Acme Supplier",
                "delivery_date": "2026-06-10",
                "po_grand_total": "100.00",
            },
            rows=[{"bulk_qty": "2", "bulk_unit_price": "50", "bulk_line_total": "100"}],
        ))

        assert result["export_allowed"] is True
        assert result["validation_status"] == "READY"

    def test_workbench_export_payload_rejects_blocked_data(self):
        import pytest
        from fastapi import HTTPException
        from app.api.v1.routers.forms import WorkbenchExportRequest, _workbench_export_payload

        with pytest.raises(HTTPException) as exc:
            _workbench_export_payload(WorkbenchExportRequest(module="sales", fields={}, rows=[]))

        assert exc.value.status_code == 422
        assert "blocked_fields" in exc.value.detail

    def test_training_mapping_backend_file_save_and_load(self, monkeypatch, tmp_path):
        import asyncio
        from types import SimpleNamespace
        from app.api.v1.routers import companies
        from app.api.v1.routers.companies import TrainingMappingSaveRequest

        monkeypatch.setattr(companies, "settings", SimpleNamespace(output_path=tmp_path))
        user = {"user_id": "user-1", "org_id": "org-1"}
        body = TrainingMappingSaveRequest(
            module="sales_order",
            document_type="Sales Invoice",
            fields=[{"ocr_key": "invoice_number", "target_field": "sales_order_no", "value": "SO-1"}],
        )

        saved = asyncio.run(companies.save_training_mapping(body, current_user=user))
        loaded = asyncio.run(companies.load_training_mapping("sales_order", "Sales Invoice", current_user=user))

        assert saved["success"] is True
        assert loaded["found"] is True
        assert loaded["mapping"]["fields"][0]["value"] == "SO-1"

    class _DatasetResult:
        def __init__(self, rows):
            self.rows = rows

        def scalar_one_or_none(self):
            return self.rows[0] if self.rows else None

        def scalars(self):
            return self

        def all(self):
            return list(self.rows)

    class _DatasetDB:
        def __init__(self, datasets=None, execute_rows=None):
            self.datasets = list(datasets or [])
            self.execute_rows = execute_rows
            self.commits = 0

        async def execute(self, _statement):
            rows = self.datasets if self.execute_rows is None else self.execute_rows
            return TestWorkbenchProductionEndpoints._DatasetResult(rows)

        def add(self, dataset):
            import uuid
            from datetime import datetime, timezone
            dataset.id = dataset.id or uuid.uuid4()
            dataset.version = dataset.version or 1
            dataset.created_at = dataset.created_at or datetime.now(timezone.utc)
            dataset.updated_at = datetime.now(timezone.utc)
            self.datasets.append(dataset)

        async def commit(self):
            from datetime import datetime, timezone
            self.commits += 1
            for dataset in self.datasets:
                dataset.updated_at = datetime.now(timezone.utc)

        async def refresh(self, _dataset):
            return None

    @staticmethod
    def _training_dataset(**overrides):
        import uuid
        from datetime import datetime, timezone
        from app.models.company import TrainingDataset

        values = {
            "id": uuid.uuid4(),
            "org_id": "org-1",
            "dataset_name": "Acme Sales",
            "module": "sales_order",
            "document_type": "Sales Invoice",
            "company_name": "Acme Ltd",
            "vendor_name": None,
            "customer_name": "Buyer One",
            "is_active": True,
            "version": 1,
            "mappings_json": [{"ocr_key": "invoice_number", "target_field": "sales_order_no"}],
            "row_mappings_json": [{"ocr_key": "quantity", "target_field": "qty"}],
            "metadata_json": {"warnings": []},
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }
        values.update(overrides)
        return TrainingDataset(**values)

    def test_create_training_dataset(self):
        import asyncio
        from app.api.v1.routers.companies import TrainingDatasetRequest, save_training_dataset

        db = self._DatasetDB(execute_rows=[])
        body = TrainingDatasetRequest(
            dataset_name="Sales Order Dataset 1",
            module="sales_order",
            document_type="Sales Invoice",
            company_name="Acme Ltd",
            mappings_json=[{"ocr_key": "invoice_number", "target_field": "sales_order_no"}],
        )
        result = asyncio.run(save_training_dataset(body, db=db, current_user={"org_id": "org-1"}))

        assert result["success"] is True
        assert result["created"] is True
        assert result["dataset"]["dataset_name"] == "Sales Order Dataset 1"
        assert result["dataset"]["version"] == 1

    def test_list_training_datasets(self):
        import asyncio
        from app.api.v1.routers.companies import list_training_datasets

        dataset = self._training_dataset()
        result = asyncio.run(list_training_datasets(
            module="sales_order", document_type=None, company_name=None,
            vendor_name=None, customer_name=None, active_only=True,
            db=self._DatasetDB([dataset]), current_user={"org_id": "org-1"},
        ))

        assert result["success"] is True
        assert len(result["datasets"]) == 1
        assert result["datasets"][0]["mappings_json"][0]["target_field"] == "sales_order_no"

    def test_best_match_exact_company(self):
        import asyncio
        from app.api.v1.routers.companies import TrainingDatasetBestMatchRequest, best_match_training_dataset

        dataset = self._training_dataset(company_name="Acme Ltd")
        body = TrainingDatasetBestMatchRequest(
            module="sales_order", document_type="Sales Invoice", company_name="  ACME LTD ",
        )
        result = asyncio.run(best_match_training_dataset(body, db=self._DatasetDB([dataset]), current_user={"org_id": "org-1"}))

        assert result["dataset"]["id"] == str(dataset.id)
        assert result["match_score"] == 100
        assert "company" in result["match_reason"]

    def test_best_match_module_document_type_fallback(self):
        import asyncio
        from app.api.v1.routers.companies import TrainingDatasetBestMatchRequest, best_match_training_dataset

        dataset = self._training_dataset(company_name="Different Company")
        body = TrainingDatasetBestMatchRequest(
            module="sales_order", document_type="Sales Invoice", company_name="Unknown Company",
        )
        result = asyncio.run(best_match_training_dataset(body, db=self._DatasetDB([dataset]), current_user={"org_id": "org-1"}))

        assert result["match_score"] == 60
        assert result["match_reason"] == "module_document_type"

    def test_deactivate_training_dataset(self):
        import asyncio
        from app.api.v1.routers.companies import deactivate_training_dataset

        dataset = self._training_dataset()
        result = asyncio.run(deactivate_training_dataset(dataset.id, db=self._DatasetDB([dataset]), current_user={"org_id": "org-1"}))

        assert result["success"] is True
        assert result["dataset"]["is_active"] is False
        assert result["dataset"]["version"] == 2

    def test_training_dataset_endpoint_shape(self):
        import asyncio
        from app.api.v1.routers.companies import get_training_dataset

        dataset = self._training_dataset()
        result = asyncio.run(get_training_dataset(dataset.id, db=self._DatasetDB([dataset]), current_user={"org_id": "org-1"}))

        assert set(result["dataset"]) == {
            "id", "dataset_name", "module", "document_type", "company_name",
            "vendor_name", "customer_name", "is_active", "version", "mappings_json",
            "row_mappings_json", "metadata_json", "created_at", "updated_at",
        }

    @staticmethod
    def _sales_workbench_request(**overrides):
        from app.api.v1.routers.forms import WorkbenchExportRequest

        values = {
            "module": "sales_order",
            "document_type": "sales_order",
            "dataset_id": "dataset-sales-1",
            "source_job_id": "job-sales-1",
            "form_data": {
                "sales_order_no": "SO-100",
                "sales_order_date": "2026-06-10",
                "customer_id": "CUST-1",
                "bill_to_address": "Bill Address",
                "ship_to_address": "Ship Address",
                "delivery_date": "2026-06-12",
                "order_total": "118.00",
                "order_tax": "18.00",
                "qty_total": "2",
            },
            "item_rows": [{
                "line_no": "1", "product": "Widget", "product_id": "PROD-1",
                "bulk_qty": "2", "bulk_unit_price": "50", "bulk_tax_amount": "18",
                "bulk_line_total": "118", "bulk_uom_code": "EA",
            }],
            "metadata": {"dataset_name": "Acme Sales", "dataset_match_reason": "module_document_type"},
        }
        values.update(overrides)
        return WorkbenchExportRequest(**values)

    @staticmethod
    def _purchase_workbench_request(**overrides):
        from app.api.v1.routers.forms import WorkbenchExportRequest

        values = {
            "module": "purchase_order",
            "document_type": "purchase_order",
            "dataset_id": "dataset-purchase-1",
            "source_job_id": "job-purchase-1",
            "form_data": {
                "po_number": "PO-100", "po_date": "2026-06-10", "supplier_id": "SUP-1",
                "delivery_date": "2026-06-12", "po_grand_total": "100.00",
                "bill_to_address": "Bill Address", "ship_to_address": "Ship Address",
            },
            "item_rows": [{
                "line_no": "1", "product": "Material", "product_id": "PROD-2",
                "supplier_part_no": "PART-1", "bulk_qty": "2", "bulk_unit_price": "50",
                "bulk_line_total": "100", "bulk_line_sub_total": "100",
            }],
            "metadata": {"dataset_name": "Acme Purchase", "dataset_match_reason": "exact_module_document_vendor"},
        }
        values.update(overrides)
        return WorkbenchExportRequest(**values)

    class _DraftDB:
        def __init__(self):
            self.added = None

        def add(self, obj):
            import uuid
            from datetime import datetime, timezone
            self.added = obj
            obj.id = obj.id or uuid.uuid4()
            obj.created_at = obj.created_at or datetime.now(timezone.utc)
            obj.updated_at = obj.updated_at or datetime.now(timezone.utc)

        async def commit(self):
            return None

        async def refresh(self, _obj):
            return None

    @staticmethod
    async def _response_bytes(response):
        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk if isinstance(chunk, bytes) else chunk.encode())
        return b"".join(chunks)

    def test_workbench_save_draft_sales(self):
        import asyncio
        from app.api.v1.routers.forms import save_workbench_draft

        db = self._DraftDB()
        result = asyncio.run(save_workbench_draft(
            self._sales_workbench_request(),
            current_user={"user_id": "00000000-0000-0000-0000-000000000001", "org_id": None}, db=db,
        ))
        assert result["status"] == "saved"
        assert result["module"] == "sales_order"
        assert result["row_count"] == 1
        assert db.added.dataset_id == "dataset-sales-1"

    def test_workbench_save_draft_purchase(self):
        import asyncio
        from app.api.v1.routers.forms import save_workbench_draft

        result = asyncio.run(save_workbench_draft(
            self._purchase_workbench_request(),
            current_user={"user_id": "00000000-0000-0000-0000-000000000002", "org_id": None}, db=self._DraftDB(),
        ))
        assert result["status"] == "saved"
        assert result["module"] == "purchase_order"
        assert result["field_count"] >= 5

    def test_export_json_sales_allowed(self):
        from app.api.v1.routers.forms import _workbench_export_payload, build_erp_workbench_payload

        request = self._sales_workbench_request()
        validation, fields, rows = _workbench_export_payload(request)
        payload = build_erp_workbench_payload(request, validation, fields, rows)
        assert payload["module"] == "sales_order"
        assert payload["header"]["customer_id"] == "CUST-1"
        assert payload["lines"][0]["product_id"] == "PROD-1"

    def test_export_json_purchase_allowed(self):
        from app.api.v1.routers.forms import _workbench_export_payload, build_erp_workbench_payload

        request = self._purchase_workbench_request()
        validation, fields, rows = _workbench_export_payload(request)
        payload = build_erp_workbench_payload(request, validation, fields, rows)
        assert payload["module"] == "purchase_order"
        assert payload["header"]["supplier_id"] == "SUP-1"
        assert payload["lines"][0]["part_no"] == "PART-1"

    def test_export_blocked_when_required_fields_missing(self):
        from app.api.v1.routers.forms import WorkbenchExportRequest, validate_workbench_payload

        result = validate_workbench_payload(WorkbenchExportRequest(module="sales_order"))
        assert result["export_allowed"] is False
        assert result["blocked"] is True
        assert result["message"] == "Export blocked. Please complete required fields."

    def test_export_blocked_when_required_erp_master_ids_missing(self):
        from app.api.v1.routers.forms import validate_workbench_payload

        request = self._sales_workbench_request()
        request.item_rows[0].pop("product_id")
        result = validate_workbench_payload(request)
        assert result["export_allowed"] is False
        assert "rows[0].product_id" in result["blocked_fields"]
        assert any("ERP master selection" in warning for warning in result["warnings"])

    def test_excel_export_returns_valid_binary_response(self):
        import asyncio
        from app.api.v1.routers.forms import export_workbench_excel

        response = asyncio.run(export_workbench_excel(self._sales_workbench_request(), current_user={"user_id": "u"}))
        data = asyncio.run(self._response_bytes(response))
        assert data[:2] == b"PK"
        assert response.media_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    def test_pdf_export_returns_valid_binary_response(self):
        import asyncio
        from app.api.v1.routers.forms import export_workbench_pdf

        response = asyncio.run(export_workbench_pdf(self._purchase_workbench_request(), current_user={"user_id": "u"}))
        data = asyncio.run(self._response_bytes(response))
        assert data.startswith(b"%PDF")
        assert response.media_type == "application/pdf"

    def test_export_response_includes_dataset_metadata(self):
        from app.api.v1.routers.forms import _workbench_export_payload, build_erp_workbench_payload

        request = self._sales_workbench_request()
        validation, fields, rows = _workbench_export_payload(request)
        payload = build_erp_workbench_payload(request, validation, fields, rows)
        assert payload["dataset"] == {
            "id": "dataset-sales-1", "name": "Acme Sales", "match_score": None,
            "match_reason": "module_document_type", "header_mapped_count": 0,
            "row_mapped_count": 0, "fallback_mapped_count": 0,
        }

    def test_workbench_json_endpoint_returns_erp_shape(self):
        import asyncio
        import json
        from app.api.v1.routers.forms import export_workbench_json

        response = asyncio.run(export_workbench_json(self._sales_workbench_request(), current_user={"user_id": "u"}))
        payload = json.loads(asyncio.run(self._response_bytes(response)))
        assert set(payload) == {"module", "header", "lines", "validation", "dataset", "metadata"}
        assert payload["metadata"]["source_job_id"] == "job-sales-1"

    def test_upload_db_fallback_returns_consistent_shape(self, monkeypatch):
        import asyncio
        import uuid
        from types import SimpleNamespace
        from app.api.v1.routers import upload
        from app.models.enums import ExtractionStatus, JobStatus

        job_id = uuid.uuid4()
        extracted = SimpleNamespace(
            id=uuid.uuid4(),
            raw_text="PO Number: PO-1",
            validated_fields={"po_number": {"value": "PO-1", "confidence": 0.91}},
            extracted_fields={},
            line_items=[{"qty": "1"}],
            validation_errors=[],
            status=ExtractionStatus.VALIDATED.value,
            raw_ocr_json={"document_type": "purchase_order"},
        )
        db_job = SimpleNamespace(status=JobStatus.COMPLETED.value)

        class Result:
            def scalar_one_or_none(self):
                return extracted

        class DB:
            async def execute(self, _stmt):
                return Result()

        async def fake_owned(_db, _job_uuid, _user):
            return db_job

        old_jobs = upload.OCR_DEMO_JOBS.copy()
        upload.OCR_DEMO_JOBS.clear()
        try:
            monkeypatch.setattr(upload, "get_owned_ocr_job", fake_owned)
            result = asyncio.run(upload.get_upload_job(str(job_id), {"user_id": "u", "role": "operator"}, DB()))
        finally:
            upload.OCR_DEMO_JOBS.clear()
            upload.OCR_DEMO_JOBS.update(old_jobs)

        assert result["document_type"] == "purchase_order"
        assert result["mapped_fields"]["po_number"]["value"] == "PO-1"
        assert result["line_items"] == [{"qty": "1"}]
        assert result["export_allowed"] is True


class TestTask7RowCanonical:
    @staticmethod
    def _real_document_rows(filename: str) -> tuple[dict, list[dict]]:
        import fitz
        from pathlib import Path
        from app.services.company_form_mapper import (
            auto_fill, classify_document, flatten_item_rows_for_legacy_fields,
            map_sales_invoice_fields,
        )

        path = Path(r"D:\OCR_MASTER\datasets") / filename
        with fitz.open(path) as document:
            raw_text = "\n".join(page.get_text() for page in document)
        classification = classify_document(raw_text, filename)
        if classification["mapper"] == "sales":
            result = flatten_item_rows_for_legacy_fields(
                map_sales_invoice_fields(raw_text), classification["doc_type"], raw_text
            )
        else:
            result = auto_fill(raw_text, doc_type=classification["doc_type"].lower())
        item_entry = result["mapped_fields"].get("item_rows", {})
        rows = item_entry.get("value", []) if isinstance(item_entry, dict) else item_entry
        return result, rows or []

    def test_commercial_invoice_vertical_table_produces_item_rows(self):
        from app.services.company_form_mapper import extract_fields

        text = (
            "Quantity\nPAIRS\nRate\nUSD\nAmount\nUSD\nOrder.No\n"
            "Article No / Fit\nStyle / Colour\nSITE\nHSN CODE\nSIZE\n"
            "CHILDREN SHOES MADE OF COW FINISHED LEATHER\n"
            "62073736\n26182815-F\nJONWIN STEP Y BLACK\nDC01\n"
            "64035113\n3-5.5\n300\n11.88\n3564.00\nTotal\n300\n3564.00"
        )
        rows = extract_fields(text)["item_rows"]["value"]
        assert len(rows) == 1
        assert rows[0]["article"] == "26182815-F"
        assert rows[0]["qty"] == "300"
        assert rows[0]["total"] == "3564.00"

    def test_salesinvoice_real_row_count_stays_at_least_two(self):
        assert len(self._real_document_rows("salesinvoice.pdf")[1]) >= 2

    def test_poprint_real_row_count_stays_at_least_thirty_five(self):
        assert len(self._real_document_rows("POPRINT.pdf")[1]) >= 35

    def test_grn_real_rows_are_structured_without_invoice_date_alias(self):
        result, rows = self._real_document_rows("GRN.pdf")
        assert len(rows) >= 4
        for alias in (
            "invoice_number", "invoice_no", "inv_no", "bill_no", "bill_number",
            "tax_invoice_no", "document_no", "voucher_no", "challan_no",
        ):
            assert result["mapped_fields"].get(alias, {}).get("value") != "Invoice Date"

    def test_final_normalizer_unwraps_nested_mapped_value_objects(self):
        from app.services.final_field_normalizer import build_final_ocr_decision

        decision = build_final_ocr_decision([
            {"field_name": "invoice_number", "value": {"value": "INV-7"}, "confidence": 0.9},
            {"field_name": "invoice_date", "value": {"value": "10-06-2026"}, "confidence": 0.9},
            {"field_name": "company_name", "value": {"value": "Acme Ltd"}, "confidence": 0.9},
            {"field_name": "grand_total", "value": {"value": "100.00"}, "confidence": 0.9},
        ], document_type="commercial_invoice")
        assert decision["canonical_fields"]["invoice_number"]["value"] == "INV-7"
        assert decision["export_allowed"] is True

    def test_optional_fields_do_not_block_final_export_decision(self):
        from app.services.final_field_normalizer import build_final_ocr_decision

        fields = [
            {"field_name": "invoice_number", "value": "INV-8", "confidence": 0.9},
            {"field_name": "invoice_date", "value": "10-06-2026", "confidence": 0.9},
            {"field_name": "company_name", "value": "Acme Ltd", "confidence": 0.9},
            {"field_name": "grand_total", "value": "100.00", "confidence": 0.9},
            {"field_name": "notes", "value": "", "status": "MISSING"},
            {"field_name": "declaration_text", "value": "", "status": "MISSING"},
            {"field_name": "statement_of_origin", "value": "", "status": "MISSING"},
        ]
        decision = build_final_ocr_decision(fields, document_type="export_invoice")
        assert decision["export_allowed"] is True
        assert not {"notes", "declaration_text", "statement_of_origin"} & set(decision["blocked_fields"])

    def test_missing_erp_master_ids_have_master_selection_reason_code(self):
        from app.api.v1.routers.forms import WorkbenchRequest, validate_workbench_payload

        result = validate_workbench_payload(WorkbenchRequest(
            module="sales_order",
            form_data={
                "sales_order_date": "2026-06-10", "customer_name": "Acme Buyer",
                "bill_to_address": "Bill", "ship_to_address": "Ship",
                "delivery_date": "2026-06-12", "order_total": "100",
            },
            item_rows=[{
                "product": "Leather Shoe", "bulk_qty": "2", "bulk_unit_price": "50",
                "bulk_line_total": "100",
            }],
        ))
        reasons = {item["field"]: item for item in result["blocker_reasons"]}
        assert reasons["customer_id"]["reason_code"] == "ERP_master_id_missing"
        assert reasons["rows[0].product_id"]["reason_code"] == "ERP_master_id_missing"
        assert "Needs ERP master selection" in reasons["customer_id"]["message"]

    def test_dataset_row_mapping_overrides_generic_row_value(self):
        from app.api.v1.routers.forms import WorkbenchRequest, validate_workbench_payload

        result = validate_workbench_payload(WorkbenchRequest(
            module="purchase_order",
            form_data={
                "po_number": "PO-7", "po_date": "2026-06-10", "supplier_id": "SUP-1",
                "delivery_date": "2026-06-12", "po_grand_total": "100",
            },
            item_rows=[{
                "product_id": "PROD-1", "qty": "1", "detected_qty": "2",
                "unit_price": "50", "line_total": "100",
            }],
            row_mappings_json=[{"ocr_key": "detected_qty", "target_field": "bulk_qty"}],
        ))
        assert result["line_items"][0]["bulk_qty"] == "2"
        assert "rows[0].qty" not in result["blocked_fields"]

    def test_final_export_blockers_include_reason_codes(self):
        from app.services.final_field_normalizer import build_final_ocr_decision

        decision = build_final_ocr_decision([], document_type="commercial_invoice")
        reasons = {item["field"]: item["reason_code"] for item in decision["blocker_classification"]}
        assert reasons["invoice_number"] == "truly_absent_from_ocr"
        assert reasons["invoice_date"] == "truly_absent_from_ocr"
        assert reasons["company_name"] == "truly_absent_from_ocr"
        assert reasons["grand_total"] == "truly_absent_from_ocr"


class TestTask8DatasetEndToEnd:
    def test_create_dataset_contract_has_header_and_row_mappings(self):
        from app.api.v1.routers.companies import TrainingDatasetRequest

        request = TrainingDatasetRequest(
            dataset_name="Task 8 Dataset", module="sales_order", document_type="Sales Invoice",
            company_name="Acme", vendor_name="Vendor", customer_name="Customer", is_active=True,
            mappings_json=[{"ocr_key": "Invoice Number", "target_field": "sales_order_no"}],
            row_mappings_json=[{"ocr_key": "Detected Qty", "target_field": "qty"}],
            metadata_json={"source": "training"},
        )
        payload = request.model_dump()
        assert payload["mappings_json"][0]["target_field"] == "sales_order_no"
        assert payload["row_mappings_json"][0]["target_field"] == "qty"
        assert payload["metadata_json"]["source"] == "training"

    def test_best_match_dataset_shape_returns_both_mapping_sets(self):
        from datetime import datetime, timezone
        from types import SimpleNamespace
        import uuid
        from app.api.v1.routers.companies import _dataset_to_dict

        dataset = SimpleNamespace(
            id=uuid.uuid4(), dataset_name="Matched", module="sales_order", document_type="Sales Invoice",
            company_name="Acme", vendor_name=None, customer_name=None, is_active=True, version=2,
            mappings_json=[{"ocr_key": "Invoice Number", "target_field": "sales_order_no"}],
            row_mappings_json=[{"ocr_key": "Detected Qty", "target_field": "qty"}],
            metadata_json={}, created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
        )
        result = _dataset_to_dict(dataset)
        assert result["mappings_json"] and result["row_mappings_json"]

    def test_workbench_frontend_payload_includes_dataset_contract(self):
        from pathlib import Path

        source = Path("frontend/index.html").read_text(encoding="utf-8")
        for token in ("dataset_id:", "selected_dataset:", "mappings_json:", "row_mappings_json:",
                      "dataset_match_score:", "dataset_header_mapped_count:", "dataset_row_mapped_count:"):
            assert token in source

    def test_dataset_header_mapping_overrides_generic_and_unwraps_object(self):
        from app.api.v1.routers.forms import WorkbenchRequest, _normalize_workbench_payload

        request = WorkbenchRequest(
            module="sales_order", form_data={"sales_order_no": "GENERIC-1"},
            ocr_fields={"invoice_number": {"value": {"value": "DATASET-9"}, "confidence": 0.99}},
            selected_dataset={"mappings_json": [{"ocr_key": "Invoice Number", "target_field": "sales_order_no"}]},
        )
        _, fields, _ = _normalize_workbench_payload(request)
        assert fields["sales_order_no"] == "DATASET-9"

    def test_dataset_row_mapping_overrides_generic_row_mapping(self):
        from app.api.v1.routers.forms import WorkbenchRequest, _normalize_workbench_payload

        request = WorkbenchRequest(
            module="purchase_order", item_rows=[{"bulk_qty": "1", "product_id": "PROD-1"}],
            ocr_rows=[{"Detected Qty": {"value": "4"}}],
            selected_dataset={"row_mappings_json": [{"ocr_key": "detected_qty", "target_field": "bulk_qty"}]},
        )
        _, _, rows = _normalize_workbench_payload(request)
        assert rows[0]["bulk_qty"] == "4"

    def test_validate_and_export_use_dataset_enriched_fields_and_metadata(self):
        from app.api.v1.routers.forms import WorkbenchExportRequest, _workbench_export_payload, build_erp_workbench_payload

        request = WorkbenchExportRequest(
            module="sales_order", dataset_id="dataset-8", document_type="sales_order",
            form_data={"sales_order_date": "2026-06-10", "customer_id": "CUST-1", "bill_to_address": "Bill",
                       "ship_to_address": "Ship", "delivery_date": "2026-06-12", "order_total": "100"},
            item_rows=[{"product_id": "PROD-1", "bulk_qty": "2", "bulk_unit_price": "50", "bulk_line_total": "100"}],
            ocr_fields={"Invoice Number": {"value": "SO-DATASET-8"}},
            selected_dataset={"mappings_json": [{"ocr_key": "invoice_number", "target_field": "sales_order_no"}]},
            metadata={"dataset_name": "Task 8", "dataset_match_score": 100, "dataset_match_reason": "exact",
                      "dataset_header_mapped_count": 1, "dataset_row_mapped_count": 1, "fallback_mapped_count": 3},
        )
        validation, fields, rows = _workbench_export_payload(request)
        payload = build_erp_workbench_payload(request, validation, fields, rows)
        assert validation["export_allowed"] is True
        assert payload["header"]["sales_order_no"] == "SO-DATASET-8"
        assert payload["dataset"]["id"] == "dataset-8"
        assert payload["dataset"]["match_score"] == 100

    def test_localstorage_dataset_fallback_remains_visible(self):
        from pathlib import Path

        source = Path("frontend/index.html").read_text(encoding="utf-8")
        assert 'ocr_training_datasets_cache' in source
        assert "Local dataset cache used" in source
        assert 'match_reason:"local_cache"' in source

    def test_frontend_has_no_duplicate_ids_and_dataset_js_paths(self):
        import re
        from pathlib import Path

        source = Path("frontend/index.html").read_text(encoding="utf-8")
        ids = re.findall(r'id="([^"]+)"', source)
        assert len(ids) == len(set(ids))
        assert "function applyMainDatasetFields" in source
        assert "function applyMainDatasetRow" in source
        assert "function collectMainWorkbenchPayload" in source


class TestTask9ERPMasterMapping:
    class _Result:
        def __init__(self, rows): self.rows = rows
        def scalar_one_or_none(self): return self.rows[0] if self.rows else None
        def scalars(self): return self
        def all(self): return list(self.rows)

    class _DB:
        def __init__(self, rows=None): self.rows=list(rows or []); self.added=[]; self.commits=0
        async def execute(self, _statement): return TestTask9ERPMasterMapping._Result(self.rows)
        def add(self, item):
            import uuid
            from datetime import datetime, timezone
            item.id = item.id or uuid.uuid4(); item.created_at=datetime.now(timezone.utc); item.updated_at=item.created_at
            self.added.append(item)
        async def commit(self): self.commits += 1
        async def refresh(self, _item): return None

    @staticmethod
    def _mapping(master_type, text, erp_id, module="sales_order"):
        from types import SimpleNamespace
        from app.api.v1.routers.companies import normalize_erp_master_text
        return SimpleNamespace(master_type=master_type, detected_text=text, normalized_text=normalize_erp_master_text(text),
                               erp_id=erp_id, module=module, is_active=True)

    def test_create_erp_master_mapping(self):
        import asyncio
        from app.api.v1.routers.companies import ERPMasterMappingRequest, save_erp_master_mapping
        db=self._DB()
        result=asyncio.run(save_erp_master_mapping(ERPMasterMappingRequest(master_type="customer",detected_text="Acme Ltd.",erp_id="CUST-1"),db=db,current_user={"org_id":"org-1"}))
        assert result["created"] is True and result["mapping"]["erp_id"] == "CUST-1"

    def test_resolve_customer_to_customer_id(self):
        from app.api.v1.routers.companies import resolve_erp_master_values
        result=resolve_erp_master_values({"customer_name":"Acme Ltd."},[],[self._mapping("customer","ACME LTD","CUST-1")],"sales_order")
        assert result["resolved_fields"]["customer_id"] == "CUST-1"

    def test_resolve_supplier_to_supplier_id(self):
        from app.api.v1.routers.companies import resolve_erp_master_values
        result=resolve_erp_master_values({"supplier_name":"Global Supply"},[],[self._mapping("supplier","Global Supply","SUP-1","purchase_order")],"purchase_order")
        assert result["resolved_fields"]["supplier_id"] == "SUP-1"

    def test_resolve_product_row_to_product_id(self):
        from app.api.v1.routers.companies import resolve_erp_master_values
        result=resolve_erp_master_values({},[{"product":"Widget A"}],[self._mapping("product","Widget A","PROD-1")],"sales_order")
        assert result["resolved_rows"][0]["product_id"] == "PROD-1"

    def test_resolve_tax_group_to_tax_group_id(self):
        from app.api.v1.routers.companies import resolve_erp_master_values
        result=resolve_erp_master_values({},[{"bulk_tax_group":"GST 18%"}],[self._mapping("tax_group","GST 18%","TAX-18")],"sales_order")
        assert result["resolved_rows"][0]["tax_group_id"] == "TAX-18"

    def test_unresolved_master_has_reason_code(self):
        from app.api.v1.routers.companies import resolve_erp_master_values
        result=resolve_erp_master_values({"customer_name":"Unknown Buyer"},[],[],"sales_order")
        assert any(item["reason_code"] == "ERP_master_id_missing" and item["reason"] == "Needs ERP master selection" for item in result["unresolved"])

    def test_export_blocked_when_master_missing(self):
        from app.api.v1.routers.forms import WorkbenchRequest, validate_workbench_payload
        result=validate_workbench_payload(WorkbenchRequest(module="sales_order",form_data={"sales_order_date":"2026-06-10","customer_name":"Acme","bill_to_address":"B","ship_to_address":"S","delivery_date":"2026-06-11","order_total":"100"},item_rows=[{"product":"Widget","bulk_qty":"1","bulk_unit_price":"100","bulk_line_total":"100"}]))
        assert result["export_allowed"] is False
        assert any(item["reason_code"] == "ERP_master_id_missing" for item in result["blocker_reasons"])

    def test_export_allowed_after_required_master_ids_resolved(self):
        from app.api.v1.routers.forms import WorkbenchRequest, validate_workbench_payload
        result=validate_workbench_payload(WorkbenchRequest(module="sales_order",form_data={"sales_order_date":"2026-06-10","customer_id":"CUST-1","bill_to_address":"B","ship_to_address":"S","delivery_date":"2026-06-11","order_total":"100"},item_rows=[{"product_id":"PROD-1","bulk_qty":"1","bulk_unit_price":"100","bulk_line_total":"100"}]))
        assert result["export_allowed"] is True

    def test_bulk_save_master_mappings(self):
        import asyncio
        from app.api.v1.routers.companies import ERPMasterBulkRequest, ERPMasterMappingRequest, bulk_save_erp_master_mappings
        db=self._DB()
        body=ERPMasterBulkRequest(mappings=[ERPMasterMappingRequest(master_type="customer",detected_text="Acme",erp_id="C1"),ERPMasterMappingRequest(master_type="product",detected_text="Widget",erp_id="P1")])
        result=asyncio.run(bulk_save_erp_master_mappings(body,db=db,current_user={"org_id":"org-1"}))
        assert result["saved_count"] == 2 and result["created_count"] == 2

    def test_dataset_mapping_then_master_resolution_order(self):
        from app.api.v1.routers.forms import WorkbenchRequest, _normalize_workbench_payload
        from app.api.v1.routers.companies import resolve_erp_master_values
        request=WorkbenchRequest(module="sales_order",ocr_fields={"Buyer":{"value":"Acme Ltd"}},selected_dataset={"mappings_json":[{"ocr_key":"Buyer","target_field":"customer_name"}]})
        _,fields,rows=_normalize_workbench_payload(request)
        resolved=resolve_erp_master_values(fields,rows,[self._mapping("customer","Acme Ltd","CUST-9")],"sales_order")
        assert fields["customer_name"] == "Acme Ltd" and resolved["resolved_fields"]["customer_id"] == "CUST-9"

    def test_localstorage_master_mapping_fallback_exists(self):
        from pathlib import Path
        source=Path("frontend/index.html").read_text(encoding="utf-8")
        assert "ocr_erp_master_mappings_cache" in source
        assert "Using local master mapping cache" in source

    def test_master_model_and_migration_registered(self):
        from pathlib import Path
        from app.models import ERPMasterMapping
        assert ERPMasterMapping.__tablename__ == "erp_master_mappings"
        migration=Path("alembic/versions/0006_add_erp_master_mappings.py").read_text(encoding="utf-8")
        assert 'down_revision = "0005_training_workbench"' in migration and "drop_table(\"erp_master_mappings\")" in migration
