import pytest
import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.v1.routers.forms import normalize_dataset_mappings
from app.api.v1.routers.companies import (
    TrainingDatasetBestMatchRequest, 
    best_match_training_dataset,
    _score_training_dataset
)
from app.models.company import TrainingDataset

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

def test_normalize_canonical_schema():
    raw = [
        {"source_key": "invoice_no", "target_erp_name": "sales_order_no", "canonical_name": "order_number"}
    ]
    normalized = normalize_dataset_mappings(raw)
    assert len(normalized) == 1
    assert normalized[0]["source_key"] == "invoice_no"
    assert normalized[0]["target_erp_name"] == "sales_order_no"
    assert normalized[0]["canonical_name"] == "order_number"

def test_normalize_old_local_storage_format():
    raw = {
        "customer_name": {"label": "buyer_name", "confidence": 0.9, "value": "Acme Corp"}
    }
    normalized = normalize_dataset_mappings(raw)
    assert len(normalized) == 1
    assert normalized[0]["source_key"] == "buyer_name"
    assert normalized[0]["target_erp_name"] == "customer_name"
    assert normalized[0]["sample_value"] == "Acme Corp"
    assert normalized[0]["confidence"] == "0.9"

def test_normalize_object_map_format():
    raw = {"invoice_number": "order_no", "date": "order_date"}
    normalized = normalize_dataset_mappings(raw)
    assert len(normalized) == 2
    targets = [n["target_erp_name"] for n in normalized]
    assert "order_no" in targets
    assert "order_date" in targets

def test_normalize_row_mapping_format():
    raw = [
        {"ocr_key": "qty", "target_field": "quantity"}
    ]
    normalized = normalize_dataset_mappings(raw)
    assert len(normalized) == 1
    assert normalized[0]["target_erp_name"] == "quantity"
    assert normalized[0]["source_key"] == "qty"

def test_normalize_empty_mapping_ignored():
    raw = [
        {"source_key": "", "target_erp_name": "some_field"},
        {"source_key": "none", "target_erp_name": "none"},
        {"source_key": "ocr_key", "target_erp_name": ""}
    ]
    normalized = normalize_dataset_mappings(raw)
    assert len(normalized) == 0

def test_score_best_match_100():
    dataset = TrainingDataset(
        module="sales_order",
        document_type="Sales Invoice",
        company_name="Acme Ltd",
        vendor_name=None,
        customer_name="Buyer One"
    )
    body = TrainingDatasetBestMatchRequest(
        module="sales_order",
        document_type="Sales Invoice",
        company_name="Acme Ltd"
    )
    score, reason, identity = _score_training_dataset(dataset, body)
    assert score == 100
    assert "exact" in reason
    assert identity == "company"

def test_score_best_match_multiple_exact():
    dataset = TrainingDataset(
        module="sales_order",
        document_type="Sales Invoice",
        company_name="Acme Ltd",
        vendor_name=None,
        customer_name="Buyer One"
    )
    body = TrainingDatasetBestMatchRequest(
        module="sales_order",
        document_type="Sales Invoice",
        company_name="Acme Ltd",
        customer_name="Buyer One"
    )
    score, reason, identity = _score_training_dataset(dataset, body)
    assert score == 100
    assert "company" in identity and "customer" in identity

def test_score_doc_type_match_70():
    dataset = TrainingDataset(
        module="sales_order",
        document_type="Sales Invoice",
        company_name="Some Company"
    )
    body = TrainingDatasetBestMatchRequest(
        module="sales_order",
        document_type="Sales Invoice",
        company_name="Other Company"
    )
    score, reason, identity = _score_training_dataset(dataset, body)
    assert score == 70
    assert reason == "module_document_type"

def test_score_module_only_30():
    dataset = TrainingDataset(
        module="sales_order",
        document_type="Sales Invoice"
    )
    body = TrainingDatasetBestMatchRequest(
        module="sales_order",
        document_type="Different Type"
    )
    score, reason, identity = _score_training_dataset(dataset, body)
    assert score == 30
    assert reason == "module_only"

@pytest.mark.asyncio
async def test_best_match_response_structure():
    dataset = TrainingDataset(
        id=str(uuid.uuid4()),
        dataset_name="Test DS",
        module="sales_order",
        document_type="Sales Invoice",
        is_active=True,
        mappings_json=[],
        row_mappings_json=[],
        metadata_json={},
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc)
    )
    body = TrainingDatasetBestMatchRequest(module="sales_order")
    db = MockDB([dataset])
    
    result = await best_match_training_dataset(body, db=db, current_user={})
    assert "dataset" in result
    assert "match_score" in result
    assert "is_partial" in result
    assert result["match_score"] == 30
    assert result["is_partial"] is True
    assert "warning" in result
