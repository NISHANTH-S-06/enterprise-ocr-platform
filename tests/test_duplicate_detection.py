import uuid
import pytest
from app.services.duplicate_detection import (
    normalize_doc_number,
    normalize_company,
    normalize_date,
    normalize_amount,
    detect_duplicates,
    extract_keys_from_dict
)
from app.services.source_trace import calculate_review_summary
from app.api.v1.routers.upload_helpers import OCR_DEMO_JOBS

def test_normalization_invoice_number():
    assert normalize_doc_number("INV-123/456_A") == "INV123456A"
    assert normalize_doc_number("  inv 99-88 #2  ") == "INV99882"
    assert normalize_doc_number("") == ""
    assert normalize_doc_number(None) == ""

def test_normalization_company():
    assert normalize_company("CDS Soles Pvt Ltd") == "cdssoles"
    assert normalize_company("CDS Soles India Co.") == "cdssolesindia"
    assert normalize_company("CDS Soles (India) Limited") == "cdssolesindia"
    assert normalize_company("") == ""
    assert normalize_company(None) == ""

def test_normalization_date():
    assert normalize_date("22-06-2026") == "2026-06-22"
    assert normalize_date("2026/06/22") == "2026-06-22"
    assert normalize_date("06/22/2026") == "2026-06-22"
    assert normalize_date("22.06.26") == "2026-06-22"
    assert normalize_date("") == ""
    assert normalize_date(None) == ""

def test_normalization_amount():
    assert normalize_amount("$1,234.56") == "1234.56"
    assert normalize_amount("Rs. 450.00") == "450.00"
    assert normalize_amount("203,546.00") == "203546.00"
    assert normalize_amount("") == ""
    assert normalize_amount(None) == ""

@pytest.mark.asyncio
async def test_duplicate_same_checksum():
    current_job_id = uuid.uuid4()
    checksum = "abc123sha"
    org_id = uuid.uuid4()
    created_by_id = uuid.uuid4()
    
    # Mock OCR_DEMO_JOBS
    OCR_DEMO_JOBS.clear()
    prev_job_id = str(uuid.uuid4())
    OCR_DEMO_JOBS[prev_job_id] = {
        "job_id": prev_job_id,
        "status": "completed",
        "org_id": str(org_id),
        "owner_user_id": str(created_by_id),
        "checksum_sha256": checksum,
        "filename": "original.pdf",
        "created_at": "2026-06-22T12:00:00Z"
    }

    res = await detect_duplicates(
        current_job_id=current_job_id,
        checksum=checksum,
        validated_fields={},
        document_type="general",
        org_id=org_id,
        created_by_id=created_by_id
    )

    assert res["status"] == "exact_duplicate"
    assert res["confidence"] == 1.0
    assert res["matched_job_id"] == prev_job_id
    assert "same file checksum" in res["reasons"]
    assert res["blocking"] is True
    assert res["review_required"] is True

@pytest.mark.asyncio
async def test_duplicate_same_business_key_exact():
    current_job_id = uuid.uuid4()
    checksum = "current_sha"
    org_id = uuid.uuid4()
    created_by_id = uuid.uuid4()
    
    OCR_DEMO_JOBS.clear()
    prev_job_id = str(uuid.uuid4())
    OCR_DEMO_JOBS[prev_job_id] = {
        "job_id": prev_job_id,
        "status": "completed",
        "org_id": str(org_id),
        "owner_user_id": str(created_by_id),
        "checksum_sha256": "different_sha",
        "filename": "prev.pdf",
        "document_type": "sales",
        "validated_fields": {
            "invoice_number": {"value": "INV-1001"},
            "company_name": {"value": "CDS Soles Pvt Ltd"},
            "grand_total": {"value": "100.00"},
            "invoice_date": {"value": "2026-06-22"}
        }
    }

    # Match all: same doc_type + same vendor + same doc number + same total
    current_fields = {
        "invoice_number": {"value": "INV-1001"},
        "company_name": {"value": "CDS Soles Pvt Ltd"},
        "grand_total": {"value": "100.00"},
        "invoice_date": {"value": "2026-06-22"}
    }

    res = await detect_duplicates(
        current_job_id=current_job_id,
        checksum=checksum,
        validated_fields=current_fields,
        document_type="sales",
        org_id=org_id,
        created_by_id=created_by_id
    )

    assert res["status"] == "exact_duplicate"
    assert res["confidence"] == 0.95
    assert res["matched_job_id"] == prev_job_id
    assert "same invoice number" in res["reasons"]
    assert "same vendor" in res["reasons"]
    assert "same total amount" in res["reasons"]

@pytest.mark.asyncio
async def test_duplicate_likely():
    current_job_id = uuid.uuid4()
    checksum = "current_sha"
    org_id = uuid.uuid4()
    created_by_id = uuid.uuid4()
    
    OCR_DEMO_JOBS.clear()
    prev_job_id = str(uuid.uuid4())
    OCR_DEMO_JOBS[prev_job_id] = {
        "job_id": prev_job_id,
        "status": "completed",
        "org_id": str(org_id),
        "owner_user_id": str(created_by_id),
        "checksum_sha256": "different_sha",
        "filename": "prev.pdf",
        "document_type": "sales",
        "validated_fields": {
            "invoice_number": {"value": "INV-1001"},
            "company_name": {"value": "CDS Soles Pvt Ltd"}
        }
    }

    # Match document number + vendor, but total amount differs
    current_fields = {
        "invoice_number": {"value": "INV-1001"},
        "company_name": {"value": "CDS Soles Pvt Ltd"},
        "grand_total": {"value": "500.00"}
    }

    res = await detect_duplicates(
        current_job_id=current_job_id,
        checksum=checksum,
        validated_fields=current_fields,
        document_type="sales",
        org_id=org_id,
        created_by_id=created_by_id
    )

    assert res["status"] == "likely_duplicate"
    assert res["confidence"] == 0.8
    assert res["matched_job_id"] == prev_job_id
    assert "same invoice number" in res["reasons"]
    assert "same vendor" in res["reasons"]
    assert res["blocking"] is False
    assert res["review_required"] is True

@pytest.mark.asyncio
async def test_duplicate_possible():
    current_job_id = uuid.uuid4()
    checksum = "current_sha"
    org_id = uuid.uuid4()
    created_by_id = uuid.uuid4()
    
    OCR_DEMO_JOBS.clear()
    prev_job_id = str(uuid.uuid4())
    OCR_DEMO_JOBS[prev_job_id] = {
        "job_id": prev_job_id,
        "status": "completed",
        "org_id": str(org_id),
        "owner_user_id": str(created_by_id),
        "checksum_sha256": "different_sha",
        "filename": "prev.pdf",
        "document_type": "sales",
        "validated_fields": {
            "company_name": {"value": "CDS Soles Pvt Ltd"},
            "invoice_date": {"value": "2026-06-22"},
            "grand_total": {"value": "100.00"}
        }
    }

    # Same vendor + date + total, but missing invoice number
    current_fields = {
        "company_name": {"value": "CDS Soles Pvt Ltd"},
        "invoice_date": {"value": "2026-06-22"},
        "grand_total": {"value": "100.00"}
    }

    res = await detect_duplicates(
        current_job_id=current_job_id,
        checksum=checksum,
        validated_fields=current_fields,
        document_type="sales",
        org_id=org_id,
        created_by_id=created_by_id
    )

    assert res["status"] == "possible_duplicate"
    assert res["confidence"] == 0.5
    assert res["matched_job_id"] == prev_job_id
    assert "same vendor" in res["reasons"]
    assert "same total amount" in res["reasons"]
    assert res["blocking"] is False
    assert res["review_required"] is False

@pytest.mark.asyncio
async def test_not_duplicate():
    current_job_id = uuid.uuid4()
    checksum = "current_sha"
    org_id = uuid.uuid4()
    created_by_id = uuid.uuid4()
    
    OCR_DEMO_JOBS.clear()
    prev_job_id = str(uuid.uuid4())
    OCR_DEMO_JOBS[prev_job_id] = {
        "job_id": prev_job_id,
        "status": "completed",
        "org_id": str(org_id),
        "owner_user_id": str(created_by_id),
        "checksum_sha256": "different_sha",
        "filename": "prev.pdf",
        "document_type": "sales",
        "validated_fields": {
            "invoice_number": {"value": "INV-1002"},
            "company_name": {"value": "CDS Soles Pvt Ltd"}
        }
    }

    # Different invoice number
    current_fields = {
        "invoice_number": {"value": "INV-1001"},
        "company_name": {"value": "CDS Soles Pvt Ltd"}
    }

    res = await detect_duplicates(
        current_job_id=current_job_id,
        checksum=checksum,
        validated_fields=current_fields,
        document_type="sales",
        org_id=org_id,
        created_by_id=created_by_id
    )

    assert res["status"] == "not_duplicate"
    assert res["confidence"] == 0.0

@pytest.mark.asyncio
async def test_non_crash_safety():
    # Pass invalid values/None and ensure it falls back cleanly to default not_duplicate structure without throwing errors
    res = await detect_duplicates(
        current_job_id=uuid.uuid4(),
        checksum=None,
        validated_fields=None, # type: ignore
        document_type=None, # type: ignore
        org_id=None,
        created_by_id=uuid.uuid4()
    )
    assert res["status"] == "not_duplicate"
    assert res["confidence"] == 0.0

def test_calculate_review_summary_with_duplicate():
    dup_det = {
        "status": "exact_duplicate",
        "confidence": 1.0,
        "matched_job_id": "matched_id",
        "matched_document_name": "matched.pdf",
        "matched_created_at": "2026-06-22T12:00:00Z",
        "reasons": ["same file checksum"],
        "blocking": True,
        "review_required": True
    }

    summary = calculate_review_summary(
        source_traces={},
        duplicate_detection=dup_det
    )

    assert summary["duplicate_warning"] == "Possible duplicate document detected"
    assert "same file checksum" in summary["duplicate_reason"]
    assert "matched.pdf" in summary["duplicate_matched_reference"]
    assert summary["export_ready"] is False
    assert "duplicate_document" in summary["export_blockers"]
