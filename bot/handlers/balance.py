from decimal import Decimal, InvalidOperation
import html
import re

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.db.models import PromoTarget
from bot.keyboards.balance import (
    amount_keyboard,
    custom_amount_keyboard,
    payment_link_keyboard,
    topup_keyboard,
    topup_preview_keyboard,
)
from bot.providers.payment.factory import build_payment_provider
from bot.repositories.ledger import BalanceLedgerRepository
from bot.repositories.payment import PaymentRepository
from bot.repositories.promo import PromoRepository
from bot.repositories.user import UserRepository
from bot.services.payment import PaymentService
from bot.services.promo import PromoService
from bot.states.balance import TopUpStates
from bot.utils.messages import _short_text, edit_or_send, edit_or_send_banner, send_or_answer

router = Router()


async def _edit_only(message: Message, text: str, reply_markup=None) -> None:
    rendered = _short_text(text)

    async def _try_edit_text(payload: str) -> bool:
        try:
            await message.edit_text(payload, reply_markup=reply_markup)
            return True
        except TelegramBadRequest as exc:
            err = str(exc).lower()
            if "message is not modified" in err:
                return True
            return False
        except TelegramNetworkError:
            return False

    async def _try_edit_caption(payload: str) -> bool:
        try:
            await message.edit_caption(caption=payload, reply_markup=reply_markup)
            return True
        except TelegramBadRequest as exc:
            err = str(exc).lower()
            if "message is not modified" in err:
                return True
            return False
        except TelegramNetworkError:
            return False

    if await _try_edit_text(rendered):
        return
    if await _try_edit_caption(rendered):
        return

    safe_plain = html.escape(re.sub(r"<[^>]+>", "", text))
    safe_rendered = _short_text(safe_plain)

    if await _try_edit_text(safe_rendered):
        return
    if await _try_edit_caption(safe_rendered):
        return

    try:
        await message.answer(safe_rendered, reply_markup=reply_markup)
    except (TelegramBadRequest, TelegramNetworkError):
        return


async def _edit_only_by_ids(bot, chat_id: int, message_id: int, text: str, reply_markup=None) -> None:
    rendered = _short_text(text)
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=rendered,
            reply_markup=reply_markup,
        )
        return
    except TelegramBadRequest as exc:
        err = str(exc).lower()
        if "message is not modified" in err:
            return
    except TelegramNetworkError:
        return

    try:
        await bot.edit_message_caption(
            chat_id=chat_id,
            message_id=message_id,
            caption=rendered,
            reply_markup=reply_markup,
        )
        return
    except TelegramBadRequest as exc:
        err = str(exc).lower()
        if "message is not modified" in err:
            return
    except TelegramNetworkError:
        return

    safe_plain = html.escape(re.sub(r"<[^>]+>", "", text))
    safe_rendered = _short_text(safe_plain)
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=safe_rendered,
            reply_markup=reply_markup,
        )
        return
    except TelegramBadRequest:
        pass
    except TelegramNetworkError:
        return
    try:
        await bot.edit_message_caption(
            chat_id=chat_id,
            message_id=message_id,
            caption=safe_rendered,
            reply_markup=reply_markup,
        )
    except (TelegramBadRequest, TelegramNetworkError):
        return


async def _render_amount_picker(message: Message, state: FSMContext) -> None:
    await state.update_data(
        topup_chat_id=message.chat.id,
        topup_message_id=message.message_id,
    )
    await _edit_only(
        message,
        "Выберите сумму пополнения:",
        reply_markup=amount_keyboard(),
    )


async def _render_custom_amount_prompt(message: Message, state: FSMContext) -> None:
    await state.update_data(
        topup_chat_id=message.chat.id,
        topup_message_id=message.message_id,
    )
    await _edit_only(
        message,
        "Введите сумму пополнения в рублях:",
        reply_markup=custom_amount_keyboard(),
    )


async def _edit_topup_screen(state: FSMContext, message: Message, text: str, reply_markup=None) -> None:
    data = await state.get_data()
    chat_id = int(data.get("topup_chat_id", 0) or 0)
    message_id = int(data.get("topup_message_id", 0) or 0)
    if chat_id and message_id:
        await _edit_only_by_ids(message.bot, chat_id, message_id, text, reply_markup=reply_markup)
        return
    await send_or_answer(message, text, reply_markup=reply_markup)


def _promo_line(data: dict) -> str:
    code = data.get("topup_promo_code")
    if not code:
        return "не применен"
    return f"{code}"


def _topup_preview_text(base_amount: Decimal, final_amount: Decimal, promo_code: str | None) -> str:
    if promo_code and final_amount != base_amount:
        return (
            "💳 <b>Пополнение баланса</b>\n\n"
            f"Сумма без скидки: <b>{base_amount} ₽</b>\n"
            f"Промокод: <b>{promo_code}</b>\n"
            f"К оплате: <b>{final_amount} ₽</b>"
        )
    return (
        "💳 <b>Пополнение баланса</b>\n\n"
        f"К оплате: <b>{final_amount} ₽</b>\n"
        f"Промокод: <b>{promo_code or 'не применен'}</b>"
    )


async def _render_topup_preview(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    base_amount = Decimal(str(data.get("topup_base_amount", "0")))
    final_amount = Decimal(str(data.get("topup_final_amount", "0")))
    promo_code = data.get("topup_promo_code")
    await state.update_data(
        topup_chat_id=message.chat.id,
        topup_message_id=message.message_id,
    )
    await _edit_only(
        message,
        _topup_preview_text(base_amount, final_amount, promo_code),
        reply_markup=topup_preview_keyboard(has_promo=bool(promo_code)),
    )


@router.callback_query(F.data == "menu:balance")
async def balance_menu(call: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    user = await UserRepository(session).get_by_tg_id(call.from_user.id)
    await edit_or_send_banner(
        call.message,
        f"💳 Ваш баланс: <b>{user.balance} ₽</b>",
        reply_markup=topup_keyboard(),
        banner_path=settings.message_banner_balance_path,
    )
    await call.answer()


@router.callback_query(F.data == "balance:topup")
async def ask_amount(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _render_amount_picker(call.message, state)
    await call.answer()


@router.callback_query(F.data.startswith("balance:amount:"))
async def process_amount_callback(call: CallbackQuery, state: FSMContext) -> None:
    amount_token = call.data.split(":")[-1]
    if amount_token == "custom":
        await state.set_state(TopUpStates.waiting_custom_amount)
        await _render_custom_amount_prompt(call.message, state)
        await call.answer()
        return

    amount = Decimal(amount_token)
    await state.update_data(
        topup_base_amount=str(amount),
        topup_final_amount=str(amount),
        topup_promo_code="",
        topup_promo_id=None,
    )
    await _render_topup_preview(call.message, state)
    await call.answer()


@router.message(TopUpStates.waiting_custom_amount)
async def process_custom_amount(message: Message, state: FSMContext) -> None:
    try:
        raw_text = (message.text or "").strip()
        amount = Decimal(raw_text.replace(",", "."))
        if amount <= 0:
            raise ValueError
    except (InvalidOperation, ValueError):
        await _edit_topup_screen(
            state,
            message,
            "❌ Введите корректную сумму, например <b>100</b>.",
            reply_markup=custom_amount_keyboard(),
        )
        return

    await state.set_state(None)
    await state.update_data(
        topup_base_amount=str(amount),
        topup_final_amount=str(amount),
        topup_promo_code="",
        topup_promo_id=None,
    )
    await _edit_topup_screen(
        state,
        message,
        _topup_preview_text(amount, amount, None),
        reply_markup=topup_preview_keyboard(has_promo=False),
    )


@router.callback_query(F.data == "balance:promo:enter")
async def topup_promo_enter(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("topup_base_amount"):
        await call.answer("Сначала выберите сумму пополнения.", show_alert=True)
        return
    await state.set_state(TopUpStates.waiting_promo_code)
    await _edit_only(call.message, "Введите промокод для пополнения:", reply_markup=custom_amount_keyboard())
    await state.update_data(
        topup_chat_id=call.message.chat.id,
        topup_message_id=call.message.message_id,
    )
    await call.answer()


@router.callback_query(F.data == "balance:promo:clear")
async def topup_promo_clear(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    base_raw = data.get("topup_base_amount")
    if not base_raw:
        await call.answer("Сначала выберите сумму.", show_alert=True)
        return
    base_amount = Decimal(str(base_raw))
    await state.update_data(
        topup_final_amount=str(base_amount),
        topup_promo_code="",
        topup_promo_id=None,
    )
    await _render_topup_preview(call.message, state)
    await call.answer("Промокод удален")


@router.message(TopUpStates.waiting_promo_code)
async def topup_promo_apply(message: Message, state: FSMContext, session: AsyncSession) -> None:
    code = (message.text or "").strip().upper()
    data = await state.get_data()
    base_raw = data.get("topup_base_amount")
    if not base_raw:
        await state.clear()
        await _edit_topup_screen(state, message, "Сессия истекла. Начните пополнение заново.")
        return

    if not code:
        await _edit_topup_screen(state, message, "Введите промокод.")
        return

    base_amount = Decimal(str(base_raw))
    service = PromoService(PromoRepository(session), UserRepository(session))
    try:
        final_amount, promo = await service.validate_and_apply(
            code=code,
            user_id=message.from_user.id,
            base_amount=base_amount,
            target=PromoTarget.balance,
        )
    except ValueError as exc:
        await _edit_topup_screen(state, message, f"❌ {exc}", reply_markup=custom_amount_keyboard())
        return

    await state.set_state(None)
    await state.update_data(
        topup_final_amount=str(final_amount),
        topup_promo_code=promo.code if promo else "",
        topup_promo_id=promo.id if promo else None,
    )

    await _edit_topup_screen(
        state,
        message,
        _topup_preview_text(base_amount, final_amount, promo.code if promo else None),
        reply_markup=topup_preview_keyboard(has_promo=bool(promo)),
    )


@router.callback_query(F.data == "balance:pay")
async def topup_pay(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    final_raw = data.get("topup_final_amount")
    base_raw = data.get("topup_base_amount")
    if not final_raw or not base_raw:
        await call.answer("Сначала выберите сумму пополнения.", show_alert=True)
        return

    amount = Decimal(str(final_raw))
    promo_id = data.get("topup_promo_id")
    await _send_topup_invoice(call.message, call.from_user.id, amount, promo_id, session)
    await state.clear()
    await call.answer()


async def _send_topup_invoice(
    message: Message,
    user_id: int,
    amount: Decimal,
    promo_id: int | None,
    session: AsyncSession,
) -> None:
    payment_service = PaymentService(
        PaymentRepository(session),
        BalanceLedgerRepository(session),
        UserRepository(session),
        provider=build_payment_provider(),
    )
    try:
        invoice = await payment_service.initiate_topup(user_id=user_id, amount=amount, promo_code_id=promo_id)
    except ValueError as exc:
        await edit_or_send(message, f"Не удалось создать платеж: {exc}")
        return

    if not invoice.pay_url:
        await edit_or_send(message, "Платеж создан, но ссылка на оплату не получена.")
        return

    await edit_or_send(
        message,
        (
            f"Сумма к оплате: <b>{amount} ₽</b>\n\n"
            "1) Нажмите «Оплатить».\n"
            "2) После оплаты нажмите «Проверить оплату»."
        ),
        reply_markup=payment_link_keyboard(pay_url=invoice.pay_url, invoice_id=invoice.invoice_id),
    )


@router.callback_query(F.data.startswith("balance:check:"))
async def check_topup_payment(call: CallbackQuery, session: AsyncSession) -> None:
    invoice_id = call.data.removeprefix("balance:check:").strip()
    if not invoice_id:
        await call.answer("Некорректный id платежа.", show_alert=True)
        return

    payment_service = PaymentService(
        PaymentRepository(session),
        BalanceLedgerRepository(session),
        UserRepository(session),
        provider=build_payment_provider(),
    )
    try:
        ok = await payment_service.confirm_payment(invoice_id)
    except ValueError as exc:
        await call.answer("Ошибка проверки платежа.", show_alert=True)
        await edit_or_send(call.message, f"Не удалось проверить платеж: {exc}")
        return

    if not ok:
        await call.answer("Платеж еще не подтвержден.", show_alert=True)
        return

    user = await UserRepository(session).get_by_tg_id(call.from_user.id)
    await edit_or_send_banner(
        call.message,
        f"✅ Оплата подтверждена.\nБаланс: <b>{user.balance} ₽</b>",
        reply_markup=topup_keyboard(),
        banner_path=settings.message_banner_payment_success_path,
    )
    await call.answer("Оплата подтверждена")
