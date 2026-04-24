"""Background server monitoring service.

Runs an infinite loop every 30 minutes:
- Fetches all active TrackedServers from DB
- Checks HTTP reachability
- Fetches balance from hosting provider API (if configured)
- Updates server.last_status, server.last_checked_at, server.account_balance
- Sends Telegram alert to all admins when days_remaining <= 2
"""
from __future__ import annotations

import asyncio
import logging
import ssl
from datetime import datetime, timezone
from decimal import Decimal

import aiohttp

from bot.config import settings
from bot.providers.hosting.factory import build_hosting_client

logger = logging.getLogger(__name__)

_CHECK_TIMEOUT = 8
_MONITOR_INTERVAL_SECONDS = 30 * 60  # 30 minutes
_ALERT_COOLDOWN_SECONDS = 12 * 60 * 60  # 12 hours between alerts per server

# In-memory cooldown: server_id → datetime of last alert sent
_last_alert_sent: dict[int, datetime] = {}


async def _check_server_status(ip: str) -> str:
    """Returns 'online' or 'offline' by attempting HTTP connections and TCP fallback."""
    urls_to_try: list[str] = []
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

    # Fallback: TCP ping on common ports
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


def _days_remaining(balance: Decimal, monthly_cost: Decimal) -> float | None:
    """Return how many days the balance will last at current daily burn rate.

    Returns None if inputs are invalid or daily cost is zero.
    """
    if monthly_cost <= 0:
        return None
    daily_cost = monthly_cost / Decimal("30")
    if daily_cost <= 0:
        return None
    return float(balance / daily_cost)


async def _send_alert(bot, server_name: str, balance: Decimal, days: float | None) -> None:
    admin_ids = settings.admin_id_set
    if not admin_ids:
        logger.warning("No admin IDs configured — cannot send server alerts.")
        return

    if days is not None and days < 0:
        days_text = f"<b>баланс отрицательный</b> ({balance:.2f} ₽)"
    elif days is not None:
        days_text = f"осталось <b>{days:.1f} дн.</b> (баланс {balance:.2f} ₽)"
    else:
        days_text = f"баланс {balance:.2f} ₽, стоимость неизвестна"

    text = (
        f"⚠️ <b>Внимание: сервер {server_name}</b>\n\n"
        f"Баланс хостинг-аккаунта скоро закончится:\n"
        f"{days_text}\n\n"
        f"Пожалуйста, пополните баланс провайдера."
    )

    for admin_id in admin_ids:
        try:
            await bot.send_message(admin_id, text)
        except Exception as exc:
            logger.warning("Failed to send alert to admin %s: %s", admin_id, exc)


def _account_key(server) -> tuple | None:
    """Hashable key identifying the hosting account (mirrors admin_servers logic)."""
    provider = (server.provider_type or "manual").lower()
    if provider in ("manual", "firstbyte"):
        return None
    if provider == "play2go":
        key_field = server.provider_secret
    else:
        key_field = server.provider_login
    if not key_field:
        return None
    return (provider, key_field)


def _account_siblings(server, all_servers: list) -> list:
    """Return other active servers sharing the same provider account."""
    key = _account_key(server)
    if key is None:
        return []
    return [
        s for s in all_servers
        if s.id != server.id
        and s.is_active
        and _account_key(s) == key
    ]


async def _check_one_server(bot, session, server, all_servers: list) -> None:
    """Run full check for a single server: status + provider balance + alert."""
    from bot.repositories.server import ServerRepository

    repo = ServerRepository(session)
    siblings = _account_siblings(server, all_servers)

    # 1. HTTP / TCP status check
    try:
        status = await _check_server_status(server.ip)
    except Exception as exc:
        logger.warning("Status check failed for server %s (id=%s): %s", server.name, server.id, exc)
        status = "offline"

    server.last_status = status
    server.last_checked_at = datetime.now(timezone.utc)

    # 2. Hosting provider balance — propagate to all servers on the same account
    client = build_hosting_client(server)
    if client is not None:
        try:
            provider_status = await client.fetch_status()
            if provider_status.balance is not None:
                server.account_balance = provider_status.balance
                logger.info(
                    "Server %s (id=%s): balance updated to %s",
                    server.name,
                    server.id,
                    provider_status.balance,
                )
                for sibling in siblings:
                    sibling.account_balance = provider_status.balance
                    await repo.save(sibling)
                    logger.info(
                        "Server %s (id=%s): balance propagated from account sibling %s",
                        sibling.name,
                        sibling.id,
                        server.name,
                    )
        except ValueError as exc:
            # e.g. Yandex Cloud not configured
            logger.info("Server %s (id=%s): provider skipped — %s", server.name, server.id, exc)
        except Exception as exc:
            logger.warning(
                "Provider API failed for server %s (id=%s): %s", server.name, server.id, exc
            )

    await repo.save(server)

    # 3. Alert check — use total monthly cost across all servers on the same account
    balance = server.account_balance
    total_monthly_cost = server.monthly_cost + sum(s.monthly_cost for s in siblings)
    days = _days_remaining(balance, total_monthly_cost)

    # If next_payment_date is set and still in the future, server is paid — skip alert
    now = datetime.now(timezone.utc)
    if server.next_payment_date is not None:
        npd = server.next_payment_date
        if npd.tzinfo is None:
            npd = npd.replace(tzinfo=timezone.utc)
        if npd > now:
            logger.debug(
                "Server %s (id=%s): alert suppressed (next_payment_date=%s is in the future)",
                server.name,
                server.id,
                npd.date(),
            )
            return

    should_alert = False
    if days is not None and days <= 2:
        should_alert = True
    elif balance is not None and balance < 0:
        should_alert = True

    if should_alert:
        last_sent = _last_alert_sent.get(server.id)
        if last_sent and (now - last_sent).total_seconds() < _ALERT_COOLDOWN_SECONDS:
            logger.debug(
                "Server %s (id=%s): alert suppressed (cooldown, last sent %s ago)",
                server.name,
                server.id,
                now - last_sent,
            )
        else:
            logger.warning(
                "Server %s (id=%s): low balance alert (days_remaining=%s, balance=%s)",
                server.name,
                server.id,
                days,
                balance,
            )
            _last_alert_sent[server.id] = now
            await _send_alert(bot, server.name, balance, days)


async def _run_monitoring_cycle(bot, session_factory) -> None:
    """Execute one full monitoring cycle over all active servers."""
    from bot.repositories.server import ServerRepository
    from sqlalchemy import select
    from bot.db.models import TrackedServer

    async with session_factory() as session:
        result = await session.execute(
            select(TrackedServer).where(TrackedServer.is_active.is_(True))
        )
        servers = list(result.scalars().all())

    if not servers:
        logger.debug("Server monitor: no active servers to check.")
        return

    logger.info("Server monitor: checking %d server(s)...", len(servers))

    for server in servers:
        async with session_factory() as session:
            # Reload server and full list in fresh session for each iteration
            from sqlalchemy import select as sa_select
            from bot.db.models import TrackedServer as TS
            result = await session.execute(sa_select(TS).where(TS.id == server.id))
            fresh_server = result.scalar_one_or_none()
            if fresh_server is None:
                continue
            all_result = await session.execute(sa_select(TS).where(TS.is_active.is_(True)))
            all_active = list(all_result.scalars().all())
            try:
                await _check_one_server(bot, session, fresh_server, all_active)
                await session.commit()
            except Exception as exc:
                logger.error(
                    "Error monitoring server %s (id=%s): %s",
                    server.name,
                    server.id,
                    exc,
                    exc_info=True,
                )
                await session.rollback()

    logger.info("Server monitor: cycle complete.")


async def run_server_monitor(bot, session_factory) -> None:
    """Runs forever, checks all servers every 30 minutes.

    Usage (in main.py after bot/dp setup):
        asyncio.create_task(run_server_monitor(bot, AsyncSessionFactory))
    """
    logger.info("Server monitor started (interval=%ds).", _MONITOR_INTERVAL_SECONDS)
    while True:
        await asyncio.sleep(_MONITOR_INTERVAL_SECONDS)
        try:
            await _run_monitoring_cycle(bot, session_factory)
        except Exception as exc:
            logger.error("Server monitor cycle error: %s", exc, exc_info=True)
