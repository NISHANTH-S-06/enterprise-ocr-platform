"""field_normalizer.py — Phase 2B: Canonical field output + export lock.

Responsibilities:
  1. Build canonical_fields dict from raw mapped_fields (alias-collapsed, deduplicated)
  2. Detect and reject placeholder/header values for critical fields
  3. Score each canonical field: AUTO_FILLED | NEED_REVIEW | MISSING | VALIDATION_ERROR
  4. Block export if any critical field is NEED_REVIEW, MISSING, or VALIDATION_ERROR
  5. Prefer structured item-table fields over generic quantity/unit_price
  6. Return review_fields, blocked_fields, export_block_reasons

This module does NOT modify the existing p2_fields / detection_report output.
It adds new keys: canonical_fields, review_fields, blocked_fields,
                   export_allowed, export_block_reasons,
                   auto_filled_count, need_review_count,
                   missing_count, validation_error_count
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# ── Canonical field set (UI-facing, deduplicated) ────────────────────────────
# These are the ONLY fields that appear in canonical_fields output.
# All aliases collapse into one of these.
CANONICAL_FIELDS_ORDERED = [
    # Identity
    "invoice_number", "invoice_date", "document_type",
    "po_number", "po_date",
    # Parties
    "vendor_name", "company_name", "buyer_name", "consignee_name",
    "gst_number", "iec_code", "pan_number",
    # Address
    "address", "billing_address", "shipping_address",
    # Contact
    "phone", "email",
    # Shipping
    "port_of_loading", "port_of_discharge", "mode_of_transport",
    "final_destination", "country_of_origin",
    "gross_weight", "net_weight", "total_cartons",
    "vessel_flight_no", "container_number", "seal_number",
    "awb_number", "eway_bill_number", "lr_number", "vehicle_number",
    # Line items (prefer item_rows; quantity/rate are GENERIC fallbacks)
    "item_description", "hsn_code",
    "quantity", "rate", "taxable_value",
    # Tax
    "cgst", "sgst", "igst", "igst_percent", "total_tax",
    # Totals
    "grand_total", "total_amount", "currency", "exchange_rate",
    "amount_in_words",
    # Bank
    "bank_name", "account_number", "ifsc", "swift_code", "branch",
    # Misc
    "payment_terms", "payment_status", "notes",
    "declaration_text", "statement_of_origin",
]

# Alias → canonical mapping (alias fields are NEVER emitted as canonical output)
_ALIAS_MAP: dict[str, str] = {
    # invoice_number aliases
    "invoice_no": "invoice_number", "inv_no": "invoice_number",
    "bill_no": "invoice_number", "bill_number": "invoice_number",
    "tax_invoice_no": "invoice_number", "document_no": "invoice_number",
    "voucher_no": "invoice_number", "challan_no": "invoice_number",
    # invoice_date aliases
    "date": "invoice_date", "invoice_dt": "invoice_date",
    "bill_date": "invoice_date", "document_date": "invoice_date",
    "dated": "invoice_date", "doc_date": "invoice_date",
    # po_number aliases
    "po_no": "po_number", "purchase_order_number": "po_number",
    "order_number": "po_number", "order_no": "po_number",
    "buyer_order_no": "po_number",
    # vendor_name aliases
    "supplier_name": "vendor_name", "supplier": "vendor_name",
    "seller": "vendor_name", "exporter": "vendor_name",
    # buyer_name aliases
    "customer_name": "buyer_name", "buyer": "buyer_name",
    "bill_to": "buyer_name", "sold_to": "buyer_name",
    "party_name": "buyer_name", "importer": "buyer_name",
    # consignee aliases
    "consignee": "consignee_name", "ship_to": "consignee_name",
    "delivery_to": "consignee_name", "receiver": "consignee_name",
    "delivery_name": "consignee_name",
    # gst aliases
    "gstin": "gst_number", "gst_no": "gst_number",
    "vendor_gstin": "gst_number", "buyer_gstin": "gst_number",
    "supplier_gstin": "gst_number", "gst_supplier": "gst_number",
    # total/grand_total aliases
    "total_amount": "grand_total", "invoice_total": "grand_total",
    "net_amount": "grand_total", "amount_payable": "grand_total",
    "final_total": "grand_total", "invoice_value": "grand_total",
    "total_amount_inr": "grand_total",
    # quantity aliases
    "qty": "quantity", "total_qty": "quantity",
    "total_quantity": "quantity",
    # rate aliases
    "unit_price": "rate", "unit_rate": "rate", "price": "rate",
    # tax aliases
    "cgst_amount": "cgst", "sgst_amount": "sgst",
    "igst_amount": "igst", "igst_rate": "igst_percent",
    "tax_amount": "total_tax", "gst_amount": "total_tax",
    # address aliases
    "company_address": "billing_address",
    "buyer_address": "billing_address",
    "consignee_address": "shipping_address",
    # bank aliases
    "account_no": "account_number", "ac_no": "account_number",
    "bank_account": "account_number", "beneficiary_account": "account_number",
    "ifsc_code": "ifsc",
    # weight
    "gross_wt": "gross_weight", "net_wt": "net_weight",
    # cartons
    "cartons": "total_cartons", "total_packages": "total_cartons",
    # misc
    "taxable_amount": "taxable_value", "assessable_value": "taxable_value",
    "subtotal": "taxable_value",
    "item_name": "item_description",
    "hsn": "hsn_code", "hsn_sac": "hsn_code",
    "declaration": "declaration_text",
    "origin_statement": "statement_of_origin",
}

# Critical fields — NEED_REVIEW blocks export (in addition to MISSING/VALIDATION_ERROR)
CRITICAL_FIELDS: set[str] = {
    "invoice_number", "invoice_date",
    "vendor_name", "company_name",
    "grand_total",
}

# Fields where generic values must be rejected (placeholder/header detection)
_PLACEHOLDER_RE = re.compile(
    r"^("
    r"value|values|group|of goods|description of goods|"
    r"seal number|box serial number|description|"
    r"total|date|invoice|export invoice|tax invoice|commercial invoice|"
    r"purchase order|goods receipt|grn|invoice no|sl no|s\.?\s*no\.?|"
    r"sr no|serial no|item|qty|quantity|rate|amount|uom|"
    r"article|colour|type|size|sno|order no|hsn|sac|"
    r"cow leather|leather|goods|pcs|pair"
    r")$",
    re.I,
)

# Per-field maximum allowed string length for name fields
_NAME_MAX_LEN = 120

# High-confidence threshold
_HIGH_CONF = 0.80
_REVIEW_CONF = 0.50


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class CanonicalField:
    name:               str
    value:              str
    confidence:         float
    status:             str   # AUTO_FILLED | NEED_REVIEW | MISSING | VALIDATION_ERROR
    method:             str   = "regex"
    source:             str   = "ocr"
    evidence:           str   = ""
    is_critical:        bool  = False
    validation_message: str   = ""


@dataclass
class NormalizationResult:
    canonical_fields:        dict[str, CanonicalField]
    review_fields:           list[str]          # field names needing review
    blocked_fields:          list[str]          # field names blocking export
    export_allowed:          bool
    export_block_reasons:    list[str]
    auto_filled_count:       int = 0
    need_review_count:       int = 0
    missing_count:           int = 0
    validation_error_count:  int = 0


# ── Placeholder / header value detector ──────────────────────────────────────

def _is_placeholder(value: str, field_name: str) -> bool:
    """Return True if value looks like a form header/label, not real data."""
    v = value.strip()
    if not v:
        return False
    # Pure placeholder words
    if _PLACEHOLDER_RE.match(v):
        return True
    # Value is just digits that look like a date in DDMMYYYY format for non-date fields
    if field_name not in ("invoice_date", "po_date", "grn_date", "due_date"):
        if re.match(r"^\d{8}$", v):
            # Could be 03042026 = date misextracted into total_value
            return True
    # Name field too long (likely captured multi-line garbage)
    if field_name in ("vendor_name", "company_name", "buyer_name", "consignee_name"):
        if len(v) > _NAME_MAX_LEN:
            return True
    return False


# ── Item-table conflict resolver ──────────────────────────────────────────────

def _item_rows_summary(mapped_fields: dict[str, Any]) -> dict[str, str]:
    """Extract first-row qty and rate from item_rows if available."""
    rows_raw = mapped_fields.get("item_rows") or mapped_fields.get("items")
    rows: list[dict] = []
    if isinstance(rows_raw, dict):
        rows = rows_raw.get("value") or []
    elif isinstance(rows_raw, list):
        rows = rows_raw
    if not rows or not isinstance(rows[0], dict):
        return {}
    r0 = rows[0]
    out: dict[str, str] = {}
    for key in ("qty", "quantity"):
        if r0.get(key):
            out["quantity"] = str(r0[key])
            break
    for key in ("price", "rate", "unit_price"):
        if r0.get(key):
            out["rate"] = str(r0[key])
            break
    return out


# ── Core normalizer ───────────────────────────────────────────────────────────

def build_canonical_output(
    mapped_fields:     dict[str, Any],
    validation_errors: list[str] | None = None,
    validation_warnings: list[str] | None = None,
) -> NormalizationResult:
    """
    Collapse alias fields into canonical names, apply placeholder detection,
    score each field, and compute export_allowed.
    """
    # Build validation message index
    all_val_msgs = list(validation_errors or []) + list(validation_warnings or [])
    val_msg_index: dict[str, list[str]] = {}
    for msg in all_val_msgs:
        for fname in CANONICAL_FIELDS_ORDERED:
            if fname.lower() in msg.lower():
                val_msg_index.setdefault(fname, []).append(msg)

    # Item-table first-row values (preferred over generic quantity/rate)
    item_summary = _item_rows_summary(mapped_fields)

    # ── Step 1: Collapse aliases → canonical bucket (best confidence wins) ────
    canonical_buckets: dict[str, dict[str, Any]] = {}

    for raw_key, data in mapped_fields.items():
        if raw_key in ("item_rows", "items"):
            continue
        # Resolve canonical name
        canon = _ALIAS_MAP.get(raw_key, raw_key)
        # If not in our output set, skip (debug fields like sales_mapping_debug etc.)
        if canon not in CANONICAL_FIELDS_ORDERED:
            # Still try raw_key as canonical if it's in the output set
            if raw_key in CANONICAL_FIELDS_ORDERED:
                canon = raw_key
            else:
                continue

        # Parse data
        if isinstance(data, dict):
            value      = str(data.get("value", "") or "").strip()
            confidence = float(data.get("confidence", 0.0) or 0.0)
            method     = data.get("method", "regex")
            source     = data.get("source", "ocr")
            evidence   = str(data.get("source_evidence", "") or "")[:120]
        else:
            value      = str(data or "").strip()
            confidence = 0.5 if value else 0.0
            method     = "regex"
            source     = "ocr"
            evidence   = ""

        # Skip empty
        if not value:
            canonical_buckets.setdefault(canon, {
                "value": "", "confidence": 0.0, "method": method,
                "source": source, "evidence": evidence,
            })
            continue

        # Demote alias-sourced confidence slightly
        if method == "alias":
            confidence = min(confidence, 0.78)

        # Best-confidence wins
        existing = canonical_buckets.get(canon)
        if existing is None or (value and confidence > existing.get("confidence", 0.0)):
            canonical_buckets[canon] = {
                "value": value, "confidence": confidence,
                "method": method, "source": source, "evidence": evidence,
            }

    # ── Step 2: Override quantity/rate from item table if available ───────────
    for cf_name, item_key in (("quantity", "quantity"), ("rate", "rate")):
        item_val = item_summary.get(item_key, "")
        if item_val:
            existing = canonical_buckets.get(cf_name, {})
            # Item table is more reliable than heuristic generic extraction
            canonical_buckets[cf_name] = {
                "value":      item_val,
                "confidence": 0.85,
                "method":     "item_table",
                "source":     "item_rows",
                "evidence":   f"item_rows[0].{item_key}",
            }

    # ── Step 3: Score each canonical field ───────────────────────────────────
    result_fields: dict[str, CanonicalField] = {}

    for cf_name in CANONICAL_FIELDS_ORDERED:
        bucket   = canonical_buckets.get(cf_name, {})
        value    = bucket.get("value", "")
        conf     = float(bucket.get("confidence", 0.0) or 0.0)
        method   = bucket.get("method", "regex")
        source   = bucket.get("source", "ocr")
        evidence = bucket.get("evidence", "")
        is_crit  = cf_name in CRITICAL_FIELDS
        val_msgs = val_msg_index.get(cf_name, [])
        val_msg  = "; ".join(val_msgs) if val_msgs else ""

        # Placeholder detection
        if value and _is_placeholder(value, cf_name):
            status  = "VALIDATION_ERROR"
            val_msg = val_msg or f"Rejected placeholder value: '{value}'"
            value   = ""          # clear so it's treated as missing below
            conf    = 0.0

        if val_msgs and value:
            status = "VALIDATION_ERROR"
        elif not value:
            status = "MISSING"
        elif method == "gemini":
            status = "NEED_REVIEW"
        elif conf >= _HIGH_CONF:
            status = "AUTO_FILLED"
        elif conf >= _REVIEW_CONF:
            status = "NEED_REVIEW"
        else:
            status = "NEED_REVIEW"

        result_fields[cf_name] = CanonicalField(
            name=cf_name,
            value=value,
            confidence=round(conf, 3),
            status=status,
            method=method,
            source=source,
            evidence=evidence,
            is_critical=is_crit,
            validation_message=val_msg,
        )

    # ── Step 4: Counts ────────────────────────────────────────────────────────
    auto_filled = need_review = missing = val_error = 0
    for cf in result_fields.values():
        if cf.status == "AUTO_FILLED":
            auto_filled += 1
        elif cf.status == "NEED_REVIEW":
            need_review += 1
        elif cf.status == "MISSING":
            missing += 1
        elif cf.status == "VALIDATION_ERROR":
            val_error += 1

    # ── Step 5: Export lock — critical field NEED_REVIEW also blocks ──────────
    blocked: list[str] = []
    reasons: list[str] = []
    for cf in result_fields.values():
        if not cf.is_critical:
            continue
        if cf.status in ("MISSING", "VALIDATION_ERROR", "NEED_REVIEW"):
            blocked.append(cf.name)
            if cf.status == "MISSING":
                reasons.append(f"{cf.name}: required field is missing")
            elif cf.status == "VALIDATION_ERROR":
                reasons.append(f"{cf.name}: {cf.validation_message or 'validation failed'}")
            elif cf.status == "NEED_REVIEW":
                reasons.append(
                    f"{cf.name}: value '{cf.value}' needs review "
                    f"(confidence {cf.confidence:.0%})"
                )

    review_fields = [
        cf.name for cf in result_fields.values()
        if cf.status in ("NEED_REVIEW", "VALIDATION_ERROR")
    ]

    return NormalizationResult(
        canonical_fields=result_fields,
        review_fields=review_fields,
        blocked_fields=blocked,
        export_allowed=len(blocked) == 0,
        export_block_reasons=reasons,
        auto_filled_count=auto_filled,
        need_review_count=need_review,
        missing_count=missing,
        validation_error_count=val_error,
    )


def serialize_canonical_fields(result: NormalizationResult) -> list[dict]:
    """Serialize canonical_fields to API response list (UI-ready)."""
    return [
        {
            "field_name":          cf.name,
            "value":               cf.value,
            "confidence":          cf.confidence,
            "status":              cf.status,
            "method":              cf.method,
            "source":              cf.source,
            "evidence":            cf.evidence,
            "is_critical":         cf.is_critical,
            "validation_message":  cf.validation_message,
        }
        for cf in result.canonical_fields.values()
    ]
