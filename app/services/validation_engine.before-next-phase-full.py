"""validation_engine.py — Extended validation rules for ERP OCR pipeline.

Rules implemented:
  1.  GST number format (15-char GSTIN)
  2.  IEC code format (10 alphanumeric)
  3.  Date format + range (DD-MM-YYYY variants, 2000–2035)
  4.  HSN code numeric (4–8 digits)
  5.  qty * rate ≈ amount (line-item cross-check)
  6.  taxable_value + tax ≈ grand_total
  7.  IGST/CGST+SGST mutual exclusion (interstate vs intrastate)
  8.  Currency code (ISO 4217 subset)
  9.  Bank account number format (9–18 digits)
  10. IFSC format

Each rule returns (ok: bool, severity: str, message: str).
severity: 'critical' | 'high' | 'medium' | 'low'

Public API:
  run_validation(mapped_fields, raw_text, doc_type) → ValidationReport
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# ── Patterns ──────────────────────────────────────────────────────────────────
_GST_RE      = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
_IEC_RE      = re.compile(r"^[A-Z0-9]{10}$")
_IFSC_RE     = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
_SWIFT_RE    = re.compile(r"^[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}([A-Z0-9]{3})?$")
_HSN_RE      = re.compile(r"^\d{4,8}$")
_BANK_AC_RE  = re.compile(r"^\d{9,18}$")
_CURRENCY_RE = re.compile(r"^(USD|EUR|INR|GBP|AED|JPY|AUD|CAD|SGD|CHF|CNY)$", re.I)
_DATE_RES    = [
    re.compile(r"^(\d{1,2})[\/\-\.](\d{1,2})[\/\-\.](\d{2,4})$"),
    re.compile(r"^(\d{4})[\/\-\.](\d{1,2})[\/\-\.](\d{1,2})$"),
]


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class RuleResult:
    rule_name:  str
    field_name: str
    status:     str   # pass | fail | warning | skipped
    severity:   str   # critical | high | medium | low | info
    message:    str   = ""


@dataclass
class ValidationReport:
    valid:            bool
    errors:           list[str]       = field(default_factory=list)    # critical failures
    warnings:         list[str]       = field(default_factory=list)    # non-blocking
    rule_results:     list[RuleResult] = field(default_factory=list)
    validated_fields: dict[str, Any]  = field(default_factory=dict)
    field_count:      int             = 0


# ── Helpers ───────────────────────────────────────────────────────────────────

def _val(mapped: dict[str, Any], key: str) -> str:
    v = mapped.get(key, "")
    if isinstance(v, dict):
        v = v.get("value", "")
    return str(v or "").strip()


def _to_float(s: str) -> float | None:
    try:
        return float(s.replace(",", ""))
    except (ValueError, AttributeError):
        return None


def _rule(rule_name: str, field_name: str, ok: bool, severity: str, message: str) -> RuleResult:
    return RuleResult(
        rule_name=rule_name,
        field_name=field_name,
        status="pass" if ok else ("warning" if severity in ("low", "medium") else "fail"),
        severity=severity,
        message=message if not ok else "",
    )


# ── Individual rule functions ─────────────────────────────────────────────────

def _check_gst(mapped: dict[str, Any]) -> list[RuleResult]:
    results = []
    for fname in ("gst_number", "gstin", "buyer_gstin", "vendor_gstin"):
        v = _val(mapped, fname).upper().replace(" ", "")
        if not v:
            continue
        ok = bool(_GST_RE.match(v))
        results.append(_rule("gst_format", fname, ok, "medium",
                             f"Invalid GST: '{v}' — expected 15-char GSTIN"))
    return results


def _check_iec(mapped: dict[str, Any]) -> list[RuleResult]:
    v = _val(mapped, "iec_code").upper().replace(" ", "")
    if not v:
        return []
    ok = bool(_IEC_RE.match(v))
    return [_rule("iec_format", "iec_code", ok, "medium",
                  f"Invalid IEC: '{v}' — expected 10 alphanumeric chars")]


def _check_dates(mapped: dict[str, Any]) -> list[RuleResult]:
    from datetime import datetime
    results = []
    for fname in ("invoice_date", "po_date", "grn_date", "due_date", "shipment_date"):
        v = _val(mapped, fname)
        if not v:
            continue
        matched = False
        for r in _DATE_RES:
            m = r.match(v)
            if m:
                parts = [int(x) for x in m.groups()]
                if parts[0] > 31:
                    y, mo, d = parts
                else:
                    d, mo, y = parts
                    if y < 100:
                        y += 2000
                try:
                    dt = datetime(y, mo, d)
                    ok = 2000 <= dt.year <= 2035
                    results.append(_rule("date_format", fname, ok, "medium",
                                        f"Date '{v}' out of valid range (2000–2035)"))
                except ValueError:
                    results.append(_rule("date_format", fname, False, "medium",
                                        f"Invalid date values in '{v}'"))
                matched = True
                break
        if not matched:
            results.append(_rule("date_format", fname, False, "low",
                                 f"Unrecognised date format: '{v}'"))
    return results


def _check_hsn(mapped: dict[str, Any]) -> list[RuleResult]:
    v = _val(mapped, "hsn_code").replace(" ", "")
    if not v:
        return []
    ok = bool(_HSN_RE.match(v))
    return [_rule("hsn_numeric", "hsn_code", ok, "low",
                  f"HSN must be 4–8 digits, got: '{v}'")]


def _check_line_item_math(mapped: dict[str, Any]) -> list[RuleResult]:
    """qty * rate ≈ amount for each line item in item_rows."""
    results = []
    item_rows_raw = mapped.get("item_rows", "")
    items: list[dict] = []
    if isinstance(item_rows_raw, dict):
        items = item_rows_raw.get("value", []) or []
    elif isinstance(item_rows_raw, list):
        items = item_rows_raw

    for i, row in enumerate(items or []):
        if not isinstance(row, dict):
            continue
        qty   = _to_float(str(row.get("quantity", row.get("qty", "")) or ""))
        rate  = _to_float(str(row.get("rate", row.get("unit_price", "")) or ""))
        amt   = _to_float(str(row.get("amount", row.get("total", "")) or ""))
        if qty is not None and rate is not None and amt is not None and amt > 0:
            expected = round(qty * rate, 2)
            ok = abs(expected - amt) <= max(1.0, amt * 0.01)   # 1% tolerance
            if not ok:
                results.append(_rule(
                    "line_item_math", f"item_rows[{i}]", ok, "medium",
                    f"Row {i}: qty({qty}) × rate({rate}) = {expected}, got amount={amt}",
                ))
    return results


def _check_grand_total(mapped: dict[str, Any]) -> list[RuleResult]:
    """taxable_value + total_tax ≈ grand_total (10% tolerance)."""
    taxable = _to_float(_val(mapped, "taxable_value") or _val(mapped, "subtotal"))
    tax     = _to_float(_val(mapped, "total_tax") or _val(mapped, "tax_amount"))
    total   = _to_float(_val(mapped, "grand_total") or _val(mapped, "total_amount"))
    if taxable is None or tax is None or total is None or total == 0:
        return []
    expected = round(taxable + tax, 2)
    ok = abs(expected - total) <= max(1.0, total * 0.10)
    return [_rule("grand_total_check", "grand_total", ok, "high",
                  f"taxable({taxable}) + tax({tax}) = {expected}, but grand_total={total}")]


def _check_gst_tax_logic(mapped: dict[str, Any]) -> list[RuleResult]:
    """IGST and CGST/SGST should not both be non-zero."""
    igst  = _to_float(_val(mapped, "igst") or _val(mapped, "igst_amount"))
    cgst  = _to_float(_val(mapped, "cgst") or _val(mapped, "cgst_amount"))
    sgst  = _to_float(_val(mapped, "sgst") or _val(mapped, "sgst_amount"))

    if igst is None and cgst is None and sgst is None:
        return []
    igst_  = igst  or 0.0
    cgst_  = cgst  or 0.0
    sgst_  = sgst  or 0.0
    if igst_ > 0 and (cgst_ > 0 or sgst_ > 0):
        return [_rule("igst_cgst_exclusion", "igst", False, "high",
                      f"Both IGST({igst_}) and CGST/SGST({cgst_}/{sgst_}) are non-zero — "
                      "inter-state uses IGST only; intra-state uses CGST+SGST")]
    return []


def _check_currency(mapped: dict[str, Any]) -> list[RuleResult]:
    v = _val(mapped, "currency").upper().strip()
    if not v:
        return []
    ok = bool(_CURRENCY_RE.match(v))
    return [_rule("currency_code", "currency", ok, "low",
                  f"Unrecognised currency code: '{v}'")]


def _check_bank_account(mapped: dict[str, Any]) -> list[RuleResult]:
    results = []
    for fname in ("account_number", "account_no"):
        v = _val(mapped, fname).replace(" ", "").replace("-", "")
        if not v:
            continue
        ok = bool(_BANK_AC_RE.match(v))
        results.append(_rule("bank_account_format", fname, ok, "low",
                             f"Bank account '{v}' should be 9–18 digits"))
    return results


def _check_ifsc(mapped: dict[str, Any]) -> list[RuleResult]:
    v = _val(mapped, "ifsc").upper().replace(" ", "")
    if not v:
        return []
    ok = bool(_IFSC_RE.match(v))
    return [_rule("ifsc_format", "ifsc", ok, "medium",
                  f"Invalid IFSC: '{v}' — expected XXXX0XXXXXX")]


# ── Public API ────────────────────────────────────────────────────────────────

def run_validation(
    mapped_fields: dict[str, Any],
    raw_text:      str  = "",
    doc_type:      str  = "general",
) -> ValidationReport:
    """
    Run all validation rules on mapped_fields.
    Returns ValidationReport with errors (hard), warnings (soft), rule_results.
    """
    all_rules: list[RuleResult] = []
    all_rules.extend(_check_gst(mapped_fields))
    all_rules.extend(_check_iec(mapped_fields))
    all_rules.extend(_check_dates(mapped_fields))
    all_rules.extend(_check_hsn(mapped_fields))
    all_rules.extend(_check_line_item_math(mapped_fields))
    all_rules.extend(_check_grand_total(mapped_fields))
    all_rules.extend(_check_gst_tax_logic(mapped_fields))
    all_rules.extend(_check_currency(mapped_fields))
    all_rules.extend(_check_bank_account(mapped_fields))
    all_rules.extend(_check_ifsc(mapped_fields))

    # Required fields per doc type
    req_map: dict[str, list[str]] = {
        "sales":              ["invoice_no", "invoice_date", "grand_total"],
        "sales_invoice":      ["invoice_no", "invoice_date", "grand_total"],
        "export_invoice":     ["invoice_no", "invoice_date", "grand_total", "iec_code"],
        "commercial_invoice": ["invoice_no", "invoice_date", "grand_total"],
        "purchase_order":     ["po_number", "po_date"],
        "grn":                ["grn_number"],
        "general":            [],
    }
    for rf in req_map.get(doc_type.lower(), []):
        v = _val(mapped_fields, rf)
        if not v:
            all_rules.append(RuleResult(
                rule_name="required_field",
                field_name=rf,
                status="fail",
                severity="high",
                message=f"Required field missing for {doc_type}: '{rf}'",
            ))

    # Classify
    errors:   list[str] = []
    warnings: list[str] = []
    for r in all_rules:
        if not r.message:
            continue
        if r.status == "fail" and r.severity in ("critical", "high"):
            errors.append(f"{r.field_name}: {r.message}")
        else:
            warnings.append(f"{r.field_name}: {r.message}")

    # Build validated_fields (pass-through with clean values)
    validated: dict[str, Any] = {}
    for fname, data in mapped_fields.items():
        if isinstance(data, dict):
            validated[fname] = data
        else:
            validated[fname] = {"value": str(data or ""), "confidence": 0.5}

    return ValidationReport(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        rule_results=all_rules,
        validated_fields=validated,
        field_count=len(validated),
    )
