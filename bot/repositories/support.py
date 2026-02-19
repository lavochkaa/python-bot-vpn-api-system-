from sqlalchemy import select

from bot.db.models import SupportTicket, TicketStatus
from bot.repositories.base import BaseRepository


class SupportRepository(BaseRepository[SupportTicket]):
    model = SupportTicket

    async def get_recent(self, limit: int = 10) -> list[SupportTicket]:
        result = await self.session.execute(
            select(SupportTicket)
            .order_by(SupportTicket.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_user_tickets(self, user_id: int, limit: int = 10) -> list[SupportTicket]:
        result = await self.session.execute(
            select(SupportTicket)
            .where(SupportTicket.user_id == user_id)
            .order_by(SupportTicket.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_by_status(self, status: TicketStatus, limit: int = 20) -> list[SupportTicket]:
        result = await self.session.execute(
            select(SupportTicket)
            .where(SupportTicket.status == status)
            .order_by(SupportTicket.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())
