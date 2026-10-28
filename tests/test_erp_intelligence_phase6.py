from __future__ import annotations
import uuid
import pytest
from types import SimpleNamespace
from app.services.master_matcher import match_master_data
from app.services.erp_intelligence import check_po_grn_invoice_match, check_tax_gst_validation, build_erp_intelligence_payload
from app.services.source_trace import build_source_traces, calculate_review_summary

class MockDB:
    def __init__(self, scalars_result=None):
        self.scalars_result = scalars_result or []
    
    async def execute(self, query):
        class Result:
            def __init__(self, data):
                self.data = data
            def scalars(self):
                class Scalars:
                    def __init__(self, data):
                        self.data = data
                    def all(self):
                        return self.data
                return Scalars(self.data)
        return Result(self.scalars_result)

# Mocked ERPMasterMapping-like objects
def create_mock_master(master_type, name, erp_id, gst_number=None, email=None, phone=None, address=None):
    return SimpleNamespace(
        master_type=master_type,
        detected_text=name,
        normalized_text=name.lower(),
        display_name=name,
        erp_name=name,
        legal_name=name,
        alias_names="",
        erp_id=erp_id,
        gst_number=gst_number,
        is_active=True,
        org_id=None,
        metadata_json={
            "email": email,
            "phone": phone,
            "address": address
        }
    )

# 1. Test Exact GST / Vendor Master Match
@pytest.mark.asyncio
async def test_exact_gst_vendor_master_match():
    master = create_mock_master("supplier", "CDS Soles Pvt Ltd", "SUP-001", gst_number="33AABCC1234F1Z0")
    db = MockDB([master])
    
    validated_fields = {
        "vendor_name": {"value": "CDS Soles Pvt Ltd"},
        "gst_number": {"value": "33AABCC1234F1Z0"}
    }
    
    res = await match_master_data(db, org_id=None, doc_type="purchase_order", validated_fields=validated_fields, mapped_result={})
    assert res["status"] == "exact_master_match"
    assert res["selected_master_id"] == "SUP-001"
    assert res["review_required"] is False

# 2. Test Ambiguous Master Match Requires Review
@pytest.mark.asyncio
async def test_ambiguous_master_match():
    m1 = create_mock_master("supplier", "Aline Enterprises", "SUP-101")
    m2 = create_mock_master("supplier", "Aline Solutions", "SUP-102")
    db = MockDB([m1, m2])
    
    validated_fields = {
        "vendor_name": {"value": "Aline"}
    }
    
    res = await match_master_data(db, org_id=None, doc_type="purchase_order", validated_fields=validated_fields, mapped_result={})
    print("DEBUG res:", res)
    assert res["status"] == "ambiguous_master_match"
    assert res["review_required"] is True
    assert res["blocking"] is True

# 3. Test No Master Found Warning
@pytest.mark.asyncio
async def test_no_master_found_warning():
    db = MockDB([])
    validated_fields = {
        "vendor_name": {"value": "Unknown Vendor"}
    }
    res = await match_master_data(db, org_id=None, doc_type="purchase_order", validated_fields=validated_fields, mapped_result={})
    assert res["status"] == "no_master_match"
    assert res["review_required"] is True
    assert "No candidate matched" in res["reasons"][0] or "No master mapping" in res["reasons"][0]

# 4. Test PO Number Match
@pytest.mark.asyncio
async def test_po_number_match():
    org_id = uuid.uuid4()
    
    # Pre-existing PO doc
    prev_po = SimpleNamespace(
        job_id=uuid.uuid4(),
        mapped_output={
            "po_number": "PO-999",
            "grand_total": "1000.00",
            "vendor_name": "CDS Soles Pvt Ltd"
        },
        raw_ocr_json={
            "document_type": "purchase_order"
        },
        line_items=[
            {"product": "Sole-A", "quantity": "10", "unit_price": "100.00", "line_total": "1000.00"}
        ],
        job=SimpleNamespace(
            filename="po.pdf",
            ocr_config={"document_type": "purchase_order"}
        )
    )
    
    db = MockDB([prev_po])
    
    validated_fields = {
        "po_number": {"value": "PO-999"},
        "grand_total": {"value": "1000.00"},
        "vendor_name": {"value": "CDS Soles Pvt Ltd"}
    }
    
    res = await check_po_grn_invoice_match(
        db=db,
        org_id=org_id,
        doc_type="invoice",
        validated_fields=validated_fields,
        line_items=[
            {"product": "Sole-A", "quantity": "10", "unit_price": "100.00", "line_total": "1000.00"}
        ]
    )
    
    assert res["status"] == "matched"
    assert res["review_required"] is False
    assert "Matched successfully" in res["reasons"][0]

# 5. Test PO Number Mismatch Warning
@pytest.mark.asyncio
async def test_po_number_mismatch_warning():
    prev_po = SimpleNamespace(
        job_id=uuid.uuid4(),
        mapped_output={
            "po_number": "PO-999",
            "grand_total": "1000.00",
            "vendor_name": "CDS Soles Pvt Ltd"
        },
        raw_ocr_json={
            "document_type": "purchase_order"
        },
        line_items=[],
        job=SimpleNamespace(
            filename="po.pdf",
            ocr_config={"document_type": "purchase_order"}
        )
    )
    
    db = MockDB([prev_po])
    
    validated_fields = {
        "po_number": {"value": "PO-999"},
        "grand_total": {"value": "1500.00"}, # Mismatch
        "vendor_name": {"value": "CDS Soles Pvt Ltd"}
    }
    
    res = await check_po_grn_invoice_match(
        db=db,
        org_id=None,
        doc_type="invoice",
        validated_fields=validated_fields,
        line_items=[]
    )
    
    assert res["status"] == "mismatched"
    assert res["review_required"] is True
    assert any("Grand total mismatch" in r for r in res["reasons"])

# 6. Test Quantity Mismatch Review
@pytest.mark.asyncio
async def test_quantity_mismatch_review():
    prev_po = SimpleNamespace(
        job_id=uuid.uuid4(),
        mapped_output={
            "po_number": "PO-999",
            "grand_total": "1000.00",
            "vendor_name": "CDS Soles Pvt Ltd"
        },
        raw_ocr_json={
            "document_type": "purchase_order"
        },
        line_items=[
            {"product": "Sole-A", "quantity": "10"}
        ],
        job=SimpleNamespace(
            filename="po.pdf",
            ocr_config={"document_type": "purchase_order"}
        )
    )
    
    db = MockDB([prev_po])
    
    validated_fields = {
        "po_number": {"value": "PO-999"},
        "grand_total": {"value": "1000.00"},
        "vendor_name": {"value": "CDS Soles Pvt Ltd"}
    }
    
    # Quantity in invoice (12) exceeds PO (10)
    res = await check_po_grn_invoice_match(
        db=db,
        org_id=None,
        doc_type="invoice",
        validated_fields=validated_fields,
        line_items=[
            {"product": "Sole-A", "quantity": "12"}
        ]
    )
    
    assert res["status"] == "mismatched"
    assert res["review_required"] is True
    assert any("Quantity" in r and "exceeds" in r for r in res["reasons"])

# 7. Test Price Mismatch Review
@pytest.mark.asyncio
async def test_price_mismatch_review():
    prev_po = SimpleNamespace(
        job_id=uuid.uuid4(),
        mapped_output={
            "po_number": "PO-999",
            "grand_total": "1000.00",
            "vendor_name": "CDS Soles Pvt Ltd"
        },
        raw_ocr_json={
            "document_type": "purchase_order"
        },
        line_items=[
            {"product": "Sole-A", "unit_price": "100.00"}
        ],
        job=SimpleNamespace(
            filename="po.pdf",
            ocr_config={"document_type": "purchase_order"}
        )
    )
    
    db = MockDB([prev_po])
    
    validated_fields = {
        "po_number": {"value": "PO-999"},
        "grand_total": {"value": "1000.00"},
        "vendor_name": {"value": "CDS Soles Pvt Ltd"}
    }
    
    # Unit price differs
    res = await check_po_grn_invoice_match(
        db=db,
        org_id=None,
        doc_type="invoice",
        validated_fields=validated_fields,
        line_items=[
            {"product": "Sole-A", "unit_price": "110.00"}
        ]
    )
    
    assert res["status"] == "mismatched"
    assert res["review_required"] is True
    assert any("Unit price" in r and "differs" in r for r in res["reasons"])

# 8. Test Tax Total Validation Pass
def test_tax_total_validation_pass():
    validated_fields = {
        "gst_number": {"value": "33AABCC1234F1Z0"},
        "subtotal": {"value": "1000.00"},
        "cgst": {"value": "90.00"},
        "sgst": {"value": "90.00"},
        "grand_total": {"value": "1180.00"}
    }
    
    res = check_tax_gst_validation(validated_fields, [])
    assert res["status"] == "pass"
    assert len(res["export_blockers"]) == 0

# 9. Test Tax Total Mismatch Blocker
def test_tax_total_mismatch_blocker():
    validated_fields = {
        "gst_number": {"value": "33AABCC1234F1Z0"},
        "subtotal": {"value": "1000.00"},
        "igst": {"value": "180.00"},
        "grand_total": {"value": "1500.00"} # expected is 1180
    }
    
    res = check_tax_gst_validation(validated_fields, [])
    assert res["status"] == "fail"
    assert len(res["export_blockers"]) > 0
    assert any("Grand total validation failure" in b for b in res["export_blockers"])

# 10. Test Duplicate Detection Integration
def test_calculate_review_summary_with_erp():
    dup_det = {
        "status": "exact_duplicate",
        "confidence": 1.0,
        "matched_job_id": "matched_job_id",
        "matched_document_name": "po.pdf",
        "matched_created_at": None,
        "reasons": ["same file checksum"],
        "blocking": True,
        "review_required": True
    }
    
    erp_intel = {
        "master_match": {"status": "no_master_match"},
        "po_grn_invoice_match": {"status": "not_applicable"},
        "tax_validation": {"status": "pass", "warnings": ["test warning"], "export_blockers": []},
        "review_required": True,
        "blocking": False,
        "warnings": ["test warning"],
        "export_blockers": []
    }
    
    summary = calculate_review_summary(
        source_traces={},
        duplicate_detection=dup_det,
        erp_intelligence=erp_intel
    )
    
    assert summary["duplicate_warning"] == "Possible duplicate document detected"
    assert "test warning" in summary["required_review_reasons"]
    assert summary["export_ready"] is False
    assert "duplicate_document" in summary["export_blockers"]

# 11. Test Response Remains Backward Compatible
@pytest.mark.asyncio
async def test_response_backward_compatibility():
    master = create_mock_master("supplier", "CDS Soles Pvt Ltd", "SUP-001")
    db = MockDB([master])
    
    validated_fields = {
        "vendor_name": {"value": "CDS Soles Pvt Ltd"},
        "gst_number": {"value": "33AABCC1234F1Z0"},
        "subtotal": {"value": "1000.00"},
        "grand_total": {"value": "1180.00"},
        "total_tax": {"value": "180.00"}
    }
    
    master_match = await match_master_data(db, None, "purchase_order", validated_fields, {})
    payload = await build_erp_intelligence_payload(
        db=db,
        org_id=None,
        doc_type="purchase_order",
        validated_fields=validated_fields,
        line_items=[],
        master_match=master_match
    )
    
    # Assert return shape exactly matches expected payload
    assert "master_match" in payload
    assert "po_grn_invoice_match" in payload
    assert "tax_validation" in payload
    assert "review_required" in payload
    assert "blocking" in payload
    assert "warnings" in payload
    assert "export_blockers" in payload

# 12. Test Source Traces Include ERP Intelligence
def test_source_traces_include_erp():
    mapped = {
        "supplier_id": {"value": "SUP-001", "confidence": 0.95, "method": "master_match", "source": "master_match"}
    }
    validated = {
        "supplier_id": {"value": "SUP-001", "confidence": 0.95, "method": "master_match", "source": "master_match"}
    }
    validation_report = {"errors": [], "warnings": []}
    
    erp_intel = {
        "master_match": {
            "party_type": "supplier",
            "status": "exact_master_match",
            "confidence": 0.98,
            "reasons": ["GST matched exactly"],
            "review_required": False
        }
    }
    
    traces = build_source_traces(mapped, validated, validation_report, extra_meta={"erp_intelligence": erp_intel})
    assert "supplier_id" in traces
    trace = traces["supplier_id"]
    assert trace["erp_intelligence"] is not None
    assert trace["erp_intelligence"]["source_type"] == "erp_master"
    assert trace["erp_intelligence"]["confidence"] == 0.98
    assert trace["erp_intelligence"]["validation_status"] == "valid"
    assert trace["source_type"] == "erp_master"
    assert trace["source_engine"] == "erp_master_mappings"
