"""add photo file id to ticket messages

Revision ID: 0009_ticket_message_photos
Revises: 0008_app_settings_maintenance
Create Date: 2026-03-08
"""

from alembic import op
import sqlalchemy as sa


revision = "0009_ticket_message_photos"
down_revision = "0008_app_settings_maintenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ticket_messages", sa.Column("photo_file_id", sa.String(length=512), nullable=True))


def downgrade() -> None:
    op.drop_column("ticket_messages", "photo_file_id")
