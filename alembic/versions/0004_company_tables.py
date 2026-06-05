"""Add company master data tables — Phase 1.

Revision ID: 0004_company_tables
Revises: 0003_ocr_batches
Create Date: 2026-06-05

New tables:
  companies, company_templates, field_mappings,
  correction_memory, validation_results, document_quality_reports

Seed data: 3 sample companies inserted in upgrade().
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision      = "0004_company_tables"
down_revision = "0003_ocr_batches"
branch_labels = None
depends_on    = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def upgrade() -> None:
    # ── companies ────────────────────────────────────────────────────────────
    op.create_table(
        "companies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("company_name",         sa.String(255), nullable=False),
        sa.Column("gst_number",           sa.String(20),  nullable=True),
        sa.Column("iec_code",             sa.String(20),  nullable=True),
        sa.Column("address",              sa.Text,        nullable=True),
        sa.Column("bank_name",            sa.String(255), nullable=True),
        sa.Column("account_number",       sa.String(50),  nullable=True),
        sa.Column("ifsc_code",            sa.String(20),  nullable=True),
        sa.Column("swift_code",           sa.String(20),  nullable=True),
        sa.Column("default_currency",     sa.String(10),  nullable=False, server_default="INR"),
        sa.Column("default_tax_type",     sa.String(50),  nullable=True),
        sa.Column("default_port_loading", sa.String(255), nullable=True),
        sa.Column("default_port_discharge", sa.String(255), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("gst_number", name="uq_companies_gst"),
    )
    op.create_index("ix_companies_gst_number",  "companies", ["gst_number"])
    op.create_index("ix_companies_company_name", "companies", ["company_name"])

    # ── company_templates ────────────────────────────────────────────────────
    op.create_table(
        "company_templates",
        sa.Column("id",               postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id",       postgresql.UUID(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_type",    sa.String(50),  nullable=False),
        sa.Column("template_name",    sa.String(255), nullable=False),
        sa.Column("template_version", sa.String(20),  nullable=False, server_default="1.0"),
        sa.Column("is_active",        sa.Boolean,     nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "document_type IN ('export_invoice','commercial_invoice','purchase_order','grn','sales_invoice','general')",
            name="ck_company_templates_doc_type",
        ),
    )
    op.create_index("ix_company_templates_company_id", "company_templates", ["company_id"])
    op.create_index("ix_company_templates_doc_type",   "company_templates", ["document_type"])

    # ── field_mappings ────────────────────────────────────────────────────────
    op.create_table(
        "field_mappings",
        sa.Column("id",            postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("template_id",   postgresql.UUID(as_uuid=True), sa.ForeignKey("company_templates.id", ondelete="CASCADE"), nullable=False),
        sa.Column("field_name",    sa.String(100), nullable=False),
        sa.Column("label_text",    sa.String(255), nullable=True),
        sa.Column("method",        sa.String(50),  nullable=False, server_default="regex"),
        sa.Column("regex_pattern", sa.Text,        nullable=True),
        sa.Column("section_name",  sa.String(100), nullable=True),
        sa.Column("priority",      sa.Integer,     nullable=False, server_default="50"),
        sa.Column("required",      sa.Boolean,     nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "method IN ('regex','keyword_proximity','table_cell','fixed_position','llm_extract','manual')",
            name="ck_field_mappings_method",
        ),
    )
    op.create_index("ix_field_mappings_template_id", "field_mappings", ["template_id"])
    op.create_index("ix_field_mappings_field_name",  "field_mappings", ["field_name"])

    # ── correction_memory ─────────────────────────────────────────────────────
    op.create_table(
        "correction_memory",
        sa.Column("id",              postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id",      postgresql.UUID(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("template_id",     postgresql.UUID(as_uuid=True), sa.ForeignKey("company_templates.id", ondelete="SET NULL"), nullable=True),
        sa.Column("document_type",   sa.String(50),  nullable=True),
        sa.Column("field_name",      sa.String(100), nullable=False),
        sa.Column("old_value",       sa.Text,        nullable=True),
        sa.Column("correct_value",   sa.Text,        nullable=False),
        sa.Column("evidence_text",   sa.Text,        nullable=True),
        sa.Column("rule_suggestion", sa.Text,        nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_correction_memory_company_id",    "correction_memory", ["company_id"])
    op.create_index("ix_correction_memory_template_id",   "correction_memory", ["template_id"])
    op.create_index("ix_correction_memory_field_name",    "correction_memory", ["field_name"])
    op.create_index("ix_correction_memory_document_type", "correction_memory", ["document_type"])

    # ── validation_results ────────────────────────────────────────────────────
    op.create_table(
        "validation_results",
        sa.Column("id",          postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id", sa.String(255), nullable=False),
        sa.Column("field_name",  sa.String(100), nullable=False),
        sa.Column("rule_name",   sa.String(100), nullable=False),
        sa.Column("status",      sa.String(20),  nullable=False, server_default="fail"),
        sa.Column("message",     sa.Text,        nullable=True),
        sa.Column("severity",    sa.String(20),  nullable=False, server_default="medium"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('pass','fail','warning','skipped')",           name="ck_validation_results_status"),
        sa.CheckConstraint("severity IN ('critical','high','medium','low','info')",   name="ck_validation_results_severity"),
    )
    op.create_index("ix_validation_results_document_id", "validation_results", ["document_id"])
    op.create_index("ix_validation_results_field_name",  "validation_results", ["field_name"])
    op.create_index("ix_validation_results_status",      "validation_results", ["status"])

    # ── document_quality_reports ──────────────────────────────────────────────
    op.create_table(
        "document_quality_reports",
        sa.Column("id",                postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id",       sa.String(255), nullable=False),
        sa.Column("quality_status",    sa.String(20),  nullable=False, server_default="acceptable"),
        sa.Column("blur_score",        sa.Float,       nullable=True),
        sa.Column("resolution_status", sa.String(20),  nullable=False, server_default="unknown"),
        sa.Column("rotation_angle",    sa.Float,       nullable=True),
        sa.Column("warnings_json",     postgresql.JSONB, nullable=True),
        sa.Column("can_continue",      sa.Boolean,     nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("quality_status IN ('good','acceptable','poor','unreadable')", name="ck_doc_quality_status"),
        sa.CheckConstraint("resolution_status IN ('high','medium','low','unknown')",      name="ck_doc_quality_resolution"),
    )
    op.create_index("ix_doc_quality_document_id", "document_quality_reports", ["document_id"])

    # ── seed: 3 sample companies ──────────────────────────────────────────────
    companies_table = sa.table(
        "companies",
        sa.column("id",                   postgresql.UUID(as_uuid=True)),
        sa.column("company_name",         sa.String),
        sa.column("gst_number",           sa.String),
        sa.column("iec_code",             sa.String),
        sa.column("default_currency",     sa.String),
        sa.column("bank_name",            sa.String),
        sa.column("ifsc_code",            sa.String),
        sa.column("is_active",            sa.Boolean),
        sa.column("created_at",           sa.DateTime(timezone=True)),
        sa.column("updated_at",           sa.DateTime(timezone=True)),
    )
    now = _now()
    op.bulk_insert(companies_table, [
        {"id": uuid.UUID("11111111-1111-1111-1111-111111111111"), "company_name": "Aline Exports Pvt Ltd",       "gst_number": "27AABCA1234A1Z5", "iec_code": "0315012345", "default_currency": "USD", "bank_name": "HDFC Bank",   "ifsc_code": "HDFC0001234", "is_active": True, "created_at": now, "updated_at": now},
        {"id": uuid.UUID("22222222-2222-2222-2222-222222222222"), "company_name": "Global Sprint Trading Co",    "gst_number": "29AABCB5678B1Z3", "iec_code": "0415056789", "default_currency": "USD", "bank_name": "ICICI Bank",  "ifsc_code": "ICIC0002345", "is_active": True, "created_at": now, "updated_at": now},
        {"id": uuid.UUID("33333333-3333-3333-3333-333333333333"), "company_name": "Sunrise Imports & Distribution", "gst_number": "06AABCC9012C1Z1", "iec_code": "0515098765", "default_currency": "INR", "bank_name": "Axis Bank",   "ifsc_code": "UTIB0003456", "is_active": True, "created_at": now, "updated_at": now},
    ])


def downgrade() -> None:
    op.drop_index("ix_doc_quality_document_id",   table_name="document_quality_reports")
    op.drop_table("document_quality_reports")

    op.drop_index("ix_validation_results_status",      table_name="validation_results")
    op.drop_index("ix_validation_results_field_name",  table_name="validation_results")
    op.drop_index("ix_validation_results_document_id", table_name="validation_results")
    op.drop_table("validation_results")

    op.drop_index("ix_correction_memory_document_type", table_name="correction_memory")
    op.drop_index("ix_correction_memory_field_name",    table_name="correction_memory")
    op.drop_index("ix_correction_memory_template_id",   table_name="correction_memory")
    op.drop_index("ix_correction_memory_company_id",    table_name="correction_memory")
    op.drop_table("correction_memory")

    op.drop_index("ix_field_mappings_field_name",  table_name="field_mappings")
    op.drop_index("ix_field_mappings_template_id", table_name="field_mappings")
    op.drop_table("field_mappings")

    op.drop_index("ix_company_templates_doc_type",   table_name="company_templates")
    op.drop_index("ix_company_templates_company_id", table_name="company_templates")
    op.drop_table("company_templates")

    op.drop_index("ix_companies_company_name", table_name="companies")
    op.drop_index("ix_companies_gst_number",   table_name="companies")
    op.drop_table("companies")
