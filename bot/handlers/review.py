"""Review flow: user picks a star rating → optional text → published to review channel."""
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.repositories.user import UserRepository
from bot.states.review import ReviewStates
from bot.utils.messages import send_or_answer_banner, edit_or_send_banner

router = Router()
logger = logging.getLogger(__name__)

STARS = {1: "⭐", 2: "⭐⭐", 3: "⭐⭐⭐", 4: "⭐⭐⭐⭐", 5: "⭐⭐⭐⭐⭐"}


def _rating_keyboard():
    builder = InlineKeyboardBuilder()
    for n in range(1, 6):
        builder.button(text=STARS[n], callback_data=f"review:rate:{n}")
    builder.button(text="🔙 Отмена", callback_data="review:cancel")
    builder.adjust(1, 1, 1, 1, 1, 1)
    return builder.as_markup()


def _after_rating_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="➡️ Пропустить", callback_data="review:skip_text")
    builder.button(text="🔙 Отмена", callback_data="review:cancel")
    builder.adjust(2)
    return builder.as_markup()


def _back_to_menu_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 В меню", callback_data="menu:main")
    builder.adjust(1)
    return builder.as_markup()


async def _publish_review(
    bot,
    user_display: str,
    rating: int,
    text: str | None,
) -> None:
    """Send the review to the review channel."""
    target = settings.review_channel_id or settings.review_channel_username
    if not target:
        logger.warning("review_channel_id / review_channel_username not configured — review not published")
        return

    stars_line = STARS[rating]
    parts = [f"{stars_line}  <b>Отзыв от пользователя</b>"]
    if text:
        parts.append(f"\n{text}")
    parts.append(f"\n<i>— {user_display}</i>")

    try:
        await bot.send_message(target, "\n".join(parts))
    except TelegramAPIError as exc:
        logger.warning("Failed to publish review to channel %s: %s", target, exc)


# ── Entry point (called from menu button and from subscription handler) ──────

async def start_review(target: Message | CallbackQuery, state: FSMContext) -> None:
    """Show the rating picker. Can be called from a callback or a message."""
    await state.set_state(ReviewStates.waiting_rating)

    text = "Оцените сервис — нажмите на звёзды:"
    kb = _rating_keyboard()

    if isinstance(target, CallbackQuery):
        await edit_or_send_banner(
            target.message,
            text,
            reply_markup=kb,
            banner_path=settings.message_banner_support_path,
        )
        await target.answer()
    else:
        await send_or_answer_banner(
            target,
            text,
            reply_markup=kb,
            banner_path=settings.message_banner_support_path,
        )


# ── Menu button ──────────────────────────────────────────────────────────────

@router.callback_query(F.data == "menu:review")
async def menu_review(call: CallbackQuery, state: FSMContext) -> None:
    await start_review(call, state)


# ── Rating chosen ─────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("review:rate:"), ReviewStates.waiting_rating)
async def review_rate(call: CallbackQuery, state: FSMContext) -> None:
    rating = int(call.data.split(":")[-1])
    await state.update_data(rating=rating)
    await state.set_state(ReviewStates.waiting_text)

    await edit_or_send_banner(
        call.message,
        f"Вы поставили {STARS[rating]}\n\n"
        "Напишите пару слов о сервисе (необязательно) или нажмите «Пропустить»:",
        reply_markup=_after_rating_keyboard(),
        banner_path=settings.message_banner_support_path,
    )
    await call.answer()


# ── User typed review text ────────────────────────────────────────────────────

@router.message(ReviewStates.waiting_text)
async def review_text(message: Message, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    rating = data.get("rating", 5)

    repo = UserRepository(session)
    user, _ = await repo.get_or_create(
        tg_id=message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
    )
    display = user.username and f"@{user.username}" or user.full_name or "Аноним"

    await _publish_review(message.bot, display, rating, message.text)
    await state.clear()

    await send_or_answer_banner(
        message,
        "✅ Спасибо за отзыв! Он уже опубликован.",
        reply_markup=_back_to_menu_keyboard(),
        banner_path=settings.message_banner_support_path,
    )


# ── Skip text ─────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "review:skip_text", ReviewStates.waiting_text)
async def review_skip_text(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    rating = data.get("rating", 5)

    repo = UserRepository(session)
    user, _ = await repo.get_or_create(
        tg_id=call.from_user.id,
        username=call.from_user.username,
        full_name=call.from_user.full_name,
    )
    display = user.username and f"@{user.username}" or user.full_name or "Аноним"

    await _publish_review(call.bot, display, rating, None)
    await state.clear()

    await edit_or_send_banner(
        call.message,
        "✅ Спасибо за отзыв! Он уже опубликован.",
        reply_markup=_back_to_menu_keyboard(),
        banner_path=settings.message_banner_support_path,
    )
    await call.answer()


# ── Cancel ────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "review:cancel")
async def review_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await edit_or_send_banner(
        call.message,
        "Отменено.",
        reply_markup=_back_to_menu_keyboard(),
        banner_path=settings.message_banner_support_path,
    )
    await call.answer()
