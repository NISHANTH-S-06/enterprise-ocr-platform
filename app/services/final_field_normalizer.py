"""Final OCR field normalization and export gating.

This module is a final, non-destructive pass over Phase 2 field output. It
keeps the original p2_fields list available for debug and emits a canonical,
alias-collapsed view for UI/export decisions.
"""
from __future__ import annotations

from typing import Any


ALIAS_MAP = {
    "invoice_no": "invoice_number",
    "inv_no": "invoice_number",
    "bill_no": "invoice_number",
    "bill_number": "invoice_number",
    "tax_invoice_no": "invoice_number",
    "document_no": "invoice_number",
    "voucher_no": "invoice_number",
    "challan_no": "invoice_number",
    "date": "invoice_date",
    "dated": "invoice_date",
    "invoice_dt": "invoice_date",
    "bill_date": "invoice_date",
    "document_date": "invoice_date",
    "doc_type": "document_type",
    "vendor": "company_name",
    "supplier": "company_name",
    "seller": "company_name",
    "exporter": "company_name",
    "vendor_name": "company_name",
    "supplier_name": "company_name",
    "po_number": "purchase_order_number",
    "po_no": "purchase_order_number",
    "order_no": "purchase_order_number",
    "buyer_order_no": "purchase_order_number",
    "qty": "quantity",
    "total_qty": "quantity",
    "total_quantity": "quantity",
    "rate": "unit_price",
    "unit_rate": "unit_price",
    "price": "unit_price",
    "description": "item_description",
    "description_of_goods": "item_description",
    "container_no": "container_number",
    "amount_inr": "total_amount_inr",
    "inr_total": "total_amount_inr",
    "total_inr": "total_amount_inr",
    "invoice_value_inr": "total_amount_inr",
    "ex_rate": "exchange_rate",
    "conversion_rate": "exchange_rate",
    "forex_rate": "exchange_rate",
    "bank_account": "account_number",
    "account_no": "account_number",
    "ac_no": "account_number",
    "beneficiary_account": "account_number",
    "sold_to": "buyer_name",
    "party_name": "buyer_name",
    "receiver": "consignee_name",
    "billing_address": "buyer_address",
    "shipping_address": "consignee_address",
    "address": "buyer_address",
    "igst_rate": "igst_percentage",
    "grn_no": "grn_number",
    "grn_date": "grn_date",
}

DROP_FROM_CANONICAL = {
    "bill_no",
    "bill_number",
    "inv_no",
    "invoice_no",
    "tax_invoice_no",
    "document_no",
    "voucher_no",
    "challan_no",
    "date",
    "dated",
    "invoice_dt",
    "bill_date",
    "document_date",
    "vendor",
    "supplier",
    "seller",
    "exporter",
    "po_number",
    "po_no",
    "order_no",
    "buyer_order_no",
    "qty",
    "total_qty",
    "total_quantity",
    "rate",
    "unit_rate",
    "price",
    "description",
    "description_of_goods",
    "container_no",
    "doc_type",
    "amount_inr",
    "inr_total",
    "total_inr",
    "invoice_value_inr",
    "ex_rate",
    "conversion_rate",
    "forex_rate",
    "bank_account",
    "account_no",
    "ac_no",
    "beneficiary_account",
    "sold_to",
    "party_name",
    "receiver",
    "billing_address",
    "shipping_address",
    "address",
    "igst_rate",
    "igst_percentage",
}

CANONICAL_TARGETS = {
    "invoice_number",
    "invoice_date",
    "company_name",
    "purchase_order_number",
    "quantity",
    "unit_price",
    "item_description",
    "container_number",
    "gst_number",
    "iec_code",
    "buyer_name",
    "buyer_address",
    "consignee_name",
    "consignee_address",
    "grand_total",
    "total_amount",
    "total_amount_inr",
    "exchange_rate",
    "account_number",
    "bank_name",
    "swift_code",
    "ifsc_code",
    "document_type",
    "line_item_count",
    "grn_number",
    "grn_date",
}

INVALID_PLACEHOLDERS = {
    "VALUE",
    "GROUP",
    "OF GOODS",
    "SEAL NUMBER",
    "BOX SERIAL NUMBER",
    "DESCRIPTION",
    "TOTAL",
    "DATE",
    "INVOICE",
    "COW LEATHER",
    "NA",
    "N/A",
    "NONE",
    "NULL",
}

CRITICAL_FIELDS = {
    "invoice_number",
    "invoice_date",
    "company_name",
    "buyer_name",
    "grand_total",
    "total_amount",
}

CORE_REQUIRED_FIELDS = {"invoice_number", "invoice_date", "company_name"}
BLOCKING_STATUSES = {"MISSING", "VALIDATION_ERROR", "MANUAL_REQUIRED"}
STATUS_RANK = {
    "AUTO_FILLED": 4,
    "NEED_REVIEW": 3,
    "MANUAL_REQUIRED": 2,
    "VALIDATION_ERROR": 1,
    "MISSING": 0,
}


def _canonical_name(field: dict[str, Any]) -> str:
    raw_name = str(field.get("field_name") or field.get("name") or "").strip()
    normalized = raw_name.lower()
    if normalized in ALIAS_MAP:
        target = ALIAS_MAP[normalized]
        return target if target in CANONICAL_TARGETS or target.startswith("sales_item_") else ""
    if normalized in DROP_FROM_CANONICAL:
        return ""
    if normalized in CANONICAL_TARGETS or normalized.startswith("sales_item_"):
        return normalized
    return ""


def _clean_value(value: Any) -> str:
    while isinstance(value, dict):
        value = value.get("value", "")
    return str(value or "").strip()


def _confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value or 0.0)))
    except (TypeError, ValueError):
        return 0.0


def _is_invalid_placeholder(value: str) -> bool:
    return value.strip().upper() in INVALID_PLACEHOLDERS


def _normalize_field(field: dict[str, Any], canonical_name: str) -> dict[str, Any]:
    value = _clean_value(field.get("value"))
    confidence = _confidence(field.get("confidence"))
    status = str(field.get("status") or ("AUTO_FILLED" if value else "MISSING")).strip().upper()
    validation_message = str(field.get("validation_message") or "").strip()
    is_critical = canonical_name in CRITICAL_FIELDS

    if _is_invalid_placeholder(value):
        status = "VALIDATION_ERROR" if is_critical else "NEED_REVIEW"
        confidence = min(confidence, 0.25)
        validation_message = f"Invalid placeholder value for {canonical_name}: {value}"
    elif not value:
        status = "MISSING"

    return {
        "field_name": canonical_name,
        "value": value,
        "confidence": round(confidence, 3),
        "status": status,
        "method": field.get("method", "regex"),
        "source": field.get("source", "ocr"),
        "evidence": field.get("evidence", ""),
        "is_critical": is_critical,
        "validation_message": validation_message,
    }


def _is_better_candidate(candidate: dict[str, Any], current: dict[str, Any] | None) -> bool:
    if current is None:
        return True
    candidate_rank = STATUS_RANK.get(str(candidate.get("status", "")).upper(), -1)
    current_rank = STATUS_RANK.get(str(current.get("status", "")).upper(), -1)
    if candidate_rank != current_rank:
        return candidate_rank > current_rank
    return _confidence(candidate.get("confidence")) > _confidence(current.get("confidence"))


def _missing_field(name: str, required_fields: set[str] | None = None) -> dict[str, Any]:
    required_fields = required_fields or CORE_REQUIRED_FIELDS
    return {
        "field_name": name,
        "value": "",
        "confidence": 0.0,
        "status": "MISSING",
        "method": "final_normalizer",
        "source": "final_normalizer",
        "evidence": "",
        "is_critical": name in required_fields,
        "validation_message": f"Required field missing: {name}",
    }


def _block_reason(field: dict[str, Any]) -> str:
    name = str(field.get("field_name") or "")
    status = str(field.get("status") or "")
    value = str(field.get("value") or "")
    if status == "VALIDATION_ERROR" and value:
        return f"{name} has invalid value: {value}"
    return f"{name} is {status}"


def build_final_ocr_decision(p2_fields: list[dict], document_type: str | None = None) -> dict:
    """Build the final canonical OCR decision from Phase 2 field rows."""
    canonical_fields: dict[str, dict[str, Any]] = {}

    for raw_field in p2_fields or []:
        if not isinstance(raw_field, dict):
            continue
        canonical_name = _canonical_name(raw_field)
        if not canonical_name:
            continue
        normalized = _normalize_field(raw_field, canonical_name)
        if _is_better_candidate(normalized, canonical_fields.get(canonical_name)):
            canonical_fields[canonical_name] = normalized

    detected_type = _clean_value(document_type or canonical_fields.get("document_type", {}).get("value")).lower()
    if detected_type in {"purchase_order", "po", "purchase"}:
        required_fields = {"purchase_order_number", "company_name"}
        total_required = False
    elif detected_type in {"grn", "goods_receipt", "goods_receipt_note"}:
        required_fields = {"grn_number"}
        total_required = False
    else:
        required_fields = set(CORE_REQUIRED_FIELDS)
        total_required = detected_type in {
            "sales", "invoice", "tax_invoice", "export_invoice", "commercial_invoice"
        } or not detected_type

    for required_name in required_fields:
        canonical_fields.setdefault(required_name, _missing_field(required_name, required_fields))

    has_grand_total = bool(_clean_value(canonical_fields.get("grand_total", {}).get("value")))
    has_total_amount = bool(_clean_value(canonical_fields.get("total_amount", {}).get("value")))
    if total_required and not has_grand_total and not has_total_amount:
        canonical_fields.setdefault("grand_total", _missing_field("grand_total", required_fields | {"grand_total"}))

    review_fields: list[str] = []
    blocked_fields: list[str] = []
    export_block_reasons: list[str] = []
    blocker_classification: list[dict[str, str]] = []

    counts = {
        "canonical_auto_filled_count": 0,
        "canonical_need_review_count": 0,
        "canonical_missing_count": 0,
        "canonical_validation_error_count": 0,
    }

    for name, field in canonical_fields.items():
        status = str(field.get("status") or "").upper()
        if status == "AUTO_FILLED":
            counts["canonical_auto_filled_count"] += 1
        elif status == "NEED_REVIEW":
            counts["canonical_need_review_count"] += 1
            review_fields.append(name)
        elif status == "MISSING":
            counts["canonical_missing_count"] += 1
            review_fields.append(name)
        elif status == "VALIDATION_ERROR":
            counts["canonical_validation_error_count"] += 1
            review_fields.append(name)

        if name in required_fields and status in BLOCKING_STATUSES:
            blocked_fields.append(name)
            message = _block_reason(field)
            export_block_reasons.append(message)
            blocker_classification.append({
                "field": name,
                "reason_code": "canonical_but_validation_blocked" if status == "VALIDATION_ERROR" else "truly_absent_from_ocr",
                "message": message,
            })

    if total_required and not has_grand_total and not has_total_amount and "grand_total" not in blocked_fields:
        blocked_fields.append("grand_total")
        export_block_reasons.append("grand_total/total_amount is MISSING")
        blocker_classification.append({
            "field": "grand_total",
            "reason_code": "truly_absent_from_ocr",
            "message": "grand_total/total_amount is MISSING",
        })

    return {
        "canonical_fields": canonical_fields,
        "review_fields": review_fields,
        "blocked_fields": blocked_fields,
        "export_allowed": not blocked_fields,
        "export_block_reasons": export_block_reasons,
        "blocker_classification": blocker_classification,
        **counts,
    }
