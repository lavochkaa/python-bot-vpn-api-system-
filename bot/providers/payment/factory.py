from bot.config import settings
from bot.providers.payment.base import PaymentProvider
from bot.providers.payment.stub import StubPaymentProvider
from bot.providers.payment.yookassa import YooKassaPaymentProvider


def build_payment_provider() -> PaymentProvider:
    provider = (settings.payment_provider or "stub").strip().lower()
    if provider == "yookassa":
        return YooKassaPaymentProvider()
    return StubPaymentProvider()
