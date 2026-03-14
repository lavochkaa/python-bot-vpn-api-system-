import asyncio
import time
from datetime import datetime, timezone
from dataclasses import dataclass
from sqlalchemy.ext.asyncio import AsyncSession
from bot.db.models import User, Subscription
from bot.providers.vpn.factory import build_vpn_provider
from bot.repositories.vpn_key import VpnKeyRepository
from bot.utils.subscription_url_info import fetch_subscription_url_info, merge_usage_info


@dataclass
class MainMenuSnapshot:
    text: str
    remaining_gb: float | None
    remaining_days: int | None


_USAGE_CACHE_TTL_SECONDS = 30.0
_USAGE_CACHE: dict[tuple[int, str], tuple[float, dict | None]] = {}


def _usage_cache_key(user: User, sub: Subscription) -> tuple[int, str]:
    provider_ref = (
        sub.provider_subscription_id
        or user.subscription_uuid
        or f"sub:{sub.id}"
    )
    return user.id, str(provider_ref)


def _get_cached_usage(user: User, sub: Subscription) -> dict | None:
    key = _usage_cache_key(user, sub)
    cached = _USAGE_CACHE.get(key)
    if not cached:
        return None
    expires_at, payload = cached
    if expires_at <= time.monotonic():
        _USAGE_CACHE.pop(key, None)
        return None
    return payload


def _store_cached_usage(user: User, sub: Subscription, usage_info: dict | None) -> None:
    _USAGE_CACHE[_usage_cache_key(user, sub)] = (
        time.monotonic() + _USAGE_CACHE_TTL_SECONDS,
        usage_info,
    )


def invalidate_usage_cache(user: User, sub: Subscription | None) -> None:
    if sub is None:
        return
    _USAGE_CACHE.pop(_usage_cache_key(user, sub), None)


async def format_main_menu(user: User, session: AsyncSession) -> str:
    snapshot = await build_main_menu_snapshot(user, session)
    return snapshot.text


async def build_main_menu_snapshot(
    user: User,
    session: AsyncSession,
    *,
    include_live_usage: bool = True,
    usage_timeout_seconds: float = 2.0,
) -> MainMenuSnapshot:
    from bot.repositories.subscription import SubscriptionRepository

    sub = await SubscriptionRepository(session).get_active(user.id)
    usage_info: dict | None = None
    if sub and include_live_usage:
        usage_info = _get_cached_usage(user, sub)
        if usage_info is None:
            provider = build_vpn_provider()
            try:
                api_usage_info = await asyncio.wait_for(
                    provider.get_user_usage(
                        user_id=user.id,
                        subscription_uuid=user.subscription_uuid,
                        provider_subscription_id=sub.provider_subscription_id,
                    ),
                    timeout=usage_timeout_seconds,
                )
            except Exception:
                api_usage_info = None
            try:
                keys = await VpnKeyRepository(session).get_user_keys(user.id, limit=1)
                sub_url = keys[0].key if keys else None
                sub_url_info = await asyncio.wait_for(
                    fetch_subscription_url_info(sub_url, timeout_seconds=usage_timeout_seconds),
                    timeout=usage_timeout_seconds + 0.5,
                )
            except Exception:
                sub_url_info = None
            usage_info = merge_usage_info(api_usage_info, sub_url_info)
            _store_cached_usage(user, sub, usage_info)
    sub_info, remaining_gb, remaining_days = await format_subscription_for_user(
        sub,
        show_type=False,
        usage_info=usage_info,
    )
    text = (
        f"👋 Привет, <b>{user.full_name or 'пользователь'}</b>!\n\n"
        f"💳 Баланс: <b>{user.balance} ₽</b>\n\n"
        f"{sub_info}"
    )
    return MainMenuSnapshot(text=text, remaining_gb=remaining_gb, remaining_days=remaining_days)


async def format_subscription_for_user(
    sub: Subscription | None,
    show_type: bool = False,
    usage_info: dict | None = None,
) -> tuple[str, float | None, int | None]:
    if not sub:
        return "📦 Тариф: <b>none</b>\nСтатус: ❌ none\nДействует до: <b>—</b>", None, None

    now = datetime.now(timezone.utc)
    expires = usage_info.get("expire_at") if usage_info else None
    if expires is None:
        expires = sub.expires_at
    is_active = sub.is_active and expires is not None and expires > now
    status = "✅ активна" if is_active else "❌ истекла"
    status_code = "active" if is_active else "expired"
    expires_str = expires.strftime("%d.%m.%Y") if expires else "—"
    remaining_text = "истекла"
    if is_active and expires is not None:
        delta = expires - now
        remaining_days = delta.days
        if delta.seconds > 0 or delta.microseconds > 0:
            remaining_days += 1
        remaining_text = f"осталось {max(0, remaining_days)} дн."
    else:
        remaining_days = None
    plan_name = sub.plan.name if sub.plan else f"ID #{sub.plan_id}"
    total_gb = float(sub.traffic_gb) if sub.traffic_gb else None
    used_gb = None
    if usage_info:
        used_gb = usage_info.get("current_usage_gb")
        provider_total = usage_info.get("usage_limit_gb")
        if total_gb is None and provider_total is not None:
            total_gb = float(provider_total)
    if total_gb is not None and used_gb is not None:
        remaining_gb = max(total_gb - float(used_gb), 0.0)
        total_title = int(total_gb) if float(total_gb).is_integer() else round(total_gb, 2)
        used_title = round(float(used_gb), 2)
        remaining_title = round(float(remaining_gb), 2)
        traffic_title = f"{total_title} ГБ (исп: {used_title} ГБ, ост: {remaining_title} ГБ)"
    elif total_gb is not None:
        remaining_gb = None
        traffic_title = f"{int(total_gb) if float(total_gb).is_integer() else round(total_gb, 2)} ГБ"
    elif used_gb is not None:
        remaining_gb = None
        traffic_title = f"— (исп: {round(float(used_gb), 2)} ГБ)"
    else:
        remaining_gb = None
        traffic_title = "—"
    duration_title = f"{sub.duration_days} дн." if sub.duration_days else "—"
    lines = [
        f"📦 Тариф: <b>{plan_name}</b>\n"
        f"Срок: <b>{duration_title}</b>\n"
        f"Трафик: <b>{traffic_title}</b>\n"
        f"Статус: {status} ({status_code})\n"
        f"Действует до: <b>{expires_str}</b> ({remaining_text})"
    ]
    if show_type:
        plan_type_title = {"phone": "PHONE", "pc": "PC"}.get((sub.plan_type or "").lower(), "—")
        lines.insert(1, f"Тип: <b>{plan_type_title}</b>\n")
    return "".join(lines), remaining_gb, remaining_days


async def format_subscription_info(sub: Subscription | None) -> str:
    """Backward-compatible wrapper."""
    text, _, _ = await format_subscription_for_user(sub, show_type=False)
    return text
