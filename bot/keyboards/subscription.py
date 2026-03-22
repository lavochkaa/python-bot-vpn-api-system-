from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.constants.subscription_pricing import DURATION_MONTH_OPTIONS, TRAFFIC_OPTIONS, DEVICE_LIMIT_OPTIONS

def subscription_activated_keyboard(
    show_reset_traffic: bool = False,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Изменить тариф", callback_data="menu:subscription:configure")
    if show_reset_traffic:
        builder.button(text="♻️ Сбросить трафик (79 ₽)", callback_data="sub_reset_traffic")
    builder.button(text="🔙 Назад", callback_data="menu:main")
    builder.adjust(1)
    return builder.as_markup()


def subscription_configurator_keyboard(
    selected_gb: int | None,
    selected_term_months: int | None,
    selected_device_limit: int | None,
    has_promo: bool = False,
) -> InlineKeyboardMarkup:
    promo_button = InlineKeyboardButton(
        text="❌ Убрать промокод" if has_promo else "🎟 Промокод",
        callback_data="sub_promo_clear" if has_promo else "sub_promo_enter",
    )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{'✅ ' if gb == selected_gb else ''}{gb} ГБ",
                    callback_data=f"sub_gb_{gb}",
                )
                for gb in TRAFFIC_OPTIONS
            ],
            [
                InlineKeyboardButton(
                    text=f"{'✅ ' if term == selected_term_months else ''}{term} мес",
                    callback_data=f"sub_term_{term}",
                )
                for term in DURATION_MONTH_OPTIONS
            ],
            [
                InlineKeyboardButton(
                    text=f"{'✅ ' if device_limit == selected_device_limit else ''}{device_limit} устр.",
                    callback_data=f"sub_devices_{device_limit}",
                )
                for device_limit in DEVICE_LIMIT_OPTIONS
            ],
            [
                promo_button,
                InlineKeyboardButton(text="💳 Оплатить", callback_data="sub_pay"),
            ],
            [
                InlineKeyboardButton(text="🔙 Назад", callback_data="menu:main"),
            ],
        ]
    )


def insufficient_balance_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💵 Пополнить баланс", callback_data="menu:balance")
    builder.button(text="🔙 К подписке", callback_data="menu:subscription")
    builder.adjust(1)
    return builder.as_markup()
