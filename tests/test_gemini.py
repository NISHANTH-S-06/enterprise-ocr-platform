import pytest
import asyncio
from app.services.gemini_service import gemini_enrich_missing_fields
from app.core.config import get_settings
from app.api.v1.routers.upload import _ai_fallback_audit_metadata, _ai_fallback_target_fields
from app.services.validation_service import validate_mapped_fields


@pytest.fixture(autouse=True)
def reset_ai_settings():
    settings = get_settings()
    original = {
        "AI_FALLBACK_ENABLED": settings.AI_FALLBACK_ENABLED,
        "GEMINI_API_KEY": settings.GEMINI_API_KEY,
        "AI_FALLBACK_MIN_CONFIDENCE": settings.AI_FALLBACK_MIN_CONFIDENCE,
        "AI_FALLBACK_REGEX_PROTECT_CONFIDENCE": settings.AI_FALLBACK_REGEX_PROTECT_CONFIDENCE,
    }
    yield
    for key, value in original.items():
        setattr(settings, key, value)

def test_gemini_enrich_disabled_by_default():
    async def run_test():
        settings = get_settings()
        settings.AI_FALLBACK_ENABLED = False
        mapped_result = {"mapped_fields": {}, "missing_fields": ["invoice_number"]}
        res = await gemini_enrich_missing_fields(mapped_result.copy(), "raw", "sales")
        assert "ai_fallback" not in res
    asyncio.run(run_test())

def test_gemini_enrich_no_missing_fields():
    async def run_test():
        settings = get_settings()
        settings.AI_FALLBACK_ENABLED = True
        mapped_result = {
            "mapped_fields": {"invoice_number": {"value": "123", "confidence": 0.95}},
            "missing_fields": []
        }
        res = await gemini_enrich_missing_fields(mapped_result.copy(), "raw", "sales")
        assert "ai_fallback" not in res
        settings.AI_FALLBACK_ENABLED = False
    asyncio.run(run_test())


def test_gemini_enrich_missing_fields_with_synonyms():
    async def run_test():
        mapped_result = {
            "mapped_fields": {
                "vendor_name": {"value": "Acme", "confidence": 0.95},
                "total_amount": {"value": "100", "confidence": 0.50}  # Weak field
            },
            "missing_fields": ["invoice_number", "invoice_date", "buyer_name", "consignee", "grand_total"]
        }
        mock_response = {
            "invoice_number": {"value": "INV-123", "confidence": 0.90, "source_evidence": "Bill Number: INV-123"},
            "invoice_date": {"value": "2024-01-01", "confidence": 0.85, "source_evidence": "Document Date: 2024-01-01"},
            "buyer_name": {"value": "Buyer Inc", "confidence": 0.99, "source_evidence": "Bill To: Buyer Inc"},
            "consignee": {"value": "Ship Inc", "confidence": 0.95, "source_evidence": "Ship To: Ship Inc"},
            "grand_total": {"value": "500", "confidence": 0.88, "source_evidence": "Total Amount: 500"},
            "total_amount": {"value": "150", "confidence": 0.80, "source_evidence": "Total Amount: 150"},
            "vendor_name": {"value": "Evil Corp", "confidence": 0.99, "source_evidence": "Vendor: Evil Corp"} # Should be blocked
        }
        
        res = await gemini_enrich_missing_fields(mapped_result, "raw", "sales", mock_response=mock_response)
        
        fields = res["mapped_fields"]
        # AI enriched fields
        assert fields["invoice_number"]["value"] == "INV-123"
        assert fields["invoice_number"]["method"] == "gemini"
        assert fields["invoice_date"]["value"] == "2024-01-01"
        assert fields["buyer_name"]["value"] == "Buyer Inc"
        assert fields["consignee"]["value"] == "Ship Inc"
        assert fields["grand_total"]["value"] == "500"
        
        # Overwrite weak field
        assert fields["total_amount"]["value"] == "150"
        assert fields["total_amount"]["method"] == "gemini"
        
        # Protect strong field
        assert fields["vendor_name"]["value"] == "Acme"
        assert "method" not in fields["vendor_name"] or fields["vendor_name"]["method"] != "gemini"
    asyncio.run(run_test())

def test_gemini_enrich_low_confidence():
    async def run_test():
        settings = get_settings()
        settings.AI_FALLBACK_ENABLED = True
        mapped_result = {"mapped_fields": {}, "missing_fields": ["notes"]}
        mock_response = {
            "notes": {"value": "Some notes", "confidence": 0.60, "source_evidence": "Notes: Some notes"}
        }
        res = await gemini_enrich_missing_fields(mapped_result, "raw", "sales", mock_response=mock_response)
        
        assert "notes" not in res["mapped_fields"]
        assert res["ai_fallback"]["fields_rejected"] == ["notes"]
        assert res["ai_fallback"]["needs_review"] is True
    asyncio.run(run_test())

def test_gemini_enrich_missing_evidence():
    async def run_test():
        settings = get_settings()
        settings.AI_FALLBACK_ENABLED = True
        mapped_result = {"mapped_fields": {}, "missing_fields": ["tax_amount"]}
        mock_response = {
            "tax_amount": {"value": "10.00", "confidence": 0.99, "source_evidence": ""}
        }
        res = await gemini_enrich_missing_fields(mapped_result, "raw", "sales", mock_response=mock_response)
        
        assert "tax_amount" not in res["mapped_fields"]
        assert res["ai_fallback"]["fields_rejected"] == ["tax_amount"]
        assert res["ai_fallback"]["needs_review"] is True
    asyncio.run(run_test())

from app.services.gemini_service import build_gemini_field_prompt, parse_gemini_json_response, call_gemini_api

def test_prompt_builder():
    prompt = build_gemini_field_prompt("sample text", "invoice", ["invoice_number", "total_amount"], {"vendor_name": "Acme"})
    assert "invoice_number" in prompt
    assert "total_amount" in prompt
    assert "Acme" in prompt
    assert "ONLY valid JSON" in prompt

def test_parser_accepts_valid_json():
    json_str = '{"invoice_number": {"value": "123", "confidence": 0.95, "source_evidence": "Bill No: 123"}}'
    res = parse_gemini_json_response(json_str)
    assert "invoice_number" in res
    assert res["invoice_number"]["value"] == "123"
    assert res["invoice_number"]["confidence"] == 0.95
    assert res["invoice_number"]["source_evidence"] == "Bill No: 123"

def test_parser_strips_markdown():
    json_str = '```json\n{"total_amount": {"value": "100", "confidence": 0.9, "source_evidence": "Total: 100"}}\n```'
    res = parse_gemini_json_response(json_str)
    assert "total_amount" in res
    assert res["total_amount"]["value"] == "100"

def test_parser_rejects_missing_evidence():
    json_str = '{"notes": {"value": "hello", "confidence": 0.9}}'
    res = parse_gemini_json_response(json_str)
    assert "notes" not in res

def test_parser_handles_invalid_json_safely():
    res = parse_gemini_json_response('this is not json')
    assert res == {}

def test_stub_disabled():
    async def run_test():
        settings = get_settings()
        settings.AI_FALLBACK_ENABLED = False
        res = await call_gemini_api("prompt")
        assert res["used"] == False
        assert res["reason"] == "disabled"
    asyncio.run(run_test())

def test_stub_missing_api_key():
    async def run_test():
        settings = get_settings()
        settings.AI_FALLBACK_ENABLED = True
        settings.GEMINI_API_KEY = ""
        res = await call_gemini_api("prompt")
        assert res["used"] == False
        assert res["reason"] == "missing_api_key"
        settings.AI_FALLBACK_ENABLED = False
    asyncio.run(run_test())

from unittest.mock import patch, MagicMock

def test_stub_client_unavailable():
    async def run_test():
        settings = get_settings()
        settings.AI_FALLBACK_ENABLED = True
        settings.GEMINI_API_KEY = "mock_key"
        with patch.dict('sys.modules', {'google.genai': None, 'google': None}):
            res = await call_gemini_api("prompt")
            assert res["used"] == False
            assert res["reason"] == "client_unavailable"
        settings.AI_FALLBACK_ENABLED = False
    asyncio.run(run_test())

def test_live_call_mocked_success():
    async def run_test():
        settings = get_settings()
        settings.AI_FALLBACK_ENABLED = True
        settings.GEMINI_API_KEY = "mock_key"
        
        mock_client_cls = MagicMock()
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = '{"test": {"value": "123", "confidence": 0.9, "source_evidence": "abc"}}'
        mock_client.models.generate_content.return_value = mock_response
        mock_client_cls.return_value = mock_client
        
        google_mock = MagicMock()
        genai_mock = MagicMock()
        genai_mock.Client = mock_client_cls
        google_mock.genai = genai_mock
        
        with patch.dict('sys.modules', {'google': google_mock, 'google.genai': genai_mock}):
            res = await call_gemini_api("prompt")
            assert res["used"] == True
            assert "results" in res
            assert res["results"]["test"]["value"] == "123"
            
        settings.AI_FALLBACK_ENABLED = False
    asyncio.run(run_test())

def test_live_call_api_exception():
    async def run_test():
        settings = get_settings()
        settings.AI_FALLBACK_ENABLED = True
        settings.GEMINI_API_KEY = "mock_key"
        
        mock_client_cls = MagicMock()
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = Exception("API failed")
        mock_client_cls.return_value = mock_client
        
        google_mock = MagicMock()
        genai_mock = MagicMock()
        genai_mock.Client = mock_client_cls
        google_mock.genai = genai_mock
        
        with patch.dict('sys.modules', {'google': google_mock, 'google.genai': genai_mock}):
            res = await call_gemini_api("prompt")
            assert res["used"] == False
            assert res["reason"] == "api_exception"
            assert res["error"] == "API failed"
            
        settings.AI_FALLBACK_ENABLED = False
    asyncio.run(run_test())


def test_upload_ai_disabled_path_unchanged():
    settings = get_settings()
    settings.AI_FALLBACK_ENABLED = False
    mapped_result = {
        "mapped_fields": {"invoice_number": {"value": "INV-1", "confidence": 0.40}},
        "missing_fields": ["grand_total"],
    }
    assert _ai_fallback_target_fields(mapped_result) == ["grand_total", "invoice_number"]
    assert _ai_fallback_audit_metadata(mapped_result) is None


def test_missing_field_recovered_and_tagged():
    async def run_test():
        settings = get_settings()
        settings.AI_FALLBACK_ENABLED = True
        mapped_result = {"mapped_fields": {}, "missing_fields": ["invoice_number"]}
        mock_response = {
            "invoice_number": {
                "value": "INV-42",
                "confidence": 0.91,
                "source_evidence": "Invoice No: INV-42",
            }
        }
        res = await gemini_enrich_missing_fields(mapped_result, "Invoice No: INV-42", "sales", mock_response=mock_response)
        field = res["mapped_fields"]["invoice_number"]
        assert field["value"] == "INV-42"
        assert field["method"] == "gemini"
        assert field["source_evidence"] == "Invoice No: INV-42"
        assert field["reason"] == "AI enriched"
    asyncio.run(run_test())


def test_high_confidence_regex_protected():
    async def run_test():
        settings = get_settings()
        settings.AI_FALLBACK_ENABLED = True
        mapped_result = {
            "mapped_fields": {"invoice_number": {"value": "REGEX-1", "confidence": 0.95}},
            "missing_fields": ["invoice_number"],
        }
        mock_response = {
            "invoice_number": {"value": "AI-1", "confidence": 0.99, "source_evidence": "Invoice AI-1"}
        }
        res = await gemini_enrich_missing_fields(mapped_result, "Invoice AI-1", "sales", mock_response=mock_response)
        assert res["mapped_fields"]["invoice_number"]["value"] == "REGEX-1"
        assert res["ai_fallback"]["fields_recovered"] == []
    asyncio.run(run_test())


def test_validation_rejects_bad_ai_output():
    mapped_fields = {
        "invoice_date": {
            "value": "not-a-date",
            "confidence": 0.95,
            "method": "gemini",
            "source_evidence": "Date: not-a-date",
            "reason": "AI enriched",
        }
    }
    validation = validate_mapped_fields(mapped_fields, raw_text="Date: not-a-date", doc_type="sales")
    assert any("invoice_date" in warning for warning in validation["warnings"])
    audit = _ai_fallback_audit_metadata(
        {
            "ai_fallback": {
                "provider": "gemini",
                "model": "gemini-test",
                "fields_requested": ["invoice_date"],
                "fields_recovered": ["invoice_date"],
                "fields_rejected": [],
                "needs_review": False,
                "reason": "used",
            }
        },
        validation["warnings"],
    )
    assert audit["needs_review"] is True


def test_audit_metadata_generated():
    mapped_result = {
        "ai_fallback": {
            "provider": "gemini",
            "model": "gemini-test",
            "fields_requested": ["grand_total"],
            "fields_recovered": [],
            "fields_rejected": ["grand_total"],
            "needs_review": True,
            "reason": "used",
        }
    }
    audit = _ai_fallback_audit_metadata(mapped_result)
    assert audit == {
        "provider": "gemini",
        "model": "gemini-test",
        "fields_requested": ["grand_total"],
        "fields_recovered": [],
        "fields_rejected": ["grand_total"],
        "needs_review": True,
        "reason": "used",
    }
