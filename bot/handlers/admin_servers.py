"""Admin server monitoring handlers.

Uses the same single-message anchor pattern as admin.py:
- Callback handlers: edit call.message in-place, store anchor
- FSM message handlers: edit the tracked anchor message via _admin_edit_message
This keeps all admin UI in one message that never disappears.
"""
import asyncio
import html
import ssl
from datetime import datetime, timezone
from decimal import Decimal

import aiohttp
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.db.models import TrackedServer
from bot.keyboards.admin import (
    admin_back_keyboard,
    admin_server_delete_confirm_keyboard,
    admin_server_list_keyboard,
    admin_server_provider_type_keyboard,
    admin_server_view_keyboard,
    admin_servers_keyboard,
)
from bot.repositories.payment import PaymentRepository
from bot.repositories.server import ServerRepository
from bot.states.admin import AdminTicketStates
from bot.utils.messages import edit_or_send

router = Router()

_CHECK_TIMEOUT = 8

_PROVIDERS_WITH_CREDENTIALS = {
    "beget": (
        "Введите логин Beget-аккаунта:",
        "Введите пароль Beget-аккаунта:",
        None,
    ),
    "yandex_cloud": (
        "Введите folder_id (Yandex Cloud):",
        "Введите API-ключ (Yandex Cloud):",
        "Введите billing_account_id (или '-' чтобы пропустить):",
    ),
    "play2go": (
        "Введите идентификатор сервера Pterodactyl (или '-' если один сервер):",
        "Введите API токен Play2go (начинается с ptlc_):",
        None,
    ),
}
_PROVIDERS_NO_CREDENTIALS = {"firstbyte", "manual"}


# ─────────────────────────────────────────────────────────
# Anchor helpers (same pattern as admin.py)
# ─────────────────────────────────────────────────────────

def _is_admin(user_id: int) -> bool:
    return user_id in settings.admin_id_set


async def _set_anchor(state: FSMContext, message: Message) -> None:
    await state.update_data(admin_chat_id=message.chat.id, admin_message_id=message.message_id)


async def _edit_anchor(message: Message, state: FSMContext, text: str, reply_markup=None) -> None:
    """Edit the tracked anchor message. If no anchor — send a new one and track it."""
    data = await state.get_data()
    chat_id = data.get("admin_chat_id")
    msg_id = data.get("admin_message_id")

    if not chat_id or not msg_id:
        sent = await message.answer(text, reply_markup=reply_markup)
        await _set_anchor(state, sent)
        return

    try:
        await message.bot.edit_message_text(
            chat_id=int(chat_id),
            message_id=int(msg_id),
            text=text,
            reply_markup=reply_markup,
        )
        return
    except TelegramBadRequest as exc:
        err = str(exc).lower()
        if "message is not modified" in err:
            return
    except TelegramNetworkError:
        return

    # Fallback: try caption edit (photo message)
    try:
        await message.bot.edit_message_caption(
            chat_id=int(chat_id),
            message_id=int(msg_id),
            caption=text,
            reply_markup=reply_markup,
        )
    except (TelegramBadRequest, TelegramNetworkError):
        pass


# ─────────────────────────────────────────────────────────
# Domain helpers
# ─────────────────────────────────────────────────────────

def _days_until(dt: datetime | None) -> int | None:
    if dt is None:
        return None
    now = datetime.now(timezone.utc)
    delta = dt - now
    if delta.total_seconds() <= 0:
        return 0
    return delta.days + (1 if delta.seconds > 0 else 0)


def _days_from_balance(balance: Decimal, monthly_cost: Decimal) -> float | None:
    if monthly_cost <= 0:
        return None
    return float(balance / (monthly_cost / Decimal("30")))


def _fmt_server_card(server: TrackedServer) -> str:
    status_icon = {"online": "🟢 Online", "offline": "🔴 Offline"}.get(
        server.last_status, "⚪ Неизвестно"
    )
    days_pay = _days_until(server.next_payment_date)
    days_balance = _days_from_balance(server.account_balance, server.monthly_cost)

    days_pay_text = f"{days_pay} дн." if days_pay is not None else "—"
    days_balance_text = f"~{days_balance:.1f} дн." if days_balance is not None else "—"

    last_checked = (
        server.last_checked_at.strftime("%d.%m.%Y %H:%M")
        if server.last_checked_at
        else "не проверялся"
    )
    next_payment = (
        server.next_payment_date.strftime("%d.%m.%Y")
        if server.next_payment_date
        else "—"
    )
    provider_label = {
        "beget": "Beget",
        "yandex_cloud": "Yandex Cloud",
        "firstbyte": "FirstByte",
        "play2go": "Play2go",
        "manual": "Вручную",
    }.get(server.provider_type or "manual", server.provider_type or "—")

    return (
        f"🖥 <b>{html.escape(server.name)}</b>\n\n"
        f"IP: <code>{html.escape(server.ip)}</code>\n"
        f"Статус: <b>{status_icon}</b>\n"
        f"Последняя проверка: <b>{last_checked}</b>\n\n"
        f"Провайдер: <b>{provider_label}</b>\n"
        f"Баланс аккаунта: <b>{server.account_balance} ₽</b>\n"
        f"Стоимость/мес: <b>{server.monthly_cost} ₽</b>\n"
        f"Хватит баланса: <b>{days_balance_text}</b>\n"
        f"Следующая оплата: <b>{next_payment}</b> (через {days_pay_text})\n\n"
        f"Примечания: {html.escape(server.notes or '—')}"
    )


async def _check_server_status(panel_url: str | None, ip: str) -> str:
    urls_to_try = []
    if panel_url and panel_url.strip():
        urls_to_try.append(panel_url.strip())
    for scheme in ("https", "http"):
        urls_to_try.append(f"{scheme}://{ip}")

    timeout = aiohttp.ClientTimeout(total=_CHECK_TIMEOUT, connect=5)
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    connector = aiohttp.TCPConnector(ssl=ssl_ctx)

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        for url in urls_to_try:
            try:
                async with session.get(url, allow_redirects=True) as resp:
                    if resp.status < 600:
                        return "online"
            except Exception:
                continue

    for port in (443, 80, 22):
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(ip, port), timeout=4
            )
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return "online"
        except Exception:
            continue

    return "offline"


# ─────────────────────────────────────────────────────────
# Entry / list
# ─────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin:servers")
async def admin_servers_menu(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await _set_anchor(state, call.message)
    await edit_or_send(
        call.message,
        "🖥 <b>Мониторинг серверов</b>\n\nВыберите действие:",
        reply_markup=admin_servers_keyboard(),
    )
    await call.answer()


@router.callback_query(F.data == "admin:servers:list")
async def admin_servers_list(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await _set_anchor(state, call.message)
    servers = await ServerRepository(session).get_all()
    if not servers:
        await edit_or_send(
            call.message,
            "🖥 <b>Серверы</b>\n\nНет добавленных серверов.",
            reply_markup=admin_servers_keyboard(),
        )
        await call.answer()
        return
    await edit_or_send(
        call.message,
        f"🖥 <b>Серверы</b> ({len(servers)} шт.)\n\nВыберите сервер:",
        reply_markup=admin_server_list_keyboard(servers),
    )
    await call.answer()


# ─────────────────────────────────────────────────────────
# View single server
# ─────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("admin:server:view:"))
async def admin_server_view(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    server_id = int(call.data.split(":")[-1])
    server = await ServerRepository(session).get(server_id)
    if not server:
        await call.answer("Сервер не найден", show_alert=True)
        return
    await _set_anchor(state, call.message)
    await edit_or_send(
        call.message,
        _fmt_server_card(server),
        reply_markup=admin_server_view_keyboard(server_id),
    )
    await call.answer()


# ─────────────────────────────────────────────────────────
# Check status
# ─────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("admin:server:check:"))
async def admin_server_check(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    server_id = int(call.data.split(":")[-1])
    repo = ServerRepository(session)
    server = await repo.get(server_id)
    if not server:
        await call.answer("Сервер не найден", show_alert=True)
        return
    await _set_anchor(state, call.message)
    try:
        await call.answer("Проверяю...")
    except TelegramBadRequest:
        pass
    await edit_or_send(call.message, f"⏳ Проверяю сервер <b>{html.escape(server.name)}</b>...")

    # Also try to refresh balance from provider
    from bot.providers.hosting.factory import build_hosting_client
    client = build_hosting_client(server)
    if client is not None:
        try:
            ps = await client.fetch_status()
            if ps.balance is not None:
                server.account_balance = ps.balance
        except Exception:
            pass

    status = await _check_server_status(server.panel_url, server.ip)
    await repo.update_status(server, status)
    await edit_or_send(
        call.message,
        _fmt_server_card(server),
        reply_markup=admin_server_view_keyboard(server_id),
    )


# ─────────────────────────────────────────────────────────
# Delete server
# ─────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("admin:server:delete:confirm:"))
async def admin_server_delete_confirm(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    server_id = int(call.data.split(":")[-1])
    repo = ServerRepository(session)
    server = await repo.get(server_id)
    if not server:
        await call.answer("Сервер не найден", show_alert=True)
        return
    await repo.delete(server)
    await _set_anchor(state, call.message)
    await edit_or_send(
        call.message,
        "🗑 Сервер удалён.",
        reply_markup=admin_servers_keyboard(),
    )
    await call.answer()


@router.callback_query(F.data.startswith("admin:server:delete:"))
async def admin_server_delete_ask(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    server_id = int(call.data.split(":")[-1])
    server = await ServerRepository(session).get(server_id)
    if not server:
        await call.answer("Сервер не найден", show_alert=True)
        return
    await _set_anchor(state, call.message)
    await edit_or_send(
        call.message,
        f"Удалить сервер <b>{html.escape(server.name)}</b>?",
        reply_markup=admin_server_delete_confirm_keyboard(server_id),
    )
    await call.answer()


# ─────────────────────────────────────────────────────────
# Edit: balance
# ─────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("admin:server:edit:balance:"))
async def admin_server_edit_balance_start(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    server_id = int(call.data.split(":")[-1])
    await state.set_state(AdminTicketStates.waiting_server_edit_balance)
    await state.update_data(edit_server_id=server_id)
    await _set_anchor(state, call.message)
    await edit_or_send(
        call.message,
        "Введите новый баланс аккаунта в ₽ (например: 1500.50):",
        reply_markup=admin_back_keyboard(),
    )
    await call.answer()


@router.message(AdminTicketStates.waiting_server_edit_balance)
async def admin_server_edit_balance_save(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not _is_admin(message.from_user.id):
        await state.clear()
        return
    raw = (message.text or "").strip().replace(",", ".")
    try:
        balance = Decimal(raw)
        if balance < 0:
            raise ValueError
    except Exception:
        await _edit_anchor(message, state, "Введите неотрицательное число.", reply_markup=admin_back_keyboard())
        return
    data = await state.get_data()
    server_id = int(data.get("edit_server_id", 0))
    repo = ServerRepository(session)
    server = await repo.get(server_id)
    if not server:
        await state.clear()
        await _edit_anchor(message, state, "Сервер не найден.", reply_markup=admin_servers_keyboard())
        return
    server.account_balance = balance
    await repo.save(server)
    await state.set_state(None)
    await _edit_anchor(message, state, _fmt_server_card(server), reply_markup=admin_server_view_keyboard(server_id))


# ─────────────────────────────────────────────────────────
# Edit: payment date
# ─────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("admin:server:edit:paydate:"))
async def admin_server_edit_paydate_start(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    server_id = int(call.data.split(":")[-1])
    await state.set_state(AdminTicketStates.waiting_server_edit_payment_date)
    await state.update_data(edit_server_id=server_id)
    await _set_anchor(state, call.message)
    await edit_or_send(
        call.message,
        "Введите дату следующей оплаты в формате YYYY-MM-DD (или '-' для сброса):",
        reply_markup=admin_back_keyboard(),
    )
    await call.answer()


@router.message(AdminTicketStates.waiting_server_edit_payment_date)
async def admin_server_edit_paydate_save(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not _is_admin(message.from_user.id):
        await state.clear()
        return
    raw = (message.text or "").strip()
    if raw in {"-", "—", "none", "нет"}:
        pay_date = None
    else:
        try:
            pay_date = datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            await _edit_anchor(
                message, state,
                "Неверный формат. Введите YYYY-MM-DD или '-'.",
                reply_markup=admin_back_keyboard(),
            )
            return
    data = await state.get_data()
    server_id = int(data.get("edit_server_id", 0))
    repo = ServerRepository(session)
    server = await repo.get(server_id)
    if not server:
        await state.clear()
        await _edit_anchor(message, state, "Сервер не найден.", reply_markup=admin_servers_keyboard())
        return
    server.next_payment_date = pay_date
    await repo.save(server)
    await state.set_state(None)
    await _edit_anchor(message, state, _fmt_server_card(server), reply_markup=admin_server_view_keyboard(server_id))


# ─────────────────────────────────────────────────────────
# Edit: monthly cost
# ─────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("admin:server:edit:cost:"))
async def admin_server_edit_cost_start(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    server_id = int(call.data.split(":")[-1])
    await state.set_state(AdminTicketStates.waiting_server_edit_monthly_cost)
    await state.update_data(edit_server_id=server_id)
    await _set_anchor(state, call.message)
    await edit_or_send(
        call.message,
        "Введите стоимость сервера в месяц в ₽ (например: 800):",
        reply_markup=admin_back_keyboard(),
    )
    await call.answer()


@router.message(AdminTicketStates.waiting_server_edit_monthly_cost)
async def admin_server_edit_cost_save(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not _is_admin(message.from_user.id):
        await state.clear()
        return
    raw = (message.text or "").strip().replace(",", ".")
    try:
        cost = Decimal(raw)
        if cost < 0:
            raise ValueError
    except Exception:
        await _edit_anchor(message, state, "Введите неотрицательное число.", reply_markup=admin_back_keyboard())
        return
    data = await state.get_data()
    server_id = int(data.get("edit_server_id", 0))
    repo = ServerRepository(session)
    server = await repo.get(server_id)
    if not server:
        await state.clear()
        await _edit_anchor(message, state, "Сервер не найден.", reply_markup=admin_servers_keyboard())
        return
    server.monthly_cost = cost
    await repo.save(server)
    await state.set_state(None)
    await _edit_anchor(message, state, _fmt_server_card(server), reply_markup=admin_server_view_keyboard(server_id))


# ─────────────────────────────────────────────────────────
# Add server wizard
# ─────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin:servers:add")
async def admin_server_add_start(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await state.set_state(AdminTicketStates.waiting_server_name)
    await _set_anchor(state, call.message)
    await edit_or_send(
        call.message,
        "➕ <b>Добавление сервера</b>\n\nШаг 1/5. Введите название сервера:",
        reply_markup=admin_back_keyboard(),
    )
    await call.answer()


@router.message(AdminTicketStates.waiting_server_name)
async def admin_server_add_name(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        await state.clear()
        return
    name = (message.text or "").strip()
    if not name:
        await _edit_anchor(message, state, "Название не может быть пустым.", reply_markup=admin_back_keyboard())
        return
    await state.update_data(server_name=name)
    await state.set_state(AdminTicketStates.waiting_server_ip)
    await _edit_anchor(message, state, "Шаг 2/5. Введите IP-адрес сервера:", reply_markup=admin_back_keyboard())


@router.message(AdminTicketStates.waiting_server_ip)
async def admin_server_add_ip(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        await state.clear()
        return
    ip = (message.text or "").strip()
    if not ip:
        await _edit_anchor(message, state, "IP не может быть пустым.", reply_markup=admin_back_keyboard())
        return
    await state.update_data(server_ip=ip)
    await state.set_state(AdminTicketStates.waiting_server_panel_url)
    await _edit_anchor(
        message, state,
        "Шаг 3/5. Введите URL панели Hiddify (или '-' чтобы пропустить):",
        reply_markup=admin_back_keyboard(),
    )


@router.message(AdminTicketStates.waiting_server_panel_url)
async def admin_server_add_panel_url(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        await state.clear()
        return
    raw = (message.text or "").strip()
    panel_url = None if raw in {"-", "—", "нет"} else raw
    await state.update_data(server_panel_url=panel_url or "")
    await state.set_state(None)  # Wait for callback — provider type keyboard
    await _edit_anchor(
        message, state,
        "Шаг 4/5. Выберите тип хостинг-провайдера:",
        reply_markup=admin_server_provider_type_keyboard(),
    )


# ── Provider type selection (callback) ──────────────────

@router.callback_query(F.data.startswith("admin:server:provider:"))
async def admin_server_provider_chosen(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return

    data = await state.get_data()
    if not data.get("server_name"):
        await call.answer("Сначала начните добавление сервера.", show_alert=True)
        return

    provider_type = call.data.split(":")[-1]
    await state.update_data(server_provider_type=provider_type)
    await _set_anchor(state, call.message)

    if provider_type in _PROVIDERS_WITH_CREDENTIALS:
        login_prompt = _PROVIDERS_WITH_CREDENTIALS[provider_type][0]
        await state.set_state(AdminTicketStates.waiting_server_provider_login)
        await edit_or_send(call.message, login_prompt, reply_markup=admin_back_keyboard())
    else:
        await state.update_data(
            server_provider_login=None,
            server_provider_secret=None,
            server_provider_extra=None,
        )
        await state.set_state(AdminTicketStates.waiting_server_monthly_cost)
        await edit_or_send(
            call.message,
            "Шаг 5/5. Введите стоимость сервера в месяц в ₽ (или '0'):",
            reply_markup=admin_back_keyboard(),
        )
    await call.answer()


@router.message(AdminTicketStates.waiting_server_provider_login)
async def admin_server_provider_login_input(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        await state.clear()
        return
    login = (message.text or "").strip()
    if not login:
        await _edit_anchor(message, state, "Логин не может быть пустым.", reply_markup=admin_back_keyboard())
        return
    await state.update_data(server_provider_login=login)
    data = await state.get_data()
    provider_type = data.get("server_provider_type", "")
    password_prompt = _PROVIDERS_WITH_CREDENTIALS.get(provider_type, ("", "Введите пароль/секрет:", None))[1]
    await state.set_state(AdminTicketStates.waiting_server_provider_secret)
    await _edit_anchor(message, state, password_prompt, reply_markup=admin_back_keyboard())


@router.message(AdminTicketStates.waiting_server_provider_secret)
async def admin_server_provider_secret_input(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        await state.clear()
        return
    secret = (message.text or "").strip()
    if not secret:
        await _edit_anchor(message, state, "Пароль/секрет не может быть пустым.", reply_markup=admin_back_keyboard())
        return
    await state.update_data(server_provider_secret=secret)
    data = await state.get_data()
    provider_type = data.get("server_provider_type", "")
    extra_prompt = _PROVIDERS_WITH_CREDENTIALS.get(provider_type, ("", "", None))[2]
    if extra_prompt:
        await state.set_state(AdminTicketStates.waiting_server_provider_extra)
        await _edit_anchor(message, state, extra_prompt, reply_markup=admin_back_keyboard())
    else:
        await state.update_data(server_provider_extra=None)
        await state.set_state(AdminTicketStates.waiting_server_monthly_cost)
        await _edit_anchor(
            message, state,
            "Шаг 5/5. Введите стоимость сервера в месяц в ₽ (или '0'):",
            reply_markup=admin_back_keyboard(),
        )


@router.message(AdminTicketStates.waiting_server_provider_extra)
async def admin_server_provider_extra_input(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        await state.clear()
        return
    import json as _json
    raw = (message.text or "").strip()
    if raw in {"-", "—", "нет", ""}:
        extra_json = None
    else:
        data = await state.get_data()
        provider_type = data.get("server_provider_type", "")
        if provider_type == "yandex_cloud":
            extra_json = _json.dumps({"billing_account_id": raw})
        else:
            extra_json = raw
    await state.update_data(server_provider_extra=extra_json)
    await state.set_state(AdminTicketStates.waiting_server_monthly_cost)
    await _edit_anchor(
        message, state,
        "Шаг 5/5. Введите стоимость сервера в месяц в ₽ (или '0'):",
        reply_markup=admin_back_keyboard(),
    )


@router.message(AdminTicketStates.waiting_server_monthly_cost)
async def admin_server_add_cost(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not _is_admin(message.from_user.id):
        await state.clear()
        return
    raw = (message.text or "").strip().replace(",", ".")
    try:
        cost = Decimal(raw)
        if cost < 0:
            raise ValueError
    except Exception:
        await _edit_anchor(message, state, "Введите неотрицательное число.", reply_markup=admin_back_keyboard())
        return

    data = await state.get_data()
    if not data.get("server_name"):
        await state.clear()
        await _edit_anchor(message, state, "Сессия истекла. Начните заново.", reply_markup=admin_servers_keyboard())
        return

    server = TrackedServer(
        name=data.get("server_name", "Server"),
        ip=data.get("server_ip", ""),
        panel_url=data.get("server_panel_url") or None,
        provider_type=data.get("server_provider_type") or "manual",
        provider_login=data.get("server_provider_login") or None,
        provider_secret=data.get("server_provider_secret") or None,
        provider_extra=data.get("server_provider_extra") or None,
        monthly_cost=cost,
        account_balance=Decimal("0"),
        last_status="unknown",
    )
    repo = ServerRepository(session)
    await repo.save(server)
    await state.clear()
    await _edit_anchor(
        message, state,
        f"✅ Сервер добавлен!\n\n{_fmt_server_card(server)}",
        reply_markup=admin_server_view_keyboard(server.id),
    )


# ─────────────────────────────────────────────────────────
# Finance / Bookkeeping
# ─────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin:servers:finance")
async def admin_servers_finance(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await _set_anchor(state, call.message)

    servers = await ServerRepository(session).get_all()
    topup_ops_total, topup_sum_total = await PaymentRepository(session).topup_totals()

    total_monthly_cost = sum((s.monthly_cost for s in servers), Decimal("0"))
    total_balance = sum((s.account_balance for s in servers), Decimal("0"))

    lines = ["💹 <b>Бухгалтерия</b>\n", "━━━ Серверные расходы ━━━"]

    for server in servers:
        days_bal = _days_from_balance(server.account_balance, server.monthly_cost)
        days_pay = _days_until(server.next_payment_date)
        status_icon = {"online": "🟢", "offline": "🔴"}.get(server.last_status, "⚪")
        bal_text = f"~{days_bal:.1f} дн." if days_bal is not None else "?"
        pay_text = f"{days_pay} дн." if days_pay is not None else "?"
        lines.append(
            f"{status_icon} <b>{html.escape(server.name)}</b>\n"
            f"   💵 {server.monthly_cost} ₽/мес | "
            f"Баланс: {server.account_balance} ₽ ({bal_text}) | "
            f"До оплаты: {pay_text}"
        )

    lines += [
        "",
        "━━━ Итого расходы ━━━",
        f"Серверов: <b>{len(servers)}</b>",
        f"Расходы/мес: <b>{total_monthly_cost} ₽</b>",
        f"Баланс на счетах: <b>{total_balance} ₽</b>",
        "",
        "━━━ Доходы (всё время) ━━━",
        f"Пополнений: <b>{topup_ops_total}</b>",
        f"Сумма: <b>{topup_sum_total} ₽</b>",
    ]

    await edit_or_send(call.message, "\n".join(lines), reply_markup=admin_back_keyboard())
    await call.answer()
