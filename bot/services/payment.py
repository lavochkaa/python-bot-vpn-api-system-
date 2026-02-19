from decimal import Decimal
from bot.db.models import BalanceLedger, Payment, PaymentKind, PaymentStatus
from bot.repositories.ledger import BalanceLedgerRepository
from bot.repositories.payment import PaymentRepository
from bot.repositories.user import UserRepository
from bot.providers.payment.base import PaymentProvider


class PaymentService:
    def __init__(
        self,
        payment_repo: PaymentRepository,
        ledger_repo: BalanceLedgerRepository,
        user_repo: UserRepository,
        provider: PaymentProvider | None,
    ):
        self.payment_repo = payment_repo
        self.ledger_repo = ledger_repo
        self.user_repo = user_repo
        self.provider = provider

    async def initiate_topup(
        self, user_id: int, amount: Decimal, promo_code_id: int | None = None
    ) -> str:
        """Create pending payment record and return invoice ID/URL."""
        if self.provider is None:
            raise ValueError("Payment provider is not configured.")
        invoice_id = await self.provider.create_invoice(user_id, amount)
        payment = Payment(
            user_id=user_id,
            amount=amount,
            kind=PaymentKind.topup,
            status=PaymentStatus.pending,
            provider_payload=invoice_id,
            promo_code_id=promo_code_id,
        )
        await self.payment_repo.save(payment)
        return invoice_id

    async def create_pending_payment(
        self,
        user_id: int,
        amount: Decimal,
        provider_payload: str,
        promo_code_id: int | None = None,
        kind: PaymentKind = PaymentKind.topup,
        plan_id: int | None = None,
    ) -> Payment:
        payment = Payment(
            user_id=user_id,
            amount=amount,
            kind=kind,
            status=PaymentStatus.pending,
            provider_payload=provider_payload,
            promo_code_id=promo_code_id,
            plan_id=plan_id,
        )
        return await self.payment_repo.save(payment)

    async def confirm_payment(self, invoice_id: str) -> bool:
        """
        Check payment with provider, update status, top up user balance.
        Returns True if payment was successfully confirmed.
        """
        if self.provider is None:
            raise ValueError("Payment provider is not configured.")
        result = await self.provider.check_payment(invoice_id)
        if not result.success:
            return False

        payment = await self.payment_repo.get_by_invoice_for_update(invoice_id)
        if not payment:
            return False
        if payment.status == PaymentStatus.completed:
            return True

        user = await self.user_repo.get_by_tg_id_for_update(payment.user_id)
        if not user:
            return False

        user.balance += payment.amount
        payment.status = PaymentStatus.completed
        payment.telegram_charge_id = result.transaction_id
        await self.payment_repo.session.commit()
        await self.payment_repo.session.refresh(payment)
        await self.user_repo.session.refresh(user)

        await self.ledger_repo.save(
            BalanceLedger(
                user_id=user.id,
                amount=payment.amount,
                reason="topup",
                payment_id=payment.id,
            )
        )
        return True

    async def confirm_telegram_payment(
        self,
        payload: str,
        telegram_charge_id: str,
    ) -> tuple[bool, Payment | None, bool]:
        existing = await self.payment_repo.get_by_telegram_charge_id(telegram_charge_id)
        if existing:
            return True, existing, False

        payment = await self.payment_repo.get_by_invoice_for_update(payload)
        if not payment:
            return False, None, False

        if payment.status == PaymentStatus.completed:
            return True, payment, False

        user = await self.user_repo.get_by_tg_id_for_update(payment.user_id)
        if not user:
            return False, None, False

        payment.status = PaymentStatus.completed
        payment.telegram_charge_id = telegram_charge_id

        if payment.kind == PaymentKind.topup:
            user.balance += payment.amount

        await self.payment_repo.session.commit()
        await self.payment_repo.session.refresh(payment)
        await self.user_repo.session.refresh(user)

        if payment.kind == PaymentKind.topup:
            await self.ledger_repo.save(
                BalanceLedger(
                    user_id=user.id,
                    amount=payment.amount,
                    reason="topup",
                    payment_id=payment.id,
                )
            )

        return True, payment, True
