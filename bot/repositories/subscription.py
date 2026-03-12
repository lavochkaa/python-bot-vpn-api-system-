from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from bot.db.models import Subscription
from bot.repositories.base import BaseRepository


class SubscriptionRepository(BaseRepository[Subscription]):
    model = Subscription

    async def get_active(self, user_id: int) -> Subscription | None:
        result = await self.session.execute(
            select(Subscription).where(
                Subscription.user_id == user_id,
                Subscription.is_active == True,
            )
            .options(selectinload(Subscription.plan))
        )
        return result.scalar_one_or_none()

    async def get_latest(self, user_id: int) -> Subscription | None:
        result = await self.session.execute(
            select(Subscription)
            .where(Subscription.user_id == user_id)
            .order_by(Subscription.id.desc())
            .limit(1)
            .options(selectinload(Subscription.plan))
        )
        return result.scalar_one_or_none()

    async def get_all_current_active(self) -> list[Subscription]:
        now = datetime.now(timezone.utc)
        result = await self.session.execute(
            select(Subscription)
            .where(
                Subscription.is_active == True,
                Subscription.expires_at.is_not(None),
                Subscription.expires_at > now,
            )
            .order_by(Subscription.expires_at.asc())
            .options(selectinload(Subscription.plan))
        )
        return list(result.scalars().all())
