"""
forms.py — Auto Company Form Fill router
POST /api/v1/forms/auto-fill     → extract fields from OCR job
POST /api/v1/forms/export/pdf    → generate PDF from filled fields
POST /api/v1/forms/export/excel  → generate Excel from filled fields

Workbench endpoints live in forms_workbench_routes.py.
Helper constants and business logic live in forms_workbench_helpers.py.
Export builders live in forms_export.py.
"""
from __future__ import annotations

import io
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import require_roles
from app.models.enums import LogEventType, LogLevel, UserRole
from app.models.extracted_data import ExtractedData
from app.services.company_form_mapper import (
    auto_fill, classify_document, flatten_item_rows_for_legacy_fields,
    map_sales_invoice_fields, missing_fields_for_doc, normalize_mapped_fields,
)
from app.services.ownership_service import get_owned_extracted_data, get_owned_ocr_job
from app.services.audit_service import write_audit_log
from app.services.gemini_service import gemini_enrich_missing_fields
from app.services.validation_service import validate_mapped_fields
from app.services.tabular_document_parser import parse_csv_document, parse_excel_document
from app.services.export_precheck_engine import build_export_precheck

# Re-export schemas and functions used by tests and other modules
from app.api.v1.routers.forms_workbench_helpers import (  # noqa: F401
    WorkbenchRequest,
    WorkbenchExportRequest,
    WorkbenchAutoFillRequest,
    WorkbenchMatchTrainingRequest,
    build_workbench_auto_fill,
    validate_workbench_payload,
    _normalize_workbench_payload,
    _workbench_export_payload,
    build_erp_workbench_payload,
    normalize_workbench_key,
    clean_workbench_value,
    normalize_dataset_mappings,
)
from app.api.v1.routers.forms_workbench_routes import (  # noqa: F401
    export_workbench_json,
    export_workbench_excel,
    export_workbench_pdf,
    save_workbench_draft,
    auto_fill_workbench,
    save_workbench_correction,
    list_workbench_corrections,
)
from app.api.v1.routers.forms_export import (  # noqa: F401
    _resolve_export_fields,
    write_export_audit,
    write_export_failure_audit,
)

from app.schemas.correction import (
    WorkbenchCorrectionApplyRequest,
    WorkbenchCorrectionBulkCreate,
    WorkbenchCorrectionCreate,
)

router = APIRouter(prefix="/forms", tags=["Company Form Fill"])
settings = get_settings()


# ── Schemas (local to this router) ────────────────────────────────────────────

from pydantic import BaseModel, Field


class AutoFillRequest(BaseModel):
    extracted_data_id: Optional[str] = None
    raw_text: Optional[str] = None
    doc_type: Optional[str] = "general"


class ExportRequest(BaseModel):
    fields: Dict[str, Any]
    title: Optional[str] = "Company Invoice Form"
    extracted_data_id: Optional[str] = None
    skip_validation: bool = False


class ExportPrecheckRequest(BaseModel):
    module: str
    document_type: Optional[str] = None
    form_data: Dict[str, Any] = Field(default_factory=dict)
    item_rows: list = Field(default_factory=list)
    mapped_fields: Dict[str, Any] = Field(default_factory=dict)
    resolved_masters: list = Field(default_factory=list)
    unresolved_masters: list = Field(default_factory=list)
    confidence_map: Dict[str, Any] = Field(default_factory=dict)
    warnings: list = Field(default_factory=list)
    source_type: Optional[str] = None


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/workbench/parse-tabular", summary="Parse Excel or CSV for the workbench")
async def parse_workbench_tabular(
    file: UploadFile = File(...),
    current_user: dict = Depends(require_roles(UserRole.OPERATOR, UserRole.REVIEWER)),
) -> dict[str, Any]:
    filename = file.filename or "upload"
    extension = Path(filename).suffix.lower()
    if extension == ".xls":
        raise HTTPException(status_code=415, detail="Legacy .xls is not supported. Please upload .xlsx or .csv.")
    if extension not in {".xlsx", ".csv"}:
        raise HTTPException(status_code=415, detail="Unsupported tabular file. Please upload .xlsx or .csv.")
    max_bytes = settings.MAX_FILE_SIZE_MB * 1024 * 1024
    data = await file.read(max_bytes + 1)
    if not data:
        raise HTTPException(status_code=400, detail="The uploaded Excel/CSV file is empty.")
    if len(data) > max_bytes:
        raise HTTPException(status_code=413, detail=f"File exceeds {settings.MAX_FILE_SIZE_MB} MB")
    try:
        result = parse_excel_document(data, filename) if extension == ".xlsx" else parse_csv_document(data, filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    result["filename"] = filename
    return result


@router.post("/auto-fill", summary="Auto-fill company form from OCR result")
async def auto_fill_form(
    req: AutoFillRequest,
    current_user: dict = Depends(require_roles(UserRole.OPERATOR, UserRole.REVIEWER)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    raw_text = (req.raw_text or "").strip()

    if not raw_text:
        if not req.extracted_data_id:
            raise HTTPException(status_code=422, detail="Provide extracted_data_id or raw_text")
        try:
            ext_id = uuid.UUID(req.extracted_data_id)
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid extracted_data_id UUID")

        extraction = await get_owned_extracted_data(db, ext_id, current_user)
        raw_text = extraction.raw_text or ""

    cls = classify_document(raw_text, filename="")
    requested_doc_type = (req.doc_type or "general").lower()
    if cls.get("mapper") == "sales" or requested_doc_type in {"sales", "invoice", "export_invoice", "commercial_invoice", "tax_invoice"}:
        result = map_sales_invoice_fields(raw_text)
        result = flatten_item_rows_for_legacy_fields(result, cls.get("doc_type") or requested_doc_type, raw_text)
    else:
        result = auto_fill(raw_text, doc_type=req.doc_type or "general")
    result["mapped_fields"] = normalize_mapped_fields(result.get("mapped_fields", {}))
    regex_fields = dict(result.get("mapped_fields", {}))

    # ── Early Company detection ──────────────────────────────────────────────
    detected_company_info: dict[str, Any] = {}
    try:
        from app.services.company_detector import detect_company
        det = await detect_company(raw_text, db)
        detected_company_info = {
            "company_id":    str(det.company_id) if det.company_id else None,
            "company_name":  det.company_name,
            "match_method":  det.match_method,
            "confidence":    det.confidence,
            "is_known":      det.is_known,
            "matched_value": det.matched_value,
        }
    except Exception as _det_exc:
        logger.warning("Early company detection failed in auto_fill_form: {}", _det_exc)

    # ── Training template matching & application ─────────────────────────────
    _training_matched = False
    _training_name: str | None = None
    _training_score: float = 0.0
    _training_id: str | None = None
    _training_applied_fields: list[str] = []
    _training_conflicts: list[dict[str, Any]] = []
    _training_match = None

    doc_type_for_training = (result.get("document_type") or cls.get("doc_type") or requested_doc_type or "general").lower()

    try:
        from app.services.training_template_engine import match_training_dataset, apply_training_to_mapped_fields, THRESHOLD_APPLY
        _training_match = await match_training_dataset(
            db,
            raw_text,
            doc_type_for_training,
            detected_company_info,
            doc_type_for_training,
            update_usage=True,
        )
        if _training_match and _training_match.get("score", 0.0) >= THRESHOLD_APPLY:
            result["raw_text"] = raw_text
            _apply_result = apply_training_to_mapped_fields(result, _training_match)
            result["mapped_fields"] = _apply_result.get("mapped_fields", result.get("mapped_fields", {}))
            _training_matched = True
            _training_name = _training_match.get("dataset_name") or _training_match.get("name")
            _training_score = float(_training_match.get("score", 0.0))
            _training_id = str(_training_match.get("dataset_id") or _training_match.get("id") or "")
            _training_applied_fields = _apply_result.get("training_applied_fields", [])
            _training_conflicts = _apply_result.get("training_conflicts", [])
            result["mapped_fields"] = normalize_mapped_fields(result.get("mapped_fields", {}))

            logger.info(
                "TrainingTemplate applied in auto_fill_form: matched={} score={:.2f} applied={}",
                _training_name, _training_score, _training_applied_fields,
            )
    except Exception as _te:
        logger.warning("TrainingTemplate failed/skipped in auto_fill_form: {}", _te)

    if settings.AI_FALLBACK_ENABLED:
        result = await gemini_enrich_missing_fields(
            result,
            raw_text,
            result.get("document_type") or result.get("doc_type") or requested_doc_type,
            missing_fields=result.get("missing_fields", []),
        )
    result["mapped_fields"] = normalize_mapped_fields(result.get("mapped_fields", {}))
    doc_type = (result.get("document_type") or result.get("doc_type") or cls.get("doc_type") or req.doc_type or "general")
    result["document_type"] = str(doc_type).lower()
    result["missing_fields"] = missing_fields_for_doc(result["mapped_fields"], result["document_type"])
    validation = validate_mapped_fields(result["mapped_fields"], raw_text=raw_text, doc_type=result["document_type"])

    # ── Field Level Tracing ──────────────────────────────────────────────────
    field_level_trace = []
    try:
        from app.services.training_template_engine import generate_field_level_trace
        field_level_trace = generate_field_level_trace(
            doc_type=result["document_type"],
            raw_mapped_fields=regex_fields,
            validation_result=validation,
            matched_dataset_info=_training_match,
            applied_fields=_training_applied_fields,
            conflicts=_training_conflicts,
        )
    except Exception as _trace_exc:
        logger.warning("Failed to generate field level trace: {}", _trace_exc)

    result["regex_fields"] = regex_fields
    result["gemini_fields"] = result.get("gemini_fields", {})
    item_data = result["mapped_fields"].get("items") or result["mapped_fields"].get("item_rows") or {}
    result["items"] = item_data.get("value", []) if isinstance(item_data, dict) else (item_data or [])
    result["validation_errors"] = validation["errors"]
    result["validation_warnings"] = validation["warnings"]
    result["confidence"] = {
        k: float(v.get("confidence", 0.0) or 0.0)
        for k, v in result["mapped_fields"].items()
        if isinstance(v, dict)
    }
    if raw_text and not any(
        k != "document_type" and (v.get("value") if isinstance(v, dict) else v)
        for k, v in result["mapped_fields"].items()
    ):
        result["weak_mapping"] = True
        result["message"] = "OCR text detected but mapping failed"
        result["raw_text_preview"] = raw_text[:500]
    result["raw_text"] = raw_text[:3000]
    if "Mock OCR" in raw_text:
        result["warning"] = "Real OCR engine not installed."
        result["install_hint"] = "pip install paddlepaddle paddleocr"

    result["field_level_trace"] = field_level_trace
    result["training_template_matched"] = _training_matched
    result["training_template_name"] = _training_name
    result["training_match_score"] = _training_score
    result["training_template_id"] = _training_id or None
    result["training_applied_fields"] = _training_applied_fields
    result["training_conflicts"] = _training_conflicts
    result["detected_company"] = detected_company_info
    result["company_id"] = detected_company_info.get("company_id")

    logger.info(
        "AutoFill: user={} document_type={} text_len={} preview={} keys={} missing={} training_matched={} score={}",
        current_user["user_id"],
        result.get("document_type"),
        len(raw_text),
        raw_text[:500].replace("\n", " ") if raw_text else "",
        sorted(result.get("mapped_fields", {}).keys()),
        result.get("missing_fields", []),
        _training_matched,
        _training_score,
    )
    return result


# ── Register workbench + export sub-routers ───────────────────────────────────

from app.api.v1.routers.forms_workbench_routes import workbench_router  # noqa: E402
from app.api.v1.routers.forms_export import export_router  # noqa: E402

router.include_router(workbench_router)
router.include_router(export_router)
