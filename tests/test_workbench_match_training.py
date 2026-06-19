import pytest
import uuid
from unittest.mock import AsyncMock, MagicMock
from fastapi.testclient import TestClient
from fastapi import FastAPI

from app.api.v1.routers.forms import WorkbenchAutoFillRequest, build_workbench_auto_fill, router
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.company import TrainingDataset

# Initialize FastAPI app for testing
app = FastAPI()
app.include_router(router)

def mock_get_db():
    db = MagicMock()
    db.execute = AsyncMock()
    return db

@pytest.fixture
def mock_operator_user():
    return {"user_id": str(uuid.uuid4()), "role": "operator", "org_id": None}

@pytest.fixture
def client(mock_operator_user):
    # Override authentication and db dependencies
    app.dependency_overrides[get_db] = mock_get_db
    app.dependency_overrides[get_current_user] = lambda: mock_operator_user
    yield TestClient(app)
    app.dependency_overrides = {}


def test_workbench_match_training_endpoint(client, monkeypatch):
    """Test that the /forms/workbench/match-training endpoint successfully matches a training dataset."""
    fake_dataset = TrainingDataset(
        id=uuid.uuid4(),
        dataset_name="Test Template",
        module="sales_order",
        document_type="sales_invoice",
        mappings_json=[{"source_key": "inv_no", "target_erp_name": "invoice_number"}],
        row_mappings_json=[]
    )
    
    mock_match = {
        "dataset": fake_dataset,
        "score": 0.85,
        "match_reason": "similarity_auto",
        "apply_mode": "auto",
        "dataset_id": str(fake_dataset.id),
        "dataset_name": fake_dataset.dataset_name,
    }
    
    # Monkeypatch the match_training_dataset service call
    async def mock_match_training(*args, **kwargs):
        return mock_match

    monkeypatch.setattr(
        "app.services.training_template_engine.match_training_dataset",
        mock_match_training
    )

    payload = {
        "module": "sales_order",
        "raw_text": "TAX INVOICE ABC LTD INVOICE NO: 123",
        "document_type": "sales_invoice",
        "company_name": "ABC LTD"
    }

    resp = client.post("/forms/workbench/match-training", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["dataset_id"] == str(fake_dataset.id)
    assert data["dataset_name"] == "Test Template"
    assert data["score"] == 0.85
    assert data["apply_mode"] == "auto"


def test_workbench_auto_fill_with_training_match(client, monkeypatch):
    """Test that the /forms/workbench/auto-fill endpoint auto-matches a dataset when dataset_id is not specified."""
    fake_dataset = TrainingDataset(
        id=uuid.uuid4(),
        dataset_name="Auto Matched Template",
        module="sales_order",
        document_type="sales_invoice",
        mappings_json=[{"source_key": "inv_no", "target_erp_name": "invoice_number"}],
        row_mappings_json=[]
    )
    
    mock_match = {
        "dataset": fake_dataset,
        "score": 0.90,
        "match_reason": "similarity_auto",
        "apply_mode": "auto",
        "dataset_id": str(fake_dataset.id),
        "dataset_name": fake_dataset.dataset_name,
    }

    async def mock_match_training(*args, **kwargs):
        return mock_match

    monkeypatch.setattr(
        "app.services.training_template_engine.match_training_dataset",
        mock_match_training
    )

    payload = {
        "module": "sales_order",
        "ocr_result": {
            "raw_text": "TAX INVOICE ABC LTD INVOICE NO: 123",
            "document_type": "sales_invoice",
            "company_name": "ABC LTD"
        }
    }

    resp = client.post("/forms/workbench/auto-fill", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert "training_match" in data
    assert data["training_match"]["dataset_id"] == str(fake_dataset.id)
    assert data["training_match"]["score"] == 0.90
    assert data["dataset_name"] == "Auto Matched Template"


def test_workbench_auto_fill_rejects_group_identifier_from_training():
    req = WorkbenchAutoFillRequest(
        module="sales_order",
        ocr_result={
            "document_type": "sales_invoice",
            "fields": {"order_number": "GROUP"},
        },
    )
    dataset = {
        "mappings_json": [
            {"source_key": "order_number", "target_erp_name": "sales_order_no"},
            {"source_key": "order_number", "target_erp_name": "customer_po_number"},
        ],
        "row_mappings_json": [],
    }

    result = build_workbench_auto_fill(req, req.ocr_result, dataset=dataset)

    assert "sales_order_no" not in result["form_data"]
    assert "customer_po_number" not in result["form_data"]
    assert any(item["reason"] == "label_only_or_suspicious_value" for item in result["skipped_keys"])


def test_workbench_purchase_po_date_requires_po_label_evidence():
    req = WorkbenchAutoFillRequest(
        module="purchase_order",
        ocr_result={
            "document_type": "purchase_order",
            "fields": {
                "invoice_date": "01/02/2026",
                "date": "02/02/2026",
                "dated": "03/02/2026",
                "delivery_date": "04/02/2026",
                "po_date": "05/02/2026",
            },
        },
    )

    result = build_workbench_auto_fill(req, req.ocr_result, dataset={"mappings_json": [], "row_mappings_json": []})

    assert result["form_data"]["po_date"] == "05/02/2026"
    assert result["form_data"]["delivery_date"] == "04/02/2026"
    rejected = {(item["normalized_key"], item["reason"]) for item in result["skipped_keys"]}
    assert ("invoice_date", "no_alias_match") in rejected or "invoice_date" not in result["form_data"]


def test_workbench_purchase_rejects_generic_date_for_po_date_dataset_mapping():
    req = WorkbenchAutoFillRequest(
        module="purchase_order",
        ocr_result={
            "document_type": "purchase_order",
            "fields": {"date": "02/02/2026", "Dated": "03/02/2026"},
        },
    )
    dataset = {
        "mappings_json": [
            {"source_key": "date", "source_label": "date", "target_erp_name": "po_date"},
            {"source_key": "Dated", "source_label": "Dated", "target_erp_name": "po_date"},
        ],
        "row_mappings_json": [],
    }

    result = build_workbench_auto_fill(req, req.ocr_result, dataset=dataset)

    assert "po_date" not in result["form_data"]
    assert any(item["reason"] == "label_evidence_mismatch" for item in result["skipped_keys"])


def test_workbench_rejects_supplier_customer_without_name_label_evidence():
    req = WorkbenchAutoFillRequest(
        module="purchase_order",
        ocr_result={
            "document_type": "purchase_order",
            "fields": {
                "made_from": "COW LEATHER",
                "country_of_origin": "INDIA",
                "contact_person": "John Smith",
                "Supplier": "Valid Vendor Ltd",
            },
        },
    )
    dataset = {
        "mappings_json": [
            {"source_key": "made_from", "target_erp_name": "supplier_name"},
            {"source_key": "country_of_origin", "target_erp_name": "supplier_name"},
            {"source_key": "contact_person", "target_erp_name": "supplier_name"},
            {"source_key": "Supplier", "target_erp_name": "supplier_name"},
        ],
        "row_mappings_json": [],
    }

    result = build_workbench_auto_fill(req, req.ocr_result, dataset=dataset)

    assert result["form_data"]["supplier_name"] == "Valid Vendor Ltd"
    assert not any(m["ocr_value"] in {"COW LEATHER", "INDIA", "John Smith"} and m["target_erp_name"] == "supplier_name" for m in result["matched_targets"])


def test_workbench_rows_preserve_sales_purchase_item_values():
    sales_req = WorkbenchAutoFillRequest(
        module="sales_order",
        ocr_result={
            "document_type": "sales_invoice",
            "item_rows": [{
                "product": "Finished Shoe",
                "product_id": "P-100",
                "description": "Finished Shoe Black",
                "uom": "PAIRS",
                "qty": "10",
                "rate": "25.50",
                "discount_amount": "5",
                "tax_amount": "2.50",
                "amount": "252.50",
            }],
        },
    )
    purchase_req = WorkbenchAutoFillRequest(
        module="purchase_order",
        ocr_result={
            "document_type": "purchase_order",
            "item_rows": [{
                "product": "Leather Upper",
                "product_id": "RM-200",
                "description": "Leather Upper Brown",
                "uom": "PCS",
                "qty": "4",
                "unit_price": "100",
                "discount_amount": "10",
                "tax_amount": "8",
                "line_total": "398",
            }],
        },
    )

    sales = build_workbench_auto_fill(sales_req, sales_req.ocr_result)
    purchase = build_workbench_auto_fill(purchase_req, purchase_req.ocr_result)

    assert sales["item_rows"][0]["product"] == "Finished Shoe"
    assert sales["item_rows"][0]["bulk_qty"] == "10"
    assert sales["item_rows"][0]["bulk_unit_price"] == "25.50"
    assert sales["item_rows"][0]["bulk_line_total"] == "252.50"
    assert purchase["item_rows"][0]["product"] == "Leather Upper"
    assert purchase["item_rows"][0]["bulk_qty"] == "4"
    assert purchase["item_rows"][0]["bulk_unit_price"] == "100"
    assert purchase["item_rows"][0]["bulk_line_total"] == "398"


def test_workbench_auto_fill_uses_item_order_no_for_customer_po_number():
    req = WorkbenchAutoFillRequest(
        module="sales_order",
        ocr_result={
            "document_type": "sales_invoice",
            "fields": {"order_number": "GROUP"},
            "item_rows": [
                {"order_no": "03042026", "group": "GROUP", "article": "SHOE", "qty": "4325"},
                {"order_no": "03042026", "group": "GROUP", "article": "SHOE", "qty": "780"},
            ],
        },
    )
    dataset = {
        "mappings_json": [{"source_key": "order_number", "target_erp_name": "customer_po_number"}],
        "row_mappings_json": [],
    }

    result = build_workbench_auto_fill(req, req.ocr_result, dataset=dataset)

    assert result["form_data"]["customer_po_number"] == "03042026"
    assert any(item["source"] == "row_header_fallback" for item in result["matched_targets"])


def test_workbench_auto_fill_reusable_fallback_and_dynamic_protection():
    req = WorkbenchAutoFillRequest(
        module="sales",
        ocr_result={
            "document_type": "sales",
            "mapped_fields": {
                "customer_name": {"value": "Acme Corp", "confidence": 0.95},
            },
        },
    )
    dataset = {
        "mappings_json": [
            {"source_key": "invoice_no", "target_erp_name": "sales_order_no", "sample_value": "SO-OLD"},
            {"source_key": "salesperson", "target_erp_name": "salesperson_id", "sample_value": "SALES-REP-1"},
            {"source_key": "payment_term", "target_erp_name": "ar_payment_term_id", "sample_value": "NET45"},
        ],
        "row_mappings_json": [],
    }

    result = build_workbench_auto_fill(req, req.ocr_result, dataset=dataset)
    form_data = result["form_data"]

    # 1. Reusable fallback fields should be filled
    assert form_data["salesperson_id"] == "SALES-REP-1"
    assert form_data["ar_payment_term_id"] == "NET45"
    
    # 2. Reusable fallback trace fields should verify training_dataset source
    traces = {t["field"]: t for t in result["field_level_trace"]}
    assert traces["salesperson_id"]["applied_skipped"] == "applied"
    assert traces["salesperson_id"]["source"] == "training_dataset"
    assert traces["salesperson_id"]["value_source"] == "training_dataset"

    # 3. Dynamic fields should be protected (remain empty, not fallback to template values)
    assert "sales_order_no" not in form_data
    assert traces["sales_order_no"]["applied_skipped"] == "skipped"


def test_workbench_auto_fill_ignores_low_confidence_match(client, monkeypatch):
    """Test that the /forms/workbench/auto-fill endpoint ignores a training match with score below THRESHOLD_SUGGEST (0.70)."""
    fake_dataset = TrainingDataset(
        id=uuid.uuid4(),
        dataset_name="Low Match Template",
        module="sales_order",
        document_type="sales_invoice",
        mappings_json=[{"source_key": "inv_no", "target_erp_name": "invoice_number"}],
        row_mappings_json=[]
    )
    
    mock_match = {
        "dataset": fake_dataset,
        "score": 0.65, # Below 0.70
        "match_reason": "similarity_suggest",
        "apply_mode": "suggest",
        "dataset_id": str(fake_dataset.id),
        "dataset_name": fake_dataset.dataset_name,
    }

    async def mock_match_training(*args, **kwargs):
        return mock_match

    monkeypatch.setattr(
        "app.services.training_template_engine.match_training_dataset",
        mock_match_training
    )

    payload = {
        "module": "sales_order",
        "ocr_result": {
            "raw_text": "SOME OTHER TEXT",
            "document_type": "sales_invoice",
            "company_name": "ABC LTD"
        }
    }

    resp = client.post("/forms/workbench/auto-fill", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("training_match") is None
    assert data.get("dataset_name") is None

