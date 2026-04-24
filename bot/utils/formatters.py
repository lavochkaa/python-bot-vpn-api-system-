import asyncio
import time
import re
from datetime import datetime, timezone
from dataclasses import dataclass

import aiohttp
from sqlalchemy.ext.asyncio import AsyncSession
from bot.config import settings
from bot.db.models import User, Subscription
from bot.providers.vpn.factory import build_vpn_provider


@dataclass
class MainMenuSnapshot:
    text: str
    remaining_gb: float | None
    remaining_days: int | None


def _format_gb(value: float | None) -> str:
    if value is None:
        return "—"
    precision = 2 if 0 < abs(value) < 1 else 1
    return f"{value:.{precision}f}".rstrip("0").rstrip(".")


def _resolve_device_count(usage_info: dict | None) -> int | None:
    if not usage_info:
        return None

    raw_count = usage_info.get("connected_devices_count")
    try:
        connected_count = int(raw_count) if raw_count is not None else None
    except (TypeError, ValueError):
        connected_count = None
    return connected_count


def _escape_html(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _resolve_connected_devices(usage_info: dict | None) -> list[str]:
    if not usage_info:
        return []

    raw_devices = usage_info.get("connected_devices")
    if not isinstance(raw_devices, list):
        return []

    result: list[str] = []
    seen: set[str] = set()
    for item in raw_devices:
        label = str(item or "").strip()
        if not label or label in seen:
            continue
        seen.add(label)
        result.append(label)
    return result


def _device_emoji(label: str) -> str:
    low = str(label or "").lower()
    if "mac" in low or "ios" in low or "iphone" in low or "ipad" in low:
        return "🍎"
    if "android" in low:
        return "🤖"
    if "windows" in low:
        return "🪟"
    if "linux" in low or "ubuntu" in low or "debian" in low:
        return "🐧"
    return "📱"


def _build_main_menu_subscription_block(sub: Subscription, usage_info: dict | None) -> tuple[str, float | None, int | None]:
    now = datetime.now(timezone.utc)
    expires = usage_info.get("expire_at") if usage_info else None
    if expires is None:
        expires = sub.expires_at

    remaining_days = None
    if expires and expires > now:
        delta = expires - now
        remaining_days = delta.days + (1 if delta.seconds > 0 or delta.microseconds > 0 else 0)

    used_gb = usage_info.get("current_usage_gb") if usage_info else None
    total_gb = usage_info.get("usage_limit_gb") if usage_info and usage_info.get("usage_limit_gb") is not None else None
    if total_gb is None and sub.traffic_gb is not None:
        total_gb = float(sub.traffic_gb)
    remaining_gb = None
    if total_gb is not None and used_gb is not None:
        remaining_gb = max(float(total_gb) - float(used_gb), 0.0)

    device_limit = None
    if usage_info:
        raw_limit = usage_info.get("device_limit")
        if raw_limit is not None:
            try:
                device_limit = int(raw_limit)
            except (TypeError, ValueError):
                device_limit = None
    if device_limit is None:
        device_limit = max(0, int(settings.vpn_api_hwid_device_limit or 3))

    connected_count = _resolve_device_count(usage_info)
    connected_devices = _resolve_connected_devices(usage_info)
    devices_title = f"{connected_count if connected_count is not None else '—'} / {device_limit}"
    devices_block = ""
    if connected_devices:
        device_lines = [f"{_device_emoji(device)} {device}" for device in connected_devices]
        devices_pre = "📱 Подключенные устройства:\n" + "\n".join(device_lines)
        devices_block = (
            f"\n<pre>{_escape_html(devices_pre)}</pre>"
        )

    expires_title = expires.strftime("%d.%m.%Y %H:%M") if expires else "—"
    text = (
        "📦 <b>Текущая подписка</b>\n\n"
        f"Трафик: <b>{_format_gb(used_gb)} / {_format_gb(total_gb)} ГБ</b>\n"
        f"Действует до: <b>{expires_title}</b>\n"
        f"Устройства: <b>{devices_title}</b>"
        f"{devices_block}"
    )
    return text, remaining_gb, remaining_days


_USAGE_CACHE_TTL_SECONDS = 30.0
_USAGE_CACHE_MAX_SIZE = 500
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
    if len(_USAGE_CACHE) >= _USAGE_CACHE_MAX_SIZE:
        # Evict oldest entries
        now = time.monotonic()
        expired = [k for k, (exp, _) in _USAGE_CACHE.items() if exp <= now]
        for k in expired:
            _USAGE_CACHE.pop(k, None)
        # If still over limit, remove oldest by expiry time
        if len(_USAGE_CACHE) >= _USAGE_CACHE_MAX_SIZE:
            oldest = sorted(_USAGE_CACHE, key=lambda k: _USAGE_CACHE[k][0])
            for k in oldest[:len(_USAGE_CACHE) - _USAGE_CACHE_MAX_SIZE + 1]:
                _USAGE_CACHE.pop(k, None)
    _USAGE_CACHE[_usage_cache_key(user, sub)] = (
        time.monotonic() + _USAGE_CACHE_TTL_SECONDS,
        usage_info,
    )


def invalidate_usage_cache(user: User, sub: Subscription | None) -> None:
    if sub is None:
        return
    _USAGE_CACHE.pop(_usage_cache_key(user, sub), None)


def _merge_usage_info(primary: dict | None, fallback: dict | None) -> dict | None:
    if not primary:
        return fallback
    if not fallback:
        return primary

    merged = dict(fallback)
    for key, value in primary.items():
        if value is not None:
            merged[key] = value
    return merged


def _ensure_device_limit(usage_info: dict | None, fallback_device_limit: int | None) -> dict | None:
    if fallback_device_limit is None:
        return usage_info
    if usage_info is None:
        return {"device_limit": int(fallback_device_limit)}
    if usage_info.get("device_limit") is None:
        usage_info = dict(usage_info)
        usage_info["device_limit"] = int(fallback_device_limit)
    return usage_info


def _parse_subscription_userinfo(value: str | None) -> dict | None:
    text = str(value or "").strip()
    if not text:
        return None

    pairs = dict(re.findall(r"([a-zA-Z_]+)=([0-9]+)", text))
    upload = int(pairs.get("upload", "0"))
    download = int(pairs.get("download", "0"))
    total = pairs.get("total")
    expire = pairs.get("expire")

    used_bytes = upload + download
    total_bytes = int(total) if total is not None else None
    expire_at = None
    if expire and expire.isdigit():
        expire_at = datetime.fromtimestamp(int(expire), tz=timezone.utc)

    return {
        "current_usage_gb": round(used_bytes / (1024 * 1024 * 1024), 2),
        "usage_limit_gb": round(total_bytes / (1024 * 1024 * 1024), 2) if total_bytes is not None else None,
        "expire_at": expire_at,
    }


async def _fetch_subscription_usage_fallback(
    user: User,
    sub: Subscription,
    session: AsyncSession,
    *,
    timeout_seconds: float,
) -> dict | None:
    from bot.repositories.vpn_key import VpnKeyRepository

    keys = await VpnKeyRepository(session).get_user_keys(user.id, limit=1)
    if not keys:
        return None
    key_url = str(keys[0].key or "").strip()
    if not key_url.startswith(("http://", "https://")):
        return None

    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    async with aiohttp.ClientSession(timeout=timeout) as client:
        try:
            async with client.get(key_url, allow_redirects=True) as response:
                header = (
                    response.headers.get("subscription-userinfo")
                    or response.headers.get("Subscription-Userinfo")
                )
                return _parse_subscription_userinfo(header)
        except Exception:
            return None


async def resolve_subscription_usage_info(
    user: User,
    sub: Subscription,
    session: AsyncSession,
    *,
    usage_timeout_seconds: float = 6.0,
) -> dict | None:
    usage_info = _get_cached_usage(user, sub)
    if usage_info is None:
        provider = build_vpn_provider()
        try:
            usage_info = await asyncio.wait_for(
                provider.get_user_usage(
                    user_id=user.id,
                    subscription_uuid=user.subscription_uuid,
                    provider_subscription_id=sub.provider_subscription_id,
                ),
                timeout=usage_timeout_seconds,
            )
        except Exception:
            usage_info = None
        fallback_usage = await _fetch_subscription_usage_fallback(
            user,
            sub,
            session,
            timeout_seconds=usage_timeout_seconds,
        )
        usage_info = _merge_usage_info(usage_info, fallback_usage)
        usage_info = _ensure_device_limit(usage_info, user.max_devices)
        _store_cached_usage(user, sub, usage_info)
    return usage_info


async def format_main_menu(user: User, session: AsyncSession) -> str:
    snapshot = await build_main_menu_snapshot(user, session)
    return snapshot.text


async def build_main_menu_snapshot(
    user: User,
    session: AsyncSession,
    *,
    include_live_usage: bool = True,
    usage_timeout_seconds: float = 6.0,
) -> MainMenuSnapshot:
    from bot.repositories.subscription import SubscriptionRepository

    sub = await SubscriptionRepository(session).get_active(user.id)
    usage_info: dict | None = None
    if sub and include_live_usage:
        usage_info = await resolve_subscription_usage_info(
            user,
            sub,
            session,
            usage_timeout_seconds=usage_timeout_seconds,
        )
    if sub:
        subscription_block, remaining_gb, remaining_days = _build_main_menu_subscription_block(sub, usage_info)
    else:
        subscription_block, remaining_gb, remaining_days = await format_subscription_for_user(
            sub,
            show_type=False,
            usage_info=usage_info,
        )
    text = (
        f"👋 Привет, <b>{user.full_name or 'пользователь'}</b>!\n\n"
        f"💳 Баланс: <b>{user.balance} ₽</b>\n\n"
        f"{subscription_block}"
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
    plan = sub.__dict__.get("plan")
    plan_name = plan.name if plan else f"ID #{sub.plan_id}"
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
