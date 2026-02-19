"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-02-18 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


discount_type = sa.Enum("percent", "fixed", name="discounttype")
payment_status = sa.Enum("pending", "completed", "failed", name="paymentstatus")
payment_kind = sa.Enum("topup", "subscription", name="paymentkind")
ticket_status = sa.Enum("open", "closed", name="ticketstatus")
vpn_key_status = sa.Enum("active", "revoked", name="vpnkeystatus")

def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("full_name", sa.String(length=256), nullable=True),
        sa.Column("segment", sa.String(length=64), nullable=True),
        sa.Column("balance", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "plans",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("price", sa.Numeric(12, 2), nullable=False),
        sa.Column("period_days", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )

    op.create_table(
        "promo_codes",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("code", sa.String(length=64), nullable=False, unique=True),
        sa.Column("discount_type", discount_type, nullable=False),
        sa.Column("discount_value", sa.Numeric(12, 2), nullable=False),
        sa.Column("max_activations", sa.Integer(), nullable=True),
        sa.Column("activations_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("allowed_user_ids", sa.Text(), nullable=True),
        sa.Column("allowed_segment", sa.String(length=64), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("plan_id", sa.Integer(), sa.ForeignKey("plans.id"), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "payments",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("kind", payment_kind, nullable=False, server_default="topup"),
        sa.Column("status", payment_status, nullable=False, server_default="pending"),
        sa.Column("provider_payload", sa.Text(), nullable=True),
        sa.Column("telegram_charge_id", sa.String(length=128), nullable=True),
        sa.Column("promo_code_id", sa.Integer(), sa.ForeignKey("promo_codes.id"), nullable=True),
        sa.Column("plan_id", sa.Integer(), sa.ForeignKey("plans.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("telegram_charge_id", name="uq_payments_telegram_charge_id"),
    )

    op.create_table(
        "balance_ledger",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column("payment_id", sa.Integer(), sa.ForeignKey("payments.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "promo_redemptions",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("promo_id", sa.Integer(), sa.ForeignKey("promo_codes.id"), nullable=False),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "vpn_keys",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("plan_id", sa.Integer(), sa.ForeignKey("plans.id"), nullable=True),
        sa.Column("subscription_id", sa.Integer(), sa.ForeignKey("subscriptions.id"), nullable=True),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("status", vpn_key_status, nullable=False, server_default="active"),
    )

    op.create_table(
        "support_tickets",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("status", ticket_status, nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("support_tickets")
    op.drop_table("vpn_keys")
    op.drop_table("promo_redemptions")
    op.drop_table("balance_ledger")
    op.drop_table("payments")
    op.drop_table("subscriptions")
    op.drop_table("promo_codes")
    op.drop_table("plans")
    op.drop_table("users")

    bind = op.get_bind()
    vpn_key_status.drop(bind, checkfirst=True)
    ticket_status.drop(bind, checkfirst=True)
    payment_kind.drop(bind, checkfirst=True)
    payment_status.drop(bind, checkfirst=True)
    discount_type.drop(bind, checkfirst=True)
