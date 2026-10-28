import time
from typing import Any, Dict, List
from loguru import logger

from app.core.config import get_settings
from app.services.document_ai.result_schema import build_document_ai_result
from app.services.document_ai.evidence_validator import validate_evidence

settings = get_settings()

# Critical fields that trigger fallback if missing or low confidence
CRITICAL_FIELDS = {"invoice_number", "invoice_date", "grand_total"}

def should_trigger_fallback(
    mapped_result: Dict[str, Any],
    raw_text: str,
    validation: Dict[str, Any]
) -> bool:
    """
    Evaluates fallback trigger rules to decide if VLM extraction should run.
    """
    # 1. Feature flag gate
    if not settings.DOCUMENT_AI_ENABLED:
        return False

    # 2. OCR is weak or empty
    text_len = len((raw_text or "").strip())
    if mapped_result.get("weak_ocr") or text_len < 20:
        logger.info("Trigger: Fallback triggered due to weak/empty OCR text.")
        return True

    # 3. Check if critical fields are missing or have very low confidence
    mapped_fields = mapped_result.get("mapped_fields", {})
    validated_fields = validation.get("validated_fields", {})
    
    missing_critical = False
    low_confidence_critical = False

    for field in CRITICAL_FIELDS:
        # Check validated entries
        val_entry = validated_fields.get(field) or mapped_fields.get(field)
        val = None
        conf = 0.0

        if isinstance(val_entry, dict):
            val = val_entry.get("value")
            conf = float(val_entry.get("confidence", 0.0))
        elif val_entry is not None:
            val = val_entry
            conf = 0.5  # default baseline

        if val in (None, ""):
            missing_critical = True
        elif conf < 0.70:
            low_confidence_critical = True

    # Check for company identifiers: at least one of supplier_name, vendor_name, or customer_name must be present with high confidence
    company_fields = ["supplier_name", "vendor_name", "customer_name"]
    company_found = False
    company_low_conf = True

    for field in company_fields:
        val_entry = validated_fields.get(field) or mapped_fields.get(field)
        val = None
        conf = 0.0

        if isinstance(val_entry, dict):
            val = val_entry.get("value")
            conf = float(val_entry.get("confidence", 0.0))
        elif val_entry is not None:
            val = val_entry
            conf = 0.5

        if val not in (None, ""):
            company_found = True
            if conf >= 0.70:
                company_low_conf = False

    if not company_found:
        missing_critical = True
    elif company_low_conf:
        low_confidence_critical = True

    if missing_critical:
        logger.info("Trigger: Fallback triggered due to missing critical header fields.")
        return True

    if low_confidence_critical:
        logger.info("Trigger: Fallback triggered due to low-confidence critical fields.")
        return True

    # 4. Check if item rows table is missing/empty
    item_rows = validated_fields.get("item_rows") or mapped_fields.get("item_rows") or []
    if isinstance(item_rows, dict):
        item_rows = item_rows.get("value", [])
    if not isinstance(item_rows, list) or not item_rows:
        logger.info("Trigger: Fallback triggered due to missing line item rows.")
        return True

    # 5. Check if validation blockers/errors are high
    errors = validation.get("errors", [])
    if len(errors) > 2:
        logger.info(f"Trigger: Fallback triggered due to high validation error count ({len(errors)}).")
        return True

    # If existing baseline is strong, do not run fallback VLM
    return False


async def run_document_ai_fallback(
    mapped_result: Dict[str, Any],
    raw_text: str,
    validation: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Main entry point for routing document extraction tasks to candidate VLMs.
    """
    t0 = time.monotonic()

    # 1. Flag check
    if not settings.DOCUMENT_AI_ENABLED:
        return build_document_ai_result(
            enabled=False,
            provider="none",
            status="disabled",
            processing_ms=0,
            review_required=True
        )

    # 2. Trigger check
    if not should_trigger_fallback(mapped_result, raw_text, validation):
        return build_document_ai_result(
            enabled=True,
            provider=settings.DOCUMENT_AI_PROVIDER,
            status="skipped",
            processing_ms=0,
            review_required=True
        )

    # 3. Execution (Simulated/routed without heavy library imports)
    provider = settings.DOCUMENT_AI_PROVIDER.lower().strip()
    logger.info(f"Document AI fallback starting: provider={provider}")
    
    # Standard warning: no live models or network calls are executed in default skeleton
    warnings = ["VLM fallback adapter executed in mock/sandbox mode."]
    mapped_output: Dict[str, Any] = {}
    item_rows: List[Dict[str, Any]] = []
    
    # Simulate failed adapter/attemped behavior if provider is configured but mock/unimplemented
    if provider == "none":
        return build_document_ai_result(
            enabled=True,
            provider="none",
            status="failed",
            processing_ms=int((time.monotonic() - t0) * 1000),
            warnings=["No document AI provider is configured."],
            review_required=True,
            error_message="Fallback adapter configuration error"
        )
        
    status = "attempted"
    
    # Validate whatever mapped fields the fallback VLM returns (currently empty in base router skeleton)
    validated_fields, val_warnings, evidence_records, review_req = validate_evidence(
        mapped_fields=mapped_output,
        raw_text=raw_text,
        source_engine=provider,
        require_evidence=settings.DOCUMENT_AI_REQUIRE_EVIDENCE
    )
    
    warnings.extend(val_warnings)
    
    return build_document_ai_result(
        enabled=True,
        provider=provider,
        status="completed" if status == "attempted" else status,
        processing_ms=int((time.monotonic() - t0) * 1000),
        mapped_fields=validated_fields,
        item_rows=item_rows,
        evidence=evidence_records,
        warnings=warnings,
        review_required=review_req or (status != "completed"),
        error_message=None
    )
