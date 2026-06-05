from __future__ import annotations
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional
from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base
from app.models.mixins import TimestampMixin
from app.models.enums import JobStatus

if TYPE_CHECKING:
    from app.models.batch import OCRBatch
    from app.models.extracted_data import ExtractedData
    from app.models.file import UploadedFile
    from app.models.user import User
    from app.models.template import ExtractionTemplate


class OCRJob(Base, TimestampMixin):
    __tablename__ = "ocr_jobs"
    __table_args__ = (
        CheckConstraint(f"status IN ({','.join(repr(s.value) for s in JobStatus)})", name="ck_ocr_jobs_status"),
        CheckConstraint("retry_count >= 0",              name="ck_ocr_jobs_retry_non_neg"),
        CheckConstraint("retry_count <= max_retries",    name="ck_ocr_jobs_retry_lte_max"),
        Index("ix_ocr_jobs_file_id",       "file_id"),
        Index("ix_ocr_jobs_batch_id",      "batch_id"),
        Index("ix_ocr_jobs_created_by_id", "created_by_id"),
        Index("ix_ocr_jobs_status",        "status"),
        Index("ix_ocr_jobs_queued_at",     "queued_at"),
        Index("ix_ocr_jobs_org_id",        "org_id"),
    )

    id:              Mapped[uuid.UUID]       = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    file_id:         Mapped[uuid.UUID]       = mapped_column(UUID(as_uuid=True), ForeignKey("uploaded_files.id", ondelete="CASCADE"), nullable=False)
    batch_id:        Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("ocr_batches.id", ondelete="SET NULL"), nullable=True)
    created_by_id:   Mapped[uuid.UUID]       = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    org_id:          Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("organisations.id", ondelete="SET NULL"), nullable=True)
    template_id:     Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("extraction_templates.id", ondelete="SET NULL"), nullable=True)
    status:          Mapped[str]             = mapped_column(String(20), nullable=False, default=JobStatus.QUEUED.value)
    priority:        Mapped[int]             = mapped_column(Integer, nullable=False, default=5)
    retry_count:     Mapped[int]             = mapped_column(Integer, nullable=False, default=0)
    max_retries:     Mapped[int]             = mapped_column(Integer, nullable=False, default=3)
    queued_at:       Mapped[datetime]        = mapped_column(nullable=False)
    started_at:      Mapped[Optional[datetime]] = mapped_column(nullable=True)
    completed_at:    Mapped[Optional[datetime]] = mapped_column(nullable=True)
    processing_ms:   Mapped[Optional[int]]   = mapped_column(Integer, nullable=True)
    worker_id:       Mapped[Optional[str]]   = mapped_column(String(255), nullable=True)
    celery_task_id:  Mapped[Optional[str]]   = mapped_column(String(255), nullable=True)
    ocr_config:      Mapped[dict]            = mapped_column(JSONB, nullable=False, default=dict)
    page_count:      Mapped[Optional[int]]   = mapped_column(Integer, nullable=True)
    avg_confidence:  Mapped[Optional[float]] = mapped_column(Numeric(5, 4), nullable=True)
    total_words:     Mapped[Optional[int]]   = mapped_column(Integer, nullable=True)
    error_message:   Mapped[Optional[str]]   = mapped_column(Text, nullable=True)
    error_traceback: Mapped[Optional[str]]   = mapped_column(Text, nullable=True)

    file:           Mapped["UploadedFile"]          = relationship("UploadedFile", back_populates="ocr_jobs", lazy="raise")
    batch:          Mapped[Optional["OCRBatch"]]    = relationship("OCRBatch", back_populates="ocr_jobs", lazy="raise")
    created_by:     Mapped["User"]                  = relationship("User", back_populates="ocr_jobs", foreign_keys=[created_by_id], lazy="raise")
    events:         Mapped[List["JobEvent"]]         = relationship("JobEvent", back_populates="job", cascade="all, delete-orphan", order_by="JobEvent.occurred_at", lazy="raise")
    extracted_data: Mapped[Optional["ExtractedData"]] = relationship("ExtractedData", back_populates="job", uselist=False, lazy="raise")

    def __repr__(self) -> str: return f"<OCRJob id={self.id} status={self.status!r}>"


class JobEvent(Base):
    __tablename__ = "job_events"
    __table_args__ = (Index("ix_job_events_job_id", "job_id"), Index("ix_job_events_occurred_at", "occurred_at"))

    id:          Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id:      Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("ocr_jobs.id", ondelete="CASCADE"), nullable=False)
    event_type:  Mapped[str]       = mapped_column(String(50), nullable=False)
    occurred_at: Mapped[datetime]  = mapped_column(nullable=False)
    message:     Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    payload:     Mapped[dict]      = mapped_column(JSONB, nullable=False, default=dict)

    job: Mapped["OCRJob"] = relationship("OCRJob", back_populates="events", lazy="raise")
