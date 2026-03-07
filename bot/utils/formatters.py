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


async def format_main_menu(user: User, session: AsyncSession) -> str:
    snapshot = await build_main_menu_snapshot(user, session)
    return snapshot.text


async def build_main_menu_snapshot(user: User, session: AsyncSession) -> MainMenuSnapshot:
    from bot.repositories.subscription import SubscriptionRepository

    sub = await SubscriptionRepository(session).get_active(user.id)
    usage_info: dict | None = None
    if sub:
        provider = build_vpn_provider()
        try:
            usage_info = await provider.get_user_usage(
                user_id=user.id,
                subscription_uuid=user.subscription_uuid,
                provider_subscription_id=sub.provider_subscription_id,
            )
        except Exception:
            usage_info = None
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
        if provider_total is not None:
            total_gb = float(provider_total)
    if total_gb is not None and used_gb is not None:
        remaining_gb = max(total_gb - float(used_gb), 0.0)
        traffic_title = f"{int(total_gb) if float(total_gb).is_integer() else round(total_gb, 2)} ГБ (исп: {round(float(used_gb), 2)} ГБ)"
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
