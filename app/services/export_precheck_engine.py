from __future__ import annotations

import re
from datetime import datetime
from typing import Any


REQUIRED_CONFIDENCE = 0.75
OPTIONAL_CONFIDENCE = 0.60
SUSPICIOUS_LITERALS = {"[object object]", "undefined", "null", "nan"}
LABEL_ONLY_VALUES = {
    "invoice no", "invoice number", "order no", "order number", "po no",
    "po number", "date", "order date", "total", "amount", "customer", "supplier",
}
CRITICAL_MASTER_TYPES = {"customer", "supplier", "product"}


def _module(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text.startswith("purchase"):
        return "purchase_order"
    if text.startswith("sales"):
        return "sales_order"
    return "general"


def _value(value: Any) -> str:
    seen: set[int] = set()
    while isinstance(value, dict):
        marker = id(value)
        if marker in seen:
            return ""
        seen.add(marker)
        for key in ("value", "text", "raw_value", "normalized_value", "extracted_value"):
            if key in value:
                value = value[key]
                break
        else:
            return ""
    if value is None or isinstance(value, (list, tuple, set, dict)):
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _first(source: dict[str, Any], keys: tuple[str, ...]) -> tuple[str, str]:
    for key in keys:
        value = _value(source.get(key))
        if value:
            return key, value
    return keys[0], ""


def _number(value: Any) -> float | None:
    text = _value(value).replace(",", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group())
    except ValueError:
        return None


def _confidence_map(payload: dict[str, Any]) -> dict[str, float]:
    result: dict[str, float] = {}
    sources = [
        payload.get("confidence_map"),
        payload.get("mapped_fields"),
        payload.get("form_data"),
    ]
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key, value in source.items():
            confidence = value.get("confidence") if isinstance(value, dict) else None
            if source is payload.get("confidence_map") and not isinstance(value, dict):
                confidence = value
            if confidence is None:
                continue
            try:
                result[str(key)] = max(0.0, min(1.0, float(confidence)))
            except (TypeError, ValueError):
                continue
    return result


def _valid_date(value: str) -> bool:
    text = value.strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y", "%d.%m.%Y"):
        try:
            datetime.strptime(text[:10], fmt)
            return True
        except ValueError:
            continue
    return bool(re.search(r"\b(?:19|20)\d{2}\b", text) and re.search(r"\b(?:0?[1-9]|[12]\d|3[01])\b", text))


def _suspicious(field: str, value: Any) -> str | None:
    text = _value(value)
    lowered = text.lower()
    if lowered in SUSPICIOUS_LITERALS:
        return "Object/null placeholder value"
    if lowered in LABEL_ONLY_VALUES:
        return "Label-only value"
    if len(text) > 500:
        return "Value is unusually long and may contain raw OCR text"
    if "date" in field and text and not _valid_date(text):
        return "Impossible or unrecognized date"
    if any(token in field for token in ("amount", "total", "price", "rate", "qty", "quantity")) and text and _number(text) is None:
        return "Numeric field has no numeric value"
    return None


def _is_hard_suspicious(reason: str) -> bool:
    return reason in {"Object/null placeholder value", "Label-only value"}


def quality_grade(score: int, hard_blocker: bool = False) -> str:
    if hard_blocker or score < 40:
        return "BLOCKED"
    if score >= 90:
        return "A"
    if score >= 75:
        return "B"
    if score >= 60:
        return "C"
    return "D"


def build_export_precheck(payload: dict[str, Any]) -> dict[str, Any]:
    module = _module(payload.get("module"))
    document_type = str(payload.get("document_type") or "").strip()
    form_data = dict(payload.get("form_data") or payload.get("fields") or {})
    item_rows = list(payload.get("item_rows") or payload.get("rows") or [])
    mapped_fields = dict(payload.get("mapped_fields") or {})
    warnings = [str(item) for item in payload.get("warnings") or [] if str(item).strip()]
    confidence = _confidence_map(payload)
    unresolved_input = [dict(item) for item in payload.get("unresolved_masters") or [] if isinstance(item, dict)]
    resolved_input = [dict(item) for item in payload.get("resolved_masters") or [] if isinstance(item, dict)]

    required_fields: list[dict[str, Any]] = []
    missing_required: list[str] = []
    low_confidence: list[dict[str, Any]] = []
    row_issues: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    suspicious_fields: list[dict[str, str]] = []
    score = 100

    def block(field: str, code: str, message: str) -> None:
        if not any(item["field"] == field and item["code"] == code for item in blockers):
            blockers.append({"field": field, "code": code, "message": message})

    def require(name: str, label: str, alternatives: tuple[str, ...], hard: bool = False) -> None:
        nonlocal score
        key, value = _first(form_data, alternatives)
        satisfied = bool(value)
        item = {"field": name, "label": label, "alternatives": list(alternatives), "satisfied": satisfied, "value": value}
        required_fields.append(item)
        if not satisfied:
            missing_required.append(name)
            score -= 15
            if hard:
                block(name, "missing_required", f"Required field missing: {label}")
            return
        field_conf = next((confidence[candidate] for candidate in alternatives if candidate in confidence), None)
        if field_conf is not None:
            item["confidence"] = field_conf
            if field_conf < REQUIRED_CONFIDENCE:
                score -= 8
                low_confidence.append({"field": key, "confidence": field_conf, "required": True})

    amounts_exist = any(
        _number(form_data.get(key)) not in (None, 0)
        for key in ("order_total", "po_grand_total", "total_amount", "grand_total")
    ) or any(
        _number(row.get("bulk_line_total") or row.get("line_total") or row.get("amount")) not in (None, 0)
        for row in item_rows if isinstance(row, dict)
    )

    if module == "sales_order":
        require("customer", "Customer", ("customer_id", "customer_name"))
        require("sales_order_date", "Sales order date", ("sales_order_date", "order_date"), hard=True)
        require("sales_order_number", "Sales/customer order number", ("customer_po_number", "sales_order_no", "order_no"), hard=True)
        if amounts_exist:
            require("currency", "Currency", ("currency_code_id", "currency", "invoice_currency"))
    elif module == "purchase_order":
        require("supplier", "Supplier", ("supplier_id", "supplier_name"))
        require("po_number", "Purchase order number", ("po_number", "purchase_order_number"), hard=True)
        require("po_date", "Purchase order date", ("po_date", "order_date"), hard=True)
        if amounts_exist:
            require("currency", "Currency", ("currency", "currency_code_id"))
    else:
        detected = sum(bool(_value(value)) for value in {**mapped_fields, **form_data}.values())
        required_fields.append({"field": "detected_fields", "label": "Detected fields", "satisfied": detected > 0, "value": detected})
        if detected == 0:
            missing_required.append("detected_fields")
            score -= 15
            block("detected_fields", "no_fields_detected", "No fields were detected")

    if module in {"sales_order", "purchase_order"}:
        critical_id = "customer_id" if module == "sales_order" else "supplier_id"
        critical_label = "customer" if module == "sales_order" else "supplier"
        if not _value(form_data.get(critical_id)):
            score -= 20
            block(critical_id, "missing_critical_master_id", f"ERP {critical_label} master ID is required")
        if not item_rows or not any(isinstance(row, dict) and any(_value(value) for key, value in row.items() if key != "row_index") for row in item_rows):
            score -= 15
            row_issues.append({"row": None, "field": "item_rows", "code": "empty_rows", "message": "All item rows are empty"})
            block("item_rows", "all_rows_empty", "At least one item row is required")

    for index, raw_row in enumerate(item_rows):
        if not isinstance(raw_row, dict):
            continue
        row = raw_row
        row_label = index + 1
        product = _first(row, ("product_id", "product", "description"))[1]
        qty = _number(row.get("bulk_qty") or row.get("qty") or row.get("quantity"))
        price = _number(row.get("bulk_unit_price") or row.get("unit_price") or row.get("price"))
        line_total = _number(row.get("bulk_line_total") or row.get("line_total") or row.get("amount"))
        if module in {"sales_order", "purchase_order"}:
            for field, ok, message in (
                ("product", bool(product), "Product or description is required"),
                ("qty", qty is not None and qty > 0, "Quantity must be greater than zero"),
                ("unit_price_or_line_total", (price is not None and price >= 0) or (line_total is not None and line_total > 0), "Unit price or line total is required"),
            ):
                required_fields.append({"field": f"rows[{index}].{field}", "label": f"Row {row_label} {field}", "satisfied": ok})
                if not ok:
                    score -= 15
                    missing_required.append(f"rows[{index}].{field}")
                    row_issues.append({"row": index, "field": field, "code": "missing_required", "message": message})
                    block(f"rows[{index}].{field}", "invalid_row", f"Row {row_label}: {message}")
            if not _value(row.get("product_id")):
                score -= 10
                row_issues.append({"row": index, "field": "product_id", "code": "missing_product_id", "message": "ERP product master ID is required"})
                block(f"rows[{index}].product_id", "missing_product_id", f"Row {row_label}: ERP product master ID is required")
            tax_value = row.get("bulk_tax_amount") or row.get("tax_amount") or row.get("bulk_tax_group") or row.get("tax_group")
            if _value(tax_value) and not _value(row.get("tax_group_id")):
                score -= 8
                row_issues.append({"row": index, "field": "tax_group_id", "code": "missing_tax_group_id", "message": "Tax exists but ERP tax group ID is missing"})
        for key, value in row.items():
            reason = _suspicious(str(key), value)
            if reason:
                item = {"field": f"rows[{index}].{key}", "value": _value(value), "reason": reason}
                suspicious_fields.append(item)
                if _is_hard_suspicious(reason):
                    score -= 10
                    block(item["field"], "suspicious_value", f"{item['field']}: {reason}")
                else:
                    warnings.append(f"{item['field']}: {reason}")
        for key in ("bulk_line_total", "line_total", "amount", "bulk_unit_price", "unit_price"):
            numeric = _number(row.get(key))
            if numeric is not None and numeric < 0:
                block(f"rows[{index}].{key}", "negative_amount", f"Row {row_label}: negative amount is not allowed")

    for key, value in {**mapped_fields, **form_data}.items():
        if not _value(value):
            continue
        reason = _suspicious(str(key), value)
        if reason:
            suspicious_fields.append({"field": str(key), "value": _value(value), "reason": reason})
            if _is_hard_suspicious(reason):
                score -= 10
                block(str(key), "suspicious_value", f"{key}: {reason}")
            else:
                warnings.append(f"{key}: {reason}")

    required_names = {candidate for item in required_fields for candidate in item.get("alternatives", [])}
    for field, field_conf in confidence.items():
        if field in required_names:
            continue
        if field_conf < OPTIONAL_CONFIDENCE:
            low_confidence.append({"field": field, "confidence": field_conf, "required": False})
            warnings.append(f"Optional field has low confidence: {field}")

    resolved_targets = {str(item.get("target_field") or "") for item in resolved_input}
    if module == "sales_order" and _value(form_data.get("customer_id")):
        resolved_targets.add("customer_id")
    if module == "purchase_order" and _value(form_data.get("supplier_id")):
        resolved_targets.add("supplier_id")
    for index, row in enumerate(item_rows):
        if isinstance(row, dict) and _value(row.get("product_id")):
            resolved_targets.add(f"item_rows.{index}.product_id")

    unresolved: list[dict[str, Any]] = []
    for item in unresolved_input:
        master_type = str(item.get("master_type") or "").lower()
        target = str(item.get("target_field") or "")
        source_value = _value(item.get("source_value"))
        if target in resolved_targets or not source_value:
            continue
        unresolved.append(item)
        if master_type in CRITICAL_MASTER_TYPES:
            score -= 20
            block(target or master_type, "unresolved_critical_master", f"Unresolved ERP {master_type} master: {source_value}")
        else:
            warnings.append(f"Unresolved ERP {master_type or 'master'}: {source_value}")

    for warning in list(dict.fromkeys(warnings)):
        if warning:
            score -= 3

    score = max(0, min(100, score))
    hard_blocker = bool(blockers)
    grade = quality_grade(score, hard_blocker)
    export_allowed = not hard_blocker and grade in {"A", "B"}
    review_required = bool(low_confidence or row_issues or unresolved or warnings or grade != "A")
    ready_count = sum(1 for item in required_fields if item.get("satisfied"))
    return {
        "export_allowed": export_allowed,
        "review_required": review_required,
        "quality_score": score,
        "quality_grade": grade,
        "ready_count": ready_count,
        "missing_required_count": len(missing_required),
        "low_confidence_count": len(low_confidence),
        "unresolved_master_count": len(unresolved),
        "row_issue_count": len(row_issues),
        "required_fields": required_fields,
        "missing_required_fields": list(dict.fromkeys(missing_required)),
        "low_confidence_fields": low_confidence,
        "unresolved_masters": unresolved,
        "row_issues": row_issues,
        "suspicious_fields": suspicious_fields,
        "blockers": blockers,
        "warnings": list(dict.fromkeys(warnings)),
        "export_formats": {"json": export_allowed, "excel": export_allowed, "pdf": export_allowed},
        "module": module,
        "document_type": document_type,
    }
