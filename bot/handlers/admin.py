from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from bot.config import settings
from bot.db.models import TicketStatus
from bot.keyboards.admin import (
    admin_back_keyboard,
    admin_menu_keyboard,
    admin_sign_keyboard,
    admin_ticket_actions_keyboard,
    admin_tickets_keyboard,
    admin_user_manage_keyboard,
)
from bot.repositories.support import SupportRepository
from bot.repositories.subscription import SubscriptionRepository
from bot.repositories.user import UserRepository
from bot.states.admin import AdminTicketStates
from bot.utils.messages import edit_or_send, send_or_answer, send_to_chat

router = Router()


def _is_admin(user_id: int) -> bool:
    return user_id in settings.admin_id_set


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

    def _fmt_signed_decimal(value: Decimal) -> str:
        if value > 0:
            return f"+{value}"
        return str(value)

    def _fmt_signed_int(value: int) -> str:
        if value > 0:
            return f"+{value}"
        return str(value)

    draft_text = (
        "\n\n<b>Черновик изменений</b>\n"
        f"Баланс: <b>{_fmt_signed_decimal(balance_delta)} ₽</b>\n"
        f"Дни подписки: <b>{_fmt_signed_int(days_delta)} дн.</b>\n"
        "Тариф: <b>заглушка</b>"
    )
    return text + draft_text, has_sub


def _user_ticket_link_keyboard(ticket_id: int):
    from aiogram.utils.keyboard import InlineKeyboardBuilder

    builder = InlineKeyboardBuilder()
    builder.button(text="🎫 Перейти к обращению", callback_data=f"support:ticket:{ticket_id}")
    builder.adjust(1)
    return builder.as_markup()


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
async def admin_menu(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return

    await edit_or_send(
        call.message,
        "⚙️ <b>Админ-панель</b>\n\nВыберите раздел:",
        reply_markup=admin_menu_keyboard(),
    )
    await call.answer()


@router.callback_query(F.data == "admin:users")
async def admin_users_search(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminTicketStates.waiting_user_id)
    await edit_or_send(call.message, "Введите Telegram user_id клиента:")
    await call.answer()


@router.message(AdminTicketStates.waiting_user_id)
async def admin_users_search_input(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not _is_admin(message.from_user.id):
        await state.clear()
        await send_or_answer(message, "Доступ запрещен.")
        return
    if not message.text or not message.text.strip().isdigit():
        await send_or_answer(message, "Введите числовой user_id.")
        return

    user_id = int(message.text.strip())
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
    await edit_or_send(
        call.message,
        text,
        reply_markup=admin_user_manage_keyboard(user_id, has_sub),
    )
    await call.answer()


@router.callback_query(F.data.startswith("admin:user:edit_balance:"))
async def admin_user_edit_balance(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    user_id = int(call.data.split(":")[-1])
    await edit_or_send(
        call.message,
        f"Изменение баланса для <code>{user_id}</code>.\nВыберите действие:",
        reply_markup=admin_sign_keyboard("balance", user_id),
    )
    await call.answer()


@router.callback_query(F.data.startswith("admin:user:edit_days:"))
async def admin_user_edit_days(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    user_id = int(call.data.split(":")[-1])
    await edit_or_send(
        call.message,
        f"Изменение дней подписки для <code>{user_id}</code>.\nВыберите действие:",
        reply_markup=admin_sign_keyboard("days", user_id),
    )
    await call.answer()


@router.callback_query(F.data.startswith("admin:user:edit_plan:"))
async def admin_user_edit_plan(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    user_id = int(call.data.split(":")[-1])
    await state.update_data(plan_change="placeholder")
    await call.answer("Пока заглушка: изменение тарифа будет добавлено позже.", show_alert=True)


@router.callback_query(F.data.startswith("admin:user:balance_op:"))
async def admin_user_balance_op(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    parts = call.data.split(":")
    if len(parts) != 5:
        await call.answer("Некорректный формат действия", show_alert=True)
        return
    _, _, _, user_id_raw, op = parts
    user_id = int(user_id_raw)
    await state.set_state(AdminTicketStates.waiting_balance_amount)
    await state.update_data(draft_user_id=user_id, balance_op=op)
    await edit_or_send(
        call.message,
        f"Введите сумму для {'увеличения' if op == 'add' else 'уменьшения'} баланса пользователя <code>{user_id}</code>:",
    )
    await call.answer()


@router.message(AdminTicketStates.waiting_balance_amount)
async def admin_user_balance_amount_input(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not _is_admin(message.from_user.id):
        await state.clear()
        await send_or_answer(message, "Доступ запрещен.")
        return
    if not message.text:
        await send_or_answer(message, "Введите сумму числом.")
        return
    raw = message.text.strip().replace(",", ".")
    try:
        amount = Decimal(raw)
        if amount <= 0:
            raise ValueError
    except Exception:
        await send_or_answer(message, "Введите положительное число, например 250.")
        return

    data = await state.get_data()
    user_id = int(data.get("draft_user_id", 0))
    op = data.get("balance_op", "add")
    if not user_id:
        await state.clear()
        await send_or_answer(message, "Сессия истекла. Начните заново через поиск клиента.")
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
        await call.answer("Некорректный формат действия", show_alert=True)
        return
    _, _, _, user_id_raw, op = parts
    user_id = int(user_id_raw)
    await state.set_state(AdminTicketStates.waiting_days_amount)
    await state.update_data(draft_user_id=user_id, days_op=op)
    await edit_or_send(
        call.message,
        f"Введите количество дней для {'увеличения' if op == 'add' else 'уменьшения'} подписки пользователя <code>{user_id}</code>:",
    )
    await call.answer()


@router.message(AdminTicketStates.waiting_days_amount)
async def admin_user_days_amount_input(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not _is_admin(message.from_user.id):
        await state.clear()
        await send_or_answer(message, "Доступ запрещен.")
        return
    if not message.text or not message.text.strip().isdigit():
        await send_or_answer(message, "Введите целое положительное число дней.")
        return
    days = int(message.text.strip())
    if days <= 0:
        await send_or_answer(message, "Введите число больше нуля.")
        return

    data = await state.get_data()
    user_id = int(data.get("draft_user_id", 0))
    op = data.get("days_op", "add")
    if not user_id:
        await state.clear()
        await send_or_answer(message, "Сессия истекла. Начните заново через поиск клиента.")
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
        await call.answer("Нет черновика изменений для этого пользователя.", show_alert=True)
        return

    balance_delta = Decimal(str(data.get("balance_delta", "0")))
    days_delta = int(data.get("days_delta", 0))
    plan_change = data.get("plan_change", "")

    user_repo = UserRepository(session)
    user = await user_repo.get_by_tg_id_for_update(user_id)
    if not user:
        await call.answer("Пользователь не найден", show_alert=True)
        return

    if balance_delta:
        user.balance = max(Decimal("0"), user.balance + balance_delta)
        await user_repo.save(user)

    note = []
    if days_delta:
        sub_repo = SubscriptionRepository(session)
        sub = await sub_repo.get_active(user_id)
        if sub:
            base = sub.expires_at or datetime.now(timezone.utc)
            sub.expires_at = base + timedelta(days=days_delta)
            await sub_repo.save(sub)
        else:
            note.append("Нет активной подписки: дни не применены.")

    if plan_change:
        note.append("Изменение тарифа пока заглушка.")

    await state.update_data(balance_delta="0", days_delta=0, plan_change="")
    text, has_sub = await _render_user_profile_with_draft(user_id, session, state)
    if note:
        text += "\n\n" + "\n".join(note)
    await edit_or_send(
        call.message,
        text,
        reply_markup=admin_user_manage_keyboard(user_id, has_sub),
    )
    await call.answer("Изменения применены")


async def _render_admin_tickets(call: CallbackQuery, session: AsyncSession, filter_status: str) -> None:
    repo = SupportRepository(session)
    if filter_status == "closed":
        tickets = await repo.get_by_status(TicketStatus.closed, limit=20)
        title = "🎫 <b>Закрытые тикеты</b>"
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

    lines = [title, "Выберите тикет кнопкой ниже:"]
    for ticket in tickets:
        created = ticket.created_at.strftime("%d.%m %H:%M")
        lines.append(f"#{ticket.id} | user={ticket.user_id} | {ticket.status.value} | {created}")

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


@router.callback_query(F.data == "admin:tickets:closed")
async def admin_tickets_closed(call: CallbackQuery, session: AsyncSession) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await _render_admin_tickets(call, session, "closed")
    await call.answer()


@router.callback_query(F.data.startswith("admin:ticket:"))
async def admin_ticket_details(call: CallbackQuery, session: AsyncSession) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return

    ticket_id = int(call.data.split(":")[-1])
    ticket = await SupportRepository(session).get(ticket_id)
    if not ticket:
        await call.answer("Тикет не найден", show_alert=True)
        return

    await edit_or_send(
        call.message,
        f"🎫 <b>Тикет #{ticket.id}</b>\n"
        f"Пользователь: <code>{ticket.user_id}</code>\n"
        f"Статус: <b>{ticket.status.value}</b>\n"
        f"Создан: <b>{ticket.created_at.strftime('%d.%m.%Y %H:%M')}</b>\n\n"
        f"<b>Текст:</b>\n{ticket.text}",
        reply_markup=admin_ticket_actions_keyboard(ticket.id, is_open=ticket.status == TicketStatus.open),
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

    await state.set_state(AdminTicketStates.waiting_reply_text)
    await state.update_data(ticket_id=ticket_id, user_id=ticket.user_id)
    await edit_or_send(
        call.message,
        f"Введите ответ для пользователя <code>{ticket.user_id}</code> по тикету #{ticket_id}:"
    )
    await call.answer()


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
    await repo.save(ticket)
    try:
        await send_to_chat(
            call.bot,
            ticket.user_id,
            f"✅ Тикет #{ticket.id} был закрыт администратором.",
            reply_markup=_user_ticket_link_keyboard(ticket.id),
        )
    except TelegramAPIError:
        pass

    await call.answer("Тикет закрыт")
    await admin_ticket_details(call, session)


@router.message(AdminTicketStates.waiting_reply_text)
async def admin_reply_send(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not _is_admin(message.from_user.id):
        await state.clear()
        await send_or_answer(message, "Доступ запрещен.")
        return
    if not message.text or not message.text.strip():
        await send_or_answer(message, "Отправьте текст ответа.")
        return

    data = await state.get_data()
    ticket_id = data.get("ticket_id")
    user_id = data.get("user_id")
    if not ticket_id or not user_id:
        await state.clear()
        await send_or_answer(message, "Сессия ответа устарела. Откройте тикет заново.")
        return

    ticket = await SupportRepository(session).get(int(ticket_id))
    if not ticket:
        await state.clear()
        await send_or_answer(message, "Тикет не найден.")
        return

    try:
        await send_to_chat(
            message.bot,
            int(user_id),
            (
                f"💬 Ответ поддержки по тикету #{ticket.id}:\n\n"
                f"{message.text.strip()}"
            ),
            reply_markup=_user_ticket_link_keyboard(ticket.id),
        )
    except TelegramAPIError:
        await send_or_answer(
            message,
            f"Не удалось отправить ответ пользователю {user_id}. "
            "Возможно, пользователь заблокировал бота."
        )
        await state.clear()
        return

    await send_or_answer(message, f"Ответ по тикету #{ticket.id} отправлен пользователю {user_id}.")
    await state.clear()


@router.callback_query(F.data == "admin:stats")
async def admin_stats(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return

    await edit_or_send(
        call.message,
        "📊 <b>Статистика</b>\n\nРаздел в разработке (заглушка).",
        reply_markup=admin_back_keyboard(),
    )
    await call.answer()
