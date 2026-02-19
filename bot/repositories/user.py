from sqlalchemy import select
from bot.db.models import User
from bot.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    model = User

    async def get_by_tg_id(self, tg_id: int) -> User | None:
        result = await self.session.execute(select(User).where(User.id == tg_id))
        return result.scalar_one_or_none()

    async def get_by_tg_id_for_update(self, tg_id: int) -> User | None:
        result = await self.session.execute(
            select(User).where(User.id == tg_id).with_for_update()
        )
        return result.scalar_one_or_none()

    async def get_or_create(
        self, tg_id: int, username: str | None, full_name: str | None
    ) -> tuple[User, bool]:
        user = await self.get_by_tg_id(tg_id)
        if user:
            return user, False
        user = User(id=tg_id, username=username, full_name=full_name)
        await self.save(user)
        return user, True
