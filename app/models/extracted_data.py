from __future__ import annotations
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base
from app.models.mixins import TimestampMixin
from app.models.enums import ExtractionStatus

if TYPE_CHECKING:
    from app.models.job import OCRJob
    from app.models.template import ExtractionTemplate
    from app.models.fill_job import FormFillJob


class ExtractedData(Base, TimestampMixin):
    """
    Structured extraction result for one OCR job.

    Schema
    ──────
    raw_ocr_json      : verbatim OCR engine output (bboxes, confidence per line)
    extracted_fields  : field-name → {raw, value, confidence, method} mapping
    validated_fields  : post-validation / post-correction version
    mapped_output     : final template-mapped key/value pairs used for form-filling
    line_items        : parsed table rows (list of dicts)
    validation_errors : [{field, error, value}]
    """
    __tablename__ = "extracted_data"
    __table_args__ = (
        CheckConstraint(f"status IN ({','.join(repr(s.value) for s in ExtractionStatus)})", name="ck_extracted_data_status"),
        Index("ix_extracted_data_job_id",      "job_id"),
        Index("ix_extracted_data_file_id",     "file_id"),
        Index("ix_extracted_data_template_id", "template_id"),
        Index("ix_extracted_data_status",      "status"),
        Index("ix_extracted_data_created_at",  "created_at"),
    )

    id:          Mapped[uuid.UUID]       = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id:      Mapped[uuid.UUID]       = mapped_column(UUID(as_uuid=True), ForeignKey("ocr_jobs.id", ondelete="CASCADE"),       nullable=False, unique=True)
    file_id:     Mapped[uuid.UUID]       = mapped_column(UUID(as_uuid=True), ForeignKey("uploaded_files.id", ondelete="CASCADE"), nullable=False)
    template_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("extraction_templates.id", ondelete="SET NULL"), nullable=True)
    org_id:      Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("organisations.id",  ondelete="SET NULL"), nullable=True)

    # Raw OCR output
    raw_ocr_json:     Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    raw_text:         Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    page_count:       Mapped[int]  = mapped_column(Integer, nullable=False, default=0)

    # Extraction layers
    extracted_fields:  Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    validated_fields:  Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    mapped_output:     Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    line_items:        Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    # Quality
    overall_confidence:       Mapped[Optional[float]] = mapped_column(Numeric(5, 4), nullable=True)
    validation_errors:        Mapped[list]            = mapped_column(JSONB, nullable=False, default=list)
    fields_extracted:         Mapped[int]             = mapped_column(Integer, nullable=False, default=0)
    fields_high_confidence:   Mapped[int]             = mapped_column(Integer, nullable=False, default=0)
    fields_needing_review:    Mapped[int]             = mapped_column(Integer, nullable=False, default=0)

    # State
    status:            Mapped[str]             = mapped_column(String(20), nullable=False, default=ExtractionStatus.PENDING.value)
    reviewer_notes:    Mapped[Optional[str]]   = mapped_column(Text, nullable=True)
    reviewed_at:       Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_by_id:    Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    extractor_version: Mapped[str]             = mapped_column(String(20), nullable=False, default="2.0.0")

    job:       Mapped["OCRJob"]               = relationship("OCRJob", back_populates="extracted_data", lazy="raise")
    template:  Mapped[Optional["ExtractionTemplate"]] = relationship("ExtractionTemplate", back_populates="extractions", lazy="raise")
    fill_jobs: Mapped[List["FormFillJob"]]    = relationship("FormFillJob", back_populates="extracted_data", lazy="raise")

    def __repr__(self) -> str: return f"<ExtractedData job_id={self.job_id} status={self.status!r} conf={self.overall_confidence}>"
