"""Pydantic schemas for Company master data — Phase 1."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Company
# ---------------------------------------------------------------------------

class CompanyBase(BaseModel):
    company_name:           str
    gst_number:             Optional[str] = None
    iec_code:               Optional[str] = None
    address:                Optional[str] = None
    bank_name:              Optional[str] = None
    account_number:         Optional[str] = None
    ifsc_code:              Optional[str] = None
    swift_code:             Optional[str] = None
    default_currency:       str           = "INR"
    default_tax_type:       Optional[str] = None
    default_port_loading:   Optional[str] = None
    default_port_discharge: Optional[str] = None
    is_active:              bool          = True


class CompanyCreate(CompanyBase):
    pass


class CompanyUpdate(BaseModel):
    company_name:           Optional[str]  = None
    gst_number:             Optional[str]  = None
    iec_code:               Optional[str]  = None
    address:                Optional[str]  = None
    bank_name:              Optional[str]  = None
    account_number:         Optional[str]  = None
    ifsc_code:              Optional[str]  = None
    swift_code:             Optional[str]  = None
    default_currency:       Optional[str]  = None
    default_tax_type:       Optional[str]  = None
    default_port_loading:   Optional[str]  = None
    default_port_discharge: Optional[str]  = None
    is_active:              Optional[bool] = None


class CompanyRead(CompanyBase):
    model_config = ConfigDict(from_attributes=True)
    id:         uuid.UUID
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# CompanyTemplate
# ---------------------------------------------------------------------------

class CompanyTemplateBase(BaseModel):
    company_id:       uuid.UUID
    document_type:    str
    template_name:    str
    template_version: str  = "1.0"
    is_active:        bool = True


class CompanyTemplateCreate(CompanyTemplateBase):
    pass


class CompanyTemplateRead(CompanyTemplateBase):
    model_config = ConfigDict(from_attributes=True)
    id:         uuid.UUID
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# FieldMapping
# ---------------------------------------------------------------------------

class FieldMappingBase(BaseModel):
    template_id:   uuid.UUID
    field_name:    str
    label_text:    Optional[str] = None
    method:        str           = "regex"
    regex_pattern: Optional[str] = None
    section_name:  Optional[str] = None
    priority:      int           = 50
    required:      bool          = False


class FieldMappingCreate(FieldMappingBase):
    pass


class FieldMappingRead(FieldMappingBase):
    model_config = ConfigDict(from_attributes=True)
    id:         uuid.UUID
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# CorrectionMemory
# ---------------------------------------------------------------------------

class CorrectionMemoryCreate(BaseModel):
    company_id:      uuid.UUID
    template_id:     Optional[uuid.UUID] = None
    document_type:   Optional[str]       = None
    field_name:      str
    old_value:       Optional[str]       = None
    correct_value:   str
    evidence_text:   Optional[str]       = None
    rule_suggestion: Optional[str]       = None


class CorrectionMemoryRead(CorrectionMemoryCreate):
    model_config = ConfigDict(from_attributes=True)
    id:         uuid.UUID
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# ValidationResult
# ---------------------------------------------------------------------------

class ValidationResultCreate(BaseModel):
    document_id: str
    field_name:  str
    rule_name:   str
    status:      str = "fail"
    message:     Optional[str] = None
    severity:    str = "medium"


class ValidationResultRead(ValidationResultCreate):
    model_config = ConfigDict(from_attributes=True)
    id:         uuid.UUID
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# DocumentQualityReport
# ---------------------------------------------------------------------------

class DocumentQualityReportCreate(BaseModel):
    document_id:       str
    quality_status:    str   = "acceptable"
    blur_score:        Optional[float] = None
    resolution_status: str   = "unknown"
    rotation_angle:    Optional[float] = None
    warnings_json:     Optional[dict]  = None
    can_continue:      bool  = True


class DocumentQualityReportRead(DocumentQualityReportCreate):
    model_config = ConfigDict(from_attributes=True)
    id:         uuid.UUID
    created_at: datetime
    updated_at: datetime
