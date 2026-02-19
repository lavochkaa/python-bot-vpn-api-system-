from bot.db.models import SupportTicket
from bot.repositories.support import SupportRepository


class SupportService:
    def __init__(self, repo: SupportRepository):
        self.repo = repo

    async def create_ticket(self, user_id: int, text: str) -> SupportTicket:
        """Create a new support ticket and return it (with assigned id)."""
        ticket = SupportTicket(user_id=user_id, text=text)
        return await self.repo.save(ticket)
