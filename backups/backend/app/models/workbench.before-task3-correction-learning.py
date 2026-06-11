from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import Boolean, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.mixins import TimestampMixin


class WorkbenchDraft(Base, TimestampMixin):
    __tablename__ = "workbench_drafts"
    __table_args__ = (
        Index("ix_workbench_drafts_org_id", "org_id"),
        Index("ix_workbench_drafts_created_by_id", "created_by_id"),
        Index("ix_workbench_drafts_module", "module"),
        Index("ix_workbench_drafts_source_job_id", "source_job_id"),
    )

    id:                Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id:            Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("organisations.id", ondelete="SET NULL"), nullable=True)
    created_by_id:     Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    module:            Mapped[str] = mapped_column(String(50), nullable=False)
    document_type:     Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    dataset_id:        Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    source_job_id:     Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    form_data:         Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    item_rows:         Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    validation_result: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    metadata_json:     Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    is_active:         Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
