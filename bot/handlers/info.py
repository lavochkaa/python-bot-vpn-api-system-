from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from bot.utils.messages import edit_or_send

router = Router()


@router.callback_query(F.data == "menu:info")
async def info_section(call: CallbackQuery) -> None:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Назад", callback_data="menu:main")
    await edit_or_send(
        call.message,
        "ℹ️ <b>Информация</b>\n\n🚧 Раздел в разработке.\n\nСкоро здесь появится полезная информация о сервисе.",
        reply_markup=builder.as_markup(),
    )
    await call.answer()
