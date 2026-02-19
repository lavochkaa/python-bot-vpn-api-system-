from decimal import Decimal, ROUND_HALF_UP

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.keyboards.subscription import (
    devices_keyboard,
    duration_keyboard,
    insufficient_balance_keyboard,
    plan_type_keyboard,
    subscription_menu_keyboard,
    subscription_promo_keyboard,
    traffic_keyboard,
)
from bot.providers.vpn.stub import StubVpnKeyProvider
from bot.repositories.ledger import BalanceLedgerRepository
from bot.repositories.plan import PlanRepository
from bot.repositories.promo import PromoRepository
from bot.repositories.subscription import SubscriptionRepository
from bot.repositories.user import UserRepository
from bot.repositories.vpn_key import VpnKeyRepository
from bot.services.promo import PromoService
from bot.services.subscription import SubscriptionService
from bot.states.subscription import SubscriptionStates
from bot.utils.formatters import format_subscription_info
from bot.utils.messages import edit_or_send, send_or_answer

router = Router()

# Base pricing model for constructor options.
# TODO: move multipliers to DB/admin settings when tariff constructor matures.
DURATION_MULTIPLIERS = {
    30: Decimal("1.00"),
    180: Decimal("5.50"),
    365: Decimal("10.00"),
}
TRAFFIC_MULTIPLIERS = {
    "100": Decimal("1.00"),
    "300": Decimal("1.70"),
    "unlimited": Decimal("2.50"),
}
DEVICES_MULTIPLIERS = {
    "2": Decimal("1.00"),
    "4": Decimal("1.40"),
    "10": Decimal("2.30"),
    "unlimited": Decimal("3.50"),
}
TRAFFIC_LABELS = {"100": "100 ГБ", "300": "300 ГБ", "unlimited": "Безлимит"}
DEVICES_LABELS = {"2": "2", "4": "4", "10": "10", "unlimited": "Неограниченно"}
PLAN_LABELS = {"vpn": "VPN обход", "vpn_bypass": "VPN + обход"}


def _calc_final_price(base: Decimal, duration_days: int, traffic: str, devices: str) -> Decimal:
    amount = (
        base
        * DURATION_MULTIPLIERS[duration_days]
        * TRAFFIC_MULTIPLIERS[traffic]
        * DEVICES_MULTIPLIERS[devices]
    )
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


async def _resolve_plan_id_by_slug(session: AsyncSession, slug: str) -> int | None:
    plans = await PlanRepository(session).get_active_plans()
    for plan in plans:
        if plan.slug == slug:
            return plan.id
    return None


def _build_summary_text(data: dict, final_price: Decimal) -> str:
    return (
        "📦 <b>Параметры подписки</b>\n"
        f"Срок: <b>{data['duration_days']} дней</b>\n"
        f"Тип: <b>{PLAN_LABELS.get(data['plan_slug'], data['plan_slug'])}</b>\n"
        f"Трафик: <b>{TRAFFIC_LABELS.get(data['traffic'], data['traffic'])}</b>\n"
        f"Устройства: <b>{DEVICES_LABELS.get(data['devices'], data['devices'])}</b>\n\n"
        f"Итоговая стоимость: <b>{final_price} ₽</b>\n\n"
        "Применить промокод?"
    )


@router.callback_query(F.data == "menu:subscription")
async def subscription_menu(call: CallbackQuery, session: AsyncSession) -> None:
    sub = await SubscriptionRepository(session).get_active(call.from_user.id)
    sub_text = await format_subscription_info(sub)
    await edit_or_send(
        call.message,
        f"{sub_text}\n\nВы можете приобрести новую подписку.",
        reply_markup=subscription_menu_keyboard(),
    )
    await call.answer()


@router.callback_query(F.data == "subscription:change")
async def subscription_change(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(SubscriptionStates.waiting_duration)
    await edit_or_send(call.message, "Выберите срок подписки:", reply_markup=duration_keyboard())
    await call.answer()


@router.callback_query(F.data.startswith("subscription:duration:"), SubscriptionStates.waiting_duration)
async def subscription_pick_duration(call: CallbackQuery, state: FSMContext) -> None:
    duration_days = int(call.data.split(":")[-1])
    await state.update_data(duration_days=duration_days)
    await state.set_state(SubscriptionStates.waiting_plan_type)
    await edit_or_send(call.message, "Выберите тип подписки:", reply_markup=plan_type_keyboard())
    await call.answer()


@router.callback_query(F.data == "subscription:type:back")
async def subscription_type_back(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SubscriptionStates.waiting_plan_type)
    await edit_or_send(call.message, "Выберите тип подписки:", reply_markup=plan_type_keyboard())
    await call.answer()


@router.callback_query(F.data.startswith("subscription:type:"), SubscriptionStates.waiting_plan_type)
async def subscription_pick_type(call: CallbackQuery, state: FSMContext) -> None:
    plan_slug = call.data.split(":")[-1]
    if plan_slug not in PLAN_LABELS:
        await call.answer("Неверный тип", show_alert=True)
        return
    await state.update_data(plan_slug=plan_slug)
    await state.set_state(SubscriptionStates.waiting_traffic)
    await edit_or_send(call.message, "Выберите объем трафика:", reply_markup=traffic_keyboard())
    await call.answer()


@router.callback_query(F.data == "subscription:traffic:back")
async def subscription_traffic_back(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SubscriptionStates.waiting_traffic)
    await edit_or_send(call.message, "Выберите объем трафика:", reply_markup=traffic_keyboard())
    await call.answer()


@router.callback_query(F.data.startswith("subscription:traffic:"), SubscriptionStates.waiting_traffic)
async def subscription_pick_traffic(call: CallbackQuery, state: FSMContext) -> None:
    traffic = call.data.split(":")[-1]
    if traffic not in TRAFFIC_MULTIPLIERS:
        await call.answer("Неверный трафик", show_alert=True)
        return
    await state.update_data(traffic=traffic)
    await state.set_state(SubscriptionStates.waiting_devices)
    await edit_or_send(call.message, "Выберите количество устройств:", reply_markup=devices_keyboard())
    await call.answer()


@router.callback_query(F.data == "subscription:devices:back")
async def subscription_devices_back(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SubscriptionStates.waiting_devices)
    await edit_or_send(call.message, "Выберите количество устройств:", reply_markup=devices_keyboard())
    await call.answer()


@router.callback_query(F.data.startswith("subscription:devices:"), SubscriptionStates.waiting_devices)
async def subscription_pick_devices(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    devices = call.data.split(":")[-1]
    if devices not in DEVICES_MULTIPLIERS:
        await call.answer("Неверное количество устройств", show_alert=True)
        return

    data = await state.get_data()
    duration_days = data.get("duration_days")
    plan_slug = data.get("plan_slug")
    traffic = data.get("traffic")
    if not duration_days or not plan_slug or not traffic:
        await state.clear()
        await call.answer("Сессия выбора устарела. Начните заново.", show_alert=True)
        return

    plan_id = await _resolve_plan_id_by_slug(session, plan_slug)
    if not plan_id:
        await state.clear()
        await call.answer("Тариф не найден. Проверьте планы в БД.", show_alert=True)
        return

    plan = await PlanRepository(session).get(plan_id)
    final_price = _calc_final_price(plan.price, duration_days, traffic, devices)
    await state.update_data(
        plan_id=plan_id,
        devices=devices,
        final_price=str(final_price),
        promo_id=None,
    )
    await state.set_state(SubscriptionStates.waiting_promo_choice)
    await edit_or_send(
        call.message,
        _build_summary_text(
            {
                "duration_days": duration_days,
                "plan_slug": plan_slug,
                "traffic": traffic,
                "devices": devices,
            },
            final_price,
        ),
        reply_markup=subscription_promo_keyboard(),
    )
    await call.answer()


@router.callback_query(F.data == "subscription:promo", SubscriptionStates.waiting_promo_choice)
async def subscription_ask_promo(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SubscriptionStates.waiting_promo_code)
    await edit_or_send(call.message, "Введите промокод сообщением:")
    await call.answer()


@router.message(SubscriptionStates.waiting_promo_code)
async def subscription_apply_promo(message: Message, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    base_price = Decimal(data["final_price"])
    promo_service = PromoService(PromoRepository(session), UserRepository(session))

    try:
        final_price, promo = await promo_service.validate_and_apply(
            message.text.strip(),
            message.from_user.id,
            base_price,
        )
    except ValueError as exc:
        await send_or_answer(message, f"❌ {exc}")
        return

    await state.update_data(final_price=str(final_price), promo_id=promo.id, promo_code=promo.code)
    await _finish_purchase(message, message.from_user.id, state, session)


@router.callback_query(F.data == "subscription:skip_promo", SubscriptionStates.waiting_promo_choice)
async def subscription_skip_promo(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await _finish_purchase(call.message, call.from_user.id, state, session)
    await call.answer()


async def _finish_purchase(message: Message, user_id: int, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    plan_id = int(data["plan_id"])
    final_price = Decimal(data["final_price"])
    promo_id = data.get("promo_id")
    duration_days = int(data.get("duration_days", 30))

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

    subscription_service = SubscriptionService(
        SubscriptionRepository(session),
        PlanRepository(session),
        UserRepository(session),
        BalanceLedgerRepository(session),
        VpnKeyRepository(session),
        StubVpnKeyProvider(),
    )
    promo_service = PromoService(PromoRepository(session), UserRepository(session))
    sub = await subscription_service.purchase_with_balance(
        user_id=user_id,
        plan_id=plan_id,
        final_price=final_price,
        period_days=duration_days,
    )
    if promo_id:
        await promo_service.mark_redeemed(promo_id, user_id)

    await edit_or_send(
        message,
        "✅ Подписка активирована.\n"
        f"Действует до: <b>{sub.expires_at.strftime('%d.%m.%Y')}</b>\n"
        "Ключ выдан автоматически в разделе «Мои ключи».",
        reply_markup=subscription_menu_keyboard(),
    )
    await state.clear()
