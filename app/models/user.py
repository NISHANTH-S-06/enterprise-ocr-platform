from __future__ import annotations
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional
from sqlalchemy import BigInteger, Boolean, CheckConstraint, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base
from app.models.mixins import SoftDeleteMixin, TimestampMixin
from app.models.enums import UserRole, UserStatus

if TYPE_CHECKING:
    from app.models.batch import OCRBatch
    from app.models.file import UploadedFile
    from app.models.job import OCRJob
    from app.models.log import AuditLog
    from app.models.template import ExtractionTemplate
    from app.models.fill_job import FormFillJob


class Organisation(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "organisations"
    __table_args__ = (UniqueConstraint("slug"), Index("ix_organisations_slug", "slug"))

    id:         Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name:       Mapped[str]       = mapped_column(String(255), nullable=False)
    slug:       Mapped[str]       = mapped_column(String(100), nullable=False)
    is_active:  Mapped[bool]      = mapped_column(Boolean, nullable=False, default=True)
    max_users:  Mapped[int]       = mapped_column(Integer, nullable=False, default=50)
    max_storage_mb: Mapped[int]   = mapped_column(BigInteger, nullable=False, default=10_240)
    settings:   Mapped[dict]      = mapped_column(JSONB, nullable=False, default=dict)

    users:     Mapped[List["User"]]               = relationship("User", back_populates="organisation", lazy="raise")
    templates: Mapped[List["ExtractionTemplate"]] = relationship("ExtractionTemplate", back_populates="organisation", lazy="raise")

    def __repr__(self) -> str: return f"<Organisation slug={self.slug!r}>"


class User(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email", name="uq_users_email"),
        CheckConstraint(f"role IN ({','.join(repr(r.value) for r in UserRole)})", name="ck_users_role"),
        CheckConstraint(f"status IN ({','.join(repr(s.value) for s in UserStatus)})", name="ck_users_status"),
        Index("ix_users_email", "email"), Index("ix_users_org_id", "org_id"),
        Index("ix_users_role", "role"),   Index("ix_users_status", "status"),
    )

    id:               Mapped[uuid.UUID]       = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email:            Mapped[str]             = mapped_column(String(255), nullable=False)
    hashed_password:  Mapped[str]             = mapped_column(String(255), nullable=False)
    full_name:        Mapped[str]             = mapped_column(String(255), nullable=False)
    role:             Mapped[str]             = mapped_column(String(50),  nullable=False, default=UserRole.OPERATOR.value)
    status:           Mapped[str]             = mapped_column(String(50),  nullable=False, default=UserStatus.PENDING.value)
    org_id:           Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("organisations.id", ondelete="SET NULL"), nullable=True)
    is_email_verified: Mapped[bool]           = mapped_column(Boolean, nullable=False, default=False)
    last_login_at:    Mapped[Optional[datetime]] = mapped_column(nullable=True)
    last_login_ip:    Mapped[Optional[str]]   = mapped_column(INET, nullable=True)
    failed_login_count: Mapped[int]           = mapped_column(Integer, nullable=False, default=0)
    preferences:      Mapped[dict]            = mapped_column(JSONB, nullable=False, default=dict)

    organisation: Mapped[Optional["Organisation"]]   = relationship("Organisation", back_populates="users", lazy="raise")
    sessions:     Mapped[List["UserSession"]]        = relationship("UserSession", back_populates="user", cascade="all, delete-orphan", lazy="raise")
    api_keys:     Mapped[List["ApiKey"]]             = relationship("ApiKey", back_populates="user", cascade="all, delete-orphan", lazy="raise")
    uploaded_files: Mapped[List["UploadedFile"]]     = relationship("UploadedFile", back_populates="uploaded_by", foreign_keys="UploadedFile.uploaded_by_id", lazy="raise")
    ocr_jobs:     Mapped[List["OCRJob"]]             = relationship("OCRJob", back_populates="created_by", foreign_keys="OCRJob.created_by_id", lazy="raise")
    audit_logs:   Mapped[List["AuditLog"]]           = relationship("AuditLog", back_populates="user", lazy="raise")
    fill_jobs:    Mapped[List["FormFillJob"]]         = relationship("FormFillJob", back_populates="created_by", lazy="raise")
    ocr_batches:  Mapped[List["OCRBatch"]]            = relationship("OCRBatch", back_populates="created_by", lazy="raise")

    def __repr__(self) -> str: return f"<User email={self.email!r} role={self.role!r}>"


class UserSession(Base, TimestampMixin):
    __tablename__ = "user_sessions"
    __table_args__ = (Index("ix_user_sessions_user_id", "user_id"), Index("ix_user_sessions_token_hash", "token_hash"))

    id:          Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id:     Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash:  Mapped[str]       = mapped_column(String(64), nullable=False, unique=True)
    user_agent:  Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ip_address:  Mapped[Optional[str]] = mapped_column(INET, nullable=True)
    expires_at:  Mapped[datetime]  = mapped_column(nullable=False)
    revoked_at:  Mapped[Optional[datetime]] = mapped_column(nullable=True)
    is_revoked:  Mapped[bool]      = mapped_column(Boolean, nullable=False, default=False)

    user: Mapped["User"] = relationship("User", back_populates="sessions", lazy="raise")


class ApiKey(Base, TimestampMixin):
    __tablename__ = "api_keys"
    __table_args__ = (Index("ix_api_keys_user_id", "user_id"), Index("ix_api_keys_key_hash", "key_hash"))

    id:       Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id:  Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name:     Mapped[str]       = mapped_column(String(255), nullable=False)
    key_hash: Mapped[str]       = mapped_column(String(64),  nullable=False, unique=True)
    prefix:   Mapped[str]       = mapped_column(String(8),   nullable=False)
    scopes:   Mapped[list]      = mapped_column(JSONB, nullable=False, default=list)
    expires_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    is_active: Mapped[bool]     = mapped_column(Boolean, nullable=False, default=True)

    user: Mapped["User"] = relationship("User", back_populates="api_keys", lazy="raise")
