"""User correction helpers for final OCR review output."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from app.services.final_field_normalizer import (
    CANONICAL_TARGETS,
    INVALID_PLACEHOLDERS,
    build_final_ocr_decision,
)


NUMERIC_FIELDS = {"grand_total", "total_amount", "total_amount_inr", "exchange_rate", "quantity", "unit_price"}


def normalize_correction_value(value: Any) -> str:
    """Normalize a user correction into a stable string value."""
    return " ".join(str(value or "").strip().split())


def _is_allowed_field(field_name: str) -> bool:
    return field_name in CANONICAL_TARGETS or field_name.startswith("sales_item_")


def _basic_validation_error(field_name: str, value: str) -> str:
    if not value:
        return "Correction value is empty"
    if value.upper() in INVALID_PLACEHOLDERS:
        return f"Invalid placeholder value: {value}"
    if field_name == "invoice_date":
        for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%Y/%m/%d"):
            try:
                datetime.strptime(value, fmt)
                break
            except ValueError:
                continue
        else:
            return "Expected invoice_date format DD-MM-YYYY or YYYY-MM-DD"
    if field_name in NUMERIC_FIELDS:
        try:
            float(value.replace(",", ""))
        except ValueError:
            return f"Expected numeric value for {field_name}"
    return ""


def apply_user_corrections(canonical_fields: dict[str, Any], corrections: dict[str, Any]) -> dict[str, Any]:
    """Apply user corrections to canonical fields without mutating the input."""
    cleaned_fields = {
        str(name): dict(field)
        for name, field in (canonical_fields or {}).items()
        if isinstance(field, dict)
    }
    applied: list[str] = []
    unknown_fields: list[str] = []
    invalid_fields: dict[str, str] = {}

    for raw_name, raw_value in (corrections or {}).items():
        field_name = str(raw_name or "").strip()
        value = normalize_correction_value(raw_value)
        if not field_name or not _is_allowed_field(field_name):
            unknown_fields.append(field_name)
            continue

        previous = cleaned_fields.get(field_name, {})
        validation_error = _basic_validation_error(field_name, value)
        status = "VALIDATION_ERROR" if validation_error else "AUTO_FILLED"
        cleaned_fields[field_name] = {
            **previous,
            "field_name": field_name,
            "value": value,
            "confidence": 1.0 if not validation_error else 0.25,
            "status": status,
            "method": "manual_correction",
            "source": "user_correction",
            "evidence": previous.get("evidence", ""),
            "is_critical": bool(previous.get("is_critical", False)),
            "validation_message": validation_error,
        }
        applied.append(field_name)
        if validation_error:
            invalid_fields[field_name] = validation_error

    return {
        "canonical_fields": cleaned_fields,
        "applied_fields": applied,
        "unknown_fields": unknown_fields,
        "invalid_fields": invalid_fields,
    }


def build_correction_memory_records(
    *,
    company_id: uuid.UUID | str | None,
    template_id: uuid.UUID | str | None = None,
    document_type: str | None = None,
    canonical_fields_before: dict[str, Any] | None = None,
    corrections: dict[str, Any] | None = None,
    evidence_text: str | None = None,
) -> list[CorrectionMemory]:
    """Build CorrectionMemory ORM rows for corrected canonical fields."""
    if not company_id:
        return []
    from app.models.company import CorrectionMemory
    from app.services.source_trace import is_field_dynamic, is_field_reusable

    company_uuid = uuid.UUID(str(company_id))
    template_uuid = uuid.UUID(str(template_id)) if template_id else None
    records: list[CorrectionMemory] = []

    for field_name, raw_value in (corrections or {}).items():
        normalized_name = str(field_name or "").strip()
        if not normalized_name or not _is_allowed_field(normalized_name):
            continue
        # Enforce safety check: dynamic fields must not be saved to memory
        if is_field_dynamic(normalized_name):
            continue
        # Enforce safety check: only reusable fields can be saved to memory
        if not is_field_reusable(normalized_name):
            continue

        correct_value = normalize_correction_value(raw_value)
        if not correct_value:
            continue
        previous = (canonical_fields_before or {}).get(normalized_name, {})
        old_value = previous.get("value") if isinstance(previous, dict) else None
        records.append(CorrectionMemory(
            company_id=company_uuid,
            template_id=template_uuid,
            document_type=document_type,
            field_name=normalized_name,
            old_value=str(old_value) if old_value not in (None, "") else None,
            correct_value=correct_value,
            evidence_text=evidence_text,
            rule_suggestion="manual_correction",
        ))
    return records


def recompute_final_decision_after_corrections(canonical_fields: dict[str, Any]) -> dict[str, Any]:
    """Recompute final export/review state from current canonical fields."""
    rows = [
        field
        for field in (canonical_fields or {}).values()
        if isinstance(field, dict)
    ]
    return build_final_ocr_decision(rows)


async def resolve_company_for_correction_memory(db: Any, job: dict[str, Any], canonical_fields: dict[str, Any]) -> dict[str, Any]:
    """Find company identity for memory save without requiring newer in-memory jobs."""
    detected_company = job.get("detected_company") if isinstance(job.get("detected_company"), dict) else {}
    if detected_company.get("company_id"):
        return detected_company

    from app.services.company_resolver import resolve_company_id

    resolved = await resolve_company_id(
        db,
        job.get("raw_text") or job.get("text") or job.get("extracted_text") or "",
        canonical_fields,
        detected_company,
    )
    if isinstance(resolved, dict):
        merged = {**detected_company, **resolved}
        job["detected_company"] = merged
        if merged.get("company_id"):
            job["company_id"] = merged.get("company_id")
        return merged
    return detected_company
