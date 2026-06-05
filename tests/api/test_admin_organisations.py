import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI
from unittest.mock import AsyncMock, MagicMock
from app.api.v1.routers.admin import router
from app.core.database import get_db
from app.models.user import Organisation
from app.models.log import AuditLog
import uuid
from datetime import datetime, timezone
from app.api.v1.routers.admin import require_superadmin

app = FastAPI()
app.include_router(router)

def mock_get_db():
    db = MagicMock()
    db.execute = AsyncMock()
    db.get = AsyncMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    added = []
    db.added = added

    def add(obj):
        added.append(obj)

    async def flush():
        now = datetime.now(timezone.utc)
        for obj in added:
            if isinstance(obj, (Organisation, AuditLog)) and obj.id is None:
                obj.id = uuid.uuid4()
            if isinstance(obj, Organisation):
                if obj.created_at is None:
                    obj.created_at = now
                if obj.updated_at is None:
                    obj.updated_at = now

    db.add.side_effect = add
    db.flush.side_effect = flush
    return db

@pytest.fixture
def superadmin_override():
    def _override():
        return {"user_id": str(uuid.uuid4()), "role": "superadmin"}
    return _override

@pytest.fixture
def admin_override():
    def _override():
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Insufficient role")
    return _override

@pytest.fixture
def client(superadmin_override):
    app.dependency_overrides[get_db] = mock_get_db
    app.dependency_overrides[require_superadmin] = superadmin_override
    yield TestClient(app)
    app.dependency_overrides = {}

def test_admin_denied_organisation_access(admin_override):
    app.dependency_overrides[get_db] = mock_get_db
    app.dependency_overrides[require_superadmin] = admin_override
    test_client = TestClient(app)
    org_id = uuid.uuid4()
    
    resp = test_client.get("/admin/organisations")
    assert resp.status_code == 403
    
    resp = test_client.post("/admin/organisations", json={"name": "test"})
    assert resp.status_code == 403

    resp = test_client.get(f"/admin/organisations/{org_id}")
    assert resp.status_code == 403

    resp = test_client.patch(f"/admin/organisations/{org_id}", json={"active": False})
    assert resp.status_code == 403
    app.dependency_overrides = {}

def test_non_superadmin_roles_denied_by_dependency():
    from fastapi import HTTPException

    for role in ("admin", "operator", "reviewer", "viewer"):
        with pytest.raises(HTTPException) as exc:
            require_superadmin({"user_id": str(uuid.uuid4()), "role": role})
        assert exc.value.status_code == 403

def test_superadmin_can_manage_organisations(client, monkeypatch):
    # Mock db.execute and db.get
    mock_db = mock_get_db()
    app.dependency_overrides[get_db] = lambda: mock_db
    
    # Mock duplicate check for POST
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = mock_result
    
    # POST
    create_payload = {
        "name": "Test Org Inc",
        "limits": {"max_users": 100},
        "settings": {"theme": "dark", "secret_key": "hidden"}
    }
    
    resp = client.post("/admin/organisations", json=create_payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Test Org Inc"
    assert data["slug"] == "test-org-inc"
    assert data["limits"]["max_users"] == 100
    assert "secret_key" not in data["settings"]
    assert "secret_key" not in str(mock_db.added)
    org_id = data["id"]
    created_audit = [obj for obj in mock_db.added if isinstance(obj, AuditLog)][0]
    assert created_audit.action == "organisation.created"
    assert created_audit.metadata_ == {
        "org_id": org_id,
        "slug": "test-org-inc",
        "changed_fields": ["name", "slug", "active", "limits", "settings"],
    }
    
    # Mock GET /organisations
    mock_org = Organisation(
        id=uuid.UUID(org_id),
        name="Test Org Inc",
        slug="test-org-inc",
        is_active=True,
        max_users=100,
        max_storage_mb=10240,
        settings={"theme": "dark", "api_key": "hidden"}
    )
    mock_db.execute.return_value.scalars.return_value.all.return_value = [mock_org]
    
    resp = client.get("/admin/organisations")
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 1
    
    # Mock GET /organisations/{id}
    mock_db.get.return_value = mock_org
    resp = client.get(f"/admin/organisations/{org_id}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Test Org Inc"
    assert resp.json()["settings"] == {"theme": "dark"}
    
    # Patch
    patch_payload = {"active": False, "name": "Updated Org Inc"}
    resp = client.patch(f"/admin/organisations/{org_id}", json=patch_payload)
    assert resp.status_code == 200
    assert resp.json()["active"] is False
    updated_audit = [obj for obj in mock_db.added if isinstance(obj, AuditLog) and obj.action == "organisation.updated"][0]
    assert updated_audit.metadata_["org_id"] == org_id
    assert updated_audit.metadata_["slug"] == "test-org-inc"
    assert updated_audit.metadata_["changed_fields"] == ["name", "active"]

def test_generated_slug_works(client):
    mock_db = mock_get_db()
    app.dependency_overrides[get_db] = lambda: mock_db
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = mock_result

    resp = client.post("/admin/organisations", json={"name": "Generated Slug Org"})

    assert resp.status_code == 200
    assert resp.json()["slug"] == "generated-slug-org"

def test_deactivate_works(client):
    mock_db = mock_get_db()
    app.dependency_overrides[get_db] = lambda: mock_db
    org_id = uuid.uuid4()
    mock_db.get.return_value = Organisation(
        id=org_id,
        name="Deactivate Org",
        slug="deactivate-org",
        is_active=True,
        max_users=50,
        max_storage_mb=10240,
        settings={},
    )

    resp = client.patch(f"/admin/organisations/{org_id}", json={"active": False})

    assert resp.status_code == 200
    assert resp.json()["active"] is False
    audit = [obj for obj in mock_db.added if isinstance(obj, AuditLog)][0]
    assert audit.action == "organisation.updated"
    assert audit.metadata_["changed_fields"] == ["active"]

def test_list_active_filter_and_safe_fields(client):
    mock_db = mock_get_db()
    app.dependency_overrides[get_db] = lambda: mock_db
    org = Organisation(
        id=uuid.uuid4(),
        name="Visible Org",
        slug="visible-org",
        is_active=True,
        max_users=999,
        max_storage_mb=999999,
        settings={"secret": "hidden"},
    )
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [org]
    mock_db.execute.return_value = mock_result

    resp = client.get("/admin/organisations?active=true&limit=5&offset=0")

    assert resp.status_code == 200
    data = resp.json()
    assert data["limit"] == 5
    assert data["items"][0] == {
        "id": str(org.id),
        "name": "Visible Org",
        "slug": "visible-org",
        "active": True,
        "created_at": None,
        "updated_at": None,
    }
    assert "settings" not in data["items"][0]
    assert "limits" not in data["items"][0]

def test_duplicate_slug_rejected(client):
    mock_db = mock_get_db()
    app.dependency_overrides[get_db] = lambda: mock_db
    
    # simulate existing org
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = True
    mock_db.execute.return_value = mock_result
    
    payload = {"name": "Duplicate Org", "slug": "dup-org"}
    resp = client.post("/admin/organisations", json=payload)
    assert resp.status_code == 409

def test_unsafe_slug_rejected(client):
    payload = {"name": "Unsafe Org", "slug": "unsafe space"}
    resp = client.post("/admin/organisations", json=payload)
    assert resp.status_code == 400
