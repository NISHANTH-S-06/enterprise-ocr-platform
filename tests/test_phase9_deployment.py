import os
from pathlib import Path
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock, patch

from app.api.v1.routers.health import router
from app.core.database import get_db

# standalone testing app to isolate router under test
app = FastAPI()
app.include_router(router)

@pytest.fixture
def mock_db():
    db = MagicMock()
    db.execute = AsyncMock()
    return db

@pytest.fixture
def client(mock_db):
    app.dependency_overrides[get_db] = lambda: mock_db
    yield TestClient(app)
    app.dependency_overrides = {}


def test_health_liveness_get_and_head(client):
    """Verify GET and HEAD methods on liveness probe /health."""
    # Test GET
    response_get = client.get("/health")
    assert response_get.status_code == 200
    data = response_get.json()
    assert data["status"] == "ok"
    assert "app" in data
    assert "environment" in data
    assert len(response_get.content) > 0

    # Test HEAD
    response_head = client.head("/health")
    assert response_head.status_code == 200
    # HEAD must have NO body content
    assert len(response_head.content) == 0


def test_health_readiness_get_and_head(client, mock_db):
    """Verify GET and HEAD methods on readiness probe."""
    async def mock_ocr_status():
        return {"engine": "paddleocr", "mock": False, "status": "ok"}

    with patch("app.api.v1.routers.health._ocr_engine_status", mock_ocr_status):
        # Test GET
        response_get = client.get("/ready")
        assert response_get.status_code == 200
        data = response_get.json()
        assert data["status"] == "ok"
        assert data["checks"]["database"] == "ok"
        assert data["checks"]["ocr_engine"] == "paddleocr"
        assert data["checks"]["ocr_mock"] == "False"
        assert len(response_get.content) > 0

        # Test HEAD
        response_head = client.head("/ready")
        assert response_head.status_code == 200
        assert len(response_head.content) == 0

        # Test duplicate alias path /health/ready
        response_alias_get = client.get("/health/ready")
        assert response_alias_get.status_code == 200
        response_alias_head = client.head("/health/ready")
        assert response_alias_head.status_code == 200
        assert len(response_alias_head.content) == 0


def test_required_deployment_scripts_exist():
    """Verify that all required Phase 9 PowerShell scripts exist in scripts/ directory."""
    project_root = Path(__file__).resolve().parent.parent
    scripts_dir = project_root / "scripts"
    
    required_scripts = [
        "start_backend.ps1",
        "start_frontend.ps1",
        "stop_local_servers.ps1",
        "check_health.ps1",
        "run_verification.ps1"
    ]
    
    for script_name in required_scripts:
        script_path = scripts_dir / script_name
        assert script_path.exists(), f"Required deployment script missing: {script_name}"
        assert script_path.is_file()


def test_runbook_documentation_exists_and_safe():
    """Verify that the OFFICE_RUNBOOK.md document exists and is free of credentials/secrets."""
    project_root = Path(__file__).resolve().parent.parent
    runbook_path = project_root / "docs" / "OFFICE_RUNBOOK.md"
    
    assert runbook_path.exists(), "OFFICE_RUNBOOK.md documentation is missing"
    assert runbook_path.is_file()
    
    content = runbook_path.read_text(encoding="utf-8")
    
    # Assert no sensitive patterns
    sensitive_keywords = [
        "password =", "secret_key =", "aws_secret", "private_key",
        "postgres://", "postgresql://"
    ]
    
    for kw in sensitive_keywords:
        assert kw not in content.lower(), f"Potential credential or connection string leaked: '{kw}'"
