import logging
from dataclasses import dataclass
from typing import Any

import aiohttp


logger = logging.getLogger(__name__)


@dataclass
class VpnClientConfig:
    base_url: str
    api_key: str
    timeout_seconds: int = 20
    verify_ssl: bool = True


class VpnApiClient:
    def __init__(self, config: VpnClientConfig):
        if not config.base_url:
            raise ValueError("VPN_API_BASE_URL is not configured.")
        if not config.api_key:
            raise ValueError("VPN_API_KEY is not configured.")
        self.config = config

    async def create_subscription(self, user_id: int, days: int, traffic_gb: int) -> dict[str, Any]:
        payload = {
            "user_id": user_id,
            "days": days,
            "traffic_gb": traffic_gb,
        }
        return await self._request("POST", "/subscriptions", json=payload)

    async def extend_subscription(self, subscription_id: str, days: int, traffic_gb: int) -> dict[str, Any]:
        payload = {
            "days": days,
            "traffic_gb": traffic_gb,
        }
        return await self._request("POST", f"/subscriptions/{subscription_id}/extend", json=payload)

    async def get_subscription(self, subscription_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/subscriptions/{subscription_id}")

    async def _request(self, method: str, path: str, json: dict[str, Any] | None = None) -> dict[str, Any]:
        url = self.config.base_url.rstrip("/") + "/" + path.lstrip("/")
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        timeout = aiohttp.ClientTimeout(total=self.config.timeout_seconds)
        connector = aiohttp.TCPConnector(ssl=self.config.verify_ssl)

        try:
            async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
                async with session.request(method, url, headers=headers, json=json) as response:
                    text = await response.text()
                    if response.status >= 400:
                        logger.error("VPN API error %s %s: %s %s", method, url, response.status, text)
                        raise ValueError(f"VPN API error: HTTP {response.status}")
                    ctype = (response.headers.get("Content-Type") or "").lower()
                    if "application/json" in ctype:
                        return await response.json()
                    return {"raw": text}
        except aiohttp.ClientError as exc:
            logger.exception("VPN API request failed %s %s", method, url)
            raise ValueError("VPN API is unavailable.") from exc
