from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.repositories.user import UserRepository
from bot.keyboards.main_menu import channel_check_keyboard, main_menu_keyboard
from bot.utils.channel_subscription import check_channel_subscription
from bot.utils.formatters import format_main_menu
from bot.utils.messages import edit_or_send_banner, send_or_answer_banner

router = Router()


async def _safe_edit_text(call: CallbackQuery, text: str, reply_markup) -> None:
    try:
        await edit_or_send_banner(
            call.message,
            text,
            reply_markup=reply_markup,
            banner_path=settings.message_banner_main_path,
        )
    except TelegramBadRequest:
        pass


@router.message(CommandStart())
async def cmd_start(message: Message, session: AsyncSession) -> None:
    is_member, reason = await check_channel_subscription(message.bot, message.from_user.id)

    if not is_member:
        details = ""
        if reason == "bot_not_admin":
            details = (
                "\n\n⚠️ Бот не может проверить подписку. "
                "Добавьте бота администратором канала."
            )
        elif reason == "chat_not_found":
            details = "\n\n⚠️ Канал не найден. Проверьте настройки канала."
        await send_or_answer_banner(
            message,
            "Чтобы продолжить использование бота, подпишитесь на Telegram-канал.\n\n"
            f"Канал: {settings.normalized_channel_username}\n"
            "После подписки нажмите «✅ Проверить»."
            f"{details}",
            reply_markup=channel_check_keyboard(),
            banner_path=settings.message_banner_main_path,
        )
        return

    repo = UserRepository(session)
    user, created = await repo.get_or_create(
        tg_id=message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
    )
    await send_or_answer_banner(
        message,
        await format_main_menu(user, session),
        reply_markup=main_menu_keyboard(),
        banner_path=settings.message_banner_main_path,
    )


@router.callback_query(lambda c: c.data == "check:subscription")
async def check_subscription_callback(call: CallbackQuery, session: AsyncSession) -> None:
    """Re-entry point after user subscribes to channel."""
    is_member, reason = await check_channel_subscription(call.bot, call.from_user.id)

    if not is_member:
        details = ""
        if reason == "bot_not_admin":
            details = "\n\n⚠️ Бот не администратор канала и не может проверить подписку."
        elif reason == "chat_not_found":
            details = "\n\n⚠️ Канал не найден. Проверьте VPN_CHANNEL_ID / VPN_CHANNEL_USERNAME."
        await _safe_edit_text(
            call,
            "Подписка пока не найдена. Подпишитесь на канал и нажмите проверку снова."
            f"{details}",
            reply_markup=channel_check_keyboard(),
        )
        await call.answer("Подписка не подтверждена", show_alert=True)
        return

    repo = UserRepository(session)
    user, _ = await repo.get_or_create(
        tg_id=call.from_user.id,
        username=call.from_user.username,
        full_name=call.from_user.full_name,
    )
    await _safe_edit_text(call, await format_main_menu(user, session), main_menu_keyboard())
    await call.answer("✅ Подписка подтверждена!")


@router.callback_query(lambda c: c.data == "menu:main")
async def back_to_menu(call: CallbackQuery, session: AsyncSession) -> None:
    repo = UserRepository(session)
    user, _ = await repo.get_or_create(
        tg_id=call.from_user.id,
        username=call.from_user.username,
        full_name=call.from_user.full_name,
    )
    await _safe_edit_text(call, await format_main_menu(user, session), main_menu_keyboard())
    await call.answer()
