"""Play2go hosting provider client.

Play2go uses Pterodactyl panel. The client API token starts with 'ptlc_'.
Base URL: https://panel.play2go.cloud/api/client

What we can get:
- Server list with names, IPs, status (running/stopped)
- Resource usage (CPU, RAM)

What we CANNOT get via Pterodactyl:
- Billing account balance (no such API endpoint)
  Balance must be entered manually in the bot.
"""
import logging
from decimal import Decimal

import aiohttp

from bot.providers.hosting.base import HostingProviderClient, ProviderStatus

logger = logging.getLogger(__name__)

_BASE_URL = "https://panel.play2go.cloud"
_TIMEOUT = aiohttp.ClientTimeout(total=15, connect=8)


class Play2goClient(HostingProviderClient):
    """Pterodactyl-based Play2go client.

    Checks server power status and returns it.
    Balance is not available via this API and remains at whatever was set manually.
    """

    def __init__(self, api_token: str, server_identifier: str | None = None) -> None:
        self._token = api_token.strip()
        self._server_id = (server_identifier or "").strip() or None

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def fetch_status(self) -> ProviderStatus:
        """Check power state of the server via Pterodactyl API."""
        if not self._server_id:
            # No server identifier — just confirm credentials are valid by listing servers
            return await self._check_account()

        url = f"{_BASE_URL}/api/client/servers/{self._server_id}/resources"
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            try:
                async with session.get(url, headers=self._headers()) as resp:
                    if resp.status == 404:
                        logger.warning(
                            "Play2go: server %s not found (404), falling back to account check",
                            self._server_id,
                        )
                        return await self._check_account()
                    if resp.status != 200:
                        logger.warning("Play2go: resources endpoint returned HTTP %s", resp.status)
                        return ProviderStatus(balance=None, is_online=None)
                    data = await resp.json()
                    state = data.get("attributes", {}).get("current_state", "")
                    is_online = state == "running"
                    return ProviderStatus(balance=None, is_online=is_online)
            except Exception as exc:
                logger.warning("Play2go: request failed: %s", exc)
                return ProviderStatus(balance=None, is_online=None)

    async def _check_account(self) -> ProviderStatus:
        """Fallback: hit /api/client to verify the token is valid."""
        url = f"{_BASE_URL}/api/client"
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            try:
                async with session.get(url, headers=self._headers()) as resp:
                    if resp.status == 200:
                        return ProviderStatus(balance=None, is_online=True)
                    logger.warning("Play2go: account check returned HTTP %s", resp.status)
                    return ProviderStatus(balance=None, is_online=None)
            except Exception as exc:
                logger.warning("Play2go: account check failed: %s", exc)
                return ProviderStatus(balance=None, is_online=None)
