import asyncio
from decimal import Decimal, ROUND_CEILING
import html
import logging
import re

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import BalanceLedger, PromoTarget
from bot.constants.subscription_pricing import (
    DURATION_MONTH_OPTIONS,
    DURATION_MONTH_TO_DAYS,
    DEVICE_LIMIT_OPTIONS,
    DEVICE_LIMIT_PRICE_MULTIPLIERS,
    SUBSCRIPTION_PRICE_MATRIX,
    TRAFFIC_OPTIONS,
)
from bot.keyboards.subscription import (
    insufficient_balance_keyboard,
    subscription_activated_keyboard,
    subscription_configurator_keyboard,
)
from bot.providers.vpn.factory import build_vpn_provider
from bot.repositories.ledger import BalanceLedgerRepository
from bot.repositories.plan import PlanRepository
from bot.repositories.promo import PromoRepository
from bot.repositories.subscription import SubscriptionRepository
from bot.repositories.user import UserRepository
from bot.repositories.vpn_key import VpnKeyRepository
from bot.services.promo import PromoService
from bot.services.subscription import SubscriptionService
from bot.states.subscription import SubscriptionStates
from bot.utils.messages import _short_text
from bot.utils.formatters import (
    format_subscription_for_user,
    invalidate_usage_cache,
    resolve_subscription_usage_info,
)

router = Router()
logger = logging.getLogger(__name__)
_subscription_locks: dict[int, asyncio.Lock] = {}

DEFAULT_PLAN_TYPE = "pc"
DEFAULT_PLAN_SLUG = "vpn"
DEFAULT_BUILD_PRESET = "max"
TRAFFIC_RESET_PRICE = Decimal("79")
_CONFIG_UNSET = object()


def _get_subscription_lock(user_id: int) -> asyncio.Lock:
    lock = _subscription_locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _subscription_locks[user_id] = lock
    return lock


async def _edit_only(message: Message, text: str, reply_markup=None) -> Message | None:
    rendered = _short_text(text)

    async def _try_edit_text(payload: str) -> bool:
        try:
            await message.edit_text(payload, reply_markup=reply_markup)
            return True
        except TelegramBadRequest as exc:
            err = str(exc).lower()
            if "message is not modified" in err:
                return True
            return False
        except TelegramNetworkError:
            return False

    async def _try_edit_caption(payload: str) -> bool:
        try:
            await message.edit_caption(caption=payload, reply_markup=reply_markup)
            return True
        except TelegramBadRequest as exc:
            err = str(exc).lower()
            if "message is not modified" in err:
                return True
            return False
        except TelegramNetworkError:
            return False

    # text messages
    if await _try_edit_text(rendered):
        return message

    # media messages (photo/video with caption)
    if await _try_edit_caption(rendered):
        return message

    # entity-safe retry in plain text/caption
    safe_plain = html.escape(re.sub(r"<[^>]+>", "", text))
    safe_rendered = _short_text(safe_plain)

    if await _try_edit_text(safe_rendered):
        return message
    if await _try_edit_caption(safe_rendered):
        return message

    try:
        return await message.answer(safe_rendered, reply_markup=reply_markup)
    except TelegramBadRequest:
        return None
    except TelegramNetworkError:
        return None


async def _edit_only_by_ids(bot, chat_id: int, message_id: int, text: str, reply_markup=None) -> tuple[int, int] | None:
    rendered = _short_text(text)
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=rendered,
            reply_markup=reply_markup,
        )
        return chat_id, message_id
    except TelegramBadRequest as exc:
        err = str(exc).lower()
        if "message is not modified" in err:
            return chat_id, message_id
    except TelegramNetworkError:
        return None

    try:
        await bot.edit_message_caption(
            chat_id=chat_id,
            message_id=message_id,
            caption=rendered,
            reply_markup=reply_markup,
        )
        return chat_id, message_id
    except TelegramBadRequest as exc:
        err = str(exc).lower()
        if "message is not modified" in err:
            return chat_id, message_id
    except TelegramNetworkError:
        return None

    safe_plain = html.escape(re.sub(r"<[^>]+>", "", text))
    safe_rendered = _short_text(safe_plain)
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=safe_rendered,
            reply_markup=reply_markup,
        )
        return chat_id, message_id
    except TelegramBadRequest:
        pass
    except TelegramNetworkError:
        return None
    try:
        await bot.edit_message_caption(
            chat_id=chat_id,
            message_id=message_id,
            caption=safe_rendered,
            reply_markup=reply_markup,
        )
        return chat_id, message_id
    except (TelegramBadRequest, TelegramNetworkError):
        pass

    try:
        sent = await bot.send_message(
            chat_id=chat_id,
            text=safe_rendered,
            reply_markup=reply_markup,
        )
        return sent.chat.id, sent.message_id
    except (TelegramBadRequest, TelegramNetworkError):
        return None


async def _store_subscription_screen_refs(state: FSMContext, screen: Message | None) -> None:
    if screen is None:
        return
    await state.update_data(
        sub_chat_id=screen.chat.id,
        sub_message_id=screen.message_id,
    )


async def _store_subscription_screen_refs_by_ids(
    state: FSMContext,
    refs: tuple[int, int] | None,
) -> None:
    if refs is None:
        return
    chat_id, message_id = refs
    await state.update_data(
        sub_chat_id=chat_id,
        sub_message_id=message_id,
    )


def _safe_error_text(exc: Exception) -> str:
    text = str(exc)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > 320:
        text = text[:320] + "..."
    return html.escape(text)


def _calculate_price(traffic_gb: int | None, term_months: int | None, device_limit: int | None = 3) -> Decimal | None:
    if traffic_gb is None or term_months is None or device_limit is None:
        return None
    duration_days = DURATION_MONTH_TO_DAYS.get(term_months)
    if duration_days is None:
        return None
    base_price = SUBSCRIPTION_PRICE_MATRIX.get((duration_days, traffic_gb))
    multiplier = DEVICE_LIMIT_PRICE_MULTIPLIERS.get(int(device_limit))
    if base_price is None or multiplier is None:
        return None
    price = base_price * multiplier
    return price.quantize(Decimal("1"), rounding=ROUND_CEILING)


def _normalize_constructor_value(value: object, allowed: tuple[int, ...]) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed in allowed else None


def _parse_constructor_callback(
    callback_data: str | None,
    prefix: str,
) -> tuple[int | None, int | None, int | None] | None:
    if not callback_data:
        return None
    parts = callback_data.split(":")
    if len(parts) != 4 or parts[0] != prefix:
        return None
    return (
        _normalize_constructor_value(parts[1], TRAFFIC_OPTIONS),
        _normalize_constructor_value(parts[2], DURATION_MONTH_OPTIONS),
        _normalize_constructor_value(parts[3], DEVICE_LIMIT_OPTIONS),
    )


def _get_constructor_selection(data: dict[str, object]) -> tuple[int | None, int | None, int | None]:
    return (
        _normalize_constructor_value(data.get("sub_traffic_gb"), TRAFFIC_OPTIONS),
        _normalize_constructor_value(data.get("sub_term_months"), DURATION_MONTH_OPTIONS),
        _normalize_constructor_value(data.get("sub_device_limit"), DEVICE_LIMIT_OPTIONS),
    )


def _missing_constructor_fields(
    traffic_gb: int | None,
    term_months: int | None,
    device_limit: int | None,
) -> list[str]:
    missing: list[str] = []
    if traffic_gb is None:
        missing.append("объем")
    if term_months is None:
        missing.append("срок")
    if device_limit is None:
        missing.append("устройства")
    return missing


def _build_missing_constructor_text(
    traffic_gb: int | None,
    term_months: int | None,
    device_limit: int | None,
) -> str:
    missing = _missing_constructor_fields(traffic_gb, term_months, device_limit)
    if not missing:
        return ""
    return "Не выбрано: " + ", ".join(missing) + "."


def _build_configurator_text(
    traffic_gb: int | None,
    term_months: int | None,
    device_limit: int | None,
    *,
    promo_code: str | None = None,
    final_price: Decimal | None = None,
) -> str:
    volume_title = f"{traffic_gb} ГБ" if traffic_gb is not None else "не выбран"
    term_title = f"{term_months} мес" if term_months is not None else "не выбран"
    devices_title = f"{device_limit} устр." if device_limit is not None else "не выбраны"
    price = _calculate_price(traffic_gb, term_months, device_limit)
    if final_price is not None:
        price = final_price
    price_title = f"{price} ₽" if price is not None else "будет рассчитана после выбора параметров"
    promo_title = promo_code if promo_code else "не применен"
    missing_text = _build_missing_constructor_text(traffic_gb, term_months, device_limit)
    missing_block = f"\n⚠️ <b>{missing_text}</b>\n" if missing_text else "\n"

    return (
        "📦 <b>Настройте подписку самостоятельно</b>\n\n"
        "<b>Выбранная конфигурация:</b>\n"
        f"• Объем: <b>{volume_title}</b>\n"
        f"• Срок: <b>{term_title}</b>\n"
        f"• Устройства: <b>{devices_title}</b>\n"
        f"• Промокод: <b>{promo_title}</b>\n"
        f"• Цена: <b>{price_title}</b>\n"
        f"{missing_block}"
        "Выберите нужные параметры ниже."
    )


async def _set_constructor_selection(
    state: FSMContext,
    *,
    traffic_gb: int | None | object = _CONFIG_UNSET,
    term_months: int | None | object = _CONFIG_UNSET,
    device_limit: int | None | object = _CONFIG_UNSET,
) -> tuple[int | None, int | None, int | None]:
    data = await state.get_data()
    current_traffic_gb, current_term_months, current_device_limit = _get_constructor_selection(data)

    next_traffic_gb = current_traffic_gb if traffic_gb is _CONFIG_UNSET else traffic_gb
    next_term_months = current_term_months if term_months is _CONFIG_UNSET else term_months
    next_device_limit = current_device_limit if device_limit is _CONFIG_UNSET else device_limit

    updates: dict[str, object] = {
        "sub_traffic_gb": next_traffic_gb,
        "sub_term_months": next_term_months,
        "sub_device_limit": next_device_limit,
        "plan_type": DEFAULT_PLAN_TYPE,
        "plan_slug": DEFAULT_PLAN_SLUG,
        "build_preset": DEFAULT_BUILD_PRESET,
    }
    if (
        next_traffic_gb != current_traffic_gb
        or next_term_months != current_term_months
        or next_device_limit != current_device_limit
    ):
        updates["sub_promo_code"] = ""
        updates["sub_promo_id"] = None
        updates["sub_promo_final_price"] = ""

    await state.update_data(**updates)
    return next_traffic_gb, next_term_months, next_device_limit


async def _reset_constructor_state(state: FSMContext) -> None:
    await state.update_data(
        sub_traffic_gb=None,
        sub_term_months=None,
        sub_device_limit=None,
        sub_promo_code="",
        sub_promo_id=None,
        sub_promo_final_price="",
        plan_type=DEFAULT_PLAN_TYPE,
        plan_slug=DEFAULT_PLAN_SLUG,
        build_preset=DEFAULT_BUILD_PRESET,
    )


async def _build_configurator_view(
    state: FSMContext,
) -> tuple[str, object]:
    data = await state.get_data()
    traffic_gb, term_months, device_limit = _get_constructor_selection(data)
    promo_code = data.get("sub_promo_code")
    promo_final = data.get("sub_promo_final_price")
    promo_final_price = Decimal(str(promo_final)) if promo_final else None

    text = _build_configurator_text(
        traffic_gb,
        term_months,
        device_limit,
        promo_code=str(promo_code) if promo_code else None,
        final_price=promo_final_price,
    )
    keyboard = subscription_configurator_keyboard(
        traffic_gb,
        term_months,
        device_limit,
        has_promo=bool(promo_code),
    )
    return text, keyboard


async def _resolve_plan_id_by_slug(session: AsyncSession, slug: str) -> int | None:
    plans = await PlanRepository(session).get_active_plans()
    for plan in plans:
        if plan.slug == slug:
            return plan.id
    return None


def _build_service(session: AsyncSession) -> SubscriptionService:
    return SubscriptionService(
        SubscriptionRepository(session),
        PlanRepository(session),
        UserRepository(session),
        BalanceLedgerRepository(session),
        VpnKeyRepository(session),
        build_vpn_provider(),
    )


async def _build_active_subscription_text(
    sub,
    *,
    session: AsyncSession,
    user_id: int,
    subscription_uuid: str | None,
) -> str:
    _ = subscription_uuid
    user = await UserRepository(session).get_by_tg_id(user_id)
    usage_info = None
    if user is not None:
        usage_info = await resolve_subscription_usage_info(
            user,
            sub,
            session,
            usage_timeout_seconds=3.0,
        )

    details_text, _, _ = await format_subscription_for_user(
        sub,
        show_type=True,
        usage_info=usage_info,
    )
    return (
        "📦 <b>У вас уже есть активная подписка</b>\n\n"
        f"{details_text}\n\n"
        "Ниже вы можете сбросить трафик или вернуться назад."
    )


async def _render_configurator(
    target: Message,
    state: FSMContext,
    *,
    with_banner: bool,
) -> None:
    text, keyboard = await _build_configurator_view(state)
    _ = with_banner
    screen = await _edit_only(target, text, reply_markup=keyboard)
    await _store_subscription_screen_refs(state, screen or target)


async def _render_configurator_selection(
    target: Message,
    state: FSMContext,
    *,
    traffic_gb: int | None,
    term_months: int | None,
    device_limit: int | None,
    with_banner: bool,
) -> None:
    await state.update_data(
        sub_traffic_gb=traffic_gb,
        sub_term_months=term_months,
        sub_device_limit=device_limit,
        plan_type=DEFAULT_PLAN_TYPE,
        plan_slug=DEFAULT_PLAN_SLUG,
        build_preset=DEFAULT_BUILD_PRESET,
    )
    data = await state.get_data()
    promo_code = data.get("sub_promo_code")
    promo_final = data.get("sub_promo_final_price")
    promo_final_price = Decimal(str(promo_final)) if promo_final else None

    text = _build_configurator_text(
        traffic_gb,
        term_months,
        device_limit,
        promo_code=str(promo_code) if promo_code else None,
        final_price=promo_final_price,
    )
    keyboard = subscription_configurator_keyboard(
        traffic_gb,
        term_months,
        device_limit,
        has_promo=bool(promo_code),
    )
    _ = with_banner
    screen = await _edit_only(target, text, reply_markup=keyboard)
    await _store_subscription_screen_refs(state, screen or target)


async def _render_configurator_by_ids(
    bot,
    state: FSMContext,
    *,
    chat_id: int,
    message_id: int,
) -> None:
    text, keyboard = await _build_configurator_view(state)
    refs = await _edit_only_by_ids(bot, chat_id, message_id, text, reply_markup=keyboard)
    await _store_subscription_screen_refs_by_ids(state, refs)


@router.callback_query(F.data == "menu:subscription")
async def subscription_menu(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    async with _get_subscription_lock(call.from_user.id):
        await state.clear()
        await _reset_constructor_state(state)
        sub = await SubscriptionRepository(session).get_active(call.from_user.id)
        if sub:
            user = await UserRepository(session).get_by_tg_id(call.from_user.id)
            if user:
                invalidate_usage_cache(user, sub)
            await _edit_only(
                call.message,
                await _build_active_subscription_text(
                    sub,
                    session=session,
                    user_id=call.from_user.id,
                    subscription_uuid=user.subscription_uuid if user else None,
                ),
                reply_markup=subscription_activated_keyboard(show_reset_traffic=True),
            )
            await call.answer()
            return
        await _render_configurator_selection(
            call.message,
            state,
            traffic_gb=None,
            term_months=None,
            device_limit=None,
            with_banner=True,
        )
    await call.answer()


@router.callback_query(F.data == "menu:subscription:configure")
async def subscription_configure_menu(call: CallbackQuery, state: FSMContext) -> None:
    async with _get_subscription_lock(call.from_user.id):
        await state.clear()
        await _reset_constructor_state(state)
        await _render_configurator_selection(
            call.message,
            state,
            traffic_gb=None,
            term_months=None,
            device_limit=None,
            with_banner=True,
        )
    await call.answer()


@router.callback_query(F.data == "sub_reset_traffic")
async def subscription_reset_traffic(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    async with _get_subscription_lock(call.from_user.id):
        try:
            await call.answer("Выполняю сброс трафика...")
        except TelegramBadRequest:
            pass
        await state.clear()
        await _reset_constructor_state(state)
        sub_repo = SubscriptionRepository(session)
        user_repo = UserRepository(session)

        active_sub = await sub_repo.get_active(call.from_user.id)
        if not active_sub:
            try:
                await call.answer("Активной подписки нет.", show_alert=True)
            except TelegramBadRequest:
                pass
            await _render_configurator_selection(
                call.message,
                state,
                traffic_gb=None,
                term_months=None,
                device_limit=None,
                with_banner=False,
            )
            return

        user = await user_repo.get_by_tg_id_for_update(call.from_user.id)
        if not user:
            try:
                await call.answer("Пользователь не найден.", show_alert=True)
            except TelegramBadRequest:
                pass
            return
        if user.balance < TRAFFIC_RESET_PRICE:
            await _edit_only(
                call.message,
                "Недостаточно средств для сброса трафика.\n"
                f"Баланс: <b>{user.balance} ₽</b>\n"
                f"Стоимость сброса: <b>{TRAFFIC_RESET_PRICE} ₽</b>",
                reply_markup=insufficient_balance_keyboard(),
            )
            try:
                await call.answer("Недостаточно средств", show_alert=True)
            except TelegramBadRequest:
                pass
            return

        provider = build_vpn_provider()
        try:
            ok = await provider.reset_user_traffic(
                user_id=user.id,
                subscription_uuid=user.subscription_uuid,
                provider_subscription_id=active_sub.provider_subscription_id,
            )
        except Exception:
            logger.exception("Traffic reset call failed for user_id=%s", user.id)
            ok = False

        if not ok:
            await session.rollback()
            await _edit_only(
                call.message,
                "❌ Не удалось выполнить сброс трафика в панели.\n"
                "Попробуйте позже или обратитесь в поддержку.",
                reply_markup=subscription_activated_keyboard(show_reset_traffic=True),
            )
            try:
                await call.answer("Сброс не выполнен", show_alert=True)
            except TelegramBadRequest:
                pass
            return

        user.balance -= TRAFFIC_RESET_PRICE
        session.add(
            BalanceLedger(
                user_id=user.id,
                amount=-TRAFFIC_RESET_PRICE,
                reason="subscription_traffic_reset",
            )
        )
        await session.commit()
        invalidate_usage_cache(user, active_sub)

        await _edit_only(
            call.message,
            "✅ Трафик успешно сброшен.\n"
            f"Списано: <b>{TRAFFIC_RESET_PRICE} ₽</b>\n"
            f"Текущий баланс: <b>{user.balance} ₽</b>",
            reply_markup=subscription_activated_keyboard(show_reset_traffic=True),
        )
        try:
            await call.answer("Трафик сброшен")
        except TelegramBadRequest:
            pass


@router.callback_query(F.data.startswith("sub_gb_"))
async def subscription_pick_gb(call: CallbackQuery, state: FSMContext) -> None:
    try:
        traffic_gb = int(call.data.split("_")[-1])
    except (TypeError, ValueError):
        await call.answer("Некорректный объем.", show_alert=True)
        return

    if traffic_gb not in TRAFFIC_OPTIONS:
        await call.answer("Некорректный объем.", show_alert=True)
        return

    async with _get_subscription_lock(call.from_user.id):
        await _set_constructor_selection(
            state,
            traffic_gb=traffic_gb,
        )
        await _render_configurator(
            call.message,
            state,
            with_banner=False,
        )
    await call.answer()


@router.callback_query(F.data.startswith("sub_cfg:"))
async def subscription_pick_config(call: CallbackQuery, state: FSMContext) -> None:
    parsed = _parse_constructor_callback(call.data, "sub_cfg")
    if parsed is None:
        await call.answer("Некорректный выбор.", show_alert=True)
        return

    traffic_gb, term_months, device_limit = parsed

    async with _get_subscription_lock(call.from_user.id):
        await _set_constructor_selection(
            state,
            traffic_gb=traffic_gb,
            term_months=term_months,
            device_limit=device_limit,
        )
        await _render_configurator_selection(
            call.message,
            state,
            traffic_gb=traffic_gb,
            term_months=term_months,
            device_limit=device_limit,
            with_banner=False,
        )
    await call.answer()


@router.callback_query(F.data.startswith("sub_term_"))
async def subscription_pick_term(call: CallbackQuery, state: FSMContext) -> None:
    try:
        term_months = int(call.data.split("_")[-1])
    except (TypeError, ValueError):
        await call.answer("Некорректный срок.", show_alert=True)
        return

    if term_months not in DURATION_MONTH_OPTIONS:
        await call.answer("Некорректный срок.", show_alert=True)
        return

    async with _get_subscription_lock(call.from_user.id):
        await _set_constructor_selection(
            state,
            term_months=term_months,
        )
        await _render_configurator(
            call.message,
            state,
            with_banner=False,
        )
    await call.answer()


@router.callback_query(F.data.startswith("sub_devices_"))
async def subscription_pick_devices(call: CallbackQuery, state: FSMContext) -> None:
    try:
        device_limit = int(call.data.split("_")[-1])
    except (TypeError, ValueError):
        await call.answer("Некорректный лимит устройств.", show_alert=True)
        return

    if device_limit not in DEVICE_LIMIT_OPTIONS:
        await call.answer("Некорректный лимит устройств.", show_alert=True)
        return

    async with _get_subscription_lock(call.from_user.id):
        await _set_constructor_selection(
            state,
            device_limit=device_limit,
        )
        await _render_configurator(
            call.message,
            state,
            with_banner=False,
        )
    await call.answer()


@router.callback_query(F.data.startswith("sub_promo_enter"))
async def subscription_promo_enter(call: CallbackQuery, state: FSMContext) -> None:
    async with _get_subscription_lock(call.from_user.id):
        parsed = _parse_constructor_callback(call.data, "sub_promo_enter")
        if parsed is None:
            parsed = _get_constructor_selection(await state.get_data())
        traffic_gb, term_months, device_limit = parsed
        missing_text = _build_missing_constructor_text(traffic_gb, term_months, device_limit)
        if missing_text:
            await call.answer(f"Сначала завершите выбор. {missing_text}", show_alert=True)
            return
        await state.update_data(
            sub_traffic_gb=traffic_gb,
            sub_term_months=term_months,
            sub_device_limit=device_limit,
        )
        await state.set_state(SubscriptionStates.waiting_promo_code)
        screen = await _edit_only(call.message, "Введите промокод для подписки:")
        await _store_subscription_screen_refs(state, screen or call.message)
    await call.answer()


@router.callback_query(F.data.startswith("sub_promo_clear"))
async def subscription_promo_clear(call: CallbackQuery, state: FSMContext) -> None:
    async with _get_subscription_lock(call.from_user.id):
        parsed = _parse_constructor_callback(call.data, "sub_promo_clear")
        if parsed is None:
            parsed = _get_constructor_selection(await state.get_data())
        traffic_gb, term_months, device_limit = parsed
        await state.update_data(sub_promo_code="", sub_promo_id=None, sub_promo_final_price="")
        await _render_configurator_selection(
            call.message,
            state,
            traffic_gb=traffic_gb,
            term_months=term_months,
            device_limit=device_limit,
            with_banner=False,
        )
    await call.answer("Промокод удален")


@router.message(SubscriptionStates.waiting_promo_code)
async def subscription_promo_apply(message: Message, state: FSMContext, session: AsyncSession) -> None:
    async with _get_subscription_lock(message.from_user.id):
        code = (message.text or "").strip().upper()
        data = await state.get_data()
        traffic_gb, term_months, device_limit = _get_constructor_selection(data)
        if traffic_gb is None or term_months is None or device_limit is None:
            await state.clear()
            await message.answer("Сессия истекла. Откройте подписку заново.")
            return
        base_price = _calculate_price(traffic_gb, term_months, device_limit)
        if base_price is None:
            await state.set_state(None)
            await message.answer("Не удалось рассчитать цену для этой конфигурации.")
            return

        service = PromoService(PromoRepository(session), UserRepository(session))
        try:
            final_price, promo = await service.validate_and_apply(
                code=code,
                user_id=message.from_user.id,
                base_amount=base_price,
                target=PromoTarget.subscription,
            )
        except ValueError as exc:
            chat_id = int(data.get("sub_chat_id", 0))
            message_id = int(data.get("sub_message_id", 0))
            error_text = (
                f"❌ {_safe_error_text(exc)}\n\n"
                "Введите другой промокод или вернитесь назад к настройке подписки."
            )
            if chat_id and message_id:
                refs = await _edit_only_by_ids(message.bot, chat_id, message_id, error_text)
                await _store_subscription_screen_refs_by_ids(state, refs)
            else:
                sent = await message.answer(error_text)
                await _store_subscription_screen_refs(state, sent)
            return

        await state.set_state(None)
        await state.update_data(
            sub_promo_code=promo.code if promo else "",
            sub_promo_id=promo.id if promo else None,
            sub_promo_final_price=str(final_price),
        )
        chat_id = int(data.get("sub_chat_id", 0))
        message_id = int(data.get("sub_message_id", 0))
        if chat_id and message_id:
            await _render_configurator_by_ids(
                message.bot,
                state,
                chat_id=chat_id,
                message_id=message_id,
            )
        else:
            text, keyboard = await _build_configurator_view(state)
            sent = await message.answer(text, reply_markup=keyboard)
            await _store_subscription_screen_refs(state, sent)


@router.callback_query(F.data.startswith("sub_pay"))
async def subscription_pay(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    async with _get_subscription_lock(call.from_user.id):
        data = await state.get_data()
        parsed = _parse_constructor_callback(call.data, "sub_pay")
        if parsed is None:
            parsed = _get_constructor_selection(data)
        traffic_gb, term_months, device_limit = parsed
        await state.update_data(
            sub_traffic_gb=traffic_gb,
            sub_term_months=term_months,
            sub_device_limit=device_limit,
        )

        missing_text = _build_missing_constructor_text(traffic_gb, term_months, device_limit)
        if missing_text:
            await call.answer(f"Не хватает параметров. {missing_text}", show_alert=True)
            return

        base_price = _calculate_price(traffic_gb, term_months, device_limit)
        if base_price is None:
            await call.answer("Цена для этой конфигурации не найдена.", show_alert=True)
            return

        price = base_price
        promo_id = data.get("sub_promo_id")
        promo_code = data.get("sub_promo_code")
        if promo_code:
            promo_service = PromoService(PromoRepository(session), UserRepository(session))
            try:
                price, promo = await promo_service.validate_and_apply(
                    code=str(promo_code),
                    user_id=call.from_user.id,
                    base_amount=base_price,
                    target=PromoTarget.subscription,
                )
                promo_id = promo.id if promo else None
            except ValueError:
                await state.update_data(sub_promo_code="", sub_promo_id=None, sub_promo_final_price="")
                await _render_configurator_selection(
                    call.message,
                    state,
                    traffic_gb=traffic_gb,
                    term_months=term_months,
                    device_limit=device_limit,
                    with_banner=False,
                )
                await call.answer("Промокод сброшен", show_alert=True)
                return

        duration_days = DURATION_MONTH_TO_DAYS[term_months]

        try:
            await call.answer("Обрабатываю оплату...")
        except TelegramBadRequest:
            pass

        try:
            await _finish_purchase(
                call.message,
                call.from_user.id,
                state,
                session,
                traffic_gb=traffic_gb,
                duration_days=duration_days,
                device_limit=device_limit,
                final_price=price,
                promo_id=int(promo_id) if promo_id else None,
            )
        except ValueError as exc:
            await _edit_only(
                call.message,
                f"❌ {_safe_error_text(exc)}\n\nПроверьте настройки API в .env и попробуйте снова.",
                reply_markup=subscription_configurator_keyboard(traffic_gb, term_months, device_limit, has_promo=bool(promo_code)),
            )
        except Exception:
            logger.exception("Unexpected error while finishing subscription purchase")
            await _edit_only(
                call.message,
                "❌ Ошибка при оформлении подписки. Попробуйте еще раз через минуту.",
                reply_markup=subscription_configurator_keyboard(traffic_gb, term_months, device_limit, has_promo=bool(promo_code)),
            )


async def _finish_purchase(
    message: Message,
    user_id: int,
    state: FSMContext,
    session: AsyncSession,
    *,
    traffic_gb: int,
    duration_days: int,
    device_limit: int,
    final_price: Decimal,
    promo_id: int | None = None,
) -> None:
    await _edit_only(message, "⏳ Оформляю подписку, подождите...")

    plan_id = await _resolve_plan_id_by_slug(session, DEFAULT_PLAN_SLUG)
    if not plan_id:
        raise ValueError("Тариф не найден. Проверьте планы в БД.")

    user = await UserRepository(session).get_by_tg_id(user_id)
    if not user or user.balance < final_price:
        await _edit_only(
            message,
            f"Недостаточно средств.\nБаланс: <b>{user.balance if user else 0} ₽</b>\n"
            f"Нужно: <b>{final_price} ₽</b>",
            reply_markup=insufficient_balance_keyboard(),
        )
        return

    subscription_service = _build_service(session)
    sub = await subscription_service.purchase_with_balance(
        user_id=user_id,
        plan_id=plan_id,
        final_price=final_price,
        period_days=duration_days,
        plan_type=DEFAULT_PLAN_TYPE,
        traffic_gb=traffic_gb,
        device_limit=device_limit,
        build_preset=DEFAULT_BUILD_PRESET,
    )
    if promo_id:
        try:
            promo_service = PromoService(PromoRepository(session), UserRepository(session))
            await promo_service.mark_redeemed(promo_id=promo_id, user_id=user_id)
        except Exception:
            logger.exception("Failed to mark promo redemption promo_id=%s user_id=%s", promo_id, user_id)

    user_keys = await VpnKeyRepository(session).get_user_keys(user_id, limit=1)
    key_text = ""
    connect_url = None
    if user_keys:
        key_value = user_keys[0].key
        if key_value.startswith(("http://", "https://")):
            safe_url = key_value.replace("<", "").replace(">", "")
            connect_url = key_value
            key_text = f"\n\n🔌 <b>Ссылка подписки:</b>\n{safe_url}"
        elif len(key_value) <= 800 and "\n" not in key_value:
            key_text = "\n\n🔌 Подписка готова."
        else:
            key_text = "\n\n🔌 Подписка готова."

    await _edit_only(
        message,
        "✅ Подписка активирована.\n"
        f"Действует до: <b>{sub.expires_at.strftime('%d.%m.%Y')}</b>"
        f"{key_text}",
            reply_markup=subscription_activated_keyboard(show_reset_traffic=True),
    )
    await state.clear()

    # Offer a review only on renewals (not the very first activation)
    existing_count = await SubscriptionRepository(session).count_user_subscriptions(user_id)
    if existing_count > 1:
        from aiogram.utils.keyboard import InlineKeyboardBuilder as _IKB
        review_kb = _IKB()
        review_kb.button(text="⭐ Оставить отзыв", callback_data="menu:review")
        review_kb.button(text="Позже", callback_data="menu:main")
        review_kb.adjust(2)
        await message.answer(
            "Вы продлили подписку — как вам сервис?\n"
            "Оставьте отзыв, это займёт 30 секунд 🙏",
            reply_markup=review_kb.as_markup(),
        )
