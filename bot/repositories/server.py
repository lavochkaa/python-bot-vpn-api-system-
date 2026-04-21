from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import TrackedServer


class ServerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_all(self) -> list[TrackedServer]:
        result = await self._session.execute(
            select(TrackedServer).order_by(TrackedServer.created_at)
        )
        return list(result.scalars().all())

    async def get(self, server_id: int) -> TrackedServer | None:
        return await self._session.get(TrackedServer, server_id)

    async def save(self, server: TrackedServer) -> TrackedServer:
        self._session.add(server)
        await self._session.flush()
        return server

    async def delete(self, server: TrackedServer) -> None:
        await self._session.delete(server)
        await self._session.flush()

    async def update_status(
        self,
        server: TrackedServer,
        status: str,
    ) -> None:
        server.last_status = status
        server.last_checked_at = datetime.now(timezone.utc)
        await self._session.flush()
