from bot.db.models import SupportTicket
from bot.repositories.support import SupportRepository


class SupportService:
    def __init__(self, repo: SupportRepository):
        self.repo = repo

    async def create_ticket(self, user_id: int, text: str, photo_file_id: str | None = None) -> SupportTicket:
        """Create a new support ticket and return it (with assigned id)."""
        return await self.repo.create_ticket(user_id=user_id, text=text, photo_file_id=photo_file_id)
