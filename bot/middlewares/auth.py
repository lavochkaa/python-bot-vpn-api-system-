import logging
from typing import Callable, Awaitable, Any
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery, Update
from bot.config import settings
from bot.repositories.app_setting import AppSettingRepository
from bot.utils.channel_subscription import check_channel_subscription
from bot.utils.maintenance import build_maintenance_notice

logger = logging.getLogger(__name__)


class ChannelSubscriptionMiddleware(BaseMiddleware):
    """
    Blocks all handlers until the user subscribes to the required channel.
    Shows a subscribe prompt with a re-check button.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        from bot.keyboards.main_menu import channel_check_keyboard

        bot = data["bot"]
        session = data.get("session")

        message: Message | None = None
        callback: CallbackQuery | None = None

        if isinstance(event, Message):
            message = event
        elif isinstance(event, CallbackQuery):
            callback = event
            if isinstance(event.message, Message):
                message = event.message
        elif isinstance(event, Update):
            callback = event.callback_query
            if isinstance(event.message, Message):
                message = event.message
            elif callback is not None and isinstance(callback.message, Message):
                message = callback.message

        actor = callback.from_user if callback else (message.from_user if message else None)

        if actor is not None:
            user_id = actor.id
            if user_id in settings.admin_id_set:
                return await handler(event, data)

            if session is not None:
                maintenance_enabled = await AppSettingRepository(session).get_bool(
                    "maintenance_mode",
                    default=False,
                )
                if maintenance_enabled:
                    text, kb = await build_maintenance_notice(session, user_id)
                    if message is not None:
                        await message.answer(text, reply_markup=kb)
                    if callback is not None:
                        await callback.answer("Бот временно на техработах", show_alert=True)
                    return

            if message is not None and message.successful_payment:
                return await handler(event, data)

            # Let /start reach its dedicated handler, which already knows
            # how to show the subscription gate and the main menu safely.
            if message is not None and (message.text or "").strip().startswith("/start"):
                return await handler(event, data)

            # Allow the "check subscription" callback to pass through
            if callback is not None and callback.data == "check:subscription":
                return await handler(event, data)

            is_member, reason = await check_channel_subscription(bot, user_id)
            if reason:
                logger.warning("Subscription check failed for user_id=%s reason=%s", user_id, reason)

            if not is_member:
                details = ""
                if reason == "bot_not_admin":
                    details = (
                        "\n\n⚠️ Бот не может проверить подписку. "
                        "Добавьте бота администратором канала."
                    )
                elif reason == "chat_not_found":
                    details = (
                        "\n\n⚠️ Канал не найден. Проверьте VPN_CHANNEL_ID / VPN_CHANNEL_USERNAME."
                    )
                text = (
                    "Чтобы продолжить использование бота, подпишитесь на Telegram-канал.\n\n"
                    f"Канал: {settings.normalized_channel_username}\n"
                    "После подписки нажмите «✅ Проверить»."
                    f"{details}"
                )
                kb = channel_check_keyboard()
                if message is not None:
                    await message.answer(text, reply_markup=kb)
                if callback is not None:
                    await callback.answer()
                return  # block further processing

        return await handler(event, data)
