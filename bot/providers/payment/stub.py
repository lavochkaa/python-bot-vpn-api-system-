from decimal import Decimal
from bot.providers.payment.base import PaymentProvider, PaymentResult


class StubPaymentProvider(PaymentProvider):
    """Stub payment provider for development. Replace with real gateway."""

    async def create_invoice(self, user_id: int, amount: Decimal) -> str:
        # TODO: integrate real payment gateway (YooKassa, Telegram Payments, Cryptomus, etc.)
        return f"stub_invoice_{user_id}_{amount}"

    async def check_payment(self, invoice_id: str) -> PaymentResult:
        # TODO: check real payment status via gateway API
        return PaymentResult(success=True, transaction_id=invoice_id)
