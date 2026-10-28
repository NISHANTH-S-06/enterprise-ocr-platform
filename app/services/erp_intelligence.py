from __future__ import annotations
import re
from typing import Any, Dict, List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.extracted_data import ExtractedData
from app.models.job import OCRJob
from loguru import logger

def _val(mapped: dict[str, Any], key: str) -> str:
    if not mapped:
        return ""
    v = mapped.get(key, "")
    if isinstance(v, dict):
        v = v.get("value", "")
    return str(v or "").strip()

def _to_float(s: str) -> float | None:
    try:
        return float(str(s).replace(",", ""))
    except (ValueError, AttributeError):
        return None

def normalize_ref(ref: str) -> str:
    if not ref:
        return ""
    return re.sub(r"[^a-zA-Z0-9]", "", str(ref)).lower()

def clean_name(name: str) -> str:
    if not name:
        return ""
    n = name.lower()
    suffixes = [
        r"\bpvt\b", r"\bltd\b", r"\bllc\b", r"\binc\b", r"\bco\b",
        r"\bprivate\b", r"\blimited\b", r"\bcompany\b", r"\bcorporation\b", r"\bcorp\b",
        r"\btrading\b", r"\bindustries\b"
    ]
    for s in suffixes:
        n = re.sub(s, "", n)
    n = re.sub(r"[^a-z0-9\s]", "", n)
    return "".join(n.split())

async def check_po_grn_invoice_match(
    db: AsyncSession,
    org_id: Optional[Any],
    doc_type: str,
    validated_fields: dict[str, Any],
    line_items: list[dict[str, Any]]
) -> dict[str, Any]:
    doc_type_lower = str(doc_type or "").lower()
    
    # 1. Extract values from current document
    po_num = _val(validated_fields, "po_number") or _val(validated_fields, "purchase_order_number") or _val(validated_fields, "po_no") or _val(validated_fields, "order_no") or _val(validated_fields, "order_number")
    grn_num = _val(validated_fields, "grn_number")
    inv_num = _val(validated_fields, "invoice_no") or _val(validated_fields, "invoice_number") or _val(validated_fields, "invoice_num")
    
    vendor_name = _val(validated_fields, "vendor_name") or _val(validated_fields, "supplier_name") or _val(validated_fields, "supplier")
    customer_name = _val(validated_fields, "customer_name") or _val(validated_fields, "buyer_name") or _val(validated_fields, "customer")
    
    grand_total = _to_float(_val(validated_fields, "grand_total") or _val(validated_fields, "total_amount") or _val(validated_fields, "po_grand_total") or _val(validated_fields, "order_total"))
    total_tax = _to_float(_val(validated_fields, "total_tax") or _val(validated_fields, "tax_amount") or _val(validated_fields, "po_tax_total") or _val(validated_fields, "order_tax"))

    # Determine status
    status = "not_applicable"
    confidence = 0.0
    matched_job_id = None
    matched_document_name = None
    reasons = []
    review_required = False
    blocking = False

    # Fetch candidate matches from DB
    if not po_num and not grn_num and not inv_num:
        return {
            "status": status,
            "confidence": confidence,
            "matched_job_id": matched_job_id,
            "matched_document_name": matched_document_name,
            "reasons": reasons,
            "review_required": review_required,
            "blocking": blocking
        }

    stmt = select(ExtractedData).join(OCRJob, ExtractedData.job_id == OCRJob.id)
    if org_id:
        stmt = stmt.where(OCRJob.org_id == org_id)
    
    res = await db.execute(stmt)
    all_extracted = res.scalars().all()

    # Normalised values for matching
    norm_po = normalize_ref(po_num)
    norm_grn = normalize_ref(grn_num)
    norm_inv = normalize_ref(inv_num)

    target_extracted: ExtractedData | None = None
    target_type = "unknown"

    # Match rules based on current doc type
    if doc_type_lower in ("purchase_order", "purchase"):
        # Current document is a PO. Try to find a matching Invoice or GRN.
        for e in all_extracted:
            e_doc_type = str(e.raw_ocr_json.get("document_type") or e.job.ocr_config.get("document_type") or "").lower()
            e_po = normalize_ref(e.mapped_output.get("po_number") or e.mapped_output.get("purchase_order_number") or e.mapped_output.get("po_no") or e.mapped_output.get("order_no"))
            if e_po and norm_po and e_po == norm_po:
                target_extracted = e
                target_type = e_doc_type
                break
    elif doc_type_lower == "grn":
        # Current document is a GRN. Try to find matching PO or Invoice.
        for e in all_extracted:
            e_doc_type = str(e.raw_ocr_json.get("document_type") or e.job.ocr_config.get("document_type") or "").lower()
            # Match by GRN number if available
            e_grn = normalize_ref(e.mapped_output.get("grn_number"))
            if e_grn and norm_grn and e_grn == norm_grn:
                target_extracted = e
                target_type = e_doc_type
                break
            # Match by PO number if GRN references a PO
            e_po = normalize_ref(e.mapped_output.get("po_number") or e.mapped_output.get("purchase_order_number") or e.mapped_output.get("po_no") or e.mapped_output.get("order_no"))
            if e_po and norm_po and e_po == norm_po and e_doc_type in ("purchase_order", "purchase"):
                target_extracted = e
                target_type = "purchase_order"
                break
    else:
        # Current document is Invoice (Sales/Purchase). Try to find matching PO or GRN.
        # Check PO first
        if norm_po:
            for e in all_extracted:
                e_doc_type = str(e.raw_ocr_json.get("document_type") or e.job.ocr_config.get("document_type") or "").lower()
                if e_doc_type in ("purchase_order", "purchase"):
                    e_po = normalize_ref(e.mapped_output.get("po_number") or e.mapped_output.get("purchase_order_number") or e.mapped_output.get("po_no") or e.mapped_output.get("order_no") or e.mapped_output.get("order_number"))
                    if e_po and e_po == norm_po:
                        target_extracted = e
                        target_type = "purchase_order"
                        break
        # Then check GRN
        if not target_extracted and norm_grn:
            for e in all_extracted:
                e_doc_type = str(e.raw_ocr_json.get("document_type") or e.job.ocr_config.get("document_type") or "").lower()
                if e_doc_type == "grn":
                    e_grn = normalize_ref(e.mapped_output.get("grn_number"))
                    if e_grn and e_grn == norm_grn:
                        target_extracted = e
                        target_type = "grn"
                        break

    if not target_extracted:
        # We specified a PO or GRN but it wasn't found
        if po_num or grn_num:
            status = "missing_po_grn"
            review_required = True
            if po_num:
                reasons.append(f"po_number: Referenced PO '{po_num}' was not found in the database.")
            if grn_num:
                reasons.append(f"grn_number: Referenced GRN '{grn_num}' was not found in the database.")
        return {
            "status": status,
            "confidence": 0.0,
            "matched_job_id": None,
            "matched_document_name": None,
            "reasons": reasons,
            "review_required": review_required,
            "blocking": blocking
        }

    # We found a matching document!
    matched_job_id = str(target_extracted.job_id)
    matched_document_name = target_extracted.job.filename if target_extracted.job else "Unknown"
    confidence = 1.0
    status = "matched"

    # Compare values between current and matched document
    t_fields = target_extracted.mapped_output
    t_line_items = target_extracted.line_items

    # 1. Compare Totals
    t_grand_total = _to_float(t_fields.get("grand_total") or t_fields.get("total_amount") or t_fields.get("po_grand_total") or t_fields.get("order_total"))
    if grand_total is not None and t_grand_total is not None:
        if abs(grand_total - t_grand_total) > 5.0:
            status = "mismatched"
            review_required = True
            reasons.append(f"grand_total: Grand total mismatch: current is {grand_total}, but matched {target_type} has {t_grand_total}")
            
    t_total_tax = _to_float(t_fields.get("total_tax") or t_fields.get("tax_amount") or t_fields.get("po_tax_total") or t_fields.get("order_tax"))
    if total_tax is not None and t_total_tax is not None:
        if abs(total_tax - t_total_tax) > 5.0:
            status = "mismatched"
            review_required = True
            reasons.append(f"total_tax: Tax amount mismatch: current is {total_tax}, but matched {target_type} has {t_total_tax}")

    # 2. Compare Vendor/Customer Names
    t_vendor = t_fields.get("vendor_name") or t_fields.get("supplier_name") or t_fields.get("supplier")
    if vendor_name and t_vendor:
        if clean_name(vendor_name) != clean_name(t_vendor):
            status = "mismatched"
            review_required = True
            reasons.append(f"supplier_id: Vendor mismatch: current is '{vendor_name}', but matched {target_type} has '{t_vendor}'")

    t_customer = t_fields.get("customer_name") or t_fields.get("buyer_name") or t_fields.get("customer")
    if customer_name and t_customer:
        if clean_name(customer_name) != clean_name(t_customer):
            status = "mismatched"
            review_required = True
            reasons.append(f"customer_id: Customer mismatch: current is '{customer_name}', but matched {target_type} has '{t_customer}'")

    # 3. Compare Line Items
    if line_items and t_line_items:
        for idx, row in enumerate(line_items):
            desc = _val(row, "product") or _val(row, "description") or _val(row, "product_description")
            qty = _to_float(_val(row, "quantity") or _val(row, "qty") or _val(row, "bulk_qty") or _val(row, "accepted_qty"))
            price = _to_float(_val(row, "unit_price") or _val(row, "rate") or _val(row, "bulk_unit_price") or _val(row, "price"))
            
            if qty is None and price is None:
                continue

            # Try to match the row in target's line items
            t_row_match = None
            norm_desc = clean_name(desc)
            for t_row in t_line_items:
                t_desc = _val(t_row, "product") or _val(t_row, "description") or _val(t_row, "product_description")
                if norm_desc and clean_name(t_desc) == norm_desc:
                    t_row_match = t_row
                    break

            if not t_row_match:
                # If exact name match fails, try fallback or partial overlap
                for t_row in t_line_items:
                    t_desc = _val(t_row, "product") or _val(t_row, "description") or _val(t_row, "product_description")
                    if norm_desc and norm_desc in clean_name(t_desc):
                        t_row_match = t_row
                        break

            if t_row_match:
                t_qty = _to_float(_val(t_row_match, "quantity") or _val(t_row_match, "qty") or _val(t_row_match, "bulk_qty") or _val(t_row_match, "accepted_qty"))
                t_price = _to_float(_val(t_row_match, "unit_price") or _val(t_row_match, "rate") or _val(t_row_match, "bulk_unit_price") or _val(t_row_match, "price"))
                
                if qty is not None and t_qty is not None and qty > t_qty:
                    status = "mismatched"
                    review_required = True
                    reasons.append(f"item_rows: Row {idx+1} ({desc or 'item'}): Quantity {qty} exceeds matched {target_type} quantity {t_qty}")
                    
                if price is not None and t_price is not None and abs(price - t_price) > 0.05:
                    status = "mismatched"
                    review_required = True
                    reasons.append(f"item_rows: Row {idx+1} ({desc or 'item'}): Unit price {price} differs from matched {target_type} unit price {t_price}")
            else:
                status = "mismatched"
                review_required = True
                reasons.append(f"item_rows: Row {idx+1} ({desc[:20] if desc else 'item'}): Item not found in matched {target_type}")

    if not reasons:
        reasons.append(f"Matched successfully with {target_type} '{matched_document_name}'.")

    return {
        "status": status,
        "confidence": confidence,
        "matched_job_id": matched_job_id,
        "matched_document_name": matched_document_name,
        "reasons": reasons,
        "review_required": review_required,
        "blocking": blocking
    }

def check_tax_gst_validation(
    validated_fields: dict[str, Any],
    line_items: list[dict[str, Any]]
) -> dict[str, Any]:
    warnings = []
    export_blockers = []
    review_required = False

    gst = _val(validated_fields, "gst_number") or _val(validated_fields, "gstin") or _val(validated_fields, "buyer_gstin") or _val(validated_fields, "vendor_gstin")
    gst_clean = gst.upper().replace(" ", "")
    
    # 1. GST Format Check
    if gst_clean:
        GST_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
        if not GST_RE.match(gst_clean):
            warnings.append(f"gst_number: GST format is invalid: '{gst}'")
            review_required = True

    # 2. IGST/CGST/SGST consistency
    igst = _to_float(_val(validated_fields, "igst") or _val(validated_fields, "igst_amount"))
    cgst = _to_float(_val(validated_fields, "cgst") or _val(validated_fields, "cgst_amount"))
    sgst = _to_float(_val(validated_fields, "sgst") or _val(validated_fields, "sgst_amount"))

    igst_ = igst or 0.0
    cgst_ = cgst or 0.0
    sgst_ = sgst or 0.0

    if igst_ > 0 and (cgst_ > 0 or sgst_ > 0):
        msg = f"igst: Tax consistency failure: Both IGST ({igst_}) and CGST/SGST ({cgst_}/{sgst_}) are non-zero."
        export_blockers.append(msg)
        review_required = True
    
    if cgst_ > 0 or sgst_ > 0:
        if abs(cgst_ - sgst_) > 1.5:  # Allow 1.5 INR/USD tolerance
            msg = f"cgst: Tax consistency warning: CGST ({cgst_}) and SGST ({sgst_}) are not equal."
            warnings.append(msg)
            review_required = True

    # 3. subtotal + tax + freight/charges = grand total
    subtotal = _to_float(_val(validated_fields, "subtotal") or _val(validated_fields, "taxable_value") or _val(validated_fields, "taxable_amount"))
    tax = _to_float(_val(validated_fields, "total_tax") or _val(validated_fields, "tax_amount") or _val(validated_fields, "po_tax_total") or _val(validated_fields, "order_tax"))
    freight = _to_float(_val(validated_fields, "freight") or _val(validated_fields, "freight_charges") or _val(validated_fields, "packaging_charges") or _val(validated_fields, "other_charges") or _val(validated_fields, "freight_amount"))
    grand_total = _to_float(_val(validated_fields, "grand_total") or _val(validated_fields, "total_amount") or _val(validated_fields, "po_grand_total") or _val(validated_fields, "order_total"))

    subtotal_ = subtotal or 0.0
    tax_ = tax or (igst_ + cgst_ + sgst_) or 0.0
    freight_ = freight or 0.0

    if grand_total is not None:
        expected_total = round(subtotal_ + tax_ + freight_, 2)
        diff = abs(expected_total - grand_total)
        if diff > 5.0:  # Serious mismatch
            msg = f"grand_total: Grand total validation failure: subtotal({subtotal_}) + tax({tax_}) + freight({freight_}) = {expected_total}, but grand_total = {grand_total}"
            export_blockers.append(msg)
            review_required = True
        elif diff > 0.05:  # Rounding difference
            warnings.append(f"grand_total: Grand total has minor rounding discrepancy: expected {expected_total}, got {grand_total}")

    # 4. line totals add up to subtotal / header total
    if line_items:
        line_total_sum = 0.0
        has_lines = False
        for row in line_items:
            line_total = _to_float(_val(row, "line_total") or _val(row, "amount") or _val(row, "total") or _val(row, "bulk_line_total"))
            if line_total is not None:
                line_total_sum += line_total
                has_lines = True
        
        if has_lines:
            expected_sub = subtotal or grand_total or 0.0
            if expected_sub > 0:
                diff = abs(line_total_sum - expected_sub)
                if diff > 5.0:
                    msg = f"subtotal: Line totals sum mismatch: sum of line items is {round(line_total_sum, 2)}, but header subtotal is {expected_sub}"
                    export_blockers.append(msg)
                    review_required = True
                elif diff > 0.05:
                    warnings.append(f"subtotal: Line totals sum has minor discrepancy: line items sum {round(line_total_sum, 2)}, header subtotal {expected_sub}")

    status = "pass"
    if export_blockers:
        status = "fail"
    elif warnings:
        status = "warning"

    return {
        "status": status,
        "warnings": warnings,
        "export_blockers": export_blockers,
        "review_required": review_required
    }

async def build_erp_intelligence_payload(
    db: AsyncSession,
    org_id: Optional[Any],
    doc_type: str,
    validated_fields: dict[str, Any],
    line_items: list[dict[str, Any]],
    master_match: dict[str, Any]
) -> dict[str, Any]:
    try:
        po_grn_match = await check_po_grn_invoice_match(db, org_id, doc_type, validated_fields, line_items)
    except Exception as e:
        logger.exception("Error during PO/GRN matching: {}", e)
        po_grn_match = {
            "status": "not_applicable",
            "confidence": 0.0,
            "matched_job_id": None,
            "matched_document_name": None,
            "reasons": [f"Error during PO/GRN match check: {e}"],
            "review_required": False,
            "blocking": False
        }

    try:
        tax_val = check_tax_gst_validation(validated_fields, line_items)
    except Exception as e:
        logger.exception("Error during tax validation: {}", e)
        tax_val = {
            "status": "pass",
            "warnings": [f"Error during tax validation: {e}"],
            "export_blockers": [],
            "review_required": False
        }

    review_required = master_match.get("review_required", False) or po_grn_match.get("review_required", False) or tax_val.get("review_required", False)
    blocking = master_match.get("blocking", False) or po_grn_match.get("blocking", False) or bool(tax_val.get("export_blockers"))

    warnings_list = []
    # Collect warnings
    for r in master_match.get("reasons", []):
        if master_match.get("review_required") and r not in warnings_list:
            warnings_list.append(r)
    for r in po_grn_match.get("reasons", []):
        if po_grn_match.get("review_required") and r not in warnings_list:
            warnings_list.append(r)
    for w in tax_val.get("warnings", []):
        if w not in warnings_list:
            warnings_list.append(w)

    export_blockers = list(tax_val.get("export_blockers", []))
    if master_match.get("status") == "ambiguous_master_match":
        msg = f"Ambiguous master match for {master_match.get('party_type', 'party')}"
        if msg not in export_blockers:
            export_blockers.append(msg)

    return {
        "master_match": master_match,
        "po_grn_invoice_match": po_grn_match,
        "tax_validation": tax_val,
        "review_required": review_required,
        "blocking": blocking,
        "warnings": warnings_list,
        "export_blockers": export_blockers
    }
