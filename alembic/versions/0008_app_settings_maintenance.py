"""add app settings table for maintenance mode

Revision ID: 0008_app_settings_maintenance
Revises: 0007_promo_target
Create Date: 2026-03-07
"""

from alembic import op
import sqlalchemy as sa


revision = "0008_app_settings_maintenance"
down_revision = "0007_promo_target"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_app_settings_key", "app_settings", ["key"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_app_settings_key", table_name="app_settings")
    op.drop_table("app_settings")
