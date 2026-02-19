from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from bot.db.models import User, Subscription


async def format_main_menu(user: User, session: AsyncSession) -> str:
    from bot.repositories.subscription import SubscriptionRepository
    sub = await SubscriptionRepository(session).get_active(user.id)
    sub_info = await format_subscription_info(sub)
    return (
        f"👋 Привет, <b>{user.full_name or 'пользователь'}</b>!\n\n"
        f"💳 Баланс: <b>{user.balance} ₽</b>\n\n"
        f"{sub_info}"
    )


async def format_subscription_info(sub: Subscription | None) -> str:
    if not sub:
        return "📦 Тариф: <b>none</b>\nСтатус: ❌ none\nДействует до: <b>—</b>"

    now = datetime.now(timezone.utc)
    expires = sub.expires_at
    is_active = sub.is_active and expires is not None and expires > now
    status = "✅ активна" if is_active else "❌ истекла"
    status_code = "active" if is_active else "expired"
    expires_str = expires.strftime("%d.%m.%Y") if expires else "—"
    plan_name = sub.plan.name if sub.plan else f"ID #{sub.plan_id}"
    return (
        f"📦 Тариф: <b>{plan_name}</b>\n"
        f"Статус: {status} ({status_code})\n"
        f"Действует до: <b>{expires_str}</b>"
    )
