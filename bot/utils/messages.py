from pathlib import Path
import logging

from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram import Bot
from aiogram.types import FSInputFile, Message

from bot.config import settings

logger = logging.getLogger(__name__)
_banner_file_id: str | None = settings.message_banner_file_id or None


def _banner_file() -> FSInputFile | None:
    path = Path(settings.message_banner_path)
    if not path.is_absolute():
        path = Path.cwd() / path
    if not path.exists():
        return None
    return FSInputFile(path)


async def send_or_answer(message: Message, text: str, reply_markup=None) -> None:
    global _banner_file_id
    if _banner_file_id:
        try:
            await message.answer_photo(photo=_banner_file_id, caption=text, reply_markup=reply_markup)
            return
        except TelegramNetworkError:
            logger.warning("Banner file_id send failed, fallback to local file/text.")
        except TelegramBadRequest:
            _banner_file_id = None

    banner = _banner_file()
    if banner:
        try:
            sent = await message.answer_photo(photo=banner, caption=text, reply_markup=reply_markup)
            if sent.photo:
                _banner_file_id = sent.photo[-1].file_id
            return
        except TelegramNetworkError:
            logger.warning("Banner photo send failed, fallback to text message.")
        except TelegramBadRequest as exc:
            logger.warning("Banner photo send failed (%s), fallback to text message.", exc)
    await message.answer(text, reply_markup=reply_markup)


async def send_to_chat(bot: Bot, chat_id: int, text: str, reply_markup=None) -> None:
    global _banner_file_id
    if _banner_file_id:
        try:
            await bot.send_photo(chat_id=chat_id, photo=_banner_file_id, caption=text, reply_markup=reply_markup)
            return
        except TelegramNetworkError:
            logger.warning("Banner file_id send_to_chat failed for chat_id=%s, fallback to local file/text.", chat_id)
        except TelegramBadRequest:
            _banner_file_id = None

    banner = _banner_file()
    if banner:
        try:
            sent = await bot.send_photo(chat_id=chat_id, photo=banner, caption=text, reply_markup=reply_markup)
            if sent.photo:
                _banner_file_id = sent.photo[-1].file_id
            return
        except TelegramNetworkError:
            logger.warning("Banner photo send_to_chat failed for chat_id=%s, fallback to text.", chat_id)
        except TelegramBadRequest as exc:
            logger.warning(
                "Banner photo send_to_chat failed for chat_id=%s (%s), fallback to text.",
                chat_id,
                exc,
            )
    await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)


async def edit_or_send(message: Message, text: str, reply_markup=None) -> None:
    global _banner_file_id
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
                await message.answer_photo(photo=_banner_file_id, caption=text, reply_markup=reply_markup)
                return
            except TelegramNetworkError:
                logger.warning("Banner file_id send after edit failed, fallback to local file/text.")
            except TelegramBadRequest:
                _banner_file_id = None
        try:
            sent = await message.answer_photo(photo=banner, caption=text, reply_markup=reply_markup)
            if sent.photo:
                _banner_file_id = sent.photo[-1].file_id
            return
        except TelegramNetworkError:
            logger.warning("Banner photo send after edit failed, fallback to text message.")
        except TelegramBadRequest as exc:
            logger.warning("Banner photo send after edit failed (%s), fallback to text message.", exc)
        await message.answer(text, reply_markup=reply_markup)
        return

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
