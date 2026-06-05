from __future__ import annotations
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID
from pydantic import BaseModel, Field
from app.models.enums import ExportFormat


class FillRequest(BaseModel):
    extracted_data_id: UUID
    template_id:       Optional[UUID]    = None
    export_format:     ExportFormat      = ExportFormat.JSON
    overrides:         Dict[str, Any]    = Field(default_factory=dict, description="Manual field corrections")
    include_metadata:  bool              = True


class FieldFillResult(BaseModel):
    field:      str
    value:      Any
    source:     str          # "extracted" | "override" | "default" | "skipped"
    confidence: Optional[float] = None


class FillResult(BaseModel):
    job_id:         UUID
    export_format:  ExportFormat
    output_path:    str
    fields_filled:  int
    fields_skipped: int
    fields_overridden: int
    warnings:       List[str]
    field_results:  List[FieldFillResult]
    processing_ms:  int
