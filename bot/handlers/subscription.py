from datetime import datetime, timezone
from decimal import Decimal
import html
import logging
import re

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.constants.subscription_pricing import DURATION_OPTIONS, TRAFFIC_OPTIONS, SUBSCRIPTION_PRICE_MATRIX
from bot.keyboards.subscription import (
    duration_keyboard,
    insufficient_balance_keyboard,
    subscription_activated_keyboard,
    subscription_active_keyboard,
    subscription_confirm_keyboard,
    traffic_keyboard,
)
from bot.providers.vpn.factory import build_vpn_provider
from bot.repositories.ledger import BalanceLedgerRepository
from bot.repositories.plan import PlanRepository
from bot.repositories.subscription import SubscriptionRepository
from bot.repositories.user import UserRepository
from bot.repositories.vpn_key import VpnKeyRepository
from bot.services.subscription import SubscriptionService
from bot.states.subscription import SubscriptionStates
from bot.utils.messages import edit_or_send

router = Router()
logger = logging.getLogger(__name__)

# Constructor keeps one real plan underneath and controls options via traffic/duration.
DEFAULT_PLAN_TYPE = "pc"
DEFAULT_PLAN_SLUG = "vpn"
DEFAULT_BUILD_PRESET = "max"


def _is_effectively_active(sub) -> bool:
    if not sub or not sub.is_active or not sub.expires_at:
        return False
    return sub.expires_at > datetime.now(timezone.utc)


def _safe_error_text(exc: Exception) -> str:
    text = str(exc)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > 320:
        text = text[:320] + "..."
    return html.escape(text)


def _build_summary_text(duration_days: int, traffic_gb: int, final_price: Decimal) -> str:
    return (
        "📦 <b>Подтверждение подписки</b>\n"
        f"Срок: <b>{duration_days} дней</b>\n"
        f"Трафик: <b>{traffic_gb} ГБ</b>\n"
        f"Стоимость: <b>{final_price} ₽</b>\n\n"
        "Нажмите «Оплатить с баланса» для оформления."
    )


def _format_active_subscription(sub) -> str:
    now = datetime.now(timezone.utc)
    expires = sub.expires_at
    is_active = sub.is_active and expires is not None and expires > now
    status = "✅ активна" if is_active else "❌ истекла"
    expires_str = expires.strftime("%d.%m.%Y") if expires else "—"
    duration_title = f"{sub.duration_days} дней" if sub.duration_days else "—"
    traffic_title = f"{sub.traffic_gb} ГБ" if sub.traffic_gb else "—"
    return (
        "📦 <b>Ваша подписка</b>\n\n"
        f"Срок: <b>{duration_title}</b>\n"
        f"Трафик: <b>{traffic_title}</b>\n"
        f"Статус: {status}\n"
        f"Действует до: <b>{expires_str}</b>"
    )


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


@router.callback_query(F.data == "menu:subscription")
async def subscription_menu(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    sub = await SubscriptionRepository(session).get_active(call.from_user.id)
    if _is_effectively_active(sub):
        text = f"{_format_active_subscription(sub)}\n\nМожно оформить новую подписку."
        await edit_or_send(call.message, text, reply_markup=subscription_active_keyboard())
    else:
        await state.clear()
        await state.set_state(SubscriptionStates.waiting_duration)
        await edit_or_send(
            call.message,
            "📦 <b>Новая подписка</b>\n\nШаг 1/3: выберите срок подписки:",
            reply_markup=duration_keyboard(),
        )
    await call.answer()


@router.callback_query(F.data == "subscription:change")
async def subscription_change(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.update_data(
        plan_type=DEFAULT_PLAN_TYPE,
        plan_slug=DEFAULT_PLAN_SLUG,
        build_preset=DEFAULT_BUILD_PRESET,
    )
    await state.set_state(SubscriptionStates.waiting_duration)
    await edit_or_send(call.message, "Шаг 1/3: выберите срок подписки:", reply_markup=duration_keyboard())
    await call.answer()


@router.callback_query(F.data.startswith("subscription:duration:"), SubscriptionStates.waiting_duration)
async def subscription_pick_duration(call: CallbackQuery, state: FSMContext) -> None:
    duration_days = int(call.data.split(":")[-1])
    if duration_days not in DURATION_OPTIONS:
        await call.answer("Неверный срок подписки.", show_alert=True)
        return
    await state.update_data(
        duration_days=duration_days,
        plan_type=DEFAULT_PLAN_TYPE,
        plan_slug=DEFAULT_PLAN_SLUG,
        build_preset=DEFAULT_BUILD_PRESET,
    )
    await state.set_state(SubscriptionStates.waiting_traffic)
    await edit_or_send(call.message, "Шаг 2/3: выберите лимит трафика:", reply_markup=traffic_keyboard())
    await call.answer()


@router.callback_query(F.data == "subscription:duration:back")
async def subscription_duration_back(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SubscriptionStates.waiting_duration)
    await edit_or_send(call.message, "Шаг 1/3: выберите срок подписки:", reply_markup=duration_keyboard())
    await call.answer()


@router.callback_query(F.data == "subscription:traffic:back")
async def subscription_traffic_back(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SubscriptionStates.waiting_traffic)
    await edit_or_send(call.message, "Шаг 2/3: выберите лимит трафика:", reply_markup=traffic_keyboard())
    await call.answer()


@router.callback_query(F.data.startswith("subscription:traffic:"), SubscriptionStates.waiting_traffic)
async def subscription_pick_traffic(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    traffic_gb = int(call.data.split(":")[-1])
    if traffic_gb not in TRAFFIC_OPTIONS:
        await call.answer("Неверный трафик", show_alert=True)
        return

    data = await state.get_data()
    duration_days = int(data.get("duration_days", 0))
    plan_slug = str(data.get("plan_slug", DEFAULT_PLAN_SLUG))
    if not duration_days:
        await state.clear()
        await call.answer("Сессия устарела. Начните заново.", show_alert=True)
        return

    plan_id = await _resolve_plan_id_by_slug(session, plan_slug)
    if not plan_id:
        await state.clear()
        await call.answer("Тариф не найден. Проверьте планы в БД.", show_alert=True)
        return

    try:
        price = SUBSCRIPTION_PRICE_MATRIX.get((duration_days, traffic_gb))
        if price is None:
            raise ValueError("Неверные параметры тарифа.")
        final_price = price.quantize(Decimal("0.01"))
    except ValueError as exc:
        await state.clear()
        await call.answer(str(exc), show_alert=True)
        return

    await state.update_data(
        plan_id=plan_id,
        traffic_gb=traffic_gb,
        final_price=str(final_price),
        plan_type=DEFAULT_PLAN_TYPE,
        build_preset=DEFAULT_BUILD_PRESET,
    )
    await state.set_state(SubscriptionStates.waiting_confirm)
    await edit_or_send(
        call.message,
        _build_summary_text(duration_days, traffic_gb, final_price),
        reply_markup=subscription_confirm_keyboard(),
    )
    await call.answer()


@router.callback_query(F.data == "subscription:pay_balance", SubscriptionStates.waiting_confirm)
async def subscription_pay_balance(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    try:
        await call.answer("Обрабатываю оплату...")
    except TelegramBadRequest:
        # Callback query could be expired if processing/update took too long before handler start.
        pass
    try:
        await _finish_purchase(call.message, call.from_user.id, state, session)
    except ValueError as exc:
        await edit_or_send(
            call.message,
            f"❌ {_safe_error_text(exc)}\n\nПроверьте настройки API в .env и попробуйте снова.",
            reply_markup=subscription_confirm_keyboard(),
        )
        await state.set_state(SubscriptionStates.waiting_confirm)
    except Exception:
        logger.exception("Unexpected error while finishing subscription purchase")
        await edit_or_send(
            call.message,
            "❌ Ошибка при оформлении подписки.\nПопробуйте еще раз через минуту.",
            reply_markup=subscription_confirm_keyboard(),
        )
        await state.set_state(SubscriptionStates.waiting_confirm)


# Backward compatibility for old messages/callbacks.
@router.callback_query(F.data.in_({"subscription:type:back", "subscription:type:phone", "subscription:type:pc"}))
async def subscription_legacy_type(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SubscriptionStates.waiting_duration)
    await edit_or_send(call.message, "Шаг 1/3: выберите срок подписки:", reply_markup=duration_keyboard())
    await call.answer()


@router.callback_query(F.data.in_({"subscription:build:back", "subscription:devices:back"}))
async def subscription_legacy_back_to_traffic(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SubscriptionStates.waiting_traffic)
    await edit_or_send(call.message, "Шаг 2/3: выберите лимит трафика:", reply_markup=traffic_keyboard())
    await call.answer()


@router.callback_query(F.data.in_({"subscription:build:min", "subscription:build:mid", "subscription:build:max"}))
async def subscription_legacy_build_choice(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SubscriptionStates.waiting_traffic)
    await edit_or_send(call.message, "Шаг 2/3: выберите лимит трафика:", reply_markup=traffic_keyboard())
    await call.answer()


async def _finish_purchase(message: Message, user_id: int, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    if "plan_id" not in data or "final_price" not in data:
        await state.clear()
        await edit_or_send(
            message,
            "Сессия оплаты устарела. Выберите подписку заново.",
            reply_markup=duration_keyboard(),
        )
        await state.set_state(SubscriptionStates.waiting_duration)
        return

    plan_id = int(data["plan_id"])
    final_price = Decimal(data["final_price"])
    duration_days = int(data.get("duration_days", 30))
    plan_type = str(data.get("plan_type", DEFAULT_PLAN_TYPE))
    build_preset = str(data.get("build_preset", DEFAULT_BUILD_PRESET))
    traffic_gb = int(data.get("traffic_gb", 0))

    await edit_or_send(message, "⏳ Оформляю подписку, подождите...")

    user = await UserRepository(session).get_by_tg_id(user_id)
    if not user or user.balance < final_price:
        await edit_or_send(
            message,
            f"Недостаточно средств.\nБаланс: <b>{user.balance if user else 0} ₽</b>\n"
            f"Нужно: <b>{final_price} ₽</b>",
            reply_markup=insufficient_balance_keyboard(),
        )
        await state.clear()
        return

    try:
        subscription_service = _build_service(session)
    except ValueError as exc:
        await state.set_state(SubscriptionStates.waiting_confirm)
        await edit_or_send(
            message,
            f"❌ {_safe_error_text(exc)}\n\nПроверьте настройки API в .env.",
            reply_markup=subscription_confirm_keyboard(),
        )
        return

    try:
        sub = await subscription_service.purchase_with_balance(
            user_id=user_id,
            plan_id=plan_id,
            final_price=final_price,
            period_days=duration_days,
            plan_type=plan_type,
            traffic_gb=traffic_gb,
            build_preset=build_preset,
        )
    except ValueError as exc:
        await state.set_state(SubscriptionStates.waiting_confirm)
        await edit_or_send(
            message,
            f"❌ {_safe_error_text(exc)}\n\nПопробуйте еще раз позже.",
            reply_markup=subscription_confirm_keyboard(),
        )
        return

    user_keys = await VpnKeyRepository(session).get_user_keys(user_id, limit=1)
    key_text = ""
    if user_keys:
        key_value = user_keys[0].key
        if key_value.startswith(("http://", "https://")):
            safe_url = key_value.replace("<", "").replace(">", "")
            key_text = f"\n\n🔌 <b>Ссылка подписки:</b>\n{safe_url}"
        elif len(key_value) <= 800 and "\n" not in key_value:
            key_text = "\n\n🔌 Подписка готова. Откройте раздел «Подключиться», чтобы получить конфиг."
        else:
            key_text = "\n\n🔌 Подписка готова. Откройте раздел «Подключиться», чтобы получить конфиг."

    await edit_or_send(
        message,
        "✅ Подписка активирована.\n"
        f"Действует до: <b>{sub.expires_at.strftime('%d.%m.%Y')}</b>"
        f"{key_text}\n\n"
        "Кнопка «Подключиться» откроет инструкцию и подключение.",
        reply_markup=subscription_activated_keyboard(),
    )
    await state.clear()
