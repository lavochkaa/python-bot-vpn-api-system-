from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def topup_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💵 Пополнить баланс", callback_data="balance:topup")
    builder.button(text="🔙 Назад", callback_data="menu:main")
    builder.adjust(1)
    return builder.as_markup()


def amount_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="100 ₽", callback_data="balance:amount:100")
    builder.button(text="300 ₽", callback_data="balance:amount:300")
    builder.button(text="500 ₽", callback_data="balance:amount:500")
    builder.button(text="✍️ Другая сумма", callback_data="balance:amount:custom")
    builder.button(text="🔙 Назад", callback_data="menu:balance")
    builder.adjust(3, 1, 1)
    return builder.as_markup()


def topup_preview_keyboard(has_promo: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if has_promo:
        builder.button(text="❌ Убрать промокод", callback_data="balance:promo:clear")
    else:
        builder.button(text="🎟 Промокод", callback_data="balance:promo:enter")
    builder.button(text="💳 Перейти к оплате", callback_data="balance:pay")
    builder.button(text="🔙 Назад", callback_data="balance:topup")
    builder.adjust(1)
    return builder.as_markup()


def custom_amount_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Назад", callback_data="balance:topup")
    builder.adjust(1)
    return builder.as_markup()


def payment_link_keyboard(pay_url: str, invoice_id: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💳 Оплатить", url=pay_url)
    builder.button(text="✅ Проверить оплату", callback_data=f"balance:check:{invoice_id}")
    builder.button(text="🔙 К балансу", callback_data="menu:balance")
    builder.adjust(1)
    return builder.as_markup()
