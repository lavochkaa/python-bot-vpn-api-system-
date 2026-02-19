from aiogram import F, Router
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot.repositories.vpn_key import VpnKeyRepository
from bot.utils.messages import edit_or_send, send_or_answer

router = Router()


@router.callback_query(F.data == "menu:keys")
async def my_keys(call: CallbackQuery, session: AsyncSession) -> None:
    keys = await VpnKeyRepository(session).get_user_keys(call.from_user.id, limit=5)

    if not keys:
        builder = InlineKeyboardBuilder()
        builder.button(text="🔙 В меню", callback_data="menu:main")
        await edit_or_send(
            call.message,
            "🔑 У вас нет активных ключей.\n\nПодключите тариф, чтобы получить ключ.",
            reply_markup=builder.as_markup(),
        )
        await call.answer()
        return

    builder = InlineKeyboardBuilder()
    for vpn_key in keys:
        builder.button(
            text=f"Ключ #{vpn_key.id} ({vpn_key.issued_at.strftime('%d.%m.%Y')})",
            callback_data=f"key:show:{vpn_key.id}",
        )
    builder.button(text="🔙 В меню", callback_data="menu:main")
    builder.adjust(1)

    await edit_or_send(
        call.message,
        f"🔑 Ваши ключи (последние {len(keys)}):",
        reply_markup=builder.as_markup(),
    )
    await call.answer()


@router.callback_query(F.data.startswith("key:show:"))
async def show_key(call: CallbackQuery, session: AsyncSession) -> None:
    key_id = int(call.data.split(":")[-1])
    vpn_key = await VpnKeyRepository(session).get_user_key(call.from_user.id, key_id)
    if not vpn_key:
        await call.answer("Ключ не найден.", show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    builder.button(text="📋 Скопировать", callback_data=f"key:copy:{vpn_key.id}")
    builder.button(text="🔙 К списку", callback_data="menu:keys")
    builder.adjust(1)
    await edit_or_send(
        call.message,
        f"Ключ #{vpn_key.id}\n"
        f"Выдан: <b>{vpn_key.issued_at.strftime('%d.%m.%Y %H:%M')}</b>",
        reply_markup=builder.as_markup(),
    )
    await call.answer()


@router.callback_query(F.data.startswith("key:copy:"))
async def copy_key(call: CallbackQuery, session: AsyncSession) -> None:
    key_id = int(call.data.split(":")[-1])
    vpn_key = await VpnKeyRepository(session).get_user_key(call.from_user.id, key_id)
    if not vpn_key:
        await call.answer("Ключ не найден.", show_alert=True)
        return

    await send_or_answer(
        call.message,
        f"<code>{vpn_key.key}</code>\n\n"
        "⚠️ Не пересылайте ключ третьим лицам."
    )
    await call.answer("Ключ отправлен отдельным сообщением")
