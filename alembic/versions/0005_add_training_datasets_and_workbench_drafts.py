"""add_training_datasets_and_workbench_drafts

Revision ID: 0005_training_workbench
Revises: 0004_company_tables
Create Date: 2026-06-10

New tables:
  training_datasets  — OCR training/field-mapping datasets per org
  workbench_drafts   — saved form workbench drafts per user
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# ---------------------------------------------------------------------------
# Revision identifiers
# ---------------------------------------------------------------------------
revision = "0005_training_workbench"
down_revision = "0004_company_tables"
branch_labels = None
depends_on = None

# ---------------------------------------------------------------------------
# Helpers — detect dialect so we use JSONB on Postgres, JSON/Text on SQLite
# ---------------------------------------------------------------------------

def _json_col():
    """Return JSONB for PostgreSQL, JSON for everything else."""
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import JSONB
        return JSONB
    return sa.Text        # SQLite / other: store as text


# ---------------------------------------------------------------------------
# upgrade
# ---------------------------------------------------------------------------

def upgrade() -> None:
    json_type = _json_col()

    # ------------------------------------------------------------------
    # training_datasets
    # ------------------------------------------------------------------
    op.create_table(
        "training_datasets",
        sa.Column("id",                sa.String(36),  primary_key=True),
        sa.Column("org_id",            sa.String(64),  nullable=True),
        sa.Column("dataset_name",      sa.String(255), nullable=False),
        sa.Column("module",            sa.String(50),  nullable=False),
        sa.Column("document_type",     sa.String(100), nullable=False),
        sa.Column("company_name",      sa.String(255), nullable=True),
        sa.Column("vendor_name",       sa.String(255), nullable=True),
        sa.Column("customer_name",     sa.String(255), nullable=True),
        sa.Column("is_active",         sa.Boolean(),   nullable=False, server_default=sa.true()),
        sa.Column("version",           sa.Integer(),   nullable=False, server_default="1"),
        sa.Column("mappings_json",     json_type,      nullable=False, server_default="[]"),
        sa.Column("row_mappings_json", json_type,      nullable=False, server_default="[]"),
        sa.Column("metadata_json",     json_type,      nullable=False, server_default="{}"),
        sa.Column("created_at",        sa.DateTime(),  nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at",        sa.DateTime(),  nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "org_id", "dataset_name", "module", "document_type",
            name="uq_training_datasets_scope_name",
        ),
        sa.CheckConstraint(
            "module IN ('sales_order','purchase_order')",
            name="ck_training_datasets_module",
        ),
    )

    op.create_index("ix_training_datasets_org_id",       "training_datasets", ["org_id"])
    op.create_index("ix_training_datasets_module",       "training_datasets", ["module"])
    op.create_index("ix_training_datasets_document_type","training_datasets", ["document_type"])
    op.create_index("ix_training_datasets_active",       "training_datasets", ["is_active"])
    op.create_index("ix_training_datasets_company_name", "training_datasets", ["company_name"])
    op.create_index("ix_training_datasets_vendor_name",  "training_datasets", ["vendor_name"])
    op.create_index("ix_training_datasets_customer_name","training_datasets", ["customer_name"])

    # ------------------------------------------------------------------
    # workbench_drafts
    # ------------------------------------------------------------------
    # Use postgresql UUID type when on postgres, Text otherwise (SQLite compat)
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import UUID as PG_UUID
        uuid_col = lambda: PG_UUID(as_uuid=False)   # stores as str in pg
    else:
        uuid_col = lambda: sa.String(36)

    op.create_table(
        "workbench_drafts",
        sa.Column("id",                uuid_col(),     primary_key=True),
        sa.Column("org_id",            uuid_col(),     nullable=True),
        sa.Column("created_by_id",     uuid_col(),     nullable=False),
        sa.Column("module",            sa.String(50),  nullable=False),
        sa.Column("document_type",     sa.String(100), nullable=True),
        sa.Column("dataset_id",        sa.String(64),  nullable=True),
        sa.Column("source_job_id",     sa.String(64),  nullable=True),
        sa.Column("form_data",         json_type,      nullable=False, server_default="{}"),
        sa.Column("item_rows",         json_type,      nullable=False, server_default="[]"),
        sa.Column("validation_result", json_type,      nullable=False, server_default="{}"),
        sa.Column("metadata_json",     json_type,      nullable=False, server_default="{}"),
        sa.Column("is_active",         sa.Boolean(),   nullable=False, server_default=sa.true()),
        sa.Column("created_at",        sa.DateTime(),  nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at",        sa.DateTime(),  nullable=False, server_default=sa.func.now()),
        # FK to organisations — soft: nullable, no cascade failure if org removed
        sa.ForeignKeyConstraint(
            ["org_id"], ["organisations.id"],
            name="fk_workbench_drafts_org_id",
            ondelete="SET NULL",
        ),
        # FK to users — restrict deletion if user has open drafts
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"],
            name="fk_workbench_drafts_created_by_id",
            ondelete="RESTRICT",
        ),
    )

    op.create_index("ix_workbench_drafts_org_id",        "workbench_drafts", ["org_id"])
    op.create_index("ix_workbench_drafts_created_by_id", "workbench_drafts", ["created_by_id"])
    op.create_index("ix_workbench_drafts_module",        "workbench_drafts", ["module"])
    op.create_index("ix_workbench_drafts_source_job_id", "workbench_drafts", ["source_job_id"])
    op.create_index("ix_workbench_drafts_dataset_id",    "workbench_drafts", ["dataset_id"])


# ---------------------------------------------------------------------------
# downgrade
# ---------------------------------------------------------------------------

def downgrade() -> None:
    # drop workbench_drafts indexes then table
    op.drop_index("ix_workbench_drafts_dataset_id",    table_name="workbench_drafts")
    op.drop_index("ix_workbench_drafts_source_job_id", table_name="workbench_drafts")
    op.drop_index("ix_workbench_drafts_module",        table_name="workbench_drafts")
    op.drop_index("ix_workbench_drafts_created_by_id", table_name="workbench_drafts")
    op.drop_index("ix_workbench_drafts_org_id",        table_name="workbench_drafts")
    op.drop_table("workbench_drafts")

    # drop training_datasets indexes then table
    op.drop_index("ix_training_datasets_customer_name", table_name="training_datasets")
    op.drop_index("ix_training_datasets_vendor_name",  table_name="training_datasets")
    op.drop_index("ix_training_datasets_company_name", table_name="training_datasets")
    op.drop_index("ix_training_datasets_active",       table_name="training_datasets")
    op.drop_index("ix_training_datasets_document_type",table_name="training_datasets")
    op.drop_index("ix_training_datasets_module",       table_name="training_datasets")
    op.drop_index("ix_training_datasets_org_id",       table_name="training_datasets")
    op.drop_table("training_datasets")
