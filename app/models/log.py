from __future__ import annotations
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base
from app.models.enums import LogEventType, LogLevel

if TYPE_CHECKING:
    from app.models.user import User


class AuditLog(Base):
    """
    Append-only audit trail. Never updated or soft-deleted.

    resource_type / resource_id: polymorphic pointer (e.g. "ocr_job" / "<uuid>")
    diff:    before/after snapshot for mutation events
    request_id: correlates with X-Request-ID header for distributed tracing
    """
    __tablename__ = "audit_logs"
    __table_args__ = (
        CheckConstraint(f"level IN ({','.join(repr(l.value) for l in LogLevel)})", name="ck_audit_logs_level"),
        CheckConstraint(f"event_type IN ({','.join(repr(e.value) for e in LogEventType)})", name="ck_audit_logs_event_type"),
        Index("ix_audit_logs_user_id",      "user_id"),
        Index("ix_audit_logs_org_id",       "org_id"),
        Index("ix_audit_logs_event_type",   "event_type"),
        Index("ix_audit_logs_resource",     "resource_type", "resource_id"),
        Index("ix_audit_logs_occurred_at",  "occurred_at"),
        Index("ix_audit_logs_request_id",   "request_id"),
    )

    id:            Mapped[uuid.UUID]       = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id:       Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    org_id:        Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    event_type:    Mapped[str]             = mapped_column(String(50), nullable=False)
    level:         Mapped[str]             = mapped_column(String(20), nullable=False, default=LogLevel.INFO.value)
    action:        Mapped[str]             = mapped_column(String(100), nullable=False)
    resource_type: Mapped[Optional[str]]   = mapped_column(String(100), nullable=True)
    resource_id:   Mapped[Optional[str]]   = mapped_column(String(255), nullable=True)
    message:       Mapped[Optional[str]]   = mapped_column(Text, nullable=True)
    diff:          Mapped[dict]            = mapped_column(JSONB, nullable=False, default=dict)
    metadata_:     Mapped[dict]            = mapped_column("metadata", JSONB, nullable=False, default=dict)
    ip_address:    Mapped[Optional[str]]   = mapped_column(INET, nullable=True)
    user_agent:    Mapped[Optional[str]]   = mapped_column(Text, nullable=True)
    request_id:    Mapped[Optional[str]]   = mapped_column(String(64), nullable=True)
    occurred_at:   Mapped[datetime]        = mapped_column(DateTime(timezone=True), nullable=False)

    user: Mapped[Optional["User"]] = relationship("User", back_populates="audit_logs", lazy="raise")

    def __repr__(self) -> str: return f"<AuditLog event={self.event_type!r} action={self.action!r}>"


class SystemLog(Base):
    """
    High-volume application logs (structured, NOT audit).
    Partitioned by month in production (partition key = occurred_at).
    """
    __tablename__ = "system_logs"
    __table_args__ = (
        CheckConstraint(f"level IN ({','.join(repr(l.value) for l in LogLevel)})", name="ck_system_logs_level"),
        Index("ix_system_logs_level",       "level"),
        Index("ix_system_logs_service",     "service"),
        Index("ix_system_logs_occurred_at", "occurred_at"),
        Index("ix_system_logs_trace_id",    "trace_id"),
    )

    id:          Mapped[uuid.UUID]     = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    level:       Mapped[str]           = mapped_column(String(20),  nullable=False)
    service:     Mapped[str]           = mapped_column(String(100), nullable=False, default="api")
    message:     Mapped[str]           = mapped_column(Text,        nullable=False)
    logger_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    module:      Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    line_number: Mapped[Optional[int]] = mapped_column(nullable=True)
    exception:   Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    extra:       Mapped[dict]          = mapped_column(JSONB, nullable=False, default=dict)
    trace_id:    Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    span_id:     Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    occurred_at: Mapped[datetime]      = mapped_column(DateTime(timezone=True), nullable=False)

    def __repr__(self) -> str: return f"<SystemLog level={self.level!r} service={self.service!r}>"
