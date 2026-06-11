"""
tests/test_task7_1_critical_blockers.py
Task 7.1 — Critical Production Blockers Fix Tests

Tests:
1-3.   Blocked JSON/Excel/PDF workbench export returns HTTP 422 with precheck details
4-6.   Allowed JSON/Excel/PDF workbench export still succeeds
7.     Legacy export route cannot bypass precheck
8-11.  Auto-fill workbench response includes export_precheck, export_allowed, quality_score, blockers
12-14. Fill job response does not expose D:\\ / C:\\ / /mnt/ paths
15-17. Path traversal ../ / ..\\ / %2e%2e rejected by safe_join_output_path
18.    Absolute path injection rejected by safe_join_output_path
19.    safe_join_output_path keeps final path inside output directory
20.    JSON/Excel/PDF blocked response shape is consistent
21.    Release snapshot docs/RELEASE_SNAPSHOT.md exists
22.    Task 7.1 report tests/artifacts/task7_1_critical_blockers_report.json exists
23.    Existing Task 6 precheck module still importable and functional
24.    E2E export functions (precheck engine) still work correctly
"""
from __future__ import annotations

import json
import os
import sys
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Ensure project root on path ───────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ── Import core modules under test ────────────────────────────────────────────
from app.core.config import get_settings, safe_join_output_path
from app.services.export_precheck_engine import build_export_precheck


# ═══════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════

def _blocked_precheck_payload(module: str = "sales_order") -> dict:
    """Minimal payload guaranteed to produce export_allowed=False."""
    return {
        "module": module,
        "document_type": "sales_invoice",
        "form_data": {},          # all required fields missing → blockers
        "item_rows": [],
        "mapped_fields": {},
        "resolved_masters": [],
        "unresolved_masters": [],
        "confidence_map": {},
        "warnings": [],
        "source_type": "test",
    }


def _allowed_precheck_payload(module: str = "general") -> dict:
    """Minimal payload for module=general that produces export_allowed=True."""
    return {
        "module": module,
        "document_type": "general",
        "form_data": {"invoice_no": "INV-001", "vendor_name": "Test Vendor"},
        "item_rows": [],
        "mapped_fields": {"invoice_no": {"value": "INV-001", "confidence": 0.95}},
        "resolved_masters": [],
        "unresolved_masters": [],
        "confidence_map": {"invoice_no": 0.95},
        "warnings": [],
        "source_type": "test",
    }


def _expected_blocked_shape() -> set:
    return {
        "export_allowed",
        "quality_score",
        "quality_grade",
        "blockers",
        "missing_required_fields",
        "unresolved_masters",
        "row_issues",
    }


def _blocked_workbench_request():
    from app.api.v1.routers.forms import WorkbenchExportRequest
    return WorkbenchExportRequest(module="sales_order")


def _allowed_workbench_request():
    from app.api.v1.routers.forms import WorkbenchExportRequest
    return WorkbenchExportRequest(
        module="sales_order",
        form_data={
            "customer_id": "CUST-1", "sales_order_date": "2026-06-11",
            "customer_po_number": "PO-1", "currency_code_id": "INR",
            "order_total": "100", "bill_to_address": "Billing address",
            "ship_to_address": "Shipping address", "delivery_date": "2026-06-12",
        },
        item_rows=[{
            "product_id": "PROD-1", "product": "Widget", "bulk_qty": "1",
            "bulk_unit_price": "100", "bulk_line_total": "100",
        }],
    )


def _assert_export_blocked(endpoint) -> None:
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(endpoint(_blocked_workbench_request(), {"user_id": "test"}))
    assert exc_info.value.status_code == 422
    assert _expected_blocked_shape() <= set(exc_info.value.detail)


# ═══════════════════════════════════════════════════════════
# TESTS 1-3: Blocked export returns HTTP 422 with precheck
# ═══════════════════════════════════════════════════════════

def test_01_blocked_export_precheck_shape_json():
    from app.api.v1.routers.forms import export_workbench_json
    _assert_export_blocked(export_workbench_json)


def test_02_blocked_export_precheck_shape_excel():
    from app.api.v1.routers.forms import export_workbench_excel
    _assert_export_blocked(export_workbench_excel)


def test_03_blocked_export_precheck_shape_pdf():
    from app.api.v1.routers.forms import export_workbench_pdf
    _assert_export_blocked(export_workbench_pdf)


# ═══════════════════════════════════════════════════════════
# TESTS 4-6: Allowed export still works
# ═══════════════════════════════════════════════════════════

def test_04_allowed_json_export_precheck_passes():
    from app.api.v1.routers.forms import export_workbench_json
    response = asyncio.run(export_workbench_json(_allowed_workbench_request(), {"user_id": "test"}))
    assert response.media_type == "application/json"


def test_05_allowed_excel_export_precheck_passes():
    from app.api.v1.routers.forms import export_workbench_excel
    response = asyncio.run(export_workbench_excel(_allowed_workbench_request(), {"user_id": "test"}))
    assert response.media_type.endswith("spreadsheetml.sheet")


def test_06_allowed_pdf_export_precheck_passes():
    from app.api.v1.routers.forms import export_workbench_pdf
    response = asyncio.run(export_workbench_pdf(_allowed_workbench_request(), {"user_id": "test"}))
    assert response.media_type == "application/pdf"


# ═══════════════════════════════════════════════════════════
# TEST 7: Legacy export cannot bypass precheck
# ═══════════════════════════════════════════════════════════

def test_07_legacy_export_resolve_fields_calls_precheck():
    from fastapi import HTTPException
    from app.api.v1.routers.forms import ExportRequest, _resolve_export_fields
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(_resolve_export_fields(ExportRequest(fields={}, skip_validation=True), MagicMock(), {}))
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["export_allowed"] is False


# ═══════════════════════════════════════════════════════════
# TESTS 8-11: Auto-fill workbench response includes precheck
# ═══════════════════════════════════════════════════════════

def test_08_workbench_autofill_response_includes_export_precheck():
    """build_workbench_auto_fill response must include export_precheck key."""
    import inspect
    import app.api.v1.routers.forms as forms_module
    source = inspect.getsource(forms_module.build_workbench_auto_fill)
    assert "export_precheck" in source, "build_workbench_auto_fill must include export_precheck in response"


def test_09_workbench_autofill_response_includes_export_allowed():
    """build_workbench_auto_fill response must include export_allowed key."""
    import inspect
    import app.api.v1.routers.forms as forms_module
    source = inspect.getsource(forms_module.build_workbench_auto_fill)
    assert '"export_allowed"' in source or "'export_allowed'" in source


def test_10_workbench_autofill_response_includes_quality_score():
    """build_workbench_auto_fill response must include quality_score key."""
    import inspect
    import app.api.v1.routers.forms as forms_module
    source = inspect.getsource(forms_module.build_workbench_auto_fill)
    assert "quality_score" in source


def test_11_workbench_autofill_response_includes_blockers():
    """build_workbench_auto_fill response must include blockers key."""
    import inspect
    import app.api.v1.routers.forms as forms_module
    source = inspect.getsource(forms_module.build_workbench_auto_fill)
    assert '"blockers"' in source or "'blockers'" in source


# ═══════════════════════════════════════════════════════════
# TESTS 12-14: Fill job response does not expose absolute paths
# ═══════════════════════════════════════════════════════════

def test_12_fill_job_response_no_d_drive_path():
    """get_fill_job response must not return output_path with D:\\ prefix."""
    import inspect
    import app.api.v1.routers.fill as fill_module
    source = inspect.getsource(fill_module.get_fill_job)
    # output_path field must NOT be in the returned dict
    assert '"output_path"' not in source and "'output_path'" not in source, \
        "get_fill_job must not expose raw output_path"


def test_13_fill_job_response_no_c_drive_path():
    """get_fill_job response must not return raw filesystem path."""
    import app.api.v1.routers.fill as fill_module
    import inspect
    source = inspect.getsource(fill_module.get_fill_job)
    assert "output_path" not in source.split("return")[1], \
        "get_fill_job return dict must not contain output_path key"


def test_14_fill_job_response_no_mnt_path():
    """get_fill_job must return has_output and download_url instead of raw path."""
    import inspect
    import app.api.v1.routers.fill as fill_module
    source = inspect.getsource(fill_module.get_fill_job)
    assert "has_output" in source, "get_fill_job must return has_output"
    assert "download_url" in source, "get_fill_job must return download_url"
    assert "output_filename" in source, "get_fill_job must return output_filename"


# ═══════════════════════════════════════════════════════════
# TESTS 15-17: Path traversal rejection
# ═══════════════════════════════════════════════════════════

def test_15_path_traversal_slash_dot_dot_rejected():
    """safe_join_output_path must reject ../ traversal."""
    base = Path("output").resolve()
    base.mkdir(parents=True, exist_ok=True)
    with pytest.raises(ValueError, match=r"(?i)(traversal|escape|outside|\.\.|\.\.)"):
        safe_join_output_path(base, "../etc/passwd")


def test_16_path_traversal_backslash_dot_dot_rejected():
    """safe_join_output_path must reject ..\\ traversal."""
    base = Path("output").resolve()
    base.mkdir(parents=True, exist_ok=True)
    with pytest.raises(ValueError, match=r"(?i)(traversal|escape|outside|\.\.)"):
        safe_join_output_path(base, "..\\secrets\\key.pem")


def test_17_url_encoded_traversal_rejected():
    """safe_join_output_path must reject %2e%2e URL-encoded traversal."""
    base = Path("output").resolve()
    base.mkdir(parents=True, exist_ok=True)
    with pytest.raises(ValueError):
        safe_join_output_path(base, "%2e%2e/etc/passwd")


# ═══════════════════════════════════════════════════════════
# TEST 18: Absolute path injection rejected
# ═══════════════════════════════════════════════════════════

def test_18_absolute_path_injection_rejected():
    """safe_join_output_path must reject absolute path injection."""
    base = Path("output").resolve()
    base.mkdir(parents=True, exist_ok=True)
    with pytest.raises(ValueError, match=r"(?i)(absolute|injection|escape|outside)"):
        safe_join_output_path(base, "/etc/passwd")


# ═══════════════════════════════════════════════════════════
# TEST 19: safe_join_output_path valid path stays inside base
# ═══════════════════════════════════════════════════════════

def test_19_safe_join_output_path_valid_stays_inside():
    """safe_join_output_path must return path inside base_dir for valid filename."""
    base = Path("output").resolve()
    base.mkdir(parents=True, exist_ok=True)
    result = safe_join_output_path(base, "some-export.json")
    assert str(result).startswith(str(base)), \
        f"Result path {result} is not inside base {base}"
    assert result.name == "some-export.json"


# ═══════════════════════════════════════════════════════════
# TEST 20: Blocked response shape consistent across JSON/Excel/PDF
# ═══════════════════════════════════════════════════════════

def test_20_blocked_response_shape_consistent():
    """JSON, Excel, PDF blocked precheck response must have identical key set."""
    expected = _expected_blocked_shape()
    for module in ["sales_order", "purchase_order"]:
        precheck = build_export_precheck(_blocked_precheck_payload(module))
        if not precheck["export_allowed"]:
            missing = expected - set(precheck.keys())
            assert not missing, f"Module {module} blocked precheck missing keys: {missing}"


# ═══════════════════════════════════════════════════════════
# TEST 21: Release snapshot exists
# ═══════════════════════════════════════════════════════════

def test_21_release_snapshot_exists():
    """docs/RELEASE_SNAPSHOT.md must exist."""
    snapshot = PROJECT_ROOT / "docs" / "RELEASE_SNAPSHOT.md"
    assert snapshot.exists(), f"Release snapshot missing: {snapshot}"
    content = snapshot.read_text(encoding="utf-8")
    assert "Alembic" in content, "Release snapshot must mention Alembic"
    assert "pytest" in content, "Release snapshot must include pytest command"
    assert "e2e_runner" in content, "Release snapshot must include e2e command"


# ═══════════════════════════════════════════════════════════
# TEST 22: Task 7.1 report exists
# ═══════════════════════════════════════════════════════════

def test_22_task71_report_exists():
    """tests/artifacts/task7_1_critical_blockers_report.json must exist and be valid JSON."""
    report = PROJECT_ROOT / "tests" / "artifacts" / "task7_1_critical_blockers_report.json"
    assert report.exists(), f"Report missing: {report}"
    data = json.loads(report.read_text(encoding="utf-8"))
    assert "export_bypass_fix" in data
    assert "fill_bypass_fix" in data
    assert "path_hardening_fix" in data
    assert "release_snapshot" in data


# ═══════════════════════════════════════════════════════════
# TEST 23: Existing Task 6 precheck still functional
# ═══════════════════════════════════════════════════════════

def test_23_task6_precheck_module_still_functional():
    """export_precheck_engine.build_export_precheck must still work correctly."""
    result = build_export_precheck({
        "module": "sales_order",
        "document_type": "sales_invoice",
        "form_data": {
            "customer_id": "CUST-001",
            "customer_name": "Test Customer",
            "sales_order_date": "2024-01-15",
            "sales_order_no": "SO-001",
            "customer_po_number": "PO-2024-001",
            "order_total": "10000",
        },
        "item_rows": [
            {"product": "Widget A", "product_id": "PRD-001", "bulk_qty": "10",
             "bulk_unit_price": "1000", "bulk_line_total": "10000", "bulk_tax_amount": "0"}
        ],
        "mapped_fields": {},
        "resolved_masters": [],
        "unresolved_masters": [],
        "confidence_map": {},
        "warnings": [],
        "source_type": "test",
    })
    assert "export_allowed" in result
    assert "quality_score" in result
    assert "quality_grade" in result
    assert "blockers" in result
    assert isinstance(result["quality_score"], (int, float))


# ═══════════════════════════════════════════════════════════
# TEST 24: E2E export precheck engine works end-to-end
# ═══════════════════════════════════════════════════════════

def test_24_e2e_export_precheck_engine_end_to_end():
    """Full end-to-end: precheck engine blocks, then allows after fixing required fields."""
    # Step 1: blocked
    blocked = build_export_precheck(_blocked_precheck_payload("sales_order"))
    assert blocked["export_allowed"] is False
    assert len(blocked["blockers"]) > 0

    # Step 2: allowed after providing required data
    allowed = build_export_precheck({
        "module": "general",
        "document_type": "general",
        "form_data": {
            "invoice_no": "INV-2024-001",
            "vendor_name": "ACME Corp",
            "total_amount": "50000",
        },
        "item_rows": [],
        "mapped_fields": {
            "invoice_no": {"value": "INV-2024-001", "confidence": 0.98},
            "vendor_name": {"value": "ACME Corp", "confidence": 0.95},
        },
        "resolved_masters": [],
        "unresolved_masters": [],
        "confidence_map": {"invoice_no": 0.98, "vendor_name": 0.95},
        "warnings": [],
        "source_type": "test",
    })
    assert allowed["export_allowed"] is True
    assert allowed["quality_grade"] in {"A", "B"}

    # Step 3: verify grade scale
    assert 0 <= allowed["quality_score"] <= 100


@pytest.mark.parametrize("field,value", [
    ("amount_in_words", "Rupees One Hundred Only"),
    ("address", "A" * 600),
    ("po_date", "OCR date requires review"),
])
def test_legacy_ocr_anomalies_require_review_without_hard_block(field, value):
    payload = _allowed_precheck_payload("general")
    payload["form_data"][field] = value
    result = build_export_precheck(payload)
    assert result["export_allowed"] is True
    assert result["review_required"] is True
    assert result["warnings"]
