from sqlalchemy import select
from bot.db.models import PromoCode, PromoRedemption
from bot.repositories.base import BaseRepository


class PromoRepository(BaseRepository[PromoCode]):
    model = PromoCode

    async def get_by_code(self, code: str) -> PromoCode | None:
        result = await self.session.execute(
            select(PromoCode).where(PromoCode.code == code)
        )
        return result.scalar_one_or_none()

    async def get_by_code_for_update(self, code: str) -> PromoCode | None:
        result = await self.session.execute(
            select(PromoCode).where(PromoCode.code == code).with_for_update()
        )
        return result.scalar_one_or_none()

    async def has_user_redeemed(self, promo_id: int, user_id: int) -> bool:
        result = await self.session.execute(
            select(PromoRedemption).where(
                PromoRedemption.promo_id == promo_id,
                PromoRedemption.user_id == user_id,
            )
        )
        return result.scalar_one_or_none() is not None

    async def add_redemption(self, promo_id: int, user_id: int) -> None:
        redemption = PromoRedemption(promo_id=promo_id, user_id=user_id)
        self.session.add(redemption)
        await self.session.commit()

    async def list_active(self, offset: int = 0, limit: int = 10) -> list[PromoCode]:
        result = await self.session.execute(
            select(PromoCode)
            .where(PromoCode.is_active == True)
            .order_by(PromoCode.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_all(self, offset: int = 0, limit: int = 10) -> list[PromoCode]:
        result = await self.session.execute(
            select(PromoCode)
            .order_by(PromoCode.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def delete(self, promo: PromoCode) -> None:
        await self.session.delete(promo)
        await self.session.commit()
