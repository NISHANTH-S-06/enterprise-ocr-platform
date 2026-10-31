import pytest
import uuid
import csv
import io
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from fastapi import FastAPI, HTTPException, UploadFile, status
from fastapi.testclient import TestClient

from app.api.v1.routers.upload import router
from app.core.database import get_db
from app.core.security import get_current_user
from app.api.v1.routers.upload_helpers import OCR_DEMO_JOBS
from app.models.enums import UserRole, BatchStatus, JobStatus, ExtractionStatus
from app.models.batch import OCRBatch
from app.models.file import UploadedFile
from app.models.job import OCRJob
from app.models.extracted_data import ExtractedData

# Initialize FastAPI app for the upload/batch router
app = FastAPI()
app.include_router(router)

@pytest.fixture
def mock_user():
    return {"user_id": str(uuid.uuid4()), "role": "operator", "org_id": None}

@pytest.fixture
def mock_db():
    db = MagicMock()
    db.execute = AsyncMock()
    db.get = AsyncMock()
    db.commit = AsyncMock()
    db.flush = AsyncMock()
    db.rollback = AsyncMock()
    db.added = []
    db.add.side_effect = db.added.append
    return db

@pytest.fixture
def client(mock_db, mock_user):
    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[get_current_user] = lambda: mock_user
    yield TestClient(app)
    app.dependency_overrides = {}


class TestPhase7ArchiveSearch:

    # 1. Batch Upload Resilience (one invalid file shouldn't fail batch)
    def test_batch_upload_resilience(self, client, mock_db, monkeypatch):
        # We need to mock _tenant_limit_source and check_batch_limits_for_user
        async def mock_tenant_limit_source(*args, **kwargs):
            return {}
        
        monkeypatch.setattr(
            "app.api.v1.routers.upload._tenant_limit_source",
            mock_tenant_limit_source
        )
        monkeypatch.setattr(
            "app.api.v1.routers.upload_batch.check_batch_limits_for_user",
            lambda *args: None
        )

        # Mock prepared files: file1 succeeds, file2 raises HTTPException
        file1_prepared = {
            "data": b"dummy content",
            "safe_name": "file1.pdf",
            "detected": "application/pdf",
            "content_type": "application/pdf",
        }
        
        store_info = {
            "checksum": "abc123sha",
            "stored": "file1_stored.pdf",
            "dest": "path/to/file1_stored.pdf",
            "upload_save_ms": 10
        }

        async def mock_prep(upload_file):
            if upload_file.filename == "file2.pdf":
                raise HTTPException(status_code=400, detail="Invalid mime type")
            return file1_prepared

        async def mock_store(*args):
            return store_info

        monkeypatch.setattr(
            "app.api.v1.routers.upload._prepare_upload_file",
            mock_prep
        )
        monkeypatch.setattr(
            "app.api.v1.routers.upload._store_prepared_upload",
            mock_store
        )

        # Mock audit log
        async def mock_audit(*args, **kwargs):
            pass
        monkeypatch.setattr(
            "app.api.v1.routers.upload.write_audit_log",
            mock_audit
        )

        # Background tasks mock
        monkeypatch.setattr(
            "app.api.v1.routers.upload._ocr_background",
            lambda *args, **kwargs: None
        )

        files = [
            ("files", ("file1.pdf", b"content1", "application/pdf")),
            ("files", ("file2.pdf", b"content2", "application/pdf")),
        ]

        resp = client.post("/upload/batches", files=files)
        assert resp.status_code == 202
        data = resp.json()
        
        assert data["file_count"] == 2
        assert data["accepted"] == 1
        assert data["rejected"] == 1
        assert len(data["errors"]) == 1
        assert data["errors"][0]["filename"] == "file2.pdf"
        assert "Invalid mime type" in data["errors"][0]["error"]
        assert len(data["files"]) == 1
        assert data["files"][0]["filename"] == "file1.pdf"

    # 2. Batch status counters accuracy
    def test_batch_status_counters(self, client, mock_db, monkeypatch):
        batch_id = uuid.uuid4()
        mock_batch = OCRBatch(
            id=batch_id,
            created_by_id=uuid.uuid4(),
            org_id=None,
            status=BatchStatus.PROCESSING.value,
            file_count=3,
            total_bytes=1000,
            started_at=datetime.now(timezone.utc),
            completed_count=1,
            failed_count=1,
            needs_review_count=0,
            validated_count=0,
            total_pages=0,
        )

        # Mock batch child rows: returns files, jobs, extractions
        file_id_1 = uuid.uuid4()
        file_id_2 = uuid.uuid4()
        file_id_3 = uuid.uuid4()
        job_id_1 = uuid.uuid4()
        job_id_2 = uuid.uuid4()
        job_id_3 = uuid.uuid4()

        mock_files = [
            UploadedFile(id=file_id_1, original_filename="doc1.pdf", mime_type="application/pdf", file_size=100),
            UploadedFile(id=file_id_2, original_filename="doc2.pdf", mime_type="application/pdf", file_size=200),
            UploadedFile(id=file_id_3, original_filename="doc3.pdf", mime_type="application/pdf", file_size=300),
        ]

        mock_jobs = {
            file_id_1: OCRJob(id=job_id_1, status=JobStatus.COMPLETED.value, error_message=None),
            file_id_2: OCRJob(id=job_id_2, status=JobStatus.FAILED.value, error_message="C:\\private\\file.env failed"),
            file_id_3: OCRJob(id=job_id_3, status=JobStatus.PROCESSING.value, error_message=None),
        }

        mock_extractions = {
            job_id_1: ExtractedData(status=ExtractionStatus.VALIDATED.value, validation_errors=[])
        }

        # Setup OCR_DEMO_JOBS values for these jobs to test counter metrics
        OCR_DEMO_JOBS[str(job_id_1)] = {
            "job_id": str(job_id_1),
            "status": "completed",
            "export_allowed": True,
            "duplicate_detection": {"status": "not_duplicate", "blocking": False},
            "erp_intelligence": {"blocking": False},
        }
        OCR_DEMO_JOBS[str(job_id_2)] = {
            "job_id": str(job_id_2),
            "status": "failed",
            "error_message": "C:\\private\\file.env failed",
        }
        OCR_DEMO_JOBS[str(job_id_3)] = {
            "job_id": str(job_id_3),
            "status": "processing",
        }

        # Mock get_owned_ocr_batch helper
        async def mock_get_batch(*args):
            return mock_batch
        
        async def mock_child_rows(*args):
            return mock_files, mock_jobs, mock_extractions

        monkeypatch.setattr(
            "app.api.v1.routers.upload.get_owned_ocr_batch",
            mock_get_batch
        )
        monkeypatch.setattr(
            "app.api.v1.routers.upload._batch_child_rows",
            mock_child_rows
        )

        resp = client.get(f"/upload/batches/{batch_id}")
        assert resp.status_code == 200
        data = resp.json()

        assert data["batch_id"] == str(batch_id)
        assert data["queued_count"] == 0
        assert data["processing_count"] == 1
        assert data["completed_count"] == 1
        assert data["failed_count"] == 1
        assert data["export_ready_count"] == 1
        assert len(data["jobs"]) == 3

        # Verify error message path redaction
        failed_job = next(j for j in data["jobs"] if j["job_id"] == str(job_id_2))
        assert "private" not in failed_job["error_message"]
        assert "file.env" not in failed_job["error_message"]
        assert failed_job["error_message"] == "An internal resource error occurred"

    # 3. Document Archive fields and filters
    def test_document_archive_filtering(self, client, mock_db, monkeypatch):
        # We query the DB and fetch memory jobs
        # Mock DB execute returning empty list so we focus on memory jobs filter test
        mock_res = MagicMock()
        mock_res.scalars.return_value.all.return_value = []
        mock_db.execute.return_value = mock_res

        # Set up a set of diverse memory jobs
        job_id_1 = str(uuid.uuid4())
        job_id_2 = str(uuid.uuid4())
        job_id_3 = str(uuid.uuid4())

        OCR_DEMO_JOBS.clear()
        
        OCR_DEMO_JOBS[job_id_1] = {
            "job_id": job_id_1,
            "filename": "invoice_nike.pdf",
            "uploaded_at": "2026-06-20T10:00:00Z",
            "created_at": "2026-06-20T10:00:00Z",
            "status": "completed",
            "document_type": "invoice",
            "owner_user_id": client.app.dependency_overrides[get_current_user]()["user_id"],
            "fields": {
                "vendor_name": "Nike Corp",
                "customer_name": "Retailer Inc",
                "invoice_number": "NK-888",
                "total_amount": "5000.50",
            },
            "duplicate_detection": {"status": "not_duplicate", "blocking": False},
            "export_allowed": True,
        }

        OCR_DEMO_JOBS[job_id_2] = {
            "job_id": job_id_2,
            "filename": "po_adidas.pdf",
            "uploaded_at": "2026-06-21T11:00:00Z",
            "created_at": "2026-06-21T11:00:00Z",
            "status": "completed",
            "document_type": "purchase_order",
            "owner_user_id": client.app.dependency_overrides[get_current_user]()["user_id"],
            "fields": {
                "vendor_name": "Adidas AG",
                "customer_name": "Sports Outlet",
                "invoice_number": "AD-999",
                "total_amount": "1200.00",
            },
            "duplicate_detection": {"status": "duplicate_exact", "blocking": True},
            "export_allowed": False,
        }

        OCR_DEMO_JOBS[job_id_3] = {
            "job_id": job_id_3,
            "filename": "grn_puma.pdf",
            "uploaded_at": "2026-06-22T12:00:00Z",
            "created_at": "2026-06-22T12:00:00Z",
            "status": "processing",
            "document_type": "grn",
            "owner_user_id": client.app.dependency_overrides[get_current_user]()["user_id"],
            "fields": {},
            "duplicate_detection": {"status": "not_duplicate", "blocking": False},
            "export_allowed": False,
        }

        # Test no filter: returns all 3 sorted desc by created_at (job 3, job 2, job 1)
        resp = client.get("/upload/archive")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        assert data["documents"][0]["job_id"] == job_id_3
        assert data["documents"][1]["job_id"] == job_id_2
        assert data["documents"][2]["job_id"] == job_id_1
        
        # Verify no raw OCR text is in the response fields
        for doc in data["documents"]:
            assert "raw_text" not in doc
            assert "text" not in doc
            assert "extracted_text" not in doc
            assert "ocr_response" not in doc

        # Test search query filter
        resp = client.get("/upload/archive?query=adidas")
        assert resp.json()["total"] == 1
        assert resp.json()["documents"][0]["job_id"] == job_id_2

        # Test vendor_name filter
        resp = client.get("/upload/archive?vendor_name=Nike")
        assert resp.json()["total"] == 1
        assert resp.json()["documents"][0]["supplier_name"] == "Nike Corp"

        # Test duplicate_status filter
        resp = client.get("/upload/archive?duplicate_status=duplicate_exact")
        assert resp.json()["total"] == 1
        assert resp.json()["documents"][0]["job_id"] == job_id_2

        # Test amount range filter
        resp = client.get("/upload/archive?min_amount=2000&max_amount=6000")
        assert resp.json()["total"] == 1
        assert resp.json()["documents"][0]["supplier_name"] == "Nike Corp"

        # Test date range filter
        resp = client.get("/upload/archive?start_date=2026-06-21&end_date=2026-06-22")
        assert resp.json()["total"] == 2
        assert {doc["job_id"] for doc in resp.json()["documents"]} == {job_id_2, job_id_3}

    # 4. Bulk JSON/CSV summary export
    def test_selected_summary_export(self, client, mock_db, monkeypatch):
        # We need mock DB returning the query results for the selected job IDs
        job_id_1 = uuid.uuid4()
        job_id_2 = uuid.uuid4()
        
        file1 = UploadedFile(id=uuid.uuid4(), original_filename="nike.pdf", document_type="invoice")
        file2 = UploadedFile(id=uuid.uuid4(), original_filename="adidas.pdf", document_type="purchase_order")

        job1 = OCRJob(
            id=job_id_1,
            file=file1,
            created_at=datetime(2026, 6, 20, 10, 0, 0, tzinfo=timezone.utc),
            status="completed",
            ocr_config={
                "duplicate_detection": {"status": "not_duplicate", "blocking": False},
                "erp_intelligence": {"blocking": False}
            }
        )
        job2 = OCRJob(
            id=job_id_2,
            file=file2,
            created_at=datetime(2026, 6, 21, 11, 0, 0, tzinfo=timezone.utc),
            status="completed",
            ocr_config={
                "duplicate_detection": {"status": "duplicate_exact", "blocking": True},
                "erp_intelligence": {"blocking": False}
            }
        )

        extracted_data_1 = ExtractedData(
            id=uuid.uuid4(),
            job_id=job_id_1,
            validated_fields={
                "vendor_name": "Nike Corp",
                "customer_name": "Retailer Inc",
                "invoice_number": "NK-888",
                "total_amount": "5000.50"
            },
            status=ExtractionStatus.VALIDATED.value,
            validation_errors=[]
        )
        extracted_data_2 = ExtractedData(
            id=uuid.uuid4(),
            job_id=job_id_2,
            validated_fields={
                "vendor_name": "Adidas AG",
                "customer_name": "Sports Outlet",
                "invoice_number": "AD-999",
                "total_amount": "1200.00"
            },
            status=ExtractionStatus.NEEDS_REVIEW.value,
            validation_errors=[]
        )

        job1.extracted_data = extracted_data_1
        job2.extracted_data = extracted_data_2

        mock_res = MagicMock()
        mock_res.scalars.return_value.all.return_value = [job1, job2]
        mock_db.execute.return_value = mock_res

        # Test JSON format export
        payload = {"job_ids": [str(job_id_1), str(job_id_2)], "format": "json"}
        resp = client.post("/upload/export/selected", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert data["documents"][0]["job_id"] == str(job_id_1)
        assert data["documents"][1]["job_id"] == str(job_id_2)
        assert data["documents"][0]["supplier_name"] == "Nike Corp"
        assert data["documents"][0]["export_ready"] is True
        assert data["documents"][1]["export_ready"] is False # Duplicate blocking

        # Test CSV format export
        payload["format"] = "csv"
        resp = client.post("/upload/export/selected", json=payload)
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "text/csv; charset=utf-8"
        assert "attachment; filename=" in resp.headers["content-disposition"]
        
        # Parse CSV output
        csv_text = resp.text
        f = io.StringIO(csv_text)
        reader = list(csv.reader(f))
        
        # Assert header and row count
        assert len(reader) == 3 # Header + 2 rows
        assert reader[0][0] == "Job ID"
        assert reader[0][1] == "Document Name"
        
        # Row 1 check
        assert reader[1][0] == str(job_id_1)
        assert reader[1][1] == "nike.pdf"
        assert reader[1][5] == "Nike Corp" # Supplier Name
        assert reader[1][9] == "5000.5" # Total Amount
        assert reader[1][14] == "true" # Export Ready

        # Row 2 check
        assert reader[2][0] == str(job_id_2)
        assert reader[2][1] == "adidas.pdf"
        assert reader[2][5] == "Adidas AG"
        assert reader[2][9] == "1200.0"
        assert reader[2][14] == "false" # Duplicate blocks export

    # 5. Reopen job result payload structure
    def test_reopen_job_result_payload(self, client, mock_db, monkeypatch):
        # Verify get_upload_job returns full details for a completed job,
        # including source traces, erp_intelligence, and duplicate_detection
        job_id = str(uuid.uuid4())
        OCR_DEMO_JOBS.clear()
        
        OCR_DEMO_JOBS[job_id] = {
            "job_id": job_id,
            "status": "completed",
            "owner_user_id": client.app.dependency_overrides[get_current_user]()["user_id"],
            "filename": "nike.pdf",
            "uploaded_at": "2026-06-20T10:00:00Z",
            "created_at": "2026-06-20T10:00:00Z",
            "document_type": "invoice",
            "raw_text": "Nike Corp Invoice 888 Total $5000.50",
            "raw_text_preview": "Nike Corp Invoice 888 Total $5000.50",
            "progress_percent": 100,
            "current_step": "completed",
            "canonical_fields": {
                "vendor_name": {"value": "Nike Corp", "confidence": 0.95},
                "total_amount": {"value": "5000.50", "confidence": 0.96},
            },
            "source_traces": {
                "vendor_name": {"review_status": "auto_approved", "validation_status": "valid"}
            },
            "duplicate_detection": {
                "status": "not_duplicate",
                "confidence": 0.0,
                "matched_job_id": None,
                "reasons": []
            },
            "erp_intelligence": {
                "master_match": {"status": "match_exact", "selected_master_name": "Nike Corp"},
                "tax_validation": {"status": "pass"}
            },
            "export_allowed": True,
        }

        resp = client.get(f"/upload/jobs/{job_id}")
        assert resp.status_code == 200
        data = resp.json()

        assert data["job_id"] == job_id
        assert data["status"] == "completed"
        assert data["filename"] == "nike.pdf"
        assert "source_traces" in data
        assert "duplicate_detection" in data
        assert "erp_intelligence" in data
        assert "canonical_fields" in data
        assert data["export_allowed"] is True
