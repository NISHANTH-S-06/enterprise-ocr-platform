from __future__ import annotations
import re
from typing import Any, Dict, List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.company import ERPMasterMapping
from loguru import logger

def normalize_name(name: str) -> str:
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
    return " ".join(n.split())

def normalize_gst(gst: str) -> str:
    if not gst:
        return ""
    return "".join(c for c in gst.upper() if c.isalnum())

def normalize_phone(phone: str) -> str:
    if not phone:
        return ""
    digits = "".join(c for c in phone if c.isdigit())
    return digits[-10:] if len(digits) >= 10 else digits

def normalize_email(email: str) -> str:
    if not email:
        return ""
    return email.strip().lower()

def get_address_tokens(address: str) -> set[str]:
    if not address:
        return set()
    clean = re.sub(r"[^a-z0-9\s]", " ", address.lower())
    return {w for w in clean.split() if len(w) >= 3}

def extract_field_value(fields: dict[str, Any], keys: list[str]) -> str:
    for k in keys:
        if k in fields:
            val = fields[k]
            if isinstance(val, dict):
                val = val.get("value", "")
            if val:
                return str(val).strip()
    return ""

def score_candidate(mapping: ERPMasterMapping, criteria: dict[str, str]) -> dict[str, Any]:
    matched_signals = []
    conflict_signals = []
    score = 0
    reasons = []

    # 1. ERP ID match
    mapped_erp_id = (mapping.erp_id or "").strip()
    if mapped_erp_id and criteria["erp_id"] == mapped_erp_id:
        matched_signals.append("erp_id")
        score = 100
        reasons.append("ERP ID matched exactly")
        return {
            "score": score,
            "matched_signals": matched_signals,
            "conflict_signals": conflict_signals,
            "reasons": reasons
        }

    # 2. GST Match
    mapped_gst = normalize_gst(mapping.gst_number or mapping.metadata_json.get("gst_number") or mapping.metadata_json.get("gst"))
    if criteria["gst"] and mapped_gst:
        if criteria["gst"] == mapped_gst:
            matched_signals.append("gst")
            score = max(score, 98)
            reasons.append("GST number matched exactly")
        else:
            conflict_signals.append("gst")
            reasons.append("GST number conflict detected")

    # 3. Email Match
    mapped_email = normalize_email(mapping.metadata_json.get("email") or mapping.metadata_json.get("email_address"))
    if criteria["email"] and mapped_email:
        if criteria["email"] == mapped_email:
            matched_signals.append("email")
            score = max(score, 95)
            reasons.append("Email matched exactly")
        else:
            conflict_signals.append("email")

    # 4. Phone Match
    mapped_phone = normalize_phone(mapping.metadata_json.get("phone") or mapping.metadata_json.get("phone_number") or mapping.metadata_json.get("contact_number"))
    if criteria["phone"] and mapped_phone:
        if criteria["phone"] == mapped_phone:
            matched_signals.append("phone")
            score = max(score, 90)
            reasons.append("Phone number matched exactly")
        else:
            conflict_signals.append("phone")

    # 5. Name Match
    normalized_criteria_name = normalize_name(criteria["name"])
    mapping_names = {
        normalize_name(mapping.detected_text),
        normalize_name(mapping.normalized_text),
        normalize_name(mapping.erp_name),
        normalize_name(mapping.display_name),
        normalize_name(mapping.legal_name)
    }
    if mapping.alias_names:
        for alias in str(mapping.alias_names).split("|"):
            mapping_names.add(normalize_name(alias))
    mapping_names = {name for name in mapping_names if name}

    name_matched = False
    if normalized_criteria_name:
        if normalized_criteria_name in mapping_names:
            matched_signals.append("name")
            score = max(score, 90)
            reasons.append("Name matched exactly after normalization")
            name_matched = True
        else:
            tokens_criteria = set(normalized_criteria_name.split())
            best_overlap = 0.0
            for m_name in mapping_names:
                tokens_map = set(m_name.split())
                if tokens_criteria and tokens_map:
                    overlap = len(tokens_criteria & tokens_map) / max(len(tokens_criteria | tokens_map), 1)
                    best_overlap = max(best_overlap, overlap)
            
            if best_overlap >= 0.8:
                matched_signals.append("name")
                score = max(score, int(70 + best_overlap * 20))
                reasons.append(f"Name matched fuzzy with high similarity ({int(best_overlap*100)}%)")
                name_matched = True
            elif best_overlap >= 0.5:
                matched_signals.append("name")
                score = max(score, int(60 + best_overlap * 20))
                reasons.append(f"Name matched fuzzy with moderate similarity ({int(best_overlap*100)}%)")
                name_matched = True
            elif best_overlap < 0.2 and mapping_names:
                conflict_signals.append("name")

    # 6. Address Match
    if criteria["address"]:
        criteria_address_tokens = get_address_tokens(criteria["address"])
        mapped_address = mapping.metadata_json.get("address") or mapping.metadata_json.get("billing_address") or mapping.metadata_json.get("shipping_address")
        if mapped_address:
            mapped_address_tokens = get_address_tokens(mapped_address)
            if criteria_address_tokens and mapped_address_tokens:
                overlap = len(criteria_address_tokens & mapped_address_tokens) / max(len(criteria_address_tokens | mapped_address_tokens), 1)
                if overlap >= 0.6:
                    matched_signals.append("address")
                    score = max(score, int(50 + overlap * 30))
                    reasons.append(f"Address matched with token similarity ({int(overlap*100)}%)")

    # Deduct for conflicts
    if "gst" in conflict_signals:
        score = max(0, score - 60)
    if "name" in conflict_signals:
        score = max(0, score - 40)
    if "email" in conflict_signals:
        score = max(0, score - 30)
    if "phone" in conflict_signals:
        score = max(0, score - 20)

    return {
        "score": score,
        "matched_signals": sorted(list(set(matched_signals))),
        "conflict_signals": sorted(list(set(conflict_signals))),
        "reasons": reasons
    }

async def match_master_data(
    db: AsyncSession,
    org_id: Optional[Any],
    doc_type: str,
    validated_fields: dict[str, Any],
    mapped_result: dict[str, Any]
) -> dict[str, Any]:
    try:
        doc_type_lower = str(doc_type or "").lower()
        if doc_type_lower in ("purchase_order", "purchase", "grn"):
            party_type = "supplier"
            criteria = {
                "name": extract_field_value(validated_fields, ["supplier_name", "vendor_name", "company_name"]),
                "gst": normalize_gst(extract_field_value(validated_fields, ["gst_number", "vendor_gst", "supplier_gst", "gstin", "vendor_gstin"])),
                "email": normalize_email(extract_field_value(validated_fields, ["supplier_email", "vendor_email", "email"])),
                "phone": normalize_phone(extract_field_value(validated_fields, ["supplier_phone", "vendor_phone", "phone", "contact_number"])),
                "address": extract_field_value(validated_fields, ["supplier_address", "vendor_address", "billing_address", "company_address", "address"]),
                "erp_id": extract_field_value(validated_fields, ["supplier_id", "vendor_id"]),
            }
        else:
            party_type = "customer"
            criteria = {
                "name": extract_field_value(validated_fields, ["customer_name", "buyer_name"]),
                "gst": normalize_gst(extract_field_value(validated_fields, ["customer_gst", "buyer_gst", "gst_number", "gstin", "buyer_gstin"])),
                "email": normalize_email(extract_field_value(validated_fields, ["customer_email", "email"])),
                "phone": normalize_phone(extract_field_value(validated_fields, ["customer_phone", "phone", "contact_number"])),
                "address": extract_field_value(validated_fields, ["customer_address", "billing_address", "buyer_address", "consignee_address", "shipping_address", "address"]),
                "erp_id": extract_field_value(validated_fields, ["customer_id"]),
            }

        stmt = select(ERPMasterMapping).where(
            ERPMasterMapping.master_type == party_type,
            ERPMasterMapping.is_active == True
        )
        if org_id:
            stmt = stmt.where(ERPMasterMapping.org_id == str(org_id))
        else:
            stmt = stmt.where(ERPMasterMapping.org_id.is_(None))

        res = await db.execute(stmt)
        mappings = res.scalars().all()

        if not mappings:
            return {
                "party_type": party_type,
                "status": "no_master_match",
                "confidence": 0.0,
                "selected_master_id": "",
                "selected_master_name": "",
                "candidates": [],
                "reasons": ["No master mapping records available in database"],
                "review_required": True,
                "blocking": False
            }

        candidates = []
        for mapping in mappings:
            eval_res = score_candidate(mapping, criteria)
            if eval_res["score"] >= 40:  # Include reasonable candidates
                candidates.append({
                    "master_id": mapping.erp_id,
                    "master_name": mapping.display_name or mapping.erp_name or mapping.detected_text,
                    "score": float(eval_res["score"]),
                    "matched_signals": eval_res["matched_signals"],
                    "conflict_signals": eval_res["conflict_signals"],
                    "reasons": eval_res["reasons"]
                })

        candidates.sort(key=lambda x: -x["score"])

        if not candidates:
            return {
                "party_type": party_type,
                "status": "no_master_match",
                "confidence": 0.0,
                "selected_master_id": "",
                "selected_master_name": "",
                "candidates": [],
                "reasons": ["No candidate matched the criteria above the threshold"],
                "review_required": True,
                "blocking": False
            }

        # Check for ambiguity
        best = candidates[0]
        is_ambiguous = False
        if len(candidates) > 1:
            second = candidates[1]
            if (best["score"] - second["score"]) < 10 and best["score"] >= 60:
                is_ambiguous = True

        status = "no_master_match"
        selected_master_id = ""
        selected_master_name = ""
        review_required = True
        blocking = False
        reasons = list(best["reasons"])

        has_conflict = len(best["conflict_signals"]) > 0
        has_critical_conflict = any(c in best["conflict_signals"] for c in ("gst", "name"))

        if is_ambiguous:
            status = "ambiguous_master_match"
            review_required = True
            blocking = True
            reasons = ["Multiple candidates matched with similar scores. Human review required."]
        elif has_critical_conflict:
            status = "possible_master_match"
            review_required = True
            blocking = False
            selected_master_name = best["master_name"]
            # Clear selected ID to prevent silent fill on conflict
            selected_master_id = ""
            reasons.append("Conflicting GST/name detected. Human review required.")
        else:
            score = best["score"]
            if score >= 95:
                status = "exact_master_match"
                review_required = False
                blocking = False
                selected_master_id = best["master_id"]
                selected_master_name = best["master_name"]
            elif score >= 80:
                status = "likely_master_match"
                review_required = (score < 85)
                blocking = False
                selected_master_id = best["master_id"]
                selected_master_name = best["master_name"]
            elif score >= 60:
                status = "possible_master_match"
                review_required = True
                blocking = False
                selected_master_name = best["master_name"]
                # Do not fill ID silently for possible match
            else:
                status = "no_master_match"
                review_required = True
                blocking = False

        return {
            "party_type": party_type,
            "status": status,
            "confidence": float(best["score"] / 100.0) if best else 0.0,
            "selected_master_id": selected_master_id,
            "selected_master_name": selected_master_name,
            "candidates": candidates[:5],
            "reasons": reasons,
            "review_required": review_required,
            "blocking": blocking
        }

    except Exception as e:
        logger.exception("Error during master matching: {}", e)
        return {
            "party_type": "unknown",
            "status": "no_master_match",
            "confidence": 0.0,
            "selected_master_id": "",
            "selected_master_name": "",
            "candidates": [],
            "reasons": [f"Internal master matching error: {str(e)}"],
            "review_required": False,
            "blocking": False
        }
