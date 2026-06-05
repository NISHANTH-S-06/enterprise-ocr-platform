from __future__ import annotations
import uuid
from typing import TYPE_CHECKING, List, Optional
from sqlalchemy import BigInteger, Boolean, CheckConstraint, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base
from app.models.mixins import SoftDeleteMixin, TimestampMixin
from app.models.enums import FileCategory, FileStatus

if TYPE_CHECKING:
    from app.models.batch import OCRBatch
    from app.models.job import OCRJob
    from app.models.user import User
    from app.models.template import ExtractionTemplate


class UploadedFile(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "uploaded_files"
    __table_args__ = (
        CheckConstraint(f"file_category IN ({','.join(repr(c.value) for c in FileCategory)})", name="ck_uploaded_files_file_category"),
        CheckConstraint(f"status IN ({','.join(repr(s.value) for s in FileStatus)})", name="ck_uploaded_files_status"),
        CheckConstraint("file_size > 0", name="ck_uploaded_files_file_size_positive"),
        Index("ix_uploaded_files_uploaded_by_id", "uploaded_by_id"),
        Index("ix_uploaded_files_batch_id",       "batch_id"),
        Index("ix_uploaded_files_org_id",         "org_id"),
        Index("ix_uploaded_files_status",         "status"),
        Index("ix_uploaded_files_checksum",       "checksum_sha256"),
        Index("ix_uploaded_files_created_at",     "created_at"),
    )

    id:                Mapped[uuid.UUID]       = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    uploaded_by_id:    Mapped[uuid.UUID]       = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    batch_id:          Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("ocr_batches.id", ondelete="SET NULL"), nullable=True)
    org_id:            Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("organisations.id", ondelete="SET NULL"), nullable=True)
    template_id:       Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("extraction_templates.id", ondelete="SET NULL"), nullable=True)
    original_filename: Mapped[str]             = mapped_column(String(500), nullable=False)
    stored_filename:   Mapped[str]             = mapped_column(String(500), nullable=False, unique=True)
    storage_path:      Mapped[str]             = mapped_column(Text,        nullable=False)
    mime_type:         Mapped[str]             = mapped_column(String(127), nullable=False)
    file_category:     Mapped[str]             = mapped_column(String(20),  nullable=False)
    file_size:         Mapped[int]             = mapped_column(BigInteger,  nullable=False)
    checksum_sha256:   Mapped[str]             = mapped_column(String(64),  nullable=False)
    page_count:        Mapped[Optional[int]]   = mapped_column(Integer,     nullable=True)
    width_px:          Mapped[Optional[int]]   = mapped_column(Integer,     nullable=True)
    height_px:         Mapped[Optional[int]]   = mapped_column(Integer,     nullable=True)
    document_type:     Mapped[Optional[str]]   = mapped_column(String(50),  nullable=True)
    language_hint:     Mapped[Optional[str]]   = mapped_column(String(10),  nullable=True)
    status:            Mapped[str]             = mapped_column(String(20),  nullable=False, default=FileStatus.PENDING.value)
    validation_error:  Mapped[Optional[str]]   = mapped_column(Text, nullable=True)
    quarantine_reason: Mapped[Optional[str]]   = mapped_column(Text, nullable=True)
    extra:             Mapped[dict]            = mapped_column(JSONB, nullable=False, default=dict)

    uploaded_by: Mapped["User"]              = relationship("User", back_populates="uploaded_files", foreign_keys=[uploaded_by_id], lazy="raise")
    batch:       Mapped[Optional["OCRBatch"]] = relationship("OCRBatch", back_populates="uploaded_files", lazy="raise")
    pages:       Mapped[List["FilePage"]]    = relationship("FilePage", back_populates="file", cascade="all, delete-orphan", order_by="FilePage.page_number", lazy="raise")
    ocr_jobs:    Mapped[List["OCRJob"]]      = relationship("OCRJob", back_populates="file", lazy="raise")

    def __repr__(self) -> str: return f"<UploadedFile name={self.original_filename!r} status={self.status!r}>"


class FilePage(Base, TimestampMixin):
    __tablename__ = "file_pages"
    __table_args__ = (Index("ix_file_pages_file_id", "file_id"), Index("ix_file_pages_page_number", "page_number"))

    id:          Mapped[uuid.UUID]     = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    file_id:     Mapped[uuid.UUID]     = mapped_column(UUID(as_uuid=True), ForeignKey("uploaded_files.id", ondelete="CASCADE"), nullable=False)
    page_number: Mapped[int]           = mapped_column(Integer, nullable=False)
    image_path:  Mapped[Optional[str]] = mapped_column(Text,    nullable=True)
    width_px:    Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    height_px:   Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    dpi:         Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    raw_text:    Mapped[Optional[str]] = mapped_column(Text,    nullable=True)
    confidence:  Mapped[Optional[float]] = mapped_column(Numeric(5, 4), nullable=True)
    word_count:  Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    file: Mapped["UploadedFile"] = relationship("UploadedFile", back_populates="pages", lazy="raise")
