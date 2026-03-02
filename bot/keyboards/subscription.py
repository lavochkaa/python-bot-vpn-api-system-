from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def subscription_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🛒 Приобрести подписку", callback_data="subscription:change")
    builder.button(text="🔙 Назад", callback_data="menu:main")
    builder.adjust(1)
    return builder.as_markup()


def subscription_active_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Изменить подписку", callback_data="subscription:change")
    builder.button(text="🔙 Назад", callback_data="menu:main")
    builder.adjust(1)
    return builder.as_markup()


def subscription_activated_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔌 Подключиться", callback_data="menu:connect")
    builder.button(text="🔄 Изменить подписку", callback_data="subscription:change")
    builder.button(text="🔙 Назад", callback_data="menu:main")
    builder.adjust(1)
    return builder.as_markup()


def duration_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="1 месяц", callback_data="subscription:duration:30")
    builder.button(text="3 месяца", callback_data="subscription:duration:90")
    builder.button(text="6 месяцев", callback_data="subscription:duration:180")
    builder.button(text="12 месяцев", callback_data="subscription:duration:365")
    builder.button(text="🔙 Назад", callback_data="menu:main")
    builder.adjust(1)
    return builder.as_markup()


def plan_type_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📱 Обход + VPN (телефон)", callback_data="subscription:type:phone")
    builder.button(text="💻 VPN (ПК)", callback_data="subscription:type:pc")
    builder.button(text="🔙 К сроку", callback_data="subscription:change")
    builder.adjust(1)
    return builder.as_markup()


def traffic_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="50 ГБ", callback_data="subscription:traffic:50")
    builder.button(text="150 ГБ", callback_data="subscription:traffic:150")
    builder.button(text="500 ГБ", callback_data="subscription:traffic:500")
    builder.button(text="🔙 К сроку", callback_data="subscription:duration:back")
    builder.adjust(1)
    return builder.as_markup()


def subscription_confirm_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Оплатить с баланса", callback_data="subscription:pay_balance")
    builder.button(text="🔙 К трафику", callback_data="subscription:traffic:back")
    builder.adjust(1)
    return builder.as_markup()


def promo_retry_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔁 Повторить попытку", callback_data="subscription:promo:retry")
    builder.button(text="🔙 Назад", callback_data="subscription:promo:back")
    builder.adjust(1)
    return builder.as_markup()


def insufficient_balance_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💵 Пополнить баланс", callback_data="menu:balance")
    builder.button(text="🔙 К подписке", callback_data="menu:subscription")
    builder.adjust(1)
    return builder.as_markup()
