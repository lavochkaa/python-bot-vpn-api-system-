"""subscription config fields

Revision ID: 0002
Revises: 0001
Create Date: 2026-02-21 23:59:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("subscriptions", sa.Column("duration_days", sa.Integer(), nullable=True))
    op.add_column("subscriptions", sa.Column("plan_type", sa.String(length=16), nullable=True))
    op.add_column("subscriptions", sa.Column("traffic_gb", sa.Integer(), nullable=True))
    op.add_column("subscriptions", sa.Column("build_preset", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("subscriptions", "build_preset")
    op.drop_column("subscriptions", "traffic_gb")
    op.drop_column("subscriptions", "plan_type")
    op.drop_column("subscriptions", "duration_days")
