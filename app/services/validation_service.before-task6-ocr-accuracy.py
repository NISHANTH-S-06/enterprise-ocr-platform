"""
validation_service.py — Field-level validation for OCR extracted data.
Rules: GST format, date format, amount sanity, duplicate detection, mock guard.
No external deps — pure Python + regex.
"""
from __future__ import annotations
import re
import hashlib
from datetime import datetime
from typing import Any, Dict, List, Tuple

# ── In-process duplicate registry (reset on restart; sufficient for demo) ────
_SEEN_HASHES: set[str] = set()

GST_RE   = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
IEC_RE = re.compile(r"^[A-Z0-9]{10}$")
IFSC_RE = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
SWIFT_RE = re.compile(r"^[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}([A-Z0-9]{3})?$")
CURRENCY_RE = re.compile(r"^(USD|EUR|INR|GBP|AED|JPY|AUD|CAD|SGD|CHF|CNY)$", re.I)
DATE_RES = [
    re.compile(r"^(\d{1,2})[\/\-\.](\d{1,2})[\/\-\.](\d{2,4})$"),
    re.compile(r"^(\d{4})[\/\-\.](\d{1,2})[\/\-\.](\d{1,2})$"),
]
AMOUNT_RE = re.compile(r"^[0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{1,2})?$|^[0-9]+(?:\.[0-9]{1,2})?$")
MOCK_SIGNALS = {"mock ocr", "install paddlepaddle", "no real ocr engine", "ocr extraction failed"}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _str(v: Any) -> str:
    return str(v).strip() if v is not None else ""


def _validate_gst(value: str) -> Tuple[bool, str]:
    v = _str(value).upper().replace(" ", "")
    if not v:
        return True, ""   # optional field — absence is not an error
    return bool(GST_RE.match(v)), f"Invalid GST format: '{v}' (expected 15-char GSTIN)"


def _validate_date(value: str) -> Tuple[bool, str]:
    v = _str(value)
    if not v:
        return True, ""
    for r in DATE_RES:
        m = r.match(v)
        if m:
            parts = [int(x) for x in m.groups()]
            # Determine Y/M/D order
            if parts[0] > 31:  # YYYY-MM-DD
                y, mo, d = parts[0], parts[1], parts[2]
            else:              # DD-MM-YYYY or DD-MM-YY
                d, mo, y = parts[0], parts[1], parts[2]
                if y < 100:
                    y += 2000
            try:
                dt = datetime(y, mo, d)
                if dt.year < 2000 or dt.year > 2035:
                    return False, f"Date out of range: '{v}'"
                return True, ""
            except ValueError:
                return False, f"Invalid date values: '{v}'"
    return False, f"Unrecognised date format: '{v}'"


def _validate_amount(value: str) -> Tuple[bool, str]:
    v = _str(value)
    if not v:
        return True, ""
    if not AMOUNT_RE.match(v):
        return False, f"Invalid amount format: '{v}'"
    num = float(v.replace(",", ""))
    if num < 0:
        return False, f"Negative amount: '{v}'"
    return True, ""


def _validate_pattern(value: str, pattern: re.Pattern[str], label: str) -> Tuple[bool, str]:
    v = _str(value).upper().replace(" ", "")
    if not v:
        return True, ""
    return bool(pattern.match(v)), f"Invalid {label} format: '{v}'"


def _grand_total_check(fields: Dict[str, Any]) -> List[str]:
    """Warn if grand_total and total_amount diverge by more than ±5."""
    errors = []
    gt  = _str(fields.get("grand_total", {}).get("value") if isinstance(fields.get("grand_total"), dict) else fields.get("grand_total", ""))
    tot = _str(fields.get("total_amount", {}).get("value") if isinstance(fields.get("total_amount"), dict) else fields.get("total_amount", ""))
    if gt and tot:
        try:
            gv = float(gt.replace(",", ""))
            tv = float(tot.replace(",", ""))
            if abs(gv - tv) > 5.0:
                errors.append(f"grand_total ({gt}) and total_amount ({tot}) diverge — review manually")
        except ValueError:
            pass
    return errors


def _duplicate_check(doc_no: str, doc_date: str, gst: str) -> Tuple[bool, str]:
    key = f"{doc_no}|{doc_date}|{gst}".lower().strip()
    h = hashlib.sha256(key.encode()).hexdigest()[:16]
    if h in _SEEN_HASHES:
        return False, f"Duplicate document detected: doc_no='{doc_no}' date='{doc_date}'"
    _SEEN_HASHES.add(h)
    return True, ""


def _mock_guard(raw_text: str) -> List[str]:
    low = (raw_text or "").lower()
    return [f"Mock OCR data detected — real engine required"]  \
        if any(sig in low for sig in MOCK_SIGNALS) else []


# ── Public API ────────────────────────────────────────────────────────────────

def validate_mapped_fields(
    mapped_fields: Dict[str, Any],
    raw_text: str = "",
    doc_type: str = "general",
) -> Dict[str, Any]:
    """
    Validate mapped_fields dict (value may be str or {value, confidence}).
    Returns:
        {
            valid: bool,
            errors: [str],        # hard errors — block export
            warnings: [str],      # soft issues — allow export with flag
            validated_fields: dict  # same shape as mapped_fields, cleaned values
        }
    """
    errors: List[str] = []
    warnings: List[str] = []

    def get_val(field: str) -> str:
        v = mapped_fields.get(field, "")
        return _str(v.get("value") if isinstance(v, dict) else v)

    # Mock guard (hard error)
    errors.extend(_mock_guard(raw_text))

    # GST validation
    gst = get_val("gst_number")
    ok, msg = _validate_gst(gst)
    if not ok:
        warnings.append(msg)   # warn, not block (OCR may mis-read 1 char)
    for field, pattern, label in (
        ("iec_code", IEC_RE, "IEC"),
        ("ifsc", IFSC_RE, "IFSC"),
        ("swift_code", SWIFT_RE, "SWIFT"),
        ("currency", CURRENCY_RE, "currency"),
    ):
        ok, msg = _validate_pattern(get_val(field), pattern, label)
        if not ok:
            warnings.append(f"{field}: {msg}")

    # Date validation
    for df in ("invoice_date", "due_date", "po_date", "grn_date"):
        dv = get_val(df)
        ok, msg = _validate_date(dv)
        if not ok:
            warnings.append(f"{df}: {msg}")

    # Amount validation
    for af in ("total_amount", "grand_total", "igst_amount", "subtotal"):
        av = get_val(af)
        ok, msg = _validate_amount(av)
        if not ok:
            warnings.append(f"{af}: {msg}")

    # Cross-check totals
    warnings.extend(_grand_total_check(mapped_fields))

    # Required fields by doc_type
    required_map = {
        "sales":    ["invoice_number", "consignee_name", "grand_total"],
        "sales_order": ["sales_order_date", "customer_id", "bill_to_address", "ship_to_address", "delivery_date", "order_total"],
        "general":  [],
        "grn":      ["grn_number"],
        "purchase": ["po_number"],
        "purchase_order": ["po_number", "po_date", "supplier_id", "delivery_date", "po_grand_total"],
    }
    required = required_map.get(doc_type, [])
    for rf in required:
        if not get_val(rf):
            warnings.append(f"Required field missing: '{rf}'")

    # Duplicate check
    doc_no   = get_val("invoice_number") or get_val("po_number") or get_val("grn_number") or ""
    doc_date = get_val("invoice_date")   or get_val("po_date")   or get_val("grn_date")   or ""
    gst_key  = gst
    if doc_no and doc_date:
        ok, msg = _duplicate_check(doc_no, doc_date, gst_key)
        if not ok:
            warnings.append(msg)

    # Build validated_fields: strip confirmed label-only values.
    # Intentionally narrow — only exact multi-word document titles, not single words
    # like "invoice" which could be part of a valid value (mapper already guards these).
    LABEL_TRASH = re.compile(
        r"^(invoice|invoice no|description of goods|group|consignee|buyer|"
        r"buyer if other than consignee|delivery at|grand total|statement of origin|"
        r"declaration|export invoice|commercial invoice|purchase order|"
        r"goods receipt note|tax invoice|original|duplicate)$", re.I
    )
    validated: Dict[str, Any] = {}
    for field, data in mapped_fields.items():
        if isinstance(data, dict):
            val = _str(data.get("value", ""))
            conf = float(data.get("confidence", 0.5) or 0.5)
            # Preserve detection metadata through validation
            method = data.get("method", "regex")
            evidence = data.get("source_evidence", "")
        else:
            val = _str(data)
            conf = 0.5
            method = "regex"
            evidence = ""
        if field == "item_rows":
            validated[field] = data
            continue
        if field == "items":
            validated[field] = data
            continue
        if LABEL_TRASH.match(val):
            warnings.append(f"Field '{field}' has label-only value '{val}' — cleared")
            continue
        entry: Dict[str, Any] = {"value": val, "confidence": conf}
        source = data.get("source", method) if isinstance(data, dict) else method
        if source:
            entry["source"] = source
        if method and method != "regex":
            entry["method"] = method
        if evidence:
            entry["source_evidence"] = evidence
        validated[field] = entry

    return {
        "valid":             len(errors) == 0,
        "errors":            errors,
        "warnings":          warnings,
        "validated_fields":  validated,
        "field_count":       len(validated),
    }
