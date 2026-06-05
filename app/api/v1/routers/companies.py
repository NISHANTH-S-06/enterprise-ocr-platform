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

import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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


# ── helpers ─────────────────────────────────────────────────────────────────

async def _get_company_or_404(db: AsyncSession, company_id: uuid.UUID) -> Company:
    row = await db.get(Company, company_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Company not found")
    return row


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
