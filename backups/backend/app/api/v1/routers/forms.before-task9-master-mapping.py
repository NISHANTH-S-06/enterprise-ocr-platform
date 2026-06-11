"""
forms.py — Auto Company Form Fill router
POST /api/v1/forms/auto-fill     → extract fields from OCR job
POST /api/v1/forms/export/pdf    → generate PDF from filled fields
POST /api/v1/forms/export/excel  → generate Excel from filled fields
"""
from __future__ import annotations

import io
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import require_roles
from app.models.enums import LogEventType, LogLevel, UserRole
from app.models.extracted_data import ExtractedData
from app.models.workbench import WorkbenchDraft
from app.services.company_form_mapper import auto_fill, classify_document, flatten_item_rows_for_legacy_fields, map_sales_invoice_fields, missing_fields_for_doc, normalize_mapped_fields
from app.services.ownership_service import get_owned_extracted_data
from app.services.audit_service import write_audit_log
from app.services.gemini_service import gemini_enrich_missing_fields
from app.services.validation_service import validate_mapped_fields

router = APIRouter(prefix="/forms", tags=["Company Form Fill"])
settings = get_settings()


# ── Schemas ───────────────────────────────────────────────────────────────────

class AutoFillRequest(BaseModel):
    extracted_data_id: Optional[str] = None
    raw_text: Optional[str] = None
    doc_type: Optional[str] = "general"   # P3: allow caller to specify doc type


class ExportRequest(BaseModel):
    fields: Dict[str, Any]
    title: Optional[str] = "Company Invoice Form"
    extracted_data_id: Optional[str] = None   # preferred — loads validated_fields from DB
    skip_validation: bool = False              # dev override only


class WorkbenchRequest(BaseModel):
    module: str
    document_type: Optional[str] = None
    dataset_id: Optional[str] = None
    source_job_id: Optional[str] = None
    fields: Dict[str, Any] = Field(default_factory=dict)
    rows: List[Dict[str, Any]] = Field(default_factory=list)
    form_data: Dict[str, Any] = Field(default_factory=dict)
    item_rows: List[Dict[str, Any]] = Field(default_factory=list)
    validation_result: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    ocr_fields: Dict[str, Any] = Field(default_factory=dict)
    ocr_rows: List[Dict[str, Any]] = Field(default_factory=list)
    mappings_json: List[Dict[str, Any]] = Field(default_factory=list)
    row_mappings_json: List[Dict[str, Any]] = Field(default_factory=list)
    selected_dataset: Dict[str, Any] = Field(default_factory=dict)
    title: Optional[str] = None


class WorkbenchExportRequest(WorkbenchRequest):
    pass


# ── Routes ────────────────────────────────────────────────────────────────────

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
    result["raw_text"] = raw_text[:3000]   # cap for frontend preview
    if "Mock OCR" in raw_text:
        result["warning"] = "Real OCR engine not installed."
        result["install_hint"] = "pip install paddlepaddle paddleocr"
    logger.info(
        "AutoFill: user={} document_type={} text_len={} preview={} keys={} missing={}",
        current_user["user_id"],
        result.get("document_type"),
        len(raw_text),
        raw_text[:500].replace("\n", " ") if raw_text else "",
        sorted(result.get("mapped_fields", {}).keys()),
        result.get("missing_fields", []),
    )
    return result


def _wb_value(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("value", value.get("text", value.get("raw_value", "")))
        if isinstance(value, dict):
            return _wb_value(value)
    return str(value or "").strip()


def _wb_number(value: Any) -> float | None:
    text = _wb_value(value).replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _wb_key(value: Any) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _wb_mapped_value(source: dict[str, str], key: str) -> str:
    if key in source and source[key]:
        return source[key]
    normalized = _wb_key(key)
    return next((value for source_key, value in source.items() if _wb_key(source_key) == normalized and value), "")


def _normalize_workbench_payload(req: WorkbenchRequest) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    module = "purchase" if str(req.module or "").lower().startswith("purchase") else "sales"
    source_fields = req.form_data or req.fields or {}
    source_rows = req.item_rows or req.rows or []
    fields = {str(k): _wb_value(v) for k, v in source_fields.items()}
    dataset = req.selected_dataset or {}
    dataset_mappings = dataset.get("mappings_json") or req.mappings_json or req.metadata.get("mappings_json", [])
    raw_fields = req.ocr_fields or req.metadata.get("ocr_fields", {})
    normalized_raw_fields = {str(k): _wb_value(v) for k, v in raw_fields.items()} if isinstance(raw_fields, dict) else {}
    for mapping in dataset_mappings:
        if not isinstance(mapping, dict):
            continue
        source_key = str(mapping.get("ocr_key") or mapping.get("source_field") or "").strip()
        target_key = str(mapping.get("target_field") or mapping.get("erp_field") or "").strip()
        source_value = _wb_mapped_value(normalized_raw_fields, source_key) or _wb_mapped_value(fields, source_key)
        if source_key and target_key and source_value:
            fields[target_key] = source_value
    rows: list[dict[str, Any]] = []
    dataset_row_mappings = dataset.get("row_mappings_json") or req.row_mappings_json or req.metadata.get("row_mappings_json", [])
    raw_rows = req.ocr_rows or req.metadata.get("ocr_rows", [])
    row_count = max(len(source_rows), len(raw_rows))
    for index in range(row_count):
        row = source_rows[index] if index < len(source_rows) else {}
        if not isinstance(row, dict):
            row = {}
        clean = {str(k): _wb_value(v) for k, v in row.items()}
        raw_row = raw_rows[index] if index < len(raw_rows) and isinstance(raw_rows[index], dict) else row
        raw_clean = {str(k): _wb_value(v) for k, v in raw_row.items()}
        for mapping in dataset_row_mappings:
            if not isinstance(mapping, dict):
                continue
            source_key = str(mapping.get("ocr_key") or mapping.get("source_field") or "").strip()
            target_key = str(mapping.get("target_field") or mapping.get("erp_field") or "").strip()
            source_value = _wb_mapped_value(raw_clean, source_key) or _wb_mapped_value(clean, source_key)
            if source_key and target_key and source_value:
                clean[target_key] = source_value
        if any(v for k, v in clean.items() if k != "row_index"):
            rows.append(clean)
    return module, fields, rows


def validate_workbench_payload(req: WorkbenchRequest) -> dict[str, Any]:
    module, fields, rows = _normalize_workbench_payload(req)
    document_type = req.document_type or ("purchase_order" if module == "purchase" else "sales_order")
    warnings: list[str] = []
    blocked: list[str] = []
    blocker_reasons: list[dict[str, str]] = []

    def block(field: str, reason_code: str, message: str) -> None:
        blocked.append(field)
        blocker_reasons.append({"field": field, "reason_code": reason_code, "message": message})

    if module == "purchase":
        required = {
            "po_number": "PO Number",
            "po_date": "PO Date",
            "supplier_id": "Supplier",
            "delivery_date": "Delivery Date",
            "po_grand_total": "PO Grand Total",
        }
        total_key = "po_grand_total"
        tax_key = "po_tax_total"
    else:
        required = {
            "sales_order_date": "Sales Order Date",
            "customer_id": "Customer",
            "bill_to_address": "Bill To Address",
            "ship_to_address": "Ship To Address",
            "delivery_date": "Delivery Date",
            "order_total": "Order Total",
        }
        total_key = "order_total"
        tax_key = "order_tax"

    required_missing = [key for key in required if not fields.get(key)]
    for key in required_missing:
        visible_name = fields.get("supplier_name") if key == "supplier_id" else fields.get("customer_name") if key == "customer_id" else ""
        if visible_name:
            block(key, "ERP_master_id_missing", f"Needs ERP master selection for {required[key].lower()}")
        else:
            block(key, "truly_absent_from_ocr", f"Required field missing: {required[key]}")
    if module == "purchase" and fields.get("supplier_name") and not fields.get("supplier_id"):
        warnings.append("Needs ERP master selection for supplier")
    elif module == "sales" and fields.get("customer_name") and not fields.get("customer_id"):
        warnings.append("Needs ERP master selection for customer")
    if not rows:
        block("item_rows", "truly_absent_from_ocr", "Required item rows are missing")

    line_total_sum = 0.0
    tax_total_sum = 0.0
    valid_rows = 0
    for index, row in enumerate(rows):
        qty = _wb_number(row.get("bulk_qty") or row.get("qty"))
        price = _wb_number(row.get("bulk_unit_price") or row.get("unit_price"))
        line_total = _wb_number(row.get("bulk_line_total") or row.get("line_total") or row.get("amount"))
        tax_amount = _wb_number(row.get("bulk_tax_amount") or row.get("tax_amount")) or 0.0
        if qty is None or qty <= 0:
            block(f"rows[{index}].qty", "canonical_but_validation_blocked", f"Row {index + 1}: quantity must be greater than zero")
        if price is None or price < 0:
            block(f"rows[{index}].unit_price", "canonical_but_validation_blocked", f"Row {index + 1}: unit price is invalid")
        if line_total is None or line_total <= 0:
            block(f"rows[{index}].line_total", "canonical_but_validation_blocked", f"Row {index + 1}: line total must be greater than zero")
        else:
            line_total_sum += line_total
            valid_rows += 1
        tax_total_sum += tax_amount
        product_text = row.get("product") or row.get("description")
        if product_text and not row.get("product_id"):
            block(f"rows[{index}].product_id", "ERP_master_id_missing", f"Row {index + 1}: Needs ERP master selection for product")
            warnings.append(f"Row {index + 1}: Needs ERP master selection for product")
        tax_group_text = row.get("bulk_tax_group") or row.get("tax_group")
        if tax_group_text and not row.get("tax_group_id"):
            block(f"rows[{index}].tax_group_id", "ERP_master_id_missing", f"Row {index + 1}: Needs ERP master selection for tax group")
            warnings.append(f"Row {index + 1}: Needs ERP master selection for tax group")
        if qty is not None and price is not None and line_total is not None:
            discount = _wb_number(row.get("bulk_discount_amount") or row.get("discount_amount")) or 0.0
            expected = max(qty * price - discount + tax_amount, 0.0)
            if abs(expected - line_total) > max(1.0, line_total * 0.02):
                warnings.append(f"Row {index + 1}: qty x price calculation differs from line total")

    submitted_total = _wb_number(fields.get(total_key))
    totals_check = {
        "submitted_total": submitted_total,
        "calculated_total": round(line_total_sum, 2),
        "submitted_tax": _wb_number(fields.get(tax_key)),
        "calculated_tax": round(tax_total_sum, 2),
        "mismatch": False,
    }
    if submitted_total is not None and valid_rows and abs(submitted_total - line_total_sum) > 1.0:
        totals_check["mismatch"] = True
        warnings.append("Submitted total and calculated item total mismatch")

    low_confidence = [
        key for key, value in (req.fields or {}).items()
        if isinstance(value, dict) and value.get("confidence") is not None and float(value.get("confidence") or 0) < 0.5
    ]
    warnings.extend(f"Low confidence field: {field}" for field in low_confidence)

    blocked = list(dict.fromkeys(blocked))
    blocker_reasons = list({(item["field"], item["reason_code"]): item for item in blocker_reasons}.values())
    required_missing = list(dict.fromkeys(required_missing))
    export_allowed = not blocked
    return {
        "success": True,
        "module": module,
        "document_type": document_type,
        "validation_status": "READY" if export_allowed and not warnings else ("BLOCKED" if blocked else "NEEDS_REVIEW"),
        "export_allowed": export_allowed,
        "blocked": not export_allowed,
        "required_missing": required_missing,
        "blocked_fields": blocked,
        "blocker_reasons": blocker_reasons,
        "warnings": list(dict.fromkeys(warnings)),
        "message": "Export allowed." if export_allowed else "Export blocked. Please complete required fields.",
        "normalized_fields": fields,
        "line_items": rows,
        "totals_check": totals_check,
    }


@router.post("/workbench/validate", summary="Validate Sales/Purchase workbench form")
async def validate_workbench_form(
    req: WorkbenchRequest,
    current_user: dict = Depends(require_roles(UserRole.OPERATOR, UserRole.REVIEWER)),
) -> dict:
    return validate_workbench_payload(req)


@router.post("/workbench/save-draft", summary="Save Sales/Purchase workbench draft")
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


def _workbench_export_payload(req: WorkbenchExportRequest) -> tuple[dict[str, Any], dict[str, str], list[dict[str, Any]]]:
    validation = validate_workbench_payload(req)
    if not validation["export_allowed"]:
        raise HTTPException(
            status_code=422,
            detail={
                "export_allowed": False,
                "blocked": True,
                "message": "Export blocked. Please complete required fields.",
                "blocked_fields": validation["blocked_fields"],
                "required_missing": validation["required_missing"],
                "warnings": validation["warnings"],
            },
        )
    return validation, validation["normalized_fields"], validation["line_items"]


SALES_HEADER_KEYS = (
    "sales_order_no", "sales_order_date", "order_type_id", "customer_type_id",
    "customer_id", "ship_to_customer_id", "bill_to_address", "ship_to_address",
    "delivery_date", "customer_po_number", "currency_code_id", "payment_term_id",
    "freight_terms_id", "delivery_terms_id", "remarks", "order_tax", "order_total",
    "qty_total",
)
PURCHASE_HEADER_KEYS = (
    "po_number", "po_date", "po_type", "supplier_id", "suppliername", "suppliersite_id",
    "delivery_date", "payment_term_id", "freight_terms_id", "freight_carrier_id",
    "delivery_terms_id", "project_id", "currency", "supplier_reference_no",
    "bill_to_address", "ship_to_address", "transport_charges", "unloading_charges",
    "insurance_charges", "packing_charges", "po_tax_total", "po_grand_total",
    "round_off", "remarks",
)
SALES_LINE_KEYS = (
    "line_no", "product", "product_id", "description", "uom_code_id", "qty",
    "unit_price", "discount_percentage", "discount_amount", "hsn_code", "tax_group_id",
    "tax_amount", "line_total", "delivery_date",
)
PURCHASE_LINE_KEYS = (
    "line_no", "product", "product_id", "part_no", "description", "uom_code_id", "qty",
    "unit_price", "discount_percentage", "discount_amount", "hsn_code", "tax_group_id",
    "tax_amount", "line_sub_total", "line_total", "promised_date", "comments",
)


def _pick(source: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = _wb_value(source.get(key))
        if value:
            return value
    return ""


def _erp_line(row: dict[str, Any], module: str) -> dict[str, str]:
    aliases = {
        "line_no": ("line_no",), "product": ("product",), "product_id": ("product_id",),
        "part_no": ("part_no", "supplier_part_no"), "description": ("description", "product_description"),
        "uom_code_id": ("uom_code_id", "bulk_uom_code", "uom"), "qty": ("qty", "bulk_qty"),
        "unit_price": ("unit_price", "bulk_unit_price"),
        "discount_percentage": ("discount_percentage", "bulk_discount_percentage"),
        "discount_amount": ("discount_amount", "bulk_discount_amount"),
        "hsn_code": ("hsn_code", "bulk_hsn_code"),
        "tax_group_id": ("tax_group_id",), "tax_amount": ("tax_amount", "bulk_tax_amount"),
        "line_sub_total": ("line_sub_total", "bulk_line_sub_total"),
        "line_total": ("line_total", "bulk_line_total"),
        "delivery_date": ("delivery_date", "bulk_delivery_date"),
        "promised_date": ("promised_date",), "comments": ("comments",),
    }
    keys = PURCHASE_LINE_KEYS if module == "purchase" else SALES_LINE_KEYS
    return {key: _pick(row, *aliases.get(key, (key,))) for key in keys}


def build_erp_workbench_payload(
    req: WorkbenchExportRequest,
    validation: dict[str, Any],
    fields: dict[str, str],
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    module = validation["module"]
    if module == "purchase":
        header_aliases = {
            "suppliername": ("suppliername", "supplier_name"),
            "suppliersite_id": ("suppliersite_id", "supplier_site"),
            "payment_term_id": ("payment_term_id",), "freight_terms_id": ("freight_terms_id", "freight_term"),
            "freight_carrier_id": ("freight_carrier_id", "freight_carrier"),
            "delivery_terms_id": ("delivery_terms_id", "delivery_term"),
        }
        header_keys = PURCHASE_HEADER_KEYS
    else:
        header_aliases = {
            "currency_code_id": ("currency_code_id", "invoice_currency"),
            "payment_term_id": ("payment_term_id", "ar_payment_term_id"),
            "freight_terms_id": ("freight_terms_id", "freight_term"),
            "delivery_terms_id": ("delivery_terms_id", "delivery_name"),
        }
        header_keys = SALES_HEADER_KEYS
    header = {key: _pick(fields, *header_aliases.get(key, (key,))) for key in header_keys}
    dataset = {
        "id": req.dataset_id,
        "name": req.metadata.get("dataset_name"),
        "match_score": req.metadata.get("dataset_match_score"),
        "match_reason": req.metadata.get("dataset_match_reason"),
        "header_mapped_count": req.metadata.get("dataset_header_mapped_count", 0),
        "row_mapped_count": req.metadata.get("dataset_row_mapped_count", 0),
        "fallback_mapped_count": req.metadata.get("fallback_mapped_count", 0),
    }
    metadata = {key: value for key, value in (req.metadata or {}).items() if key not in {"raw_text", "full_text", "text", "extracted_text"}}
    metadata.update({"document_type": validation["document_type"], "source_job_id": req.source_job_id, "exported_at": datetime.now(timezone.utc).isoformat()})
    return {
        "module": f"{module}_order",
        "header": header,
        "lines": [_erp_line(row, module) for row in rows],
        "validation": validation,
        "dataset": dataset,
        "metadata": metadata,
    }


@router.post("/workbench/export/json", summary="Export validated workbench form as JSON")
async def export_workbench_json(
    req: WorkbenchExportRequest,
    current_user: dict = Depends(require_roles(UserRole.OPERATOR)),
) -> StreamingResponse:
    validation, fields, rows = _workbench_export_payload(req)
    payload = build_erp_workbench_payload(req, validation, fields, rows)
    data = json.dumps(payload, indent=2, default=str).encode("utf-8")
    filename = f"{validation['module']}-workbench-{uuid.uuid4().hex[:8]}.json"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/workbench/export/excel", summary="Export validated workbench form as Excel")
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


@router.post("/workbench/export/pdf", summary="Export validated workbench form as PDF")
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


@router.post("/export/pdf", summary="Export filled form as PDF")
async def export_pdf(
    req: ExportRequest,
    request: Request,
    current_user: dict = Depends(require_roles(UserRole.OPERATOR)),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    try:
        fields = await _resolve_export_fields(req, db, current_user)
    except HTTPException as exc:
        await write_export_failure_audit(db, req, request, current_user, "pdf", exc)
        await db.commit()
        raise
    buf = _build_pdf(fields, req.title or "Company Form")
    filename = f"company-form-{uuid.uuid4().hex[:8]}.pdf"
    await write_export_audit(db, req, request, current_user, "pdf", filename, len(fields), len(buf))
    return StreamingResponse(
        io.BytesIO(buf),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/export/excel", summary="Export filled form as Excel")
async def export_excel(
    req: ExportRequest,
    request: Request,
    current_user: dict = Depends(require_roles(UserRole.OPERATOR)),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    try:
        fields = await _resolve_export_fields(req, db, current_user)
    except HTTPException as exc:
        await write_export_failure_audit(db, req, request, current_user, "excel", exc)
        await db.commit()
        raise
    buf = _build_excel(fields, req.title or "Company Form")
    filename = f"company-form-{uuid.uuid4().hex[:8]}.xlsx"
    await write_export_audit(db, req, request, current_user, "excel", filename, len(fields), len(buf))
    return StreamingResponse(
        io.BytesIO(buf),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


async def _resolve_export_fields(req: ExportRequest, db, current_user: dict) -> Dict[str, Any]:
    """Task 3+4: Prefer DB validated_fields; fall back to client fields with server validation."""
    # Prefer DB validated_fields when extracted_data_id provided
    if req.extracted_data_id and not req.skip_validation:
        try:
            ext_id = uuid.UUID(req.extracted_data_id)
            extraction = await get_owned_extracted_data(db, ext_id, current_user)
            if extraction.validated_fields:
                # Return flat {field: value} from validated_fields
                return {
                    k: (v["value"] if isinstance(v, dict) else str(v))
                    for k, v in extraction.validated_fields.items()
                }
        except ValueError:
            pass  # fall through to client fields

    # Server-side validate client-submitted fields
    client_fields = normalize_mapped_fields({k: {"value": v, "confidence": 0.7, "source": "manual"} for k, v in req.fields.items() if v})
    validation = validate_mapped_fields(client_fields, raw_text="", doc_type="general")

    if not req.skip_validation and validation["errors"]:
        raise HTTPException(
            status_code=422,
            detail={"message": "Export blocked: validation errors", "errors": validation["errors"]},
        )

    # Return validated flat fields
    return {
        k: (v["value"] if isinstance(v, dict) else str(v))
        for k, v in validation["validated_fields"].items()
    } or req.fields  # last resort: use raw client fields


async def write_export_audit(
    db: AsyncSession,
    req: ExportRequest,
    request: Request,
    current_user: dict,
    export_format: str,
    filename: str,
    field_count: int,
    byte_count: int,
) -> None:
    await write_audit_log(
        db,
        action=f"export.{export_format}",
        event_type=LogEventType.DATA_EXPORT,
        status="success",
        user_id=current_user.get("user_id"),
        org_id=current_user.get("org_id"),
        resource_type="extracted_data" if req.extracted_data_id else "export",
        resource_id=req.extracted_data_id or filename,
        message=f"{export_format.upper()} export generated",
        metadata={
            "format": export_format,
            "filename": filename,
            "field_count": field_count,
            "byte_count": byte_count,
        },
        request=request,
    )


async def write_export_failure_audit(
    db: AsyncSession,
    req: ExportRequest,
    request: Request,
    current_user: dict,
    export_format: str,
    exc: HTTPException,
) -> None:
    await write_audit_log(
        db,
        action="validation.failure",
        event_type=LogEventType.EXTRACTION,
        status="failure",
        user_id=current_user.get("user_id"),
        org_id=current_user.get("org_id"),
        resource_type="extracted_data" if req.extracted_data_id else "export",
        resource_id=req.extracted_data_id or export_format,
        level=LogLevel.WARNING,
        message="Export validation failed",
        metadata={"format": export_format, "status_code": exc.status_code, "detail": exc.detail},
        error=str(exc.detail),
        request=request,
    )


# ── Export builders ───────────────────────────────────────────────────────────

def _build_workbench_excel(payload: dict[str, Any]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    workbook = Workbook()
    header_sheet = workbook.active
    header_sheet.title = "Header"
    header_sheet.append(["ERP Field", "Value"])
    for cell in header_sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="4F46E5")
    for key, value in payload["header"].items():
        header_sheet.append([key, str(value)[:4000]])
    header_sheet.column_dimensions["A"].width = 30
    header_sheet.column_dimensions["B"].width = 70
    for row in header_sheet.iter_rows():
        row[1].alignment = Alignment(wrap_text=True, vertical="top")

    line_sheet = workbook.create_sheet("Lines")
    line_keys = list(payload["lines"][0].keys()) if payload["lines"] else []
    if line_keys:
        line_sheet.append(line_keys)
        for cell in line_sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="4F46E5")
        for line in payload["lines"]:
            line_sheet.append([str(line.get(key, ""))[:4000] for key in line_keys])
        for column in line_sheet.columns:
            line_sheet.column_dimensions[column[0].column_letter].width = 18

    validation_sheet = workbook.create_sheet("Validation")
    validation_sheet.append(["Property", "Value"])
    validation_rows = {
        "status": payload["validation"].get("validation_status"),
        "export_allowed": payload["validation"].get("export_allowed"),
        "blocked_fields": ", ".join(payload["validation"].get("blocked_fields", [])),
        "warnings": " | ".join(payload["validation"].get("warnings", [])),
        "dataset_id": payload["dataset"].get("id"),
        "dataset_name": payload["dataset"].get("name"),
        "dataset_match_reason": payload["dataset"].get("match_reason"),
    }
    for key, value in validation_rows.items():
        validation_sheet.append([key, str(value or "")[:4000]])
    validation_sheet.column_dimensions["A"].width = 28
    validation_sheet.column_dimensions["B"].width = 80

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _build_workbench_pdf(payload: dict[str, Any], title: str) -> bytes:
    from xml.sax.saxutils import escape

    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

        buffer = io.BytesIO()
        document = SimpleDocTemplate(buffer, pagesize=landscape(A4), leftMargin=24, rightMargin=24, topMargin=24, bottomMargin=24)
        styles = getSampleStyleSheet()
        story = [Paragraph(escape(title), styles["Title"]), Spacer(1, 8)]
        dataset_name = payload["dataset"].get("name") or "Generic mapping"
        story.append(Paragraph(f"Dataset: {escape(str(dataset_name))}", styles["Normal"]))
        story.append(Paragraph(f"Validation: {escape(str(payload['validation'].get('validation_status', '')))}", styles["Normal"]))
        story.append(Spacer(1, 8))
        header_rows = [[Paragraph("Field", styles["BodyText"]), Paragraph("Value", styles["BodyText"])]]
        for key, value in payload["header"].items():
            header_rows.append([Paragraph(escape(key), styles["BodyText"]), Paragraph(escape(str(value)[:1000]), styles["BodyText"])])
        header_table = Table(header_rows, colWidths=[150, 600], repeatRows=1)
        header_table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E0E7FF")), ("GRID", (0, 0), (-1, -1), 0.4, colors.grey), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        story.extend([header_table, Spacer(1, 10)])
        if payload["lines"]:
            keys = list(payload["lines"][0].keys())
            line_rows = [[Paragraph(escape(key), styles["BodyText"]) for key in keys]]
            for line in payload["lines"]:
                line_rows.append([Paragraph(escape(str(line.get(key, ""))[:300]), styles["BodyText"]) for key in keys])
            line_table = Table(line_rows, repeatRows=1)
            line_table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E0E7FF")), ("GRID", (0, 0), (-1, -1), 0.3, colors.grey), ("FONTSIZE", (0, 0), (-1, -1), 6), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
            story.extend([Paragraph("Item Lines", styles["Heading2"]), line_table])
        document.build(story)
        return buffer.getvalue()
    except ImportError:
        fallback_fields = dict(payload["header"])
        fallback_fields["dataset"] = payload["dataset"].get("name") or "Generic mapping"
        fallback_fields["validation"] = payload["validation"].get("validation_status")
        fallback_fields["items"] = payload["lines"]
        return _pdf_fallback(fallback_fields, title)


def _build_pdf(fields: Dict[str, str], title: str) -> bytes:
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.lib import colors
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        from reportlab.lib.enums import TA_CENTER, TA_LEFT

        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4,
                                leftMargin=18*mm, rightMargin=18*mm,
                                topMargin=18*mm, bottomMargin=18*mm)
        styles = getSampleStyleSheet()
        brand = colors.HexColor("#6c63ff")
        dark  = colors.HexColor("#1a1d27")

        h1 = ParagraphStyle("H1", parent=styles["Heading1"], textColor=brand,
                             fontSize=18, spaceAfter=4, alignment=TA_CENTER)
        sub = ParagraphStyle("SUB", parent=styles["Normal"], textColor=colors.grey,
                             fontSize=9, spaceAfter=10, alignment=TA_CENTER)
        lbl = ParagraphStyle("LBL", parent=styles["Normal"], textColor=colors.grey,
                             fontSize=8, fontName="Helvetica-Bold")
        val = ParagraphStyle("VAL", parent=styles["Normal"], fontSize=10,
                             textColor=dark)

        story = [
            Paragraph(title, h1),
            Paragraph("Generated by Aline X OCR Platform", sub),
            Spacer(1, 6*mm),
        ]

        LABELS = {
            "invoice_no": "Invoice #", "po_no": "PO Number", "po_date": "PO Date",
            "gstin": "GSTIN", "buyer_gstin": "Buyer GSTIN", "vendor_gstin": "Vendor GSTIN",
            "address": "Address", "hsn_code": "HSN/SAC", "rate": "Rate",
            "taxable_value": "Taxable Value", "cgst": "CGST", "sgst": "SGST",
            "igst": "IGST", "total_tax": "Total Tax", "amount_in_words": "Amount in Words",
            "bank_name": "Bank Name", "account_no": "Account Number", "ifsc": "IFSC",
            "document_type": "Document Type",
            "vendor_name": "Vendor Name", "customer_name": "Customer Name",
            "buyer_name": "Buyer Name", "consignee": "Consignee",
            "consignee_name": "Consignee Name",
            "invoice_number": "Invoice #", "bill_number": "Bill #",
            "purchase_order_number": "PO Number", "po_number": "PO Number",
            "invoice_date": "Invoice Date", "po_date": "PO Date",
            "grn_number": "GRN Number", "grn_date": "GRN Date",
            "due_date": "Due Date", "gst_number": "GST Number",
            "pan_number": "PAN Number", "phone": "Phone", "email": "Email",
            "billing_address": "Billing Address", "shipping_address": "Shipping Address",
            "item_name": "Item Name", "item_description": "Item Description",
            "quantity": "Quantity", "unit_price": "Unit Price",
            "subtotal": "Subtotal", "tax_amount": "Tax Amount",
            "discount": "Discount", "total_amount": "Total Amount",
            "grand_total": "Grand Total",
            "payment_status": "Payment Status", "notes": "Notes",
        }

        rows = []
        EXCLUDE_KEYS = {"item_rows", "items", "raw_text", "full_text", "text", "extracted_text", "raw_text_preview"}
        for key, label in LABELS.items():
            v = fields.get(key, "")
            if v:
                val_str = str(v)
                if len(val_str) > 1000:
                    val_str = val_str[:997] + "..."
                rows.append([Paragraph(label, lbl), Paragraph(val_str, val)])
        for key, v in fields.items():
            if v and key not in LABELS and key not in EXCLUDE_KEYS:
                val_str = str(v)
                if len(val_str) > 1000:
                    val_str = val_str[:997] + "..."
                rows.append([Paragraph(key.replace("_", " ").title(), lbl), Paragraph(val_str, val)])

        if rows:
            t = Table(rows, colWidths=[55*mm, 110*mm])
            t.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f0efff")),
                ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, colors.HexColor("#fafafa")]),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#e0e0ee")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]))
            story.append(t)
        elif not (fields.get("items") or fields.get("item_rows")):
            story.append(Paragraph("No fields provided.", styles["Normal"]))
        item_rows = fields.get("items") or fields.get("item_rows") or []
        if isinstance(item_rows, list) and item_rows:
            headers = ["Sno", "Order No", "Group", "Article", "Colour", "HSN/SAC", "Type", "Size", "UOM", "Qty", "Price", "Amount", "Total", "Act"]
            keys = ["sno", "order_no", "group", "article", "colour", "hsn_sac", "type", "size", "uom", "qty", "price", "amount", "total", "act"]
            item_table = [headers] + [[str(row.get(k, "")) if isinstance(row, dict) else "" for k in keys] for row in item_rows]
            story.append(Spacer(1, 6*mm))
            it = Table(item_table, repeatRows=1)
            it.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0efff")),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#e0e0ee")),
                ("FONTSIZE", (0, 0), (-1, -1), 6),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]))
            story.append(it)

        doc.build(story)
        return buf.getvalue()
    except ImportError:
        return _pdf_fallback(fields, title)


def _pdf_fallback(fields: Dict[str, str], title: str) -> bytes:
    """Minimal PDF if reportlab not available."""
    lines = [f"  {k}: {v}" for k, v in fields.items() if v]
    body = "\n".join(lines)
    content = f"Company Form — {title}\n\n{body}\n"
    # Bare-bones valid PDF
    stream = content.encode()
    pdf = (
        b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
        b"/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
        b"4 0 obj<</Length " + str(len(stream) + 20).encode() + b">>\nstream\nBT /F1 12 Tf 40 750 Td\n"
        + stream + b"\nET\nendstream\nendobj\n"
        b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
        b"xref\n0 6\ntrailer<</Size 6/Root 1 0 R>>\n%%EOF\n"
    )
    return pdf


def _build_excel(fields: Dict[str, str], title: str) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    wb = Workbook()
    ws = wb.active
    ws.title = "Company Form"

    brand_fill = PatternFill("solid", fgColor="6C63FF")
    header_fill = PatternFill("solid", fgColor="EEEDff")
    thin = Border(
        left=Side(style="thin", color="CCCCEE"),
        right=Side(style="thin", color="CCCCEE"),
        top=Side(style="thin", color="CCCCEE"),
        bottom=Side(style="thin", color="CCCCEE"),
    )

    # Title row
    ws.merge_cells("A1:B1")
    ws["A1"] = title
    ws["A1"].font = Font(bold=True, size=14, color="FFFFFF")
    ws["A1"].fill = brand_fill
    ws["A1"].alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 28

    # Sub-header
    ws.merge_cells("A2:B2")
    ws["A2"] = "Generated by Aline X OCR Platform"
    ws["A2"].font = Font(italic=True, size=9, color="888888")
    ws["A2"].alignment = Alignment(horizontal="center")

    ws.column_dimensions["A"].width = 26
    ws.column_dimensions["B"].width = 48

    LABELS = {
        "invoice_no": "Invoice #", "po_no": "PO Number", "po_date": "PO Date",
        "gstin": "GSTIN", "buyer_gstin": "Buyer GSTIN", "vendor_gstin": "Vendor GSTIN",
        "address": "Address", "hsn_code": "HSN/SAC", "rate": "Rate",
        "taxable_value": "Taxable Value", "cgst": "CGST", "sgst": "SGST",
        "igst": "IGST", "total_tax": "Total Tax", "amount_in_words": "Amount in Words",
        "bank_name": "Bank Name", "account_no": "Account Number", "ifsc": "IFSC",
        "document_type": "Document Type",
        "vendor_name": "Vendor Name", "customer_name": "Customer Name",
        "buyer_name": "Buyer Name", "consignee": "Consignee",
        "consignee_name": "Consignee Name",
        "invoice_number": "Invoice #", "bill_number": "Bill #",
        "purchase_order_number": "PO Number", "po_number": "PO Number",
        "invoice_date": "Invoice Date", "po_date": "PO Date",
        "grn_number": "GRN Number", "grn_date": "GRN Date",
        "due_date": "Due Date", "gst_number": "GST Number",
        "pan_number": "PAN Number", "phone": "Phone", "email": "Email",
        "billing_address": "Billing Address", "shipping_address": "Shipping Address",
        "item_name": "Item Name", "item_description": "Item Description",
        "quantity": "Quantity", "unit_price": "Unit Price",
        "subtotal": "Subtotal", "tax_amount": "Tax Amount",
        "discount": "Discount", "total_amount": "Total Amount",
        "grand_total": "Grand Total",
        "payment_status": "Payment Status", "notes": "Notes",
    }

    row = 3
    for key, label in LABELS.items():
        v = fields.get(key, "")
        ws[f"A{row}"] = label
        ws[f"A{row}"].font = Font(bold=True, size=10, color="4B4B8F")
        ws[f"A{row}"].fill = header_fill
        ws[f"A{row}"].border = thin
        ws[f"A{row}"].alignment = Alignment(vertical="center")
        ws[f"B{row}"] = str(v) if v else ""
        ws[f"B{row}"].border = thin
        ws[f"B{row}"].alignment = Alignment(wrap_text=True, vertical="center")
        ws.row_dimensions[row].height = 18
        row += 1
    for key, v in fields.items():
        if not v or key in LABELS or key in {"item_rows", "items"}:
            continue
        ws[f"A{row}"] = key.replace("_", " ").title()
        ws[f"A{row}"].font = Font(bold=True, size=10, color="4B4B8F")
        ws[f"A{row}"].fill = header_fill
        ws[f"A{row}"].border = thin
        ws[f"B{row}"] = str(v)
        ws[f"B{row}"].border = thin
        ws[f"B{row}"].alignment = Alignment(wrap_text=True, vertical="center")
        row += 1
    item_rows = fields.get("items") or fields.get("item_rows") or []
    if isinstance(item_rows, list) and item_rows:
        row += 1
        headers = ["Sno", "Order No", "Group", "Article", "Colour", "HSN/SAC", "Type", "Size", "UOM", "Qty", "Price", "Amount", "Total", "Act"]
        keys = ["sno", "order_no", "group", "article", "colour", "hsn_sac", "type", "size", "uom", "qty", "price", "amount", "total", "act"]
        for col, header in enumerate(headers, start=1):
            cell = ws.cell(row=row, column=col, value=header)
            cell.font = Font(bold=True, size=10, color="4B4B8F")
            cell.fill = header_fill
            cell.border = thin
        for item in item_rows:
            row += 1
            for col, key in enumerate(keys, start=1):
                cell = ws.cell(row=row, column=col, value=str(item.get(key, "")) if isinstance(item, dict) else "")
                cell.border = thin
                cell.alignment = Alignment(wrap_text=True, vertical="center")

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
