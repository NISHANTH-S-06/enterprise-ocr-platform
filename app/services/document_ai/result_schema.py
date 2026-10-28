from typing import Any, Dict, List, Optional

def build_document_ai_result(
    enabled: bool = False,
    provider: str = "none",
    mode: str = "fallback_only",
    status: str = "disabled",
    processing_ms: int = 0,
    mapped_fields: Optional[Dict[str, Any]] = None,
    item_rows: Optional[List[Dict[str, Any]]] = None,
    evidence: Optional[List[Dict[str, Any]]] = None,
    warnings: Optional[List[str]] = None,
    review_required: bool = True,
    error_message: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Constructs a normalized document AI execution result schema.
    Conforms to the Phase 8 standard output structure.
    """
    return {
        "enabled": enabled,
        "provider": provider,
        "mode": mode,
        "status": status,  # "disabled | skipped | attempted | failed | completed"
        "processing_ms": processing_ms,
        "mapped_fields": mapped_fields if mapped_fields is not None else {},
        "item_rows": item_rows if item_rows is not None else [],
        "evidence": evidence if evidence is not None else [],
        "warnings": warnings if warnings is not None else [],
        "review_required": review_required,
        "error_message": error_message,
    }
