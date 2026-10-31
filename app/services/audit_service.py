from __future__ import annotations

import uuid
from ipaddress import ip_address as parse_ip_address
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import LogEventType, LogLevel
from app.models.log import AuditLog


def _uuid_or_none(value: Any) -> Optional[uuid.UUID]:
    if not value:
        return None
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def request_context(request: Request | None) -> dict[str, Any]:
    if request is None:
        return {}
    forwarded_for = request.headers.get("x-forwarded-for")
    ip_address = (forwarded_for.split(",", 1)[0].strip() if forwarded_for else None) or (
        request.client.host if request.client else None
    )
    try:
        ip_address = str(parse_ip_address(ip_address)) if ip_address else None
    except ValueError:
        ip_address = None
    return {
        "ip_address": ip_address,
        "user_agent": request.headers.get("user-agent"),
        "request_id": request.headers.get("x-request-id"),
    }


async def write_audit_log(
    db: AsyncSession,
    *,
    action: str,
    event_type: LogEventType | str,
    status: str,
    user_id: Any = None,
    org_id: Any = None,
    resource_type: str | None = None,
    resource_id: Any = None,
    message: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    level: LogLevel | str = LogLevel.INFO,
    error: str | None = None,
    request: Request | None = None,
) -> AuditLog:
    ctx = request_context(request)
    metadata_ = dict(metadata or {})
    metadata_["status"] = status
    if error:
        metadata_["error"] = error

    row = AuditLog(
        user_id=_uuid_or_none(user_id),
        org_id=_uuid_or_none(org_id),
        event_type=event_type.value if isinstance(event_type, LogEventType) else str(event_type),
        level=level.value if isinstance(level, LogLevel) else str(level),
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id is not None else None,
        message=message,
        diff={},
        metadata_=metadata_,
        ip_address=ctx.get("ip_address"),
        user_agent=ctx.get("user_agent"),
        request_id=ctx.get("request_id"),
        occurred_at=datetime.now(timezone.utc),
    )
    if hasattr(db, "add"):
        db.add(row)
    if hasattr(db, "flush"):
        await db.flush()
    return row
