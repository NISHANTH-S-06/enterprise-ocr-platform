"""Company master data API — Phase 1.

Endpoints:
  GET/POST  /companies
  GET/PUT   /companies/{id}
  GET/POST  /companies/{id}/templates
  GET/POST  /companies/{id}/templates/{tid}/fields
  POST      /companies/{id}/corrections
  POST      /documents/{doc_id}/quality-report
  GET       /documents/{doc_id}/quality-report
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.company import (
    Company, CompanyTemplate, FieldMapping,
    CorrectionMemory, DocumentQualityReport,
)
from app.schemas.company import (
    CompanyCreate, CompanyRead, CompanyUpdate,
    CompanyTemplateCreate, CompanyTemplateRead,
    FieldMappingCreate, FieldMappingRead,
    CorrectionMemoryCreate, CorrectionMemoryRead,
    DocumentQualityReportCreate, DocumentQualityReportRead,
)

router = APIRouter(prefix="/companies", tags=["companies"])
doc_router = APIRouter(prefix="/documents", tags=["documents"])
settings = get_settings()


class TrainingMappingSaveRequest(BaseModel):
    module: str
    document_type: str
    company_id: str | None = None
    template_id: str | None = None
    mapping_version: str | None = None
    fields: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    missing: list[Any] = []
    low_confidence: list[Any] = []
    warnings: list[Any] = []


# ── helpers ─────────────────────────────────────────────────────────────────

async def _get_company_or_404(db: AsyncSession, company_id: uuid.UUID) -> Company:
    row = await db.get(Company, company_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Company not found")
    return row


def _mapping_store_dir():
    root = settings.output_path / "training_mappings"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _mapping_key(current_user: dict, module: str, document_type: str) -> str:
    org = str(current_user.get("org_id") or "orgless")
    user = str(current_user.get("user_id") or "anonymous")
    safe_module = "".join(ch if ch.isalnum() else "_" for ch in str(module or "general").lower()).strip("_") or "general"
    safe_doc = "".join(ch if ch.isalnum() else "_" for ch in str(document_type or "general").lower()).strip("_") or "general"
    return f"{org}_{user}_{safe_module}_{safe_doc}.json"


@router.post("/templates/mappings/save", summary="Save OCR training mapping")
async def save_training_mapping(
    body: TrainingMappingSaveRequest,
    current_user: dict = Depends(get_current_user),
) -> dict:
    if not body.module or not body.document_type:
        raise HTTPException(status_code=422, detail="module and document_type are required")
    payload = body.model_dump()
    payload["saved_at"] = datetime.now(timezone.utc).isoformat()
    payload["saved_by_user_id"] = current_user.get("user_id")
    payload["org_id"] = current_user.get("org_id")
    path = _mapping_store_dir() / _mapping_key(current_user, body.module, body.document_type)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return {
        "success": True,
        "storage": "backend_file",
        "module": body.module,
        "document_type": body.document_type,
        "saved_at": payload["saved_at"],
        "field_count": len(body.fields or []),
    }


@router.get("/templates/mappings", summary="Load OCR training mapping")
async def load_training_mapping(
    module: str = Query(...),
    document_type: str = Query(...),
    current_user: dict = Depends(get_current_user),
) -> dict:
    path = _mapping_store_dir() / _mapping_key(current_user, module, document_type)
    if not path.exists():
        return {"success": False, "found": False, "module": module, "document_type": document_type}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="Stored mapping is invalid JSON")
    return {"success": True, "found": True, "mapping": payload}


# ── companies ───────────────────────────────────────────────────────────────

@router.get("", response_model=List[CompanyRead])
async def list_companies(
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    result = await db.execute(select(Company).order_by(Company.company_name))
    return result.scalars().all()


@router.post("", response_model=CompanyRead, status_code=status.HTTP_201_CREATED)
async def create_company(
    body: CompanyCreate,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    obj = Company(**body.model_dump())
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


@router.get("/{company_id}", response_model=CompanyRead)
async def get_company(
    company_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    return await _get_company_or_404(db, company_id)


@router.put("/{company_id}", response_model=CompanyRead)
async def update_company(
    company_id: uuid.UUID,
    body: CompanyUpdate,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    obj = await _get_company_or_404(db, company_id)
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(obj, k, v)
    await db.commit()
    await db.refresh(obj)
    return obj


# ── templates ───────────────────────────────────────────────────────────────

@router.get("/{company_id}/templates", response_model=List[CompanyTemplateRead])
async def list_templates(
    company_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    await _get_company_or_404(db, company_id)
    result = await db.execute(
        select(CompanyTemplate)
        .where(CompanyTemplate.company_id == company_id)
        .order_by(CompanyTemplate.template_name)
    )
    return result.scalars().all()


@router.post("/{company_id}/templates", response_model=CompanyTemplateRead, status_code=201)
async def create_template(
    company_id: uuid.UUID,
    body: CompanyTemplateCreate,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    await _get_company_or_404(db, company_id)
    data = body.model_dump()
    data["company_id"] = company_id
    obj = CompanyTemplate(**data)
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


# ── field mappings ───────────────────────────────────────────────────────────

@router.get("/{company_id}/templates/{template_id}/fields", response_model=List[FieldMappingRead])
async def list_fields(
    company_id: uuid.UUID,
    template_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    result = await db.execute(
        select(FieldMapping)
        .where(FieldMapping.template_id == template_id)
        .order_by(FieldMapping.priority, FieldMapping.field_name)
    )
    return result.scalars().all()


@router.post("/{company_id}/templates/{template_id}/fields", response_model=FieldMappingRead, status_code=201)
async def create_field(
    company_id: uuid.UUID,
    template_id: uuid.UUID,
    body: FieldMappingCreate,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    data = body.model_dump()
    data["template_id"] = template_id
    obj = FieldMapping(**data)
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


# ── correction memory ────────────────────────────────────────────────────────

@router.post("/{company_id}/corrections", response_model=CorrectionMemoryRead, status_code=201)
async def record_correction(
    company_id: uuid.UUID,
    body: CorrectionMemoryCreate,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    await _get_company_or_404(db, company_id)
    data = body.model_dump()
    data["company_id"] = company_id
    obj = CorrectionMemory(**data)
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


# ── document quality report ──────────────────────────────────────────────────

@doc_router.post("/{document_id}/quality-report", response_model=DocumentQualityReportRead, status_code=201)
async def create_quality_report(
    document_id: str,
    body: DocumentQualityReportCreate,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    data = body.model_dump()
    data["document_id"] = document_id
    obj = DocumentQualityReport(**data)
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


@doc_router.get("/{document_id}/quality-report", response_model=DocumentQualityReportRead)
async def get_quality_report(
    document_id: str,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    result = await db.execute(
        select(DocumentQualityReport)
        .where(DocumentQualityReport.document_id == document_id)
        .order_by(DocumentQualityReport.created_at.desc())
        .limit(1)
    )
    obj = result.scalar_one_or_none()
    if not obj:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Quality report not found")
    return obj
