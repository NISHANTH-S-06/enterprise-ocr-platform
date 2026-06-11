"""
Task 4 — ERP Master Import + Resolver Hardening tests.
No live DB, no PaddleOCR required.
"""
from __future__ import annotations
import csv
import io
import json
from pathlib import Path
from types import SimpleNamespace


# ── helpers ───────────────────────────────────────────────────────────────────

def _mapping(master_type, text, erp_id, module="sales_order", **kwargs):
    from app.api.v1.routers.companies import normalize_erp_master_text
    ns = SimpleNamespace(
        master_type=master_type,
        detected_text=text,
        normalized_text=normalize_erp_master_text(text),
        erp_id=erp_id,
        erp_code=kwargs.get("erp_code"),
        erp_name=kwargs.get("erp_name"),
        display_name=kwargs.get("display_name"),
        alias_names=kwargs.get("alias_names"),
        gst_number=kwargs.get("gst_number"),
        pan_number=kwargs.get("pan_number"),
        hsn_code=kwargs.get("hsn_code"),
        part_number=kwargs.get("part_number"),
        product_description=kwargs.get("product_description"),
        uom=kwargs.get("uom"),
        tax_rate=kwargs.get("tax_rate"),
        currency_code=kwargs.get("currency_code"),
        module=module,
        is_active=True,
    )
    return ns


class TestTask4ERPMasterModelFields:
    def test_model_has_new_columns(self):
        from app.models.company import ERPMasterMapping
        cols = {c.key for c in ERPMasterMapping.__table__.columns}
        for field in ("legal_name", "alias_names", "gst_number", "pan_number",
                      "hsn_code", "part_number", "product_description", "uom",
                      "tax_rate", "currency_code", "display_name"):
            assert field in cols, f"Missing column: {field}"

    def test_master_type_expansion_model(self):
        from app.api.v1.routers.companies import ERP_MASTER_TYPES
        for t in ("payment_term", "delivery_term", "freight_term", "currency",
                  "branch", "project", "salesperson", "carrier"):
            assert t in ERP_MASTER_TYPES, f"Missing master_type: {t}"

    def test_migration_0008_exists(self):
        migration = Path("alembic/versions/0008_harden_erp_master_mappings.py").read_text(encoding="utf-8")
        assert 'down_revision = "0007"' in migration
        assert "legal_name" in migration
        assert "gst_number" in migration
        assert "part_number" in migration
        assert "payment_term" in migration


class TestTask4ColumnAliasDetection:
    def test_customer_csv_column_detection(self):
        from app.api.v1.routers.companies import _detect_column_mapping
        headers = ["customer_id", "customer_name", "gstin", "legal_name"]
        result = _detect_column_mapping(headers, "customer")
        assert result.get("erp_id") == "customer_id"
        assert result.get("detected_text") == "customer_name"
        assert result.get("gst_number") == "gstin"

    def test_supplier_csv_column_detection(self):
        from app.api.v1.routers.companies import _detect_column_mapping
        headers = ["vendor_id", "vendor_name", "gst_number"]
        result = _detect_column_mapping(headers, "supplier")
        assert result.get("erp_id") == "vendor_id"
        assert result.get("detected_text") == "vendor_name"

    def test_product_csv_column_detection(self):
        from app.api.v1.routers.companies import _detect_column_mapping
        headers = ["product_id", "product_name", "hsn", "uom", "part_no"]
        result = _detect_column_mapping(headers, "product")
        assert result.get("erp_id") == "product_id"
        assert result.get("detected_text") == "product_name"
        assert result.get("hsn_code") == "hsn"
        assert result.get("part_number") == "part_no"


class TestTask4ImportParsing:
    def _csv_bytes(self, headers, rows):
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=headers)
        w.writeheader()
        w.writerows(rows)
        return buf.getvalue().encode("utf-8")

    def test_csv_import_parses_customer(self):
        from app.api.v1.routers.companies import _parse_import_rows
        rows = [{"customer_id": "CUST-001", "customer_name": "ABC Exports Pvt Ltd", "gstin": "29ABCDE1234F1Z5"}]
        requests, errors = _parse_import_rows(rows, "customer", "sales_order")
        assert not errors
        assert len(requests) == 1
        assert requests[0].erp_id == "CUST-001"
        assert requests[0].detected_text == "ABC Exports Pvt Ltd"
        assert requests[0].gst_number == "29ABCDE1234F1Z5"

    def test_csv_import_parses_supplier(self):
        from app.api.v1.routers.companies import _parse_import_rows
        rows = [{"vendor_id": "SUP-010", "vendor_name": "Global Supply Co"}]
        requests, errors = _parse_import_rows(rows, "supplier", "purchase_order")
        assert not errors
        assert requests[0].erp_id == "SUP-010"
        assert requests[0].detected_text == "Global Supply Co"

    def test_csv_import_parses_product(self):
        from app.api.v1.routers.companies import _parse_import_rows
        rows = [{"product_id": "PROD-100", "product_name": "Widget A", "part_no": "WGT-A", "hsn": "8544"}]
        requests, errors = _parse_import_rows(rows, "product", None)
        assert not errors
        assert requests[0].erp_id == "PROD-100"
        assert requests[0].part_number == "WGT-A"
        assert requests[0].hsn_code == "8544"

    def test_json_import_parses_records(self):
        from app.api.v1.routers.companies import _parse_import_rows
        rows = [
            {"erp_id": "TAX-18", "tax_group": "GST 18%", "tax_rate": "18"},
            {"erp_id": "TAX-5", "tax_group": "GST 5%", "tax_rate": "5"},
        ]
        requests, errors = _parse_import_rows(rows, "tax_group", None)
        assert len(requests) == 2
        assert requests[0].erp_id == "TAX-18"
        assert requests[0].tax_rate == 18.0

    def test_missing_erp_id_produces_error(self):
        from app.api.v1.routers.companies import _parse_import_rows
        rows = [{"customer_name": "No ID Corp"}]
        requests, errors = _parse_import_rows(rows, "customer", None)
        assert len(errors) == 1
        assert len(requests) == 0


class TestTask4ScoredResolver:
    def test_exact_erp_code_score_100(self):
        from app.api.v1.routers.companies import resolve_erp_master_scored
        m = _mapping("customer", "ABC Exports", "CUST-1", erp_code="ABC-EXP")
        result = resolve_erp_master_scored("customer", "ABC Exports", {"erp_code": "ABC-EXP"}, [m], "sales_order")
        assert result["resolved"] is True
        assert result["score"] == 100
        assert result["match_reason"] == "exact_erp_code"

    def test_gst_exact_score_98(self):
        from app.api.v1.routers.companies import resolve_erp_master_scored
        m = _mapping("customer", "ABC Ltd", "CUST-2", gst_number="29ABCDE1234F1Z5")
        result = resolve_erp_master_scored("customer", "ABC Ltd", {"gst_number": "29ABCDE1234F1Z5"}, [m], "sales_order")
        assert result["resolved"] is True
        assert result["score"] == 98
        assert result["match_reason"] == "exact_gst"

    def test_part_number_score_95(self):
        from app.api.v1.routers.companies import resolve_erp_master_scored
        m = _mapping("product", "Widget A", "PROD-1", part_number="WGT-A")
        result = resolve_erp_master_scored("product", "Widget A", {"part_number": "WGT-A"}, [m], "sales_order")
        assert result["resolved"] is True
        assert result["score"] == 95

    def test_alias_exact_score_85(self):
        from app.api.v1.routers.companies import resolve_erp_master_scored
        m = _mapping("customer", "ABC Exports Private Limited", "CUST-3", alias_names="ABC Exports|ABC Pvt Ltd")
        result = resolve_erp_master_scored("customer", "ABC Exports", {}, [m], "sales_order")
        assert result["resolved"] is True
        assert result["score"] == 85
        assert result["match_reason"] == "alias_exact"

    def test_normalized_name_score_90(self):
        from app.api.v1.routers.companies import resolve_erp_master_scored
        m = _mapping("customer", "ABC Exports Pvt Ltd", "CUST-4")
        result = resolve_erp_master_scored("customer", "ABC Exports Pvt Ltd", {}, [m], "sales_order")
        assert result["resolved"] is True
        assert result["score"] == 90

    def test_fuzzy_low_score_unresolved(self):
        from app.api.v1.routers.companies import resolve_erp_master_scored
        m = _mapping("customer", "Completely Different Company Name", "CUST-5")
        result = resolve_erp_master_scored("customer", "Unknown Buyer XYZ", {}, [m], "sales_order")
        assert result["resolved"] is False

    def test_no_master_data_unresolved(self):
        from app.api.v1.routers.companies import resolve_erp_master_scored
        result = resolve_erp_master_scored("customer", "Some Customer", {}, [], "sales_order")
        assert result["resolved"] is False
        assert result["unresolved_reason"] == "no_master_data"


class TestTask4BulkResolve:
    def test_bulk_resolve_fills_customer_id(self):
        from app.api.v1.routers.companies import resolve_erp_master_values
        m = _mapping("customer", "Acme Ltd", "CUST-9")
        result = resolve_erp_master_values({"customer_name": "Acme Ltd"}, [], [m], "sales_order")
        assert result["resolved_fields"]["customer_id"] == "CUST-9"

    def test_bulk_resolve_fills_supplier_id(self):
        from app.api.v1.routers.companies import resolve_erp_master_values
        m = _mapping("supplier", "Global Supplies", "SUP-5", module="purchase_order")
        result = resolve_erp_master_values({"supplier_name": "Global Supplies"}, [], [m], "purchase_order")
        assert result["resolved_fields"]["supplier_id"] == "SUP-5"

    def test_bulk_resolve_fills_product_id_in_rows(self):
        from app.api.v1.routers.companies import resolve_erp_master_values
        m = _mapping("product", "Widget A", "PROD-1")
        result = resolve_erp_master_values({}, [{"product": "Widget A"}], [m], "sales_order")
        assert result["resolved_rows"][0]["product_id"] == "PROD-1"


class TestTask4ExportGate:
    def test_missing_product_id_blocks_sales_export(self):
        from app.api.v1.routers.forms import WorkbenchRequest, validate_workbench_payload
        result = validate_workbench_payload(WorkbenchRequest(
            module="sales_order",
            form_data={"sales_order_date": "2026-06-10", "customer_id": "CUST-1",
                       "bill_to_address": "B", "ship_to_address": "S",
                       "delivery_date": "2026-06-11", "order_total": "100"},
            item_rows=[{"product": "Widget", "bulk_qty": "1", "bulk_unit_price": "100", "bulk_line_total": "100"}]
        ))
        assert result["export_allowed"] is False
        assert any("product" in item["reason_code"].lower() or "ERP_master" in item["reason_code"]
                   for item in result["blocker_reasons"])

    def test_missing_supplier_id_blocks_purchase_export(self):
        from app.api.v1.routers.forms import WorkbenchRequest, validate_workbench_payload
        result = validate_workbench_payload(WorkbenchRequest(
            module="purchase_order",
            form_data={"po_number": "PO-1", "po_date": "2026-06-10", "supplier_name": "Acme",
                       "bill_to_address": "B", "ship_to_address": "S", "delivery_date": "2026-06-11",
                       "po_grand_total": "100"},
            item_rows=[{"product_id": "PROD-1", "bulk_qty": "1", "bulk_unit_price": "100", "bulk_line_total": "100"}]
        ))
        assert result["export_allowed"] is False
        assert any("supplier" in item["reason_code"].lower() or "ERP_master" in item["reason_code"]
                   for item in result["blocker_reasons"])


class TestTask4Frontend:
    def test_localstorage_fallback_still_visible(self):
        source = Path("frontend/index.html").read_text(encoding="utf-8")
        assert "ocr_erp_master_mappings_cache" in source
        assert "Using local master mapping cache" in source

    def test_auto_fill_response_includes_resolved_and_unresolved_masters(self):
        from app.api.v1.routers.forms import WorkbenchAutoFillRequest, build_workbench_auto_fill
        req = WorkbenchAutoFillRequest(module="sales_order", ocr_result={
            "canonical_fields": {"customer_name": {"value": "Acme Ltd"}},
            "raw_text": "Customer: Acme Ltd",
        })
        result = build_workbench_auto_fill(req, req.ocr_result or {}, None, None)
        assert "unresolved_masters" in result
        assert "resolved_masters" in result
        assert isinstance(result["unresolved_masters"], list)
        assert isinstance(result["resolved_masters"], list)

    def test_erp_import_panel_in_frontend(self):
        source = Path("frontend/index.html").read_text(encoding="utf-8")
        assert "secErpMasterImport" in source
        assert "runErpMasterImport" in source
        assert "erpImport_masterType" in source
        assert "erpImport_dryRun" in source

    def test_erp_import_nav_item_in_frontend(self):
        source = Path("frontend/index.html").read_text(encoding="utf-8")
        assert "navErpImport" in source
        assert "showErpMasterImportPage" in source
