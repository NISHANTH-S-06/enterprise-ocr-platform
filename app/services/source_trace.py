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
        elif source in ("erp_master", "master_match") or method in ("erp_master", "master_match"):
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

        # Human Review Workflow fields (Phase 2B)
        user_reviews = (extra_meta or {}).get("user_reviews") or {}
        field_review = user_reviews.get(field) or {}

        user_action = field_review.get("user_action")
        reviewer_note = field_review.get("reviewer_note")
        proposed_val = field_review.get("proposed_value")
        if proposed_val is None:
            if raw_mapped_fields and field in raw_mapped_fields:
                r_entry = raw_mapped_fields.get(field)
                proposed_val = str(r_entry.get("value") if isinstance(r_entry, dict) else r_entry).strip()
            else:
                proposed_val = val

        if not user_action and source_type == "manual_correction":
            user_action = "correct"

        final_val = val
        current_val = val

        # Calculate review status and reasons
        reasons_req = []

        if validation_status == "invalid":
            reasons_req.append("Validation status is invalid")

        if confidence < 0.80:
            reasons_req.append(f"Confidence score {confidence:.2f} is below threshold 0.80")

        doc_type_lower = str((extra_meta or {}).get("document_type") or (extra_meta or {}).get("doc_type") or "general").lower()
        req_map_fields = {
            "sales":              {"invoice_no", "invoice_date", "grand_total"},
            "sales_order":        {"sales_order_date", "customer_id", "bill_to_address", "ship_to_address", "delivery_date", "order_total"},
            "sales_invoice":      {"invoice_no", "invoice_date", "grand_total"},
            "export_invoice":     {"invoice_no", "invoice_date", "grand_total", "iec_code"},
            "commercial_invoice": {"invoice_no", "invoice_date", "grand_total"},
            "purchase_order":     {"po_number", "po_date", "supplier_id", "delivery_date", "po_grand_total"},
            "grn":                {"grn_number"},
        }
        required_set = set(req_map_fields.get(doc_type_lower, []))
        from app.services.confidence_engine import CRITICAL_FIELDS as CONF_CRITICAL_FIELDS
        is_required = field in required_set or field in CONF_CRITICAL_FIELDS

        is_placeholder = val.upper() in {"N/A", "NA", "NONE", "NULL", "-", "--", "UNKNOWN"}
        is_missing = (not val) or is_placeholder

        if is_missing and is_required:
            reasons_req.append("Required field is missing")

        if conflict_status is not None:
            reasons_req.append(f"Current document value conflicts with training value (action: {conflict_status})")

        if is_training_supplied and is_field_dynamic(field):
            reasons_req.append("Dynamic field was supplied from training template")

        if source_type == "unknown" or not source_type:
            reasons_req.append("Source type is unknown")

        if is_placeholder:
            reasons_req.append(f"Value is placeholder: '{val}'")

        if user_action == "accept":
            review_status = "accepted"
            review_reason = "Accepted by reviewer."
        elif user_action == "correct":
            review_status = "corrected"
            review_reason = f"Corrected by reviewer. Proposed value was '{proposed_val}'."
        elif user_action == "reject":
            review_status = "rejected"
            review_reason = "Rejected by reviewer."
        elif user_action == "not_available":
            review_status = "rejected"
            review_reason = "Marked as not available by reviewer."
        elif validation_status == "invalid":
            review_status = "validation_failed"
            review_reason = "; ".join(reasons_req)
        elif is_missing and is_required:
            review_status = "missing"
            review_reason = "; ".join(reasons_req)
        elif conflict_status is not None:
            review_status = "conflict"
            review_reason = "; ".join(reasons_req)
        elif reasons_req:
            review_status = "needs_review"
            review_reason = "; ".join(reasons_req)
        elif is_training_supplied:
            review_status = "training_supplied"
            review_reason = "Value supplied from matched training dataset template."
        else:
            review_status = "auto_approved"
            review_reason = "Field meets all validation and confidence requirements."

        # ERP Intelligence trace data
        erp_intel_trace = None
        erp_intel_payload = meta.get("erp_intelligence")
        if erp_intel_payload:
            master_match = erp_intel_payload.get("master_match") or {}
            po_grn_match = erp_intel_payload.get("po_grn_invoice_match") or {}
            tax_val = erp_intel_payload.get("tax_validation") or {}

            # 1. Master matching details
            if master_match and field in ("supplier_id", "customer_id", "supplier_name", "vendor_name", "customer_name", "buyer_name"):
                erp_intel_trace = {
                    "source_type": "erp_master",
                    "source_engine": "erp_master_mappings",
                    "confidence": float(master_match.get("confidence", 0.0)),
                    "reason": "; ".join(master_match.get("reasons", [])),
                    "validation_status": "valid" if master_match.get("status") in ("exact_master_match", "likely_master_match") else "invalid",
                    "review_required": bool(master_match.get("review_required", False))
                }
            # 2. PO / GRN matching details
            elif po_grn_match and po_grn_match.get("status") != "not_applicable" and field in ("po_number", "purchase_order_number", "grn_number", "invoice_no", "invoice_number"):
                field_reasons = [r for r in po_grn_match.get("reasons", []) if field in r or ("invoice" in field and "invoice" in r)]
                if field_reasons or po_grn_match.get("status") == "missing_po_grn":
                    erp_intel_trace = {
                        "source_type": "three_way_match",
                        "source_engine": "three_way_matching_service",
                        "confidence": float(po_grn_match.get("confidence", 0.0)),
                        "reason": "; ".join(po_grn_match.get("reasons", [])),
                        "validation_status": "valid" if po_grn_match.get("status") == "matched" else "invalid",
                        "review_required": bool(po_grn_match.get("review_required", False))
                    }
            # 3. Tax / GST validation details
            elif tax_val and field in ("grand_total", "subtotal", "taxable_value", "total_tax", "tax_amount", "igst", "cgst", "sgst", "gst_number", "gstin"):
                relevant_warnings = [w for w in tax_val.get("warnings", []) + tax_val.get("export_blockers", []) if field in w or ("total" in field and "total" in w) or ("tax" in field and "tax" in w) or ("gst" in field and "gst" in w)]
                if relevant_warnings:
                    erp_intel_trace = {
                        "source_type": "tax_validation",
                        "source_engine": "tax_validation_service",
                        "confidence": 1.0,
                        "reason": "; ".join(relevant_warnings),
                        "validation_status": "invalid",
                        "review_required": bool(tax_val.get("review_required", False))
                    }

        if erp_intel_trace:
            source_type = erp_intel_trace["source_type"]
            source_engine = erp_intel_trace["source_engine"]
            confidence = erp_intel_trace["confidence"]
            validation_status = erp_intel_trace["validation_status"]
            if erp_intel_trace["reason"]:
                validation_reason = erp_intel_trace["reason"]
            if erp_intel_trace["review_required"]:
                review_status = "needs_review"
                review_reason = erp_intel_trace["reason"] or "ERP intelligence validation failed"

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
            "current_value": current_val,
            "proposed_value": proposed_val,
            "final_value": final_val,
            "review_status": review_status,
            "review_reason": review_reason,
            "user_action": user_action,
            "reviewer_note": reviewer_note,
            "is_required": is_required,
            "is_placeholder": is_placeholder,
            "is_missing": is_missing,
            "erp_intelligence": erp_intel_trace,
        }

    return traces


def calculate_review_summary(
    source_traces: dict[str, dict[str, Any]],
    duplicate_detection: Optional[dict[str, Any]] = None,
    erp_intelligence: Optional[dict[str, Any]] = None
) -> dict[str, Any]:
    total_fields = len(source_traces)
    auto_approved_count = 0
    needs_review_count = 0
    validation_failed_count = 0
    missing_count = 0
    conflict_count = 0
    training_supplied_count = 0
    dynamic_training_blocked_count = 0

    export_blockers = []
    review_reasons = []

    for field, trace in source_traces.items():
        status = trace.get("review_status", "needs_review")
        if status == "auto_approved":
            auto_approved_count += 1
        elif status == "needs_review":
            needs_review_count += 1
        elif status == "validation_failed":
            validation_failed_count += 1
        elif status == "missing":
            missing_count += 1
        elif status == "conflict":
            conflict_count += 1
        elif status == "training_supplied":
            training_supplied_count += 1

        is_dynamic = trace.get("is_dynamic_field", False)
        is_training = trace.get("is_training_supplied", False)
        if is_dynamic and is_training:
            dynamic_training_blocked_count += 1

        is_req = trace.get("is_required", False)
        if is_req and status in {"needs_review", "validation_failed", "missing", "conflict", "rejected"}:
            export_blockers.append(field)
        
        reason = trace.get("review_reason")
        if reason and status != "auto_approved":
            review_reasons.append(f"{field}: {reason}")

    export_ready = len(export_blockers) == 0

    summary = {
        "total_fields": total_fields,
        "auto_approved_count": auto_approved_count,
        "needs_review_count": needs_review_count,
        "validation_failed_count": validation_failed_count,
        "missing_count": missing_count,
        "conflict_count": conflict_count,
        "training_supplied_count": training_supplied_count,
        "dynamic_training_blocked_count": dynamic_training_blocked_count,
        "export_ready": export_ready,
        "export_blockers": export_blockers,
        "required_review_reasons": list(dict.fromkeys(review_reasons)),
    }

    if erp_intelligence:
        summary["master_match_status"] = erp_intelligence.get("master_match", {}).get("status", "no_master_match")
        summary["po_grn_invoice_match_status"] = erp_intelligence.get("po_grn_invoice_match", {}).get("status", "not_applicable")
        summary["tax_validation_status"] = erp_intelligence.get("tax_validation", {}).get("status", "pass")
        for eb in erp_intelligence.get("export_blockers", []):
            if eb not in summary["export_blockers"]:
                summary["export_blockers"].append(eb)
        for w in erp_intelligence.get("warnings", []):
            if w not in summary["required_review_reasons"]:
                summary["required_review_reasons"].append(w)
        if erp_intelligence.get("blocking"):
            summary["export_ready"] = False

    if duplicate_detection and duplicate_detection.get("status") != "not_duplicate":
        reasons_str = ", ".join(duplicate_detection.get("reasons", []))
        ref_name = duplicate_detection.get("matched_document_name") or "Unknown"
        ref_id = duplicate_detection.get("matched_job_id") or "Unknown"
        ref = f"{ref_name} ({ref_id})"
        
        summary["duplicate_warning"] = "Possible duplicate document detected"
        summary["duplicate_reason"] = f"Matched based on: {reasons_str}"
        summary["duplicate_matched_reference"] = ref
        summary["duplicate_warning_status"] = "warning"
        
        if duplicate_detection.get("blocking"):
            summary["export_ready"] = False
            if "duplicate_document" not in summary["export_blockers"]:
                summary["export_blockers"].append("duplicate_document")
    else:
        summary["duplicate_warning_status"] = "pass"

    return summary

