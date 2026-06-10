"""Correction learning apply engine for the workbench (Task 3).

Apply order:
1. OCR fields normalized
2. Selected dataset mapping
3. Saved correction memory   ← this module
4. Generic fallback aliases
5. Direct ERP key match
6. Row mapping
7. ERP master resolver
8. validation/export gate
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from app.services.final_field_normalizer import INVALID_PLACEHOLDERS
from app.api.v1.routers.forms import (
    WORKBENCH_TARGET_DOM_IDS,
    WORKBENCH_ALIASES,
    WORKBENCH_ROW_ALIASES,
    clean_workbench_value,
    normalize_workbench_key,
)


def normalize_correction_value(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _module_key(module: str) -> str:
    return "purchase" if normalize_workbench_key(module).startswith("purchase") else "sales"


def build_workbench_correction_memory(
    module: str,
    source_ocr_key: str,
    source_ocr_label: Optional[str],
    source_value_sample: Optional[str],
    target_erp_name: str,
    corrected_value: str,
    document_type: Optional[str] = None,
    company_name: Optional[str] = None,
    vendor_name: Optional[str] = None,
    customer_name: Optional[str] = None,
    target_dom_id: Optional[str] = None,
    confidence: float = 1.0,
) -> dict[str, Any]:
    mod = _module_key(module)
    targets = WORKBENCH_TARGET_DOM_IDS.get(mod, {})
    dom_id = target_dom_id or targets.get(target_erp_name, "")
    return {
        "module": f"{mod}_order",
        "document_type": document_type or "",
        "company_name": company_name or "",
        "vendor_name": vendor_name or "",
        "customer_name": customer_name or "",
        "source_ocr_key": source_ocr_key,
        "source_ocr_label": source_ocr_label or source_ocr_key,
        "normalized_source_key": normalize_workbench_key(source_ocr_key),
        "source_value_sample": source_value_sample or "",
        "target_erp_name": target_erp_name,
        "target_dom_id": dom_id,
        "target_label": target_erp_name,
        "corrected_value_sample": corrected_value,
        "confidence": confidence,
        "source": "manual_correction",
    }


def apply_workbench_corrections(
    module: str,
    document_type: Optional[str],
    company_name: Optional[str],
    vendor_name: Optional[str],
    customer_name: Optional[str],
    ocr_fields: dict[str, Any],
    form_data: dict[str, str],
    item_rows: list[dict[str, Any]],
    corrections: list[dict[str, Any]],
) -> dict[str, Any]:
    """Apply correction memory to workbench form_data.

    Matching rules (PART E):
    - Strong match: module + normalized_source_key match + source_value_sample non-empty
    - Medium match: normalized_source_key + module match only
    - Do not apply: empty value, target not in form, inactive, wrong module, etc.
    """
    mod = _module_key(module)
    targets = WORKBENCH_TARGET_DOM_IDS.get(mod, {})
    aliases = WORKBENCH_ALIASES.get(mod, {})

    result_data = dict(form_data)
    result_rows: list[dict[str, Any]] = [dict(r) for r in item_rows]
    applied: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []

    ocr_lookup: dict[str, str] = {}
    for key, value in ocr_fields.items():
        nk = normalize_workbench_key(key)
        v = clean_workbench_value(value)
        if nk and v:
            ocr_lookup[nk] = v

    for corr in corrections:
        reason = _skip_reason(corr, mod, document_type, company_name, vendor_name, customer_name, targets, ocr_lookup)
        src_key = str(corr.get("source_ocr_key") or "")
        tgt = str(corr.get("target_erp_name") or "")
        cv = normalize_correction_value(corr.get("corrected_value_sample") or "")

        if reason:
            skipped.append({"source_ocr_key": src_key, "target_erp_name": tgt, "reason": reason})
            continue

        cv_clean = clean_workbench_value(cv, tgt)
        if not cv_clean or cv_clean.upper() in INVALID_PLACEHOLDERS:
            skipped.append({"source_ocr_key": src_key, "target_erp_name": tgt, "reason": "empty_value"})
            continue

        if tgt in targets:
            if tgt in result_data and result_data[tgt]:
                skipped.append({"source_ocr_key": src_key, "target_erp_name": tgt, "reason": "already_filled"})
                continue
            result_data[tgt] = cv_clean
            applied.append({"source_ocr_key": src_key, "normalized_source_key": str(corr.get("normalized_source_key") or ""), "target_erp_name": tgt, "corrected_value": cv_clean, "match_type": "strong"})
        elif tgt in aliases:
            canonical = aliases[tgt]
            if canonical in targets:
                if canonical in result_data and result_data[canonical]:
                    skipped.append({"source_ocr_key": src_key, "target_erp_name": tgt, "reason": "already_filled"})
                    continue
                result_data[canonical] = cv_clean
                applied.append({"source_ocr_key": src_key, "normalized_source_key": str(corr.get("normalized_source_key") or ""), "target_erp_name": canonical, "corrected_value": cv_clean, "match_type": "medium"})
            else:
                skipped.append({"source_ocr_key": src_key, "target_erp_name": tgt, "reason": "target_field_not_found"})
        else:
            skipped.append({"source_ocr_key": src_key, "target_erp_name": tgt, "reason": "target_field_not_found"})

    return {
        "form_data": result_data,
        "item_rows": result_rows,
        "applied_count": len(applied),
        "applied_corrections": applied,
        "skipped_corrections": skipped,
    }


def _skip_reason(
    corr: dict[str, Any],
    mod: str,
    document_type: Optional[str],
    company_name: Optional[str],
    vendor_name: Optional[str],
    customer_name: Optional[str],
    targets: dict[str, str],
    ocr_lookup: dict[str, str],
) -> str:
    if not corr.get("is_active", True):
        return "inactive"
    corr_module = str(corr.get("module") or "")
    corr_mod_key = _module_key(corr_module) if corr_module else ""
    if corr_mod_key and corr_mod_key != mod:
        return "wrong_module"

    nsk = str(corr.get("normalized_source_key") or "")
    if nsk and nsk not in ocr_lookup:
        return "source_key_not_found"

    sv = normalize_correction_value(corr.get("source_value_sample") or "")
    if not sv and not nsk:
        return "empty_value"

    dt = str(corr.get("document_type") or "")
    if dt and document_type and dt != document_type:
        if document_type not in dt and dt not in document_type:
            return "document_type_mismatch"

    cn = str(corr.get("company_name") or "")
    if cn and company_name and cn != company_name:
        return "identity_mismatch"
    vn = str(corr.get("vendor_name") or "")
    if vn and vendor_name and vn != vendor_name:
        return "identity_mismatch"
    cun = str(corr.get("customer_name") or "")
    if cun and customer_name and cun != customer_name:
        return "identity_mismatch"

    return ""


def apply_row_corrections(
    module: str,
    item_rows: list[dict[str, Any]],
    ocr_rows: list[dict[str, Any]],
    row_corrections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Apply row corrections to item rows."""
    mod = _module_key(module)
    row_aliases = WORKBENCH_ROW_ALIASES.get(mod, {})
    result: list[dict[str, Any]] = [dict(r) for r in item_rows]

    for corr in row_corrections:
        src_col_key = str(corr.get("source_column_key") or "")
        tgt = str(corr.get("target_row_erp_name") or "")
        sample = normalize_correction_value(corr.get("sample_value") or "")
        if not src_col_key or not tgt or not sample:
            continue

        target_field = row_aliases.get(tgt, tgt)
        src_normalized = normalize_workbench_key(src_col_key)

        for row_index, ocr_row in enumerate(ocr_rows):
            row_src_val = normalize_correction_value(ocr_row.get(src_col_key) or ocr_row.get(src_normalized) or "")
            if row_src_val and row_src_val == sample:
                if row_index < len(result):
                    existing = result[row_index].get(target_field, "")
                    if not existing:
                        result[row_index][target_field] = sample

    return result
