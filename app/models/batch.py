from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import BatchStatus
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.file import UploadedFile
    from app.models.job import OCRJob
    from app.models.user import User


class OCRBatch(Base, TimestampMixin):
    __tablename__ = "ocr_batches"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({','.join(repr(s.value) for s in BatchStatus)})",
            name="ck_ocr_batches_status",
        ),
        CheckConstraint("file_count >= 0", name="ck_ocr_batches_file_count_non_neg"),
        CheckConstraint("completed_count >= 0", name="ck_ocr_batches_completed_count_non_neg"),
        CheckConstraint("failed_count >= 0", name="ck_ocr_batches_failed_count_non_neg"),
        CheckConstraint("needs_review_count >= 0", name="ck_ocr_batches_needs_review_count_non_neg"),
        CheckConstraint("validated_count >= 0", name="ck_ocr_batches_validated_count_non_neg"),
        CheckConstraint("total_bytes >= 0", name="ck_ocr_batches_total_bytes_non_neg"),
        CheckConstraint("total_pages >= 0", name="ck_ocr_batches_total_pages_non_neg"),
        Index("ix_ocr_batches_created_by_id", "created_by_id"),
        Index("ix_ocr_batches_org_id", "org_id"),
        Index("ix_ocr_batches_status", "status"),
        Index("ix_ocr_batches_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    org_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organisations.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=BatchStatus.QUEUED.value)
    file_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    needs_review_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    validated_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    total_pages: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_zip_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    started_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)

    created_by: Mapped["User"] = relationship(
        "User", back_populates="ocr_batches", foreign_keys=[created_by_id], lazy="raise"
    )
    uploaded_files: Mapped[list["UploadedFile"]] = relationship(
        "UploadedFile", back_populates="batch", lazy="raise"
    )
    ocr_jobs: Mapped[list["OCRJob"]] = relationship("OCRJob", back_populates="batch", lazy="raise")

    def __repr__(self) -> str:
        return f"<OCRBatch id={self.id} status={self.status!r} files={self.file_count}>"
