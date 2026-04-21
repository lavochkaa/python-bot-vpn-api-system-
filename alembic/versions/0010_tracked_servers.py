"""add tracked_servers table

Revision ID: 0010_tracked_servers
Revises: 0009_ticket_message_photos
Create Date: 2026-04-21
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0010_tracked_servers"
down_revision = "0009_ticket_message_photos"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tracked_servers",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("ip", sa.String(length=64), nullable=False),
        sa.Column("panel_url", sa.String(length=512), nullable=True),
        sa.Column("api_key", sa.String(length=256), nullable=True),
        sa.Column("provider_name", sa.String(length=128), nullable=True),
        sa.Column("account_balance", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("next_payment_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("monthly_cost", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("last_status", sa.String(length=16), nullable=False, server_default="unknown"),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("tracked_servers")
