import asyncio
import time
from datetime import datetime, timezone
from dataclasses import dataclass
from sqlalchemy.ext.asyncio import AsyncSession
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
    return f"{value:.1f}".rstrip("0").rstrip(".")


def _guess_device_entry(last_user_agent: str | None) -> tuple[int | None, list[str]]:
    ua = str(last_user_agent or "").strip()
    if not ua:
        return None, []
    low = ua.lower()
    if "iphone" in low or "ios" in low:
        return 1, ["iOS - iPhone"]
    if "macos" in low or "darwin" in low or "macbook" in low:
        return 1, ["macOS - MacBook"]
    if "windows" in low:
        return 1, ["Windows - PC"]
    if "android" in low:
        return 1, ["Android - Phone"]
    return 1, [ua[:48]]


def _escape_code(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _resolve_device_entries(usage_info: dict | None) -> tuple[int | None, list[str]]:
    if not usage_info:
        return None, []

    raw_devices = usage_info.get("connected_devices")
    if isinstance(raw_devices, list):
        device_lines: list[str] = []
        seen: set[str] = set()
        for item in raw_devices:
            label = str(item or "").strip()
            if not label or label in seen:
                continue
            seen.add(label)
            device_lines.append(label)
        if device_lines:
            raw_count = usage_info.get("connected_devices_count")
            try:
                connected_count = int(raw_count) if raw_count is not None else len(device_lines)
            except (TypeError, ValueError):
                connected_count = len(device_lines)
            return connected_count, device_lines

    raw_count = usage_info.get("connected_devices_count")
    try:
        connected_count = int(raw_count) if raw_count is not None else None
    except (TypeError, ValueError):
        connected_count = None
    if connected_count is not None:
        return connected_count, []

    return _guess_device_entry(usage_info.get("last_user_agent"))


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
        device_limit = 2

    connected_count, device_lines = _resolve_device_entries(usage_info)
    devices_title = f"{connected_count if connected_count is not None else '—'} / {device_limit}"
    devices_block = ""
    if device_lines:
        devices_block = "\n📱 <b>Подключенные устройства:</b>\n" + "\n".join(
            f"• <code>{_escape_code(line)}</code>" for line in device_lines
        )

    expires_title = expires.strftime("%d.%m.%Y %H:%M") if expires else "—"
    text = (
        "📦 <b>Текущая подписка</b>\n\n"
        f"Трафик: <b>{_format_gb(used_gb)} / {_format_gb(total_gb)} ГБ</b>\n"
        f"Действует до: <b>{expires_title}</b>\n"
        f"Устройства: <code>{_escape_code(devices_title)}</code>"
        f"{devices_block}"
    )
    return text, remaining_gb, remaining_days


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
            _store_cached_usage(user, sub, usage_info)
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
