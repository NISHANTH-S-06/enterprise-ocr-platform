"""Harden ERP master mappings — add extended fields and expand master_type constraint.

Revision ID: 0008
Revises: 0007
"""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add extended fields (all nullable for backward compatibility)
    op.add_column("erp_master_mappings", sa.Column("legal_name", sa.String(500), nullable=True))
    op.add_column("erp_master_mappings", sa.Column("alias_names", sa.Text, nullable=True))
    op.add_column("erp_master_mappings", sa.Column("gst_number", sa.String(20), nullable=True))
    op.add_column("erp_master_mappings", sa.Column("pan_number", sa.String(12), nullable=True))
    op.add_column("erp_master_mappings", sa.Column("hsn_code", sa.String(20), nullable=True))
    op.add_column("erp_master_mappings", sa.Column("part_number", sa.String(100), nullable=True))
    op.add_column("erp_master_mappings", sa.Column("product_description", sa.Text, nullable=True))
    op.add_column("erp_master_mappings", sa.Column("uom", sa.String(30), nullable=True))
    op.add_column("erp_master_mappings", sa.Column("tax_rate", sa.Float, nullable=True))
    op.add_column("erp_master_mappings", sa.Column("currency_code", sa.String(10), nullable=True))
    op.add_column("erp_master_mappings", sa.Column("display_name", sa.String(500), nullable=True))

    # Expand master_type CHECK constraint to include new types
    # Drop old constraint first (safe — PostgreSQL)
    op.execute("""
        DO $$
        DECLARE cname text;
        BEGIN
            SELECT tc.constraint_name INTO cname
            FROM information_schema.table_constraints tc
            WHERE tc.table_name = 'erp_master_mappings'
              AND tc.constraint_type = 'CHECK'
              AND tc.constraint_name LIKE '%master%type%'
            LIMIT 1;
            IF cname IS NOT NULL THEN
                EXECUTE format('ALTER TABLE erp_master_mappings DROP CONSTRAINT %I', cname);
            END IF;
        END $$;
    """)
    op.create_check_constraint(
        "ck_erp_master_mappings_type",
        "erp_master_mappings",
        "master_type IN ('customer','supplier','product','tax_group','customer_site','supplier_site',"
        "'payment_term','delivery_term','freight_term','currency','branch','project','salesperson','carrier')",
    )

    op.create_index("ix_erp_master_gst", "erp_master_mappings", ["gst_number"])
    op.create_index("ix_erp_master_part_number", "erp_master_mappings", ["part_number"])
    op.create_index("ix_erp_master_hsn", "erp_master_mappings", ["hsn_code"])


def downgrade() -> None:
    op.drop_index("ix_erp_master_hsn", table_name="erp_master_mappings")
    op.drop_index("ix_erp_master_part_number", table_name="erp_master_mappings")
    op.drop_index("ix_erp_master_gst", table_name="erp_master_mappings")

    op.execute("""
        DO $$
        DECLARE cname text;
        BEGIN
            SELECT tc.constraint_name INTO cname
            FROM information_schema.table_constraints tc
            WHERE tc.table_name = 'erp_master_mappings'
              AND tc.constraint_type = 'CHECK'
              AND tc.constraint_name LIKE '%master%type%'
            LIMIT 1;
            IF cname IS NOT NULL THEN
                EXECUTE format('ALTER TABLE erp_master_mappings DROP CONSTRAINT %I', cname);
            END IF;
        END $$;
    """)
    op.create_check_constraint(
        "ck_erp_master_mappings_type",
        "erp_master_mappings",
        "master_type IN ('customer','supplier','product','tax_group','customer_site','supplier_site')",
    )

    for col in ["display_name", "currency_code", "tax_rate", "uom", "product_description",
                "part_number", "hsn_code", "pan_number", "gst_number", "alias_names", "legal_name"]:
        op.drop_column("erp_master_mappings", col)
