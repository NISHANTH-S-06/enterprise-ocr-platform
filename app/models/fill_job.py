from __future__ import annotations
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional
from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base
from app.models.mixins import TimestampMixin
from app.models.enums import ExportFormat, FillJobStatus

if TYPE_CHECKING:
    from app.models.extracted_data import ExtractedData
    from app.models.template import ExtractionTemplate
    from app.models.user import User


class FormFillJob(Base, TimestampMixin):
    """
    One fill job = one ExtractedData mapped into one output document.

    output_path   : final file location (relative to OUTPUT_DIR)
    export_format : pdf | excel | json
    fill_overrides: {field_name: value}  — manual corrections applied before fill
    fill_result   : {fields_filled, fields_skipped, warnings}
    """
    __tablename__ = "form_fill_jobs"
    __table_args__ = (
        CheckConstraint(f"status IN ({','.join(repr(s.value) for s in FillJobStatus)})", name="ck_form_fill_jobs_status"),
        CheckConstraint(f"export_format IN ({','.join(repr(f.value) for f in ExportFormat)})", name="ck_form_fill_jobs_format"),
        Index("ix_form_fill_jobs_extracted_data_id", "extracted_data_id"),
        Index("ix_form_fill_jobs_template_id",       "template_id"),
        Index("ix_form_fill_jobs_created_by_id",     "created_by_id"),
        Index("ix_form_fill_jobs_status",            "status"),
        Index("ix_form_fill_jobs_created_at",        "created_at"),
    )

    id:                Mapped[uuid.UUID]       = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    extracted_data_id: Mapped[uuid.UUID]       = mapped_column(UUID(as_uuid=True), ForeignKey("extracted_data.id", ondelete="CASCADE"), nullable=False)
    template_id:       Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("extraction_templates.id", ondelete="SET NULL"), nullable=True)
    created_by_id:     Mapped[uuid.UUID]       = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    org_id:            Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("organisations.id", ondelete="SET NULL"), nullable=True)

    export_format:  Mapped[str]             = mapped_column(String(20), nullable=False)
    status:         Mapped[str]             = mapped_column(String(20), nullable=False, default=FillJobStatus.PENDING.value)
    output_path:    Mapped[Optional[str]]   = mapped_column(Text, nullable=True)
    output_size:    Mapped[Optional[int]]   = mapped_column(Integer, nullable=True)
    fill_overrides: Mapped[dict]            = mapped_column(JSONB, nullable=False, default=dict)
    fill_result:    Mapped[dict]            = mapped_column(JSONB, nullable=False, default=dict)
    processing_ms:  Mapped[Optional[int]]   = mapped_column(Integer, nullable=True)
    error_message:  Mapped[Optional[str]]   = mapped_column(Text, nullable=True)
    started_at:     Mapped[Optional[datetime]] = mapped_column(nullable=True)
    completed_at:   Mapped[Optional[datetime]] = mapped_column(nullable=True)

    extracted_data: Mapped["ExtractedData"]          = relationship("ExtractedData", back_populates="fill_jobs", lazy="raise")
    template:       Mapped[Optional["ExtractionTemplate"]] = relationship("ExtractionTemplate", back_populates="fill_jobs", lazy="raise")
    created_by:     Mapped["User"]                   = relationship("User", back_populates="fill_jobs", lazy="raise")

    def __repr__(self) -> str: return f"<FormFillJob id={self.id} format={self.export_format!r} status={self.status!r}>"
