"""add provider fields to tracked_servers

Revision ID: 0011_server_provider_fields
Revises: 0010_tracked_servers
Create Date: 2026-04-21
"""

from alembic import op
import sqlalchemy as sa

revision = "0011_server_provider_fields"
down_revision = "0010_tracked_servers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tracked_servers",
        sa.Column(
            "provider_type",
            sa.String(length=32),
            nullable=False,
            server_default="manual",
        ),
    )
    op.add_column(
        "tracked_servers",
        sa.Column("provider_login", sa.String(length=256), nullable=True),
    )
    op.add_column(
        "tracked_servers",
        sa.Column("provider_secret", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "tracked_servers",
        sa.Column("provider_extra", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tracked_servers", "provider_extra")
    op.drop_column("tracked_servers", "provider_secret")
    op.drop_column("tracked_servers", "provider_login")
    op.drop_column("tracked_servers", "provider_type")
