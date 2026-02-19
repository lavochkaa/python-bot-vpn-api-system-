from abc import ABC, abstractmethod
from decimal import Decimal
from dataclasses import dataclass


@dataclass
class PaymentResult:
    success: bool
    transaction_id: str | None = None
    error: str | None = None


class PaymentProvider(ABC):
    @abstractmethod
    async def create_invoice(self, user_id: int, amount: Decimal) -> str:
        """Create a payment invoice. Returns payment URL or invoice ID."""

    @abstractmethod
    async def check_payment(self, invoice_id: str) -> PaymentResult:
        """Check payment status by invoice ID."""
