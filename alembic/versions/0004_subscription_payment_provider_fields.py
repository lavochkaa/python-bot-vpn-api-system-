"""subscription payment/provider fields

Revision ID: 0004
Revises: 0003
Create Date: 2026-02-23 03:20:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("subscriptions", sa.Column("price", sa.Numeric(12, 2), nullable=True))
    op.add_column("subscriptions", sa.Column("status", sa.String(length=32), nullable=True))
    op.add_column("subscriptions", sa.Column("provider_subscription_id", sa.String(length=128), nullable=True))
    op.add_column("subscriptions", sa.Column("payload_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("subscriptions", "payload_json")
    op.drop_column("subscriptions", "provider_subscription_id")
    op.drop_column("subscriptions", "status")
    op.drop_column("subscriptions", "price")
