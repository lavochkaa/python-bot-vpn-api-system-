from sqlalchemy import select
from bot.db.models import Payment
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
