"""Safe correction-memory application for canonical OCR fields."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from loguru import logger
from sqlalchemy import select

from app.models.company import CorrectionMemory
from app.services.correction_memory import normalize_correction_value
from app.services.final_field_normalizer import CRITICAL_FIELDS, INVALID_PLACEHOLDERS


STABLE_MEMORY_FIELDS = {
    "company_name",
    "gst_number",
    "iec_code",
    "bank_name",
    "account_number",
    "swift_code",
    "ifsc_code",
    "buyer_name",
    "buyer_address",
    "consignee_name",
    "consignee_address",
}

DYNAMIC_MEMORY_FIELDS = {
    "invoice_number",
    "invoice_date",
    "purchase_order_number",
    "grand_total",
    "total_amount",
    "total_amount_inr",
    "exchange_rate",
    "item_description",
    "quantity",
    "unit_price",
    "container_number",
    "seal_number",
}

LOW_CONFIDENCE_THRESHOLD = 0.75


def classify_memory_field(field_name: str) -> str:
    """Classify memory fields as safe-to-apply stable data or dynamic hints."""
    normalized = str(field_name or "").strip().lower()
    if normalized in STABLE_MEMORY_FIELDS:
        return "stable"
    if normalized in DYNAMIC_MEMORY_FIELDS or normalized.startswith("sales_item_"):
        return "dynamic"
    return "dynamic"


async def load_company_correction_memory(db: Any, company_id: Any, document_type: str | None = None) -> list[Any]:
    """Load newest usable correction-memory rows for a company.

    The loader is defensive around older schemas and DB failures. Callers should
    treat an empty list as "no usable memory".
    """
    if not db or not company_id:
        return []

    try:
        company_key = uuid.UUID(str(company_id))
        stmt = select(CorrectionMemory).where(CorrectionMemory.company_id == company_key)
        if document_type and hasattr(CorrectionMemory, "document_type"):
            stmt = stmt.where(
                (CorrectionMemory.document_type == document_type)
                | (CorrectionMemory.document_type.is_(None))
            )
        if hasattr(CorrectionMemory, "updated_at"):
            stmt = stmt.order_by(CorrectionMemory.field_name, CorrectionMemory.updated_at.desc())
        elif hasattr(CorrectionMemory, "created_at"):
            stmt = stmt.order_by(CorrectionMemory.field_name, CorrectionMemory.created_at.desc())
        else:
            stmt = stmt.order_by(CorrectionMemory.field_name)

        result = await db.execute(stmt)
        rows = result.scalars().all()
    except Exception as exc:
        logger.warning("Correction memory load failed: company_id={} err={}", company_id, exc)
        return []

    latest_by_field: dict[str, Any] = {}
    for row in rows:
        field_name = str(getattr(row, "field_name", "") or "").strip().lower()
        value = normalize_correction_value(getattr(row, "correct_value", ""))
        if not field_name or not value or value.upper() in INVALID_PLACEHOLDERS:
            continue
        if field_name not in latest_by_field:
            latest_by_field[field_name] = row
    return list(latest_by_field.values())


def _confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value or 0.0)))
    except (TypeError, ValueError):
        return 0.0


def _is_apply_target(field: dict[str, Any] | None) -> bool:
    if not isinstance(field, dict):
        return True
    value = normalize_correction_value(field.get("value"))
    status = str(field.get("status") or "").strip().upper()
    return (
        not value
        or value.upper() in INVALID_PLACEHOLDERS
        or status in {"MISSING", "NEED_REVIEW"}
        or _confidence(field.get("confidence")) < LOW_CONFIDENCE_THRESHOLD
    )


def _memory_timestamp(memory: Any) -> str | None:
    for attr in ("updated_at", "created_at"):
        value = getattr(memory, attr, None)
        if isinstance(value, datetime):
            return value.isoformat()
        if value:
            return str(value)
    return None


def apply_memory_to_canonical_fields(canonical_fields: dict, memories: list) -> dict:
    """Apply stable memory safely and expose dynamic memory as review hints."""
    updated = {
        str(name): dict(field)
        for name, field in (canonical_fields or {}).items()
        if isinstance(field, dict)
    }
    memory_suggestions: dict[str, dict[str, Any]] = {}
    applied_fields: list[str] = []
    suggestion_fields: list[str] = []
    warnings: list[str] = []

    if not isinstance(canonical_fields, dict):
        warnings.append("Correction memory skipped: canonical_fields unavailable")
        canonical_fields = {}
    if not memories:
        return {
            "canonical_fields": updated,
            "memory_suggestions": {},
            "memory_applied_fields": [],
            "memory_suggestion_fields": [],
            "memory_applied_count": 0,
            "memory_suggestion_count": 0,
            "warnings": warnings,
        }

    seen_fields: set[str] = set()
    for memory in memories:
        field_name = str(getattr(memory, "field_name", "") or "").strip().lower()
        remembered_value = normalize_correction_value(getattr(memory, "correct_value", ""))
        if not field_name:
            warnings.append("Correction memory row skipped: field_name unavailable")
            continue
        if field_name in seen_fields:
            continue
        seen_fields.add(field_name)
        if not remembered_value or remembered_value.upper() in INVALID_PLACEHOLDERS:
            warnings.append(f"Correction memory skipped for {field_name}: stale or empty value")
            continue

        current = updated.get(field_name)
        current_value = current.get("value", "") if isinstance(current, dict) else ""
        classification = classify_memory_field(field_name)
        old_value = normalize_correction_value(getattr(memory, "old_value", ""))

        exact_dynamic_match = (
            classification == "dynamic"
            and normalize_correction_value(current_value) == old_value
            and (bool(old_value) or _is_apply_target(current))
        )
        if (classification == "stable" and _is_apply_target(current)) or exact_dynamic_match:
            previous = current if isinstance(current, dict) else {}
            updated[field_name] = {
                **previous,
                "field_name": field_name,
                "value": remembered_value,
                "confidence": 0.85,
                "status": "AUTO_FILLED",
                "source": "correction_memory",
                "method": "memory_auto_apply",
                "evidence": previous.get("evidence", ""),
                "is_critical": bool(previous.get("is_critical", field_name in CRITICAL_FIELDS)),
                "validation_message": "",
            }
            applied_fields.append(field_name)
            continue

        if classification != "dynamic":
            continue

        memory_suggestions[field_name] = {
            "remembered_value": remembered_value,
            "current_value": current_value,
            "reason": "Dynamic field; remembered value is a review suggestion only",
            "confidence": 0.85,
            "source": "correction_memory",
        }
        timestamp = _memory_timestamp(memory)
        if timestamp:
            memory_suggestions[field_name]["remembered_at"] = timestamp
        suggestion_fields.append(field_name)

    return {
        "canonical_fields": updated,
        "memory_suggestions": memory_suggestions,
        "memory_applied_fields": applied_fields,
        "memory_suggestion_fields": suggestion_fields,
        "memory_applied_count": len(applied_fields),
        "memory_suggestion_count": len(suggestion_fields),
        "warnings": list(dict.fromkeys(warnings)),
    }
