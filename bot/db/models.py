import enum
from datetime import datetime
from decimal import Decimal
from sqlalchemy import (
    BigInteger, String, Numeric, Boolean, DateTime, Integer,
    ForeignKey, Enum as PgEnum, Text, func, UniqueConstraint
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from bot.db.base import Base


class DiscountType(str, enum.Enum):
    percent = "percent"
    fixed = "fixed"

class PromoTarget(str, enum.Enum):
    balance = "balance"
    subscription = "subscription"


class PaymentStatus(str, enum.Enum):
    pending = "pending"
    completed = "completed"
    failed = "failed"


class PaymentKind(str, enum.Enum):
    topup = "topup"
    subscription = "subscription"


class TicketStatus(str, enum.Enum):
    open = "open"
    pending = "pending"
    closed = "closed"


class VpnKeyStatus(str, enum.Enum):
    active = "active"
    revoked = "revoked"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # tg_id
    username: Mapped[str | None] = mapped_column(String(64))
    full_name: Mapped[str | None] = mapped_column(String(256))
    segment: Mapped[str | None] = mapped_column(String(64))
    subscription_token: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    subscription_uuid: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    max_devices: Mapped[int] = mapped_column(Integer, default=2, nullable=False, server_default="2")
    balance: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    subscriptions: Mapped[list["Subscription"]] = relationship(back_populates="user")
    payments: Mapped[list["Payment"]] = relationship(back_populates="user")
    vpn_keys: Mapped[list["VpnKey"]] = relationship(back_populates="user")
    tickets: Mapped[list["SupportTicket"]] = relationship(back_populates="user")
    ledger_entries: Mapped[list["BalanceLedger"]] = relationship(back_populates="user")


class Plan(Base):
    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128))           # "VPN", "VPN + обход"
    slug: Mapped[str] = mapped_column(String(64), unique=True)  # "vpn", "vpn_bypass"
    description: Mapped[str | None] = mapped_column(Text)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    period_days: Mapped[int] = mapped_column(Integer, default=30)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    plan_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    traffic_gb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    build_preset: Mapped[str | None] = mapped_column(String(32), nullable=True)
    price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    provider_subscription_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # TODO(cleanup): a legacy "devices" concept was removed from subscription constructor.

    user: Mapped["User"] = relationship(back_populates="subscriptions")
    plan: Mapped["Plan"] = relationship()


class PromoCode(Base):
    __tablename__ = "promo_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    discount_type: Mapped[DiscountType] = mapped_column(PgEnum(DiscountType, name="discounttype"))
    discount_value: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    target: Mapped[PromoTarget | None] = mapped_column(
        PgEnum(PromoTarget, name="promotarget"),
        nullable=True,
    )
    max_activations: Mapped[int | None] = mapped_column(Integer)       # None = unlimited
    activations_count: Mapped[int] = mapped_column(Integer, default=0)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    allowed_user_ids: Mapped[str | None] = mapped_column(Text)
    allowed_segment: Mapped[str | None] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    redemptions: Mapped[list["PromoRedemption"]] = relationship(back_populates="promo")


class PromoRedemption(Base):
    __tablename__ = "promo_redemptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    promo_id: Mapped[int] = mapped_column(ForeignKey("promo_codes.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    redeemed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    promo: Mapped["PromoCode"] = relationship(back_populates="redemptions")


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("telegram_charge_id", name="uq_payments_telegram_charge_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    kind: Mapped[PaymentKind] = mapped_column(PgEnum(PaymentKind, name="paymentkind"), default=PaymentKind.topup)
    status: Mapped[PaymentStatus] = mapped_column(PgEnum(PaymentStatus, name="paymentstatus"), default=PaymentStatus.pending)
    provider_payload: Mapped[str | None] = mapped_column(Text)
    telegram_charge_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    promo_code_id: Mapped[int | None] = mapped_column(ForeignKey("promo_codes.id"), nullable=True)
    plan_id: Mapped[int | None] = mapped_column(ForeignKey("plans.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="payments")


class BalanceLedger(Base):
    __tablename__ = "balance_ledger"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    reason: Mapped[str] = mapped_column(String(64))
    payment_id: Mapped[int | None] = mapped_column(ForeignKey("payments.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="ledger_entries")


class VpnKey(Base):
    __tablename__ = "vpn_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    plan_id: Mapped[int | None] = mapped_column(ForeignKey("plans.id"), nullable=True)
    subscription_id: Mapped[int | None] = mapped_column(ForeignKey("subscriptions.id"), nullable=True)
    key: Mapped[str] = mapped_column(Text)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    status: Mapped[VpnKeyStatus] = mapped_column(PgEnum(VpnKeyStatus, name="vpnkeystatus"), default=VpnKeyStatus.active)

    user: Mapped["User"] = relationship(back_populates="vpn_keys")


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[TicketStatus] = mapped_column(PgEnum(TicketStatus, name="ticketstatus"), default=TicketStatus.open)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="tickets")
    messages: Mapped[list["TicketMessage"]] = relationship(back_populates="ticket")


class TicketSenderRole(str, enum.Enum):
    user = "user"
    admin = "admin"


class TicketMessage(Base):
    __tablename__ = "ticket_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("support_tickets.id"))
    sender_role: Mapped[TicketSenderRole] = mapped_column(PgEnum(TicketSenderRole, name="ticketsenderrole"))
    message_text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    ticket: Mapped["SupportTicket"] = relationship(back_populates="messages")


class AppSetting(Base):
    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
