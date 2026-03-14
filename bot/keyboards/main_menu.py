from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def main_menu_keyboard(connect_url: str | None = None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if connect_url:
        builder.button(text="🔌 Подключиться", url=connect_url)
    else:
        builder.button(text="🔌 Подключиться", callback_data="menu:connect")
    builder.button(text="🔄 Обновить данные", callback_data="menu:main:refresh")
    builder.button(text="💳 Баланс", callback_data="menu:balance")
    builder.button(text="📦 Подписка", callback_data="menu:subscription")
    builder.button(text="ℹ️ Информация", url="https://teletype.in/@crystalvpn_bot/offerta")
    builder.button(text="🆘 Поддержка", callback_data="menu:support")
    builder.adjust(1, 1, 2, 2)
    return builder.as_markup()


def channel_check_keyboard() -> InlineKeyboardMarkup:
    from bot.config import settings
    channel_username = settings.normalized_channel_username.lstrip("@")
    builder = InlineKeyboardBuilder()
    builder.button(
        text="📢 Подписаться",
        url=f"https://t.me/{channel_username}",
    )
    builder.button(text="✅ Проверить", callback_data="check:subscription")
    builder.adjust(1)
    return builder.as_markup()


def subscription_warning_keyboard(
    *,
    show_refresh_gb: bool,
    show_renew: bool,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if show_refresh_gb:
        builder.button(text="🔄 Обновить данные", callback_data="menu:main:refresh")
    if show_renew:
        builder.button(text="🔄 Продлить подписку", callback_data="menu:subscription:configure")
    builder.button(text="📦 Открыть подписку", callback_data="menu:subscription")
    builder.adjust(1)
    return builder.as_markup()
