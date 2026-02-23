"""bot/handlers/keys.py"""

import base64
from datetime import datetime, timezone
from urllib.parse import quote

from aiogram import F, Router
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.repositories.subscription import SubscriptionRepository
from bot.repositories.vpn_key import VpnKeyRepository
from bot.utils.messages import edit_or_send, send_or_answer

router = Router()

APP_TITLES = {
    "hiddify":   "Hiddify",
    "v2":        "v2",
    "streisand": "Streisand",
    "happ":      "Happ",
}


def _telegram_safe_open_url(deep_link: str) -> str:
    """
    Telegram inline keyboard поддерживает только http/https/tg схемы.
    Для кастомных схем оборачиваем через share URL.
    """
    if deep_link.startswith(("https://", "http://", "tg://")):
        return deep_link
    return f"https://t.me/share/url?url={quote(deep_link, safe='')}"


def _build_deep_link(app: str, key: str) -> str:
    """Строит deeplink для импорта подписки в конкретное приложение."""
    key_url = quote(key, safe="")
    key_b64 = base64.urlsafe_b64encode(key.encode("utf-8")).decode("utf-8")
    if app == "hiddify":
        return settings.hiddify_deeplink_template.format(key_url=key_url, key_b64=key_b64)
    if app == "v2":
        return settings.v2_deeplink_template.format(key_url=key_url, key_b64=key_b64)
    if app == "streisand":
        return settings.streisand_deeplink_template.format(key_url=key_url, key_b64=key_b64)
    if app == "happ":
        return settings.happ_deeplink_template.format(key_url=key_url, key_b64=key_b64)
    raise ValueError(f"Unknown app: {app}")


# ── Проверка активности подписки ─────────────────────────────────────────────

def _is_effectively_active(sub) -> bool:
    if not sub or not sub.is_active or not sub.expires_at:
        return False
    return sub.expires_at > datetime.now(timezone.utc)


# ── Клавиатуры ───────────────────────────────────────────────────────────────

def _apps_pick_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="Hiddify",   callback_data="connect:app:hiddify")
    builder.button(text="v2",        callback_data="connect:app:v2")
    builder.button(text="Streisand", callback_data="connect:app:streisand")
    builder.button(text="Happ",      callback_data="connect:app:happ")
    builder.button(text="🔙 Назад",  callback_data="menu:main")
    builder.adjust(2, 2, 1)
    return builder.as_markup()


def _direct_subscription_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="❓ Как подключиться?", callback_data="connect:howto")
    builder.button(text="🔙 В меню",            callback_data="menu:main")
    builder.adjust(1, 1)
    return builder.as_markup()


def _howto_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 К ссылке подключения", callback_data="menu:connect")
    builder.button(text="🏠 В меню",                callback_data="menu:main")
    builder.adjust(1, 1)
    return builder.as_markup()


def _open_app_keyboard(app_title: str, deep_link: str):
    builder = InlineKeyboardBuilder()
    builder.button(text=f"🚀 Открыть {app_title}", url=_telegram_safe_open_url(deep_link))
    builder.button(text="🔙 К выбору приложения",  callback_data="menu:connect")
    builder.adjust(1)
    return builder.as_markup()


# ── Хэндлеры ─────────────────────────────────────────────────────────────────

@router.callback_query(F.data.in_({"menu:keys", "menu:connect"}))
async def connect_apps(call: CallbackQuery, session: AsyncSession) -> None:
    sub = await SubscriptionRepository(session).get_active(call.from_user.id)
    if not _is_effectively_active(sub):
        builder = InlineKeyboardBuilder()
        builder.button(text="🔙 В меню", callback_data="menu:main")
        builder.adjust(1)
        await edit_or_send(
            call.message,
            "🔌 Активная подписка не найдена.\n\nОформите подписку, чтобы получить подключение.",
            reply_markup=builder.as_markup(),
        )
        await call.answer()
        return

    keys = await VpnKeyRepository(session).get_user_keys(call.from_user.id, limit=1)

    if not keys:
        builder = InlineKeyboardBuilder()
        builder.button(text="🔙 В меню", callback_data="menu:main")
        builder.adjust(1)
        await edit_or_send(
            call.message,
            "🔌 Подписка активна, но конфиг пока не готов.\n"
            "Попробуйте позже или обратитесь в поддержку.",
            reply_markup=builder.as_markup(),
        )
        await call.answer()
        return

    vpn_key = keys[0]
    user_key = vpn_key.key

    if user_key.startswith(("https://", "http://")):
        safe_url = user_key.replace("<", "").replace(">", "")
        try:
            await call.answer(url=user_key)
        except Exception:
            await call.answer()
        await send_or_answer(
            call.message,
            "🔌 <b>Ваша ссылка подписки</b>\n"
            f"<code>{safe_url}</code>\n\n"
            "Скопируйте ссылку и импортируйте в приложение.\n"
            "Если нужна помощь — нажмите «Как подключиться?».",
            reply_markup=_direct_subscription_keyboard(),
        )
        return

    await send_or_answer(
        call.message,
        "🔌 <b>Ваша ссылка подписки</b>\n"
        "Нажмите «Как подключиться?» для инструкции.",
        reply_markup=_direct_subscription_keyboard(),
    )
    await call.answer()


@router.callback_query(F.data.startswith("connect:app:"))
async def connect_pick_app(call: CallbackQuery, session: AsyncSession) -> None:
    app = call.data.split(":")[-1]
    if app not in APP_TITLES:
        await call.answer("Неизвестное приложение.", show_alert=True)
        return

    sub = await SubscriptionRepository(session).get_active(call.from_user.id)
    if not _is_effectively_active(sub):
        await call.answer("Активная подписка не найдена.", show_alert=True)
        return

    keys = await VpnKeyRepository(session).get_user_keys(call.from_user.id, limit=1)
    if not keys:
        await call.answer("Конфиг ещё не готов.", show_alert=True)
        return

    vpn_key = keys[0]
    user_key = vpn_key.key
    deep_link = _build_deep_link(app, user_key)
    open_url = _telegram_safe_open_url(deep_link)

    try:
        await call.answer(url=open_url)
    except Exception:
        await call.answer()

    await send_or_answer(
        call.message,
        f"Открываю <b>{APP_TITLES[app]}</b>. Если не открылось автоматически — нажмите кнопку ниже.",
        reply_markup=_open_app_keyboard(APP_TITLES[app], deep_link),
    )


@router.callback_query(F.data == "connect:howto")
async def connect_howto(call: CallbackQuery) -> None:
    text = (
        "🧩 <b>Как подключиться</b>\n\n"
        "1. Скопируйте ссылку подписки из предыдущего сообщения.\n"
        "2. Установите любое приложение ниже.\n"
        "3. Импортируйте ссылку подписки в приложение.\n\n"
        "<b>Hiddify</b>\n"
        "Android: https://play.google.com/store/apps/details?id=app.hiddify.com\n"
        "iOS: https://apps.apple.com/us/app/hiddify-proxy-vpn/id6596777532\n\n"
        "<b>V2 (v2rayNG / v2box)</b>\n"
        "Android: https://play.google.com/store/apps/details?id=com.v2ray.ang\n"
        "iOS: https://apps.apple.com/us/app/v2box-v2ray-client/id6446814690\n\n"
        "<b>Streisand</b>\n"
        "Android: https://play.google.com/store/search?q=streisand&c=apps\n"
        "iOS: https://apps.apple.com/us/search?term=streisand\n\n"
        "<b>Happ</b>\n"
        "Android: https://play.google.com/store/search?q=happ%20vpn&c=apps\n"
        "iOS: https://apps.apple.com/us/search?term=happ%20vpn"
    )
    await edit_or_send(call.message, text, reply_markup=_howto_keyboard())
    await call.answer()
