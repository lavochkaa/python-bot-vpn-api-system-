from datetime import datetime, timezone

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.db.models import User
from bot.repositories.subscription import SubscriptionRepository
from bot.repositories.vpn_key import VpnKeyRepository


def _is_effectively_active(sub) -> bool:
    if not sub or not sub.is_active or not sub.expires_at:
        return False
    return sub.expires_at > datetime.now(timezone.utc)


async def get_user_connect_url(session: AsyncSession, user_id: int) -> str | None:
    sub = await SubscriptionRepository(session).get_active(user_id)
    if not _is_effectively_active(sub):
        return None

    keys = await VpnKeyRepository(session).get_user_keys(user_id, limit=1)
    if not keys:
        return None

    key = (keys[0].key or "").strip()
    if key.startswith(("https://", "http://")):
        return key
    return None


async def build_maintenance_notice(
    session: AsyncSession,
    user_id: int,
) -> tuple[str, InlineKeyboardMarkup]:
    connect_url = await get_user_connect_url(session, user_id)
    channel_username = settings.normalized_channel_username.lstrip("@")

    text = (
        "⚙️ <b>Сейчас бот находится на техническом обслуживании.</b>\n\n"
        "Следите за новостями в канале."
    )
    if connect_url:
        safe_url = connect_url.replace("<", "").replace(">", "")
        text += (
            "\n\n"
            "Но вам все еще доступна возможность подключить VPN.\n"
            "Вот ваша ссылка:\n"
            f"{safe_url}"
        )

    builder = InlineKeyboardBuilder()
    builder.button(text="📢 Канал", url=f"https://t.me/{channel_username}")
    if connect_url:
        builder.button(text="🔌 Подключить VPN", url=connect_url)
    builder.adjust(1)
    return text, builder.as_markup()


async def get_all_user_ids(session: AsyncSession) -> list[int]:
    rows = await session.execute(select(User.id))
    return [row[0] for row in rows.all()]
