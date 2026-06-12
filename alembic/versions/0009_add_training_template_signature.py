"""Add template_signature and lifecycle columns to training_datasets.
Widen module CHECK constraint to support commercial_invoice, grn, etc.

Revision ID: 0009
Revises: 0008
Create Date: 2026-06-12
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

# Allowed module values — widened from the original two
_MODULE_VALUES = (
    "'sales_order'",
    "'purchase_order'",
    "'sales'",
    "'purchase'",
    "'commercial_invoice'",
    "'export_invoice'",
    "'grn'",
    "'invoice'",
    "'general'",
)
_MODULE_CHECK = f"module IN ({', '.join(_MODULE_VALUES)})"


def upgrade() -> None:
    # ── 1. Add new columns to training_datasets (all nullable / have defaults) ──
    op.add_column(
        "training_datasets",
        sa.Column("template_signature", sa.Text(), nullable=True),
    )
    op.add_column(
        "training_datasets",
        sa.Column("approved", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "training_datasets",
        sa.Column("confidence", sa.Float(), nullable=True),
    )
    op.add_column(
        "training_datasets",
        sa.Column("sample_count", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "training_datasets",
        sa.Column("usage_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "training_datasets",
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
    )

    # ── 2. Add index on template_signature for fast lookup ────────────────────
    op.create_index(
        "ix_training_datasets_template_sig",
        "training_datasets",
        ["template_signature"],
    )

    # ── 3. Widen module CHECK constraint ──────────────────────────────────────
    # Use a PG-safe DO block so it degrades gracefully on SQLite (no-op).
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # Drop old constraint by name (created in migration 0005)
        op.execute("""
            DO $$
            DECLARE cname text;
            BEGIN
                SELECT tc.constraint_name INTO cname
                FROM information_schema.table_constraints tc
                WHERE tc.table_name = 'training_datasets'
                  AND tc.constraint_type = 'CHECK'
                  AND tc.constraint_name LIKE '%module%'
                LIMIT 1;
                IF cname IS NOT NULL THEN
                    EXECUTE format('ALTER TABLE training_datasets DROP CONSTRAINT %I', cname);
                END IF;
            END $$;
        """)
        op.create_check_constraint(
            "ck_training_datasets_module",
            "training_datasets",
            _MODULE_CHECK,
        )
    # SQLite: CHECK constraints are not enforced at ALTER time; existing rows
    # are unaffected and new inserts will pass the in-ORM validation instead.


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # Restore narrow constraint
        op.execute("""
            DO $$
            DECLARE cname text;
            BEGIN
                SELECT tc.constraint_name INTO cname
                FROM information_schema.table_constraints tc
                WHERE tc.table_name = 'training_datasets'
                  AND tc.constraint_type = 'CHECK'
                  AND tc.constraint_name LIKE '%module%'
                LIMIT 1;
                IF cname IS NOT NULL THEN
                    EXECUTE format('ALTER TABLE training_datasets DROP CONSTRAINT %I', cname);
                END IF;
            END $$;
        """)
        op.create_check_constraint(
            "ck_training_datasets_module",
            "training_datasets",
            "module IN ('sales_order','purchase_order')",
        )

    # Drop index and columns in reverse order
    op.drop_index("ix_training_datasets_template_sig", table_name="training_datasets")
    op.drop_column("training_datasets", "last_used_at")
    op.drop_column("training_datasets", "usage_count")
    op.drop_column("training_datasets", "sample_count")
    op.drop_column("training_datasets", "confidence")
    op.drop_column("training_datasets", "approved")
    op.drop_column("training_datasets", "template_signature")
