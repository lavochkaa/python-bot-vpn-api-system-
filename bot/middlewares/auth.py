import logging
from typing import Callable, Awaitable, Any
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery
from bot.config import settings
from bot.utils.channel_subscription import check_channel_subscription

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

        if isinstance(event, (Message, CallbackQuery)):
            user_id = event.from_user.id
            if user_id in settings.admin_id_set:
                return await handler(event, data)
            if isinstance(event, Message) and event.successful_payment:
                return await handler(event, data)

            # Allow the "check subscription" callback to pass through
            if isinstance(event, CallbackQuery) and event.data == "check:subscription":
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
                if isinstance(event, Message):
                    await event.answer(text, reply_markup=kb)
                elif isinstance(event, CallbackQuery):
                    await event.message.answer(text, reply_markup=kb)
                    await event.answer()
                return  # block further processing

        return await handler(event, data)
