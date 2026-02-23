from decimal import Decimal
from sqlalchemy import select, func
from bot.db.models import Payment, PaymentKind, PaymentStatus
from bot.repositories.base import BaseRepository


class PaymentRepository(BaseRepository[Payment]):
    model = Payment

    async def get_by_invoice(self, invoice_id: str) -> Payment | None:
        result = await self.session.execute(
            select(Payment).where(Payment.provider_payload == invoice_id)
        )
        return result.scalar_one_or_none()

    async def get_by_invoice_for_update(self, invoice_id: str) -> Payment | None:
        result = await self.session.execute(
            select(Payment)
            .where(Payment.provider_payload == invoice_id)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def get_by_telegram_charge_id(self, charge_id: str) -> Payment | None:
        result = await self.session.execute(
            select(Payment).where(Payment.telegram_charge_id == charge_id)
        )
        return result.scalar_one_or_none()

    async def topup_stats_by_user(self) -> dict[int, tuple[int, Decimal]]:
        result = await self.session.execute(
            select(
                Payment.user_id,
                func.count(Payment.id),
                func.coalesce(func.sum(Payment.amount), 0),
            )
            .where(
                Payment.kind == PaymentKind.topup,
                Payment.status == PaymentStatus.completed,
            )
            .group_by(Payment.user_id)
        )
        rows = result.all()
        return {int(user_id): (int(cnt), Decimal(str(total))) for user_id, cnt, total in rows}

    async def topup_totals(self) -> tuple[int, Decimal]:
        result = await self.session.execute(
            select(
                func.count(Payment.id),
                func.coalesce(func.sum(Payment.amount), 0),
            )
            .where(
                Payment.kind == PaymentKind.topup,
                Payment.status == PaymentStatus.completed,
            )
        )
        cnt, total = result.one()
        return int(cnt or 0), Decimal(str(total or 0))
