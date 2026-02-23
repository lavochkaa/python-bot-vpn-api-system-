from datetime import datetime, timezone
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload

from bot.db.models import SupportTicket, TicketMessage, TicketStatus, TicketSenderRole
from bot.repositories.base import BaseRepository


class SupportRepository(BaseRepository[SupportTicket]):
    model = SupportTicket

    async def get_recent(self, limit: int = 10) -> list[SupportTicket]:
        result = await self.session.execute(
            select(SupportTicket)
            .order_by(SupportTicket.updated_at.desc(), SupportTicket.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_user_tickets(self, user_id: int, limit: int = 10) -> list[SupportTicket]:
        result = await self.session.execute(
            select(SupportTicket)
            .where(SupportTicket.user_id == user_id)
            .order_by(SupportTicket.updated_at.desc(), SupportTicket.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_by_status(self, status: TicketStatus, limit: int = 20) -> list[SupportTicket]:
        result = await self.session.execute(
            select(SupportTicket)
            .where(SupportTicket.status == status)
            .order_by(SupportTicket.updated_at.desc(), SupportTicket.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_user_tickets_paginated(self, user_id: int, page: int, page_size: int = 10) -> list[SupportTicket]:
        offset = max(page - 1, 0) * page_size
        result = await self.session.execute(
            select(SupportTicket)
            .where(SupportTicket.user_id == user_id)
            .order_by(SupportTicket.updated_at.desc(), SupportTicket.id.desc())
            .offset(offset)
            .limit(page_size)
        )
        return list(result.scalars().all())

    async def get_with_messages(self, ticket_id: int) -> SupportTicket | None:
        result = await self.session.execute(
            select(SupportTicket)
            .where(SupportTicket.id == ticket_id)
            .options(selectinload(SupportTicket.messages))
        )
        return result.scalar_one_or_none()

    async def create_ticket(self, user_id: int, text: str, subject: str | None = None) -> SupportTicket:
        now = datetime.now(timezone.utc)
        ticket = SupportTicket(
            user_id=user_id,
            subject=subject,
            text=text,
            status=TicketStatus.open,
            updated_at=now,
        )
        self.session.add(ticket)
        await self.session.flush()
        message = TicketMessage(
            ticket_id=ticket.id,
            sender_role=TicketSenderRole.user,
            message_text=text,
        )
        self.session.add(message)
        await self.session.commit()
        await self.session.refresh(ticket)
        return ticket

    async def add_message(
        self,
        ticket: SupportTicket,
        sender_role: TicketSenderRole,
        message_text: str,
    ) -> TicketMessage:
        now = datetime.now(timezone.utc)
        message = TicketMessage(
            ticket_id=ticket.id,
            sender_role=sender_role,
            message_text=message_text,
        )
        ticket.text = message_text
        if sender_role == TicketSenderRole.user:
            ticket.status = TicketStatus.pending
        elif ticket.status != TicketStatus.closed:
            ticket.status = TicketStatus.open
        ticket.updated_at = now
        self.session.add(message)
        self.session.add(ticket)
        await self.session.commit()
        await self.session.refresh(message)
        await self.session.refresh(ticket)
        return message

    async def get_messages(self, ticket_id: int, limit: int = 10) -> list[TicketMessage]:
        result = await self.session.execute(
            select(TicketMessage)
            .where(TicketMessage.ticket_id == ticket_id)
            .order_by(TicketMessage.created_at.desc(), TicketMessage.id.desc())
            .limit(limit)
        )
        return list(reversed(result.scalars().all()))

    async def get_open_waiting_admin(self, limit: int = 20) -> list[SupportTicket]:
        """Open or pending tickets where last message is from user."""
        last_msg_subq = (
            select(
                TicketMessage.ticket_id,
                func.max(TicketMessage.id).label("last_msg_id"),
            )
            .group_by(TicketMessage.ticket_id)
            .subquery()
        )
        result = await self.session.execute(
            select(SupportTicket)
            .join(last_msg_subq, last_msg_subq.c.ticket_id == SupportTicket.id)
            .join(TicketMessage, TicketMessage.id == last_msg_subq.c.last_msg_id)
            .where(
                SupportTicket.status.in_([TicketStatus.open, TicketStatus.pending]),
                TicketMessage.sender_role == TicketSenderRole.user,
            )
            .order_by(SupportTicket.updated_at.desc(), SupportTicket.id.desc())
            .limit(limit)
        )
        return list(result.scalars().all())
