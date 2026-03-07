"""add promo target

Revision ID: 0007_promo_target
Revises: 0006_device_limits
Create Date: 2026-03-07
"""

from alembic import op
import sqlalchemy as sa


revision = "0007_promo_target"
down_revision = "0006_device_limits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    promo_target = sa.Enum("balance", "subscription", name="promotarget")
    promo_target.create(op.get_bind(), checkfirst=True)
    op.add_column("promo_codes", sa.Column("target", promo_target, nullable=True))


def downgrade() -> None:
    op.drop_column("promo_codes", "target")
    promo_target = sa.Enum("balance", "subscription", name="promotarget")
    promo_target.drop(op.get_bind(), checkfirst=True)

