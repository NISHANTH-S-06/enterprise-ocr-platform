from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_roles
from app.models.batch import OCRBatch
from app.models.enums import ExtractionStatus, JobStatus, LogEventType, LogLevel, UserRole, UserStatus
from app.models.extracted_data import ExtractedData
from app.models.file import UploadedFile
from app.models.fill_job import FormFillJob
from app.models.job import OCRJob
from app.models.log import AuditLog
from app.models.user import User, Organisation
import re
from pydantic import BaseModel, Field

router = APIRouter(prefix="/admin", tags=["admin"])
MAX_RECENT_LIMIT = 100
DEFAULT_RECENT_LIMIT = 20
SENSITIVE_METADATA_KEYS = (
    "raw_text",
    "extracted_text",
    "raw_ocr_json",
    "ocr_text",
    "file_content",
    "file_contents",
    "api_key",
    "authorization",
    "token",
    "password",
    "secret",
    "traceback",
    "stack_trace",
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _org_scoped_filters(model: type[Any], current_user: dict[str, Any]) -> list[Any]:
    if current_user.get("role") == UserRole.SUPERADMIN.value:
        return []
    org_id = current_user.get("org_id")
    if org_id and hasattr(model, "org_id"):
        return [model.org_id == uuid.UUID(str(org_id))]
    if hasattr(model, "org_id"):
        return [model.org_id.is_(None)]
    return []


async def _count_total(db: AsyncSession, model: type[Any], current_user: dict[str, Any], *extra_filters: Any) -> int:
    stmt = select(func.count()).select_from(model).where(*_org_scoped_filters(model, current_user), *extra_filters)
    result = await db.execute(stmt)
    return int(result.scalar_one() or 0)


async def _count_by_column(
    db: AsyncSession,
    model: type[Any],
    column: Any,
    current_user: dict[str, Any],
    *extra_filters: Any,
) -> dict[str, int]:
    stmt = (
        select(column, func.count())
        .select_from(model)
        .where(*_org_scoped_filters(model, current_user), *extra_filters)
        .group_by(column)
    )
    result = await db.execute(stmt)
    return {str(key): int(count or 0) for key, count in result.all() if key is not None}


async def _sum_column(db: AsyncSession, model: type[Any], column: Any, current_user: dict[str, Any]) -> int:
    stmt = select(func.coalesce(func.sum(column), 0)).select_from(model).where(*_org_scoped_filters(model, current_user))
    result = await db.execute(stmt)
    return int(result.scalar_one() or 0)


def _clamp_pagination(limit: int, offset: int) -> tuple[int, int]:
    safe_limit = min(max(int(limit or DEFAULT_RECENT_LIMIT), 1), MAX_RECENT_LIMIT)
    safe_offset = max(int(offset or 0), 0)
    return safe_limit, safe_offset


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return None


def _safe_text(value: Any, max_len: int = 300) -> str | None:
    if value is None:
        return None
    text = str(value)
    lowered = text.lower()
    if any(key in lowered for key in SENSITIVE_METADATA_KEYS):
        return "redacted"
    return text[:max_len]


def _safe_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(metadata, dict):
        return {}
    safe: dict[str, Any] = {}
    for key, value in metadata.items():
        key_text = str(key)
        lowered = key_text.lower()
        if any(sensitive in lowered for sensitive in SENSITIVE_METADATA_KEYS):
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            safe[key_text] = _safe_text(value, 200) if isinstance(value, str) else value
        if len(safe) >= 10:
            break
    return safe


def _mb(total_bytes: int) -> float:
    return round(float(total_bytes or 0) / (1024 * 1024), 2)


def _safe_file_size(path_value: str | None) -> tuple[int, bool]:
    if not path_value:
        return 0, True
    try:
        path = Path(path_value)
        if not path.is_file():
            return 0, True
        return int(path.stat().st_size), False
    except OSError:
        return 0, True


async def _batch_zip_storage(db: AsyncSession, current_user: dict[str, Any]) -> dict[str, int]:
    stmt = (
        select(OCRBatch.output_zip_path)
        .where(*_org_scoped_filters(OCRBatch, current_user), OCRBatch.output_zip_path.is_not(None))
    )
    result = await db.execute(stmt)
    total_bytes = 0
    zip_count = 0
    missing_files = 0
    for path_value in result.scalars().all():
        size, missing = _safe_file_size(path_value)
        if missing:
            missing_files += 1
            continue
        zip_count += 1
        total_bytes += size
    return {"total_bytes": total_bytes, "zip_count": zip_count, "missing_files": missing_files}


async def _recent_rows(
    db: AsyncSession,
    model: type[Any],
    current_user: dict[str, Any],
    order_column: Any,
    limit: int,
    offset: int,
    *extra_filters: Any,
) -> list[Any]:
    safe_limit, safe_offset = _clamp_pagination(limit, offset)
    stmt = (
        select(model)
        .where(*_org_scoped_filters(model, current_user), *extra_filters)
        .order_by(order_column.desc())
        .limit(safe_limit)
        .offset(safe_offset)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


def _audit_log_payload(row: AuditLog) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "action": row.action,
        "resource_type": row.resource_type,
        "resource_id": row.resource_id,
        "created_at": _iso(row.occurred_at),
        "user_id": str(row.user_id) if row.user_id else None,
        "metadata": _safe_metadata(getattr(row, "metadata_", None)),
    }


def _upload_payload(row: UploadedFile) -> dict[str, Any]:
    terminal = row.status in {"done", "failed", "quarantine", "validated"}
    return {
        "file_id": str(row.id),
        "filename": row.original_filename,
        "status": row.status,
        "mime_type": row.mime_type,
        "file_size": int(row.file_size or 0),
        "created_at": _iso(row.created_at),
        "processed_at": _iso(row.updated_at) if terminal else None,
        "batch_id": str(row.batch_id) if row.batch_id else None,
        "user_id": str(row.uploaded_by_id),
    }


def _job_payload(row: OCRJob) -> dict[str, Any]:
    return {
        "job_id": str(row.id),
        "file_id": str(row.file_id),
        "batch_id": str(row.batch_id) if row.batch_id else None,
        "status": row.status,
        "error_message": _safe_text(row.error_message, 300),
        "confidence": float(row.avg_confidence) if row.avg_confidence is not None else None,
        "created_at": _iso(row.created_at),
        "started_at": _iso(row.started_at),
        "completed_at": _iso(row.completed_at),
    }


def _batch_payload(row: OCRBatch) -> dict[str, Any]:
    return {
        "batch_id": str(row.id),
        "status": row.status,
        "file_count": row.file_count,
        "completed_count": row.completed_count,
        "failed_count": row.failed_count,
        "needs_review_count": row.needs_review_count,
        "validated_count": row.validated_count,
        "total_bytes": row.total_bytes,
        "total_pages": row.total_pages,
        "created_at": _iso(row.created_at),
        "completed_at": _iso(row.completed_at),
    }


@router.get("/summary", summary="Get admin dashboard summary metrics")
async def get_admin_summary(
    current_user: dict = Depends(require_roles(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user_total = await _count_total(db, User, current_user)
    user_active = await _count_total(db, User, current_user, User.status == UserStatus.ACTIVE.value)
    users_by_role = await _count_by_column(db, User, User.role, current_user)

    uploads_by_status = await _count_by_column(db, UploadedFile, UploadedFile.status, current_user)
    ocr_jobs_by_status = await _count_by_column(db, OCRJob, OCRJob.status, current_user)
    extractions_by_status = await _count_by_column(db, ExtractedData, ExtractedData.status, current_user)
    batches_by_status = await _count_by_column(db, OCRBatch, OCRBatch.status, current_user)

    export_filter = or_(AuditLog.action.like("export.%"), AuditLog.action == "batch.export.zip")
    exports_by_action = await _count_by_column(db, AuditLog, AuditLog.action, current_user, export_filter)

    return {
        "users": {
            "total": user_total,
            "active": user_active,
            "by_role": users_by_role,
        },
        "uploads": {
            "total": await _count_total(db, UploadedFile, current_user),
            "by_status": uploads_by_status,
        },
        "ocr_jobs": {
            "total": await _count_total(db, OCRJob, current_user),
            "by_status": ocr_jobs_by_status,
            "failed": ocr_jobs_by_status.get(JobStatus.FAILED.value, 0),
        },
        "extractions": {
            "total": await _count_total(db, ExtractedData, current_user),
            "by_status": extractions_by_status,
            "needs_review": extractions_by_status.get(ExtractionStatus.NEEDS_REVIEW.value, 0),
        },
        "batches": {
            "total": await _count_total(db, OCRBatch, current_user),
            "by_status": batches_by_status,
        },
        "ai": {
            "gemini_fallback_count": await _count_total(
                db, AuditLog, current_user, AuditLog.action == "ai.fallback.used"
            ),
        },
        "exports": {
            "total": sum(exports_by_action.values()),
            "by_action": exports_by_action,
            "pdf_count": exports_by_action.get("export.pdf", 0),
            "excel_count": exports_by_action.get("export.excel", 0),
            "batch_zip_count": exports_by_action.get("batch.export.zip", 0),
        },
    }


@router.get("/storage", summary="Get admin storage usage metrics")
async def get_admin_storage(
    current_user: dict = Depends(require_roles(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    upload_bytes = await _sum_column(db, UploadedFile, UploadedFile.file_size, current_user)
    upload_count = await _count_total(db, UploadedFile, current_user)
    batch_zips = await _batch_zip_storage(db, current_user)
    form_fill_bytes = await _sum_column(db, FormFillJob, FormFillJob.output_size, current_user)
    form_fill_count = await _count_total(db, FormFillJob, current_user, FormFillJob.output_size.is_not(None))
    combined_bytes = upload_bytes + batch_zips["total_bytes"] + form_fill_bytes

    return {
        "uploads": {
            "total_bytes": upload_bytes,
            "total_mb": _mb(upload_bytes),
            "file_count": upload_count,
        },
        "batch_zips": {
            "total_bytes": batch_zips["total_bytes"],
            "total_mb": _mb(batch_zips["total_bytes"]),
            "zip_count": batch_zips["zip_count"],
            "missing_files": batch_zips["missing_files"],
        },
        "form_fill_outputs": {
            "total_bytes": form_fill_bytes,
            "total_mb": _mb(form_fill_bytes),
            "output_count": form_fill_count,
        },
        "combined": {
            "total_bytes": combined_bytes,
            "total_mb": _mb(combined_bytes),
        },
    }


@router.get("/audit-logs", summary="Get recent admin audit logs")
async def get_recent_audit_logs(
    limit: int = Query(DEFAULT_RECENT_LIMIT, ge=1),
    offset: int = Query(0, ge=0),
    action: str | None = Query(None),
    current_user: dict = Depends(require_roles(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    safe_limit, safe_offset = _clamp_pagination(limit, offset)
    filters = [AuditLog.action == action] if action else []
    rows = await _recent_rows(db, AuditLog, current_user, AuditLog.occurred_at, safe_limit, safe_offset, *filters)
    return {"limit": safe_limit, "offset": safe_offset, "items": [_audit_log_payload(row) for row in rows]}


@router.get("/uploads/recent", summary="Get recent uploaded files")
async def get_recent_uploads(
    limit: int = Query(DEFAULT_RECENT_LIMIT, ge=1),
    offset: int = Query(0, ge=0),
    status: str | None = Query(None),
    current_user: dict = Depends(require_roles(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    safe_limit, safe_offset = _clamp_pagination(limit, offset)
    filters = [UploadedFile.status == status] if status else []
    rows = await _recent_rows(db, UploadedFile, current_user, UploadedFile.created_at, safe_limit, safe_offset, *filters)
    return {"limit": safe_limit, "offset": safe_offset, "items": [_upload_payload(row) for row in rows]}


@router.get("/jobs/recent", summary="Get recent OCR jobs")
async def get_recent_jobs(
    limit: int = Query(DEFAULT_RECENT_LIMIT, ge=1),
    offset: int = Query(0, ge=0),
    status: str | None = Query(None),
    current_user: dict = Depends(require_roles(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    safe_limit, safe_offset = _clamp_pagination(limit, offset)
    filters = [OCRJob.status == status] if status else []
    rows = await _recent_rows(db, OCRJob, current_user, OCRJob.created_at, safe_limit, safe_offset, *filters)
    return {"limit": safe_limit, "offset": safe_offset, "items": [_job_payload(row) for row in rows]}


@router.get("/batches/recent", summary="Get recent OCR batches")
async def get_recent_batches(
    limit: int = Query(DEFAULT_RECENT_LIMIT, ge=1),
    offset: int = Query(0, ge=0),
    status: str | None = Query(None),
    current_user: dict = Depends(require_roles(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    safe_limit, safe_offset = _clamp_pagination(limit, offset)
    filters = [OCRBatch.status == status] if status else []
    rows = await _recent_rows(db, OCRBatch, current_user, OCRBatch.created_at, safe_limit, safe_offset, *filters)
    return {"limit": safe_limit, "offset": safe_offset, "items": [_batch_payload(row) for row in rows]}


# --- Organisation Management (Superadmin Only) ---

class OrganisationCreate(BaseModel):
    name: str
    slug: str | None = None
    active: bool = True
    limits: dict = Field(default_factory=dict)
    settings: dict = Field(default_factory=dict)

class OrganisationUpdate(BaseModel):
    name: str | None = None
    active: bool | None = None
    limits: dict | None = None
    settings: dict | None = None

class OrganisationUserAssign(BaseModel):
    user_id: uuid.UUID
    role: UserRole | None = None

class UserRoleUpdate(BaseModel):
    role: UserRole

class UserOrganisationUpdate(BaseModel):
    org_id: uuid.UUID

def _sanitize_slug(name: str) -> str:
    s = name.lower()
    s = re.sub(r'[^a-z0-9-]+', '-', s)
    s = re.sub(r'-+', '-', s)
    return s.strip('-')

def _is_safe_slug(slug: str) -> bool:
    return bool(re.match(r'^[a-z0-9-]+$', slug))

def _clean_dict_secrets(d: dict) -> dict:
    safe_dict = {}
    for k, v in d.items():
        if any(secret in str(k).lower() for secret in SENSITIVE_METADATA_KEYS):
            continue
        if isinstance(v, dict):
            safe_dict[str(k)] = _clean_dict_secrets(v)
        else:
            safe_dict[str(k)] = v
    return safe_dict

def require_superadmin(current_user: dict = Depends(require_roles(UserRole.SUPERADMIN))) -> dict:
    if current_user.get("role") != UserRole.SUPERADMIN.value:
        raise HTTPException(status_code=403, detail="Insufficient role")
    return current_user

def _is_superadmin_user(current_user: dict[str, Any]) -> bool:
    return current_user.get("role") == UserRole.SUPERADMIN.value

def _current_org_uuid(current_user: dict[str, Any]) -> uuid.UUID | None:
    org_id = current_user.get("org_id")
    return uuid.UUID(str(org_id)) if org_id else None

def _require_admin_org_access(current_user: dict[str, Any], org_id: uuid.UUID) -> None:
    if _is_superadmin_user(current_user):
        return
    if current_user.get("role") != UserRole.ADMIN.value:
        raise HTTPException(status_code=403, detail="Insufficient role")
    if _current_org_uuid(current_user) != org_id:
        raise HTTPException(status_code=404, detail="Organisation not found")

def _require_admin_user_access(current_user: dict[str, Any], user: User) -> None:
    if _is_superadmin_user(current_user):
        return
    if current_user.get("role") != UserRole.ADMIN.value:
        raise HTTPException(status_code=403, detail="Insufficient role")
    if _current_org_uuid(current_user) != user.org_id:
        raise HTTPException(status_code=404, detail="User not found")

async def _get_active_org(db: AsyncSession, org_id: uuid.UUID) -> Organisation:
    org = await db.get(Organisation, org_id)
    if not org or org.is_deleted:
        raise HTTPException(status_code=404, detail="Organisation not found")
    return org

async def _get_user_for_admin(db: AsyncSession, user_id: uuid.UUID) -> User:
    user = await db.get(User, user_id)
    if not user or user.is_deleted:
        raise HTTPException(status_code=404, detail="User not found")
    return user

def _user_payload(user: User) -> dict[str, Any]:
    return {
        "id": str(user.id),
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "status": user.status,
        "org_id": str(user.org_id) if user.org_id else None,
        "created_at": _iso(user.created_at),
        "updated_at": _iso(user.updated_at),
    }

def _validate_role_update(actor: dict[str, Any], role: UserRole) -> str:
    new_role = role.value
    if new_role == UserRole.SUPERADMIN.value and not _is_superadmin_user(actor):
        raise HTTPException(status_code=403, detail="Insufficient role")
    return new_role

def _add_user_audit(
    db: AsyncSession,
    *,
    action: str,
    actor: dict[str, Any],
    user: User,
    org_id: uuid.UUID | None,
    changed_fields: list[str],
) -> None:
    db.add(
        AuditLog(
            action=action,
            event_type=LogEventType.USER_MGMT.value,
            level=LogLevel.INFO.value,
            resource_type="user",
            resource_id=str(user.id),
            user_id=uuid.UUID(actor["user_id"]),
            org_id=org_id,
            occurred_at=_utc_now(),
            metadata_={
                "user_id": str(user.id),
                "org_id": str(org_id) if org_id else None,
                "changed_fields": changed_fields,
            },
        )
    )

@router.get("/organisations", summary="List organisations")
async def list_organisations(
    limit: int = Query(DEFAULT_RECENT_LIMIT, ge=1, le=MAX_RECENT_LIMIT),
    offset: int = Query(0, ge=0),
    active: bool | None = Query(None),
    current_user: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    safe_limit, safe_offset = _clamp_pagination(limit, offset)
    filters = [Organisation.is_deleted == False]
    if active is not None:
        filters.append(Organisation.is_active == active)
    
    stmt = select(Organisation).where(*filters).order_by(Organisation.created_at.desc()).limit(safe_limit).offset(safe_offset)
    result = await db.execute(stmt)
    orgs = result.scalars().all()
    
    items = []
    for org in orgs:
        items.append({
            "id": str(org.id),
            "name": org.name,
            "slug": org.slug,
            "active": org.is_active,
            "created_at": _iso(org.created_at),
            "updated_at": _iso(org.updated_at),
        })
        
    return {"limit": safe_limit, "offset": safe_offset, "items": items}

@router.post("/organisations", summary="Create organisation")
async def create_organisation(
    payload: OrganisationCreate,
    current_user: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    slug = payload.slug
    if not slug:
        slug = _sanitize_slug(payload.name)
    if not slug or not _is_safe_slug(slug):
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Invalid slug")
    
    stmt = select(Organisation).where(Organisation.slug == slug, Organisation.is_deleted == False)
    if (await db.execute(stmt)).scalar_one_or_none():
        from fastapi import HTTPException
        raise HTTPException(status_code=409, detail="Duplicate slug")
        
    safe_limits = _clean_dict_secrets(payload.limits)
    safe_settings = _clean_dict_secrets(payload.settings)
    
    max_users = safe_limits.get("max_users", 50)
    max_storage_mb = safe_limits.get("max_storage_mb", 10240)
    
    new_org = Organisation(
        name=payload.name,
        slug=slug,
        is_active=payload.active,
        max_users=max_users,
        max_storage_mb=max_storage_mb,
        settings=safe_settings,
    )
    db.add(new_org)
    await db.flush()
    
    audit = AuditLog(
        action="organisation.created",
        event_type=LogEventType.USER_MGMT.value,
        level=LogLevel.INFO.value,
        resource_type="organisation",
        resource_id=str(new_org.id),
        user_id=uuid.UUID(current_user["user_id"]),
        org_id=new_org.id,
        occurred_at=_utc_now(),
        metadata_={
            "org_id": str(new_org.id),
            "slug": new_org.slug,
            "changed_fields": ["name", "slug", "active", "limits", "settings"],
        }
    )
    db.add(audit)
    await db.commit()
    
    return {
        "id": str(new_org.id),
        "name": new_org.name,
        "slug": new_org.slug,
        "active": new_org.is_active,
        "limits": {"max_users": new_org.max_users, "max_storage_mb": new_org.max_storage_mb},
        "settings": _clean_dict_secrets(new_org.settings or {}),
        "created_at": _iso(new_org.created_at),
        "updated_at": _iso(new_org.updated_at),
    }

@router.get("/organisations/{org_id}", summary="Get organisation detail")
async def get_organisation(
    org_id: uuid.UUID,
    current_user: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    org = await db.get(Organisation, org_id)
    if not org or org.is_deleted:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Organisation not found")
        
    return {
        "id": str(org.id),
        "name": org.name,
        "slug": org.slug,
        "active": org.is_active,
        "limits": {"max_users": org.max_users, "max_storage_mb": org.max_storage_mb},
        "settings": _clean_dict_secrets(org.settings or {}),
        "created_at": _iso(org.created_at),
        "updated_at": _iso(org.updated_at),
    }

@router.patch("/organisations/{org_id}", summary="Patch organisation")
async def patch_organisation(
    org_id: uuid.UUID,
    payload: OrganisationUpdate,
    current_user: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    org = await db.get(Organisation, org_id)
    if not org or org.is_deleted:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Organisation not found")
        
    changed = []
    if payload.name is not None and payload.name != org.name:
        org.name = payload.name
        changed.append("name")
        
    if payload.active is not None and payload.active != org.is_active:
        org.is_active = payload.active
        changed.append("active")
        
    if payload.limits is not None:
        safe_limits = _clean_dict_secrets(payload.limits)
        if "max_users" in safe_limits:
            org.max_users = safe_limits["max_users"]
            changed.append("max_users")
        if "max_storage_mb" in safe_limits:
            org.max_storage_mb = safe_limits["max_storage_mb"]
            changed.append("max_storage_mb")
            
    if payload.settings is not None:
        safe_settings = _clean_dict_secrets(payload.settings)
        org.settings = safe_settings
        changed.append("settings")
        
    if changed:
        org.updated_at = _utc_now()
        audit = AuditLog(
            action="organisation.updated",
            event_type=LogEventType.USER_MGMT.value,
            level=LogLevel.INFO.value,
            resource_type="organisation",
            resource_id=str(org.id),
            user_id=uuid.UUID(current_user["user_id"]),
            org_id=org.id,
            occurred_at=_utc_now(),
            metadata_={
                "org_id": str(org.id),
                "slug": org.slug,
                "changed_fields": changed,
            }
        )
        db.add(audit)
        await db.commit()
        
    return {
        "id": str(org.id),
        "name": org.name,
        "slug": org.slug,
        "active": org.is_active,
        "limits": {"max_users": org.max_users, "max_storage_mb": org.max_storage_mb},
        "settings": _clean_dict_secrets(org.settings or {}),
        "created_at": _iso(org.created_at),
        "updated_at": _iso(org.updated_at),
    }

@router.get("/organisations/{org_id}/users", summary="List organisation users")
async def list_organisation_users(
    org_id: uuid.UUID,
    limit: int = Query(DEFAULT_RECENT_LIMIT, ge=1, le=MAX_RECENT_LIMIT),
    offset: int = Query(0, ge=0),
    current_user: dict = Depends(require_roles(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    await _get_active_org(db, org_id)
    _require_admin_org_access(current_user, org_id)
    safe_limit, safe_offset = _clamp_pagination(limit, offset)

    stmt = (
        select(User)
        .where(User.org_id == org_id, User.is_deleted == False)
        .order_by(User.created_at.desc())
        .limit(safe_limit)
        .offset(safe_offset)
    )
    result = await db.execute(stmt)
    return {"limit": safe_limit, "offset": safe_offset, "items": [_user_payload(row) for row in result.scalars().all()]}

@router.post("/organisations/{org_id}/users/assign", summary="Assign user to organisation")
async def assign_user_to_organisation(
    org_id: uuid.UUID,
    payload: OrganisationUserAssign,
    current_user: dict = Depends(require_roles(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    await _get_active_org(db, org_id)
    _require_admin_org_access(current_user, org_id)
    user = await _get_user_for_admin(db, payload.user_id)
    if not _is_superadmin_user(current_user) and user.org_id not in (None, org_id):
        raise HTTPException(status_code=404, detail="User not found")

    changed: list[str] = []
    if user.org_id != org_id:
        user.org_id = org_id
        changed.append("org_id")
    if payload.role is not None:
        new_role = _validate_role_update(current_user, payload.role)
        if user.role != new_role:
            user.role = new_role
            changed.append("role")

    if changed:
        user.updated_at = _utc_now()
        _add_user_audit(
            db,
            action="user.organisation_assigned",
            actor=current_user,
            user=user,
            org_id=org_id,
            changed_fields=changed,
        )
        await db.commit()

    return _user_payload(user)

@router.patch("/users/{user_id}/role", summary="Update user role")
async def patch_user_role(
    user_id: uuid.UUID,
    payload: UserRoleUpdate,
    current_user: dict = Depends(require_roles(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await _get_user_for_admin(db, user_id)
    _require_admin_user_access(current_user, user)
    new_role = _validate_role_update(current_user, payload.role)

    if user.role != new_role:
        user.role = new_role
        user.updated_at = _utc_now()
        _add_user_audit(
            db,
            action="user.role_updated",
            actor=current_user,
            user=user,
            org_id=user.org_id,
            changed_fields=["role"],
        )
        await db.commit()

    return _user_payload(user)

@router.patch("/users/{user_id}/organisation", summary="Update user organisation")
async def patch_user_organisation(
    user_id: uuid.UUID,
    payload: UserOrganisationUpdate,
    current_user: dict = Depends(require_roles(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    await _get_active_org(db, payload.org_id)
    _require_admin_org_access(current_user, payload.org_id)
    user = await _get_user_for_admin(db, user_id)
    if not _is_superadmin_user(current_user) and user.org_id not in (None, payload.org_id):
        raise HTTPException(status_code=404, detail="User not found")

    if user.org_id != payload.org_id:
        user.org_id = payload.org_id
        user.updated_at = _utc_now()
        _add_user_audit(
            db,
            action="user.organisation_updated",
            actor=current_user,
            user=user,
            org_id=payload.org_id,
            changed_fields=["org_id"],
        )
        await db.commit()

    return _user_payload(user)
