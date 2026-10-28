"""
forms_workbench_routes.py — All /workbench/* route handlers.
Extracted from forms.py for maintainability.
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_roles
from app.models.enums import UserRole
from app.models.extracted_data import ExtractedData
from app.models.company import Company, CorrectionMemory, ERPMasterMapping, TrainingDataset
from app.models.workbench import WorkbenchDraft
from app.services.ownership_service import get_owned_ocr_job

from app.api.v1.routers.forms_workbench_helpers import (
    WorkbenchRequest,
    WorkbenchExportRequest,
    WorkbenchAutoFillRequest,
    WorkbenchMatchTrainingRequest,
    build_workbench_auto_fill,
    validate_workbench_payload,
    _normalize_workbench_payload,
    _workbench_export_payload,
    build_erp_workbench_payload,
    _workbench_document_type,
    normalize_workbench_key,
)
from app.schemas.correction import (
    WorkbenchCorrectionApplyRequest,
    WorkbenchCorrectionBulkCreate,
    WorkbenchCorrectionCreate,
)
from app.api.v1.routers.forms_export import (
    _build_workbench_excel,
    _build_workbench_pdf,
)

import io
from fastapi.responses import StreamingResponse

workbench_router = APIRouter()


# ── Workbench validate ────────────────────────────────────────────────────────

@workbench_router.post("/workbench/validate", summary="Validate Sales/Purchase workbench form")
async def validate_workbench_form(
    req: WorkbenchRequest,
    current_user: dict = Depends(require_roles(UserRole.OPERATOR, UserRole.REVIEWER)),
) -> dict:
    return validate_workbench_payload(req)


@workbench_router.post("/workbench/export-precheck", summary="Run export readiness and quality pre-check")
async def export_workbench_precheck(
    req,
    current_user: dict = Depends(require_roles(UserRole.OPERATOR, UserRole.REVIEWER)),
) -> dict[str, Any]:
    from app.services.export_precheck_engine import build_export_precheck
    from pydantic import BaseModel, Field

    class ExportPrecheckRequest(BaseModel):
        module: str
        document_type: Optional[str] = None
        form_data: dict[str, Any] = Field(default_factory=dict)
        item_rows: list = Field(default_factory=list)
        mapped_fields: dict[str, Any] = Field(default_factory=dict)
        resolved_masters: list = Field(default_factory=list)
        unresolved_masters: list = Field(default_factory=list)
        confidence_map: dict[str, Any] = Field(default_factory=dict)
        warnings: list = Field(default_factory=list)
        source_type: Optional[str] = None

    return build_export_precheck(req.model_dump())


@workbench_router.post("/workbench/auto-fill", summary="Map OCR truth into the Sales/Purchase workbench")
async def auto_fill_workbench(
    req: WorkbenchAutoFillRequest,
    current_user: dict = Depends(require_roles(UserRole.OPERATOR, UserRole.REVIEWER)),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    ocr_result = dict(req.ocr_result or {})
    if req.job_id:
        try:
            job_uuid = uuid.UUID(req.job_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid job_id UUID") from exc
        await get_owned_ocr_job(db, job_uuid, current_user)
        result = await db.execute(select(ExtractedData).where(ExtractedData.job_id == job_uuid))
        extracted = result.scalar_one_or_none()
        if extracted:
            restored = {
                "raw_text": extracted.raw_text or "",
                "extracted_fields": extracted.extracted_fields or {},
                "validated_fields": extracted.validated_fields or {},
                "canonical_fields": extracted.validated_fields or extracted.extracted_fields or {},
                "items": extracted.line_items or [],
                "line_items": extracted.line_items or [],
            }
            if isinstance(extracted.raw_ocr_json, dict):
                restored = {**extracted.raw_ocr_json, **restored}
            ocr_result = {**restored, **ocr_result}
    if not ocr_result:
        raise HTTPException(status_code=422, detail="Provide job_id or ocr_result")

    dataset: Optional[dict[str, Any]] = None
    training_match_info: Optional[dict[str, Any]] = None

    document_type = _workbench_document_type(ocr_result)
    raw_text = req.raw_text or ocr_result.get("raw_text") or ""

    if req.dataset_id:
        statement = select(TrainingDataset).where(
            TrainingDataset.id == str(req.dataset_id), TrainingDataset.is_active.is_(True)
        )
        if current_user.get("org_id"):
            statement = statement.where(TrainingDataset.org_id == str(current_user["org_id"]))
        result = await db.execute(statement)
        record = result.scalar_one_or_none()
        if record:
            dataset = {
                "id": str(record.id), "dataset_name": record.dataset_name,
                "mappings_json": record.mappings_json or [], "row_mappings_json": record.row_mappings_json or [],
            }
    else:
        from app.services.training_template_engine import match_training_dataset, THRESHOLD_APPLY
        company_info = {
            "company_name": ocr_result.get("company_name") or "",
            "vendor_name": ocr_result.get("vendor_name") or ocr_result.get("supplier_name") or "",
            "customer_name": ocr_result.get("customer_name") or ocr_result.get("buyer_name") or "",
            "gst_number": ocr_result.get("gst_number") or ocr_result.get("supplier_gstin") or ocr_result.get("buyer_gstin") or "",
        }
        match_res = await match_training_dataset(
            db=db,
            raw_text=raw_text,
            document_type=document_type,
            company_info=company_info,
            module=req.module,
            update_usage=False,
        )
        if match_res and match_res.get("score", 0.0) >= THRESHOLD_APPLY:
            record = match_res.get("dataset")
            if record:
                dataset = {
                    "id": str(record.id), "dataset_name": record.dataset_name,
                    "mappings_json": record.mappings_json or [], "row_mappings_json": record.row_mappings_json or [],
                }
                training_match_info = {
                    "score": match_res.get("score"),
                    "match_reason": match_res.get("match_reason"),
                    "apply_mode": match_res.get("apply_mode"),
                    "dataset_id": str(record.id),
                    "dataset_name": record.dataset_name,
                }

    corrections: list[dict[str, Any]] = []
    try:
        module_key = "purchase" if normalize_workbench_key(req.module).startswith("purchase") else "sales"
        stmt_corr = select(CorrectionMemory).where(
            CorrectionMemory.is_active.is_(True),
            CorrectionMemory.module == f"{module_key}_order",
        )
        if current_user.get("org_id"):
            stmt_corr = stmt_corr.where(CorrectionMemory.company_id.isnot(None))
        result_corr = await db.execute(stmt_corr)
        correction_rows = result_corr.scalars().all()
        for c in correction_rows:
            corrections.append({
                "source_ocr_key": c.source_ocr_key or c.field_name,
                "source_ocr_label": c.source_ocr_label or c.field_name,
                "normalized_source_key": c.normalized_source_key or normalize_workbench_key(c.field_name),
                "source_value_sample": c.source_value_sample or c.old_value or "",
                "target_erp_name": c.target_erp_name or c.field_name,
                "target_dom_id": c.target_dom_id or "",
                "corrected_value_sample": c.correct_value,
                "document_type": c.document_type or "",
                "company_name": c.company_name or "",
                "vendor_name": c.vendor_name or "",
                "customer_name": c.customer_name or "",
                "module": c.module or "",
                "is_active": c.is_active,
                "confidence": c.confidence or 1.0,
            })
    except Exception:
        corrections = []

    erp_mappings: list[Any] = []
    try:
        from app.api.v1.routers.companies import _master_scope_clause
        stmt_erp = select(ERPMasterMapping).where(
            _master_scope_clause(current_user),
            ERPMasterMapping.is_active.is_(True),
        )
        erp_result = await db.execute(stmt_erp)
        erp_mappings = list(erp_result.scalars().all())
    except Exception:
        erp_mappings = []

    response = build_workbench_auto_fill(req, ocr_result, dataset, corrections, erp_mappings)
    response["dataset_id"] = dataset.get("id") if dataset else req.dataset_id
    response["dataset_name"] = dataset.get("dataset_name") if dataset else None
    response["correction_applied_count"] = response.get("correction_applied_count", 0)
    response["correction_memory_count"] = len(corrections)
    response["training_match"] = training_match_info
    return response


@workbench_router.post("/workbench/match-training", summary="Find the best matching active training dataset using layout similarity")
async def match_workbench_training(
    req: WorkbenchMatchTrainingRequest,
    current_user: dict = Depends(require_roles(UserRole.OPERATOR, UserRole.REVIEWER)),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    from app.services.training_template_engine import match_training_dataset
    company_info = {
        "company_name": req.company_name or "",
        "vendor_name": req.vendor_name or "",
        "customer_name": req.customer_name or "",
        "gst_number": req.gst_number or "",
    }
    match_res = await match_training_dataset(
        db=db,
        raw_text=req.raw_text,
        document_type=req.document_type,
        company_info=company_info,
        module=req.module,
        update_usage=False,
    )
    if not match_res:
        return {"success": False, "dataset": None}

    record = match_res["dataset"]
    from app.api.v1.routers.companies import _dataset_to_dict
    return {
        "success": True,
        "dataset": _dataset_to_dict(record),
        "dataset_id": str(record.id),
        "dataset_name": record.dataset_name,
        "score": match_res["score"],
        "match_score": match_res["score"],
        "match_reason": match_res["match_reason"],
        "apply_mode": match_res["apply_mode"],
        "mapping_count": len(record.mappings_json or []),
        "row_mapping_count": len(record.row_mappings_json or []),
        "mappings_preview": (record.mappings_json or [])[:5],
    }


@workbench_router.post("/workbench/save-draft", summary="Save Sales/Purchase workbench draft")
async def save_workbench_draft(
    req: WorkbenchRequest,
    current_user: dict = Depends(require_roles(UserRole.OPERATOR, UserRole.REVIEWER)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    validation = validate_workbench_payload(req)
    module, fields, rows = _normalize_workbench_payload(req)
    try:
        user_id = uuid.UUID(str(current_user["user_id"]))
        org_id = uuid.UUID(str(current_user["org_id"])) if current_user.get("org_id") else None
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Invalid authenticated user") from exc
    draft = WorkbenchDraft(
        org_id=org_id,
        created_by_id=user_id,
        module=f"{module}_order",
        document_type=req.document_type,
        dataset_id=req.dataset_id,
        source_job_id=req.source_job_id,
        form_data=fields,
        item_rows=rows,
        validation_result=validation,
        metadata_json=req.metadata or {},
    )
    db.add(draft)
    await db.commit()
    await db.refresh(draft)
    return {
        "draft_id": str(draft.id),
        "module": draft.module,
        "status": "saved",
        "saved_at": draft.created_at.isoformat(),
        "field_count": len(fields),
        "row_count": len(rows),
    }


# ── Correction Learning Endpoints ─────────────────────────────────────────────

@workbench_router.post("/workbench/corrections", summary="Save a single correction memory entry")
async def save_workbench_correction(
    body: WorkbenchCorrectionCreate,
    current_user: dict = Depends(require_roles(UserRole.OPERATOR, UserRole.REVIEWER)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.services.source_trace import is_field_dynamic, is_field_reusable
    # 1. Enforce dynamic fields safety
    if is_field_dynamic(body.target_erp_name):
        raise HTTPException(
            status_code=400,
            detail="Dynamic transaction fields cannot be saved as reusable training defaults."
        )
    # 2. Enforce reusable fields explicit opt-in
    if is_field_reusable(body.target_erp_name):
        is_explicit = bool(body.metadata.get("is_reusable") or body.metadata.get("save_reusable"))
        if not is_explicit:
            raise HTTPException(
                status_code=400,
                detail="Reusable fields require explicit opt-in to be saved."
            )

    try:
        from app.services.workbench_correction_apply import build_workbench_correction_memory
        rule_suggestion = "manual_correction"
        if body.metadata.get("source"):
            rule_suggestion = str(body.metadata["source"])
        record = build_workbench_correction_memory(
            module=body.module,
            source_ocr_key=body.source_ocr_key,
            source_ocr_label=body.source_ocr_label,
            source_value_sample=body.source_value_sample,
            target_erp_name=body.target_erp_name,
            corrected_value=body.corrected_value_sample or "",
            document_type=body.document_type,
            company_name=body.company_name,
            vendor_name=body.vendor_name,
            customer_name=body.customer_name,
            target_dom_id=body.target_dom_id,
            confidence=body.confidence,
        )
        normalized_source_key = record["normalized_source_key"]
        target_erp_name = record["target_erp_name"]
        company_id = None
        try:
            if body.company_name:
                stmt_comp = select(Company).where(Company.company_name == body.company_name)
                r_comp = await db.execute(stmt_comp)
                company_row = r_comp.scalar_one_or_none()
                if company_row:
                    company_id = company_row.id
        except Exception:
            company_id = None
        corr = CorrectionMemory(
            company_id=company_id,
            field_name=normalized_source_key,
            old_value=body.source_value_sample,
            correct_value=body.corrected_value_sample or "",
            module=record["module"],
            document_type=record["document_type"],
            company_name=record["company_name"],
            vendor_name=record["vendor_name"],
            customer_name=record["customer_name"],
            source_ocr_key=record["source_ocr_key"],
            source_ocr_label=record["source_ocr_label"],
            normalized_source_key=normalized_source_key,
            source_value_sample=record["source_value_sample"],
            target_erp_name=target_erp_name,
            target_dom_id=record["target_dom_id"],
            target_label=record["target_label"],
            corrected_value_sample=record["corrected_value_sample"],
            confidence=record["confidence"],
            source=rule_suggestion,
            metadata_json=body.metadata,
            evidence_text=body.metadata.get("evidence_text", ""),
            rule_suggestion=rule_suggestion,
        )
        db.add(corr)
        await db.commit()
        await db.refresh(corr)
        return {
            "saved": True,
            "correction_id": str(corr.id),
            "normalized_source_key": normalized_source_key,
            "target_erp_name": target_erp_name,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to save correction: {exc}")


@workbench_router.post("/workbench/corrections/bulk", summary="Save multiple corrections at once")
async def save_workbench_corrections_bulk(
    body: WorkbenchCorrectionBulkCreate,
    current_user: dict = Depends(require_roles(UserRole.OPERATOR, UserRole.REVIEWER)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.services.source_trace import is_field_dynamic, is_field_reusable
    saved_ids: list[str] = []
    errors: list[dict[str, str]] = []
    company_cache: dict[str, uuid.UUID | None] = {}

    async def _get_company_id(name: str | None) -> uuid.UUID | None:
        if not name:
            return None
        if name in company_cache:
            return company_cache[name]
        try:
            stmt = select(Company).where(Company.company_name == name)
            r = await db.execute(stmt)
            row = r.scalar_one_or_none()
            company_cache[name] = row.id if row else None
            return company_cache[name]
        except Exception:
            company_cache[name] = None
            return None

    from app.services.workbench_correction_apply import build_workbench_correction_memory
    for index, item in enumerate(body.corrections):
        try:
            # 1. Enforce dynamic fields safety
            if is_field_dynamic(item.target_erp_name):
                raise ValueError("Dynamic transaction fields cannot be saved as reusable training defaults.")
            # 2. Enforce reusable fields explicit opt-in
            if is_field_reusable(item.target_erp_name):
                is_explicit = bool(item.metadata.get("is_reusable") or item.metadata.get("save_reusable"))
                if not is_explicit:
                    raise ValueError("Reusable fields require explicit opt-in to be saved.")

            record = build_workbench_correction_memory(
                module=item.module,
                source_ocr_key=item.source_ocr_key,
                source_ocr_label=item.source_ocr_label,
                source_value_sample=item.source_value_sample,
                target_erp_name=item.target_erp_name,
                corrected_value=item.corrected_value_sample or "",
                document_type=item.document_type,
                company_name=item.company_name,
                vendor_name=item.vendor_name,
                customer_name=item.customer_name,
                target_dom_id=item.target_dom_id,
                confidence=item.confidence,
            )
            company_id = await _get_company_id(item.company_name)
            corr = CorrectionMemory(
                company_id=company_id,
                field_name=record["normalized_source_key"],
                old_value=item.source_value_sample,
                correct_value=item.corrected_value_sample or "",
                module=record["module"],
                document_type=record["document_type"],
                company_name=record["company_name"],
                vendor_name=record["vendor_name"],
                customer_name=record["customer_name"],
                source_ocr_key=record["source_ocr_key"],
                source_ocr_label=record["source_ocr_label"],
                normalized_source_key=record["normalized_source_key"],
                source_value_sample=record["source_value_sample"],
                target_erp_name=record["target_erp_name"],
                target_dom_id=record["target_dom_id"],
                target_label=record["target_label"],
                corrected_value_sample=record["corrected_value_sample"],
                confidence=record["confidence"],
                source="manual_correction",
                metadata_json=item.metadata,
                evidence_text=item.metadata.get("evidence_text", ""),
                rule_suggestion="manual_correction",
            )
            db.add(corr)
            await db.flush()
            saved_ids.append(str(corr.id))
        except Exception as exc:
            errors.append({"index": str(index), "source_ocr_key": item.source_ocr_key, "error": str(exc)})
    await db.commit()
    return {
        "saved_count": len(saved_ids),
        "correction_ids": saved_ids,
        "errors": errors,
    }



@workbench_router.post("/workbench/corrections/apply", summary="Apply saved corrections to form_data")
async def apply_workbench_corrections_endpoint(
    body: WorkbenchCorrectionApplyRequest,
    current_user: dict = Depends(require_roles(UserRole.OPERATOR, UserRole.REVIEWER)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        module_key = "purchase" if normalize_workbench_key(body.module).startswith("purchase") else "sales"
        corrections: list[dict[str, Any]] = []
        try:
            stmt_corr = select(CorrectionMemory).where(
                CorrectionMemory.is_active.is_(True),
                CorrectionMemory.module == f"{module_key}_order",
            )
            result_corr = await db.execute(stmt_corr)
            correction_rows = result_corr.scalars().all()
            for c in correction_rows:
                corrections.append({
                    "source_ocr_key": c.source_ocr_key or c.field_name,
                    "source_ocr_label": c.source_ocr_label or c.field_name,
                    "normalized_source_key": c.normalized_source_key or normalize_workbench_key(c.field_name),
                    "source_value_sample": c.source_value_sample or c.old_value or "",
                    "target_erp_name": c.target_erp_name or c.field_name,
                    "target_dom_id": c.target_dom_id or "",
                    "corrected_value_sample": c.correct_value,
                    "document_type": c.document_type or "",
                    "company_name": c.company_name or "",
                    "vendor_name": c.vendor_name or "",
                    "customer_name": c.customer_name or "",
                    "module": c.module or "",
                    "is_active": c.is_active,
                    "confidence": c.confidence or 1.0,
                })
        except Exception:
            corrections = []
        from app.services.workbench_correction_apply import apply_workbench_corrections
        result = apply_workbench_corrections(
            module=module_key,
            document_type=body.document_type,
            company_name=body.company_name,
            vendor_name=body.vendor_name,
            customer_name=body.customer_name,
            ocr_fields=body.ocr_fields,
            form_data=body.form_data,
            item_rows=body.item_rows,
            corrections=corrections,
        )
        return {
            "form_data": result["form_data"],
            "item_rows": result["item_rows"],
            "applied_count": result["applied_count"],
            "applied_corrections": result["applied_corrections"],
            "skipped_corrections": result["skipped_corrections"],
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to apply corrections: {exc}")


@workbench_router.get("/workbench/corrections", summary="List saved corrections")
async def list_workbench_corrections(
    module: Optional[str] = None,
    document_type: Optional[str] = None,
    company_name: Optional[str] = None,
    vendor_name: Optional[str] = None,
    customer_name: Optional[str] = None,
    active: Optional[bool] = None,
    current_user: dict = Depends(require_roles(UserRole.OPERATOR, UserRole.REVIEWER)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        stmt = select(CorrectionMemory).order_by(CorrectionMemory.updated_at.desc().nulls_last())
        if module:
            stmt = stmt.where(CorrectionMemory.module == module)
        if document_type:
            stmt = stmt.where(CorrectionMemory.document_type == document_type)
        if company_name:
            stmt = stmt.where(CorrectionMemory.company_name == company_name)
        if vendor_name:
            stmt = stmt.where(CorrectionMemory.vendor_name == vendor_name)
        if customer_name:
            stmt = stmt.where(CorrectionMemory.customer_name == customer_name)
        if active is not None:
            stmt = stmt.where(CorrectionMemory.is_active == active)
        stmt = stmt.limit(200)
        result = await db.execute(stmt)
        rows = result.scalars().all()
        corrections_list = []
        for c in rows:
            corrections_list.append({
                "id": str(c.id),
                "module": c.module,
                "document_type": c.document_type,
                "company_name": c.company_name,
                "vendor_name": c.vendor_name,
                "customer_name": c.customer_name,
                "source_ocr_key": c.source_ocr_key,
                "source_ocr_label": c.source_ocr_label,
                "normalized_source_key": c.normalized_source_key,
                "source_value_sample": c.source_value_sample,
                "target_erp_name": c.target_erp_name,
                "target_dom_id": c.target_dom_id,
                "target_label": c.target_label,
                "corrected_value_sample": c.corrected_value_sample,
                "confidence": c.confidence,
                "apply_count": c.apply_count,
                "reject_count": c.reject_count,
                "is_active": c.is_active,
                "source": c.source,
                "created_at": c.created_at.isoformat() if c.created_at else None,
                "updated_at": c.updated_at.isoformat() if c.updated_at else None,
            })
        return {"corrections": corrections_list, "total": len(corrections_list)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to list corrections: {exc}")


@workbench_router.post("/workbench/corrections/{correction_id}/deactivate", summary="Deactivate a correction")
async def deactivate_workbench_correction(
    correction_id: str,
    current_user: dict = Depends(require_roles(UserRole.OPERATOR, UserRole.REVIEWER)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        corr_uuid = uuid.UUID(correction_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid correction_id UUID")
    stmt = select(CorrectionMemory).where(CorrectionMemory.id == corr_uuid)
    result = await db.execute(stmt)
    corr = result.scalar_one_or_none()
    if not corr:
        raise HTTPException(status_code=404, detail="Correction not found")
    corr.is_active = False
    await db.commit()
    return {"deactivated": True, "correction_id": correction_id}


# ── Workbench Export Routes ───────────────────────────────────────────────────

@workbench_router.post("/workbench/export/json", summary="Export validated workbench form as JSON")
async def export_workbench_json(
    req: WorkbenchExportRequest,
    current_user: dict = Depends(require_roles(UserRole.OPERATOR)),
) -> StreamingResponse:
    import json
    validation, fields, rows = _workbench_export_payload(req)
    payload = build_erp_workbench_payload(req, validation, fields, rows)
    data = json.dumps(payload, indent=2, default=str).encode("utf-8")
    filename = f"{validation['module']}-workbench-{uuid.uuid4().hex[:8]}.json"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@workbench_router.post("/workbench/export/excel", summary="Export validated workbench form as Excel")
async def export_workbench_excel(
    req: WorkbenchExportRequest,
    current_user: dict = Depends(require_roles(UserRole.OPERATOR)),
) -> StreamingResponse:
    validation, fields, rows = _workbench_export_payload(req)
    payload = build_erp_workbench_payload(req, validation, fields, rows)
    buf = _build_workbench_excel(payload)
    filename = f"{validation['module']}-workbench-{uuid.uuid4().hex[:8]}.xlsx"
    return StreamingResponse(
        io.BytesIO(buf),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@workbench_router.post("/workbench/export/pdf", summary="Export validated workbench form as PDF")
async def export_workbench_pdf(
    req: WorkbenchExportRequest,
    current_user: dict = Depends(require_roles(UserRole.OPERATOR)),
) -> StreamingResponse:
    validation, fields, rows = _workbench_export_payload(req)
    payload = build_erp_workbench_payload(req, validation, fields, rows)
    buf = _build_workbench_pdf(payload, req.title or f"{validation['module'].replace('_', ' ').title()}")
    filename = f"{validation['module']}-workbench-{uuid.uuid4().hex[:8]}.pdf"
    return StreamingResponse(
        io.BytesIO(buf),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
