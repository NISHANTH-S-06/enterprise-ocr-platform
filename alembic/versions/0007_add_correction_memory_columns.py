"""Add correction-learning columns to correction_memory table.

Revision ID: 0007
Revises: 0006
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

    # Make company_id nullable for module-scoped corrections
    op.alter_column("correction_memory", "company_id",
                    existing_type=postgresql.UUID(as_uuid=True),
                    nullable=True,
                    existing_server_default=None,
                    existing_postgresql_server_default=None)
    # Safely drop FK constraint by discovering actual name (avoids hardcoded name failures)
    op.execute("""
        DO $$
        DECLARE constraint_name text;
        BEGIN
            SELECT tc.constraint_name INTO constraint_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
                ON tc.constraint_name = kcu.constraint_name
                AND tc.table_schema = kcu.table_schema
            WHERE tc.table_name = 'correction_memory'
              AND tc.constraint_type = 'FOREIGN KEY'
              AND kcu.column_name = 'company_id'
            LIMIT 1;

            IF constraint_name IS NOT NULL THEN
                EXECUTE format('ALTER TABLE correction_memory DROP CONSTRAINT %I', constraint_name);
            END IF;
        END $$;
    """)
    op.create_foreign_key("correction_memory_company_id_fkey", "correction_memory", "companies",
                          ["company_id"], ["id"], ondelete="SET NULL")

    op.add_column("correction_memory", sa.Column("module", sa.String(50), nullable=True))
    op.add_column("correction_memory", sa.Column("company_name", sa.String(255), nullable=True))
    op.add_column("correction_memory", sa.Column("vendor_name", sa.String(255), nullable=True))
    op.add_column("correction_memory", sa.Column("customer_name", sa.String(255), nullable=True))
    op.add_column("correction_memory", sa.Column("source_ocr_key", sa.String(255), nullable=True))
    op.add_column("correction_memory", sa.Column("source_ocr_label", sa.String(255), nullable=True))
    op.add_column("correction_memory", sa.Column("normalized_source_key", sa.String(255), nullable=True))
    op.add_column("correction_memory", sa.Column("source_value_sample", sa.Text, nullable=True))
    op.add_column("correction_memory", sa.Column("target_erp_name", sa.String(255), nullable=True))
    op.add_column("correction_memory", sa.Column("target_dom_id", sa.String(255), nullable=True))
    op.add_column("correction_memory", sa.Column("target_label", sa.String(255), nullable=True))
    op.add_column("correction_memory", sa.Column("corrected_value_sample", sa.Text, nullable=True))
    op.add_column("correction_memory", sa.Column("confidence", sa.Float(), nullable=True, server_default=sa.text("1.0")))
    op.add_column("correction_memory", sa.Column("apply_count", sa.Integer(), nullable=False, server_default=sa.text("0")))
    op.add_column("correction_memory", sa.Column("reject_count", sa.Integer(), nullable=False, server_default=sa.text("0")))
    op.add_column("correction_memory", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("correction_memory", sa.Column("source", sa.String(50), nullable=True, server_default="manual_correction"))
    op.add_column("correction_memory", sa.Column("metadata_json", json_type, nullable=False, server_default=sa.text("'{}'")))

    op.create_index("ix_correction_memory_module", "correction_memory", ["module"])
    op.create_index("ix_correction_memory_source_ocr_key", "correction_memory", ["source_ocr_key"])
    op.create_index("ix_correction_memory_normalized_source_key", "correction_memory", ["normalized_source_key"])
    op.create_index("ix_correction_memory_active", "correction_memory", ["is_active"])


def downgrade() -> None:
    op.drop_index("ix_correction_memory_active", table_name="correction_memory")
    op.drop_index("ix_correction_memory_normalized_source_key", table_name="correction_memory")
    op.drop_index("ix_correction_memory_source_ocr_key", table_name="correction_memory")
    op.drop_index("ix_correction_memory_module", table_name="correction_memory")

    op.drop_column("correction_memory", "metadata_json")
    op.drop_column("correction_memory", "source")
    op.drop_column("correction_memory", "is_active")
    op.drop_column("correction_memory", "reject_count")
    op.drop_column("correction_memory", "apply_count")
    op.drop_column("correction_memory", "confidence")
    op.drop_column("correction_memory", "corrected_value_sample")
    op.drop_column("correction_memory", "target_label")
    op.drop_column("correction_memory", "target_dom_id")
    op.drop_column("correction_memory", "target_erp_name")
    op.drop_column("correction_memory", "source_value_sample")
    op.drop_column("correction_memory", "normalized_source_key")
    op.drop_column("correction_memory", "source_ocr_label")
    op.drop_column("correction_memory", "source_ocr_key")
    op.drop_column("correction_memory", "customer_name")
    op.drop_column("correction_memory", "vendor_name")
    op.drop_column("correction_memory", "company_name")
    op.drop_column("correction_memory", "module")
