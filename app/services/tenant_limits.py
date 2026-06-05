from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.core.config import get_settings


DEFAULT_MAX_BATCH_FILES = 10
DEFAULT_MAX_BATCH_TOTAL_MB = 100
SENSITIVE_SETTING_KEYS = ("api_key", "authorization", "token", "password", "secret")


def _settings_dict(source: Any) -> dict[str, Any]:
    if source is None:
        return {}
    if isinstance(source, dict):
        if isinstance(source.get("settings"), dict):
            return source["settings"]
        organisation = source.get("organisation")
        if isinstance(organisation, dict) and isinstance(organisation.get("settings"), dict):
            return organisation["settings"]
        return {}
    settings = getattr(source, "settings", None)
    if isinstance(settings, dict):
        return settings
    organisation = getattr(source, "organisation", None)
    org_settings = getattr(organisation, "settings", None)
    return org_settings if isinstance(org_settings, dict) else {}


def _is_sensitive_key(key: str) -> bool:
    lowered = str(key).lower()
    return any(sensitive in lowered for sensitive in SENSITIVE_SETTING_KEYS)


def redact_settings(settings: dict[str, Any] | None) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in (settings or {}).items():
        key_text = str(key)
        if _is_sensitive_key(key_text):
            continue
        if isinstance(value, dict):
            safe[key_text] = redact_settings(value)
        else:
            safe[key_text] = value
    return safe


def get_org_limits(user_or_org: Any) -> dict[str, Any]:
    settings = get_settings()
    defaults = {
        "max_upload_mb": settings.MAX_FILE_SIZE_MB,
        "max_batch_files": DEFAULT_MAX_BATCH_FILES,
        "max_batch_total_mb": DEFAULT_MAX_BATCH_TOTAL_MB,
        "monthly_upload_limit": None,
        "ai_fallback_enabled": True,
        "batch_enabled": True,
        "export_enabled": True,
    }
    for key, value in redact_settings(_settings_dict(user_or_org)).items():
        if key in defaults:
            defaults[key] = value
    return defaults


def get_org_setting(user_or_org: Any, key: str, default: Any = None) -> Any:
    if _is_sensitive_key(key):
        return default
    return get_org_limits(user_or_org).get(key, default)


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def check_batch_limits_for_user(user_or_org: Any, file_count: int, total_bytes: int) -> None:
    limits = get_org_limits(user_or_org)
    if limits.get("batch_enabled") is False:
        raise HTTPException(status_code=403, detail="Batch upload is disabled for this organisation")

    max_files = _positive_int(limits.get("max_batch_files"), DEFAULT_MAX_BATCH_FILES)
    if file_count > max_files:
        raise HTTPException(status_code=413, detail=f"Batch exceeds {max_files} files")

    max_total_mb = _positive_int(limits.get("max_batch_total_mb"), DEFAULT_MAX_BATCH_TOTAL_MB)
    max_total_bytes = max_total_mb * 1024 * 1024
    if total_bytes and total_bytes > max_total_bytes:
        raise HTTPException(status_code=413, detail="Batch exceeds total size limit")


def is_ai_fallback_enabled_for_user(user_or_org: Any) -> bool:
    return bool(get_org_setting(user_or_org, "ai_fallback_enabled", True))
