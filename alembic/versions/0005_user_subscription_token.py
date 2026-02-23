"""add subscription token to users

Revision ID: 0005
Revises: 0004
Create Date: 2026-02-23 08:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("subscription_token", sa.String(length=128), nullable=True))
    op.create_index("ix_users_subscription_token", "users", ["subscription_token"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_subscription_token", table_name="users")
    op.drop_column("users", "subscription_token")
