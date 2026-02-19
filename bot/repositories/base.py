from typing import Generic, TypeVar, Type
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from bot.db.base import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    model: Type[ModelT]

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, pk: int) -> ModelT | None:
        return await self.session.get(self.model, pk)

    async def save(self, obj: ModelT) -> ModelT:
        self.session.add(obj)
        await self.session.commit()
        await self.session.refresh(obj)
        return obj

    async def all(self) -> list[ModelT]:
        result = await self.session.execute(select(self.model))
        return list(result.scalars().all())
