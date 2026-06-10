"""Pydantic schemas for Correction Learning (Task 3)."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, Field


class WorkbenchCorrectionCreate(BaseModel):
    module: str = Field(..., description="sales_order or purchase_order")
    document_type: Optional[str] = None
    company_name: Optional[str] = None
    vendor_name: Optional[str] = None
    customer_name: Optional[str] = None
    source_ocr_key: str
    source_ocr_label: Optional[str] = None
    source_value_sample: Optional[str] = None
    target_erp_name: str
    target_dom_id: Optional[str] = None
    target_label: Optional[str] = None
    corrected_value_sample: Optional[str] = None
    confidence: float = 1.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkbenchCorrectionBulkCreate(BaseModel):
    corrections: list[WorkbenchCorrectionCreate]


class WorkbenchCorrectionApplyRequest(BaseModel):
    module: str
    document_type: Optional[str] = None
    company_name: Optional[str] = None
    vendor_name: Optional[str] = None
    customer_name: Optional[str] = None
    ocr_fields: dict[str, Any] = Field(default_factory=dict)
    form_data: dict[str, str] = Field(default_factory=dict)
    item_rows: list[dict[str, Any]] = Field(default_factory=list)


class WorkbenchAppliedCorrection(BaseModel):
    source_ocr_key: str
    normalized_source_key: str
    target_erp_name: str
    corrected_value: str
    match_type: str = "strong"


class WorkbenchSkippedCorrection(BaseModel):
    source_ocr_key: str
    target_erp_name: str
    reason: str


class WorkbenchCorrectionApplyResponse(BaseModel):
    form_data: dict[str, str]
    item_rows: list[dict[str, Any]]
    applied_count: int = 0
    applied_corrections: list[WorkbenchAppliedCorrection] = Field(default_factory=list)
    skipped_corrections: list[WorkbenchSkippedCorrection] = Field(default_factory=list)


class WorkbenchCorrectionRead(BaseModel):
    model_config = {"from_attributes": True}
    id: str
    module: Optional[str] = None
    document_type: Optional[str] = None
    company_name: Optional[str] = None
    vendor_name: Optional[str] = None
    customer_name: Optional[str] = None
    source_ocr_key: Optional[str] = None
    source_ocr_label: Optional[str] = None
    normalized_source_key: Optional[str] = None
    source_value_sample: Optional[str] = None
    target_erp_name: Optional[str] = None
    target_dom_id: Optional[str] = None
    target_label: Optional[str] = None
    corrected_value_sample: Optional[str] = None
    confidence: Optional[float] = None
    apply_count: int = 0
    reject_count: int = 0
    is_active: bool = True
    source: Optional[str] = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
