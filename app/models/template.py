from __future__ import annotations
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional
from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base
from app.models.mixins import SoftDeleteMixin, TimestampMixin
from app.models.enums import DocumentType, TemplateStatus

if TYPE_CHECKING:
    from app.models.extracted_data import ExtractedData
    from app.models.user import Organisation
    from app.models.fill_job import FormFillJob


class ExtractionTemplate(Base, TimestampMixin, SoftDeleteMixin):
    """
    Field extraction + form-fill template.

    field_schema      : {field_name: {type, required, aliases, description, validation}}
    validation_rules  : {field_name: {regex, min, max, ...}}
    mapping_config    : {target_field: source_field}  — map extracted → output keys
    fill_config       : {output_field: {cell/coord, format, transform}}  — Excel/PDF coords
    sample_values     : {field_name: example_value}  — for UI preview
    """
    __tablename__ = "extraction_templates"
    __table_args__ = (
        CheckConstraint(f"status IN ({','.join(repr(s.value) for s in TemplateStatus)})", name="ck_extraction_templates_status"),
        CheckConstraint(f"document_type IN ({','.join(repr(d.value) for d in DocumentType)})", name="ck_extraction_templates_doc_type"),
        UniqueConstraint("org_id", "name", "version", name="uq_extraction_templates_org_name_version"),
        Index("ix_extraction_templates_org_id",       "org_id"),
        Index("ix_extraction_templates_document_type","document_type"),
        Index("ix_extraction_templates_status",       "status"),
    )

    id:            Mapped[uuid.UUID]       = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id:        Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("organisations.id", ondelete="CASCADE"), nullable=True)
    created_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    parent_id:     Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("extraction_templates.id", ondelete="SET NULL"), nullable=True)

    name:          Mapped[str]  = mapped_column(String(255), nullable=False)
    description:   Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    document_type: Mapped[str]  = mapped_column(String(50),  nullable=False, default=DocumentType.INVOICE.value)
    status:        Mapped[str]  = mapped_column(String(20),  nullable=False, default=TemplateStatus.DRAFT.value)
    version:       Mapped[int]  = mapped_column(Integer,     nullable=False, default=1)
    is_global:     Mapped[bool] = mapped_column(Boolean,     nullable=False, default=False)  # system-wide default

    # Extraction config
    field_schema:     Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    validation_rules: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    mapping_config:   Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # Form-fill config (used by FormFillEngine)
    fill_config:   Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    sample_values: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # Template file (optional base PDF/XLSX to fill)
    template_file_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    template_file_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    organisation: Mapped[Optional["Organisation"]]  = relationship("Organisation", back_populates="templates", lazy="raise")
    parent:       Mapped[Optional["ExtractionTemplate"]] = relationship("ExtractionTemplate", remote_side="ExtractionTemplate.id", lazy="raise")
    extractions:  Mapped[List["ExtractedData"]]     = relationship("ExtractedData", back_populates="template", lazy="raise")
    fill_jobs:    Mapped[List["FormFillJob"]]        = relationship("FormFillJob", back_populates="template", lazy="raise")

    def __repr__(self) -> str: return f"<ExtractionTemplate name={self.name!r} v{self.version} status={self.status!r}>"
