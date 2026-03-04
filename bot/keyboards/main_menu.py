from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def main_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔌 Подключиться", callback_data="menu:connect")
    builder.button(text="💳 Баланс", callback_data="menu:balance")
    builder.button(text="📦 Подписка", callback_data="menu:subscription")
    builder.button(text="ℹ️ Информация", url="https://teletype.in/@crystalvpn_bot/offerta")
    builder.button(text="🆘 Поддержка", callback_data="menu:support")
    builder.adjust(1, 2, 2)
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
