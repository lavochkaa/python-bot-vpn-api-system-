from decimal import Decimal, InvalidOperation
from uuid import uuid4

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, LabeledPrice, Message, PreCheckoutQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.db.models import PaymentKind
from bot.keyboards.balance import amount_keyboard, custom_amount_keyboard, topup_keyboard
from bot.repositories.ledger import BalanceLedgerRepository
from bot.repositories.payment import PaymentRepository
from bot.repositories.user import UserRepository
from bot.services.payment import PaymentService
from bot.states.balance import TopUpStates
from bot.utils.messages import edit_or_send, send_or_answer

router = Router()


def _to_minor_units(amount: Decimal) -> int:
    return int((amount * Decimal("100")).quantize(Decimal("1")))


@router.callback_query(F.data == "menu:balance")
async def balance_menu(call: CallbackQuery, session: AsyncSession) -> None:
    user = await UserRepository(session).get_by_tg_id(call.from_user.id)
    await edit_or_send(
        call.message,
        f"💳 Ваш баланс: <b>{user.balance} ₽</b>",
        reply_markup=topup_keyboard(),
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
    payload = f"topup:{user_id}:{uuid4().hex}"

    payment_service = PaymentService(
        PaymentRepository(session),
        BalanceLedgerRepository(session),
        UserRepository(session),
        provider=None,  # Telegram Payments path does not use external provider adapter.
    )
    await payment_service.create_pending_payment(
        user_id=user_id,
        amount=amount,
        provider_payload=payload,
        promo_code_id=None,
        kind=PaymentKind.topup,
    )

    try:
        await message.bot.send_invoice(
            chat_id=message.chat.id,
            title="Пополнение баланса VPN-бота",
            description=f"Пополнение на {amount} ₽",
            payload=payload,
            provider_token=settings.payment_provider_token,
            currency=settings.payment_currency,
            prices=[LabeledPrice(label="Пополнение", amount=_to_minor_units(amount))],
        )
    except TelegramAPIError:
        await edit_or_send(message, "Не удалось создать счет. Проверьте PAYMENT_PROVIDER_TOKEN.")
    finally:
        await state.clear()


@router.pre_checkout_query()
async def pre_checkout(pre_checkout_query: PreCheckoutQuery) -> None:
    await pre_checkout_query.answer(ok=True)


@router.message(F.successful_payment)
async def successful_payment(message: Message, session: AsyncSession) -> None:
    successful = message.successful_payment
    payment_service = PaymentService(
        PaymentRepository(session),
        BalanceLedgerRepository(session),
        UserRepository(session),
        provider=None,
    )
    ok, payment, _processed_now = await payment_service.confirm_telegram_payment(
        payload=successful.invoice_payload,
        telegram_charge_id=successful.telegram_payment_charge_id,
    )
    if not ok or not payment:
        await send_or_answer(message, "Платеж получен, но не удалось обработать его автоматически.")
        return

    user = await UserRepository(session).get_by_tg_id(payment.user_id)
    await send_or_answer(
        message,
        f"✅ Оплата подтверждена.\n"
        f"Баланс: <b>{user.balance} ₽</b>"
    )
