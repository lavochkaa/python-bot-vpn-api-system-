"""Manual / stub hosting provider client — no API integration."""
from bot.providers.hosting.base import HostingProviderClient, ProviderStatus


class ManualClient(HostingProviderClient):
    """No-op stub for providers without API integration (firstbyte, play2go, manual)."""

    async def fetch_status(self) -> ProviderStatus:
        return ProviderStatus(balance=None, is_online=None)
