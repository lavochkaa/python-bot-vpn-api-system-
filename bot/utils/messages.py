from pathlib import Path
import logging
import html
import re

from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram import Bot
from aiogram.types import FSInputFile, Message

from bot.config import settings

logger = logging.getLogger(__name__)

def _looks_like_telegram_file_id(value: str) -> bool:
    # Telegram file_id is an opaque token, not a local filename/path.
    val = value.strip()
    if not val:
        return False
    if "/" in val or "\\" in val:
        return False
    lowered = val.lower()
    for suffix in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"):
        if lowered.endswith(suffix):
            return False
    return True


_banner_file_id: str | None = (
    settings.message_banner_file_id.strip()
    if settings.message_banner_file_id and _looks_like_telegram_file_id(settings.message_banner_file_id)
    else None
)
_banner_disabled: bool = False
_PHOTO_TIMEOUT_SECONDS = 8
_CAPTION_MAX_LEN = 1024
_TEXT_MAX_LEN = 4096


def _short_text(text: str, limit: int = _TEXT_MAX_LEN) -> str:
    if len(text) <= limit:
        return text
    suffix = "\n\n[Текст сокращен из-за лимита Telegram]"
    keep = max(0, limit - len(suffix))
    # If message contains HTML tags, truncation can break entities (<code>...</code>).
    # Convert to safe plain text for over-limit fallback.
    if "<" in text and ">" in text:
        plain = re.sub(r"<[^>]+>", "", text)
        plain = html.escape(plain[:keep])
        return plain + suffix
    return text[:keep] + suffix


def _banner_file() -> FSInputFile | None:
    path = Path(settings.message_banner_path)
    if not settings.message_banner_path.strip():
        return None
    if not path.is_absolute():
        path = Path.cwd() / path
    if not path.exists() or not path.is_file():
        return None
    return FSInputFile(path)


async def send_or_answer(message: Message, text: str, reply_markup=None) -> None:
    global _banner_file_id, _banner_disabled
    if len(text) > _CAPTION_MAX_LEN:
        try:
            await message.answer(_short_text(text), reply_markup=reply_markup)
        except TelegramBadRequest as exc:
            if "can't parse entities" in str(exc).lower():
                safe_plain = html.escape(re.sub(r"<[^>]+>", "", text))
                await message.answer(_short_text(safe_plain), reply_markup=reply_markup)
                return
            raise
        return
    if _banner_disabled:
        await message.answer(text, reply_markup=reply_markup)
        return
    if _banner_file_id:
        try:
            await message.answer_photo(
                photo=_banner_file_id,
                caption=text,
                reply_markup=reply_markup,
                request_timeout=_PHOTO_TIMEOUT_SECONDS,
            )
            return
        except TelegramNetworkError:
            logger.warning("Banner file_id send failed, fallback to local file/text.")
        except TelegramBadRequest:
            _banner_file_id = None

    banner = _banner_file()
    if banner:
        try:
            sent = await message.answer_photo(
                photo=banner,
                caption=text,
                reply_markup=reply_markup,
                request_timeout=_PHOTO_TIMEOUT_SECONDS,
            )
            if sent.photo:
                _banner_file_id = sent.photo[-1].file_id
            return
        except TelegramNetworkError:
            logger.warning("Banner photo send failed, fallback to text message.")
            _banner_disabled = True
        except TelegramBadRequest as exc:
            logger.warning("Banner photo send failed (%s), fallback to text message.", exc)
            _banner_disabled = True
        except OSError as exc:
            logger.warning("Banner photo send OS error (%s), disable banner and fallback to text.", exc)
            _banner_disabled = True
    try:
        await message.answer(_short_text(text), reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "can't parse entities" in str(exc).lower():
            safe_plain = html.escape(re.sub(r"<[^>]+>", "", text))
            await message.answer(_short_text(safe_plain), reply_markup=reply_markup)
            return
        raise


async def send_to_chat(bot: Bot, chat_id: int, text: str, reply_markup=None) -> None:
    global _banner_file_id, _banner_disabled
    if len(text) > _CAPTION_MAX_LEN:
        try:
            await bot.send_message(chat_id=chat_id, text=_short_text(text), reply_markup=reply_markup)
        except TelegramBadRequest as exc:
            if "can't parse entities" in str(exc).lower():
                safe_plain = html.escape(re.sub(r"<[^>]+>", "", text))
                await bot.send_message(chat_id=chat_id, text=_short_text(safe_plain), reply_markup=reply_markup)
                return
            raise
        return
    if _banner_disabled:
        await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
        return
    if _banner_file_id:
        try:
            await bot.send_photo(
                chat_id=chat_id,
                photo=_banner_file_id,
                caption=text,
                reply_markup=reply_markup,
                request_timeout=_PHOTO_TIMEOUT_SECONDS,
            )
            return
        except TelegramNetworkError:
            logger.warning("Banner file_id send_to_chat failed for chat_id=%s, fallback to local file/text.", chat_id)
        except TelegramBadRequest:
            _banner_file_id = None

    banner = _banner_file()
    if banner:
        try:
            sent = await bot.send_photo(
                chat_id=chat_id,
                photo=banner,
                caption=text,
                reply_markup=reply_markup,
                request_timeout=_PHOTO_TIMEOUT_SECONDS,
            )
            if sent.photo:
                _banner_file_id = sent.photo[-1].file_id
            return
        except TelegramNetworkError:
            logger.warning("Banner photo send_to_chat failed for chat_id=%s, fallback to text.", chat_id)
            _banner_disabled = True
        except TelegramBadRequest as exc:
            logger.warning(
                "Banner photo send_to_chat failed for chat_id=%s (%s), fallback to text.",
                chat_id,
                exc,
            )
            _banner_disabled = True
        except OSError as exc:
            logger.warning("Banner send_to_chat OS error for chat_id=%s (%s), disable banner.", chat_id, exc)
            _banner_disabled = True
    try:
        await bot.send_message(chat_id=chat_id, text=_short_text(text), reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "can't parse entities" in str(exc).lower():
            safe_plain = html.escape(re.sub(r"<[^>]+>", "", text))
            await bot.send_message(chat_id=chat_id, text=_short_text(safe_plain), reply_markup=reply_markup)
            return
        raise


async def edit_or_send(message: Message, text: str, reply_markup=None) -> None:
    global _banner_file_id, _banner_disabled
    if len(text) > _CAPTION_MAX_LEN:
        safe_text = _short_text(text)
        try:
            await message.edit_text(safe_text, reply_markup=reply_markup)
        except TelegramBadRequest as exc:
            err = str(exc).lower()
            if "message is not modified" in err:
                return
            if "can't parse entities" in err:
                safe_plain = html.escape(re.sub(r"<[^>]+>", "", text))
                await message.answer(_short_text(safe_plain), reply_markup=reply_markup)
                return
            await message.answer(safe_text, reply_markup=reply_markup)
        return
    if _banner_disabled:
        try:
            await message.edit_text(text, reply_markup=reply_markup)
        except TelegramBadRequest as exc:
            err = str(exc).lower()
            if "message is not modified" in err:
                return
            if "there is no text in the message to edit" in err or "message can't be edited" in err:
                await message.answer(text, reply_markup=reply_markup)
                return
            raise
        return
    banner = _banner_file()
    if banner:
        try:
            await message.edit_caption(caption=text, reply_markup=reply_markup)
            return
        except TelegramBadRequest as exc:
            err = str(exc).lower()
            if "message is not modified" in err:
                return
            if "message can't be edited" not in err and "there is no caption in the message to edit" not in err:
                raise
        except TelegramNetworkError:
            logger.warning("Banner caption edit failed, fallback to plain text flow.")
        if _banner_file_id:
            try:
                await message.answer_photo(
                    photo=_banner_file_id,
                    caption=text,
                    reply_markup=reply_markup,
                    request_timeout=_PHOTO_TIMEOUT_SECONDS,
                )
                return
            except TelegramNetworkError:
                logger.warning("Banner file_id send after edit failed, fallback to local file/text.")
            except TelegramBadRequest:
                _banner_file_id = None
        try:
            sent = await message.answer_photo(
                photo=banner,
                caption=text,
                reply_markup=reply_markup,
                request_timeout=_PHOTO_TIMEOUT_SECONDS,
            )
            if sent.photo:
                _banner_file_id = sent.photo[-1].file_id
            return
        except TelegramNetworkError:
            logger.warning("Banner photo send after edit failed, fallback to text message.")
            _banner_disabled = True
        except TelegramBadRequest as exc:
            logger.warning("Banner photo send after edit failed (%s), fallback to text message.", exc)
            _banner_disabled = True
        except OSError as exc:
            logger.warning("Banner photo send after edit OS error (%s), disable banner.", exc)
            _banner_disabled = True
        await message.answer(text, reply_markup=reply_markup)
        return

    try:
        await message.edit_text(_short_text(text), reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        err = str(exc).lower()
        if "message is not modified" in err:
            return
        if "there is no text in the message to edit" in err or "message can't be edited" in err:
            await message.answer(_short_text(text), reply_markup=reply_markup)
            return
        if "can't parse entities" in err:
            safe_plain = html.escape(re.sub(r"<[^>]+>", "", text))
            await message.answer(_short_text(safe_plain), reply_markup=reply_markup)
            return
        if "text is too long" in err:
            await message.answer(_short_text(text), reply_markup=reply_markup)
            return
        raise
