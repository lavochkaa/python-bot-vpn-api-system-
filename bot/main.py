import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from bot.config import settings
from bot.db.base import AsyncSessionFactory
from bot.middlewares.db import DbSessionMiddleware
from bot.middlewares.auth import ChannelSubscriptionMiddleware
from bot.handlers import common, balance, subscription, keys, info, support, admin, admin_servers
from bot.repositories.plan import PlanRepository


def setup_logging() -> None:
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


async def main() -> None:
    setup_logging()
    logger = logging.getLogger(__name__)
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is not configured.")

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    async with AsyncSessionFactory() as session:
        await PlanRepository(session).ensure_defaults()

    # Middlewares (order matters: db first, then auth)
    dp.update.middleware(DbSessionMiddleware())
    dp.update.middleware(ChannelSubscriptionMiddleware())

    # Routers
    dp.include_router(common.router)
    dp.include_router(balance.router)
    dp.include_router(subscription.router)
    dp.include_router(keys.router)
    dp.include_router(info.router)
    dp.include_router(support.router)
    dp.include_router(admin.router)
    dp.include_router(admin_servers.router)

    logger.info("Starting bot polling...")
    await dp.start_polling(bot, skip_updates=True)


if __name__ == "__main__":
    asyncio.run(main())
