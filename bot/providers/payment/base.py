from abc import ABC, abstractmethod
from decimal import Decimal
from dataclasses import dataclass


@dataclass
class PaymentInvoice:
    invoice_id: str
    pay_url: str | None = None


@dataclass
class PaymentResult:
    success: bool
    transaction_id: str | None = None
    error: str | None = None


class PaymentProvider(ABC):
    @abstractmethod
    async def create_invoice(self, user_id: int, amount: Decimal) -> PaymentInvoice:
        """Create invoice and return provider payment id (+ optional URL)."""

    @abstractmethod
    async def check_payment(self, invoice_id: str) -> PaymentResult:
        """Check payment status by invoice ID."""
