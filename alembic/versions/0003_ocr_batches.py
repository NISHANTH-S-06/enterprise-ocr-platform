"""Add OCR batch status plumbing.

Revision ID: 0003_ocr_batches
Revises: 0002_reviewer_role
Create Date: 2026-06-02
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003_ocr_batches"
down_revision = "0002_reviewer_role"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ocr_batches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organisations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("file_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("completed_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("needs_review_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("validated_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_bytes", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("total_pages", sa.Integer, nullable=False, server_default="0"),
        sa.Column("output_zip_path", sa.Text, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("metadata", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('queued','processing','completed','failed','partial_failed','cancelled')",
            name="ck_ocr_batches_status",
        ),
        sa.CheckConstraint("file_count >= 0", name="ck_ocr_batches_file_count_non_neg"),
        sa.CheckConstraint("completed_count >= 0", name="ck_ocr_batches_completed_count_non_neg"),
        sa.CheckConstraint("failed_count >= 0", name="ck_ocr_batches_failed_count_non_neg"),
        sa.CheckConstraint("needs_review_count >= 0", name="ck_ocr_batches_needs_review_count_non_neg"),
        sa.CheckConstraint("validated_count >= 0", name="ck_ocr_batches_validated_count_non_neg"),
        sa.CheckConstraint("total_bytes >= 0", name="ck_ocr_batches_total_bytes_non_neg"),
        sa.CheckConstraint("total_pages >= 0", name="ck_ocr_batches_total_pages_non_neg"),
    )
    op.create_index("ix_ocr_batches_created_by_id", "ocr_batches", ["created_by_id"])
    op.create_index("ix_ocr_batches_org_id", "ocr_batches", ["org_id"])
    op.create_index("ix_ocr_batches_status", "ocr_batches", ["status"])
    op.create_index("ix_ocr_batches_created_at", "ocr_batches", ["created_at"])

    op.add_column("uploaded_files", sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_uploaded_files_batch_id_ocr_batches",
        "uploaded_files",
        "ocr_batches",
        ["batch_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_uploaded_files_batch_id", "uploaded_files", ["batch_id"])

    op.add_column("ocr_jobs", sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_ocr_jobs_batch_id_ocr_batches",
        "ocr_jobs",
        "ocr_batches",
        ["batch_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_ocr_jobs_batch_id", "ocr_jobs", ["batch_id"])


def downgrade() -> None:
    op.drop_index("ix_ocr_jobs_batch_id", table_name="ocr_jobs")
    op.drop_constraint("fk_ocr_jobs_batch_id_ocr_batches", "ocr_jobs", type_="foreignkey")
    op.drop_column("ocr_jobs", "batch_id")

    op.drop_index("ix_uploaded_files_batch_id", table_name="uploaded_files")
    op.drop_constraint("fk_uploaded_files_batch_id_ocr_batches", "uploaded_files", type_="foreignkey")
    op.drop_column("uploaded_files", "batch_id")

    op.drop_index("ix_ocr_batches_created_at", table_name="ocr_batches")
    op.drop_index("ix_ocr_batches_status", table_name="ocr_batches")
    op.drop_index("ix_ocr_batches_org_id", table_name="ocr_batches")
    op.drop_index("ix_ocr_batches_created_by_id", table_name="ocr_batches")
    op.drop_table("ocr_batches")
