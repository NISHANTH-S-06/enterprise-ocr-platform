"""Add reviewer user role.

Revision ID: 0002_reviewer_role
Revises: 0001
Create Date: 2026-06-02
"""
from alembic import op

revision = "0002_reviewer_role"
down_revision = "0001"
branch_labels = None
depends_on = None


NEW_ROLES = "'superadmin','admin','manager','operator','reviewer','viewer'"
OLD_ROLES = "'superadmin','admin','manager','operator','viewer'"


def upgrade() -> None:
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_users_ck_users_role")
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_users_role")
    op.execute(f"ALTER TABLE users ADD CONSTRAINT ck_users_ck_users_role CHECK (role IN ({NEW_ROLES}))")


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_users_ck_users_role")
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_users_role")
    op.execute(f"ALTER TABLE users ADD CONSTRAINT ck_users_ck_users_role CHECK (role IN ({OLD_ROLES}))")
