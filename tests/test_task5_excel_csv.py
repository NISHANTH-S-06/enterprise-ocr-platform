from __future__ import annotations

import asyncio
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, UploadFile
from openpyxl import Workbook

from app.services.tabular_document_parser import parse_csv_document, parse_excel_document


def _xlsx(rows, title="Sheet1") -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = title
    for row in rows:
        sheet.append(row)
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


SALES_XLSX = _xlsx([
    ["Customer Name", "Acme Buyer"],
    ["Sales Order No", "SO-100"],
    ["Order Date", "2026-06-11"],
    ["Payment Terms", "Net 30"],
    [],
    ["Item", "Part No", "Qty", "UOM", "Unit Price", "Amount"],
    ["Widget", "W-1", 2, "EA", 50, 100],
], "Sales Order")

PURCHASE_XLSX = _xlsx([
    ["Supplier", "Supply Co"],
    ["PO Number", "PO-900"],
    ["PO Date", "2026-06-10"],
    [],
    ["Description", "HSN", "Quantity", "Rate", "GST Rate", "Net Amount"],
    ["Steel Bolt", "7318", 10, 5, "18%", 50],
], "Purchase Order")


def test_parse_sales_excel_key_values():
    result = parse_excel_document(SALES_XLSX, "sales_order_sample.xlsx")
    assert result["mapped_fields"]["customer_name"]["value"] == "Acme Buyer"
    assert result["mapped_fields"]["sales_order_no"]["value"] == "SO-100"


def test_parse_purchase_excel_key_values():
    result = parse_excel_document(PURCHASE_XLSX, "purchase_order_sample.xlsx")
    assert result["mapped_fields"]["supplier_name"]["value"] == "Supply Co"
    assert result["mapped_fields"]["po_number"]["value"] == "PO-900"


def test_parse_excel_item_rows():
    row = parse_excel_document(SALES_XLSX, "sales.xlsx")["item_rows"][0]
    assert row["product"] == "Widget" and row["qty"] == "2" and row["line_total"] == "100"


def test_parse_csv_item_rows():
    result = parse_csv_document(b"Item,Qty,Rate,Amount\nBolt,3,5,15\n", "item_rows_sample.csv")
    assert result["item_rows"][0] == {"product": "Bolt", "qty": "3", "unit_price": "5", "line_total": "15"}


def test_csv_delimiter_detection():
    result = parse_csv_document(b"Item;Qty;Price;Total\nNut;4;2;8\n", "items.csv")
    assert result["delimiter"] == ";" and result["item_rows"][0]["qty"] == "4"


def test_utf8_bom_csv():
    result = parse_csv_document("Item,Qty,Amount\nCaf\u00e9,1,9\n".encode("utf-8-sig"), "bom.csv")
    assert result["item_rows"][0]["product"] == "Caf\u00e9"


def test_empty_csv_rejected():
    with pytest.raises(ValueError, match="empty"):
        parse_csv_document(b"", "empty.csv")


def test_unsupported_xls_warning():
    with pytest.raises(ValueError, match="Legacy \\.xls is not supported"):
        parse_excel_document(b"legacy", "legacy.xls")


def test_excel_produces_text_shapes():
    result = parse_excel_document(SALES_XLSX, "sales.xlsx")
    assert "Acme Buyer" in result["raw_text"] and result["extracted_text"] == result["raw_text"]


def test_csv_produces_item_rows():
    assert parse_csv_document(b"Product|Quantity|Amount\nPen|2|20\n", "items.csv")["item_rows"]


def test_excel_classifies_sales_order():
    result = parse_excel_document(SALES_XLSX, "sales.xlsx")
    assert (result["module"], result["document_type"]) == ("sales_order", "SALES_ORDER")


def test_excel_classifies_purchase_order():
    result = parse_excel_document(PURCHASE_XLSX, "purchase.xlsx")
    assert (result["module"], result["document_type"]) == ("purchase_order", "PURCHASE_ORDER")


def test_workbench_parse_endpoint_returns_mapped_fields():
    from app.api.v1.routers.forms import parse_workbench_tabular
    upload = UploadFile(filename="sales.xlsx", file=BytesIO(SALES_XLSX))
    result = asyncio.run(parse_workbench_tabular(upload, current_user={"user_id": "u1"}))
    assert result["mapped_fields"]["customer_name"]["value"] == "Acme Buyer"


def test_workbench_parse_endpoint_returns_item_rows():
    from app.api.v1.routers.forms import parse_workbench_tabular
    upload = UploadFile(filename="items.csv", file=BytesIO(b"Item,Qty,Price,Amount\nBolt,2,5,10\n"))
    result = asyncio.run(parse_workbench_tabular(upload, current_user={"user_id": "u1"}))
    assert result["item_rows"][0]["product"] == "Bolt"


def test_main_auto_fill_accepts_tabular_result():
    from app.api.v1.routers.forms import WorkbenchAutoFillRequest, build_workbench_auto_fill
    parsed = parse_excel_document(SALES_XLSX, "sales.xlsx")
    result = build_workbench_auto_fill(WorkbenchAutoFillRequest(module="sales_order", ocr_result=parsed), parsed)
    assert result["form_data"]["customer_name"] == "Acme Buyer" and result["row_count"] == 1


def test_dataset_mapping_applies_to_excel_fields():
    from app.api.v1.routers.forms import WorkbenchAutoFillRequest, build_workbench_auto_fill
    parsed = parse_excel_document(SALES_XLSX, "sales.xlsx")
    dataset = {"mappings_json": [{"ocr_key": "customer_name", "target_field": "remarks"}]}
    result = build_workbench_auto_fill(WorkbenchAutoFillRequest(module="sales_order", ocr_result=parsed), parsed, dataset)
    assert result["form_data"]["remarks"] == "Acme Buyer" and result["dataset_mapped_count"] == 1


def test_correction_learning_applies_to_excel_fields():
    from app.api.v1.routers.forms import WorkbenchAutoFillRequest, build_workbench_auto_fill
    parsed = parse_excel_document(SALES_XLSX, "sales.xlsx")
    correction = {"source_ocr_key": "customer_name", "normalized_source_key": "customer_name", "target_erp_name": "customer_name", "corrected_value_sample": "Corrected Buyer", "is_active": True, "confidence": 1.0}
    result = build_workbench_auto_fill(WorkbenchAutoFillRequest(module="sales_order", ocr_result=parsed), parsed, corrections=[correction])
    assert result["form_data"]["customer_name"] == "Corrected Buyer" and result["correction_applied_count"] == 1


def _master(master_type, text, erp_id, module="sales_order"):
    from app.api.v1.routers.companies import normalize_erp_master_text
    return SimpleNamespace(master_type=master_type, detected_text=text, normalized_text=normalize_erp_master_text(text), erp_id=erp_id, erp_code=None, erp_name=text, display_name=text, alias_names=None, gst_number=None, part_number=None, hsn_code=None, tax_rate=None, module=module, is_active=True, priority=0)


def test_erp_master_resolver_resolves_excel_values():
    from app.api.v1.routers.forms import WorkbenchAutoFillRequest, build_workbench_auto_fill
    parsed = parse_excel_document(SALES_XLSX, "sales.xlsx")
    mappings = [_master("customer", "Acme Buyer", "CUST-1"), _master("product", "Widget", "PROD-1")]
    result = build_workbench_auto_fill(WorkbenchAutoFillRequest(module="sales_order", ocr_result=parsed), parsed, erp_mappings=mappings)
    assert result["form_data"]["customer_id"] == "CUST-1" and result["item_rows"][0]["product_id"] == "PROD-1"


def test_export_gate_still_blocks_missing_critical_ids():
    from app.api.v1.routers.forms import WorkbenchRequest, validate_workbench_payload
    parsed = parse_excel_document(SALES_XLSX, "sales.xlsx")
    result = validate_workbench_payload(WorkbenchRequest(module="sales_order", form_data={"customer_name": "Acme Buyer"}, item_rows=parsed["item_rows"]))
    assert result["export_allowed"] is False and "customer_id" in result["blocked_fields"]


def test_existing_pdf_ocr_types_remain_supported():
    from app.api.v1.routers.upload import ALLOWED_MIMES
    assert {"application/pdf", "image/png", "image/jpeg"}.issubset(ALLOWED_MIMES)


def test_endpoint_rejects_xls_clearly():
    from app.api.v1.routers.forms import parse_workbench_tabular
    with pytest.raises(HTTPException) as exc:
        asyncio.run(parse_workbench_tabular(UploadFile(filename="old.xls", file=BytesIO(b"x")), current_user={"user_id": "u1"}))
    assert exc.value.status_code == 415 and "Legacy .xls" in exc.value.detail
