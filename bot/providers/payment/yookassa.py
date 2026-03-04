import base64
import json
from decimal import Decimal
from uuid import uuid4

import aiohttp

from bot.config import settings
from bot.providers.payment.base import PaymentInvoice, PaymentProvider, PaymentResult


class YooKassaPaymentProvider(PaymentProvider):
    def __init__(self) -> None:
        self.api_base = (settings.yookassa_api_base or "https://api.yookassa.ru/v3").rstrip("/")
        self.shop_id = settings.yookassa_shop_id.strip()
        self.secret_key = settings.yookassa_secret_key.strip()
        self.currency = (settings.payment_currency or "RUB").strip().upper()
        self.return_url = (settings.yookassa_return_url or "https://t.me").strip()

        if not self.shop_id:
            raise ValueError("YOOKASSA_SHOP_ID is not configured.")
        if not self.secret_key:
            raise ValueError("YOOKASSA_SECRET_KEY is not configured.")

    async def create_invoice(self, user_id: int, amount: Decimal) -> PaymentInvoice:
        payload: dict[str, object] = {
            "amount": {
                "value": f"{amount.quantize(Decimal('0.01'))}",
                "currency": self.currency,
            },
            "capture": True,
            "confirmation": {
                "type": "redirect",
                "return_url": self.return_url,
            },
            "description": f"Top up balance for Telegram user {user_id}",
            "metadata": {"user_id": str(user_id)},
        }
        extra = self._load_extra_provider_data()
        if extra:
            payload.update(extra)

        response = await self._request(
            "POST",
            "/payments",
            json_data=payload,
            idempotence_key=str(uuid4()),
        )
        payment_id = str(response.get("id") or "").strip()
        confirmation = response.get("confirmation") or {}
        confirmation_url = None
        if isinstance(confirmation, dict):
            raw_url = confirmation.get("confirmation_url")
            if isinstance(raw_url, str) and raw_url.strip():
                confirmation_url = raw_url.strip()
        if not payment_id:
            raise ValueError("YooKassa create payment failed: no payment id in response.")
        if not confirmation_url:
            raise ValueError("YooKassa create payment failed: no confirmation_url in response.")
        return PaymentInvoice(invoice_id=payment_id, pay_url=confirmation_url)

    async def check_payment(self, invoice_id: str) -> PaymentResult:
        payment_id = invoice_id.strip()
        if not payment_id:
            return PaymentResult(success=False, error="Missing payment id.")
        response = await self._request("GET", f"/payments/{payment_id}")
        status = str(response.get("status") or "").lower()
        if status == "succeeded":
            return PaymentResult(success=True, transaction_id=payment_id)
        if status == "canceled":
            return PaymentResult(success=False, error="Payment was canceled.")
        return PaymentResult(success=False, error=f"Payment status: {status or 'unknown'}")

    async def _request(
        self,
        method: str,
        path: str,
        json_data: dict[str, object] | None = None,
        idempotence_key: str | None = None,
    ) -> dict:
        headers = {
            "Authorization": f"Basic {self._basic_token()}",
            "Content-Type": "application/json",
        }
        if idempotence_key:
            headers["Idempotence-Key"] = idempotence_key

        url = f"{self.api_base}/{path.lstrip('/')}"
        timeout = aiohttp.ClientTimeout(total=max(10, int(settings.vpn_api_timeout_seconds or 20)))
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.request(method, url, headers=headers, json=json_data) as response:
                body = await response.text()
                if response.status >= 400:
                    raise ValueError(f"YooKassa API error {response.status}: {self._short(body)}")
                try:
                    data = json.loads(body)
                except json.JSONDecodeError as exc:
                    raise ValueError("YooKassa API returned invalid JSON.") from exc
                if not isinstance(data, dict):
                    raise ValueError("YooKassa API returned unexpected payload.")
                return data

    def _basic_token(self) -> str:
        raw = f"{self.shop_id}:{self.secret_key}".encode("utf-8")
        return base64.b64encode(raw).decode("ascii")

    def _load_extra_provider_data(self) -> dict[str, object]:
        raw = (settings.payment_provider_data or "").strip()
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("PAYMENT_PROVIDER_DATA must be valid JSON.") from exc
        if not isinstance(parsed, dict):
            raise ValueError("PAYMENT_PROVIDER_DATA must be a JSON object.")
        return parsed

    @staticmethod
    def _short(value: str, limit: int = 240) -> str:
        cleaned = " ".join(value.split())
        if len(cleaned) <= limit:
            return cleaned
        return cleaned[:limit] + "..."
