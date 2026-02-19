from sqlalchemy import select
from decimal import Decimal
from bot.db.models import Plan
from bot.repositories.base import BaseRepository


class PlanRepository(BaseRepository[Plan]):
    model = Plan

    async def get_active_plans(self) -> list[Plan]:
        result = await self.session.execute(
            select(Plan).where(Plan.is_active == True)
        )
        return list(result.scalars().all())

    async def ensure_defaults(self) -> None:
        plans = await self.get_active_plans()
        if plans:
            return

        self.session.add_all(
            [
                Plan(
                    name="VPN",
                    slug="vpn",
                    description="Базовый VPN-тариф",
                    price=Decimal("10.00"),
                    period_days=30,
                    is_active=True,
                ),
                Plan(
                    name="VPN + обход",
                    slug="vpn_bypass",
                    description="VPN с дополнительным обходом блокировок",
                    price=Decimal("20.00"),
                    period_days=30,
                    is_active=True,
                ),
            ]
        )
        await self.session.commit()
