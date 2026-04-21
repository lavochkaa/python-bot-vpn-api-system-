"""Beget hosting provider client."""
import logging
from decimal import Decimal

import aiohttp

from bot.providers.hosting.base import HostingProviderClient, ProviderStatus

logger = logging.getLogger(__name__)

_BEGET_BASE = "https://api.beget.com/api"
_TIMEOUT = aiohttp.ClientTimeout(total=15, connect=8)


class BegetClient(HostingProviderClient):
    """Fetches account balance from Beget API."""

    def __init__(self, login: str, password: str) -> None:
        self._login = login
        self._password = password

    async def fetch_status(self) -> ProviderStatus:
        url = f"{_BEGET_BASE}/user/getAccountInfo"
        params = {
            "login": self._login,
            "passwd": self._password,
            "output_format": "json",
        }
        try:
            async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
                async with session.get(url, params=params) as resp:
                    if resp.status != 200:
                        logger.warning(
                            "Beget API returned HTTP %s for login=%s", resp.status, self._login
                        )
                        return ProviderStatus(balance=None, is_online=None)
                    data = await resp.json(content_type=None)
        except Exception as exc:
            logger.warning("Beget API request failed for login=%s: %s", self._login, exc)
            return ProviderStatus(balance=None, is_online=None)

        try:
            outer_status = data.get("status")
            if outer_status != "success":
                logger.warning("Beget API outer status=%s for login=%s", outer_status, self._login)
                return ProviderStatus(balance=None, is_online=None)
            answer = data["answer"]
            if answer.get("status") != "success":
                logger.warning(
                    "Beget API answer status=%s for login=%s", answer.get("status"), self._login
                )
                return ProviderStatus(balance=None, is_online=None)
            raw_balance = answer["result"]["user_balance"]
            balance = Decimal(str(raw_balance))
            return ProviderStatus(balance=balance, is_online=True)
        except (KeyError, TypeError, Exception) as exc:
            logger.warning("Beget API parse error for login=%s: %s", self._login, exc)
            return ProviderStatus(balance=None, is_online=None)
