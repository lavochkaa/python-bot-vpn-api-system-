from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from bot.db.models import Plan


def admin_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="👤 Клиенты", callback_data="admin:users")
    builder.button(text="🎟 Промокоды", callback_data="admin:promos")
    builder.button(text="🎫 Тикеты", callback_data="admin:tickets")
    builder.button(text="📊 Статистика", callback_data="admin:stats")
    builder.button(text="🔙 В меню", callback_data="menu:main")
    builder.adjust(1)
    return builder.as_markup()


def admin_back_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Назад", callback_data="admin:menu")
    builder.adjust(1)
    return builder.as_markup()


def admin_promos_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Создать промокод", callback_data="admin:promos:create")
    builder.button(text="📋 Активные промокоды", callback_data="admin:promos:active:1")
    builder.button(text="🔙 Назад", callback_data="admin:menu")
    builder.adjust(1)
    return builder.as_markup()


def admin_promo_target_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💳 Баланс", callback_data="admin:promo:target:balance")
    builder.button(text="📦 Подписка", callback_data="admin:promo:target:subscription")
    builder.button(text="🔙 Назад", callback_data="admin:promos")
    builder.adjust(1)
    return builder.as_markup()


def admin_tickets_keyboard(ticket_ids: list[int], active_filter: str = "open") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=("✅ Открытые" if active_filter == "open" else "Открытые"),
        callback_data="admin:tickets:open",
    )
    builder.button(
        text=("✅ Ожидают ответа" if active_filter == "pending" else "Ожидают ответа"),
        callback_data="admin:tickets:pending",
    )
    builder.button(
        text=("✅ Закрытые" if active_filter == "closed" else "Закрытые"),
        callback_data="admin:tickets:closed",
    )
    for ticket_id in ticket_ids:
        builder.button(text=f"Тикет #{ticket_id}", callback_data=f"admin:ticket:{ticket_id}")
    builder.button(text="🔎 Поиск", callback_data="admin:tickets:search")
    builder.button(text="🔙 Назад", callback_data="admin:menu")
    builder.adjust(3, 1)
    return builder.as_markup()


def admin_ticket_actions_keyboard(ticket_id: int, is_open: bool = True) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if is_open:
        builder.button(text="✉️ Ответить", callback_data=f"admin:reply:{ticket_id}")
        builder.button(text="🔒 Закрыть", callback_data=f"admin:close:{ticket_id}")
    builder.button(text="🔙 К тикетам", callback_data="admin:tickets")
    builder.adjust(1)
    return builder.as_markup()


def admin_user_manage_keyboard(user_id: int, has_subscription: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💰 Изменить баланс", callback_data=f"admin:user:edit_balance:{user_id}")
    builder.button(text="📅 Изменить дни подписки", callback_data=f"admin:user:edit_days:{user_id}")
    builder.button(text="📦 Изменить тариф", callback_data=f"admin:user:edit_plan:{user_id}")
    builder.button(text="✅ Сохранить изменения", callback_data=f"admin:user:apply:{user_id}")
    builder.button(text="🔎 Найти другого клиента", callback_data="admin:users")
    builder.button(text="🔙 Вернуться в админ-панель", callback_data="admin:menu")
    builder.adjust(1)
    return builder.as_markup()


def admin_user_plans_keyboard(user_id: int, plans: list[Plan]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for plan in plans:
        builder.button(
            text=f"{plan.name} ({plan.price} ₽)",
            callback_data=f"admin:user:set_plan:{user_id}:{plan.id}",
        )
    builder.button(text="🔙 К профилю", callback_data=f"admin:user:view:{user_id}")
    builder.adjust(1)
    return builder.as_markup()


def admin_sign_keyboard(kind: str, user_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Увеличить", callback_data=f"admin:user:{kind}_op:{user_id}:add")
    builder.button(text="➖ Уменьшить", callback_data=f"admin:user:{kind}_op:{user_id}:sub")
    builder.button(text="🔙 К профилю", callback_data=f"admin:user:view:{user_id}")
    builder.adjust(1)
    return builder.as_markup()


def admin_promos_list_keyboard(items: list[tuple[int, str]], page: int, has_next: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for promo_id, label in items:
        builder.button(text=label, callback_data=f"admin:promo:view:{promo_id}")
    if page > 1:
        builder.button(text="⬅️", callback_data=f"admin:promos:active:{page - 1}")
    if has_next:
        builder.button(text="➡️", callback_data=f"admin:promos:active:{page + 1}")
    builder.button(text="🔙 Назад", callback_data="admin:promos")
    builder.adjust(1)
    return builder.as_markup()


def admin_promo_details_keyboard(promo_id: int, active: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Изменить", callback_data=f"admin:promo:edit:{promo_id}")
    builder.button(text=("⏸ Деактивировать" if active else "▶️ Активировать"), callback_data=f"admin:promo:toggle:{promo_id}")
    builder.button(text="🗑 Удалить", callback_data=f"admin:promo:delete:{promo_id}")
    builder.button(text="⬅️ Назад", callback_data="admin:promos:active:1")
    builder.adjust(1)
    return builder.as_markup()


def admin_promo_edit_keyboard(promo_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💸 Изменить value", callback_data=f"admin:promo:edit:value:{promo_id}")
    builder.button(text="📅 Изменить expiresAt", callback_data=f"admin:promo:edit:expires:{promo_id}")
    builder.button(text="🔢 Изменить usageLimit", callback_data=f"admin:promo:edit:limit:{promo_id}")
    builder.button(text="⬅️ Назад", callback_data=f"admin:promo:view:{promo_id}")
    builder.adjust(1)
    return builder.as_markup()


def admin_promo_delete_confirm_keyboard(promo_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да, удалить", callback_data=f"admin:promo:delete:confirm:{promo_id}")
    builder.button(text="❌ Нет", callback_data=f"admin:promo:view:{promo_id}")
    builder.adjust(1)
    return builder.as_markup()
