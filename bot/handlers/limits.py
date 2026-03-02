from __future__ import annotations

import os
import re
from datetime import datetime, timezone

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.utils.messages import send_or_answer
from device_limit.firewall import FirewallManager

router = Router()
UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{12}$"
)


def _is_admin(user_id: int) -> bool:
    return user_id in settings.admin_id_set


def _command_args(message: Message) -> list[str]:
    raw = (message.text or "").strip()
    if not raw:
        return []
    parts = raw.split()
    return parts[1:] if len(parts) > 1 else []


def _is_uuid(value: str) -> bool:
    return bool(UUID_RE.match(value.strip()))


def _ports_from_env() -> list[int]:
    raw = os.getenv("INBOUND_PORTS", "443,8443")
    ports: list[int] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if not chunk.isdigit():
            continue
        port = int(chunk)
        if 1 <= port <= 65535:
            ports.append(port)
    return sorted(set(ports)) or [443, 8443]


def _ttl_seconds() -> int:
    raw = (os.getenv("TTL_SECONDS") or "120").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return 120


def _format_dt(value: datetime | None) -> str:
    if not value:
        return "—"
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


async def _resolve_user_row(session: AsyncSession, target: str) -> dict | None:
    target = target.strip()
    if target.isdigit():
        result = await session.execute(
            text(
                """
                SELECT id, subscription_uuid, max_devices
                FROM users
                WHERE id = :id
                """
            ),
            {"id": int(target)},
        )
        row = result.mappings().first()
        return dict(row) if row else None

    if not _is_uuid(target):
        return None

    result = await session.execute(
        text(
            """
            SELECT id, subscription_uuid, max_devices
            FROM users
            WHERE subscription_uuid = :uuid
            """
        ),
        {"uuid": target.lower()},
    )
    row = result.mappings().first()
    return dict(row) if row else None


@router.message(Command("setlimit"))
async def set_limit(message: Message, session: AsyncSession) -> None:
    if not _is_admin(message.from_user.id):
        await send_or_answer(message, "Доступ запрещен.")
        return

    args = _command_args(message)
    if len(args) != 2:
        await send_or_answer(message, "Использование: /setlimit <uuid|user_id> <n>")
        return

    target, value = args
    if not value.isdigit() or int(value) < 1:
        await send_or_answer(message, "n должен быть целым числом >= 1")
        return

    row = await _resolve_user_row(session, target)
    if not row:
        await send_or_answer(message, "Пользователь не найден по переданному uuid/user_id")
        return

    limit = int(value)
    await session.execute(
        text("UPDATE users SET max_devices = :limit WHERE id = :id"),
        {"limit": limit, "id": row["id"]},
    )
    await session.commit()
    await send_or_answer(
        message,
        (
            "Лимит обновлен\n"
            f"user_id: <code>{row['id']}</code>\n"
            f"uuid: <code>{row.get('subscription_uuid') or '—'}</code>\n"
            f"max_devices: <b>{limit}</b>"
        ),
    )


@router.message(Command("getlimit"))
async def get_limit(message: Message, session: AsyncSession) -> None:
    if not _is_admin(message.from_user.id):
        await send_or_answer(message, "Доступ запрещен.")
        return

    args = _command_args(message)
    if len(args) != 1:
        await send_or_answer(message, "Использование: /getlimit <uuid|user_id>")
        return

    row = await _resolve_user_row(session, args[0])
    if not row:
        await send_or_answer(message, "Пользователь не найден по переданному uuid/user_id")
        return

    await send_or_answer(
        message,
        (
            "Текущий лимит устройств\n"
            f"user_id: <code>{row['id']}</code>\n"
            f"uuid: <code>{row.get('subscription_uuid') or '—'}</code>\n"
            f"max_devices: <b>{row.get('max_devices') if row.get('max_devices') is not None else 2}</b>"
        ),
    )


@router.message(Command("whoonline"))
async def who_online(message: Message, session: AsyncSession) -> None:
    if not _is_admin(message.from_user.id):
        await send_or_answer(message, "Доступ запрещен.")
        return

    args = _command_args(message)
    if len(args) != 1:
        await send_or_answer(message, "Использование: /whoonline <uuid|user_id>")
        return

    row = await _resolve_user_row(session, args[0])
    if not row:
        await send_or_answer(message, "Пользователь не найден по переданному uuid/user_id")
        return

    uuid_value = row.get("subscription_uuid")
    if not uuid_value:
        await send_or_answer(message, "Для пользователя не задан subscription_uuid")
        return

    ttl = _ttl_seconds()
    result = await session.execute(
        text(
            """
            SELECT client_ip, last_seen, blocked_until
            FROM device_sessions
            WHERE uuid = :uuid
              AND last_seen >= (CURRENT_TIMESTAMP - (:ttl * interval '1 second'))
            ORDER BY last_seen DESC
            """
        ),
        {"uuid": uuid_value, "ttl": ttl},
    )
    rows = list(result.mappings().all())

    if not rows:
        await send_or_answer(
            message,
            (
                "Сейчас нет активных устройств\n"
                f"user_id: <code>{row['id']}</code>\n"
                f"uuid: <code>{uuid_value}</code>\n"
                f"ttl: <b>{ttl} сек</b>"
            ),
        )
        return

    lines = [
        "Активные устройства",
        f"user_id: <code>{row['id']}</code>",
        f"uuid: <code>{uuid_value}</code>",
        f"ttl: <b>{ttl} сек</b>",
        "",
    ]
    now = datetime.now(timezone.utc)
    for entry in rows:
        last_seen = entry["last_seen"]
        blocked_until = entry.get("blocked_until")
        ago = max(0, int((now - last_seen).total_seconds())) if last_seen else -1
        blocked_text = (
            blocked_until.strftime("%Y-%m-%d %H:%M:%S %Z")
            if blocked_until and blocked_until > now
            else "no"
        )
        lines.append(
            f"• <code>{entry['client_ip']}</code> | last_seen={last_seen} | {ago}s ago | blocked_until={blocked_text}"
        )

    await send_or_answer(message, "\n".join(lines))


@router.message(Command("uidinfo"))
async def uid_info(message: Message, session: AsyncSession) -> None:
    if not _is_admin(message.from_user.id):
        await send_or_answer(message, "Доступ запрещен.")
        return

    args = _command_args(message)
    if len(args) != 1:
        await send_or_answer(message, "Использование: /uidinfo <uuid|user_id>")
        return

    row = await _resolve_user_row(session, args[0])
    if not row:
        await send_or_answer(message, "Пользователь не найден по переданному uuid/user_id")
        return

    uuid_value = row.get("subscription_uuid")
    if not uuid_value:
        await send_or_answer(
            message,
            (
                "UUID не задан\n"
                f"user_id: <code>{row['id']}</code>\n"
                "Для этого пользователя еще нет subscription_uuid."
            ),
        )
        return

    ttl = _ttl_seconds()
    now = datetime.now(timezone.utc)
    max_devices = row.get("max_devices") if row.get("max_devices") is not None else 2

    active_result = await session.execute(
        text(
            """
            SELECT client_ip, last_seen, blocked_until
            FROM device_sessions
            WHERE uuid = :uuid
              AND last_seen >= (CURRENT_TIMESTAMP - (:ttl * interval '1 second'))
            ORDER BY last_seen DESC
            """
        ),
        {"uuid": uuid_value, "ttl": ttl},
    )
    active_rows = list(active_result.mappings().all())

    blocked_result = await session.execute(
        text(
            """
            SELECT client_ip, last_seen, blocked_until
            FROM device_sessions
            WHERE uuid = :uuid
              AND blocked_until IS NOT NULL
            ORDER BY blocked_until DESC NULLS LAST, last_seen DESC
            LIMIT 20
            """
        ),
        {"uuid": uuid_value},
    )
    blocked_rows = list(blocked_result.mappings().all())

    total_result = await session.execute(
        text(
            """
            SELECT COUNT(*) AS total_sessions,
                   COUNT(*) FILTER (WHERE blocked_until IS NOT NULL) AS blocked_total
            FROM device_sessions
            WHERE uuid = :uuid
            """
        ),
        {"uuid": uuid_value},
    )
    totals = total_result.mappings().first() or {"total_sessions": 0, "blocked_total": 0}

    active_blocked_count = sum(
        1
        for r in active_rows
        if r.get("blocked_until") is not None and r["blocked_until"] > now
    )

    lines = [
        "Полная информация по пользователю",
        f"user_id: <code>{row['id']}</code>",
        f"uuid: <code>{uuid_value}</code>",
        f"max_devices: <b>{max_devices}</b>",
        f"ttl_window: <b>{ttl} сек</b>",
        f"active_now: <b>{len(active_rows)}</b>",
        f"blocked_now: <b>{active_blocked_count}</b>",
        f"sessions_total_saved: <b>{totals['total_sessions']}</b>",
        f"blocked_total_saved: <b>{totals['blocked_total']}</b>",
        "",
        "Активные IP:",
    ]

    if not active_rows:
        lines.append("• нет активных устройств")
    else:
        for entry in active_rows[:20]:
            block_state = "blocked" if entry.get("blocked_until") and entry["blocked_until"] > now else "ok"
            lines.append(
                f"• <code>{entry['client_ip']}</code> | last_seen={_format_dt(entry.get('last_seen'))} | state={block_state} | blocked_until={_format_dt(entry.get('blocked_until'))}"
            )

    lines.extend(["", "Последние блокировки:"])
    if not blocked_rows:
        lines.append("• блокировок не было")
    else:
        for entry in blocked_rows:
            is_active = entry.get("blocked_until") and entry["blocked_until"] > now
            lines.append(
                f"• <code>{entry['client_ip']}</code> | blocked_until={_format_dt(entry.get('blocked_until'))} | {'ACTIVE' if is_active else 'expired'} | last_seen={_format_dt(entry.get('last_seen'))}"
            )

    lines.extend(
        [
            "",
            "Что видит пользователь при блокировке:",
            "• Подключение не устанавливается с нового устройства (таймаут/ошибка сети в клиенте).",
            "• В подписке отдельной серверной строки 'лимит исчерпан' обычно нет, это поведение на уровне Xray/firewall.",
            "• Чтобы пустить снова: /resetdevices <uuid|user_id> или дождаться истечения TTL.",
        ]
    )

    await send_or_answer(message, "\n".join(lines))


@router.message(Command("resetdevices"))
async def reset_devices(message: Message, session: AsyncSession) -> None:
    if not _is_admin(message.from_user.id):
        await send_or_answer(message, "Доступ запрещен.")
        return

    args = _command_args(message)
    if len(args) != 1:
        await send_or_answer(message, "Использование: /resetdevices <uuid|user_id>")
        return

    row = await _resolve_user_row(session, args[0])
    if not row:
        await send_or_answer(message, "Пользователь не найден по переданному uuid/user_id")
        return

    uuid_value = row.get("subscription_uuid")
    if not uuid_value:
        await send_or_answer(message, "Для пользователя не задан subscription_uuid")
        return

    blocked_rows = await session.execute(
        text(
            """
            SELECT DISTINCT client_ip
            FROM device_sessions
            WHERE uuid = :uuid
              AND blocked_until IS NOT NULL
            """
        ),
        {"uuid": uuid_value},
    )
    blocked_ips = [str(item[0]) for item in blocked_rows.all()]

    await session.execute(text("DELETE FROM device_sessions WHERE uuid = :uuid"), {"uuid": uuid_value})
    await session.commit()

    ports = _ports_from_env()
    firewall_result = "ok"
    if blocked_ips:
        fw = FirewallManager()
        failed: list[str] = []
        for ip in blocked_ips:
            try:
                fw.remove_block(ip=ip, ports=ports)
            except Exception:
                failed.append(ip)
        if failed:
            firewall_result = f"partial ({len(failed)} failed)"

    await send_or_answer(
        message,
        (
            "Сессии сброшены\n"
            f"user_id: <code>{row['id']}</code>\n"
            f"uuid: <code>{uuid_value}</code>\n"
            f"removed_sessions: <b>{len(blocked_ips)}</b>\n"
            f"firewall_unblock: <b>{firewall_result}</b>"
        ),
    )
