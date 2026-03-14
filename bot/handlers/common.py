import logging

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.repositories.user import UserRepository
from bot.keyboards.main_menu import (
    channel_check_keyboard,
    main_menu_keyboard,
    subscription_warning_keyboard,
)
from bot.utils.channel_subscription import check_channel_subscription
from bot.utils.formatters import build_main_menu_snapshot
from bot.utils.maintenance import get_user_connect_url
from bot.utils.messages import edit_or_send_banner, send_or_answer_banner

router = Router()
logger = logging.getLogger(__name__)


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


async def _maybe_send_subscription_warning(target: Message, *, remaining_gb: float | None, remaining_days: int | None) -> None:
    warn_gb = remaining_gb is not None and remaining_gb <= 1.0
    warn_day = remaining_days is not None and remaining_days <= 1
    if not warn_gb and not warn_day:
        return

    parts: list[str] = ["⚠️ <b>Внимание по подписке</b>"]
    if warn_gb:
        parts.append(f"• Осталось трафика: <b>{round(max(remaining_gb or 0.0, 0.0), 2)} ГБ</b>")
    if warn_day:
        parts.append("• До окончания подписки: <b>1 день или меньше</b>")
    parts.append("Выберите действие:")
    text = "\n".join(parts)

    await target.answer(
        text,
        reply_markup=subscription_warning_keyboard(
            show_refresh_gb=warn_gb,
            show_renew=warn_day,
        ),
    )


async def _handle_start(message: Message, session: AsyncSession, state: FSMContext) -> None:
    logger.info("Handling /start for user_id=%s text=%r", message.from_user.id, message.text)
    await state.clear()
    try:
        is_member, reason = await check_channel_subscription(message.bot, message.from_user.id)
    except Exception:
        logger.exception("Unexpected failure during /start subscription check for user_id=%s", message.from_user.id)
        is_member, reason = True, None

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
    user, _ = await repo.get_or_create(
        tg_id=message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
    )
    try:
        snapshot = await build_main_menu_snapshot(user, session)
        connect_url = await get_user_connect_url(session, user.id)
        await send_or_answer_banner(
            message,
            snapshot.text,
            reply_markup=main_menu_keyboard(connect_url),
            banner_path=settings.message_banner_main_path,
        )
        await _maybe_send_subscription_warning(
            message,
            remaining_gb=snapshot.remaining_gb,
            remaining_days=snapshot.remaining_days,
        )
    except Exception:
        await send_or_answer_banner(
            message,
            "👋 Бот активен.\n\nОткройте главное меню:",
            reply_markup=main_menu_keyboard(),
            banner_path=settings.message_banner_main_path,
        )
        logger.exception("Failed to render /start menu for user_id=%s", message.from_user.id)


@router.message(CommandStart())
async def cmd_start(message: Message, session: AsyncSession, state: FSMContext) -> None:
    await _handle_start(message, session, state)


@router.message(F.text.regexp(r"^/start(?:@[A-Za-z0-9_]+)?(?:\s+.*)?$"))
async def cmd_start_fallback(message: Message, session: AsyncSession, state: FSMContext) -> None:
    await _handle_start(message, session, state)


@router.callback_query(lambda c: c.data == "check:subscription")
async def check_subscription_callback(call: CallbackQuery, session: AsyncSession) -> None:
    """Re-entry point after user subscribes to channel."""
    try:
        is_member, reason = await check_channel_subscription(call.bot, call.from_user.id)
    except Exception:
        logger.exception("Unexpected failure during subscription re-check for user_id=%s", call.from_user.id)
        is_member, reason = True, None

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
    try:
        snapshot = await build_main_menu_snapshot(user, session)
        connect_url = await get_user_connect_url(session, user.id)
        await _safe_edit_text(call, snapshot.text, main_menu_keyboard(connect_url))
        if call.message:
            await _maybe_send_subscription_warning(
                call.message,
                remaining_gb=snapshot.remaining_gb,
                remaining_days=snapshot.remaining_days,
            )
        await call.answer("✅ Подписка подтверждена!")
    except Exception:
        logger.exception("Failed to render menu after subscription re-check for user_id=%s", call.from_user.id)
        await _safe_edit_text(call, "👋 Бот активен.\n\nОткройте главное меню:", main_menu_keyboard())
        await call.answer("✅ Проверка выполнена")


@router.callback_query(lambda c: c.data == "menu:main")
async def back_to_menu(call: CallbackQuery, session: AsyncSession) -> None:
    repo = UserRepository(session)
    user, _ = await repo.get_or_create(
        tg_id=call.from_user.id,
        username=call.from_user.username,
        full_name=call.from_user.full_name,
    )
    try:
        snapshot = await build_main_menu_snapshot(user, session)
        connect_url = await get_user_connect_url(session, user.id)
        await _safe_edit_text(call, snapshot.text, main_menu_keyboard(connect_url))
        if call.message:
            await _maybe_send_subscription_warning(
                call.message,
                remaining_gb=snapshot.remaining_gb,
                remaining_days=snapshot.remaining_days,
            )
    except Exception:
        logger.exception("Failed to render main menu for user_id=%s", call.from_user.id)
        await _safe_edit_text(call, "👋 Бот активен.\n\nОткройте главное меню:", main_menu_keyboard())
    await call.answer()
