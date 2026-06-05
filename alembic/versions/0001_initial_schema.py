"""initial schema

Revision ID: 0001
Revises:
Create Date: 2024-01-01 00:00:00
"""
from __future__ import annotations
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # organisations
    op.create_table("organisations",
        sa.Column("id",             postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name",           sa.String(255), nullable=False),
        sa.Column("slug",           sa.String(100), nullable=False, unique=True),
        sa.Column("is_active",      sa.Boolean,     nullable=False, server_default="true"),
        sa.Column("max_users",      sa.Integer,     nullable=False, server_default="50"),
        sa.Column("max_storage_mb", sa.BigInteger,  nullable=False, server_default="10240"),
        sa.Column("settings",       postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at",     sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at",     sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted_at",     sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted",     sa.Boolean, nullable=False, server_default="false"),
    )
    op.create_index("ix_organisations_slug", "organisations", ["slug"])

    # users
    op.create_table("users",
        sa.Column("id",                  postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email",               sa.String(255), nullable=False, unique=True),
        sa.Column("hashed_password",     sa.String(255), nullable=False),
        sa.Column("full_name",           sa.String(255), nullable=False),
        sa.Column("role",                sa.String(50),  nullable=False, server_default="operator"),
        sa.Column("status",              sa.String(50),  nullable=False, server_default="pending"),
        sa.Column("org_id",              postgresql.UUID(as_uuid=True), sa.ForeignKey("organisations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("is_email_verified",   sa.Boolean,     nullable=False, server_default="false"),
        sa.Column("last_login_at",       sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_ip",       postgresql.INET, nullable=True),
        sa.Column("failed_login_count",  sa.Integer,     nullable=False, server_default="0"),
        sa.Column("preferences",         postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at",          sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at",          sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted_at",          sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted",          sa.Boolean, nullable=False, server_default="false"),
        sa.CheckConstraint("role IN ('superadmin','admin','manager','operator','reviewer','viewer')", name="ck_users_role"),
        sa.CheckConstraint("status IN ('active','inactive','suspended','pending')", name="ck_users_status"),
    )
    op.create_index("ix_users_email",  "users", ["email"])
    op.create_index("ix_users_org_id", "users", ["org_id"])
    op.create_index("ix_users_role",   "users", ["role"])
    op.create_index("ix_users_status", "users", ["status"])

    # user_sessions
    op.create_table("user_sessions",
        sa.Column("id",          postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id",     postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash",  sa.String(64),  nullable=False, unique=True),
        sa.Column("user_agent",  sa.Text,        nullable=True),
        sa.Column("ip_address",  postgresql.INET, nullable=True),
        sa.Column("expires_at",  sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at",  sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_revoked",  sa.Boolean,     nullable=False, server_default="false"),
        sa.Column("created_at",  sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at",  sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_user_sessions_user_id",    "user_sessions", ["user_id"])
    op.create_index("ix_user_sessions_token_hash", "user_sessions", ["token_hash"])

    # api_keys
    op.create_table("api_keys",
        sa.Column("id",           postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id",      postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name",         sa.String(255), nullable=False),
        sa.Column("key_hash",     sa.String(64),  nullable=False, unique=True),
        sa.Column("prefix",       sa.String(8),   nullable=False),
        sa.Column("scopes",       postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("expires_at",   sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active",    sa.Boolean,     nullable=False, server_default="true"),
        sa.Column("created_at",   sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at",   sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_api_keys_user_id",  "api_keys", ["user_id"])
    op.create_index("ix_api_keys_key_hash", "api_keys", ["key_hash"])

    # extraction_templates (before uploaded_files — FK target)
    op.create_table("extraction_templates",
        sa.Column("id",                  postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id",              postgresql.UUID(as_uuid=True), sa.ForeignKey("organisations.id", ondelete="CASCADE"), nullable=True),
        sa.Column("created_by_id",       postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("parent_id",           postgresql.UUID(as_uuid=True), sa.ForeignKey("extraction_templates.id", ondelete="SET NULL"), nullable=True),
        sa.Column("name",                sa.String(255), nullable=False),
        sa.Column("description",         sa.Text,        nullable=True),
        sa.Column("document_type",       sa.String(50),  nullable=False, server_default="invoice"),
        sa.Column("status",              sa.String(20),  nullable=False, server_default="draft"),
        sa.Column("version",             sa.Integer,     nullable=False, server_default="1"),
        sa.Column("is_global",           sa.Boolean,     nullable=False, server_default="false"),
        sa.Column("field_schema",        postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("validation_rules",    postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("mapping_config",      postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("fill_config",         postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("sample_values",       postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("template_file_path",  sa.Text,        nullable=True),
        sa.Column("template_file_type",  sa.String(20),  nullable=True),
        sa.Column("created_at",          sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at",          sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted_at",          sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted",          sa.Boolean, nullable=False, server_default="false"),
        sa.CheckConstraint("status IN ('draft','active','archived')", name="ck_extraction_templates_status"),
        sa.UniqueConstraint("org_id", "name", "version", name="uq_extraction_templates_org_name_version"),
    )
    op.create_index("ix_extraction_templates_org_id",        "extraction_templates", ["org_id"])
    op.create_index("ix_extraction_templates_document_type", "extraction_templates", ["document_type"])
    op.create_index("ix_extraction_templates_status",        "extraction_templates", ["status"])

    # uploaded_files
    op.create_table("uploaded_files",
        sa.Column("id",                postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("uploaded_by_id",    postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("org_id",            postgresql.UUID(as_uuid=True), sa.ForeignKey("organisations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("template_id",       postgresql.UUID(as_uuid=True), sa.ForeignKey("extraction_templates.id", ondelete="SET NULL"), nullable=True),
        sa.Column("original_filename", sa.String(500), nullable=False),
        sa.Column("stored_filename",   sa.String(500), nullable=False, unique=True),
        sa.Column("storage_path",      sa.Text,        nullable=False),
        sa.Column("mime_type",         sa.String(127), nullable=False),
        sa.Column("file_category",     sa.String(20),  nullable=False),
        sa.Column("file_size",         sa.BigInteger,  nullable=False),
        sa.Column("checksum_sha256",   sa.String(64),  nullable=False),
        sa.Column("page_count",        sa.Integer,     nullable=True),
        sa.Column("width_px",          sa.Integer,     nullable=True),
        sa.Column("height_px",         sa.Integer,     nullable=True),
        sa.Column("document_type",     sa.String(50),  nullable=True),
        sa.Column("language_hint",     sa.String(10),  nullable=True),
        sa.Column("status",            sa.String(20),  nullable=False, server_default="pending"),
        sa.Column("validation_error",  sa.Text,        nullable=True),
        sa.Column("quarantine_reason", sa.Text,        nullable=True),
        sa.Column("extra",             postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at",        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at",        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted_at",        sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted",        sa.Boolean, nullable=False, server_default="false"),
        sa.CheckConstraint("file_category IN ('pdf','image')", name="ck_uploaded_files_file_category"),
        sa.CheckConstraint("status IN ('pending','validated','processing','done','failed','quarantine')", name="ck_uploaded_files_status"),
        sa.CheckConstraint("file_size > 0", name="ck_uploaded_files_file_size_positive"),
    )
    op.create_index("ix_uploaded_files_uploaded_by_id", "uploaded_files", ["uploaded_by_id"])
    op.create_index("ix_uploaded_files_org_id",         "uploaded_files", ["org_id"])
    op.create_index("ix_uploaded_files_status",         "uploaded_files", ["status"])
    op.create_index("ix_uploaded_files_checksum",       "uploaded_files", ["checksum_sha256"])
    op.create_index("ix_uploaded_files_created_at",     "uploaded_files", ["created_at"])

    # file_pages
    op.create_table("file_pages",
        sa.Column("id",          postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("file_id",     postgresql.UUID(as_uuid=True), sa.ForeignKey("uploaded_files.id", ondelete="CASCADE"), nullable=False),
        sa.Column("page_number", sa.Integer,     nullable=False),
        sa.Column("image_path",  sa.Text,        nullable=True),
        sa.Column("width_px",    sa.Integer,     nullable=True),
        sa.Column("height_px",   sa.Integer,     nullable=True),
        sa.Column("dpi",         sa.Integer,     nullable=True),
        sa.Column("raw_text",    sa.Text,        nullable=True),
        sa.Column("confidence",  sa.Numeric(5,4), nullable=True),
        sa.Column("word_count",  sa.Integer,     nullable=True),
        sa.Column("created_at",  sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at",  sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_file_pages_file_id",     "file_pages", ["file_id"])
    op.create_index("ix_file_pages_page_number", "file_pages", ["page_number"])

    # ocr_jobs
    op.create_table("ocr_jobs",
        sa.Column("id",              postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("file_id",         postgresql.UUID(as_uuid=True), sa.ForeignKey("uploaded_files.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_by_id",   postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("org_id",          postgresql.UUID(as_uuid=True), sa.ForeignKey("organisations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("template_id",     postgresql.UUID(as_uuid=True), sa.ForeignKey("extraction_templates.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status",          sa.String(20),  nullable=False, server_default="queued"),
        sa.Column("priority",        sa.Integer,     nullable=False, server_default="5"),
        sa.Column("retry_count",     sa.Integer,     nullable=False, server_default="0"),
        sa.Column("max_retries",     sa.Integer,     nullable=False, server_default="3"),
        sa.Column("queued_at",       sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at",      sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at",    sa.DateTime(timezone=True), nullable=True),
        sa.Column("processing_ms",   sa.Integer,     nullable=True),
        sa.Column("worker_id",       sa.String(255), nullable=True),
        sa.Column("celery_task_id",  sa.String(255), nullable=True),
        sa.Column("ocr_config",      postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("page_count",      sa.Integer,     nullable=True),
        sa.Column("avg_confidence",  sa.Numeric(5,4), nullable=True),
        sa.Column("total_words",     sa.Integer,     nullable=True),
        sa.Column("error_message",   sa.Text,        nullable=True),
        sa.Column("error_traceback", sa.Text,        nullable=True),
        sa.Column("created_at",      sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at",      sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('queued','processing','completed','failed','cancelled','retrying')", name="ck_ocr_jobs_status"),
        sa.CheckConstraint("retry_count >= 0",           name="ck_ocr_jobs_retry_non_neg"),
        sa.CheckConstraint("retry_count <= max_retries", name="ck_ocr_jobs_retry_lte_max"),
    )
    op.create_index("ix_ocr_jobs_file_id",       "ocr_jobs", ["file_id"])
    op.create_index("ix_ocr_jobs_created_by_id", "ocr_jobs", ["created_by_id"])
    op.create_index("ix_ocr_jobs_status",        "ocr_jobs", ["status"])
    op.create_index("ix_ocr_jobs_queued_at",     "ocr_jobs", ["queued_at"])
    op.create_index("ix_ocr_jobs_org_id",        "ocr_jobs", ["org_id"])

    # job_events
    op.create_table("job_events",
        sa.Column("id",          postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_id",      postgresql.UUID(as_uuid=True), sa.ForeignKey("ocr_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type",  sa.String(50),  nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("message",     sa.Text,        nullable=True),
        sa.Column("payload",     postgresql.JSONB, nullable=False, server_default="{}"),
    )
    op.create_index("ix_job_events_job_id",      "job_events", ["job_id"])
    op.create_index("ix_job_events_occurred_at", "job_events", ["occurred_at"])

    # extracted_data
    op.create_table("extracted_data",
        sa.Column("id",                       postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_id",                   postgresql.UUID(as_uuid=True), sa.ForeignKey("ocr_jobs.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("file_id",                  postgresql.UUID(as_uuid=True), sa.ForeignKey("uploaded_files.id", ondelete="CASCADE"), nullable=False),
        sa.Column("template_id",              postgresql.UUID(as_uuid=True), sa.ForeignKey("extraction_templates.id", ondelete="SET NULL"), nullable=True),
        sa.Column("org_id",                   postgresql.UUID(as_uuid=True), sa.ForeignKey("organisations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("raw_ocr_json",             postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("raw_text",                 sa.Text,        nullable=True),
        sa.Column("page_count",               sa.Integer,     nullable=False, server_default="0"),
        sa.Column("extracted_fields",         postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("validated_fields",         postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("mapped_output",            postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("line_items",               postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("overall_confidence",       sa.Numeric(5,4), nullable=True),
        sa.Column("validation_errors",        postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("fields_extracted",         sa.Integer,     nullable=False, server_default="0"),
        sa.Column("fields_high_confidence",   sa.Integer,     nullable=False, server_default="0"),
        sa.Column("fields_needing_review",    sa.Integer,     nullable=False, server_default="0"),
        sa.Column("status",                   sa.String(20),  nullable=False, server_default="pending"),
        sa.Column("reviewer_notes",           sa.Text,        nullable=True),
        sa.Column("reviewed_at",              sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by_id",           postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("extractor_version",        sa.String(20),  nullable=False, server_default="2.0.0"),
        sa.Column("created_at",               sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at",               sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('pending','extracted','validated','needs_review','rejected')", name="ck_extracted_data_status"),
    )
    op.create_index("ix_extracted_data_job_id",      "extracted_data", ["job_id"])
    op.create_index("ix_extracted_data_file_id",     "extracted_data", ["file_id"])
    op.create_index("ix_extracted_data_template_id", "extracted_data", ["template_id"])
    op.create_index("ix_extracted_data_status",      "extracted_data", ["status"])
    op.create_index("ix_extracted_data_created_at",  "extracted_data", ["created_at"])

    # form_fill_jobs
    op.create_table("form_fill_jobs",
        sa.Column("id",                  postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("extracted_data_id",   postgresql.UUID(as_uuid=True), sa.ForeignKey("extracted_data.id", ondelete="CASCADE"), nullable=False),
        sa.Column("template_id",         postgresql.UUID(as_uuid=True), sa.ForeignKey("extraction_templates.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by_id",       postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("org_id",              postgresql.UUID(as_uuid=True), sa.ForeignKey("organisations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("export_format",       sa.String(20),  nullable=False),
        sa.Column("status",              sa.String(20),  nullable=False, server_default="pending"),
        sa.Column("output_path",         sa.Text,        nullable=True),
        sa.Column("output_size",         sa.Integer,     nullable=True),
        sa.Column("fill_overrides",      postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("fill_result",         postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("processing_ms",       sa.Integer,     nullable=True),
        sa.Column("error_message",       sa.Text,        nullable=True),
        sa.Column("started_at",          sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at",        sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at",          sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at",          sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('pending','processing','completed','failed')", name="ck_form_fill_jobs_status"),
        sa.CheckConstraint("export_format IN ('pdf','excel','json')", name="ck_form_fill_jobs_format"),
    )
    op.create_index("ix_form_fill_jobs_extracted_data_id", "form_fill_jobs", ["extracted_data_id"])
    op.create_index("ix_form_fill_jobs_template_id",       "form_fill_jobs", ["template_id"])
    op.create_index("ix_form_fill_jobs_created_by_id",     "form_fill_jobs", ["created_by_id"])
    op.create_index("ix_form_fill_jobs_status",            "form_fill_jobs", ["status"])
    op.create_index("ix_form_fill_jobs_created_at",        "form_fill_jobs", ["created_at"])

    # audit_logs
    op.create_table("audit_logs",
        sa.Column("id",            postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id",       postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("org_id",        postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event_type",    sa.String(50),  nullable=False),
        sa.Column("level",         sa.String(20),  nullable=False, server_default="info"),
        sa.Column("action",        sa.String(100), nullable=False),
        sa.Column("resource_type", sa.String(100), nullable=True),
        sa.Column("resource_id",   sa.String(255), nullable=True),
        sa.Column("message",       sa.Text,        nullable=True),
        sa.Column("diff",          postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("metadata",      postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("ip_address",    postgresql.INET, nullable=True),
        sa.Column("user_agent",    sa.Text,         nullable=True),
        sa.Column("request_id",    sa.String(64),   nullable=True),
        sa.Column("occurred_at",   sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_logs_user_id",     "audit_logs", ["user_id"])
    op.create_index("ix_audit_logs_org_id",      "audit_logs", ["org_id"])
    op.create_index("ix_audit_logs_event_type",  "audit_logs", ["event_type"])
    op.create_index("ix_audit_logs_resource",    "audit_logs", ["resource_type", "resource_id"])
    op.create_index("ix_audit_logs_occurred_at", "audit_logs", ["occurred_at"])
    op.create_index("ix_audit_logs_request_id",  "audit_logs", ["request_id"])

    # system_logs
    op.create_table("system_logs",
        sa.Column("id",          postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("level",       sa.String(20),  nullable=False),
        sa.Column("service",     sa.String(100), nullable=False, server_default="api"),
        sa.Column("message",     sa.Text,        nullable=False),
        sa.Column("logger_name", sa.String(255), nullable=True),
        sa.Column("module",      sa.String(255), nullable=True),
        sa.Column("line_number", sa.Integer,     nullable=True),
        sa.Column("exception",   sa.Text,        nullable=True),
        sa.Column("extra",       postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("trace_id",    sa.String(64),  nullable=True),
        sa.Column("span_id",     sa.String(32),  nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_system_logs_level",       "system_logs", ["level"])
    op.create_index("ix_system_logs_service",     "system_logs", ["service"])
    op.create_index("ix_system_logs_occurred_at", "system_logs", ["occurred_at"])
    op.create_index("ix_system_logs_trace_id",    "system_logs", ["trace_id"])


def downgrade() -> None:
    for tbl in ("system_logs", "audit_logs", "form_fill_jobs", "extracted_data",
                "job_events", "ocr_jobs", "file_pages", "uploaded_files",
                "extraction_templates", "api_keys", "user_sessions", "users", "organisations"):
        op.drop_table(tbl)
