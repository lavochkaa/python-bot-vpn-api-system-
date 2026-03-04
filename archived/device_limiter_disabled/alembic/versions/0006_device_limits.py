"""add user device limits and active device sessions

Revision ID: 0006
Revises: 0005
Create Date: 2026-03-02 01:30:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def _column_names(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {col["name"] for col in inspector.get_columns(table_name)}


def _index_names(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {idx["name"] for idx in inspector.get_indexes(table_name)}


def _has_table(table_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    user_columns = _column_names("users")
    if "max_devices" not in user_columns:
        op.add_column(
            "users",
            sa.Column("max_devices", sa.Integer(), nullable=False, server_default="2"),
        )

    if "subscription_uuid" not in user_columns:
        op.add_column(
            "users",
            sa.Column("subscription_uuid", sa.String(length=64), nullable=True),
        )

    user_indexes = _index_names("users")
    if "ix_users_subscription_uuid" not in user_indexes:
        op.create_index("ix_users_subscription_uuid", "users", ["subscription_uuid"], unique=True)

    if not _has_table("device_sessions"):
        op.create_table(
            "device_sessions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("uuid", sa.Text(), nullable=False),
            sa.Column("client_ip", sa.Text(), nullable=False),
            sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
            sa.Column("blocked_until", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.UniqueConstraint("uuid", "client_ip", name="uq_device_sessions_uuid_client_ip"),
        )

    session_indexes = _index_names("device_sessions")
    if "ix_device_sessions_uuid" not in session_indexes:
        op.create_index("ix_device_sessions_uuid", "device_sessions", ["uuid"], unique=False)
    if "ix_device_sessions_last_seen" not in session_indexes:
        op.create_index("ix_device_sessions_last_seen", "device_sessions", ["last_seen"], unique=False)


def downgrade() -> None:
    if _has_table("device_sessions"):
        session_indexes = _index_names("device_sessions")
        if "ix_device_sessions_last_seen" in session_indexes:
            op.drop_index("ix_device_sessions_last_seen", table_name="device_sessions")
        if "ix_device_sessions_uuid" in session_indexes:
            op.drop_index("ix_device_sessions_uuid", table_name="device_sessions")
        op.drop_table("device_sessions")

    user_columns = _column_names("users")
    user_indexes = _index_names("users")
    if "ix_users_subscription_uuid" in user_indexes:
        op.drop_index("ix_users_subscription_uuid", table_name="users")
    if "subscription_uuid" in user_columns:
        op.drop_column("users", "subscription_uuid")
    if "max_devices" in user_columns:
        op.drop_column("users", "max_devices")
