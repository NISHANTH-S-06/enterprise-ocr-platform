"""Add tenant-scoped ERP master mappings.

Revision ID: 0006
Revises: 0005_training_workbench
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005_training_workbench"
branch_labels = None
depends_on = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
    op.create_table(
        "erp_master_mappings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.String(length=64), nullable=True),
        sa.Column("master_type", sa.String(length=32), nullable=False),
        sa.Column("detected_text", sa.String(length=500), nullable=False),
        sa.Column("normalized_text", sa.String(length=500), nullable=False),
        sa.Column("erp_id", sa.String(length=255), nullable=False),
        sa.Column("erp_code", sa.String(length=255), nullable=True),
        sa.Column("erp_name", sa.String(length=500), nullable=True),
        sa.Column("module", sa.String(length=50), nullable=True),
        sa.Column("company_name", sa.String(length=255), nullable=True),
        sa.Column("vendor_name", sa.String(length=255), nullable=True),
        sa.Column("customer_name", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="manual"),
        sa.Column("metadata_json", json_type, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("master_type IN ('customer','supplier','product','tax_group','customer_site','supplier_site')", name="ck_erp_master_mappings_type"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "master_type", "normalized_text", "module", "company_name", name="uq_erp_master_mapping_scope"),
    )
    op.create_index("ix_erp_master_mappings_org_id", "erp_master_mappings", ["org_id"])
    op.create_index("ix_erp_master_mappings_lookup", "erp_master_mappings", ["master_type", "normalized_text", "module"])
    op.create_index("ix_erp_master_mappings_active", "erp_master_mappings", ["is_active"])


def downgrade() -> None:
    op.drop_index("ix_erp_master_mappings_active", table_name="erp_master_mappings")
    op.drop_index("ix_erp_master_mappings_lookup", table_name="erp_master_mappings")
    op.drop_index("ix_erp_master_mappings_org_id", table_name="erp_master_mappings")
    op.drop_table("erp_master_mappings")
