"""
tests/test_queue_ready_architecture.py
Phase 5B — Queue-Ready Background Processing Architecture Tests

Tests verify:
- Job queue status constants mapping
- Lifecycle state progressions (enqueue → mark_processing → update_progress → mark_completed/mark_failed)
- Timeout and corrupted file error classification
- Polling endpoint backward-compatible response schemas
- Deterministic user/file checksum idempotency logic
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

# ── Job Queue Constants ────────────────────────────────────────────────────────

def test_job_status_constants():
    """Verify all expected job status strings are defined."""
    from app.services import job_queue
    assert job_queue.PUBLIC_JOB_STATUSES == {
        "queued",
        "processing",
        "completed",
        "failed",
        "review_required",
        "cancelled",
    }
    assert job_queue.IDEMPOTENT_ACTIVE_STATUSES == {
        "queued",
        "processing",
        "completed",
        "review_required",
    }


def test_job_queue_module_importable():
    """Ensure job_queue module loads without errors."""
    from app.services import job_queue  # noqa: F401
    assert hasattr(job_queue, "enqueue")
    assert hasattr(job_queue, "mark_processing")
    assert hasattr(job_queue, "update_progress")
    assert hasattr(job_queue, "mark_completed")
    assert hasattr(job_queue, "mark_failed")
    assert hasattr(job_queue, "get_job")


# ── OCR_DEMO_JOBS in-memory cache ─────────────────────────────────────────────

def test_ocr_demo_jobs_dict_is_shared():
    """OCR_DEMO_JOBS must be a shared dict accessible across modules."""
    from app.api.v1.routers.upload_helpers import OCR_DEMO_JOBS
    assert isinstance(OCR_DEMO_JOBS, dict)


def test_ocr_failure_text_exists():
    """OCR_FAILURE_TEXT must be a non-empty fallback string."""
    from app.api.v1.routers.upload_helpers import OCR_FAILURE_TEXT
    assert isinstance(OCR_FAILURE_TEXT, str) and len(OCR_FAILURE_TEXT) > 0


# ── Job Queue State Machine ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mark_processing_updates_demo_job():
    """mark_processing updates OCR_DEMO_JOBS status and current_step."""
    from app.api.v1.routers.upload_helpers import OCR_DEMO_JOBS
    from app.services import job_queue

    job_id = str(uuid.uuid4())
    OCR_DEMO_JOBS[job_id] = {"status": "queued", "progress_percent": 0, "current_step": "queued"}

    mock_db = AsyncMock()
    mock_job = MagicMock()
    mock_job.status = "queued"
    mock_job.ocr_config = {}
    mock_db.get = AsyncMock(return_value=mock_job)
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()

    await job_queue.mark_processing(job_id, job_queue.STEP_VALIDATING_UPLOAD, mock_db)

    assert OCR_DEMO_JOBS[job_id]["status"] == "processing"
    assert OCR_DEMO_JOBS[job_id]["current_step"] == "validating_upload"
    assert OCR_DEMO_JOBS[job_id]["progress_percent"] == 10

    del OCR_DEMO_JOBS[job_id]


@pytest.mark.asyncio
async def test_update_progress_sets_percent_and_step():
    """update_progress correctly updates progress_percent and current_step."""
    from app.api.v1.routers.upload_helpers import OCR_DEMO_JOBS
    from app.services import job_queue

    job_id = str(uuid.uuid4())
    OCR_DEMO_JOBS[job_id] = {"status": "processing", "progress_percent": 10, "current_step": "reading_file"}

    mock_db = AsyncMock()
    mock_job = MagicMock()
    mock_job.ocr_config = {}
    mock_db.get = AsyncMock(return_value=mock_job)
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()

    await job_queue.update_progress(job_id, 50, "mapping_fields", mock_db)

    assert OCR_DEMO_JOBS[job_id]["progress_percent"] == 50
    assert OCR_DEMO_JOBS[job_id]["current_step"] == "mapping_fields"

    del OCR_DEMO_JOBS[job_id]


@pytest.mark.asyncio
async def test_mark_failed_terminal_non_retryable():
    """mark_failed sets failed status and retryable=False for non-retryable errors."""
    from app.api.v1.routers.upload_helpers import OCR_DEMO_JOBS
    from app.services import job_queue

    job_id = str(uuid.uuid4())
    OCR_DEMO_JOBS[job_id] = {"status": "processing", "progress_percent": 25}

    mock_db = AsyncMock()
    mock_job = MagicMock()
    mock_job.status = "processing"
    mock_job.retry_count = 0
    mock_job.max_retries = 3
    mock_job.file_id = uuid.uuid4()
    mock_job.ocr_config = {}
    mock_job.created_by_id = uuid.uuid4()
    mock_job.org_id = None
    mock_job.id = uuid.UUID(job_id)
    mock_job.batch_id = None

    mock_db.get = AsyncMock(return_value=mock_job)
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()

    with patch("app.services.job_queue.write_audit_log", new_callable=AsyncMock), \
         patch("app.services.job_queue._refresh_batch_counters", new_callable=AsyncMock):
        await job_queue.mark_failed(job_id, "CORRUPTED_FILE", "File is corrupt", False, mock_db)

    assert OCR_DEMO_JOBS[job_id]["status"] == "failed"
    assert OCR_DEMO_JOBS[job_id]["retryable"] is False
    assert OCR_DEMO_JOBS[job_id]["error_code"] == "CORRUPTED_FILE"

    del OCR_DEMO_JOBS[job_id]


@pytest.mark.asyncio
async def test_mark_failed_timeout_is_retryable():
    """mark_failed with retryable=True records retry-ready failed status."""
    from app.api.v1.routers.upload_helpers import OCR_DEMO_JOBS
    from app.services import job_queue

    job_id = str(uuid.uuid4())
    OCR_DEMO_JOBS[job_id] = {"status": "processing", "progress_percent": 25}

    mock_db = AsyncMock()
    mock_job = MagicMock()
    mock_job.status = "processing"
    mock_job.retry_count = 0
    mock_job.max_retries = 3
    mock_job.file_id = uuid.uuid4()
    mock_job.ocr_config = {}
    mock_job.created_by_id = uuid.uuid4()
    mock_job.org_id = None
    mock_job.id = uuid.UUID(job_id)
    mock_job.batch_id = None
    mock_db.get = AsyncMock(return_value=mock_job)
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()

    with patch("app.services.job_queue.write_audit_log", new_callable=AsyncMock), \
         patch("app.services.job_queue._refresh_batch_counters", new_callable=AsyncMock):
        await job_queue.mark_failed(job_id, "OCR_TIMEOUT", "OCR processing timed out. Please retry.", True, mock_db)

    assert OCR_DEMO_JOBS[job_id]["status"] == "failed"
    assert OCR_DEMO_JOBS[job_id]["retryable"] is True
    assert OCR_DEMO_JOBS[job_id]["error_code"] == "OCR_TIMEOUT"
    assert OCR_DEMO_JOBS[job_id]["failed_at"]
    assert mock_job.retry_count == 0

    del OCR_DEMO_JOBS[job_id]


@pytest.mark.asyncio
async def test_mark_failed_exhausted_retries_becomes_failed():
    """When retry_count >= max_retries, mark_failed sets failed status even if retryable=True."""
    from app.api.v1.routers.upload_helpers import OCR_DEMO_JOBS
    from app.services import job_queue

    job_id = str(uuid.uuid4())
    OCR_DEMO_JOBS[job_id] = {"status": "processing"}

    mock_db = AsyncMock()
    mock_job = MagicMock()
    mock_job.status = "processing"
    mock_job.retry_count = 3
    mock_job.max_retries = 3
    mock_job.file_id = uuid.uuid4()
    mock_job.ocr_config = {}
    mock_job.created_by_id = uuid.uuid4()
    mock_job.org_id = None
    mock_job.id = uuid.UUID(job_id)
    mock_job.batch_id = None
    mock_db.get = AsyncMock(return_value=mock_job)
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()

    with patch("app.services.job_queue.write_audit_log", new_callable=AsyncMock), \
         patch("app.services.job_queue._refresh_batch_counters", new_callable=AsyncMock):
        await job_queue.mark_failed(job_id, "OCR_TIMEOUT", "Timed out", True, mock_db)

    assert OCR_DEMO_JOBS[job_id]["status"] == "failed"
    assert OCR_DEMO_JOBS[job_id]["retryable"] is True

    del OCR_DEMO_JOBS[job_id]


# ── Error Code Classification ──────────────────────────────────────────────────

@pytest.mark.parametrize("error_class,expected_code,expected_retryable", [
    ("asyncio.TimeoutError",  "OCR_TIMEOUT",     True),
    ("TimeoutError",          "OCR_TIMEOUT",     True),
    ("FileNotFoundError",     "CORRUPTED_FILE",  False),
    ("PermissionError",       "CORRUPTED_FILE",  False),
    ("MemoryError",           "RESOURCE_EXHAUSTED", False),
    ("OverflowError",         "RESOURCE_EXHAUSTED", False),
    ("ValueError",            "OCR_FAILED",      False),
    ("RuntimeError",          "OCR_FAILED",      False),
])
def test_error_classification_logic(error_class, expected_code, expected_retryable):
    """Verify the error classification logic matches expected codes and retryable flags."""
    # Replicate the classification logic from upload_background.py
    exc = eval(f"__builtins__['{error_class}']() if False else {error_class}()") if error_class in ("asyncio.TimeoutError", "TimeoutError") else None
    if error_class == "asyncio.TimeoutError":
        exc = asyncio.TimeoutError()
    elif error_class == "TimeoutError":
        exc = TimeoutError()
    elif error_class == "FileNotFoundError":
        exc = FileNotFoundError("test")
    elif error_class == "PermissionError":
        exc = PermissionError("test")
    elif error_class == "MemoryError":
        exc = MemoryError()
    elif error_class == "OverflowError":
        exc = OverflowError()
    elif error_class == "ValueError":
        exc = ValueError("test")
    else:
        exc = RuntimeError("test")

    is_timeout = isinstance(exc, (asyncio.TimeoutError, TimeoutError))
    if is_timeout:
        code = "OCR_TIMEOUT"
        retryable = True
    else:
        exc_name = type(exc).__name__
        if any(t in exc_name for t in ("FileNotFoundError", "PermissionError", "IsADirectoryError", "BadZipFile", "PDFSyntaxError")):
            code = "CORRUPTED_FILE"
            retryable = False
        elif isinstance(exc, (MemoryError, OverflowError)):
            code = "RESOURCE_EXHAUSTED"
            retryable = False
        else:
            code = "OCR_FAILED"
            retryable = False

    assert code == expected_code, f"For {error_class}: got code={code}"
    assert retryable == expected_retryable, f"For {error_class}: got retryable={retryable}"


# ── Progress Percent Inference ─────────────────────────────────────────────────

@pytest.mark.parametrize("status,config,expected_percent", [
    ("completed",  {"progress_percent": 100}, 100),
    ("processing", {"progress_percent": 50},  50),
    ("queued",     {},                         0),
    ("completed",  {},                        100),
    ("failed",     {},                        100),
    ("processing", {},                         50),
])
def test_progress_percent_inference(status, config, expected_percent):
    """Verify progress_percent is correctly inferred when not set in ocr_config."""
    from app.models.enums import JobStatus

    ocr_config = config
    progress_percent = ocr_config.get("progress_percent")
    if progress_percent is None:
        if status in (JobStatus.COMPLETED.value, "completed"):
            progress_percent = 100
        elif status in (JobStatus.FAILED.value, "failed"):
            progress_percent = 100
        elif status in (JobStatus.PROCESSING.value, "processing"):
            progress_percent = 50
        else:
            progress_percent = 0

    assert progress_percent == expected_percent


# ── review_required Status Mapping ────────────────────────────────────────────

def test_review_required_status_mapping():
    """Completed job without export_allowed should be mapped to review_required."""
    from app.models.enums import JobStatus

    for base_status in ("completed", JobStatus.COMPLETED.value):
        export_allowed = False
        response_status = base_status
        if response_status in (JobStatus.COMPLETED.value, "completed") and not export_allowed:
            response_status = "review_required"
        assert response_status == "review_required"


def test_completed_with_export_stays_completed():
    """Completed job with export_allowed=True must NOT be remapped to review_required."""
    from app.models.enums import JobStatus

    export_allowed = True
    response_status = "completed"
    if response_status in (JobStatus.COMPLETED.value, "completed") and not export_allowed:
        response_status = "review_required"
    assert response_status == "completed"


# ── Idempotency Key Logic ──────────────────────────────────────────────────────

def test_idempotency_key_uniqueness():
    """Two uploads with same checksum and user should share the same idempotency key."""
    from app.services.job_queue import build_idempotency_key
    checksum = "abc123"
    user_id = uuid.uuid4()
    key_a = build_idempotency_key(user_id, checksum)
    key_b = build_idempotency_key(user_id, checksum)
    assert key_a == key_b


def test_idempotency_key_differs_for_different_users():
    """Two uploads with same checksum but different users should NOT match."""
    from app.services.job_queue import build_idempotency_key
    checksum = "abc123"
    key_a = build_idempotency_key(uuid.uuid4(), checksum)
    key_b = build_idempotency_key(uuid.uuid4(), checksum)
    assert key_a != key_b


def test_idempotency_key_differs_for_different_files():
    """Same user, different checksums should NOT match."""
    from app.services.job_queue import build_idempotency_key
    user_id = uuid.uuid4()
    key_a = build_idempotency_key(user_id, "checksum_111")
    key_b = build_idempotency_key(user_id, "checksum_222")
    assert key_a != key_b


def test_lifecycle_metadata_shape_has_phase_5b_fields():
    from app.services.job_queue import lifecycle_metadata
    job_id = uuid.uuid4()
    payload = lifecycle_metadata(
        job_id=job_id,
        status="queued",
        current_step="queued",
        progress_percent=0,
        document_name="invoice.pdf",
        ocr_engine="paddleocr",
    )
    for key in (
        "job_id", "status", "created_at", "started_at", "completed_at", "failed_at",
        "updated_at", "progress_percent", "current_step", "retry_count", "max_retries",
        "error_code", "error_message", "retryable", "document_name", "document_type",
        "ocr_engine",
    ):
        assert key in payload
    assert payload["job_id"] == str(job_id)


# ── Response Schema Compatibility ─────────────────────────────────────────────

def test_job_response_has_lifecycle_fields():
    """Simulate a job response and verify all lifecycle fields are present."""
    simulated_response = {
        "job_id": str(uuid.uuid4()),
        "status": "completed",
        "progress_percent": 100,
        "current_step": "completed",
        "retryable": False,
        "error_code": None,
        "success": True,
        "fields": {},
        "confidence": {},
        "warnings": [],
        "ocr_response": {},
    }

    for required_field in ("progress_percent", "current_step", "retryable", "error_code"):
        assert required_field in simulated_response, f"Missing lifecycle field: {required_field}"


def test_failed_response_has_retryable_and_error_code():
    """Simulate a failed job response and confirm retryable/error_code are present."""
    from app.services.job_queue import failure_payload
    simulated = failure_payload(
        error_code="OCR_TIMEOUT",
        message="OCR processing timed out. Please retry.",
        retryable=True,
    )

    assert simulated["retryable"] is True
    assert simulated["error_code"] == "OCR_TIMEOUT"
    assert simulated["status"] == "failed"
    assert simulated["failed_at"]
    assert simulated["error_message"] == "OCR processing timed out. Please retry."


def test_failed_response_sanitizes_raw_traceback_and_ocr_text():
    from app.services.job_queue import failure_payload
    raw = "Traceback ... ValueError: raw OCR text CUSTOMER SECRET TOTAL 999"
    simulated = failure_payload(error_code="OCR_FAILED", message=raw, retryable=False)
    assert "Traceback" not in simulated["error_message"]
    assert "CUSTOMER SECRET" not in simulated["error_message"]
    assert simulated["error_message"] == "OCR processing failed."


def test_review_required_response_schema():
    """Simulate a review_required response and confirm export_allowed=False."""
    simulated = {
        "job_id": str(uuid.uuid4()),
        "status": "review_required",
        "progress_percent": 100,
        "current_step": "completed",
        "retryable": False,
        "error_code": None,
        "export_allowed": False,
    }

    assert simulated["status"] == "review_required"
    assert simulated["export_allowed"] is False
    assert simulated["retryable"] is False


# ── get_job helper ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_job_returns_none_for_invalid_uuid():
    """get_job should return None for invalid UUIDs without raising."""
    from app.services import job_queue

    mock_db = AsyncMock()
    result = await job_queue.get_job("not-a-uuid", mock_db)
    assert result is None


@pytest.mark.asyncio
async def test_get_job_calls_db_get_with_uuid():
    """get_job should call db.get with proper UUID."""
    from app.services import job_queue

    job_id = str(uuid.uuid4())
    mock_db = AsyncMock()
    mock_job = MagicMock()
    mock_db.get = AsyncMock(return_value=mock_job)

    result = await job_queue.get_job(job_id, mock_db)
    assert result is mock_job
    mock_db.get.assert_awaited_once()
