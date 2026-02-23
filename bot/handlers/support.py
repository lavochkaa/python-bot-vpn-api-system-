from datetime import datetime, timezone

from aiogram import Router, F
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.db.models import TicketSenderRole, TicketStatus
from bot.keyboards.support import support_keyboard
from bot.repositories.support import SupportRepository
from bot.services.support import SupportService
from bot.states.support import SupportStates
from bot.utils.messages import edit_or_send, send_or_answer, send_to_chat

router = Router()
PAGE_SIZE = 10


def _admin_ticket_link_keyboard(ticket_id: int):
    builder = InlineKeyboardBuilder()
    builder.button(text="🎫 Открыть тикет", callback_data=f"admin:ticket:{ticket_id}")
    builder.adjust(1)
    return builder.as_markup()


def _user_ticket_link_keyboard(ticket_id: int):
    builder = InlineKeyboardBuilder()
    builder.button(text="🎫 Открыть тикет", callback_data=f"support:ticket:{ticket_id}")
    builder.adjust(1)
    return builder.as_markup()


def _user_tickets_keyboard(ticket_ids: list[int], page: int, has_next: bool):
    builder = InlineKeyboardBuilder()
    for ticket_id in ticket_ids:
        builder.button(text=f"Тикет #{ticket_id}", callback_data=f"support:ticket:{ticket_id}")
    if page > 1:
        builder.button(text="⬅️", callback_data=f"support:my_tickets:{page - 1}")
    if has_next:
        builder.button(text="➡️", callback_data=f"support:my_tickets:{page + 1}")
    builder.button(text="🔙 Назад", callback_data="menu:support")
    builder.adjust(1)
    return builder.as_markup()


def _user_ticket_actions_keyboard(ticket_id: int, is_open: bool):
    builder = InlineKeyboardBuilder()
    if is_open:
        builder.button(text="✍️ Ответить", callback_data=f"support:reply:{ticket_id}")
        builder.button(text="✅ Закрыть", callback_data=f"support:close:{ticket_id}")
    builder.button(text="⬅️ Назад", callback_data="support:my_tickets:1")
    builder.adjust(1)
    return builder.as_markup()


def _preview(text: str, limit: int = 120) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit] + "..."


def _render_messages(messages) -> str:
    if not messages:
        return "Сообщений пока нет."
    lines: list[str] = []
    for item in messages:
        role = "👤 Пользователь" if item.sender_role == TicketSenderRole.user else "🛠 Админ"
        created = item.created_at.strftime("%d.%m %H:%M")
        lines.append(f"{role} [{created}]\n{item.message_text}")
    return "\n\n".join(lines)


async def _notify_admins_about_ticket_message(
    message: Message,
    ticket_id: int,
    user_id: int,
    text: str,
    is_new: bool,
) -> None:
    prefix = "🔔 Новый тикет" if is_new else "✍️ Новое сообщение в тикете"
    payload = (
        f"{prefix} #{ticket_id}\n"
        f"Пользователь: <code>{user_id}</code>\n"
        f"Превью: { _preview(text) }"
    )
    for admin_id in settings.admin_id_set:
        try:
            await send_to_chat(
                message.bot,
                admin_id,
                payload,
                reply_markup=_admin_ticket_link_keyboard(ticket_id),
            )
        except TelegramAPIError:
            continue


@router.callback_query(F.data == "menu:support")
async def support_menu(call: CallbackQuery) -> None:
    await edit_or_send(
        call.message,
        "🆘 <b>Поддержка</b>\n\nВыберите действие:",
        reply_markup=support_keyboard(),
    )
    await call.answer()


@router.callback_query(F.data == "support:create")
async def ask_support_message(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(SupportStates.waiting_message)
    await edit_or_send(
        call.message,
        "✍️ Отправьте текст обращения одним сообщением.",
    )
    await call.answer()


@router.callback_query(F.data.startswith("support:my_tickets"))
async def my_tickets(call: CallbackQuery, session: AsyncSession) -> None:
    parts = call.data.split(":")
    page = int(parts[-1]) if parts[-1].isdigit() else 1
    repo = SupportRepository(session)
    tickets = await repo.get_user_tickets_paginated(call.from_user.id, page, page_size=PAGE_SIZE + 1)
    has_next = len(tickets) > PAGE_SIZE
    tickets = tickets[:PAGE_SIZE]
    if not tickets:
        await edit_or_send(
            call.message,
            "📨 <b>Мои обращения</b>\n\nУ вас пока нет обращений.",
            reply_markup=support_keyboard(),
        )
        await call.answer()
        return

    lines = ["📨 <b>Мои обращения</b>", ""]
    for item in tickets:
        updated = item.updated_at.strftime("%d.%m %H:%M") if item.updated_at else item.created_at.strftime("%d.%m %H:%M")
        lines.append(f"#{item.id} | {item.status.value} | обновлен: {updated}")
    await edit_or_send(
        call.message,
        "\n".join(lines),
        reply_markup=_user_tickets_keyboard([item.id for item in tickets], page=page, has_next=has_next),
    )
    await call.answer()


@router.callback_query(F.data.startswith("support:ticket:"))
async def ticket_details(call: CallbackQuery, session: AsyncSession) -> None:
    ticket_id = int(call.data.split(":")[-1])
    repo = SupportRepository(session)
    ticket = await repo.get(ticket_id)
    if not ticket or ticket.user_id != call.from_user.id:
        await call.answer("Тикет не найден", show_alert=True)
        return
    messages = await repo.get_messages(ticket.id, limit=10)
    updated = ticket.updated_at.strftime("%d.%m.%Y %H:%M") if ticket.updated_at else ticket.created_at.strftime("%d.%m.%Y %H:%M")
    await edit_or_send(
        call.message,
        f"🎫 <b>Тикет #{ticket.id}</b>\n"
        f"Статус: <b>{ticket.status.value}</b>\n"
        f"Обновлён: <b>{updated}</b>\n\n"
        f"{_render_messages(messages)}",
        reply_markup=_user_ticket_actions_keyboard(ticket.id, is_open=ticket.status != TicketStatus.closed),
    )
    await call.answer()


@router.callback_query(F.data.startswith("support:reply:"))
async def ticket_reply_start(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    ticket_id = int(call.data.split(":")[-1])
    ticket = await SupportRepository(session).get(ticket_id)
    if not ticket or ticket.user_id != call.from_user.id:
        await call.answer("Тикет не найден", show_alert=True)
        return
    if ticket.status == TicketStatus.closed:
        await call.answer("Тикет уже закрыт", show_alert=True)
        return
    await state.set_state(SupportStates.waiting_reply_message)
    await state.update_data(ticket_id=ticket.id)
    await edit_or_send(call.message, f"✍️ Введите сообщение для тикета #{ticket.id}:")
    await call.answer()


@router.message(SupportStates.waiting_reply_message)
async def ticket_reply_save(message: Message, state: FSMContext, session: AsyncSession) -> None:
    text = (message.text or "").strip()
    if not text:
        await send_or_answer(message, "Отправьте текстовое сообщение.")
        return
    data = await state.get_data()
    ticket_id = data.get("ticket_id")
    if not ticket_id:
        await state.clear()
        await send_or_answer(message, "Сессия устарела. Откройте тикет заново.")
        return
    repo = SupportRepository(session)
    ticket = await repo.get(int(ticket_id))
    if not ticket or ticket.user_id != message.from_user.id:
        await state.clear()
        await send_or_answer(message, "Тикет не найден.")
        return
    if ticket.status == TicketStatus.closed:
        await state.clear()
        await send_or_answer(message, "Тикет закрыт.")
        return
    await repo.add_message(ticket, TicketSenderRole.user, text)
    await send_or_answer(
        message,
        f"Сообщение по тикету #{ticket.id} отправлено.",
        reply_markup=_user_ticket_link_keyboard(ticket.id),
    )
    await _notify_admins_about_ticket_message(
        message=message,
        ticket_id=ticket.id,
        user_id=message.from_user.id,
        text=text,
        is_new=False,
    )
    await state.clear()


@router.callback_query(F.data.startswith("support:close:"))
async def ticket_close(call: CallbackQuery, session: AsyncSession) -> None:
    ticket_id = int(call.data.split(":")[-1])
    repo = SupportRepository(session)
    ticket = await repo.get(ticket_id)
    if not ticket or ticket.user_id != call.from_user.id:
        await call.answer("Тикет не найден", show_alert=True)
        return
    if ticket.status == TicketStatus.closed:
        await call.answer("Тикет уже закрыт", show_alert=True)
        return
    ticket.status = TicketStatus.closed
    ticket.updated_at = datetime.now(timezone.utc)
    await repo.save(ticket)
    for admin_id in settings.admin_id_set:
        try:
            await send_to_chat(
                call.bot,
                admin_id,
                f"✅ Пользователь <code>{call.from_user.id}</code> закрыл тикет #{ticket.id}.",
                reply_markup=_admin_ticket_link_keyboard(ticket.id),
            )
        except TelegramAPIError:
            continue
    await ticket_details(call, session)


@router.message(SupportStates.waiting_message)
async def create_ticket(message: Message, state: FSMContext, session: AsyncSession) -> None:
    text = (message.text or "").strip()
    if not text:
        await send_or_answer(message, "Отправьте текст обращения.")
        return
    service = SupportService(SupportRepository(session))
    ticket = await service.create_ticket(message.from_user.id, text)
    await send_or_answer(
        message,
        f"✅ Обращение создано.\n"
        f"Номер тикета: <b>#{ticket.id}</b>",
        reply_markup=_user_ticket_link_keyboard(ticket.id),
    )
    await _notify_admins_about_ticket_message(
        message=message,
        ticket_id=ticket.id,
        user_id=message.from_user.id,
        text=text,
        is_new=True,
    )
    await state.clear()
