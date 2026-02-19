from sqlalchemy import select
from bot.db.models import VpnKey, VpnKeyStatus
from bot.repositories.base import BaseRepository


class VpnKeyRepository(BaseRepository[VpnKey]):
    model = VpnKey

    async def get_user_keys(self, user_id: int, limit: int = 5) -> list[VpnKey]:
        result = await self.session.execute(
            select(VpnKey).where(
                VpnKey.user_id == user_id,
                VpnKey.status == VpnKeyStatus.active,
            )
            .order_by(VpnKey.issued_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_user_key(self, user_id: int, key_id: int) -> VpnKey | None:
        result = await self.session.execute(
            select(VpnKey).where(
                VpnKey.id == key_id,
                VpnKey.user_id == user_id,
                VpnKey.status == VpnKeyStatus.active,
            )
        )
        return result.scalar_one_or_none()
