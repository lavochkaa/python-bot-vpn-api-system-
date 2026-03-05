"""bot/handlers/keys.py"""

import base64
import re
from datetime import datetime, timezone
from urllib.parse import quote
from urllib.parse import urlparse
from uuid import UUID

from aiogram import F, Router
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.repositories.subscription import SubscriptionRepository
from bot.repositories.user import UserRepository
from bot.repositories.vpn_key import VpnKeyRepository
from bot.utils.messages import edit_or_send, edit_or_send_banner

router = Router()
UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{12}"
)

APP_TITLES = {
    "hiddify":   "Hiddify",
    "v2":        "v2",
    "streisand": "Streisand",
    "happ":      "Happ",
}

DEVICE_GUIDES = {
    "android": {
        "text": (
            "📱 <b>Android — как подключиться</b>\n\n"
            "1. Установите приложение по кнопкам ниже.\n"
            "2. Вернитесь в «Подключиться» и нажмите нужное приложение (Hiddify/v2/Streisand/Happ).\n"
            "3. Подтвердите импорт подписки."
        ),
        "links": (
            ("Hiddify", "https://play.google.com/store/apps/details?id=app.hiddify.com"),
            ("v2rayNG", "https://play.google.com/store/apps/details?id=com.v2ray.ang"),
            ("Streisand", "https://play.google.com/store/search?q=streisand&c=apps"),
            ("Happ", "https://play.google.com/store/search?q=happ%20vpn&c=apps"),
        ),
    },
    "ios": {
        "text": (
            "🍎 <b>iOS — как подключиться</b>\n\n"
            "1. Установите приложение по кнопкам ниже.\n"
            "2. Вернитесь в «Подключиться» и нажмите нужное приложение.\n"
            "3. Разрешите импорт и включите подключение."
        ),
        "links": (
            ("Hiddify", "https://apps.apple.com/us/app/hiddify-proxy-vpn/id6596777532"),
            ("v2box", "https://apps.apple.com/us/app/v2box-v2ray-client/id6446814690"),
            ("Streisand", "https://apps.apple.com/us/search?term=streisand"),
            ("Happ", "https://apps.apple.com/us/search?term=happ%20vpn"),
        ),
    },
    "pc": {
        "text": (
            "💻 <b>ПК — как подключиться</b>\n\n"
            "1. Установите клиент для вашей ОС.\n"
            "2. Вернитесь в «Подключиться» и нажмите подходящее приложение.\n"
            "3. Подтвердите импорт подписки и подключитесь."
        ),
        "links": (
            ("Hiddify Next", "https://github.com/hiddify/hiddify-next/releases"),
            ("Clash Verge Rev", "https://github.com/clash-verge-rev/clash-verge-rev/releases"),
            ("v2rayN (Windows)", "https://github.com/2dust/v2rayN/releases"),
            ("Nekoray (Win/Linux)", "https://github.com/MatsuriDayo/nekoray/releases"),
        ),
    },
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


def _extract_uuid_from_key(key: str) -> str | None:
    match = UUID_RE.search(key)
    if match:
        candidate = match.group(0).lower()
        try:
            return str(UUID(candidate))
        except ValueError:
            return None

    if key.startswith(("https://", "http://")):
        parsed = urlparse(key)
        for chunk in parsed.path.split("/"):
            raw = chunk.strip().lower()
            if not raw:
                continue
            try:
                return str(UUID(raw))
            except ValueError:
                continue
    return None


# ── Проверка активности подписки ─────────────────────────────────────────────

def _is_effectively_active(sub) -> bool:
    if not sub or not sub.is_active or not sub.expires_at:
        return False
    return sub.expires_at > datetime.now(timezone.utc)


# ── Клавиатуры ───────────────────────────────────────────────────────────────

def _apps_pick_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="📱 Android", callback_data="connect:device:android")
    builder.button(text="🍎 iOS", callback_data="connect:device:ios")
    builder.button(text="💻 ПК", callback_data="connect:device:pc")
    builder.button(text="🔙 Назад",  callback_data="menu:main")
    builder.adjust(2, 1, 1)
    return builder.as_markup()


def _device_guide_keyboard(device: str):
    builder = InlineKeyboardBuilder()
    device_data = DEVICE_GUIDES[device]
    for label, url in device_data["links"]:
        builder.button(text=f"⬇️ {label}", url=url)
    builder.button(text="🔙 К подключению", callback_data="menu:connect")
    builder.button(text="🏠 В меню", callback_data="menu:main")
    builder.adjust(1)
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
        await edit_or_send_banner(
            call.message,
            "🔌 Активная подписка не найдена.\n\nОформите подписку, чтобы получить подключение.",
            reply_markup=builder.as_markup(),
            banner_path=settings.message_banner_connect_path,
        )
        await call.answer()
        return

    keys = await VpnKeyRepository(session).get_user_keys(call.from_user.id, limit=1)

    if not keys:
        builder = InlineKeyboardBuilder()
        builder.button(text="🔙 В меню", callback_data="menu:main")
        builder.adjust(1)
        await edit_or_send_banner(
            call.message,
            "🔌 Подписка активна, но конфиг пока не готов.\n"
            "Попробуйте позже или обратитесь в поддержку.",
            reply_markup=builder.as_markup(),
            banner_path=settings.message_banner_connect_path,
        )
        await call.answer()
        return

    vpn_key = keys[0]
    user_key = vpn_key.key
    extracted_uuid = _extract_uuid_from_key(user_key)
    if extracted_uuid:
        user_repo = UserRepository(session)
        user = await user_repo.get_by_tg_id(call.from_user.id)
        if user and user.subscription_uuid != extracted_uuid:
            owner = await user_repo.get_by_subscription_uuid(extracted_uuid)
            if owner is None or owner.id == user.id:
                user.subscription_uuid = extracted_uuid
                session.add(user)
                await session.commit()

    if user_key.startswith(("https://", "http://")):
        safe_url = user_key.replace("<", "").replace(">", "")
        try:
            await call.answer(url=user_key)
        except Exception:
            await call.answer()
        await edit_or_send_banner(
            call.message,
            "🔌 <b>Ваша ссылка подписки</b>\n"
            f"<code>{safe_url}</code>\n\n"
            "Скопируйте ссылку и импортируйте в приложение.\n"
            "Сначала выберите устройство, затем приложение.\n"
            "Всего можно подключить 3 устройства одновременно.",
            reply_markup=_apps_pick_keyboard(),
            banner_path=settings.message_banner_connect_path,
        )
        return

    await edit_or_send_banner(
        call.message,
        "🔌 <b>Ваша ссылка подписки</b>\n"
        "Сначала выберите устройство, затем приложение.",
        reply_markup=_apps_pick_keyboard(),
        banner_path=settings.message_banner_connect_path,
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
    extracted_uuid = _extract_uuid_from_key(user_key)
    if extracted_uuid:
        user_repo = UserRepository(session)
        user = await user_repo.get_by_tg_id(call.from_user.id)
        if user and user.subscription_uuid != extracted_uuid:
            owner = await user_repo.get_by_subscription_uuid(extracted_uuid)
            if owner is None or owner.id == user.id:
                user.subscription_uuid = extracted_uuid
                session.add(user)
                await session.commit()

    deep_link = _build_deep_link(app, user_key)
    open_url = _telegram_safe_open_url(deep_link)

    try:
        await call.answer(url=open_url)
    except Exception:
        await call.answer()

    await edit_or_send(
        call.message,
        f"Открываю <b>{APP_TITLES[app]}</b>. Если не открылось автоматически — нажмите кнопку ниже.",
        reply_markup=_open_app_keyboard(APP_TITLES[app], deep_link),
    )


@router.callback_query(F.data.startswith("connect:device:"))
async def connect_device_guide(call: CallbackQuery) -> None:
    device = call.data.split(":")[-1]
    if device not in DEVICE_GUIDES:
        await call.answer("Раздел не найден.", show_alert=True)
        return
    await edit_or_send(call.message, DEVICE_GUIDES[device]["text"], reply_markup=_device_guide_keyboard(device))
    await call.answer()


# Backward compatibility for old messages with button "Как подключиться?"
@router.callback_query(F.data == "connect:howto")
async def connect_howto_legacy(call: CallbackQuery) -> None:
    await edit_or_send(call.message, DEVICE_GUIDES["pc"]["text"], reply_markup=_device_guide_keyboard("pc"))
    await call.answer()
