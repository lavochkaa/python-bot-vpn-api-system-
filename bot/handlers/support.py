from aiogram import Router, F
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime

from bot.config import settings
from bot.db.models import TicketStatus
from bot.states.support import SupportStates
from bot.keyboards.support import support_keyboard
from bot.services.support import SupportService
from bot.repositories.support import SupportRepository
from bot.utils.messages import edit_or_send, send_or_answer, send_to_chat
from bot.utils.messages import edit_or_send

router = Router()


def _admin_ticket_link_keyboard(ticket_id: int):
    builder = InlineKeyboardBuilder()
    builder.button(text="🎫 Перейти к обращению", callback_data=f"admin:ticket:{ticket_id}")
    builder.adjust(1)
    return builder.as_markup()


def _user_ticket_link_keyboard(ticket_id: int):
    builder = InlineKeyboardBuilder()
    builder.button(text="🎫 Перейти к обращению", callback_data=f"support:ticket:{ticket_id}")
    builder.adjust(1)
    return builder.as_markup()


@router.callback_query(F.data == "menu:support")
async def support_menu(call: CallbackQuery) -> None:
    await edit_or_send(
        call.message,
        "🆘 <b>Поддержка</b>\n\n"
        "Опишите вашу проблему — мы ответим в течение 24 часов.",
        reply_markup=support_keyboard(),
    )
    await call.answer()


@router.callback_query(F.data == "support:create")
async def ask_support_message(call: CallbackQuery, state: FSMContext) -> None:
    await edit_or_send(
        call.message,
        "✍️ Опишите вашу проблему или вопрос подробнее:\n\n"
        "<i>Отправьте текстовое сообщение</i>"
    )
    await state.set_state(SupportStates.waiting_message)
    await call.answer()


@router.callback_query(F.data == "support:my_tickets")
async def my_tickets(call: CallbackQuery, session: AsyncSession) -> None:
    tickets = await SupportRepository(session).get_user_tickets(call.from_user.id, limit=10)
    if not tickets:
        await edit_or_send(
            call.message,
            "🎫 <b>Мои тикеты</b>\n\nУ вас пока нет обращений.",
            reply_markup=support_keyboard(),
        )
        await call.answer()
        return

    builder = InlineKeyboardBuilder()
    lines = ["🎫 <b>Мои тикеты</b>", "Выберите тикет:"]
    for ticket in tickets:
        created = ticket.created_at.strftime("%d.%m %H:%M")
        lines.append(f"#{ticket.id} | {ticket.status.value} | {created}")
        builder.button(text=f"Тикет #{ticket.id}", callback_data=f"support:ticket:{ticket.id}")
    builder.button(text="🔙 Назад", callback_data="menu:support")
    builder.adjust(1)
    await edit_or_send(call.message, "\n".join(lines), reply_markup=builder.as_markup())
    await call.answer()


@router.callback_query(F.data.startswith("support:ticket:"))
async def ticket_details(call: CallbackQuery, session: AsyncSession) -> None:
    ticket_id = int(call.data.split(":")[-1])
    ticket = await SupportRepository(session).get(ticket_id)
    if not ticket or ticket.user_id != call.from_user.id:
        await call.answer("Тикет не найден", show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    if ticket.status == TicketStatus.open:
        builder.button(text="➕ Дополнить", callback_data=f"support:append:{ticket.id}")
        builder.button(text="✅ Закрыть тикет", callback_data=f"support:close:{ticket.id}")
    builder.button(text="🔙 К моим тикетам", callback_data="support:my_tickets")
    builder.adjust(1)
    await edit_or_send(
        call.message,
        f"🎫 <b>Тикет #{ticket.id}</b>\n"
        f"Статус: <b>{ticket.status.value}</b>\n"
        f"Создан: <b>{ticket.created_at.strftime('%d.%m.%Y %H:%M')}</b>\n\n"
        f"{ticket.text}",
        reply_markup=builder.as_markup(),
    )
    await call.answer()


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

    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 К моим тикетам", callback_data="support:my_tickets")
    builder.adjust(1)
    await edit_or_send(
        call.message,
        f"🎫 <b>Тикет #{ticket.id}</b>\n"
        f"Статус: <b>{ticket.status.value}</b>\n"
        f"Создан: <b>{ticket.created_at.strftime('%d.%m.%Y %H:%M')}</b>\n\n"
        f"{ticket.text}",
        reply_markup=builder.as_markup(),
    )
    await call.answer("Тикет закрыт")


@router.callback_query(F.data.startswith("support:append:"))
async def ticket_append_start(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    ticket_id = int(call.data.split(":")[-1])
    ticket = await SupportRepository(session).get(ticket_id)
    if not ticket or ticket.user_id != call.from_user.id:
        await call.answer("Тикет не найден", show_alert=True)
        return
    if ticket.status == TicketStatus.closed:
        await call.answer("Нельзя дополнить закрытый тикет", show_alert=True)
        return

    await state.set_state(SupportStates.waiting_append_message)
    await state.update_data(ticket_id=ticket.id)
    await edit_or_send(call.message, f"Отправьте дополнение к тикету #{ticket.id}:")
    await call.answer()


@router.message(SupportStates.waiting_append_message)
async def ticket_append_save(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not message.text or not message.text.strip():
        await send_or_answer(message, "Отправьте текстовое дополнение.")
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
        await send_or_answer(message, "Нельзя дополнить закрытый тикет.")
        return

    append_text = message.text.strip()
    timestamp = datetime.now().strftime("%d.%m.%Y %H:%M")
    ticket.text = f"{ticket.text}\n\n--- Дополнение {timestamp} ---\n{append_text}"
    await repo.save(ticket)

    for admin_id in settings.admin_id_set:
        try:
            await send_to_chat(
                message.bot,
                admin_id,
                f"✍️ Новое дополнение к тикету #{ticket.id} от <code>{message.from_user.id}</code>:\n\n{append_text}",
                reply_markup=_admin_ticket_link_keyboard(ticket.id),
            )
        except TelegramAPIError:
            continue

    await send_or_answer(message, f"Дополнение к тикету #{ticket.id} сохранено.")
    await state.clear()


@router.message(SupportStates.waiting_message)
async def create_ticket(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not message.text or not message.text.strip():
        await send_or_answer(message, "Отправьте текстовое сообщение для создания обращения.")
        return

    service = SupportService(SupportRepository(session))
    ticket = await service.create_ticket(message.from_user.id, message.text.strip())
    await send_or_answer(
        message,
        f"✅ <b>Запрос принят!</b>\n\n"
        f"🎫 Номер тикета: <b>#{ticket.id}</b>\n\n"
        f"Мы свяжемся с вами в ближайшее время. Нажмите /start для возврата в меню.",
        reply_markup=_user_ticket_link_keyboard(ticket.id),
    )
    for admin_id in settings.admin_id_set:
        try:
            await send_to_chat(
                message.bot,
                admin_id,
                f"🔔 Новый тикет #{ticket.id}\n"
                f"От пользователя: <code>{message.from_user.id}</code>\n\n"
                f"{ticket.text}",
                reply_markup=_admin_ticket_link_keyboard(ticket.id),
            )
        except TelegramAPIError:
            continue
    await state.clear()
