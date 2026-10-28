import pytest
import uuid
from typing import Any
from fastapi import HTTPException

from app.services.source_trace import (
    build_source_traces,
    calculate_review_summary,
    is_field_dynamic,
    is_field_reusable,
)
from app.api.v1.routers.upload import (
    accept_job_field,
    correct_job_field,
    reject_job_field,
    not_available_job_field,
    get_job_review_summary,
    FieldActionRequest,
    FieldCorrectionRequest,
)
from app.api.v1.routers.upload_helpers import OCR_DEMO_JOBS
from app.models.company import CorrectionMemory
from app.models.extracted_data import ExtractedData


# ── Mocks ──────────────────────────────────────────────────────────────────

class _MockResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _MockDB:
    def __init__(self, rows=None):
        self._rows = list(rows or [])
        self.added = []
        self.committed = False
        self.rolled_back = False

    async def execute(self, _stmt):
        return _MockResult(self._rows)

    def add(self, item):
        self.added.append(item)

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True

    async def flush(self):
        pass


# ── Test Suite ──────────────────────────────────────────────────────────────

class TestHumanReviewWorkflow:

    # 1. Scenario 1: Auto-approved field trace
    def test_scenario_01_auto_approved_field_trace(self):
        mapped = {
            "buyer_address": {"value": "123 Main St", "confidence": 0.95, "method": "regex", "source": "regex"}
        }
        validated = {
            "buyer_address": {"value": "123 Main St", "confidence": 0.95, "method": "regex", "source": "regex"}
        }
        validation_report = {"errors": [], "warnings": []}
        
        traces = build_source_traces(mapped, validated, validation_report)
        assert "buyer_address" in traces
        trace = traces["buyer_address"]
        assert trace["review_status"] == "auto_approved"
        assert "meets all validation" in trace["review_reason"]

    # 2. Scenario 2: Low confidence triggers needs_review
    def test_scenario_02_low_confidence_triggers_needs_review(self):
        mapped = {
            "buyer_address": {"value": "123 Main St", "confidence": 0.70, "method": "regex", "source": "regex"}
        }
        validated = {
            "buyer_address": {"value": "123 Main St", "confidence": 0.70, "method": "regex", "source": "regex"}
        }
        validation_report = {"errors": [], "warnings": []}
        
        traces = build_source_traces(mapped, validated, validation_report)
        assert "buyer_address" in traces
        trace = traces["buyer_address"]
        assert trace["review_status"] == "needs_review"
        assert "below threshold 0.80" in trace["review_reason"]

    # 3. Scenario 3: Validation failures trigger validation_failed
    def test_scenario_03_validation_failures_trigger_validation_failed(self):
        mapped = {
            "grand_total": {"value": "100.00", "confidence": 0.95, "method": "regex", "source": "regex"}
        }
        validated = {
            "grand_total": {"value": "100.00", "confidence": 0.95, "method": "regex", "source": "regex"}
        }
        # Include validation error for grand_total
        validation_report = {
            "errors": ["Field 'grand_total' value does not match line items total."],
            "warnings": []
        }
        
        traces = build_source_traces(mapped, validated, validation_report)
        assert "grand_total" in traces
        trace = traces["grand_total"]
        assert trace["review_status"] == "validation_failed"
        assert "Validation status is invalid" in trace["review_reason"]
        assert trace["validation_status"] == "invalid"
        assert "does not match line items total" in trace["validation_reason"]

    # 4. Scenario 4: Missing required field triggers missing
    def test_scenario_04_missing_required_field_triggers_missing(self):
        required_fields = ["invoice_number", "grand_total"]
        for field in required_fields:
            for val in ("", "N/A", "NONE", "-"):
                mapped = {
                    field: {"value": val, "confidence": 0.95, "method": "regex", "source": "regex"}
                }
                validated = {
                    field: {"value": val, "confidence": 0.95, "method": "regex", "source": "regex"}
                }
                validation_report = {"errors": [], "warnings": []}
                extra_meta = {"document_type": "sales"}
                
                traces = build_source_traces(mapped, validated, validation_report, extra_meta=extra_meta)
                assert field in traces
                trace = traces[field]
                assert trace["review_status"] == "missing" or (val != "" and trace["review_status"] == "needs_review")
                assert "Required field is missing" in trace["review_reason"]

    # 5. Scenario 5: Training-supplied stable field triggers training_supplied
    def test_scenario_05_training_supplied_stable_field(self):
        mapped = {
            "buyer_address": {
                "value": "456 Oak Lane",
                "confidence": 0.90,
                "method": "training_template",
                "source": "training",
            }
        }
        validated = {
            "buyer_address": {
                "value": "456 Oak Lane",
                "confidence": 0.90,
                "method": "training_template",
                "source": "training",
            }
        }
        validation_report = {"errors": [], "warnings": []}
        
        traces = build_source_traces(mapped, validated, validation_report)
        assert "buyer_address" in traces
        trace = traces["buyer_address"]
        assert trace["review_status"] == "training_supplied"
        assert trace["is_training_supplied"] is True

    # 6. Scenario 6: Training-supplied dynamic field blocked
    def test_scenario_06_training_supplied_dynamic_field_blocked(self):
        mapped = {
            "invoice_number": {
                "value": "INV-TEST-999",
                "confidence": 0.90,
                "method": "training_template",
                "source": "training",
            }
        }
        validated = {
            "invoice_number": {
                "value": "INV-TEST-999",
                "confidence": 0.90,
                "method": "training_template",
                "source": "training",
            }
        }
        validation_report = {"errors": [], "warnings": []}
        
        traces = build_source_traces(mapped, validated, validation_report)
        assert "invoice_number" in traces
        trace = traces["invoice_number"]
        assert trace["review_status"] == "needs_review"
        assert "Dynamic field was supplied from training template" in trace["review_reason"]

    # 7. Scenario 7: Conflict status detection
    def test_scenario_07_conflict_status_detection(self):
        mapped = {
            "buyer_address": {"value": "123 Main St", "confidence": 0.95, "method": "regex", "source": "regex"}
        }
        validated = {
            "buyer_address": {"value": "123 Main St", "confidence": 0.95, "method": "regex", "source": "regex"}
        }
        validation_report = {"errors": [], "warnings": []}
        extra_meta = {
            "training_conflicts": [
                {"field": "buyer_address", "action": "override_conflict"}
            ]
        }
        
        traces = build_source_traces(mapped, validated, validation_report, extra_meta=extra_meta)
        assert "buyer_address" in traces
        trace = traces["buyer_address"]
        assert trace["review_status"] == "conflict"
        assert "conflicts with training value" in trace["review_reason"]
        assert trace["conflict_status"] == "override_conflict"

    # Helper to setup a dummy demo job in memory
    def _setup_demo_job(self, job_id: str, owner_user_id: str, grand_total_val: str = "150.00") -> dict[str, Any]:
        job = {
            "job_id": job_id,
            "status": "completed",
            "owner_user_id": owner_user_id,
            "canonical_fields": {
                "buyer_address": {"field_name": "buyer_address", "value": "123 Main St", "confidence": 0.95, "method": "regex", "source": "regex"},
                "invoice_number": {"field_name": "invoice_number", "value": "INV-001", "confidence": 0.95, "method": "regex", "source": "regex"},
                "invoice_date": {"field_name": "invoice_date", "value": "2026-06-22", "confidence": 0.95, "method": "regex", "source": "regex"},
                "company_name": {"field_name": "company_name", "value": "Acme Corp", "confidence": 0.95, "method": "regex", "source": "regex"},
                "grand_total": {"field_name": "grand_total", "value": grand_total_val, "confidence": 0.95, "method": "regex", "source": "regex"},
            },
            "document_type": "sales",
            "raw_text": f"INV-001 123 Main St 2026-06-22 Acme Corp {grand_total_val}",
            "raw_text_preview": f"INV-001 123 Main St 2026-06-22 Acme Corp {grand_total_val}",
            "detected_company": {"company_id": str(uuid.uuid4())},
            "export_allowed": True if grand_total_val else False,
            "export_block_reasons": [] if grand_total_val else ["grand_total is MISSING"],
            "warnings": [],
        }
        
        # Build initial source traces
        job["source_traces"] = build_source_traces(
            mapped_fields=job["canonical_fields"],
            validated_fields=job["canonical_fields"],
            validation_report={"errors": job["export_block_reasons"], "warnings": []},
            extra_meta={"document_type": "sales"}
        )
        
        OCR_DEMO_JOBS[job_id] = job
        return job

    # 8. Scenario 8: Accept field action endpoint
    @pytest.mark.asyncio
    async def test_scenario_08_accept_field_action_endpoint(self):
        job_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        self._setup_demo_job(job_id, user_id)
        current_user = {"user_id": user_id, "role": "operator"}
        db = _MockDB()

        req = FieldActionRequest(field_name="buyer_address", reviewer_note="Verified manually")
        res = await accept_job_field(job_id, req, current_user, db)

        assert res["success"] is True
        assert job_id in OCR_DEMO_JOBS
        job = OCR_DEMO_JOBS[job_id]
        
        assert "buyer_address" in job["source_traces"]
        trace = job["source_traces"]["buyer_address"]
        assert trace["user_action"] == "accept"
        assert trace["review_status"] == "accepted"
        assert trace["reviewer_note"] == "Verified manually"

    # 9. Scenario 9: Correct field action endpoint
    @pytest.mark.asyncio
    async def test_scenario_09_correct_field_action_endpoint(self):
        job_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        self._setup_demo_job(job_id, user_id)
        current_user = {"user_id": user_id, "role": "operator"}
        db = _MockDB()

        # Correct a reusable field with is_reusable=True
        req = FieldCorrectionRequest(
            field_name="buyer_address",
            corrected_value="789 Pine Rd",
            is_reusable=True,
            reviewer_note="Updated address"
        )
        res = await correct_job_field(job_id, req, current_user, db)

        assert res["success"] is True
        job = OCR_DEMO_JOBS[job_id]
        assert job["canonical_fields"]["buyer_address"]["value"] == "789 Pine Rd"
        
        trace = job["source_traces"]["buyer_address"]
        assert trace["user_action"] == "correct"
        assert trace["review_status"] == "corrected"
        assert trace["final_value"] == "789 Pine Rd"

        # Check CorrectionMemory row is added to the db mock
        assert len(db.added) == 1
        corr = db.added[0]
        assert isinstance(corr, CorrectionMemory)
        assert corr.field_name == "buyer_address"
        assert corr.correct_value == "789 Pine Rd"

    # 10. Scenario 10: Reject field action endpoint
    @pytest.mark.asyncio
    async def test_scenario_10_reject_field_action_endpoint(self):
        job_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        self._setup_demo_job(job_id, user_id)
        current_user = {"user_id": user_id, "role": "operator"}
        db = _MockDB()

        req = FieldActionRequest(field_name="buyer_address")
        res = await reject_job_field(job_id, req, current_user, db)

        assert res["success"] is True
        job = OCR_DEMO_JOBS[job_id]
        assert job["canonical_fields"]["buyer_address"]["value"] == ""
        
        trace = job["source_traces"]["buyer_address"]
        assert trace["user_action"] == "reject"
        assert trace["review_status"] == "rejected"

    # 11. Scenario 11: Not available field action endpoint
    @pytest.mark.asyncio
    async def test_scenario_11_not_available_field_action_endpoint(self):
        job_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        self._setup_demo_job(job_id, user_id)
        current_user = {"user_id": user_id, "role": "operator"}
        db = _MockDB()

        req = FieldActionRequest(field_name="buyer_address")
        res = await not_available_job_field(job_id, req, current_user, db)

        assert res["success"] is True
        job = OCR_DEMO_JOBS[job_id]
        assert job["canonical_fields"]["buyer_address"]["value"] == ""
        
        trace = job["source_traces"]["buyer_address"]
        assert trace["user_action"] == "not_available"
        assert trace["review_status"] == "rejected"

    # 12. Scenario 12: Reusable correction blocked for dynamic fields
    @pytest.mark.asyncio
    async def test_scenario_12_reusable_correction_blocked_for_dynamic_fields(self):
        job_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        self._setup_demo_job(job_id, user_id)
        current_user = {"user_id": user_id, "role": "operator"}
        db = _MockDB()

        # Dynamic field cannot be saved to CorrectionMemory (is_reusable = True throws HTTP 400)
        req = FieldCorrectionRequest(
            field_name="invoice_number",
            corrected_value="INV-12345",
            is_reusable=True
        )
        with pytest.raises(HTTPException) as exc:
            await correct_job_field(job_id, req, current_user, db)
        assert exc.value.status_code == 400
        assert "Dynamic transaction fields cannot be saved" in exc.value.detail

    # 13. Scenario 13: Review summary counts accuracy
    def test_scenario_13_review_summary_counts_accuracy(self):
        traces = {
            "f1": {"review_status": "auto_approved", "is_dynamic_field": False, "is_training_supplied": False, "is_required": True},
            "f2": {"review_status": "needs_review", "is_dynamic_field": False, "is_training_supplied": False, "is_required": True},
            "f3": {"review_status": "validation_failed", "is_dynamic_field": False, "is_training_supplied": False, "is_required": False},
            "f4": {"review_status": "missing", "is_dynamic_field": False, "is_training_supplied": False, "is_required": True},
            "f5": {"review_status": "conflict", "is_dynamic_field": False, "is_training_supplied": False, "is_required": False},
            "f6": {"review_status": "training_supplied", "is_dynamic_field": True, "is_training_supplied": True, "is_required": False},
        }

        summary = calculate_review_summary(traces)
        assert summary["total_fields"] == 6
        assert summary["auto_approved_count"] == 1
        assert summary["needs_review_count"] == 1
        assert summary["validation_failed_count"] == 1
        assert summary["missing_count"] == 1
        assert summary["conflict_count"] == 1
        assert summary["training_supplied_count"] == 1
        # f6 is dynamic and training-supplied
        assert summary["dynamic_training_blocked_count"] == 1
        # Blockers: f2 (needs_review & required), f4 (missing & required)
        assert sorted(summary["export_blockers"]) == ["f2", "f4"]
        assert summary["export_ready"] is False

    # 14. Scenario 14: Export readiness transitions
    @pytest.mark.asyncio
    async def test_scenario_14_export_readiness_transitions(self):
        job_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        
        # Setup job with a missing required field (grand_total required and value empty "")
        self._setup_demo_job(job_id, user_id, grand_total_val="")
        current_user = {"user_id": user_id, "role": "operator"}
        db = _MockDB()

        # Fetch initial review summary
        summary = await get_job_review_summary(job_id, current_user, db)
        assert summary["export_ready"] is False
        assert "grand_total" in summary["export_blockers"]

        # User corrects the field grand_total
        req = FieldCorrectionRequest(
            field_name="grand_total",
            corrected_value="250.00",
            is_reusable=False
        )
        res = await correct_job_field(job_id, req, current_user, db)
        assert res["success"] is True
        assert res["export_allowed"] is True
        assert res["review_summary"]["export_ready"] is True
        assert len(res["review_summary"]["export_blockers"]) == 0
