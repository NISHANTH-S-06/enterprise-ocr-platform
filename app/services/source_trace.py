from __future__ import annotations
import re
from typing import Any, Dict, List, Optional

DYNAMIC_FIELDS = {
    "invoice_number", "invoice_no", "bill_number", "bill_no", "tax_invoice_no", "document_no", "voucher_no", "challan_no",
    "invoice_date", "invoice_dt", "bill_date", "document_date", "dated",
    "purchase_order_number", "po_number", "order_number", "order_no", "buyer_order_no", "po_date", "po_type", "sales_order_no", "so_number",
    "quantity", "qty", "total_qty", "quantity_total", "qty_total",
    "unit_price", "rate", "unit_rate", "price",
    "tax_amount", "total_tax", "gst_amount", "total_gst", "cgst", "sgst", "igst", "cgst_amount", "sgst_amount", "igst_amount", "other_tax_amount",
    "grand_total", "total_amount", "invoice_total", "invoice_value", "amount_payable", "net_amount", "total_amount_inr", "inr_total", "total_inr", "invoice_value_inr"
}

REUSABLE_FIELDS = {
    "salesperson", "salesperson_id", "sales_person",
    "payment_terms", "payment_term", "terms_of_payment", "payment_method",
    "freight_term", "freight_terms", "terms_of_freight", "insurance_term", "insurance_terms", "delivery_term", "delivery_terms",
    "company_address", "billing_address", "buyer_address", "consignee_address", "shipping_address", "delivery_address", "ship_to_address", "bill_to_address",
    "freight_carrier", "carrier_name", "transport_name", "carrier", "despatch_through",
    "contact_number", "contact_person", "phone", "email", "phone_no",
    "currency", "curr", "default_currency"
}


def is_field_dynamic(field_name: str) -> bool:
    name = str(field_name).lower().strip()
    return name in DYNAMIC_FIELDS or any(kw in name for kw in ("_date", "_total", "_amount", "price", "rate", "qty", "quantity"))


def is_field_reusable(field_name: str) -> bool:
    name = str(field_name).lower().strip()
    return name in REUSABLE_FIELDS or any(kw in name for kw in ("address", "terms", "carrier", "contact"))


def get_field_normalization(field_name: str, value: str) -> Optional[str]:
    """Helper to return normalized format (e.g. clean strings/amounts) for tracing."""
    if not value:
        return None
    name = field_name.lower()
    val_clean = str(value).strip()
    if any(term in name for term in ("total", "amount", "price", "rate", "quantity", "qty", "charges", "tax")):
        # Clean amount/numeric strings (e.g. "1,234.56" -> "1234.56")
        s = re.sub(r"[^\d.-]", "", val_clean)
        return s if s else val_clean
    if "date" in name:
        # Standardize date delimiters to -
        s = re.sub(r"[\/\.]", "-", val_clean)
        return s
    return val_clean


def build_source_traces(
    mapped_fields: Dict[str, Any],       # Final mapped fields (after merges)
    validated_fields: Dict[str, Any],    # Validated fields containing clean values & validation metrics
    validation_report: Dict[str, Any],   # Dictionary with errors/warnings list
    raw_mapped_fields: Optional[Dict[str, Any]] = None,  # Original regex mapped fields before training merge
    extra_meta: Optional[Dict[str, Any]] = None,        # Custom metadata (e.g., training layout, conflicts)
) -> Dict[str, Dict[str, Any]]:
    """
    Construct evidence and source trace metadata for all fields.
    """
    meta = extra_meta or {}
    raw_mapped = raw_mapped_fields or {}
    traces: Dict[str, Dict[str, Any]] = {}

    # Extract conflicts map
    conflicts_list = meta.get("training_conflicts") or []
    conflict_by_field = {c["field"]: c for c in conflicts_list if isinstance(c, dict) and "field" in c}

    # All fields to generate trace for
    all_fields = set(mapped_fields.keys()) | set(validated_fields.keys()) | set(raw_mapped.keys())

    # Build warning and error mappings for quick field lookup
    warnings = list(validation_report.get("warnings", []))
    errors = list(validation_report.get("errors", []))

    for field in sorted(all_fields):
        if field in {"item_rows", "items"}:
            continue

        # Get final values & metadata
        v_entry = validated_fields.get(field)
        m_entry = mapped_fields.get(field)
        r_entry = raw_mapped.get(field)

        # Fallback to mapped or raw mapped if validated is empty
        active_entry = v_entry if v_entry is not None else (m_entry if m_entry is not None else r_entry)

        # Value
        val = ""
        if isinstance(active_entry, dict):
            val = str(active_entry.get("value") or "").strip()
        elif active_entry is not None:
            val = str(active_entry).strip()

        # Resolve confidence
        confidence = 0.5
        if isinstance(active_entry, dict) and active_entry.get("confidence") is not None:
            try:
                confidence = float(active_entry["confidence"])
            except (ValueError, TypeError):
                pass

        # Method and Source
        method = ""
        source = ""
        evidence = ""
        if isinstance(active_entry, dict):
            method = str(active_entry.get("method") or "").lower()
            source = str(active_entry.get("source") or "").lower()
            evidence = str(active_entry.get("source_evidence") or active_entry.get("evidence") or "")

        # Default source properties if missing
        if not source:
            source = method if method else "regex"
        if not method:
            method = "regex"

        # Determine source_type and source_engine
        source_type = "unknown"
        source_engine = "regex"

        # Check manual correction first
        if source == "user_correction" or method == "manual_correction" or "correction" in source or "correction" in method:
            source_type = "manual_correction"
            source_engine = "user"
        elif source == "correction_memory" or "memory" in source or "memory" in method:
            source_type = "manual_correction"
            source_engine = "correction_memory"
        # Check fallback
        elif method == "gemini" or source == "gemini" or "fallback" in source or "fallback" in method:
            source_type = "fallback"
            source_engine = "gemini"
        # Check erp master
        elif source == "erp_master" or method == "erp_master":
            source_type = "erp_master"
            source_engine = "erp_master_mappings"
        # Check calculated / derived total
        elif source == "calculated" or method == "calculated" or "calculated_value" in source:
            source_type = "calculated_value"
            source_engine = "validation_engine"
        # Check training dataset match
        elif source == "training" or method == "training_template" or "training" in source:
            source_type = "training_dataset"
            source_engine = "training_template"
        # Check current document extraction
        elif source in {"regex", "ocr", "template", ""} or method in {"regex", "near_label", "ocr"}:
            source_type = "current_document"
            # Extract engine name from meta or default to paddleocr/tesseract
            source_engine = meta.get("ocr_engine") or meta.get("engine") or "paddleocr"

        # Overwrite value_source if explicitly tagged
        if isinstance(active_entry, dict) and active_entry.get("value_source"):
            source_type = str(active_entry["value_source"])

        # Flags for supplied
        is_training_supplied = source_type == "training_dataset"
        # Was it originally found in the current document (via regex)?
        is_current_doc_supplied = False
        if r_entry is not None:
            r_val = str(r_entry.get("value") if isinstance(r_entry, dict) else r_entry).strip()
            if r_val:
                is_current_doc_supplied = True

        # Validation status and reasons
        validation_status = "valid"
        validation_reason_list = []
        for w in warnings:
            if f"'{field}'" in w or f" {field}:" in w or w.startswith(f"{field}:"):
                validation_reason_list.append(w)
        for e in errors:
            if f"'{field}'" in e or f" {field}:" in e or e.startswith(f"{field}:"):
                validation_reason_list.append(e)

        validation_reason = "; ".join(validation_reason_list) if validation_reason_list else None
        if validation_reason_list:
            validation_status = "invalid"

        # Conflict status
        conflict_status = None
        if field in conflict_by_field:
            conflict_status = str(conflict_by_field[field].get("action"))

        # Template Metadata
        template_id = meta.get("training_template_id") if is_training_supplied else None
        template_name = meta.get("training_template_name") if is_training_supplied else None

        # Build final trace record
        traces[field] = {
            "field_name": field,
            "value": val,
            "normalized_value": get_field_normalization(field, val),
            "source_type": source_type,
            "source_engine": source_engine,
            "confidence": confidence,
            "validation_status": validation_status,
            "validation_reason": validation_reason,
            "page_number": None,  # Not currently supported in raw layout
            "evidence_text": evidence if evidence else None,
            "template_id": template_id,
            "template_name": template_name,
            "is_training_supplied": is_training_supplied,
            "is_current_document_supplied": is_current_doc_supplied,
            "is_dynamic_field": is_field_dynamic(field),
            "is_reusable_field": is_field_reusable(field),
            "conflict_status": conflict_status,
        }

    return traces
