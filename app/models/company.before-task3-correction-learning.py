"""Company master data models — Phase 1.

Tables: companies, company_templates, field_mappings, training_datasets,
        correction_memory, validation_results, document_quality_reports
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Float, ForeignKey,
    Float, Index, Integer, JSON, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import TimestampMixin


# ---------------------------------------------------------------------------
# Company
# ---------------------------------------------------------------------------

class Company(Base, TimestampMixin):
    __tablename__ = "companies"
    __table_args__ = (
        UniqueConstraint("gst_number", name="uq_companies_gst"),
        Index("ix_companies_gst_number", "gst_number"),
        Index("ix_companies_company_name", "company_name"),
    )

    id:                   Mapped[uuid.UUID]        = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_name:         Mapped[str]              = mapped_column(String(255), nullable=False)
    gst_number:           Mapped[Optional[str]]    = mapped_column(String(20),  nullable=True)
    iec_code:             Mapped[Optional[str]]    = mapped_column(String(20),  nullable=True)
    address:              Mapped[Optional[str]]    = mapped_column(Text,        nullable=True)
    bank_name:            Mapped[Optional[str]]    = mapped_column(String(255), nullable=True)
    account_number:       Mapped[Optional[str]]    = mapped_column(String(50),  nullable=True)
    ifsc_code:            Mapped[Optional[str]]    = mapped_column(String(20),  nullable=True)
    swift_code:           Mapped[Optional[str]]    = mapped_column(String(20),  nullable=True)
    default_currency:     Mapped[str]              = mapped_column(String(10),  nullable=False, default="INR")
    default_tax_type:     Mapped[Optional[str]]    = mapped_column(String(50),  nullable=True)
    default_port_loading: Mapped[Optional[str]]    = mapped_column(String(255), nullable=True)
    default_port_discharge: Mapped[Optional[str]]  = mapped_column(String(255), nullable=True)
    is_active:            Mapped[bool]             = mapped_column(Boolean,     nullable=False, default=True)

    templates:  Mapped[List["CompanyTemplate"]]  = relationship("CompanyTemplate",  back_populates="company", cascade="all, delete-orphan", lazy="raise")
    corrections: Mapped[List["CorrectionMemory"]] = relationship("CorrectionMemory", back_populates="company", cascade="all, delete-orphan", lazy="raise")

    def __repr__(self) -> str:
        return f"<Company name={self.company_name!r} gst={self.gst_number!r}>"


# ---------------------------------------------------------------------------
# CompanyTemplate
# ---------------------------------------------------------------------------

class CompanyTemplate(Base, TimestampMixin):
    __tablename__ = "company_templates"
    __table_args__ = (
        Index("ix_company_templates_company_id", "company_id"),
        Index("ix_company_templates_doc_type",   "document_type"),
        CheckConstraint(
            "document_type IN ('export_invoice','commercial_invoice','purchase_order','grn','sales_invoice','general')",
            name="ck_company_templates_doc_type",
        ),
    )

    id:               Mapped[uuid.UUID]     = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id:       Mapped[uuid.UUID]     = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    document_type:    Mapped[str]           = mapped_column(String(50),  nullable=False)
    template_name:    Mapped[str]           = mapped_column(String(255), nullable=False)
    template_version: Mapped[str]           = mapped_column(String(20),  nullable=False, default="1.0")
    is_active:        Mapped[bool]          = mapped_column(Boolean,     nullable=False, default=True)

    company:        Mapped["Company"]              = relationship("Company",       back_populates="templates",   lazy="raise")
    field_mappings: Mapped[List["FieldMapping"]]   = relationship("FieldMapping",  back_populates="template",    cascade="all, delete-orphan", lazy="raise")
    corrections:    Mapped[List["CorrectionMemory"]] = relationship("CorrectionMemory", back_populates="template", lazy="raise")

    def __repr__(self) -> str:
        return f"<CompanyTemplate name={self.template_name!r} type={self.document_type!r}>"


# ---------------------------------------------------------------------------
# FieldMapping
# ---------------------------------------------------------------------------

class FieldMapping(Base, TimestampMixin):
    __tablename__ = "field_mappings"
    __table_args__ = (
        Index("ix_field_mappings_template_id", "template_id"),
        Index("ix_field_mappings_field_name",   "field_name"),
        CheckConstraint(
            "method IN ('regex','keyword_proximity','table_cell','fixed_position','llm_extract','manual')",
            name="ck_field_mappings_method",
        ),
    )

    id:             Mapped[uuid.UUID]     = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    template_id:    Mapped[uuid.UUID]     = mapped_column(UUID(as_uuid=True), ForeignKey("company_templates.id", ondelete="CASCADE"), nullable=False)
    field_name:     Mapped[str]           = mapped_column(String(100), nullable=False)
    label_text:     Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    method:         Mapped[str]           = mapped_column(String(50),  nullable=False, default="regex")
    regex_pattern:  Mapped[Optional[str]] = mapped_column(Text,        nullable=True)
    section_name:   Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    priority:       Mapped[int]           = mapped_column(Integer,     nullable=False, default=50)
    required:       Mapped[bool]          = mapped_column(Boolean,     nullable=False, default=False)

    template: Mapped["CompanyTemplate"] = relationship("CompanyTemplate", back_populates="field_mappings", lazy="raise")

    def __repr__(self) -> str:
        return f"<FieldMapping field={self.field_name!r} method={self.method!r}>"


# ---------------------------------------------------------------------------
# OCR Training Dataset
# ---------------------------------------------------------------------------

class TrainingDataset(Base, TimestampMixin):
    __tablename__ = "training_datasets"
    __table_args__ = (
        Index("ix_training_datasets_org_id", "org_id"),
        Index("ix_training_datasets_module", "module"),
        Index("ix_training_datasets_document_type", "document_type"),
        Index("ix_training_datasets_active", "is_active"),
        UniqueConstraint(
            "org_id", "dataset_name", "module", "document_type",
            name="uq_training_datasets_scope_name",
        ),
        CheckConstraint(
            "module IN ('sales_order','purchase_order')",
            name="ck_training_datasets_module",
        ),
    )

    id:                Mapped[str]           = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    org_id:            Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    dataset_name:      Mapped[str]           = mapped_column(String(255), nullable=False)
    module:            Mapped[str]           = mapped_column(String(50), nullable=False)
    document_type:     Mapped[str]           = mapped_column(String(100), nullable=False)
    company_name:      Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    vendor_name:       Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    customer_name:     Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_active:         Mapped[bool]          = mapped_column(Boolean, nullable=False, default=True)
    version:           Mapped[int]           = mapped_column(Integer, nullable=False, default=1)
    mappings_json:     Mapped[list]          = mapped_column(JSONB, nullable=False, default=list)
    row_mappings_json: Mapped[list]          = mapped_column(JSONB, nullable=False, default=list)
    metadata_json:     Mapped[dict]          = mapped_column(JSONB, nullable=False, default=dict)

    def __repr__(self) -> str:
        return f"<TrainingDataset name={self.dataset_name!r} module={self.module!r} version={self.version}>"


# ---------------------------------------------------------------------------
# ERP Master Mapping
# ---------------------------------------------------------------------------

class ERPMasterMapping(Base, TimestampMixin):
    __tablename__ = "erp_master_mappings"
    __table_args__ = (
        Index("ix_erp_master_mappings_org_id", "org_id"),
        Index("ix_erp_master_mappings_lookup", "master_type", "normalized_text", "module"),
        Index("ix_erp_master_mappings_active", "is_active"),
        UniqueConstraint("org_id", "master_type", "normalized_text", "module", "company_name", name="uq_erp_master_mapping_scope"),
        CheckConstraint(
            "master_type IN ('customer','supplier','product','tax_group','customer_site','supplier_site')",
            name="ck_erp_master_mappings_type",
        ),
    )

    id:              Mapped[uuid.UUID]     = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id:          Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    master_type:     Mapped[str]           = mapped_column(String(32), nullable=False)
    detected_text:   Mapped[str]           = mapped_column(String(500), nullable=False)
    normalized_text: Mapped[str]           = mapped_column(String(500), nullable=False)
    erp_id:          Mapped[str]           = mapped_column(String(255), nullable=False)
    erp_code:        Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    erp_name:        Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    module:          Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    company_name:    Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    vendor_name:     Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    customer_name:   Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_active:       Mapped[bool]          = mapped_column(Boolean, nullable=False, default=True)
    confidence:      Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    source:          Mapped[str]           = mapped_column(String(32), nullable=False, default="manual")
    metadata_json:   Mapped[dict]          = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False, default=dict)


# ---------------------------------------------------------------------------
# CorrectionMemory
# ---------------------------------------------------------------------------

class CorrectionMemory(Base, TimestampMixin):
    __tablename__ = "correction_memory"
    __table_args__ = (
        Index("ix_correction_memory_company_id",   "company_id"),
        Index("ix_correction_memory_template_id",  "template_id"),
        Index("ix_correction_memory_field_name",   "field_name"),
        Index("ix_correction_memory_document_type","document_type"),
    )

    id:             Mapped[uuid.UUID]     = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id:     Mapped[uuid.UUID]     = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    template_id:    Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("company_templates.id", ondelete="SET NULL"), nullable=True)
    document_type:  Mapped[Optional[str]] = mapped_column(String(50),  nullable=True)
    field_name:     Mapped[str]           = mapped_column(String(100), nullable=False)
    old_value:      Mapped[Optional[str]] = mapped_column(Text,        nullable=True)
    correct_value:  Mapped[str]           = mapped_column(Text,        nullable=False)
    evidence_text:  Mapped[Optional[str]] = mapped_column(Text,        nullable=True)
    rule_suggestion: Mapped[Optional[str]] = mapped_column(Text,       nullable=True)

    company:  Mapped["Company"]                   = relationship("Company",         back_populates="corrections", lazy="raise")
    template: Mapped[Optional["CompanyTemplate"]] = relationship("CompanyTemplate", back_populates="corrections", lazy="raise")

    def __repr__(self) -> str:
        return f"<CorrectionMemory field={self.field_name!r} company_id={self.company_id}>"


# ---------------------------------------------------------------------------
# ValidationResult
# ---------------------------------------------------------------------------

class ValidationResult(Base, TimestampMixin):
    __tablename__ = "validation_results"
    __table_args__ = (
        Index("ix_validation_results_document_id", "document_id"),
        Index("ix_validation_results_field_name",  "field_name"),
        Index("ix_validation_results_status",      "status"),
        CheckConstraint(
            "status IN ('pass','fail','warning','skipped')",
            name="ck_validation_results_status",
        ),
        CheckConstraint(
            "severity IN ('critical','high','medium','low','info')",
            name="ck_validation_results_severity",
        ),
    )

    id:          Mapped[uuid.UUID]     = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[str]           = mapped_column(String(255), nullable=False)   # uploaded_file id or job id (loose FK for flexibility)
    field_name:  Mapped[str]           = mapped_column(String(100), nullable=False)
    rule_name:   Mapped[str]           = mapped_column(String(100), nullable=False)
    status:      Mapped[str]           = mapped_column(String(20),  nullable=False, default="fail")
    message:     Mapped[Optional[str]] = mapped_column(Text,        nullable=True)
    severity:    Mapped[str]           = mapped_column(String(20),  nullable=False, default="medium")

    def __repr__(self) -> str:
        return f"<ValidationResult doc={self.document_id!r} field={self.field_name!r} status={self.status!r}>"


# ---------------------------------------------------------------------------
# DocumentQualityReport
# ---------------------------------------------------------------------------

class DocumentQualityReport(Base, TimestampMixin):
    __tablename__ = "document_quality_reports"
    __table_args__ = (
        Index("ix_doc_quality_document_id", "document_id"),
        CheckConstraint(
            "quality_status IN ('good','acceptable','poor','unreadable')",
            name="ck_doc_quality_status",
        ),
        CheckConstraint(
            "resolution_status IN ('high','medium','low','unknown')",
            name="ck_doc_quality_resolution",
        ),
    )

    id:                Mapped[uuid.UUID]      = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id:       Mapped[str]            = mapped_column(String(255), nullable=False)
    quality_status:    Mapped[str]            = mapped_column(String(20),  nullable=False, default="acceptable")
    blur_score:        Mapped[Optional[float]]= mapped_column(Float,       nullable=True)
    resolution_status: Mapped[str]            = mapped_column(String(20),  nullable=False, default="unknown")
    rotation_angle:    Mapped[Optional[float]]= mapped_column(Float,       nullable=True)
    warnings_json:     Mapped[Optional[dict]] = mapped_column(JSONB,       nullable=True)
    can_continue:      Mapped[bool]           = mapped_column(Boolean,     nullable=False, default=True)

    def __repr__(self) -> str:
        return f"<DocumentQualityReport doc={self.document_id!r} status={self.quality_status!r}>"
