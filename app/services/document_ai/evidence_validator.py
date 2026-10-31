from typing import Any, Dict, List, Optional, Tuple
from loguru import logger

def validate_evidence(
    mapped_fields: Dict[str, Any],
    raw_text: str,
    source_engine: str = "none",
    require_evidence: bool = True
) -> Tuple[Dict[str, Any], List[str], List[Dict[str, Any]], bool]:
    """
    Validates model extraction output against strict evidence verification rules.
    Each field in mapped_fields should conform to:
    {
        "value": Any,
        "confidence": float,
        "source_evidence": str,
        "page_number": Optional[int]
    }
    
    Returns:
        validated_fields: Dict of approved fields with updated source traces.
        warnings: List of warning strings.
        evidence_records: List of normalized evidence dictionaries.
        review_required: Boolean flag indicating if human review is needed.
    """
    validated_fields: Dict[str, Any] = {}
    warnings: List[str] = []
    evidence_records: List[Dict[str, Any]] = []
    review_required = False

    if not mapped_fields:
        return validated_fields, warnings, evidence_records, review_required

    raw_text_lower = (raw_text or "").lower()

    for field_name, data in mapped_fields.items():
        if field_name == "item_rows":
            # Pass rows through (or process separately if needed, but keep it simple for flat schema verification)
            validated_fields[field_name] = data
            continue

        if not isinstance(data, dict):
            warnings.append(f"Field '{field_name}' data format is invalid.")
            review_required = True
            continue

        val = data.get("value")
        confidence = float(data.get("confidence", 0.0))
        evidence_text = str(data.get("source_evidence") or "").strip()
        page_num = data.get("page_number")

        # 1. Check if evidence is missing
        if val in (None, ""):
            # Already empty field, no validation needed
            validated_fields[field_name] = data
            continue

        if require_evidence and not evidence_text:
            # Reject auto-fill, require review
            warnings.append(f"Field '{field_name}' rejected: missing evidence snippet.")
            review_required = True
            evidence_rec = {
                "field_name": field_name,
                "value": val,
                "confidence": confidence,
                "evidence_text": "",
                "page_number": page_num,
                "source_engine": source_engine,
                "source_type": "model_fallback",
                "review_status": "rejected_no_evidence",
            }
            evidence_records.append(evidence_rec)
            continue

        # 2. Check if evidence exists in raw OCR text
        if require_evidence and evidence_text.lower() not in raw_text_lower:
            warnings.append(f"Field '{field_name}' rejected: evidence snippet not found in raw OCR text.")
            review_required = True
            evidence_rec = {
                "field_name": field_name,
                "value": val,
                "confidence": confidence,
                "evidence_text": evidence_text,
                "page_number": page_num,
                "source_engine": source_engine,
                "source_type": "model_fallback",
                "review_status": "rejected_unverifiable_evidence",
            }
            evidence_records.append(evidence_rec)
            continue

        # Evidence is validated successfully!
        evidence_rec = {
            "field_name": field_name,
            "value": val,
            "confidence": confidence,
            "evidence_text": evidence_text,
            "page_number": page_num,
            "source_engine": source_engine,
            "source_type": "model_fallback",
            "review_status": "accepted_with_evidence",
        }
        evidence_records.append(evidence_rec)

        # Build clean trace output for main pipeline re-entry
        validated_fields[field_name] = {
            "value": val,
            "confidence": confidence,
            "method": "document_ai",
            "source": "document_ai_fallback",
            "source_evidence": evidence_text,
            "page_number": page_num,
            "value_source": "fallback",
        }

    return validated_fields, warnings, evidence_records, review_required
