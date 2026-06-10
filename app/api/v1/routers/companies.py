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
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.company import (
    Company, CompanyTemplate, FieldMapping,
    TrainingDataset, ERPMasterMapping, CorrectionMemory, DocumentQualityReport,
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


class TrainingDatasetRequest(BaseModel):
    id: uuid.UUID | None = None
    dataset_name: str
    module: str
    document_type: str
    company_name: str | None = None
    vendor_name: str | None = None
    customer_name: str | None = None
    is_active: bool = True
    mappings_json: list[dict[str, Any]] = Field(default_factory=list)
    row_mappings_json: list[dict[str, Any]] = Field(default_factory=list)
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class TrainingDatasetBestMatchRequest(BaseModel):
    module: str
    document_type: str | None = None
    company_name: str | None = None
    vendor_name: str | None = None
    customer_name: str | None = None
    detected_fields: dict[str, Any] = Field(default_factory=dict)


class ERPMasterMappingRequest(BaseModel):
    master_type: str
    detected_text: str
    erp_id: str
    erp_code: str | None = None
    erp_name: str | None = None
    module: str | None = None
    company_name: str | None = None
    vendor_name: str | None = None
    customer_name: str | None = None
    is_active: bool = True
    confidence: float | None = None
    source: str = "manual"
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class ERPMasterBulkRequest(BaseModel):
    mappings: list[ERPMasterMappingRequest] = Field(default_factory=list)


class ERPMasterResolveRequest(BaseModel):
    module: str
    fields: dict[str, Any] = Field(default_factory=dict)
    item_rows: list[dict[str, Any]] = Field(default_factory=list)
    company_name: str | None = None
    vendor_name: str | None = None
    customer_name: str | None = None


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


def _dataset_org_id(current_user: dict) -> str | None:
    value = current_user.get("org_id")
    return str(value) if value else None


def _clean_dataset_text(value: Any) -> str:
    return str(value or "").strip()


def _normalized_dataset_text(value: Any) -> str:
    return " ".join(_clean_dataset_text(value).casefold().split())


ERP_MASTER_TYPES = {"customer", "supplier", "product", "tax_group", "customer_site", "supplier_site"}


def normalize_erp_master_text(value: Any) -> str:
    text = _clean_dataset_text(value).casefold()
    return " ".join("".join(ch if ch.isalnum() or ch in {"-", "/"} else " " for ch in text).split())


def _master_value(value: Any) -> str:
    while isinstance(value, dict):
        value = value.get("value", value.get("text", value.get("raw_value", "")))
    return _clean_dataset_text(value)


def _master_to_dict(mapping: ERPMasterMapping) -> dict[str, Any]:
    return {
        "id": str(mapping.id), "master_type": mapping.master_type,
        "detected_text": mapping.detected_text, "normalized_text": mapping.normalized_text,
        "erp_id": mapping.erp_id, "erp_code": mapping.erp_code, "erp_name": mapping.erp_name,
        "module": mapping.module, "company_name": mapping.company_name,
        "vendor_name": mapping.vendor_name, "customer_name": mapping.customer_name,
        "is_active": mapping.is_active, "confidence": mapping.confidence,
        "source": mapping.source, "metadata_json": mapping.metadata_json or {},
        "created_at": mapping.created_at.isoformat() if mapping.created_at else None,
        "updated_at": mapping.updated_at.isoformat() if mapping.updated_at else None,
    }


def resolve_erp_master_values(
    fields: dict[str, Any], item_rows: list[dict[str, Any]], mappings: list[Any], module: str,
) -> dict[str, Any]:
    resolved_fields = {str(k): _master_value(v) for k, v in (fields or {}).items()}
    resolved_rows = [{str(k): _master_value(v) for k, v in row.items()} for row in (item_rows or []) if isinstance(row, dict)]
    lookup: dict[tuple[str, str], Any] = {}
    def attr(mapping: Any, key: str, default: Any = None) -> Any:
        return mapping.get(key, default) if isinstance(mapping, dict) else getattr(mapping, key, default)
    for mapping in mappings:
        if not attr(mapping, "is_active", True):
            continue
        mapping_module = attr(mapping, "module")
        if mapping_module and mapping_module != module:
            continue
        lookup[(attr(mapping, "master_type"), attr(mapping, "normalized_text") or normalize_erp_master_text(attr(mapping, "detected_text")))] = mapping
    unresolved: list[dict[str, str]] = []
    resolved_count = 0

    def apply(master_type: str, detected: str, target: dict[str, str], field_key: str, row_index: int | None = None) -> None:
        nonlocal resolved_count
        if not detected or target.get(field_key):
            return
        mapping = lookup.get((master_type, normalize_erp_master_text(detected)))
        if mapping:
            target[field_key] = attr(mapping, "erp_id")
            resolved_count += 1
        else:
            visible_key = f"rows[{row_index}].{field_key}" if row_index is not None else field_key
            unresolved.append({"master_type": master_type, "detected_text": detected, "field_key": visible_key,
                               "reason_code": "ERP_master_id_missing", "reason": "Needs ERP master selection"})

    if module == "purchase_order":
        apply("supplier", resolved_fields.get("supplier_name") or resolved_fields.get("suppliername") or resolved_fields.get("vendor_name"), resolved_fields, "supplier_id")
        apply("supplier_site", resolved_fields.get("supplier_site") or resolved_fields.get("ship_to_address"), resolved_fields, "suppliersite_id")
    else:
        customer = resolved_fields.get("customer_name") or resolved_fields.get("buyer_name")
        apply("customer", customer, resolved_fields, "customer_id")
        apply("customer", customer, resolved_fields, "ship_to_customer_id")
        apply("customer_site", resolved_fields.get("ship_to_address"), resolved_fields, "customer_site_id")
    for index, row in enumerate(resolved_rows):
        product = row.get("product") or row.get("product_description") or row.get("description")
        tax_group = row.get("bulk_tax_group") or row.get("tax_group") or row.get("gst_rate")
        apply("product", product, row, "product_id", index)
        apply("tax_group", tax_group, row, "tax_group_id", index)
    unique = list({(item["master_type"], item["detected_text"], item["field_key"]): item for item in unresolved}.values())
    return {"resolved_fields": resolved_fields, "resolved_rows": resolved_rows, "resolved_count": resolved_count, "unresolved": unique}


def _master_scope_clause(current_user: dict):
    org_id = _dataset_org_id(current_user)
    return ERPMasterMapping.org_id == org_id if org_id else ERPMasterMapping.org_id.is_(None)


def _validate_master_request(body: ERPMasterMappingRequest) -> None:
    if body.master_type not in ERP_MASTER_TYPES:
        raise HTTPException(status_code=422, detail="Unsupported ERP master type")
    if not normalize_erp_master_text(body.detected_text) or not _clean_dataset_text(body.erp_id):
        raise HTTPException(status_code=422, detail="detected_text and erp_id are required")
    if body.source not in {"manual", "imported", "dataset", "correction"}:
        raise HTTPException(status_code=422, detail="Unsupported ERP master mapping source")


async def _upsert_master_mapping(body: ERPMasterMappingRequest, db: AsyncSession, current_user: dict) -> tuple[ERPMasterMapping, bool]:
    _validate_master_request(body)
    normalized = normalize_erp_master_text(body.detected_text)
    module = _clean_dataset_text(body.module) or None
    company = _clean_dataset_text(body.company_name) or None
    result = await db.execute(select(ERPMasterMapping).where(
        _master_scope_clause(current_user), ERPMasterMapping.master_type == body.master_type,
        ERPMasterMapping.normalized_text == normalized, ERPMasterMapping.module == module,
        ERPMasterMapping.company_name == company,
    ))
    mapping = result.scalar_one_or_none()
    created = mapping is None
    if created:
        mapping = ERPMasterMapping(org_id=_dataset_org_id(current_user))
        db.add(mapping)
    mapping.master_type = body.master_type
    mapping.detected_text = _clean_dataset_text(body.detected_text)
    mapping.normalized_text = normalized
    mapping.erp_id = _clean_dataset_text(body.erp_id)
    mapping.erp_code = _clean_dataset_text(body.erp_code) or None
    mapping.erp_name = _clean_dataset_text(body.erp_name) or None
    mapping.module = module
    mapping.company_name = company
    mapping.vendor_name = _clean_dataset_text(body.vendor_name) or None
    mapping.customer_name = _clean_dataset_text(body.customer_name) or None
    mapping.is_active = body.is_active
    mapping.confidence = body.confidence
    mapping.source = body.source
    mapping.metadata_json = body.metadata_json
    return mapping, created


def _dataset_to_dict(dataset: TrainingDataset) -> dict[str, Any]:
    return {
        "id": str(dataset.id),
        "dataset_name": dataset.dataset_name,
        "module": dataset.module,
        "document_type": dataset.document_type,
        "company_name": dataset.company_name,
        "vendor_name": dataset.vendor_name,
        "customer_name": dataset.customer_name,
        "is_active": dataset.is_active,
        "version": dataset.version,
        "mappings_json": dataset.mappings_json or [],
        "row_mappings_json": dataset.row_mappings_json or [],
        "metadata_json": dataset.metadata_json or {},
        "created_at": dataset.created_at.isoformat() if dataset.created_at else None,
        "updated_at": dataset.updated_at.isoformat() if dataset.updated_at else None,
    }


def _detected_identity(body: TrainingDatasetBestMatchRequest, key: str) -> str:
    direct = getattr(body, key, None)
    if direct:
        return _clean_dataset_text(direct)
    value = body.detected_fields.get(key)
    if isinstance(value, dict):
        value = value.get("value") or value.get("text")
    return _clean_dataset_text(value)


def _score_training_dataset(
    dataset: TrainingDataset,
    body: TrainingDatasetBestMatchRequest,
) -> tuple[int, str]:
    if _normalized_dataset_text(dataset.module) != _normalized_dataset_text(body.module):
        return 0, "module_mismatch"
    requested_doc = _normalized_dataset_text(body.document_type)
    dataset_doc = _normalized_dataset_text(dataset.document_type)
    doc_matches = bool(requested_doc and requested_doc == dataset_doc)
    identities = {
        "company": (_detected_identity(body, "company_name"), dataset.company_name),
        "vendor": (_detected_identity(body, "vendor_name"), dataset.vendor_name),
        "customer": (_detected_identity(body, "customer_name"), dataset.customer_name),
    }
    exact = [name for name, (requested, stored) in identities.items()
             if requested and stored and _normalized_dataset_text(requested) == _normalized_dataset_text(stored)]
    if doc_matches and exact:
        return 100, f"exact_module_document_{'_'.join(exact)}"
    if doc_matches:
        return 60, "module_document_type"
    return 30, "module_only"


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


def _dataset_scope_clause(current_user: dict):
    org_id = _dataset_org_id(current_user)
    return TrainingDataset.org_id == org_id if org_id else TrainingDataset.org_id.is_(None)


def _validate_dataset_module(module: str) -> str:
    normalized = _normalized_dataset_text(module).replace(" ", "_")
    if normalized not in {"sales_order", "purchase_order"}:
        raise HTTPException(status_code=422, detail="module must be sales_order or purchase_order")
    return normalized


async def _get_training_dataset_or_404(
    db: AsyncSession,
    dataset_id: uuid.UUID,
    current_user: dict,
) -> TrainingDataset:
    result = await db.execute(
        select(TrainingDataset).where(
            TrainingDataset.id == str(dataset_id),
            _dataset_scope_clause(current_user),
        )
    )
    dataset = result.scalar_one_or_none()
    if not dataset:
        raise HTTPException(status_code=404, detail="Training dataset not found")
    return dataset


def _apply_dataset_request(dataset: TrainingDataset, body: TrainingDatasetRequest) -> None:
    dataset.dataset_name = _clean_dataset_text(body.dataset_name)
    dataset.module = _validate_dataset_module(body.module)
    dataset.document_type = _clean_dataset_text(body.document_type)
    dataset.company_name = _clean_dataset_text(body.company_name) or None
    dataset.vendor_name = _clean_dataset_text(body.vendor_name) or None
    dataset.customer_name = _clean_dataset_text(body.customer_name) or None
    dataset.is_active = body.is_active
    dataset.mappings_json = body.mappings_json
    dataset.row_mappings_json = body.row_mappings_json
    dataset.metadata_json = body.metadata_json


@router.post("/training-datasets", status_code=status.HTTP_201_CREATED)
async def save_training_dataset(
    body: TrainingDatasetRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
) -> dict:
    if not _clean_dataset_text(body.dataset_name) or not _clean_dataset_text(body.document_type):
        raise HTTPException(status_code=422, detail="dataset_name and document_type are required")
    module = _validate_dataset_module(body.module)
    dataset = None
    if body.id:
        dataset = await _get_training_dataset_or_404(db, body.id, current_user)
    else:
        result = await db.execute(
            select(TrainingDataset).where(
                _dataset_scope_clause(current_user),
                TrainingDataset.dataset_name == _clean_dataset_text(body.dataset_name),
                TrainingDataset.module == module,
                TrainingDataset.document_type == _clean_dataset_text(body.document_type),
            )
        )
        dataset = result.scalar_one_or_none()
    created = dataset is None
    if created:
        dataset = TrainingDataset(org_id=_dataset_org_id(current_user))
        db.add(dataset)
    else:
        dataset.version = (dataset.version or 1) + 1
    _apply_dataset_request(dataset, body)
    await db.commit()
    await db.refresh(dataset)
    return {"success": True, "created": created, "dataset": _dataset_to_dict(dataset)}


@router.get("/training-datasets")
async def list_training_datasets(
    module: str | None = Query(None),
    document_type: str | None = Query(None),
    company_name: str | None = Query(None),
    vendor_name: str | None = Query(None),
    customer_name: str | None = Query(None),
    active_only: bool = Query(True),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
) -> dict:
    clauses = [_dataset_scope_clause(current_user)]
    if module:
        clauses.append(TrainingDataset.module == _validate_dataset_module(module))
    if document_type:
        clauses.append(TrainingDataset.document_type == _clean_dataset_text(document_type))
    if company_name:
        clauses.append(TrainingDataset.company_name == _clean_dataset_text(company_name))
    if vendor_name:
        clauses.append(TrainingDataset.vendor_name == _clean_dataset_text(vendor_name))
    if customer_name:
        clauses.append(TrainingDataset.customer_name == _clean_dataset_text(customer_name))
    if active_only:
        clauses.append(TrainingDataset.is_active.is_(True))
    result = await db.execute(
        select(TrainingDataset).where(*clauses).order_by(
            TrainingDataset.updated_at.desc(), TrainingDataset.dataset_name,
        )
    )
    datasets = result.scalars().all()
    return {"success": True, "datasets": [_dataset_to_dict(dataset) for dataset in datasets]}


@router.post("/training-datasets/best-match")
async def best_match_training_dataset(
    body: TrainingDatasetBestMatchRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
) -> dict:
    module = _validate_dataset_module(body.module)
    result = await db.execute(
        select(TrainingDataset).where(
            _dataset_scope_clause(current_user),
            TrainingDataset.module == module,
            TrainingDataset.is_active.is_(True),
        ).order_by(TrainingDataset.updated_at.desc())
    )
    candidates = result.scalars().all()
    ranked = sorted(
        ((_score_training_dataset(dataset, body), dataset) for dataset in candidates),
        key=lambda item: (item[0][0], item[1].updated_at or item[1].created_at),
        reverse=True,
    )
    if not ranked or ranked[0][0][0] <= 0:
        return {"dataset": None, "match_score": 0, "match_reason": "no_dataset_found"}
    (score, reason), dataset = ranked[0]
    return {"dataset": _dataset_to_dict(dataset), "match_score": score, "match_reason": reason}


@router.get("/training-datasets/{dataset_id}")
async def get_training_dataset(
    dataset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
) -> dict:
    dataset = await _get_training_dataset_or_404(db, dataset_id, current_user)
    return {"success": True, "dataset": _dataset_to_dict(dataset)}


@router.put("/training-datasets/{dataset_id}")
async def update_training_dataset(
    dataset_id: uuid.UUID,
    body: TrainingDatasetRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
) -> dict:
    dataset = await _get_training_dataset_or_404(db, dataset_id, current_user)
    _apply_dataset_request(dataset, body)
    dataset.version = (dataset.version or 1) + 1
    await db.commit()
    await db.refresh(dataset)
    return {"success": True, "dataset": _dataset_to_dict(dataset)}


@router.post("/training-datasets/{dataset_id}/deactivate")
async def deactivate_training_dataset(
    dataset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
) -> dict:
    dataset = await _get_training_dataset_or_404(db, dataset_id, current_user)
    dataset.is_active = False
    dataset.version = (dataset.version or 1) + 1
    await db.commit()
    await db.refresh(dataset)
    return {"success": True, "dataset": _dataset_to_dict(dataset)}


@router.post("/erp-master-mappings", status_code=status.HTTP_201_CREATED)
async def save_erp_master_mapping(body: ERPMasterMappingRequest, db: AsyncSession = Depends(get_db), current_user: dict = Depends(get_current_user)) -> dict:
    mapping, created = await _upsert_master_mapping(body, db, current_user)
    await db.commit()
    await db.refresh(mapping)
    return {"success": True, "created": created, "mapping": _master_to_dict(mapping)}


@router.get("/erp-master-mappings")
async def list_erp_master_mappings(
    master_type: str | None = Query(None), detected_text: str | None = Query(None), module: str | None = Query(None),
    company_name: str | None = Query(None), active_only: bool = Query(True), db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
) -> dict:
    clauses = [_master_scope_clause(current_user)]
    if master_type:
        clauses.append(ERPMasterMapping.master_type == master_type)
    if detected_text:
        clauses.append(ERPMasterMapping.normalized_text == normalize_erp_master_text(detected_text))
    if module:
        clauses.append(ERPMasterMapping.module == _clean_dataset_text(module))
    if company_name:
        clauses.append(ERPMasterMapping.company_name == _clean_dataset_text(company_name))
    if active_only:
        clauses.append(ERPMasterMapping.is_active.is_(True))
    result = await db.execute(select(ERPMasterMapping).where(*clauses).order_by(ERPMasterMapping.updated_at.desc()))
    return {"success": True, "mappings": [_master_to_dict(item) for item in result.scalars().all()]}


@router.post("/erp-master-mappings/resolve")
async def resolve_erp_master_mappings(body: ERPMasterResolveRequest, db: AsyncSession = Depends(get_db), current_user: dict = Depends(get_current_user)) -> dict:
    result = await db.execute(select(ERPMasterMapping).where(_master_scope_clause(current_user), ERPMasterMapping.is_active.is_(True)))
    return resolve_erp_master_values(body.fields, body.item_rows, result.scalars().all(), body.module)


@router.post("/erp-master-mappings/bulk")
async def bulk_save_erp_master_mappings(body: ERPMasterBulkRequest, db: AsyncSession = Depends(get_db), current_user: dict = Depends(get_current_user)) -> dict:
    saved = []
    created_count = 0
    for item in body.mappings:
        mapping, created = await _upsert_master_mapping(item, db, current_user)
        saved.append(mapping)
        created_count += int(created)
    await db.commit()
    for mapping in saved:
        await db.refresh(mapping)
    return {"success": True, "saved_count": len(saved), "created_count": created_count, "mappings": [_master_to_dict(item) for item in saved]}


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
