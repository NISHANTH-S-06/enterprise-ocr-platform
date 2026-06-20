"""
test_ocr_benchmark.py — Unit tests for the OCR Accuracy Benchmark runner.
"""
from __future__ import annotations

import pytest
from tests.benchmark.benchmark_runner import (
    normalize_str,
    is_date_or_amount_field,
    compare_values,
    evaluate_document,
    compute_overall_stats
)


def test_normalize_str():
    assert normalize_str("  Hello, World!  ") == "helloworld"
    assert normalize_str("12,345.67") == "1234567"
    assert normalize_str("₹ 100.00") == "10000"
    assert normalize_str(None) == ""
    assert normalize_str("") == ""


def test_is_date_or_amount_field():
    # Header fields
    assert not is_date_or_amount_field("gst_number")
    assert not is_date_or_amount_field("buyer_name")
    assert not is_date_or_amount_field("company_address")
    
    # Date and amount fields
    assert is_date_or_amount_field("invoice_date")
    assert is_date_or_amount_field("po_date")
    assert is_date_or_amount_field("grand_total")
    assert is_date_or_amount_field("igst_amount")
    assert is_date_or_amount_field("taxable_value")


def test_compare_values():
    assert compare_values("GOODLEATHER SHOES PVT.LTD", "goodleather shoes pvt.ltd")
    assert compare_values("12,345.67", "12345.67")
    assert compare_values(None, "")
    assert not compare_values("123", "456")


def test_evaluate_document():
    actual = {
        "document_type": "COMMERCIAL_INVOICE",
        "fields": {
            "company_name": "Acme Corp",
            "gst_number": "33AAACG8998G1Z8",
            "invoice_number": "INV-001",
            "invoice_date": "2026-06-20",
            "grand_total": "1,000.00",
            "spurious_field": "some-value"
        },
        "item_rows": [
            {"sno": "1", "qty": "10"}
        ],
        "processing_time_ms": 150,
        "training_template_matched": True
    }
    
    ground_truth = {
        "filename": "test_doc.pdf",
        "document_type": "COMMERCIAL_INVOICE",
        "company_name": "Acme Corp",
        "fields": {
            "company_name": "Acme Corp",
            "gst_number": "33AAACG8998G1Z8",
            "invoice_number": "INV-001",
            "invoice_date": "2026-06-20",
            "grand_total": "1,000.00",
            "missing_expected": "should-have-been-filled"
        },
        "missing_fields": [
            "phone",
            "email"
        ],
        "item_rows_count": 1,
        "first_item_row": {
            "sno": "1",
            "qty": "10"
        }
    }
    
    res = evaluate_document(actual, ground_truth)
    
    assert res["filename"] == "test_doc.pdf"
    assert res["classification"]["matched"] is True
    assert res["company_match"]["matched"] is True
    # Headers check: company_name, gst_number match. missing_expected is missing.
    assert res["header_metrics"]["total"] == 4
    assert res["header_metrics"]["correct"] == 3
    assert res["header_metrics"]["missing"] == 1
    assert res["header_metrics"]["accuracy"] == 0.75
    
    # Dates & Amounts check: invoice_number (header), invoice_date (D/A), grand_total (D/A) match.
    assert res["dates_and_amounts_metrics"]["total"] == 2  # invoice_date, grand_total
    assert res["dates_and_amounts_metrics"]["correct"] == 2
    assert res["dates_and_amounts_metrics"]["accuracy"] == 1.0
    
    # Item rows
    assert res["item_rows_metrics"]["count_matched"] is True
    assert res["item_rows_metrics"]["first_row_matched"] is True
    
    # Missing fields
    assert res["missing_fields_metrics"]["expected_missing_count"] == 2
    assert res["missing_fields_metrics"]["actually_missing_count"] == 2
    assert res["missing_fields_metrics"]["false_positives_count"] == 0
    
    # Auto-fill coverage: 6 expected filled fields, 5 actual filled fields
    assert res["auto_fill_coverage"] == 5 / 6


def test_compute_overall_stats():
    res1 = {
        "classification": {"matched": True},
        "company_match": {"matched": True},
        "header_metrics": {"accuracy": 0.8},
        "dates_and_amounts_metrics": {"accuracy": 0.9},
        "auto_fill_coverage": 0.85,
        "missing_fields_metrics": {"false_positives_count": 1},
        "processing_time_ms": 100
    }
    res2 = {
        "classification": {"matched": False},
        "company_match": {"matched": True},
        "header_metrics": {"accuracy": 0.6},
        "dates_and_amounts_metrics": {"accuracy": 0.7},
        "auto_fill_coverage": 0.65,
        "missing_fields_metrics": {"false_positives_count": 0},
        "processing_time_ms": 200
    }
    
    overall = compute_overall_stats([res1, res2])
    
    assert overall["total_documents"] == 2
    assert overall["classification_accuracy"] == 0.5
    assert overall["company_matching_accuracy"] == 1.0
    assert overall["average_header_accuracy"] == 0.7
    assert overall["average_dates_and_amounts_accuracy"] == 0.8
    assert overall["average_auto_fill_coverage"] == 0.75
    assert overall["total_false_positives"] == 1
    assert overall["total_processing_time_ms"] == 300
    assert overall["average_processing_time_ms"] == 150
