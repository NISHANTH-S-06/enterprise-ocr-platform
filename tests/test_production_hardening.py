import asyncio
import os
import time
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import ORJSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1.routers.upload_helpers import (
    _validate_file_content,
    _prepare_upload_file,
    cleanup_ocr_debug_files,
)
from app.api.v1.routers.health import readiness
from app.main import create_app


def test_empty_upload_rejection():
    """Verify that an empty file upload is rejected with a 400 error."""
    mock_file = MagicMock()
    mock_file.filename = "empty.pdf"
    mock_file.content_type = "application/pdf"
    
    async def mock_read(*args, **kwargs):
        return b""
        
    mock_file.read = mock_read
    
    with pytest.raises(HTTPException) as exc:
        asyncio.run(_prepare_upload_file(mock_file))
        
    assert exc.value.status_code == 400
    assert "Empty file" in exc.value.detail


def test_password_protected_pdf_rejection():
    """Verify that a password-protected/encrypted PDF is rejected early with a friendly error."""
    fake_pdf_data = b"%PDF-1.4 mock encrypted pdf data"
    
    # Mock PyMuPDF doc structure
    mock_doc = MagicMock()
    mock_doc.is_encrypted = True
    mock_doc.page_count = 5
    
    with patch("fitz.open", return_value=mock_doc):
        with pytest.raises(HTTPException) as exc:
            _validate_file_content(fake_pdf_data, "application/pdf")
            
        assert exc.value.status_code == 400
        assert "Password-protected PDF is not allowed" in exc.value.detail


@pytest.mark.asyncio
async def test_ready_probe_reports_degraded_reason():
    """Verify that ready health probe reports a detailed degraded_reason when degraded."""
    mock_db = MagicMock(spec=AsyncSessionLocal if "AsyncSessionLocal" in globals() else object)
    mock_db.execute = AsyncMock(side_effect=Exception("Database connection timeout"))
    
    # Mock OCR engine status to return mock engine (which triggers degraded status)
    async def mock_ocr_status():
        return {"engine": "mock", "mock": True, "status": "warn"}
        
    with patch("app.api.v1.routers.health._ocr_engine_status", mock_ocr_status):
        response = await readiness(db=mock_db)
        
        assert response.status == "degraded"
        assert response.degraded_reason is not None
        assert "Database query failed" in response.degraded_reason or "OCR engine is mock" in response.degraded_reason


@pytest.mark.asyncio
async def test_standardized_error_response_shape():
    """Verify that the global HTTPException handler returns the standard shape."""
    app = create_app()
    
    # Locate the registered handler
    handler = app.exception_handlers.get(StarletteHTTPException)
    assert handler is not None
    
    mock_request = MagicMock(spec=Request)
    mock_request.url = MagicMock()
    mock_request.url.path = "/api/v1/upload/jobs/12345678-1234-1234-1234-123456789012"
    mock_request.path_params = {"job_id": "12345678-1234-1234-1234-123456789012"}
    
    exc = StarletteHTTPException(
        status_code=400,
        detail={"message": "Verification failed", "errors": ["Invalid total amount"]}
    )
    
    response = await handler(mock_request, exc)
    assert isinstance(response, ORJSONResponse)
    assert response.status_code == 400
    
    import json
    data = json.loads(response.body)
    assert data["error_code"] == "ERR_400"
    assert data["message"] == "Verification failed"
    assert data["details"] == ["Invalid total amount"]
    assert data["job_id"] == "12345678-1234-1234-1234-123456789012"
    assert data["retryable"] is False


def test_temp_ocr_debug_cleanup_helper(tmp_path):
    """Verify that the temporary debug cleanup helper deletes old files and keeps new ones."""
    debug_dir = Path("output") / "ocr_debug"
    debug_dir.mkdir(parents=True, exist_ok=True)
    
    # Create two temporary files
    old_file = debug_dir / "old_test_image.png"
    new_file = debug_dir / "new_test_image.png"
    
    old_file.write_text("old image contents")
    new_file.write_text("new image contents")
    
    # Modify mtime of old file to be 2 hours ago
    two_hours_ago = time.time() - 7200
    os.utime(old_file, (two_hours_ago, two_hours_ago))
    
    # Run cleanup with 1 hour threshold (3600 seconds)
    summary = cleanup_ocr_debug_files(max_age_seconds=3600)
    
    assert summary["deleted_count"] >= 1
    assert not old_file.exists()
    assert new_file.exists()
    
    # Cleanup new file
    if new_file.exists():
        new_file.unlink()
