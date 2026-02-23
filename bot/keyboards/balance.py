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


def custom_amount_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Назад", callback_data="balance:topup")
    builder.adjust(1)
    return builder.as_markup()
