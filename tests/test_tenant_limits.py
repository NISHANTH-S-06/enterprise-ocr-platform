import asyncio
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.models.user import Organisation
from app.services.tenant_limits import (
    check_batch_limits_for_user,
    get_org_limits,
    get_org_setting,
    is_ai_fallback_enabled_for_user,
    redact_settings,
)


def test_default_limits_load():
    limits = get_org_limits(None)

    assert limits["max_upload_mb"] == 50
    assert limits["max_batch_files"] == 10
    assert limits["max_batch_total_mb"] == 100
    assert limits["monthly_upload_limit"] is None
    assert limits["ai_fallback_enabled"] is True
    assert limits["batch_enabled"] is True
    assert limits["export_enabled"] is True


def test_org_custom_limits_override_defaults():
    org = SimpleNamespace(settings={"max_batch_files": 3, "max_batch_total_mb": 12, "batch_enabled": False})

    limits = get_org_limits(org)

    assert limits["max_batch_files"] == 3
    assert limits["max_batch_total_mb"] == 12
    assert limits["batch_enabled"] is False


def test_orgless_legacy_user_uses_defaults():
    user = {"user_id": str(uuid.uuid4()), "role": "operator", "org_id": None}

    assert get_org_limits(user)["max_batch_files"] == 10
    assert get_org_setting(user, "export_enabled", False) is True
    check_batch_limits_for_user(user, 10, 100 * 1024 * 1024)


def test_batch_max_files_blocked_by_org_limit():
    org = SimpleNamespace(settings={"max_batch_files": 1})

    with pytest.raises(HTTPException) as exc:
        check_batch_limits_for_user(org, 2, 0)

    assert exc.value.status_code == 413


def test_batch_total_mb_blocked_by_org_limit():
    org = SimpleNamespace(settings={"max_batch_total_mb": 1})

    with pytest.raises(HTTPException) as exc:
        check_batch_limits_for_user(org, 1, 2 * 1024 * 1024)

    assert exc.value.status_code == 413


def test_ai_disabled_blocks_fallback_gate(monkeypatch):
    from app.api.v1.routers import upload

    monkeypatch.setattr(upload.settings, "AI_FALLBACK_ENABLED", True)

    assert is_ai_fallback_enabled_for_user(SimpleNamespace(settings={"ai_fallback_enabled": False})) is False
    assert upload._should_attempt_ai_fallback(
        ["invoice_number"],
        SimpleNamespace(settings={"ai_fallback_enabled": False}),
    ) is False


def test_unsafe_setting_keys_redacted():
    redacted = redact_settings({
        "theme": "dark",
        "api_key": "hidden",
        "nested": {"token": "hidden", "enabled": True},
        "authorization": "Bearer hidden",
    })

    assert redacted == {"theme": "dark", "nested": {"enabled": True}}


def test_upload_batch_respects_org_max_files(monkeypatch):
    from app.api.v1.routers import upload

    org_id = uuid.uuid4()
    org = Organisation(id=org_id, name="Limited", slug="limited", is_active=True, settings={"max_batch_files": 1})

    class FakeDB:
        async def get(self, model, requested_id):
            assert model is Organisation
            assert requested_id == org_id
            return org

    with pytest.raises(HTTPException) as exc:
        asyncio.run(upload.upload_batch(
            background_tasks=object(),
            files=["a", "b"],
            request=None,
            current_user={"user_id": str(uuid.uuid4()), "role": "operator", "org_id": str(org_id)},
            db=FakeDB(),
        ))

    assert exc.value.status_code == 413


def test_upload_batch_respects_org_total_mb(monkeypatch):
    from app.api.v1.routers import upload

    async def fake_prepare(file):
        return {
            "data": b"x" * 800_000,
            "safe_name": str(file),
            "detected": "image/png",
            "ext": ".png",
            "content_type": "image/png",
        }

    org_id = uuid.uuid4()
    org = Organisation(id=org_id, name="Limited", slug="limited", is_active=True, settings={"max_batch_total_mb": 1})

    class FakeDB:
        async def get(self, model, requested_id):
            assert model is Organisation
            assert requested_id == org_id
            return org

    monkeypatch.setattr(upload, "_prepare_upload_file", fake_prepare)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(upload.upload_batch(
            background_tasks=object(),
            files=["a", "b"],
            request=None,
            current_user={"user_id": str(uuid.uuid4()), "role": "operator", "org_id": str(org_id)},
            db=FakeDB(),
        ))

    assert exc.value.status_code == 413
