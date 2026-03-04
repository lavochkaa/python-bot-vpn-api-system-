from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.keyboards.balance import amount_keyboard, custom_amount_keyboard, payment_link_keyboard, topup_keyboard
from bot.providers.payment.factory import build_payment_provider
from bot.repositories.ledger import BalanceLedgerRepository
from bot.repositories.payment import PaymentRepository
from bot.repositories.user import UserRepository
from bot.services.payment import PaymentService
from bot.states.balance import TopUpStates
from bot.utils.messages import edit_or_send, edit_or_send_banner, send_or_answer

router = Router()


@router.callback_query(F.data == "menu:balance")
async def balance_menu(call: CallbackQuery, session: AsyncSession) -> None:
    user = await UserRepository(session).get_by_tg_id(call.from_user.id)
    await edit_or_send_banner(
        call.message,
        f"💳 Ваш баланс: <b>{user.balance} ₽</b>",
        reply_markup=topup_keyboard(),
        banner_path=settings.message_banner_balance_path,
    )
    await call.answer()


@router.callback_query(F.data == "balance:topup")
async def ask_amount(call: CallbackQuery) -> None:
    await edit_or_send(
        call.message,
        "Выберите сумму пополнения:",
        reply_markup=amount_keyboard(),
    )
    await call.answer()


@router.callback_query(F.data.startswith("balance:amount:"))
async def process_amount_callback(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    amount_token = call.data.split(":")[-1]
    if amount_token == "custom":
        await state.set_state(TopUpStates.waiting_custom_amount)
        await edit_or_send(
            call.message,
            "Введите сумму пополнения в рублях:",
            reply_markup=custom_amount_keyboard(),
        )
        await call.answer()
        return

    amount = Decimal(amount_token)
    await state.update_data(final_amount=str(amount))
    await _send_topup_invoice(call.message, call.from_user.id, state, session)
    await call.answer()


@router.message(TopUpStates.waiting_custom_amount)
async def process_custom_amount(message: Message, state: FSMContext, session: AsyncSession) -> None:
    try:
        amount = Decimal(message.text.strip().replace(",", "."))
        if amount <= 0:
            raise ValueError
    except (InvalidOperation, ValueError):
        await send_or_answer(
            message,
            "❌ Введите корректную сумму, например <b>100</b>.",
            reply_markup=custom_amount_keyboard(),
        )
        return

    await state.update_data(final_amount=str(amount))
    await _send_topup_invoice(message, message.from_user.id, state, session)


async def _send_topup_invoice(message: Message, user_id: int, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    amount = Decimal(data["final_amount"])
    payment_service = PaymentService(
        PaymentRepository(session),
        BalanceLedgerRepository(session),
        UserRepository(session),
        provider=build_payment_provider(),
    )
    try:
        invoice = await payment_service.initiate_topup(user_id=user_id, amount=amount, promo_code_id=None)
    except ValueError as exc:
        await edit_or_send(message, f"Не удалось создать платеж: {exc}")
        await state.clear()
        return

    if not invoice.pay_url:
        await edit_or_send(message, "Платеж создан, но ссылка на оплату не получена.")
        await state.clear()
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
    await state.clear()


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
