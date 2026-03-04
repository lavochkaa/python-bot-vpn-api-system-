from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from bot.db.models import User, Subscription


async def format_main_menu(user: User, session: AsyncSession) -> str:
    from bot.repositories.subscription import SubscriptionRepository
    sub = await SubscriptionRepository(session).get_active(user.id)
    sub_info = await format_subscription_for_user(sub, show_type=False)
    return (
        f"👋 Привет, <b>{user.full_name or 'пользователь'}</b>!\n\n"
        f"💳 Баланс: <b>{user.balance} ₽</b>\n\n"
        f"{sub_info}"
    )


async def format_subscription_for_user(sub: Subscription | None, show_type: bool = False) -> str:
    if not sub:
        return "📦 Тариф: <b>none</b>\nСтатус: ❌ none\nДействует до: <b>—</b>"

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
    plan_name = sub.plan.name if sub.plan else f"ID #{sub.plan_id}"
    traffic_title = f"{sub.traffic_gb} ГБ" if sub.traffic_gb else "—"
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
    return "".join(lines)


async def format_subscription_info(sub: Subscription | None) -> str:
    """Backward-compatible wrapper."""
    return await format_subscription_for_user(sub, show_type=False)
