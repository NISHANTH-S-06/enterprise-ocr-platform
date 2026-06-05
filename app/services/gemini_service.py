import asyncio
import json
from typing import Any, Dict, List, Optional
from app.core.config import get_settings
from app.services.company_form_mapper import MASTER_ITEM_FIELDS, MASTER_SCHEMA_FIELDS, missing_fields_for_doc, normalize_mapped_fields

settings = get_settings()

def build_gemini_field_prompt(raw_text: str, document_type: str, target_fields: List[str], existing_fields: Dict[str, Any]) -> str:
    """Builds a strict JSON-only prompt for field extraction."""
    return f"""You are a strict data extraction AI. Extract the requested fields from the OCR text below.

RULES:
1. Return ONLY valid JSON. No markdown formatting, no code blocks, no conversational text.
2. Extract only from the provided OCR text. NEVER guess or hallucinate.
3. If a field is not found, return null for its value.
4. For each field, provide a confidence score between 0.0 and 1.0.
5. For each field, provide the exact source_evidence substring from the text that justifies the value.
6. Map synonyms to the requested canonical fields using the synonym dictionary below.
7. Reject values if source evidence is not found verbatim in OCR text.
8. Return confidence < 0.7 for any weak or ambiguous match.

SYNONYM DICTIONARY (map these labels to canonical field names):
- invoice_number: "Invoice No", "Bill No", "Tax Invoice No", "Document No", "Doc No", "Voucher No", "Reference No", "Challan No"
- invoice_date: "Invoice Date", "Bill Date", "Document Date", "Doc Date", "Date", "Dated"
- buyer_name: "Buyer", "Bill To", "Billed To", "Sold To", "Customer", "Customer Name", "Party Name", "Purchaser"
- consignee_name: "Consignee", "Ship To", "Delivery To", "Deliver To", "Receiver", "Recipient"
- grand_total: "Grand Total", "Total Amount", "Net Amount", "Amount Payable", "Final Amount", "Invoice Value", "Net Payable", "Total Payable"
- supplier_name: "Supplier", "Vendor", "Seller", "From", "Billed By"
- gst_number: "GSTIN", "GST No", "GST Number", "GSTIN/UIN"
- po_number: "PO No", "Purchase Order No", "Order No"
- grn_number: "GRN No", "Goods Receipt No"
- transport_name: "Transporter", "Transport Name", "Carrier", "Logistics"
- vehicle_number: "Vehicle No", "Truck No"
- lr_number: "LR No", "Lorry Receipt No", "Consignment No"
- eway_bill_number: "E-Way Bill No", "EWB No"
- items: table rows with columns {', '.join(MASTER_ITEM_FIELDS)}

DOCUMENT TYPE: {document_type}
TARGET FIELDS TO EXTRACT: {', '.join(target_fields)}
MASTER SCHEMA FIELDS: {', '.join(MASTER_SCHEMA_FIELDS)}
ALREADY EXTRACTED FIELDS (Context only, do not return these unless asked): {json.dumps(existing_fields)}

OCR TEXT:
---
{raw_text[:8000]}
---

EXPECTED JSON SCHEMA:
{{
  "field_name": {{
    "value": "extracted value or null",
    "confidence": 0.95,
    "source": "gemini",
    "source_evidence": "exact text from OCR"
  }}
}}
For items, return:
{{
  "items": {{
    "value": [
      {{"sno": "", "order_no": "", "group": "", "article": "", "colour": "", "hsn_sac": "", "type": "", "size": "", "uom": "", "qty": "", "price": "", "amount": "", "total": "", "act": ""}}
    ],
    "confidence": 0.80,
    "source": "gemini",
    "source_evidence": "table text substring"
  }}
}}
"""

def parse_gemini_json_response(response_text: str) -> Dict[str, Any]:
    """Parses and validates the Gemini JSON response safely."""
    try:
        # Strip markdown code blocks if AI ignored rules
        clean_text = response_text.strip()
        if clean_text.startswith("```json"):
            clean_text = clean_text[7:]
        if clean_text.startswith("```"):
            clean_text = clean_text[3:]
        if clean_text.endswith("```"):
            clean_text = clean_text[:-3]
            
        data = json.loads(clean_text.strip())
        
        valid_data = {}
        for k, v in data.items():
            if not isinstance(v, dict):
                continue
            value = v.get("value")
            if value is None:
                continue
            
            # Normalize confidence
            try:
                conf = float(v.get("confidence", 0.0))
            except (ValueError, TypeError):
                conf = 0.0
                
            evidence = str(v.get("source_evidence", "")).strip()
            if not evidence:
                continue
                
            valid_value = value
            if isinstance(value, list):
                valid_value = value
            elif isinstance(value, dict):
                valid_value = value
            else:
                valid_value = str(value)
            valid_data[k] = {
                "value": valid_value,
                "confidence": conf,
                "source": "gemini",
                "method": "gemini",
                "source_evidence": evidence
            }
        return valid_data
    except Exception as e:
        # Never raise, just return empty dict safely
        return {}

async def call_gemini_api(prompt: str) -> Dict[str, Any]:
    """Live call implementation using google-genai SDK."""
    if not settings.AI_FALLBACK_ENABLED:
        return {"used": False, "reason": "disabled"}
    
    if not settings.GEMINI_API_KEY:
        return {"used": False, "reason": "missing_api_key"}
        
    try:
        import google.genai as genai # type: ignore
    except ImportError:
        return {"used": False, "reason": "client_unavailable"}
        
    try:
        def _generate() -> Any:
            client = genai.Client(api_key=settings.GEMINI_API_KEY)
            return client.models.generate_content(
                model=settings.AI_FALLBACK_MODEL,
                contents=prompt,
            )

        response = await asyncio.wait_for(
            asyncio.to_thread(_generate),
            timeout=settings.AI_FALLBACK_TIMEOUT_SECONDS,
        )
        # Parse the JSON response
        parsed = parse_gemini_json_response(response.text)
        if not parsed:
            return {"used": False, "reason": "invalid_json_response", "raw_response": response.text}
            
        return {"used": True, "results": parsed}
    except asyncio.TimeoutError:
        return {"used": False, "reason": "timeout"}
    except Exception as e:
        return {"used": False, "reason": "api_exception", "error": str(e)}

async def test_gemini_connection(prompt: str) -> Dict[str, Any]:
    """Optional isolated helper to test the Gemini connection manually."""
    return await call_gemini_api(prompt)

async def gemini_enrich_missing_fields(
    mapped_result: Dict[str, Any],
    raw_text: str,
    document_type: str,
    missing_fields: Optional[List[str]] = None,
    *,
    mock_response: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Enriches missing or low-confidence fields using Gemini AI.
    
    Safe insertion point: Called after regex mapping, before validation.
    Will NOT overwrite fields with regex confidence >= AI_FALLBACK_REGEX_PROTECT_CONFIDENCE.
    """
    if not settings.AI_FALLBACK_ENABLED and not mock_response:
        return mapped_result
        
    mapped_fields = normalize_mapped_fields(mapped_result.get("mapped_fields", {}))
    if not missing_fields:
        missing_fields = mapped_result.get("missing_fields", [])
        
    # Determine which fields need enrichment
    fields_to_enrich = []
    
    # 1. Add genuinely missing fields
    for field in missing_fields:
        fields_to_enrich.append(field)
        
    # 2. Add weak fields (confidence < protect threshold)
    for field, data in mapped_fields.items():
        if isinstance(data, dict):
            conf = data.get("confidence", 0.0)
            if conf < settings.AI_FALLBACK_REGEX_PROTECT_CONFIDENCE:
                fields_to_enrich.append(field)
    if document_type in {"unknown", "UNKNOWN", ""}:
        fields_to_enrich.extend(["document_type", *MASTER_SCHEMA_FIELDS])
    item_rows = mapped_fields.get("item_rows") or mapped_fields.get("items")
    rows = item_rows.get("value") if isinstance(item_rows, dict) else item_rows
    if not isinstance(rows, list) or not rows:
        fields_to_enrich.append("items")
                
    # Unique list
    fields_to_enrich = list(set(fields_to_enrich))
    
    if not fields_to_enrich:
        return mapped_result

    prompt = build_gemini_field_prompt(raw_text, document_type, fields_to_enrich, mapped_fields)
    api_response = {"used": True, "results": mock_response} if mock_response is not None else await call_gemini_api(prompt)
    fallback_meta = {
        "enabled": True,
        "provider": "gemini",
        "model": settings.AI_FALLBACK_MODEL,
        "fields_requested": fields_to_enrich,
        "fields_recovered": [],
        "fields_rejected": [],
        "needs_review": False,
        "reason": api_response.get("reason", "used"),
    }
    if not api_response.get("used"):
        fallback_meta["needs_review"] = api_response.get("reason") not in {"disabled", "missing_api_key", "client_unavailable"}
        mapped_result["ai_fallback"] = fallback_meta
        return mapped_result

    ai_result = api_response.get("results") or {}
    if not isinstance(ai_result, dict):
        fallback_meta["reason"] = "invalid_response"
        fallback_meta["needs_review"] = True
        mapped_result["ai_fallback"] = fallback_meta
        return mapped_result
    
    # Process AI results
    for field, ai_data in ai_result.items():
        if field not in fields_to_enrich:
            continue
        if not isinstance(ai_data, dict):
            fallback_meta["fields_rejected"].append(field)
            fallback_meta["needs_review"] = True
            continue
            
        value = ai_data.get("value")
        try:
            confidence = float(ai_data.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        source_evidence = ai_data.get("source_evidence")
        
        # Guard: Reject if no source evidence, unverifiable evidence, or below threshold.
        evidence_text = str(source_evidence or "").strip()
        if value in (None, "") or not evidence_text or confidence < settings.AI_FALLBACK_MIN_CONFIDENCE:
            fallback_meta["fields_rejected"].append(field)
            fallback_meta["needs_review"] = True
            continue
        if evidence_text.lower() not in (raw_text or "").lower():
            fallback_meta["fields_rejected"].append(field)
            fallback_meta["needs_review"] = True
            continue
            
        # Protect high-confidence regex fields; Gemini is recovery, not primary.
        existing = mapped_fields.get(field, {})
        existing_conf = float(existing.get("confidence", 0.0) or 0.0) if isinstance(existing, dict) else 0.0
        existing_value = existing.get("value") if isinstance(existing, dict) else existing
        if existing_value not in (None, "") and existing_conf >= settings.AI_FALLBACK_REGEX_PROTECT_CONFIDENCE:
            fallback_meta["fields_rejected"].append(field)
            continue
        if existing_value not in (None, "") and confidence <= existing_conf:
            fallback_meta["fields_rejected"].append(field)
            continue
            
        mapped_fields[field] = {
            "value": value,
            "confidence": confidence,
            "method": "gemini",
            "source": "gemini",
            "source_evidence": source_evidence,
            "reason": "AI enriched"
        }
        fallback_meta["fields_recovered"].append(field)
        
    mapped_result["mapped_fields"] = normalize_mapped_fields(mapped_fields)
    mapped_result["gemini_fields"] = {
        field: mapped_result["mapped_fields"][field]
        for field in fallback_meta["fields_recovered"]
        if field in mapped_result["mapped_fields"]
    }
    mapped_result["missing_fields"] = missing_fields_for_doc(mapped_result["mapped_fields"], document_type)
    mapped_result["ai_fallback"] = fallback_meta
    
    return mapped_result
