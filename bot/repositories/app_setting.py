from sqlalchemy import select

from bot.db.models import AppSetting
from bot.repositories.base import BaseRepository


class AppSettingRepository(BaseRepository[AppSetting]):
    model = AppSetting

    async def get_by_key(self, key: str) -> AppSetting | None:
        result = await self.session.execute(
            select(AppSetting).where(AppSetting.key == key)
        )
        return result.scalar_one_or_none()

    async def get_bool(self, key: str, default: bool = False) -> bool:
        item = await self.get_by_key(key)
        if not item or item.value is None:
            return default
        return item.value.strip().lower() in {"1", "true", "on", "yes", "enabled"}

    async def set_bool(self, key: str, value: bool) -> None:
        item = await self.get_by_key(key)
        raw = "1" if value else "0"
        if item is None:
            item = AppSetting(key=key, value=raw)
            self.session.add(item)
        else:
            item.value = raw
            self.session.add(item)
        await self.session.commit()
