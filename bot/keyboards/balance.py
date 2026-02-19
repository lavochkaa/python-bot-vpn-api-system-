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
    builder.button(text="5 ₽", callback_data="balance:amount:5")
    builder.button(text="10 ₽", callback_data="balance:amount:10")
    builder.button(text="20 ₽", callback_data="balance:amount:20")
    builder.button(text="✍️ Другая сумма", callback_data="balance:amount:custom")
    builder.button(text="🔙 Назад", callback_data="menu:balance")
    builder.adjust(3, 1, 1)
    return builder.as_markup()


def promo_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🎟 Ввести промокод", callback_data="balance:promo")
    builder.button(text="➡️ Без промокода", callback_data="balance:skip_promo")
    builder.button(text="🔙 Назад", callback_data="menu:balance")
    builder.adjust(1)
    return builder.as_markup()
