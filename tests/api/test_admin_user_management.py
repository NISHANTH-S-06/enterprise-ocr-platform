import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.routers.admin import router
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.enums import UserRole, UserStatus
from app.models.log import AuditLog
from app.models.user import Organisation, User


app = FastAPI()
app.include_router(router)


def make_org(org_id=None, slug="tenant-a"):
    return Organisation(
        id=org_id or uuid.uuid4(),
        name="Tenant A",
        slug=slug,
        is_active=True,
        max_users=50,
        max_storage_mb=10240,
        settings={},
    )


def make_user(user_id=None, org_id=None, role=UserRole.OPERATOR.value):
    return User(
        id=user_id or uuid.uuid4(),
        email="user@example.com",
        hashed_password="not-returned",
        full_name="Tenant User",
        role=role,
        status=UserStatus.ACTIVE.value,
        org_id=org_id,
        preferences={"secret": "not-returned"},
    )


def mock_get_db():
    db = MagicMock()
    db.execute = AsyncMock()
    db.get = AsyncMock()
    db.commit = AsyncMock()
    db.added = []
    db.add.side_effect = db.added.append
    return db


def use_client(db, current_user):
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: current_user
    return TestClient(app)


def clear_overrides():
    app.dependency_overrides = {}


def test_superadmin_assign_user():
    db = mock_get_db()
    org = make_org()
    user = make_user(org_id=None)
    db.get.side_effect = [org, user]
    client = use_client(db, {"user_id": str(uuid.uuid4()), "role": "superadmin", "org_id": None})

    try:
        resp = client.post(
            f"/admin/organisations/{org.id}/users/assign",
            json={"user_id": str(user.id), "role": "reviewer"},
        )
    finally:
        clear_overrides()

    assert resp.status_code == 200
    data = resp.json()
    assert data["org_id"] == str(org.id)
    assert data["role"] == "reviewer"
    assert "hashed_password" not in data
    assert "preferences" not in data
    audit = [obj for obj in db.added if isinstance(obj, AuditLog)][0]
    assert audit.action == "user.organisation_assigned"
    assert audit.metadata_["changed_fields"] == ["org_id", "role"]


def test_superadmin_move_user():
    db = mock_get_db()
    source_org_id = uuid.uuid4()
    target_org = make_org(slug="tenant-b")
    user = make_user(org_id=source_org_id)
    db.get.side_effect = [target_org, user]
    client = use_client(db, {"user_id": str(uuid.uuid4()), "role": "superadmin", "org_id": None})

    try:
        resp = client.patch(f"/admin/users/{user.id}/organisation", json={"org_id": str(target_org.id)})
    finally:
        clear_overrides()

    assert resp.status_code == 200
    assert resp.json()["org_id"] == str(target_org.id)
    audit = [obj for obj in db.added if isinstance(obj, AuditLog)][0]
    assert audit.action == "user.organisation_updated"
    assert audit.metadata_["changed_fields"] == ["org_id"]


def test_admin_restricted_to_own_org():
    db = mock_get_db()
    org = make_org()
    users = [make_user(org_id=org.id)]
    result = MagicMock()
    result.scalars.return_value.all.return_value = users
    db.get.return_value = org
    db.execute.return_value = result
    client = use_client(db, {"user_id": str(uuid.uuid4()), "role": "admin", "org_id": str(org.id)})

    try:
        resp = client.get(f"/admin/organisations/{org.id}/users")
    finally:
        clear_overrides()

    assert resp.status_code == 200
    assert resp.json()["items"][0]["org_id"] == str(org.id)
    assert "hashed_password" not in resp.json()["items"][0]


def test_cross_tenant_access_denied():
    db = mock_get_db()
    own_org_id = uuid.uuid4()
    other_org = make_org(slug="other")
    db.get.return_value = other_org
    client = use_client(db, {"user_id": str(uuid.uuid4()), "role": "admin", "org_id": str(own_org_id)})

    try:
        resp = client.get(f"/admin/organisations/{other_org.id}/users")
    finally:
        clear_overrides()

    assert resp.status_code == 404


def test_operator_reviewer_viewer_denied():
    org = make_org()
    for role in ("operator", "reviewer", "viewer"):
        db = mock_get_db()
        client = use_client(db, {"user_id": str(uuid.uuid4()), "role": role, "org_id": str(org.id)})
        try:
            resp = client.get(f"/admin/organisations/{org.id}/users")
        finally:
            clear_overrides()
        assert resp.status_code == 403


def test_patch_role_safe_fields_and_audit():
    db = mock_get_db()
    org_id = uuid.uuid4()
    user = make_user(org_id=org_id, role=UserRole.OPERATOR.value)
    user.created_at = datetime.now(timezone.utc)
    db.get.return_value = user
    client = use_client(db, {"user_id": str(uuid.uuid4()), "role": "admin", "org_id": str(org_id)})

    try:
        resp = client.patch(f"/admin/users/{user.id}/role", json={"role": "reviewer"})
    finally:
        clear_overrides()

    assert resp.status_code == 200
    data = resp.json()
    assert data["role"] == "reviewer"
    assert set(data) == {"id", "email", "full_name", "role", "status", "org_id", "created_at", "updated_at"}
    audit = [obj for obj in db.added if isinstance(obj, AuditLog)][0]
    assert audit.action == "user.role_updated"
    assert audit.metadata_ == {
        "user_id": str(user.id),
        "org_id": str(org_id),
        "changed_fields": ["role"],
    }
