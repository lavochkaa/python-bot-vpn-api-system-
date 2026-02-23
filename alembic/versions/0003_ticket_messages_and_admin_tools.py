"""ticket messages and support extensions

Revision ID: 0003
Revises: 0002
Create Date: 2026-02-22 00:20:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


ticketsenderrole = postgresql.ENUM("user", "admin", name="ticketsenderrole", create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE ticketstatus ADD VALUE IF NOT EXISTS 'pending'")

    op.add_column("support_tickets", sa.Column("subject", sa.String(length=255), nullable=True))
    op.add_column(
        "support_tickets",
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
    )
    op.execute("UPDATE support_tickets SET updated_at = created_at WHERE updated_at IS NULL")
    op.alter_column("support_tickets", "updated_at", nullable=False, server_default=sa.func.now())

    op.execute(
        """
        DO $$
        BEGIN
            CREATE TYPE ticketsenderrole AS ENUM ('user', 'admin');
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
        """
    )
    op.create_table(
        "ticket_messages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("support_tickets.id"), nullable=False),
        sa.Column("sender_role", ticketsenderrole, nullable=False),
        sa.Column("message_text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("ticket_messages")
    op.drop_column("support_tickets", "updated_at")
    op.drop_column("support_tickets", "subject")
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP TYPE IF EXISTS ticketsenderrole")
