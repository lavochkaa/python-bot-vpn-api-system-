from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def subscription_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🛒 Приобрести подписку", callback_data="subscription:change")
    builder.button(text="🔙 Назад", callback_data="menu:main")
    builder.adjust(1)
    return builder.as_markup()


def duration_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="30 дней", callback_data="subscription:duration:30")
    builder.button(text="6 месяцев", callback_data="subscription:duration:180")
    builder.button(text="1 год", callback_data="subscription:duration:365")
    builder.button(text="🔙 Назад", callback_data="menu:subscription")
    builder.adjust(1)
    return builder.as_markup()


def plan_type_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="VPN обход", callback_data="subscription:type:vpn")
    builder.button(text="VPN + обход", callback_data="subscription:type:vpn_bypass")
    builder.button(text="🔙 К сроку", callback_data="subscription:change")
    builder.adjust(1)
    return builder.as_markup()


def traffic_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="100 ГБ", callback_data="subscription:traffic:100")
    builder.button(text="300 ГБ", callback_data="subscription:traffic:300")
    builder.button(text="Безлимит", callback_data="subscription:traffic:unlimited")
    builder.button(text="🔙 К типу", callback_data="subscription:type:back")
    builder.adjust(1)
    return builder.as_markup()


def devices_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="2 устройства", callback_data="subscription:devices:2")
    builder.button(text="4 устройства", callback_data="subscription:devices:4")
    builder.button(text="10 устройств", callback_data="subscription:devices:10")
    builder.button(text="Неограниченно", callback_data="subscription:devices:unlimited")
    builder.button(text="🔙 К трафику", callback_data="subscription:traffic:back")
    builder.adjust(1)
    return builder.as_markup()


def subscription_promo_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🎟 Ввести промокод", callback_data="subscription:promo")
    builder.button(text="➡️ Без промокода", callback_data="subscription:skip_promo")
    builder.button(text="🔙 К устройствам", callback_data="subscription:devices:back")
    builder.adjust(1)
    return builder.as_markup()


def insufficient_balance_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💵 Пополнить баланс", callback_data="menu:balance")
    builder.button(text="🔙 К подписке", callback_data="menu:subscription")
    builder.adjust(1)
    return builder.as_markup()
