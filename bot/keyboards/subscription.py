from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.constants.subscription_pricing import DURATION_MONTH_OPTIONS, TRAFFIC_OPTIONS

def subscription_activated_keyboard(
    show_reset_traffic: bool = False,
    *,
    connect_url: str | None = None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if connect_url:
        builder.button(text="🔌 Подключиться", url=connect_url)
    else:
        builder.button(text="🔌 Подключиться", callback_data="menu:connect")
    builder.button(text="🔄 Изменить тариф", callback_data="menu:subscription:configure")
    if show_reset_traffic:
        builder.button(text="♻️ Сбросить трафик (79 ₽)", callback_data="sub_reset_traffic")
    builder.button(text="🔙 Назад", callback_data="menu:main")
    builder.adjust(1)
    return builder.as_markup()


def subscription_configurator_keyboard(
    selected_gb: int | None,
    selected_term_months: int | None,
    has_promo: bool = False,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    for gb in TRAFFIC_OPTIONS:
        prefix = "✅ " if gb == selected_gb else ""
        builder.button(text=f"{prefix}{gb} ГБ", callback_data=f"sub_gb_{gb}")

    for term in DURATION_MONTH_OPTIONS:
        prefix = "✅ " if term == selected_term_months else ""
        builder.button(text=f"{prefix}{term} мес", callback_data=f"sub_term_{term}")

    if has_promo:
        builder.button(text="❌ Убрать промокод", callback_data="sub_promo_clear")
    else:
        builder.button(text="🎟 Промокод", callback_data="sub_promo_enter")
    builder.button(text="💳 Оплатить", callback_data="sub_pay")
    builder.button(text="🔙 Назад", callback_data="menu:main")
    builder.adjust(3, 4, 2, 1)
    return builder.as_markup()


def insufficient_balance_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💵 Пополнить баланс", callback_data="menu:balance")
    builder.button(text="🔙 К подписке", callback_data="menu:subscription")
    builder.adjust(1)
    return builder.as_markup()
