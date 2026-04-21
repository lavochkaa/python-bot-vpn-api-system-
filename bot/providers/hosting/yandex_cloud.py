"""Yandex Cloud hosting provider client — stub, not yet implemented."""
from __future__ import annotations

from bot.providers.hosting.base import HostingProviderClient, ProviderStatus


class YandexCloudClient(HostingProviderClient):
    """Stub: raises ValueError until Yandex Cloud API is fully configured."""

    def __init__(self, folder_id: str, api_key: str, billing_account_id: str | None = None) -> None:
        self._folder_id = folder_id
        self._api_key = api_key
        self._billing_account_id = billing_account_id

    async def fetch_status(self) -> ProviderStatus:
        raise ValueError("Yandex Cloud API не настроен")
