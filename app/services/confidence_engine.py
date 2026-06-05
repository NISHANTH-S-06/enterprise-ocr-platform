"""confidence_engine.py — Assign per-field confidence status labels.

Status labels (canonical set):
  AUTO_FILLED      — extracted with high confidence (≥ HIGH_THRESHOLD)
  NEED_REVIEW      — extracted but low/medium confidence or from AI fallback
  MISSING          — not extracted at all (value is empty)
  VALIDATION_ERROR — extracted but failed a validation rule
  MANUAL_REQUIRED  — marked by template as human-entry only

Summary:
  auto_filled_count
  need_review_count
  missing_count
  validation_error_count
  export_allowed        — False if any critical field has MISSING or VALIDATION_ERROR
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ── Thresholds ────────────────────────────────────────────────────────────────
HIGH_THRESHOLD   = 0.80   # ≥ this → AUTO_FILLED
REVIEW_THRESHOLD = 0.50   # ≥ this and < HIGH → NEED_REVIEW; < this → MISSING (if non-empty)

# Critical fields that block export when MISSING or VALIDATION_ERROR
CRITICAL_FIELDS = {
    "invoice_no", "invoice_number", "invoice_date",
    "grand_total", "total_amount",
    "vendor_name", "supplier_name", "buyer_name",
    "gstin", "gst_number",
}


# ── Per-field result ──────────────────────────────────────────────────────────

@dataclass
class FieldConfidenceResult:
    field_name:  str
    value:       str
    confidence:  float
    status:      str            # AUTO_FILLED | NEED_REVIEW | MISSING | VALIDATION_ERROR | MANUAL_REQUIRED
    method:      str   = "regex"
    source:      str   = "ocr"
    evidence:    str   = ""
    is_critical: bool  = False
    validation_message: str = ""


# ── Summary ───────────────────────────────────────────────────────────────────

@dataclass
class ConfidenceSummary:
    fields:                 list[FieldConfidenceResult] = field(default_factory=list)
    auto_filled_count:      int   = 0
    need_review_count:      int   = 0
    missing_count:          int   = 0
    validation_error_count: int   = 0
    manual_required_count:  int   = 0
    export_allowed:         bool  = True
    overall_confidence:     float = 0.0


# ── Main engine ───────────────────────────────────────────────────────────────

def score_fields(
    mapped_fields:    dict[str, Any],
    validation_errors: list[str] | None = None,
    template_statuses: dict[str, str] | None = None,
) -> ConfidenceSummary:
    """
    Score all fields and return a ConfidenceSummary.

    Args:
        mapped_fields:     dict of {field_name: {value, confidence, method, source, source_evidence}}
                           or {field_name: raw_value_str}
        validation_errors: list of error strings from validation_engine
        template_statuses: optional dict of {field_name: status} from template_engine
                           (MANUAL_REQUIRED fields come through here)
    """
    # Build validation error index: field_name → [messages]
    val_err_index: dict[str, list[str]] = {}
    for msg in (validation_errors or []):
        # Messages typically start with "field_name: ..." or contain "field_name"
        for fname in CRITICAL_FIELDS | set(mapped_fields.keys()):
            if fname.lower() in msg.lower():
                val_err_index.setdefault(fname, []).append(msg)

    results: list[FieldConfidenceResult] = []
    conf_sum = 0.0
    conf_count = 0

    for field_name, data in mapped_fields.items():
        if field_name == "item_rows":
            continue

        # Parse incoming field data
        if isinstance(data, dict):
            value      = str(data.get("value", "") or "").strip()
            confidence = float(data.get("confidence", 0.0) or 0.0)
            method     = data.get("method", "regex")
            source     = data.get("source", "ocr")
            evidence   = str(data.get("source_evidence", "") or data.get("evidence", "") or "")[:120]
        else:
            value      = str(data or "").strip()
            confidence = 0.5 if value else 0.0
            method     = "regex"
            source     = "ocr"
            evidence   = ""

        is_critical = field_name in CRITICAL_FIELDS
        val_errors  = val_err_index.get(field_name, [])
        val_msg     = "; ".join(val_errors) if val_errors else ""

        # Override with template status if provided
        tpl_status = (template_statuses or {}).get(field_name)
        if tpl_status == "MANUAL_REQUIRED":
            status = "MANUAL_REQUIRED"
        elif val_errors:
            status = "VALIDATION_ERROR"
        elif not value:
            status = "MISSING"
        elif method == "gemini":
            # AI-recovered values always go to review
            status = "NEED_REVIEW"
        elif confidence >= HIGH_THRESHOLD:
            status = "AUTO_FILLED"
        elif confidence >= REVIEW_THRESHOLD:
            status = "NEED_REVIEW"
        else:
            # Low confidence AND value present → NEED_REVIEW (not MISSING)
            status = "NEED_REVIEW"

        if value and status not in ("MISSING", "MANUAL_REQUIRED"):
            conf_sum   += confidence
            conf_count += 1

        results.append(FieldConfidenceResult(
            field_name=field_name,
            value=value,
            confidence=round(confidence, 3),
            status=status,
            method=method,
            source=source,
            evidence=evidence,
            is_critical=is_critical,
            validation_message=val_msg,
        ))

    # ── Summary counts ────────────────────────────────────────────────────────
    summary = ConfidenceSummary(fields=results)
    for r in results:
        if r.status == "AUTO_FILLED":
            summary.auto_filled_count += 1
        elif r.status == "NEED_REVIEW":
            summary.need_review_count += 1
        elif r.status == "MISSING":
            summary.missing_count += 1
        elif r.status == "VALIDATION_ERROR":
            summary.validation_error_count += 1
        elif r.status == "MANUAL_REQUIRED":
            summary.manual_required_count += 1

    summary.overall_confidence = round(conf_sum / conf_count, 3) if conf_count else 0.0

    # Export allowed unless critical field is MISSING or VALIDATION_ERROR
    blocked = any(
        r.is_critical and r.status in ("MISSING", "VALIDATION_ERROR")
        for r in results
    )
    summary.export_allowed = not blocked

    return summary


def build_field_response(summary: ConfidenceSummary) -> list[dict]:
    """Serialize ConfidenceSummary fields to API response format."""
    return [
        {
            "field_name":          r.field_name,
            "value":               r.value,
            "confidence":          r.confidence,
            "status":              r.status,
            "method":              r.method,
            "source":              r.source,
            "evidence":            r.evidence,
            "is_critical":         r.is_critical,
            "validation_message":  r.validation_message,
        }
        for r in summary.fields
    ]
