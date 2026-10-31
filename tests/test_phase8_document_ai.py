import pytest
from fastapi import HTTPException
from app.core.config import get_settings
from app.services.document_ai.result_schema import build_document_ai_result
from app.services.document_ai.evidence_validator import validate_evidence
from app.services.document_ai.model_router import (
    should_trigger_fallback,
    run_document_ai_fallback,
)

settings = get_settings()

def test_feature_flags_default_disabled():
    """Verify that all VLM fallback adapter settings default to disabled/safety settings."""
    assert settings.DOCUMENT_AI_ENABLED is False
    assert settings.DOCUMENT_AI_PROVIDER == "none"
    assert settings.DOCUMENT_AI_MODE == "fallback_only"
    assert settings.DOCUMENT_AI_REQUIRE_EVIDENCE is True
    assert settings.DOCUMENT_AI_ALLOW_CLOUD is False
    assert settings.DOCUMENT_AI_TIMEOUT_SECONDS == 30


def test_normalized_schema_structure():
    """Verify correctness and type defaults of normalized result schema."""
    res = build_document_ai_result()
    assert res["enabled"] is False
    assert res["provider"] == "none"
    assert res["mode"] == "fallback_only"
    assert res["status"] == "disabled"
    assert res["processing_ms"] == 0
    assert isinstance(res["mapped_fields"], dict)
    assert isinstance(res["item_rows"], list)
    assert isinstance(res["evidence"], list)
    assert isinstance(res["warnings"], list)
    assert res["review_required"] is True
    assert res["error_message"] is None


def test_model_router_returns_disabled_by_default():
    """When DOCUMENT_AI_ENABLED is False, model router must return disabled status immediately."""
    assert settings.DOCUMENT_AI_ENABLED is False
    
    # Even with weak mapping or OCR, it must return disabled
    mapped_res = {"weak_ocr": True, "mapped_fields": {}}
    raw_text = "low quality image text"
    val_report = {"valid": False, "validated_fields": {}, "errors": ["missing invoice_no"]}

    import asyncio
    res = asyncio.run(run_document_ai_fallback(mapped_res, raw_text, val_report))
    assert res["enabled"] is False
    assert res["provider"] == "none"
    assert res["status"] == "disabled"


def test_fallback_trigger_evaluation(monkeypatch):
    """Test standard fallback triggers for OCR text size, missing fields, low confidence, and validation errors."""
    
    # 1. Flag disabled: should never trigger
    monkeypatch.setattr(settings, "DOCUMENT_AI_ENABLED", False)
    mapped_res = {"weak_ocr": True, "mapped_fields": {}}
    raw_text = "some text"
    val_report = {"valid": False, "validated_fields": {}, "errors": ["error"]}
    assert should_trigger_fallback(mapped_res, raw_text, val_report) is False

    # Enable flag for trigger evaluations
    monkeypatch.setattr(settings, "DOCUMENT_AI_ENABLED", True)

    # 2. Strong OCR, complete fields, high confidence: should NOT trigger fallback
    strong_mapped = {
        "weak_ocr": False,
        "mapped_fields": {
            "invoice_number": {"value": "INV-123", "confidence": 0.95},
            "invoice_date": {"value": "2026-06-23", "confidence": 0.92},
            "grand_total": {"value": "1500.00", "confidence": 0.98},
            "supplier_name": {"value": "Lenovo Store", "confidence": 0.89},
            "item_rows": [{"sno": "1", "qty": "2", "price": "750", "total": "1500"}]
        }
    }
    strong_val = {
        "valid": True,
        "validated_fields": {
            "invoice_number": {"value": "INV-123", "confidence": 0.95},
            "invoice_date": {"value": "2026-06-23", "confidence": 0.92},
            "grand_total": {"value": "1500.00", "confidence": 0.98},
            "supplier_name": {"value": "Lenovo Store", "confidence": 0.89},
            "item_rows": [{"sno": "1", "qty": "2", "price": "750", "total": "1500"}]
        },
        "errors": []
    }
    assert should_trigger_fallback(strong_mapped, "INV-123 dated 2026-06-23 total 1500 Lenovo Store item 1 qty 2 price 750", strong_val) is False

    # 3. Weak OCR: should trigger
    weak_mapped = {"weak_ocr": True, "mapped_fields": {}}
    assert should_trigger_fallback(weak_mapped, "low text", strong_val) is True

    # 4. Short OCR text: should trigger
    assert should_trigger_fallback(strong_mapped, "short", strong_val) is True

    # 5. Missing critical header fields: should trigger
    missing_critical_mapped = {
        "weak_ocr": False,
        "mapped_fields": {
            "invoice_date": {"value": "2026-06-23", "confidence": 0.92},
            "grand_total": {"value": "1500.00", "confidence": 0.98},
            "item_rows": [{"sno": "1"}]
        }
    }
    missing_critical_val = {
        "valid": True,
        "validated_fields": {
            "invoice_date": {"value": "2026-06-23", "confidence": 0.92},
            "grand_total": {"value": "1500.00", "confidence": 0.98},
            "item_rows": [{"sno": "1"}]
        },
        "errors": []
    }
    assert should_trigger_fallback(missing_critical_mapped, "INV-123 dated 2026-06-23 total 1500", missing_critical_val) is True

    # 6. Low confidence critical field (< 0.70): should trigger
    low_conf_mapped = {
        "weak_ocr": False,
        "mapped_fields": {
            "invoice_number": {"value": "INV-123", "confidence": 0.45},  # Low confidence
            "invoice_date": {"value": "2026-06-23", "confidence": 0.92},
            "grand_total": {"value": "1500.00", "confidence": 0.98},
            "item_rows": [{"sno": "1"}]
        }
    }
    low_conf_val = {
        "valid": True,
        "validated_fields": {
            "invoice_number": {"value": "INV-123", "confidence": 0.45},
            "invoice_date": {"value": "2026-06-23", "confidence": 0.92},
            "grand_total": {"value": "1500.00", "confidence": 0.98},
            "item_rows": [{"sno": "1"}]
        },
        "errors": []
    }
    assert should_trigger_fallback(low_conf_mapped, "INV-123 dated 2026-06-23 total 1500", low_conf_val) is True

    # 7. Missing item rows: should trigger
    no_items_mapped = {
        "weak_ocr": False,
        "mapped_fields": {
            "invoice_number": {"value": "INV-123", "confidence": 0.95},
            "invoice_date": {"value": "2026-06-23", "confidence": 0.92},
            "grand_total": {"value": "1500.00", "confidence": 0.98},
            "supplier_name": {"value": "Lenovo Store", "confidence": 0.89},
        }
    }
    no_items_val = {
        "valid": True,
        "validated_fields": {
            "invoice_number": {"value": "INV-123", "confidence": 0.95},
            "invoice_date": {"value": "2026-06-23", "confidence": 0.92},
            "grand_total": {"value": "1500.00", "confidence": 0.98},
            "supplier_name": {"value": "Lenovo Store", "confidence": 0.89},
        },
        "errors": []
    }
    assert should_trigger_fallback(no_items_mapped, "INV-123 dated 2026-06-23 total 1500", no_items_val) is True


def test_evidence_validation_logic():
    """Verify that evidence validator verifies exact snippets in OCR raw text and filters unevidenced extractions."""
    raw_ocr = "Our Invoice No is INV-9999 from seller Lenox India Corp."
    
    # 1. Valid fields with verifiable evidence
    mapped = {
        "invoice_number": {"value": "INV-9999", "confidence": 0.95, "source_evidence": "INV-9999", "page_number": 1},
        "supplier_name": {"value": "Lenox India Corp.", "confidence": 0.90, "source_evidence": "Lenox India Corp.", "page_number": 1}
    }
    validated, warnings, evidence, review_req = validate_evidence(mapped, raw_ocr, "mock_vlm")
    assert "invoice_number" in validated
    assert "supplier_name" in validated
    assert validated["invoice_number"]["value"] == "INV-9999"
    assert validated["invoice_number"]["source"] == "document_ai_fallback"
    assert len(warnings) == 0
    assert review_req is False
    assert len(evidence) == 2
    assert evidence[0]["review_status"] == "accepted_with_evidence"

    # 2. Missing evidence snippet (rejected)
    missing_evidence = {
        "invoice_number": {"value": "INV-9999", "confidence": 0.95, "source_evidence": "", "page_number": 1}
    }
    validated2, warnings2, evidence2, review_req2 = validate_evidence(missing_evidence, raw_ocr, "mock_vlm")
    assert "invoice_number" not in validated2
    assert len(warnings2) == 1
    assert "missing evidence snippet" in warnings2[0]
    assert review_req2 is True
    assert evidence2[0]["review_status"] == "rejected_no_evidence"

    # 3. Unverifiable evidence snippet (rejected)
    unverifiable = {
        "invoice_number": {"value": "INV-9999", "confidence": 0.95, "source_evidence": "NOT_IN_OCR_TEXT", "page_number": 1}
    }
    validated3, warnings3, evidence3, review_req3 = validate_evidence(unverifiable, raw_ocr, "mock_vlm")
    assert "invoice_number" not in validated3
    assert len(warnings3) == 1
    assert "evidence snippet not found" in warnings3[0]
    assert review_req3 is True
    assert evidence3[0]["review_status"] == "rejected_unverifiable_evidence"
