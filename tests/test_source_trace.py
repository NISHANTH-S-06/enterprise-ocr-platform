import pytest
from app.services.source_trace import build_source_traces, is_field_dynamic, is_field_reusable


def test_field_classification():
    # Dynamic fields
    assert is_field_dynamic("invoice_number") is True
    assert is_field_dynamic("invoice_date") is True
    assert is_field_dynamic("purchase_order_number") is True
    assert is_field_dynamic("quantity") is True
    assert is_field_dynamic("unit_price") is True
    assert is_field_dynamic("tax_amount") is True
    assert is_field_dynamic("grand_total") is True
    
    # Stable / Reusable fields
    assert is_field_dynamic("salesperson") is False
    assert is_field_reusable("salesperson") is True
    assert is_field_reusable("payment_term") is True
    assert is_field_reusable("freight_term") is True
    assert is_field_reusable("billing_address") is True
    assert is_field_reusable("freight_carrier") is True
    assert is_field_reusable("contact_person") is True
    assert is_field_reusable("currency") is True


def test_build_source_traces_basic():
    mapped = {
        "invoice_number": {"value": "INV-12345", "confidence": 0.95, "method": "regex", "source": "regex"},
        "payment_term": {"value": "Net 30", "confidence": 0.8, "method": "regex", "source": "regex"},
    }
    validated = {
        "invoice_number": {"value": "INV-12345", "confidence": 0.95, "method": "regex", "source": "regex"},
        "payment_term": {"value": "Net 30", "confidence": 0.8, "method": "regex", "source": "regex"},
    }
    validation_report = {"errors": [], "warnings": []}
    
    traces = build_source_traces(mapped, validated, validation_report)
    
    assert "invoice_number" in traces
    assert "payment_term" in traces
    
    inv_trace = traces["invoice_number"]
    assert inv_trace["field_name"] == "invoice_number"
    assert inv_trace["value"] == "INV-12345"
    assert inv_trace["normalized_value"] == "INV-12345"
    assert inv_trace["source_type"] == "current_document"
    assert inv_trace["source_engine"] == "paddleocr"  # Default
    assert inv_trace["confidence"] == 0.95
    assert inv_trace["validation_status"] == "valid"
    assert inv_trace["is_dynamic_field"] is True
    assert inv_trace["is_reusable_field"] is False
    
    pay_trace = traces["payment_term"]
    assert pay_trace["value"] == "Net 30"
    assert pay_trace["source_type"] == "current_document"
    assert pay_trace["is_dynamic_field"] is False
    assert pay_trace["is_reusable_field"] is True


def test_build_source_traces_training_supplied():
    mapped = {
        "payment_term": {
            "value": "Net 45",
            "confidence": 0.9,
            "method": "training_template",
            "source": "training",
            "training_dataset_id": "test-ds-123",
            "training_dataset_name": "Test Template",
            "source_evidence": "training_sample_value_fallback",
            "value_source": "training_dataset"
        }
    }
    validated = {
        "payment_term": {
            "value": "Net 45",
            "confidence": 0.9,
            "method": "training_template",
            "source": "training"
        }
    }
    validation_report = {"errors": [], "warnings": []}
    meta = {
        "training_template_id": "test-ds-123",
        "training_template_name": "Test Template",
        "training_conflicts": []
    }
    
    traces = build_source_traces(mapped, validated, validation_report, extra_meta=meta)
    
    pay_trace = traces["payment_term"]
    assert pay_trace["value"] == "Net 45"
    assert pay_trace["source_type"] == "training_dataset"
    assert pay_trace["source_engine"] == "training_template"
    assert pay_trace["is_training_supplied"] is True
    assert pay_trace["template_id"] == "test-ds-123"
    assert pay_trace["template_name"] == "Test Template"
    assert pay_trace["is_current_document_supplied"] is False


def test_build_source_traces_validation_failure():
    mapped = {
        "grand_total": {"value": "100.00", "confidence": 0.9, "method": "regex"},
    }
    validated = {
        "grand_total": {"value": "100.00", "confidence": 0.9, "method": "regex"},
    }
    validation_report = {
        "errors": ["Field 'grand_total' does not match item total: 100.00 vs 150.00"],
        "warnings": []
    }
    
    traces = build_source_traces(mapped, validated, validation_report)
    
    total_trace = traces["grand_total"]
    assert total_trace["validation_status"] == "invalid"
    assert "does not match item total" in total_trace["validation_reason"]
