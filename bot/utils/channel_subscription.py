from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest

from bot.config import settings


async def check_channel_subscription(bot: Bot, user_id: int) -> tuple[bool, str | None]:
    candidates: list[int | str] = []
    if settings.vpn_channel_id is not None:
        candidates.append(settings.vpn_channel_id)

    raw_username = settings.vpn_channel_username.strip()
    normalized = settings.normalized_channel_username
    if raw_username:
        candidates.extend([raw_username, normalized, normalized.lstrip("@")])

    seen: set[int | str] = set()
    for target in candidates:
        if target in seen:
            continue
        seen.add(target)
        try:
            chat = await bot.get_chat(target)
        except TelegramBadRequest as exc:
            msg = str(exc).lower()
            if "chat not found" in msg:
                continue
            if "administrator" in msg or "not enough rights" in msg or "member list is inaccessible" in msg:
                return False, "bot_not_admin"
            return False, "telegram_bad_request"
        except TelegramAPIError:
            continue

        try:
            member = await bot.get_chat_member(chat.id, user_id)
            is_member = member.status not in ("left", "kicked", "banned")
            return is_member, None
        except TelegramBadRequest as exc:
            msg = str(exc).lower()
            if "chat not found" in msg:
                continue
            if "administrator" in msg or "not enough rights" in msg or "member list is inaccessible" in msg:
                return False, "bot_not_admin"
            return False, "telegram_bad_request"
        except TelegramAPIError:
            return False, "telegram_api_error"

    return False, "chat_not_found"
