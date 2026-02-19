from bot.db.models import User
from bot.repositories.user import UserRepository


class UserService:
    def __init__(self, repo: UserRepository):
        self.repo = repo

    async def get_or_register(
        self, tg_id: int, username: str | None, full_name: str | None
    ) -> tuple[User, bool]:
        """Get existing user or create new one. Returns (user, is_new)."""
        return await self.repo.get_or_create(tg_id, username, full_name)
