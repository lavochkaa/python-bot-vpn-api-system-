import csv
import io
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.db.models import DiscountType, PromoCode, PromoTarget, TicketSenderRole, TicketStatus
from bot.keyboards.admin import (
    admin_back_keyboard,
    admin_menu_keyboard,
    admin_promo_delete_confirm_keyboard,
    admin_promo_details_keyboard,
    admin_promo_edit_keyboard,
    admin_promo_target_keyboard,
    admin_promo_type_keyboard,
    admin_promos_keyboard,
    admin_promos_list_keyboard,
    admin_sign_keyboard,
    admin_ticket_actions_keyboard,
    admin_tickets_keyboard,
    admin_user_manage_keyboard,
)
from bot.repositories.payment import PaymentRepository
from bot.repositories.promo import PromoRepository
from bot.repositories.subscription import SubscriptionRepository
from bot.repositories.support import SupportRepository
from bot.repositories.user import UserRepository
from bot.states.admin import AdminTicketStates
from bot.utils.messages import edit_or_send, send_or_answer, send_to_chat

router = Router()
PROMO_PAGE_SIZE = 10


def _is_admin(user_id: int) -> bool:
    return user_id in settings.admin_id_set


def _parse_optional_date(value: str) -> datetime | None:
    raw = value.strip().lower()
    if raw in {"-", "—", "none", "нет", "skip"}:
        return None
    parsed = datetime.strptime(value.strip(), "%Y-%m-%d")
    return parsed.replace(tzinfo=timezone.utc)


def _fmt_promo_short(promo: PromoCode) -> str:
    limit = promo.max_activations if promo.max_activations is not None else "∞"
    value = f"{promo.discount_value}%"
    if promo.discount_type == DiscountType.fixed:
        value = f"{promo.discount_value}₽"
    target = promo.target.value if promo.target else "both"
    return f"{promo.code} — {value} — {target} — {promo.activations_count}/{limit}"


def _fmt_promo_card(promo: PromoCode) -> str:
    expires = promo.valid_until.strftime("%d.%m.%Y") if promo.valid_until else "без даты"
    limit = promo.max_activations if promo.max_activations is not None else "без лимита"
    target = promo.target.value if promo.target else "both"
    return (
        "🎟 <b>Промокод</b>\n\n"
        f"ID: <code>{promo.id}</code>\n"
        f"Code: <b>{promo.code}</b>\n"
        f"Target: <b>{target}</b>\n"
        f"Type: <b>{promo.discount_type.value}</b>\n"
        f"Value: <b>{promo.discount_value}</b>\n"
        f"Active: <b>{'yes' if promo.is_active else 'no'}</b>\n"
        f"ExpiresAt: <b>{expires}</b>\n"
        f"UsageLimit: <b>{limit}</b>\n"
        f"UsedCount: <b>{promo.activations_count}</b>"
    )


def _user_ticket_link_keyboard(ticket_id: int):
    from aiogram.utils.keyboard import InlineKeyboardBuilder

    builder = InlineKeyboardBuilder()
    builder.button(text="🎫 Открыть тикет", callback_data=f"support:ticket:{ticket_id}")
    builder.adjust(1)
    return builder.as_markup()


def _render_ticket_messages(messages) -> str:
    if not messages:
        return "Сообщений пока нет."
    lines: list[str] = []
    for item in messages:
        role = "👤 User" if item.sender_role == TicketSenderRole.user else "🛠 Admin"
        created = item.created_at.strftime("%d.%m %H:%M")
        lines.append(f"{role} [{created}]\n{item.message_text}")
    return "\n\n".join(lines)


def _preview(text: str, limit: int = 120) -> str:
    normalized = " ".join(text.split())
    return normalized if len(normalized) <= limit else normalized[:limit] + "..."


async def _render_user_profile(user_id: int, session: AsyncSession) -> tuple[str, bool]:
    user = await UserRepository(session).get_by_tg_id(user_id)
    if not user:
        return "Пользователь не найден.", False

    sub = await SubscriptionRepository(session).get_active(user_id)
    if not sub:
        sub_text = "Подписка: <b>none</b>"
        return (
            f"👤 <b>Профиль пользователя</b>\n"
            f"ID: <code>{user.id}</code>\n"
            f"Username: @{user.username or '—'}\n"
            f"Баланс: <b>{user.balance} ₽</b>\n"
            f"{sub_text}",
            False,
        )

    plan_name = sub.plan.name if sub.plan else f"ID {sub.plan_id}"
    expires = sub.expires_at.strftime("%d.%m.%Y") if sub.expires_at else "—"
    return (
        f"👤 <b>Профиль пользователя</b>\n"
        f"ID: <code>{user.id}</code>\n"
        f"Username: @{user.username or '—'}\n"
        f"Баланс: <b>{user.balance} ₽</b>\n"
        f"Подписка: <b>{plan_name}</b>\n"
        f"Действует до: <b>{expires}</b>",
        True,
    )


async def _get_user_draft(state: FSMContext, user_id: int) -> tuple[Decimal, int]:
    data = await state.get_data()
    if data.get("draft_user_id") != user_id:
        await state.update_data(
            draft_user_id=user_id,
            balance_delta="0",
            days_delta=0,
            plan_change="",
        )
        return Decimal("0"), 0
    return Decimal(str(data.get("balance_delta", "0"))), int(data.get("days_delta", 0))


async def _render_user_profile_with_draft(
    user_id: int, session: AsyncSession, state: FSMContext
) -> tuple[str, bool]:
    text, has_sub = await _render_user_profile(user_id, session)
    balance_delta, days_delta = await _get_user_draft(state, user_id)
    draft_text = (
        "\n\n<b>Черновик изменений</b>\n"
        f"Баланс: <b>{balance_delta:+} ₽</b>\n"
        f"Дни подписки: <b>{days_delta:+} дн.</b>\n"
        "Тариф: <b>заглушка</b>"
    )
    return text + draft_text, has_sub


@router.message(Command("admin"))
async def admin_panel(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        await send_or_answer(message, "Доступ запрещен.")
        return
    await send_or_answer(
        message,
        "⚙️ <b>Админ-панель</b>\n\nВыберите раздел:",
        reply_markup=admin_menu_keyboard(),
    )


@router.callback_query(F.data == "admin:menu")
async def admin_menu(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await edit_or_send(
        call.message,
        "⚙️ <b>Админ-панель</b>\n\nВыберите раздел:",
        reply_markup=admin_menu_keyboard(),
    )
    await call.answer()


@router.callback_query(F.data == "admin:promos")
async def admin_promos(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await edit_or_send(
        call.message,
        "🎟 <b>Промокоды</b>\n\nВыберите действие:",
        reply_markup=admin_promos_keyboard(),
    )
    await call.answer()


@router.callback_query(F.data == "admin:promos:create")
async def admin_promos_create(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await state.set_state(AdminTicketStates.waiting_promo_code)
    await edit_or_send(
        call.message,
        "Введите code промокода (латиница/цифры):",
        reply_markup=admin_back_keyboard(),
    )
    await call.answer()


@router.message(AdminTicketStates.waiting_promo_code)
async def admin_promos_code_input(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not _is_admin(message.from_user.id):
        await state.clear()
        await send_or_answer(message, "Доступ запрещен.")
        return
    code = (message.text or "").strip().upper()
    if not code:
        await send_or_answer(message, "Код не может быть пустым.", reply_markup=admin_back_keyboard())
        return
    if await PromoRepository(session).get_by_code(code):
        await send_or_answer(message, "Промокод уже существует.", reply_markup=admin_back_keyboard())
        return
    await state.update_data(promo_code=code)
    await send_or_answer(
        message,
        "Выберите тип скидки:",
        reply_markup=admin_promo_type_keyboard(),
    )


@router.callback_query(F.data.startswith("admin:promo:type:"))
async def admin_promos_type_pick(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    discount_type = call.data.split(":")[-1]
    if discount_type not in {"percent", "fixed"}:
        await call.answer("Неверный тип", show_alert=True)
        return
    data = await state.get_data()
    if not data.get("promo_code"):
        await call.answer("Сначала введите code", show_alert=True)
        return
    await state.update_data(discount_type=discount_type)
    await state.set_state(AdminTicketStates.waiting_promo_target)
    await edit_or_send(call.message, "Выберите назначение промокода:", reply_markup=admin_promo_target_keyboard())
    await call.answer()


@router.callback_query(F.data.startswith("admin:promo:target:"))
async def admin_promos_target_pick(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    target = call.data.split(":")[-1]
    if target not in {"balance", "subscription"}:
        await call.answer("Неверное назначение", show_alert=True)
        return
    data = await state.get_data()
    if not data.get("promo_code") or data.get("discount_type") not in {"percent", "fixed"}:
        await call.answer("Сначала заполните код и тип", show_alert=True)
        return
    await state.update_data(promo_target=target)
    await state.set_state(AdminTicketStates.waiting_promo_value)
    discount_type = data.get("discount_type")
    prompt = "Введите value в % (1..100):" if discount_type == "percent" else "Введите value в ₽:"
    await edit_or_send(call.message, prompt, reply_markup=admin_back_keyboard())
    await call.answer()


@router.message(AdminTicketStates.waiting_promo_value)
async def admin_promos_value_input(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        await state.clear()
        await send_or_answer(message, "Доступ запрещен.")
        return
    data = await state.get_data()
    discount_type = data.get("discount_type")
    raw = (message.text or "").strip().replace(",", ".")
    try:
        value = Decimal(raw)
    except Exception:
        await send_or_answer(message, "Введите число.", reply_markup=admin_back_keyboard())
        return
    if value <= 0:
        await send_or_answer(message, "Value должно быть > 0.", reply_markup=admin_back_keyboard())
        return
    if discount_type == "percent" and value > 100:
        await send_or_answer(message, "Процент должен быть 1..100.", reply_markup=admin_back_keyboard())
        return
    await state.update_data(discount_value=str(value))
    await state.set_state(AdminTicketStates.waiting_promo_expires)
    await send_or_answer(
        message,
        "Введите expiresAt в формате YYYY-MM-DD или '-' для пропуска:",
        reply_markup=admin_back_keyboard(),
    )


@router.message(AdminTicketStates.waiting_promo_expires)
async def admin_promos_expires_input(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        await state.clear()
        await send_or_answer(message, "Доступ запрещен.")
        return
    try:
        expires_at = _parse_optional_date((message.text or "").strip())
    except Exception:
        await send_or_answer(message, "Неверная дата. Нужен формат YYYY-MM-DD или '-'.", reply_markup=admin_back_keyboard())
        return
    await state.update_data(promo_expires_at=expires_at.isoformat() if expires_at else "")
    await state.set_state(AdminTicketStates.waiting_promo_limit)
    await send_or_answer(
        message,
        "Введите usageLimit (целое > 0) или '-' для пропуска:",
        reply_markup=admin_back_keyboard(),
    )


@router.message(AdminTicketStates.waiting_promo_limit)
async def admin_promos_limit_input(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not _is_admin(message.from_user.id):
        await state.clear()
        await send_or_answer(message, "Доступ запрещен.")
        return
    raw = (message.text or "").strip().lower()
    usage_limit: int | None
    if raw in {"-", "—", "none", "нет", "skip"}:
        usage_limit = None
    elif raw.isdigit() and int(raw) > 0:
        usage_limit = int(raw)
    else:
        await send_or_answer(message, "Введите целое > 0 или '-'.", reply_markup=admin_back_keyboard())
        return

    data = await state.get_data()
    code = data.get("promo_code")
    target_raw = data.get("promo_target")
    discount_type_raw = data.get("discount_type")
    discount_value_raw = data.get("discount_value")
    if (
        not code
        or target_raw not in {"balance", "subscription"}
        or discount_type_raw not in {"percent", "fixed"}
        or not discount_value_raw
    ):
        await state.clear()
        await send_or_answer(message, "Сессия истекла. Начните заново.")
        return
    expires_raw = data.get("promo_expires_at", "")
    expires_at = datetime.fromisoformat(expires_raw) if expires_raw else None
    promo = PromoCode(
        code=str(code).upper(),
        target=PromoTarget(target_raw),
        discount_type=DiscountType(discount_type_raw),
        discount_value=Decimal(str(discount_value_raw)),
        max_activations=usage_limit,
        activations_count=0,
        valid_until=expires_at,
        is_active=True,
    )
    await PromoRepository(session).save(promo)
    await state.clear()
    await send_or_answer(
        message,
        f"✅ Промокод создан.\n\n{_fmt_promo_card(promo)}",
        reply_markup=admin_promo_details_keyboard(promo.id, active=promo.is_active),
    )


@router.callback_query(F.data.startswith("admin:promos:active:"))
async def admin_promos_active(call: CallbackQuery, session: AsyncSession) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    page = int(call.data.split(":")[-1]) if call.data.split(":")[-1].isdigit() else 1
    offset = max(page - 1, 0) * PROMO_PAGE_SIZE
    repo = PromoRepository(session)
    promos = await repo.list_active(offset=offset, limit=PROMO_PAGE_SIZE + 1)
    has_next = len(promos) > PROMO_PAGE_SIZE
    promos = promos[:PROMO_PAGE_SIZE]
    if not promos:
        await edit_or_send(
            call.message,
            "📋 Активных промокодов нет.",
            reply_markup=admin_promos_keyboard(),
        )
        await call.answer()
        return
    labels = [(item.id, _fmt_promo_short(item)) for item in promos]
    await edit_or_send(
        call.message,
        "📋 <b>Активные промокоды</b>\nВыберите промокод:",
        reply_markup=admin_promos_list_keyboard(labels, page=page, has_next=has_next),
    )
    await call.answer()


@router.callback_query(F.data.startswith("admin:promo:view:"))
async def admin_promo_view(call: CallbackQuery, session: AsyncSession) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    promo_id = int(call.data.split(":")[-1])
    promo = await PromoRepository(session).get(promo_id)
    if not promo:
        await call.answer("Промокод не найден", show_alert=True)
        return
    await edit_or_send(
        call.message,
        _fmt_promo_card(promo),
        reply_markup=admin_promo_details_keyboard(promo.id, active=promo.is_active),
    )
    await call.answer()


@router.callback_query(F.data.startswith("admin:promo:toggle:"))
async def admin_promo_toggle(call: CallbackQuery, session: AsyncSession) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    promo_id = int(call.data.split(":")[-1])
    repo = PromoRepository(session)
    promo = await repo.get(promo_id)
    if not promo:
        await call.answer("Промокод не найден", show_alert=True)
        return
    promo.is_active = not promo.is_active
    await repo.save(promo)
    await edit_or_send(
        call.message,
        _fmt_promo_card(promo),
        reply_markup=admin_promo_details_keyboard(promo.id, active=promo.is_active),
    )
    await call.answer("Обновлено")


@router.callback_query(F.data.startswith("admin:promo:delete:confirm:"))
async def admin_promo_delete_confirm(call: CallbackQuery, session: AsyncSession) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    promo_id = int(call.data.split(":")[-1])
    repo = PromoRepository(session)
    promo = await repo.get(promo_id)
    if not promo:
        await call.answer("Промокод не найден", show_alert=True)
        return
    await repo.delete(promo)
    await edit_or_send(
        call.message,
        "🗑 Промокод удалён.",
        reply_markup=admin_promos_keyboard(),
    )
    await call.answer()


@router.callback_query(F.data.startswith("admin:promo:delete:"))
async def admin_promo_delete_ask(call: CallbackQuery, session: AsyncSession) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    promo_id = int(call.data.split(":")[-1])
    promo = await PromoRepository(session).get(promo_id)
    if not promo:
        await call.answer("Промокод не найден", show_alert=True)
        return
    await edit_or_send(
        call.message,
        f"Удалить промокод <b>{promo.code}</b>?",
        reply_markup=admin_promo_delete_confirm_keyboard(promo_id),
    )
    await call.answer()


@router.callback_query(F.data.startswith("admin:promo:edit:value:"))
async def admin_promo_edit_value_start(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    promo_id = int(call.data.split(":")[-1])
    await state.set_state(AdminTicketStates.waiting_promo_edit_value)
    await state.update_data(promo_edit_id=promo_id)
    await edit_or_send(call.message, "Введите новое value:", reply_markup=admin_back_keyboard())
    await call.answer()


@router.message(AdminTicketStates.waiting_promo_edit_value)
async def admin_promo_edit_value_save(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not _is_admin(message.from_user.id):
        await state.clear()
        await send_or_answer(message, "Доступ запрещен.")
        return
    raw = (message.text or "").strip().replace(",", ".")
    try:
        value = Decimal(raw)
    except Exception:
        await send_or_answer(message, "Введите корректное число.", reply_markup=admin_back_keyboard())
        return
    promo_id = int((await state.get_data()).get("promo_edit_id", 0))
    promo = await PromoRepository(session).get(promo_id)
    if not promo:
        await state.clear()
        await send_or_answer(message, "Промокод не найден.")
        return
    if value <= 0:
        await send_or_answer(message, "Value должно быть > 0.", reply_markup=admin_back_keyboard())
        return
    if promo.discount_type == DiscountType.percent and value > 100:
        await send_or_answer(message, "Для % значение должно быть 1..100.", reply_markup=admin_back_keyboard())
        return
    promo.discount_value = value
    await PromoRepository(session).save(promo)
    await state.clear()
    await send_or_answer(
        message,
        _fmt_promo_card(promo),
        reply_markup=admin_promo_details_keyboard(promo.id, active=promo.is_active),
    )


@router.callback_query(F.data.startswith("admin:promo:edit:expires:"))
async def admin_promo_edit_expires_start(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    promo_id = int(call.data.split(":")[-1])
    await state.set_state(AdminTicketStates.waiting_promo_edit_expires)
    await state.update_data(promo_edit_id=promo_id)
    await edit_or_send(call.message, "Введите expiresAt YYYY-MM-DD или '-':", reply_markup=admin_back_keyboard())
    await call.answer()


@router.message(AdminTicketStates.waiting_promo_edit_expires)
async def admin_promo_edit_expires_save(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not _is_admin(message.from_user.id):
        await state.clear()
        await send_or_answer(message, "Доступ запрещен.")
        return
    promo_id = int((await state.get_data()).get("promo_edit_id", 0))
    promo = await PromoRepository(session).get(promo_id)
    if not promo:
        await state.clear()
        await send_or_answer(message, "Промокод не найден.")
        return
    try:
        promo.valid_until = _parse_optional_date((message.text or "").strip())
    except Exception:
        await send_or_answer(message, "Неверная дата.", reply_markup=admin_back_keyboard())
        return
    await PromoRepository(session).save(promo)
    await state.clear()
    await send_or_answer(
        message,
        _fmt_promo_card(promo),
        reply_markup=admin_promo_details_keyboard(promo.id, active=promo.is_active),
    )


@router.callback_query(F.data.startswith("admin:promo:edit:limit:"))
async def admin_promo_edit_limit_start(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    promo_id = int(call.data.split(":")[-1])
    await state.set_state(AdminTicketStates.waiting_promo_edit_limit)
    await state.update_data(promo_edit_id=promo_id)
    await edit_or_send(call.message, "Введите usageLimit (>0) или '-':", reply_markup=admin_back_keyboard())
    await call.answer()


@router.message(AdminTicketStates.waiting_promo_edit_limit)
async def admin_promo_edit_limit_save(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not _is_admin(message.from_user.id):
        await state.clear()
        await send_or_answer(message, "Доступ запрещен.")
        return
    raw = (message.text or "").strip().lower()
    usage_limit: int | None
    if raw in {"-", "—", "none", "нет", "skip"}:
        usage_limit = None
    elif raw.isdigit() and int(raw) > 0:
        usage_limit = int(raw)
    else:
        await send_or_answer(message, "Нужен usageLimit >0 или '-'.", reply_markup=admin_back_keyboard())
        return
    promo_id = int((await state.get_data()).get("promo_edit_id", 0))
    promo = await PromoRepository(session).get(promo_id)
    if not promo:
        await state.clear()
        await send_or_answer(message, "Промокод не найден.")
        return
    promo.max_activations = usage_limit
    await PromoRepository(session).save(promo)
    await state.clear()
    await send_or_answer(
        message,
        _fmt_promo_card(promo),
        reply_markup=admin_promo_details_keyboard(promo.id, active=promo.is_active),
    )


@router.callback_query(F.data.startswith("admin:promo:edit:"))
async def admin_promo_edit(call: CallbackQuery, session: AsyncSession) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    promo_id = int(call.data.split(":")[-1])
    promo = await PromoRepository(session).get(promo_id)
    if not promo:
        await call.answer("Промокод не найден", show_alert=True)
        return
    await edit_or_send(
        call.message,
        _fmt_promo_card(promo),
        reply_markup=admin_promo_edit_keyboard(promo_id),
    )
    await call.answer()


@router.callback_query(F.data == "admin:users")
async def admin_users_search(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminTicketStates.waiting_user_id)
    await edit_or_send(call.message, "Введите Telegram user_id клиента:", reply_markup=admin_back_keyboard())
    await call.answer()


@router.message(AdminTicketStates.waiting_user_id)
async def admin_users_search_input(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not _is_admin(message.from_user.id):
        await state.clear()
        await send_or_answer(message, "Доступ запрещен.")
        return
    if not (message.text or "").strip().isdigit():
        await send_or_answer(message, "Введите числовой user_id.", reply_markup=admin_back_keyboard())
        return
    user_id = int((message.text or "").strip())
    await state.clear()
    await state.update_data(draft_user_id=user_id, balance_delta="0", days_delta=0, plan_change="")
    text, has_sub = await _render_user_profile_with_draft(user_id, session, state)
    await send_or_answer(message, text, reply_markup=admin_user_manage_keyboard(user_id, has_sub))


@router.callback_query(F.data.startswith("admin:user:view:"))
async def admin_user_view(call: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    user_id = int(call.data.split(":")[-1])
    text, has_sub = await _render_user_profile_with_draft(user_id, session, state)
    await edit_or_send(call.message, text, reply_markup=admin_user_manage_keyboard(user_id, has_sub))
    await call.answer()


@router.callback_query(F.data.startswith("admin:user:edit_balance:"))
async def admin_user_edit_balance(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    user_id = int(call.data.split(":")[-1])
    await edit_or_send(
        call.message,
        f"Изменение баланса пользователя <code>{user_id}</code>:",
        reply_markup=admin_sign_keyboard("balance", user_id),
    )
    await call.answer()


@router.callback_query(F.data.startswith("admin:user:edit_days:"))
async def admin_user_edit_days(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    user_id = int(call.data.split(":")[-1])
    await edit_or_send(
        call.message,
        f"Изменение дней подписки пользователя <code>{user_id}</code>:",
        reply_markup=admin_sign_keyboard("days", user_id),
    )
    await call.answer()


@router.callback_query(F.data.startswith("admin:user:edit_plan:"))
async def admin_user_edit_plan(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.update_data(plan_change="placeholder")
    await call.answer("Изменение тарифа пока заглушка.", show_alert=True)


@router.callback_query(F.data.startswith("admin:user:balance_op:"))
async def admin_user_balance_op(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    parts = call.data.split(":")
    if len(parts) != 5:
        await call.answer("Некорректный формат", show_alert=True)
        return
    user_id = int(parts[3])
    op = parts[4]
    await state.set_state(AdminTicketStates.waiting_balance_amount)
    await state.update_data(draft_user_id=user_id, balance_op=op)
    await edit_or_send(
        call.message,
        f"Введите сумму для {'увеличения' if op == 'add' else 'уменьшения'} баланса:",
        reply_markup=admin_back_keyboard(),
    )
    await call.answer()


@router.message(AdminTicketStates.waiting_balance_amount)
async def admin_user_balance_amount_input(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not _is_admin(message.from_user.id):
        await state.clear()
        await send_or_answer(message, "Доступ запрещен.")
        return
    raw = (message.text or "").strip().replace(",", ".")
    try:
        amount = Decimal(raw)
        if amount <= 0:
            raise ValueError
    except Exception:
        await send_or_answer(message, "Введите положительное число.", reply_markup=admin_back_keyboard())
        return
    data = await state.get_data()
    user_id = int(data.get("draft_user_id", 0))
    op = data.get("balance_op", "add")
    if not user_id:
        await state.clear()
        await send_or_answer(message, "Сессия истекла.")
        return
    current_delta = Decimal(str(data.get("balance_delta", "0")))
    delta = amount if op == "add" else -amount
    await state.update_data(balance_delta=str(current_delta + delta))
    await state.set_state(None)
    text, has_sub = await _render_user_profile_with_draft(user_id, session, state)
    await send_or_answer(message, text, reply_markup=admin_user_manage_keyboard(user_id, has_sub))


@router.callback_query(F.data.startswith("admin:user:days_op:"))
async def admin_user_days_op(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    parts = call.data.split(":")
    if len(parts) != 5:
        await call.answer("Некорректный формат", show_alert=True)
        return
    user_id = int(parts[3])
    op = parts[4]
    await state.set_state(AdminTicketStates.waiting_days_amount)
    await state.update_data(draft_user_id=user_id, days_op=op)
    await edit_or_send(
        call.message,
        f"Введите количество дней для {'увеличения' if op == 'add' else 'уменьшения'}:",
        reply_markup=admin_back_keyboard(),
    )
    await call.answer()


@router.message(AdminTicketStates.waiting_days_amount)
async def admin_user_days_amount_input(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not _is_admin(message.from_user.id):
        await state.clear()
        await send_or_answer(message, "Доступ запрещен.")
        return
    if not (message.text or "").strip().isdigit():
        await send_or_answer(message, "Введите целое число.", reply_markup=admin_back_keyboard())
        return
    days = int((message.text or "").strip())
    if days <= 0:
        await send_or_answer(message, "Нужно число > 0.", reply_markup=admin_back_keyboard())
        return
    data = await state.get_data()
    user_id = int(data.get("draft_user_id", 0))
    op = data.get("days_op", "add")
    if not user_id:
        await state.clear()
        await send_or_answer(message, "Сессия истекла.")
        return
    current_delta = int(data.get("days_delta", 0))
    delta = days if op == "add" else -days
    await state.update_data(days_delta=current_delta + delta)
    await state.set_state(None)
    text, has_sub = await _render_user_profile_with_draft(user_id, session, state)
    await send_or_answer(message, text, reply_markup=admin_user_manage_keyboard(user_id, has_sub))


@router.callback_query(F.data.startswith("admin:user:apply:"))
async def admin_user_apply(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    user_id = int(call.data.split(":")[-1])
    data = await state.get_data()
    if data.get("draft_user_id") != user_id:
        await state.update_data(draft_user_id=user_id, balance_delta="0", days_delta=0, plan_change="")
        await call.answer("Нет черновика изменений", show_alert=True)
        return
    balance_delta = Decimal(str(data.get("balance_delta", "0")))
    days_delta = int(data.get("days_delta", 0))
    user_repo = UserRepository(session)
    user = await user_repo.get_by_tg_id_for_update(user_id)
    if not user:
        await call.answer("Пользователь не найден", show_alert=True)
        return
    if balance_delta:
        user.balance = max(Decimal("0"), user.balance + balance_delta)
        await user_repo.save(user)
    if days_delta:
        sub_repo = SubscriptionRepository(session)
        sub = await sub_repo.get_active(user_id)
        if sub:
            base = sub.expires_at or datetime.now(timezone.utc)
            sub.expires_at = base + timedelta(days=days_delta)
            await sub_repo.save(sub)
    await state.update_data(balance_delta="0", days_delta=0, plan_change="")
    text, has_sub = await _render_user_profile_with_draft(user_id, session, state)
    await edit_or_send(call.message, text, reply_markup=admin_user_manage_keyboard(user_id, has_sub))
    await call.answer("Изменения применены")


async def _render_admin_tickets(call: CallbackQuery, session: AsyncSession, filter_status: str) -> None:
    repo = SupportRepository(session)
    if filter_status == "closed":
        tickets = await repo.get_by_status(TicketStatus.closed, limit=20)
        title = "🎫 <b>Закрытые тикеты</b>"
    elif filter_status == "pending":
        tickets = await repo.get_open_waiting_admin(limit=20)
        title = "🎫 <b>Ожидают ответа админа</b>"
    else:
        tickets = await repo.get_by_status(TicketStatus.open, limit=20)
        title = "🎫 <b>Открытые тикеты</b>"
    if not tickets:
        await edit_or_send(
            call.message,
            f"{title}\n\nСписок пуст.",
            reply_markup=admin_tickets_keyboard([], active_filter=filter_status),
        )
        return
    lines = [title, "Выберите тикет:"]
    for ticket in tickets:
        updated = ticket.updated_at.strftime("%d.%m %H:%M") if ticket.updated_at else ticket.created_at.strftime("%d.%m %H:%M")
        lines.append(f"#{ticket.id} | tg={ticket.user_id} | {ticket.status.value} | {updated}")
    await edit_or_send(
        call.message,
        "\n".join(lines),
        reply_markup=admin_tickets_keyboard([ticket.id for ticket in tickets], active_filter=filter_status),
    )


@router.callback_query(F.data == "admin:tickets")
async def admin_tickets(call: CallbackQuery, session: AsyncSession) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await _render_admin_tickets(call, session, "open")
    await call.answer()


@router.callback_query(F.data == "admin:tickets:open")
async def admin_tickets_open(call: CallbackQuery, session: AsyncSession) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await _render_admin_tickets(call, session, "open")
    await call.answer()


@router.callback_query(F.data == "admin:tickets:pending")
async def admin_tickets_pending(call: CallbackQuery, session: AsyncSession) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await _render_admin_tickets(call, session, "pending")
    await call.answer()


@router.callback_query(F.data == "admin:tickets:closed")
async def admin_tickets_closed(call: CallbackQuery, session: AsyncSession) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await _render_admin_tickets(call, session, "closed")
    await call.answer()


@router.callback_query(F.data == "admin:tickets:search")
async def admin_tickets_search_start(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminTicketStates.waiting_ticket_search)
    await edit_or_send(
        call.message,
        "Введите ticket_id или tg_id:",
        reply_markup=admin_back_keyboard(),
    )
    await call.answer()


@router.message(AdminTicketStates.waiting_ticket_search)
async def admin_tickets_search_input(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not _is_admin(message.from_user.id):
        await state.clear()
        await send_or_answer(message, "Доступ запрещен.")
        return
    raw = (message.text or "").strip().replace("#", "")
    if not raw.isdigit():
        await send_or_answer(message, "Введите числовой ticket_id или tg_id.", reply_markup=admin_back_keyboard())
        return
    value = int(raw)
    repo = SupportRepository(session)
    by_id = await repo.get(value)
    await state.clear()
    if by_id:
        await send_or_answer(
            message,
            f"Найден тикет #{by_id.id}. Открываю...",
            reply_markup=admin_ticket_actions_keyboard(by_id.id, is_open=by_id.status != TicketStatus.closed),
        )
        return
    tickets = await repo.get_user_tickets(value, limit=20)
    if not tickets:
        await send_or_answer(message, "Ничего не найдено.", reply_markup=admin_back_keyboard())
        return
    lines = [f"Найдено тикетов для tg_id={value}:"]
    for item in tickets:
        updated = item.updated_at.strftime("%d.%m %H:%M") if item.updated_at else item.created_at.strftime("%d.%m %H:%M")
        lines.append(f"#{item.id} | {item.status.value} | {updated}")
    await send_or_answer(
        message,
        "\n".join(lines),
        reply_markup=admin_tickets_keyboard([item.id for item in tickets], active_filter="open"),
    )


@router.callback_query(F.data.startswith("admin:ticket:"))
async def admin_ticket_details(call: CallbackQuery, session: AsyncSession) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    ticket_id = int(call.data.split(":")[-1])
    repo = SupportRepository(session)
    ticket = await repo.get(ticket_id)
    if not ticket:
        await call.answer("Тикет не найден", show_alert=True)
        return
    messages = await repo.get_messages(ticket.id, limit=10)
    await edit_or_send(
        call.message,
        f"🎫 <b>Тикет #{ticket.id}</b>\n"
        f"Пользователь: <code>{ticket.user_id}</code>\n"
        f"Статус: <b>{ticket.status.value}</b>\n"
        f"Обновлён: <b>{(ticket.updated_at or ticket.created_at).strftime('%d.%m.%Y %H:%M')}</b>\n\n"
        f"{_render_ticket_messages(messages)}",
        reply_markup=admin_ticket_actions_keyboard(ticket.id, is_open=ticket.status != TicketStatus.closed),
    )
    await call.answer()


@router.callback_query(F.data.startswith("admin:reply:"))
async def admin_reply_start(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    ticket_id = int(call.data.split(":")[-1])
    ticket = await SupportRepository(session).get(ticket_id)
    if not ticket:
        await call.answer("Тикет не найден", show_alert=True)
        return
    if ticket.status == TicketStatus.closed:
        await call.answer("Тикет закрыт", show_alert=True)
        return
    await state.set_state(AdminTicketStates.waiting_reply_text)
    await state.update_data(ticket_id=ticket_id, user_id=ticket.user_id)
    await edit_or_send(
        call.message,
        f"Введите ответ по тикету #{ticket_id} (user <code>{ticket.user_id}</code>):",
        reply_markup=admin_back_keyboard(),
    )
    await call.answer()


@router.message(AdminTicketStates.waiting_reply_text)
async def admin_reply_send(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not _is_admin(message.from_user.id):
        await state.clear()
        await send_or_answer(message, "Доступ запрещен.")
        return
    text = (message.text or "").strip()
    if not text:
        await send_or_answer(message, "Отправьте текст ответа.", reply_markup=admin_back_keyboard())
        return
    data = await state.get_data()
    ticket_id = data.get("ticket_id")
    user_id = data.get("user_id")
    if not ticket_id or not user_id:
        await state.clear()
        await send_or_answer(message, "Сессия устарела.")
        return
    repo = SupportRepository(session)
    ticket = await repo.get(int(ticket_id))
    if not ticket:
        await state.clear()
        await send_or_answer(message, "Тикет не найден.")
        return
    if ticket.status == TicketStatus.closed:
        await state.clear()
        await send_or_answer(message, "Тикет закрыт.")
        return
    await repo.add_message(ticket, TicketSenderRole.admin, text)
    if ticket.status == TicketStatus.pending:
        ticket.status = TicketStatus.open
        await repo.save(ticket)
    try:
        await send_to_chat(
            message.bot,
            int(user_id),
            f"💬 Ответ поддержки по тикету #{ticket.id}:\n\n{text}",
            reply_markup=_user_ticket_link_keyboard(ticket.id),
        )
    except TelegramAPIError:
        await send_or_answer(message, "Не удалось отправить ответ пользователю.")
        await state.clear()
        return
    await send_or_answer(
        message,
        f"Ответ по тикету #{ticket.id} отправлен.\nПревью: {_preview(text)}",
        reply_markup=admin_ticket_actions_keyboard(ticket.id, is_open=True),
    )
    await state.clear()


@router.callback_query(F.data.startswith("admin:close:"))
async def admin_close_ticket(call: CallbackQuery, session: AsyncSession) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    ticket_id = int(call.data.split(":")[-1])
    repo = SupportRepository(session)
    ticket = await repo.get(ticket_id)
    if not ticket:
        await call.answer("Тикет не найден", show_alert=True)
        return
    if ticket.status == TicketStatus.closed:
        await call.answer("Тикет уже закрыт", show_alert=True)
        return
    ticket.status = TicketStatus.closed
    ticket.updated_at = datetime.now(timezone.utc)
    await repo.save(ticket)
    try:
        await send_to_chat(
            call.bot,
            ticket.user_id,
            f"✅ Тикет #{ticket.id} закрыт администратором.",
            reply_markup=_user_ticket_link_keyboard(ticket.id),
        )
    except TelegramAPIError:
        pass
    await admin_ticket_details(call, session)


@router.callback_query(F.data == "admin:stats")
async def admin_stats(call: CallbackQuery, session: AsyncSession) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    user_repo = UserRepository(session)
    payment_repo = PaymentRepository(session)
    sub_repo = SubscriptionRepository(session)

    users = await user_repo.all()
    topup_by_user = await payment_repo.topup_stats_by_user()
    topup_ops_total, topup_sum_total = await payment_repo.topup_totals()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["tg_id", "есть_подписка", "подписка", "type", "пополнил_за_всё_время"])
    for user in users:
        active_sub = await sub_repo.get_active(user.id)
        latest_sub = active_sub or await sub_repo.get_latest(user.id)
        has_sub = "да" if active_sub else "нет"
        sub_text = "—"
        sub_type = "—"
        if latest_sub:
            plan_name = latest_sub.plan.name if latest_sub.plan else f"plan#{latest_sub.plan_id}"
            traffic = f"{latest_sub.traffic_gb}ГБ" if latest_sub.traffic_gb else "—"
            duration = f"{latest_sub.duration_days}д" if latest_sub.duration_days else "—"
            sub_text = f"{plan_name}; {traffic}; {duration}"
            sub_type = latest_sub.plan_type or "—"
        topup_sum = topup_by_user.get(user.id, (0, Decimal("0")))[1]
        writer.writerow([user.id, has_sub, sub_text, sub_type, str(topup_sum)])

    writer.writerow([])
    writer.writerow(["TOTALS"])
    writer.writerow(["Всего пользователей", str(len(users))])
    writer.writerow(["Всего пополнений (кол-во операций)", str(topup_ops_total)])
    writer.writerow(["Сумма пополнений", str(topup_sum_total)])

    file_bytes = buf.getvalue().encode("utf-8")
    await call.message.answer_document(
        BufferedInputFile(file=file_bytes, filename="admin_stats.csv"),
        caption="📊 Отчёт по пользователям сформирован.",
    )
    await edit_or_send(
        call.message,
        "📊 <b>Итоги</b>\n\n"
        f"Всего пользователей: <b>{len(users)}</b>\n"
        f"Всего пополнений (операций): <b>{topup_ops_total}</b>\n"
        f"Сумма пополнений: <b>{topup_sum_total} ₽</b>",
        reply_markup=admin_back_keyboard(),
    )
    await call.answer()
