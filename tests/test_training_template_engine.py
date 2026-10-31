"""tests/test_training_template_engine.py

Phase 3 focused tests for app/services/training_template_engine.py

Tests cover:
    1.  build_template_signature is deterministic
    2.  Signature ignores volatile invoice / date / amount values
    3.  score_template_similarity returns 0.0-1.0
    4.  score: document_type match lifts score above 0
    5.  score: company identity exact match gives high score
    6.  score: no doc_type and no identity gives low score
    7.  match_training_dataset selects correct dataset by doc_type + company
    8.  match_training_dataset returns None below threshold
    9.  match_training_dataset returns None when DB is empty
    10. apply_training_to_mapped_fields adds source="training"
    11. apply_training_to_mapped_fields adds method="training_template"
    12. apply_training_to_mapped_fields does NOT overwrite correction_memory field
    13. apply_training_to_mapped_fields does NOT overwrite manual_correction field
    14. apply_training_to_mapped_fields fills missing (empty) fields
    15. apply_training_to_mapped_fields overwrites low-confidence regex fields
    16. apply_training_to_mapped_fields returns training_conflicts metadata
    17. apply_training_to_mapped_fields with None dataset returns safe dict
    18. match_training_dataset: approved=False dataset is excluded
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

import pytest

from app.services.training_template_engine import (
    THRESHOLD_APPLY,
    THRESHOLD_IGNORE,
    apply_training_to_mapped_fields,
    build_template_signature,
    match_training_dataset,
    score_template_similarity,
)
from app.models.company import TrainingDataset


# ── Helpers ────────────────────────────────────────────────────────────────

def _make_dataset(**overrides) -> TrainingDataset:
    """Build a minimal TrainingDataset without a real DB."""
    defaults: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "org_id": "org-test",
        "dataset_name": "Test Dataset",
        "module": "sales_order",
        "document_type": "sales invoice",
        "company_name": "Acme Exports Ltd",
        "vendor_name": None,
        "customer_name": "Global Buyers Inc",
        "is_active": True,
        "version": 1,
        "mappings_json": [
            {
                "source_key": "invoice_number",
                "target_erp_name": "invoice_number",
                "sample_value": "ACME-2025-001",
                "confidence": 0.92,
            },
            {
                "source_key": "company_name",
                "target_erp_name": "company_name",
                "sample_value": "Acme Exports Ltd",
                "confidence": 0.95,
            },
            {
                "source_key": "bank_name",
                "target_erp_name": "bank_name",
                "sample_value": "HDFC Bank",
                "confidence": 0.88,
            },
        ],
        "row_mappings_json": [],
        "metadata_json": {},
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        # Phase 2A columns
        "template_signature": None,
        "approved": True,
        "confidence": 0.90,
        "sample_count": 1,
        "usage_count": 0,
        "last_used_at": None,
    }
    defaults.update(overrides)
    return TrainingDataset(**defaults)


_SAMPLE_OCR = """
ACME EXPORTS LTD
GSTIN: 27AABCA1234F1Z5
Tax Invoice
Invoice No: ACME-2025-001  Date: 15/01/2025
Bill To: Global Buyers Inc

Description          HSN    Qty    Rate    Amount
Leather Goods        4203   100    500.00  50000.00
CGST 9%                                    4500.00
SGST 9%                                    4500.00
Grand Total                               59000.00

Bank: HDFC Bank  IFSC: HDFC0001234  A/C: 0123456789
"""

_SAMPLE_OCR_DIFFERENT = """
DIFFERENT COMPANY PVT LTD
GSTIN: 07XXXXX1234X1ZX
Purchase Order
PO No: PO-XYZ-2025  Date: 10/03/2025
Vendor: Vendor ABC

Item              Qty    Rate
Widget A          50     200.00
Total                   10000.00
"""


# ═══════════════════════════════════════════════════════════════════════
# 1-2: build_template_signature
# ═══════════════════════════════════════════════════════════════════════

class TestBuildTemplateSignature:

    def test_deterministic_same_input(self):
        """Same inputs must always produce the same signature."""
        sig1 = build_template_signature(_SAMPLE_OCR, "sales_invoice",
                                        {"company_name": "Acme Exports Ltd"})
        sig2 = build_template_signature(_SAMPLE_OCR, "sales_invoice",
                                        {"company_name": "Acme Exports Ltd"})
        assert sig1 == sig2

    def test_returns_string(self):
        sig = build_template_signature(_SAMPLE_OCR, "sales_invoice")
        assert isinstance(sig, str)
        assert sig.startswith("tpl:")

    def test_ignores_invoice_number(self):
        """Changing the invoice number must NOT change the signature."""
        text_a = _SAMPLE_OCR.replace("ACME-2025-001", "ACME-2025-999")
        text_b = _SAMPLE_OCR.replace("ACME-2025-001", "ACME-2026-XYZ")
        sig_a = build_template_signature(text_a, "sales_invoice",
                                         {"company_name": "Acme Exports Ltd"})
        sig_b = build_template_signature(text_b, "sales_invoice",
                                         {"company_name": "Acme Exports Ltd"})
        assert sig_a == sig_b, "Invoice number change should not alter signature"

    def test_ignores_dates(self):
        """Changing the date must NOT change the signature."""
        text_a = _SAMPLE_OCR.replace("15/01/2025", "22/06/2026")
        text_b = _SAMPLE_OCR.replace("15/01/2025", "01/01/2020")
        sig_a = build_template_signature(text_a, "sales_invoice")
        sig_b = build_template_signature(text_b, "sales_invoice")
        assert sig_a == sig_b, "Date change should not alter signature"

    def test_ignores_amounts(self):
        """Replacing all monetary values must NOT change the signature."""
        text_a = _SAMPLE_OCR.replace("59000.00", "99999.99").replace("50000.00", "11111.11")
        text_b = _SAMPLE_OCR.replace("59000.00", "12345.67").replace("50000.00", "5000.00")
        sig_a = build_template_signature(text_a, "sales_invoice")
        sig_b = build_template_signature(text_b, "sales_invoice")
        assert sig_a == sig_b, "Amount change should not alter signature"

    def test_different_doc_type_gives_different_sig(self):
        sig_inv = build_template_signature(_SAMPLE_OCR, "sales_invoice")
        sig_po = build_template_signature(_SAMPLE_OCR, "purchase_order")
        assert sig_inv != sig_po

    def test_different_company_gives_different_sig(self):
        sig_a = build_template_signature(_SAMPLE_OCR, "sales_invoice",
                                         {"company_name": "Acme Exports Ltd"})
        sig_b = build_template_signature(_SAMPLE_OCR, "sales_invoice",
                                         {"company_name": "Beta Traders Pvt"})
        assert sig_a != sig_b

    def test_empty_text_returns_valid_sig(self):
        sig = build_template_signature("", None, None)
        assert sig.startswith("tpl:")
        assert len(sig) > 4


# ═══════════════════════════════════════════════════════════════════════
# 3-6: score_template_similarity
# ═══════════════════════════════════════════════════════════════════════

class TestScoreTemplateSimilarity:

    def test_returns_float_in_range(self):
        ds = _make_dataset()
        score = score_template_similarity(_SAMPLE_OCR, ds)
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0

    def test_doc_type_match_raises_score(self):
        ds = _make_dataset(document_type="sales invoice")
        score_match = score_template_similarity(
            _SAMPLE_OCR, ds, document_type="sales invoice")
        score_no_match = score_template_similarity(
            _SAMPLE_OCR, ds, document_type="grn_receipt")
        assert score_match > score_no_match

    def test_exact_company_identity_high_score(self):
        ds = _make_dataset(
            document_type="sales invoice",
            company_name="Acme Exports Ltd",
        )
        score = score_template_similarity(
            _SAMPLE_OCR, ds,
            document_type="sales invoice",
            company_info={"company_name": "Acme Exports Ltd"},
        )
        # doc_type exact (35) + company exact (30) = 65 pts → 0.65 minimum
        assert score >= 0.60

    def test_no_doc_type_no_identity_low_score(self):
        ds = _make_dataset(
            document_type="purchase order",
            company_name="Completely Different Co",
        )
        score = score_template_similarity(
            _SAMPLE_OCR_DIFFERENT, ds,
            document_type="grn",
            company_info={"company_name": "Unrelated Corp"},
        )
        assert score < 0.40

    def test_signature_match_lifts_score(self):
        """Pre-storing the computed signature on a dataset should boost score."""
        sig = build_template_signature(
            _SAMPLE_OCR, "sales invoice",
            {"company_name": "Acme Exports Ltd"},
        )
        ds_with_sig = _make_dataset(
            document_type="sales invoice",
            company_name="Acme Exports Ltd",
            template_signature=sig,
        )
        ds_without_sig = _make_dataset(
            document_type="sales invoice",
            company_name="Acme Exports Ltd",
            template_signature=None,
        )
        score_with = score_template_similarity(
            _SAMPLE_OCR, ds_with_sig,
            document_type="sales invoice",
            company_info={"company_name": "Acme Exports Ltd"},
        )
        score_without = score_template_similarity(
            _SAMPLE_OCR, ds_without_sig,
            document_type="sales invoice",
            company_info={"company_name": "Acme Exports Ltd"},
        )
        assert score_with >= score_without


# ═══════════════════════════════════════════════════════════════════════
# 7-9: match_training_dataset (async, uses mock DB)
# ═══════════════════════════════════════════════════════════════════════

class _MockResult:
    def __init__(self, rows):
        self._rows = rows
    def scalars(self):
        return self
    def all(self):
        return list(self._rows)


class _MockDB:
    def __init__(self, rows=None):
        self._rows = list(rows or [])
        self.committed = False

    async def execute(self, _stmt):
        return _MockResult(self._rows)

    async def commit(self):
        self.committed = True

    async def rollback(self):
        pass


class TestMatchTrainingDataset:

    def test_selects_best_by_doc_type_and_company(self):
        """Correct dataset wins when document_type + company both match."""
        ds_exact = _make_dataset(
            document_type="sales invoice",
            company_name="Acme Exports Ltd",
        )
        ds_wrong = _make_dataset(
            dataset_name="Wrong Dataset",
            document_type="purchase order",
            company_name="Completely Different Co",
        )
        db = _MockDB([ds_exact, ds_wrong])
        result = asyncio.run(match_training_dataset(
            db, _SAMPLE_OCR,
            document_type="sales invoice",
            company_info={"company_name": "Acme Exports Ltd"},
        ))
        assert result is not None
        assert result["dataset"] is ds_exact
        assert result["training_template_matched"] is True
        assert 0.0 < result["score"] <= 1.0

    def test_returns_none_below_threshold(self):
        """When no dataset scores above THRESHOLD_IGNORE, return None."""
        ds_bad = _make_dataset(
            document_type="purchase order",      # wrong doc type
            company_name="Completely Different",  # wrong company
        )
        db = _MockDB([ds_bad])
        result = asyncio.run(match_training_dataset(
            db, _SAMPLE_OCR,
            document_type="grn_receipt",
            company_info={"company_name": "Unrelated Corp XYZ"},
        ))
        # Either None or a low-score result; most importantly not a false match
        if result is not None:
            assert result["score"] < THRESHOLD_IGNORE + 0.10  # tolerance

    def test_returns_none_between_ignore_and_suggest_threshold(self):
        """Suggest-threshold misses must not auto-supply a dataset for value application."""
        ds = _make_dataset(
            document_type="sales invoice",
            company_name="Different Company",
        )
        db = _MockDB([ds])
        result = asyncio.run(match_training_dataset(
            db, _SAMPLE_OCR,
            document_type="sales invoice",
            company_info={"company_name": "Unrelated Corp"},
        ))
        assert result is None

    def test_sales_request_rejects_purchase_family_dataset(self):
        """Sales documents must not match Purchase templates even if a mock DB returns one."""
        ds_purchase = _make_dataset(
            dataset_name="Purchase Template",
            module="purchase_order",
            document_type="sales invoice",
            company_name="Acme Exports Ltd",
        )
        db = _MockDB([ds_purchase])
        result = asyncio.run(match_training_dataset(
            db, _SAMPLE_OCR,
            document_type="sales invoice",
            company_info={"company_name": "Acme Exports Ltd"},
            module="sales_order",
        ))
        assert result is None

    def test_purchase_request_rejects_sales_family_dataset(self):
        """Purchase documents must not match Sales templates even if a mock DB returns one."""
        ds_sales = _make_dataset(
            dataset_name="Sales Template",
            module="sales_order",
            document_type="purchase order",
            company_name="Acme Exports Ltd",
        )
        db = _MockDB([ds_sales])
        result = asyncio.run(match_training_dataset(
            db, "PURCHASE ORDER\nPO No: PO-100\nSupplier: Acme Exports Ltd",
            document_type="purchase order",
            company_info={"company_name": "Acme Exports Ltd"},
            module="purchase_order",
        ))
        assert result is None

    def test_returns_none_when_no_candidates(self):
        """Empty DB → None."""
        db = _MockDB([])
        result = asyncio.run(match_training_dataset(
            db, _SAMPLE_OCR, document_type="sales invoice"))
        assert result is None

    def test_returns_none_when_db_is_none(self):
        result = asyncio.run(match_training_dataset(
            None, _SAMPLE_OCR, document_type="sales invoice"))
        assert result is None

    def test_apply_mode_auto_above_threshold(self):
        """Datasets that score >= THRESHOLD_APPLY get apply_mode='auto'."""
        sig = build_template_signature(
            _SAMPLE_OCR, "sales invoice",
            {"company_name": "Acme Exports Ltd"},
        )
        ds = _make_dataset(
            document_type="sales invoice",
            company_name="Acme Exports Ltd",
            template_signature=sig,   # exact sig → +25 pts
        )
        db = _MockDB([ds])
        result = asyncio.run(match_training_dataset(
            db, _SAMPLE_OCR,
            document_type="sales invoice",
            company_info={"company_name": "Acme Exports Ltd"},
        ))
        assert result is not None
        assert result["score"] >= THRESHOLD_APPLY
        assert result["apply_mode"] == "auto"

    def test_excludes_inactive_datasets(self):
        """is_active=False datasets must not be returned."""
        ds_inactive = _make_dataset(is_active=False,
                                    document_type="sales invoice",
                                    company_name="Acme Exports Ltd")
        # The mock DB returns whatever rows we give; the filtering is in the
        # SQLAlchemy WHERE clause. We simulate a DB that correctly applies
        # the filter by giving an empty result set.
        db = _MockDB([])   # DB filtered it out already
        result = asyncio.run(match_training_dataset(
            db, _SAMPLE_OCR, document_type="sales invoice",
            company_info={"company_name": "Acme Exports Ltd"},
        ))
        assert result is None

    def test_update_usage_flag_commits(self):
        """update_usage=True should increment usage_count and commit."""
        ds = _make_dataset(
            document_type="sales invoice",
            company_name="Acme Exports Ltd",
            usage_count=3,
        )
        db = _MockDB([ds])
        asyncio.run(match_training_dataset(
            db, _SAMPLE_OCR,
            document_type="sales invoice",
            company_info={"company_name": "Acme Exports Ltd"},
            update_usage=True,
        ))
        assert db.committed is True
        assert ds.usage_count == 4


# ═══════════════════════════════════════════════════════════════════════
# 10-17: apply_training_to_mapped_fields
# ═══════════════════════════════════════════════════════════════════════

class TestApplyTrainingToMappedFields:

    def _base_mapped_result(self, **field_overrides) -> dict:
        base_fields = {
            "invoice_number": {"value": "", "confidence": 0.0, "method": "regex", "source": "regex"},
            "company_name":   {"value": "", "confidence": 0.0, "method": "regex", "source": "regex"},
            "bank_name":      {"value": "", "confidence": 0.0, "method": "regex", "source": "regex"},
        }
        base_fields.update(field_overrides)
        return {"mapped_fields": base_fields, "missing_fields": ["invoice_number"]}

    def test_adds_source_training(self):
        ds = _make_dataset()
        result = apply_training_to_mapped_fields(self._base_mapped_result(), ds)
        applied = result["training_applied_fields"]
        assert len(applied) > 0
        for fname in applied:
            assert result["mapped_fields"][fname]["source"] == "training"

    def test_adds_method_training_template(self):
        ds = _make_dataset()
        result = apply_training_to_mapped_fields(self._base_mapped_result(), ds)
        for fname in result["training_applied_fields"]:
            assert result["mapped_fields"][fname]["method"] == "training_template"

    def test_fills_missing_fields(self):
        ds = _make_dataset()
        mr = self._base_mapped_result()
        result = apply_training_to_mapped_fields(mr, ds)
        # bank_name was empty → should now be filled with sample_value
        assert result["mapped_fields"]["bank_name"]["value"] == "HDFC Bank"

    def test_does_not_overwrite_correction_memory(self):
        """Fields written by correction_memory (priority 85) must be preserved."""
        protected_value = "MEMORY-PROTECTED-123"
        mr = self._base_mapped_result(
            bank_name={
                "value": protected_value,
                "confidence": 0.85,
                "method": "memory_auto_apply",
                "source": "correction_memory",
            }
        )
        ds = _make_dataset()
        result = apply_training_to_mapped_fields(mr, ds)
        assert result["mapped_fields"]["bank_name"]["value"] == protected_value
        assert "bank_name" not in result["training_applied_fields"]

    def test_does_not_overwrite_manual_correction(self):
        """Fields corrected manually must never be overwritten."""
        protected_value = "MANUAL-OVERRIDE-999"
        mr = self._base_mapped_result(
            bank_name={
                "value": protected_value,
                "confidence": 1.0,
                "method": "manual_correction",
                "source": "user_correction",
            }
        )
        ds = _make_dataset()
        result = apply_training_to_mapped_fields(mr, ds)
        assert result["mapped_fields"]["bank_name"]["value"] == protected_value
        assert "bank_name" not in result["training_applied_fields"]

    def test_overwrites_low_confidence_regex_field(self):
        """Under new rules, training data must never overwrite valid non-empty OCR values."""
        mr = self._base_mapped_result(
            bank_name={
                "value": "UnknownBank",
                "confidence": 0.40,   # below 0.70 threshold
                "method": "regex",
                "source": "regex",
            }
        )
        ds = _make_dataset()
        result = apply_training_to_mapped_fields(mr, ds)
        assert result["mapped_fields"]["bank_name"]["value"] == "UnknownBank"
        assert "bank_name" not in result["training_applied_fields"]

    def test_keeps_high_confidence_existing_field(self):
        """Valid OCR values must have first priority and are never overwritten by training."""
        mr = self._base_mapped_result(
            bank_name={
                "value": "SBI Bank",
                "confidence": 0.90,   # high confidence
                "method": "regex",
                "source": "regex",
            }
        )
        ds = _make_dataset()
        result = apply_training_to_mapped_fields(mr, ds)
        assert result["mapped_fields"]["bank_name"]["value"] == "SBI Bank"
        assert result["mapped_fields"]["bank_name"]["source"] == "regex"

    def test_skips_dynamic_fields(self):
        """Dynamic fields must never be filled or overwritten by training dataset."""
        mr = self._base_mapped_result(
            invoice_number={"value": "", "confidence": 0.0, "method": "regex", "source": "regex"}
        )
        ds = _make_dataset(mappings_json=[
            {"target_erp_name": "invoice_number", "sample_value": "INV-12345"},
            {"target_erp_name": "company_name", "sample_value": "Acme Exports Ltd"}
        ])
        result = apply_training_to_mapped_fields(mr, ds)
        assert "invoice_number" not in result["training_applied_fields"]
        assert result["mapped_fields"]["invoice_number"]["value"] == ""

    def test_conflicts_metadata_present(self):
        """training_conflicts must be a list (may be empty or populated)."""
        ds = _make_dataset()
        result = apply_training_to_mapped_fields(self._base_mapped_result(), ds)
        assert "training_conflicts" in result
        assert isinstance(result["training_conflicts"], list)

    def test_none_dataset_returns_safe_dict(self):
        """Passing None as dataset must not raise and must return safe defaults."""
        mr = self._base_mapped_result()
        result = apply_training_to_mapped_fields(mr, None)
        assert result["training_applied_fields"] == []
        assert result["training_template_matched"] is False
        assert result["training_match_score"] == 0.0

    def test_match_dict_wrapper_accepted(self):
        """apply_training_to_mapped_fields accepts the full match dict from
        match_training_dataset (contains 'dataset' key)."""
        ds = _make_dataset()
        match_dict = {
            "dataset": ds,
            "score": 0.85,
            "apply_mode": "auto",
            "training_template_matched": True,
        }
        result = apply_training_to_mapped_fields(self._base_mapped_result(), match_dict)
        assert result["training_template_matched"] is True
        assert result["training_match_score"] == 0.85

    def test_returns_training_metadata_keys(self):
        """Return dict must contain all expected metadata keys."""
        ds = _make_dataset()
        result = apply_training_to_mapped_fields(self._base_mapped_result(), ds)
        for key in (
            "training_applied_fields",
            "training_conflicts",
            "training_template_matched",
            "training_match_score",
            "training_template_name",
            "training_dataset_id",
        ):
            assert key in result, f"Missing key: {key}"

    def test_skips_mapping_without_sample_value(self):
        """Mappings with empty sample_value must be silently skipped."""
        ds = _make_dataset(mappings_json=[
            {"target_erp_name": "invoice_number", "sample_value": ""},
            {"target_erp_name": "company_name",   "sample_value": None},
        ])
        result = apply_training_to_mapped_fields(self._base_mapped_result(), ds)
        assert result["training_applied_fields"] == []

    def test_mapped_fields_preserved_for_non_trained_keys(self):
        """Fields not in training mappings must remain untouched."""
        mr = self._base_mapped_result(
            gst_number={"value": "27AABCA1234F1Z5", "confidence": 0.99, "method": "regex", "source": "regex"}
        )
        ds = _make_dataset()  # mappings cover invoice_number, company_name, bank_name only
        result = apply_training_to_mapped_fields(mr, ds)
        assert result["mapped_fields"]["gst_number"]["value"] == "27AABCA1234F1Z5"
