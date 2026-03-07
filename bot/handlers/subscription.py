from decimal import Decimal
import html
import logging
import re

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.constants.subscription_pricing import (
    DURATION_MONTH_OPTIONS,
    DURATION_MONTH_TO_DAYS,
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
from bot.repositories.subscription import SubscriptionRepository
from bot.repositories.user import UserRepository
from bot.repositories.vpn_key import VpnKeyRepository
from bot.services.subscription import SubscriptionService
from bot.utils.messages import _short_text

router = Router()
logger = logging.getLogger(__name__)

DEFAULT_PLAN_TYPE = "pc"
DEFAULT_PLAN_SLUG = "vpn"
DEFAULT_BUILD_PRESET = "max"


async def _edit_only(message: Message, text: str, reply_markup=None) -> None:
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
        return

    # media messages (photo/video with caption)
    if await _try_edit_caption(rendered):
        return

    # entity-safe retry in plain text/caption
    safe_plain = html.escape(re.sub(r"<[^>]+>", "", text))
    safe_rendered = _short_text(safe_plain)

    if await _try_edit_text(safe_rendered):
        return
    if await _try_edit_caption(safe_rendered):
        return

    # In this flow, do not send extra fallback messages.
    return


def _safe_error_text(exc: Exception) -> str:
    text = str(exc)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > 320:
        text = text[:320] + "..."
    return html.escape(text)


def _calculate_price(traffic_gb: int | None, term_months: int | None) -> Decimal | None:
    if traffic_gb is None or term_months is None:
        return None
    duration_days = DURATION_MONTH_TO_DAYS.get(term_months)
    if duration_days is None:
        return None
    price = SUBSCRIPTION_PRICE_MATRIX.get((duration_days, traffic_gb))
    if price is None:
        return None
    return price.quantize(Decimal("0.01"))


def _build_configurator_text(traffic_gb: int | None, term_months: int | None) -> str:
    volume_title = f"{traffic_gb} ГБ" if traffic_gb is not None else "не выбран"
    term_title = f"{term_months} мес" if term_months is not None else "не выбран"
    price = _calculate_price(traffic_gb, term_months)
    price_title = f"{price} ₽" if price is not None else "будет рассчитана после выбора параметров"

    return (
        "📦 <b>Настройте подписку самостоятельно</b>\n\n"
        "<b>Выбранная конфигурация:</b>\n"
        f"• Объем: <b>{volume_title}</b>\n"
        f"• Срок: <b>{term_title}</b>\n"
        f"• Цена: <b>{price_title}</b>\n\n"
        "Выберите нужные параметры ниже."
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


async def _render_configurator(
    target: Message,
    state: FSMContext,
    *,
    traffic_gb: int | None,
    term_months: int | None,
    with_banner: bool,
) -> None:
    await state.update_data(
        sub_traffic_gb=traffic_gb,
        sub_term_months=term_months,
        plan_type=DEFAULT_PLAN_TYPE,
        plan_slug=DEFAULT_PLAN_SLUG,
        build_preset=DEFAULT_BUILD_PRESET,
    )

    text = _build_configurator_text(traffic_gb, term_months)
    keyboard = subscription_configurator_keyboard(traffic_gb, term_months)
    _ = with_banner
    await _edit_only(target, text, reply_markup=keyboard)


@router.callback_query(F.data == "menu:subscription")
async def subscription_menu(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _render_configurator(
        call.message,
        state,
        traffic_gb=None,
        term_months=None,
        with_banner=True,
    )
    await call.answer()


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

    data = await state.get_data()
    term_months = data.get("sub_term_months")
    await _render_configurator(
        call.message,
        state,
        traffic_gb=traffic_gb,
        term_months=term_months,
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

    data = await state.get_data()
    traffic_gb = data.get("sub_traffic_gb")
    await _render_configurator(
        call.message,
        state,
        traffic_gb=traffic_gb,
        term_months=term_months,
        with_banner=False,
    )
    await call.answer()


@router.callback_query(F.data == "sub_pay")
async def subscription_pay(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    traffic_gb = data.get("sub_traffic_gb")
    term_months = data.get("sub_term_months")

    if traffic_gb is None or term_months is None:
        await call.answer("Сначала выберите объем и срок подписки.", show_alert=True)
        return

    price = _calculate_price(traffic_gb, term_months)
    if price is None:
        await call.answer("Цена для этой конфигурации не найдена.", show_alert=True)
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
            final_price=price,
        )
    except ValueError as exc:
        await _edit_only(
            call.message,
            f"❌ {_safe_error_text(exc)}\n\nПроверьте настройки API в .env и попробуйте снова.",
            reply_markup=subscription_configurator_keyboard(traffic_gb, term_months),
        )
    except Exception:
        logger.exception("Unexpected error while finishing subscription purchase")
        await _edit_only(
            call.message,
            "❌ Ошибка при оформлении подписки. Попробуйте еще раз через минуту.",
            reply_markup=subscription_configurator_keyboard(traffic_gb, term_months),
        )


async def _finish_purchase(
    message: Message,
    user_id: int,
    state: FSMContext,
    session: AsyncSession,
    *,
    traffic_gb: int,
    duration_days: int,
    final_price: Decimal,
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
        build_preset=DEFAULT_BUILD_PRESET,
    )

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

    await _edit_only(
        message,
        "✅ Подписка активирована.\n"
        f"Действует до: <b>{sub.expires_at.strftime('%d.%m.%Y')}</b>"
        f"{key_text}\n\n"
        "Кнопка «Подключиться» откроет инструкцию и подключение.",
        reply_markup=subscription_activated_keyboard(),
    )
    await state.clear()
