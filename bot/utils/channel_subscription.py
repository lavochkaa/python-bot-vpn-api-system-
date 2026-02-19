from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest

from bot.config import settings


async def check_channel_subscription(bot: Bot, user_id: int) -> tuple[bool, str | None]:
    target = settings.vpn_channel_id or settings.normalized_channel_username
    try:
        member = await bot.get_chat_member(target, user_id)
        is_member = member.status not in ("left", "kicked", "banned")
        return is_member, None
    except TelegramBadRequest as exc:
        msg = str(exc).lower()
        if "chat not found" in msg:
            return False, "chat_not_found"
        if "administrator" in msg or "not enough rights" in msg or "member list is inaccessible" in msg:
            return False, "bot_not_admin"
        return False, "telegram_bad_request"
    except TelegramAPIError:
        return False, "telegram_api_error"
