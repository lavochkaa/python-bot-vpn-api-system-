"""Admin server monitoring handlers."""
import asyncio
import html
from datetime import datetime, timezone
from decimal import Decimal

import aiohttp
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.db.models import TrackedServer
from bot.keyboards.admin import (
    admin_back_keyboard,
    admin_server_delete_confirm_keyboard,
    admin_server_list_keyboard,
    admin_server_view_keyboard,
    admin_servers_keyboard,
)
from bot.repositories.payment import PaymentRepository
from bot.repositories.server import ServerRepository
from bot.states.admin import AdminTicketStates
from bot.utils.messages import edit_or_send

router = Router()

_CHECK_TIMEOUT = 8


def _is_admin(user_id: int) -> bool:
    return user_id in settings.admin_id_set


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def _days_until(dt: datetime | None) -> int | None:
    if dt is None:
        return None
    now = datetime.now(timezone.utc)
    delta = dt - now
    if delta.total_seconds() <= 0:
        return 0
    return delta.days + (1 if delta.seconds > 0 else 0)


def _fmt_server_card(server: TrackedServer) -> str:
    status_icon = {"online": "🟢 Online", "offline": "🔴 Offline"}.get(
        server.last_status, "⚪ Неизвестно"
    )
    days_left = _days_until(server.next_payment_date)
    days_text = f"{days_left} дн." if days_left is not None else "—"
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
    return (
        f"🖥 <b>{html.escape(server.name)}</b>\n\n"
        f"IP: <code>{html.escape(server.ip)}</code>\n"
        f"Статус: <b>{status_icon}</b>\n"
        f"Последняя проверка: <b>{last_checked}</b>\n\n"
        f"Провайдер: <b>{html.escape(server.provider_name or '—')}</b>\n"
        f"Баланс аккаунта: <b>{server.account_balance} ₽</b>\n"
        f"Стоимость/мес: <b>{server.monthly_cost} ₽</b>\n"
        f"Следующая оплата: <b>{next_payment}</b> ({days_text})\n\n"
        f"Примечания: {html.escape(server.notes or '—')}"
    )


async def _check_server_status(panel_url: str | None, ip: str) -> str:
    """Returns 'online' or 'offline'."""
    urls_to_try = []
    if panel_url and panel_url.strip():
        urls_to_try.append(panel_url.strip())
    for scheme in ("https", "http"):
        urls_to_try.append(f"{scheme}://{ip}")

    timeout = aiohttp.ClientTimeout(total=_CHECK_TIMEOUT, connect=5)
    import ssl
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

    # Fallback: ICMP-like TCP ping on port 443 and 80
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


# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────

@router.callback_query(F.data == "admin:servers")
async def admin_servers_menu(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await edit_or_send(
        call.message,
        "🖥 <b>Мониторинг серверов</b>\n\nВыберите действие:",
        reply_markup=admin_servers_keyboard(),
    )
    await call.answer()


# ──────────────────────────────────────────────
# Server list
# ──────────────────────────────────────────────

@router.callback_query(F.data == "admin:servers:list")
async def admin_servers_list(call: CallbackQuery, session: AsyncSession) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
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


# ──────────────────────────────────────────────
# View server
# ──────────────────────────────────────────────

@router.callback_query(F.data.startswith("admin:server:view:"))
async def admin_server_view(call: CallbackQuery, session: AsyncSession) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    server_id = int(call.data.split(":")[-1])
    server = await ServerRepository(session).get(server_id)
    if not server:
        await call.answer("Сервер не найден", show_alert=True)
        return
    await edit_or_send(
        call.message,
        _fmt_server_card(server),
        reply_markup=admin_server_view_keyboard(server_id),
    )
    await call.answer()


# ──────────────────────────────────────────────
# Check status
# ──────────────────────────────────────────────

@router.callback_query(F.data.startswith("admin:server:check:"))
async def admin_server_check(call: CallbackQuery, session: AsyncSession) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    server_id = int(call.data.split(":")[-1])
    repo = ServerRepository(session)
    server = await repo.get(server_id)
    if not server:
        await call.answer("Сервер не найден", show_alert=True)
        return
    try:
        await call.answer("Проверяю статус...")
    except TelegramBadRequest:
        pass
    await edit_or_send(call.message, f"⏳ Проверяю сервер <b>{html.escape(server.name)}</b>...")
    status = await _check_server_status(server.panel_url, server.ip)
    await repo.update_status(server, status)
    await edit_or_send(
        call.message,
        _fmt_server_card(server),
        reply_markup=admin_server_view_keyboard(server_id),
    )


# ──────────────────────────────────────────────
# Delete server
# ──────────────────────────────────────────────

@router.callback_query(F.data.startswith("admin:server:delete:confirm:"))
async def admin_server_delete_confirm(call: CallbackQuery, session: AsyncSession) -> None:
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
    await edit_or_send(
        call.message,
        "🗑 Сервер удалён.",
        reply_markup=admin_servers_keyboard(),
    )
    await call.answer()


@router.callback_query(F.data.startswith("admin:server:delete:"))
async def admin_server_delete_ask(call: CallbackQuery, session: AsyncSession) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    server_id = int(call.data.split(":")[-1])
    server = await ServerRepository(session).get(server_id)
    if not server:
        await call.answer("Сервер не найден", show_alert=True)
        return
    await edit_or_send(
        call.message,
        f"Удалить сервер <b>{html.escape(server.name)}</b>?",
        reply_markup=admin_server_delete_confirm_keyboard(server_id),
    )
    await call.answer()


# ──────────────────────────────────────────────
# Edit: balance
# ──────────────────────────────────────────────

@router.callback_query(F.data.startswith("admin:server:edit:balance:"))
async def admin_server_edit_balance_start(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    server_id = int(call.data.split(":")[-1])
    await state.set_state(AdminTicketStates.waiting_server_edit_balance)
    await state.update_data(edit_server_id=server_id)
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
        await message.answer("Введите неотрицательное число.", reply_markup=admin_back_keyboard())
        return
    data = await state.get_data()
    server_id = int(data.get("edit_server_id", 0))
    repo = ServerRepository(session)
    server = await repo.get(server_id)
    if not server:
        await state.clear()
        await message.answer("Сервер не найден.")
        return
    server.account_balance = balance
    await repo.save(server)
    await state.clear()
    await message.answer(
        _fmt_server_card(server),
        reply_markup=admin_server_view_keyboard(server_id),
    )


# ──────────────────────────────────────────────
# Edit: payment date
# ──────────────────────────────────────────────

@router.callback_query(F.data.startswith("admin:server:edit:paydate:"))
async def admin_server_edit_paydate_start(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    server_id = int(call.data.split(":")[-1])
    await state.set_state(AdminTicketStates.waiting_server_edit_payment_date)
    await state.update_data(edit_server_id=server_id)
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
            await message.answer(
                "Неверный формат даты. Введите YYYY-MM-DD или '-'.",
                reply_markup=admin_back_keyboard(),
            )
            return
    data = await state.get_data()
    server_id = int(data.get("edit_server_id", 0))
    repo = ServerRepository(session)
    server = await repo.get(server_id)
    if not server:
        await state.clear()
        await message.answer("Сервер не найден.")
        return
    server.next_payment_date = pay_date
    await repo.save(server)
    await state.clear()
    await message.answer(
        _fmt_server_card(server),
        reply_markup=admin_server_view_keyboard(server_id),
    )


# ──────────────────────────────────────────────
# Edit: monthly cost
# ──────────────────────────────────────────────

@router.callback_query(F.data.startswith("admin:server:edit:cost:"))
async def admin_server_edit_cost_start(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    server_id = int(call.data.split(":")[-1])
    await state.set_state(AdminTicketStates.waiting_server_edit_monthly_cost)
    await state.update_data(edit_server_id=server_id)
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
        await message.answer("Введите неотрицательное число.", reply_markup=admin_back_keyboard())
        return
    data = await state.get_data()
    server_id = int(data.get("edit_server_id", 0))
    repo = ServerRepository(session)
    server = await repo.get(server_id)
    if not server:
        await state.clear()
        await message.answer("Сервер не найден.")
        return
    server.monthly_cost = cost
    await repo.save(server)
    await state.clear()
    await message.answer(
        _fmt_server_card(server),
        reply_markup=admin_server_view_keyboard(server_id),
    )


# ──────────────────────────────────────────────
# Add server: multi-step wizard
# ──────────────────────────────────────────────

@router.callback_query(F.data == "admin:servers:add")
async def admin_server_add_start(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await state.set_state(AdminTicketStates.waiting_server_name)
    await edit_or_send(
        call.message,
        "➕ <b>Добавление сервера</b>\n\nШаг 1/5. Введите название сервера (например: «Main Server»):",
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
        await message.answer("Название не может быть пустым.", reply_markup=admin_back_keyboard())
        return
    await state.update_data(server_name=name)
    await state.set_state(AdminTicketStates.waiting_server_ip)
    await message.answer(
        "Шаг 2/5. Введите IP-адрес сервера:",
        reply_markup=admin_back_keyboard(),
    )


@router.message(AdminTicketStates.waiting_server_ip)
async def admin_server_add_ip(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        await state.clear()
        return
    ip = (message.text or "").strip()
    if not ip:
        await message.answer("IP не может быть пустым.", reply_markup=admin_back_keyboard())
        return
    await state.update_data(server_ip=ip)
    await state.set_state(AdminTicketStates.waiting_server_panel_url)
    await message.answer(
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
    await state.set_state(AdminTicketStates.waiting_server_provider)
    await message.answer(
        "Шаг 4/5. Введите название провайдера (например: Timeweb, Hetzner) или '-':",
        reply_markup=admin_back_keyboard(),
    )


@router.message(AdminTicketStates.waiting_server_provider)
async def admin_server_add_provider(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        await state.clear()
        return
    raw = (message.text or "").strip()
    provider = None if raw in {"-", "—", "нет"} else raw
    await state.update_data(server_provider=provider or "")
    await state.set_state(AdminTicketStates.waiting_server_monthly_cost)
    await message.answer(
        "Шаг 5/5. Введите стоимость сервера в месяц в ₽ (или '0' если неизвестно):",
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
        await message.answer("Введите неотрицательное число.", reply_markup=admin_back_keyboard())
        return

    data = await state.get_data()
    server = TrackedServer(
        name=data.get("server_name", "Server"),
        ip=data.get("server_ip", ""),
        panel_url=data.get("server_panel_url") or None,
        provider_name=data.get("server_provider") or None,
        monthly_cost=cost,
        account_balance=Decimal("0"),
        last_status="unknown",
    )
    repo = ServerRepository(session)
    await repo.save(server)
    await state.clear()
    await message.answer(
        f"✅ Сервер добавлен!\n\n{_fmt_server_card(server)}",
        reply_markup=admin_server_view_keyboard(server.id),
    )


# ──────────────────────────────────────────────
# Finance / Bookkeeping
# ──────────────────────────────────────────────

@router.callback_query(F.data == "admin:servers:finance")
async def admin_servers_finance(call: CallbackQuery, session: AsyncSession) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return

    servers = await ServerRepository(session).get_all()
    payment_repo = PaymentRepository(session)
    topup_ops_total, topup_sum_total = await payment_repo.topup_totals()

    total_monthly_cost = sum(s.monthly_cost for s in servers)
    total_balance = sum(s.account_balance for s in servers)

    lines = [
        "💹 <b>Бухгалтерия</b>\n",
        "━━━ Серверные расходы ━━━",
    ]

    for server in servers:
        days_left = _days_until(server.next_payment_date)
        days_text = f"{days_left} дн." if days_left is not None else "?"
        status_icon = {"online": "🟢", "offline": "🔴"}.get(server.last_status, "⚪")
        lines.append(
            f"{status_icon} <b>{html.escape(server.name)}</b> ({html.escape(server.ip)})\n"
            f"   Стоимость: <b>{server.monthly_cost} ₽/мес</b> | "
            f"Баланс: <b>{server.account_balance} ₽</b> | "
            f"До оплаты: <b>{days_text}</b>"
        )

    lines.append("")
    lines.append("━━━ Итого по серверам ━━━")
    lines.append(f"Серверов: <b>{len(servers)}</b>")
    lines.append(f"Расходы/мес: <b>{total_monthly_cost} ₽</b>")
    lines.append(f"Суммарный баланс аккаунтов: <b>{total_balance} ₽</b>")

    lines.append("")
    lines.append("━━━ Доходы (все время) ━━━")
    lines.append(f"Пополнений (операций): <b>{topup_ops_total}</b>")
    lines.append(f"Сумма пополнений: <b>{topup_sum_total} ₽</b>")

    await edit_or_send(
        call.message,
        "\n".join(lines),
        reply_markup=admin_back_keyboard(),
    )
    await call.answer()
