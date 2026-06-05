from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import UserRole
from app.models.batch import OCRBatch
from app.models.extracted_data import ExtractedData
from app.models.fill_job import FormFillJob
from app.models.job import OCRJob


ADMIN_ROLES = {UserRole.ADMIN.value, UserRole.SUPERADMIN.value}


def current_user_id(current_user: dict[str, Any]) -> uuid.UUID:
    return uuid.UUID(str(current_user["user_id"]))


def get_current_org_id(current_user: dict[str, Any]) -> uuid.UUID | None:
    org_id = current_user.get("org_id")
    return uuid.UUID(str(org_id)) if org_id else None


def is_superadmin(current_user: dict[str, Any]) -> bool:
    return current_user.get("role") == UserRole.SUPERADMIN.value


def is_org_admin(current_user: dict[str, Any]) -> bool:
    return current_user.get("role") == UserRole.ADMIN.value


def is_admin(current_user: dict[str, Any]) -> bool:
    return current_user.get("role") in ADMIN_ROLES


def apply_org_scope(query: Any, model: type[Any], current_user: dict[str, Any]) -> Any:
    if is_superadmin(current_user):
        return query
    org_id = get_current_org_id(current_user)
    if org_id and hasattr(model, "org_id"):
        return query.where(model.org_id == org_id)
    if hasattr(model, "org_id"):
        return query.where(model.org_id.is_(None))
    return query


def assert_same_org(resource: Any, current_user: dict[str, Any]) -> None:
    if is_superadmin(current_user):
        return
    user_org_id = get_current_org_id(current_user)
    resource_org_id = getattr(resource, "org_id", None)
    if user_org_id and resource_org_id and resource_org_id != user_org_id:
        raise HTTPException(status_code=404, detail="Resource not found")
    if is_org_admin(current_user):
        if user_org_id:
            return
        # Backward-compatible single-tenant mode: org-less admin can access org-less legacy rows only.
        if resource_org_id is None:
            return
        raise HTTPException(status_code=404, detail="Resource not found")


def assert_owner(current_user: dict[str, Any], owner_id: uuid.UUID | None) -> None:
    if is_superadmin(current_user):
        return
    if is_org_admin(current_user):
        return
    if owner_id != current_user_id(current_user):
        raise HTTPException(status_code=404, detail="Resource not found")


async def get_owned_ocr_job(db: AsyncSession, job_id: uuid.UUID, current_user: dict[str, Any]) -> OCRJob:
    job = await db.get(OCRJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Upload job not found")
    assert_same_org(job, current_user)
    assert_owner(current_user, job.created_by_id)
    return job


async def get_owned_ocr_batch(db: AsyncSession, batch_id: uuid.UUID, current_user: dict[str, Any]) -> OCRBatch:
    batch = await db.get(OCRBatch, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    assert_same_org(batch, current_user)
    assert_owner(current_user, batch.created_by_id)
    return batch


async def get_owned_extracted_data(db: AsyncSession, extracted_data_id: uuid.UUID, current_user: dict[str, Any]) -> ExtractedData:
    extraction = await db.get(ExtractedData, extracted_data_id)
    if not extraction:
        raise HTTPException(status_code=404, detail="ExtractedData not found")
    job = await db.get(OCRJob, extraction.job_id)
    if not job:
        raise HTTPException(status_code=404, detail="ExtractedData not found")
    assert_same_org(extraction, current_user)
    assert_same_org(job, current_user)
    assert_owner(current_user, job.created_by_id)
    return extraction


async def get_owned_fill_job(db: AsyncSession, job_id: uuid.UUID, current_user: dict[str, Any]) -> FormFillJob:
    job = await db.get(FormFillJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Fill job not found")
    assert_same_org(job, current_user)
    assert_owner(current_user, job.created_by_id)
    return job
