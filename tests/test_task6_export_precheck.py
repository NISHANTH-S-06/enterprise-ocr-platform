from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.services.export_precheck_engine import build_export_precheck, quality_grade


def _sales(**overrides):
    payload = {
        "module": "sales_order",
        "document_type": "SALES_ORDER",
        "form_data": {
            "customer_name": "Acme Buyer", "customer_id": "CUST-1",
            "sales_order_no": "SO-100", "sales_order_date": "2026-06-11",
            "currency_code_id": "INR", "order_total": "100",
        },
        "item_rows": [{
            "product": "Widget", "product_id": "PROD-1", "qty": "2",
            "unit_price": "50", "line_total": "100",
        }],
    }
    payload.update(overrides)
    return payload


def _purchase(**overrides):
    payload = {
        "module": "purchase_order",
        "document_type": "PURCHASE_ORDER",
        "form_data": {
            "supplier_name": "Supply Co", "supplier_id": "SUP-1",
            "po_number": "PO-100", "po_date": "2026-06-11",
            "currency": "INR", "po_grand_total": "100",
        },
        "item_rows": [{
            "product": "Bolt", "product_id": "PROD-2", "qty": "10",
            "unit_price": "10", "line_total": "100",
        }],
    }
    payload.update(overrides)
    return payload


def test_precheck_endpoint_exists():
    from app.api.v1.routers.forms import router
    assert any(getattr(route, "path", "") == "/forms/workbench/export-precheck" for route in router.routes)


def test_complete_sales_payload_grade_a():
    result = build_export_precheck(_sales())
    assert result["quality_score"] == 100
    assert result["quality_grade"] == "A"
    assert result["export_allowed"] is True


def test_missing_sales_customer_blocks_export():
    payload = _sales()
    payload["form_data"].pop("customer_id")
    payload["form_data"].pop("customer_name")
    result = build_export_precheck(payload)
    assert result["export_allowed"] is False
    assert any(item["field"] == "customer_id" for item in result["blockers"])


def test_missing_purchase_supplier_blocks_export():
    payload = _purchase()
    payload["form_data"].pop("supplier_id")
    payload["form_data"].pop("supplier_name")
    result = build_export_precheck(payload)
    assert result["export_allowed"] is False
    assert any(item["field"] == "supplier_id" for item in result["blockers"])


def test_missing_product_id_blocks_erp_export():
    payload = _sales()
    payload["item_rows"][0].pop("product_id")
    result = build_export_precheck(payload)
    assert result["export_allowed"] is False
    assert any(item["code"] == "missing_product_id" for item in result["blockers"])


def test_low_confidence_required_field_requires_review():
    result = build_export_precheck(_sales(confidence_map={"sales_order_date": 0.70}))
    assert result["review_required"] is True
    assert result["low_confidence_count"] == 1


def test_optional_low_confidence_does_not_block():
    result = build_export_precheck(_sales(confidence_map={"remarks": 0.40}))
    assert result["export_allowed"] is True
    assert result["review_required"] is True


def test_suspicious_object_string_blocks_export():
    payload = _sales()
    payload["form_data"]["remarks"] = "[object Object]"
    result = build_export_precheck(payload)
    assert result["export_allowed"] is False
    assert any(item["code"] == "suspicious_value" for item in result["blockers"])


@pytest.mark.parametrize("payload", [_sales(item_rows=[]), _purchase(item_rows=[])])
def test_empty_rows_block_order_export(payload):
    result = build_export_precheck(payload)
    assert result["export_allowed"] is False
    assert any(item["code"] == "all_rows_empty" for item in result["blockers"])


def test_general_document_with_fields_can_export():
    result = build_export_precheck({"module": "general", "form_data": {"reference": "ABC"}})
    assert result["export_allowed"] is True


def test_unresolved_critical_master_blocks_export():
    result = build_export_precheck(_sales(unresolved_masters=[{
        "master_type": "customer", "source_value": "Acme Buyer", "target_field": "customer_id",
    }], form_data={**_sales()["form_data"], "customer_id": ""}))
    assert result["export_allowed"] is False
    assert any(item["code"] == "unresolved_critical_master" for item in result["blockers"])


def test_resolved_critical_master_removes_unresolved_blocker():
    result = build_export_precheck(_sales(
        unresolved_masters=[{"master_type": "customer", "source_value": "Acme Buyer", "target_field": "customer_id"}],
        resolved_masters=[{"master_type": "customer", "target_field": "customer_id", "erp_id": "CUST-1"}],
    ))
    assert result["export_allowed"] is True
    assert result["unresolved_master_count"] == 0


def test_quality_score_clamps_at_zero():
    payload = _sales(
        form_data={"remarks": "[object Object]"},
        item_rows=[{"product": "[object Object]", "qty": "-1", "unit_price": "-1"} for _ in range(20)],
        warnings=[f"warning {index}" for index in range(20)],
    )
    assert build_export_precheck(payload)["quality_score"] == 0


def test_quality_grade_rules():
    assert quality_grade(95) == "A"
    assert quality_grade(80) == "B"
    assert quality_grade(65) == "C"
    assert quality_grade(50) == "D"
    assert quality_grade(39) == "BLOCKED"
    assert quality_grade(100, hard_blocker=True) == "BLOCKED"


def test_auto_fill_response_includes_export_precheck():
    from app.api.v1.routers.forms import WorkbenchAutoFillRequest, build_workbench_auto_fill
    request = WorkbenchAutoFillRequest(module="sales_order", ocr_result={})
    result = build_workbench_auto_fill(request, {
        "document_type": "sales_order",
        "mapped_fields": {
            "customer_name": {"value": "Acme"}, "sales_order_no": {"value": "SO-1"},
            "sales_order_date": {"value": "2026-06-11"},
        },
        "item_rows": [{"product": "Widget", "qty": "1", "unit_price": "10", "line_total": "10"}],
    })
    assert "export_precheck" in result
    assert {"quality_score", "quality_grade", "export_allowed", "review_required", "blockers"} <= result.keys()


def _blocked_request():
    from app.api.v1.routers.forms import WorkbenchExportRequest
    return WorkbenchExportRequest(module="sales_order", form_data={"sales_order_date": "2026-06-11"})


@pytest.mark.parametrize("function_name", ["export_workbench_json", "export_workbench_excel", "export_workbench_pdf"])
def test_all_export_formats_reject_blocked_payload(function_name):
    from app.api.v1.routers import forms
    with pytest.raises(HTTPException) as exc:
        asyncio.run(getattr(forms, function_name)(_blocked_request(), current_user={"user_id": "u"}))
    assert exc.value.status_code == 422
    assert exc.value.detail["export_allowed"] is False
    assert "quality_score" in exc.value.detail
    assert exc.value.detail["blockers"]


def test_frontend_has_quality_score_ui_ids():
    source = Path("frontend/index.html").read_text(encoding="utf-8")
    for element_id in (
        "mainAiFill_qualityScore", "mainAiFill_qualityGrade", "mainAiFill_exportAllowed",
        "mainAiFill_missingRequiredList", "mainAiFill_unresolvedMastersList",
        "mainAiFill_lowConfidenceList", "mainAiFill_rowIssuesList", "mainAiFill_blockersList",
    ):
        assert f'id="{element_id}"' in source


def test_frontend_export_precheck_call_exists():
    source = Path("frontend/index.html").read_text(encoding="utf-8")
    assert 'req("/api/v1/forms/workbench/export-precheck"' in source
    assert "async function runMainExportPrecheck" in source


def test_existing_pdf_ocr_flow_contract_remains():
    from app.api.v1.routers.upload import ALLOWED_MIMES
    assert "application/pdf" in ALLOWED_MIMES and "image/jpeg" in ALLOWED_MIMES


def test_existing_tabular_flow_contract_remains():
    from app.services.tabular_document_parser import parse_csv_document
    result = parse_csv_document(b"Item,Qty,Price,Amount\nBolt,2,5,10\n", "items.csv")
    assert result["item_rows"][0]["product"] == "Bolt"


def test_existing_master_resolver_contract_remains():
    from app.api.v1.routers.companies import resolve_erp_master_scored
    result = resolve_erp_master_scored("customer", "Acme", {}, [], "sales_order")
    assert result["resolved"] is False and result["unresolved_reason"] == "no_master_data"
