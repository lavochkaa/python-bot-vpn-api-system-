import logging
import socket
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp


logger = logging.getLogger(__name__)


@dataclass
class VpnClientConfig:
    base_url: str
    api_key: str
    timeout_seconds: int = 20
    verify_ssl: bool = True
    force_ipv4: bool = True


class VpnApiClient:
    _USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/145.0.0.0 Safari/537.36"
    )

    def __init__(self, config: VpnClientConfig):
        if not config.base_url:
            raise ValueError("VPN_API_BASE_URL is not configured.")
        if not config.api_key:
            raise ValueError("VPN_API_KEY is not configured.")
        self.config = config

    async def create_or_update_subscription(
        self,
        *,
        user_id: int,
        username: str,
        days: int,
        traffic_gb: int,
        device_limit: int,
        traffic_reset_strategy: str,
        internal_squad_uuid: str | None = None,
    ) -> dict[str, Any]:
        existing = await self.find_user(telegram_id=user_id, username=username)
        now = datetime.now(timezone.utc)
        expire_at = now + timedelta(days=days)
        traffic_limit_bytes = self._gb_to_bytes(traffic_gb)

        if existing:
            existing_uuid = str(existing.get("uuid") or "").strip()
            current_expire = self._parse_datetime(
                existing.get("expireAt")
                or existing.get("expiresAt")
                or existing.get("expire_at")
            )
            used_bytes = self._to_int(
                existing.get("trafficUsedBytes")
                or existing.get("usedTrafficBytes")
                or existing.get("usedTraffic")
            ) or 0
            if current_expire and current_expire > now:
                expire_at = current_expire + timedelta(days=days)
            traffic_limit_bytes = used_bytes + traffic_limit_bytes
            payload = {
                "uuid": existing_uuid,
                "username": existing.get("username") or username,
                "status": "ACTIVE",
                "trafficLimitBytes": traffic_limit_bytes,
                "trafficLimitStrategy": traffic_reset_strategy,
                "expireAt": expire_at.isoformat(),
                "telegramId": user_id,
                "hwidDeviceLimit": device_limit,
            }
            if internal_squad_uuid:
                payload["activeInternalSquads"] = [{"uuid": internal_squad_uuid}]
            update_attempts: tuple[tuple[str, str], ...] = (
                ("PATCH", f"/api/users/{existing_uuid}"),
                ("PUT", f"/api/users/{existing_uuid}"),
                ("PATCH", "/api/users"),
                ("PUT", "/api/users"),
            )
            return await self._request_method_path_fallback(update_attempts, json=payload)

        payload = {
            "username": username,
            "status": "ACTIVE",
            "trafficLimitBytes": traffic_limit_bytes,
            "trafficLimitStrategy": traffic_reset_strategy,
            "expireAt": expire_at.isoformat(),
            "telegramId": user_id,
            "hwidDeviceLimit": device_limit,
        }
        if internal_squad_uuid:
            payload["activeInternalSquads"] = [{"uuid": internal_squad_uuid}]
        return await self._request_with_fallback(
            "POST",
            ("/users", "/api/users"),
            json=payload,
        )

    async def find_user(
        self,
        *,
        telegram_id: int | None = None,
        username: str | None = None,
        uuid: str | None = None,
    ) -> dict[str, Any] | None:
        if uuid:
            for path in (f"/api/users/{uuid}",):
                try:
                    return await self._request("GET", path)
                except ValueError:
                    continue

        users = await self._list_users()
        for user in users:
            if not isinstance(user, dict):
                continue
            if uuid and str(user.get("uuid") or "").strip() == uuid:
                return user
            if telegram_id is not None and self._to_int(user.get("telegramId")) == int(telegram_id):
                return user
            if username and str(user.get("username") or "").strip() == username:
                return user
        return None

    async def get_user_usage(self, *, uuid: str | None = None, telegram_id: int | None = None, username: str | None = None) -> dict[str, Any] | None:
        user = await self.find_user(uuid=uuid, telegram_id=telegram_id, username=username)
        if not user:
            return None
        used_bytes = self._to_int(
            user.get("trafficUsedBytes")
            or user.get("usedTrafficBytes")
            or user.get("usedTraffic")
        )
        limit_bytes = self._to_int(user.get("trafficLimitBytes") or user.get("limitTrafficBytes"))
        if used_bytes is None and limit_bytes is None:
            return None
        return {
            "current_usage_gb": self._bytes_to_gb(used_bytes),
            "usage_limit_gb": self._bytes_to_gb(limit_bytes),
        }

    async def reset_user_traffic(self, uuid: str) -> bool:
        for method, path in (
            ("POST", f"/users/{uuid}/reset-traffic"),
            ("POST", f"/api/users/{uuid}/reset-traffic"),
            ("POST", f"/users/reset-traffic/{uuid}"),
            ("POST", f"/api/users/reset-traffic/{uuid}"),
        ):
            try:
                await self._request(method, path)
                return True
            except ValueError:
                continue
        return False

    def build_subscription_url(self, payload: dict[str, Any]) -> str | None:
        direct = (
            payload.get("subscriptionUrl")
            or payload.get("subscriptionURL")
            or payload.get("subscription_url")
            or payload.get("url")
            or payload.get("link")
        )
        if direct:
            return str(direct)
        return None

    async def _request(self, method: str, path: str, json: dict[str, Any] | None = None) -> dict[str, Any]:
        url = self.config.base_url.rstrip("/") + "/" + path.lstrip("/")
        timeout = aiohttp.ClientTimeout(total=self.config.timeout_seconds)
        connector = aiohttp.TCPConnector(
            ssl=self.config.verify_ssl,
            family=socket.AF_INET if self.config.force_ipv4 else socket.AF_UNSPEC,
            enable_cleanup_closed=True,
        )
        headers_candidates = self._build_headers_candidates(method, json=json)
        last_error: Exception | None = None

        try:
            async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
                for headers in headers_candidates:
                    try:
                        async with session.request(method, url, headers=headers, json=json) as response:
                            text = await response.text()
                            if response.status >= 400:
                                logger.error("VPN API error %s %s: %s %s", method, url, response.status, text)
                                raise ValueError(f"VPN API error: HTTP {response.status}")
                            ctype = (response.headers.get("Content-Type") or "").lower()
                            if "application/json" in ctype:
                                data = await response.json()
                                return self._unwrap_response(data)
                            return {"raw": text}
                    except ValueError:
                        raise
                    except aiohttp.ClientError as exc:
                        last_error = exc
                        logger.warning("VPN API transport retry %s %s via alternate headers: %s", method, url, exc)
                        continue
        except aiohttp.ClientError as exc:
            last_error = exc

        if last_error is not None:
            logger.exception("VPN API request failed %s %s", method, url, exc_info=last_error)
            raise ValueError("VPN API is unavailable.") from last_error
        raise ValueError("VPN API is unavailable.")

    def _build_headers_candidates(self, method: str, *, json: dict[str, Any] | None = None) -> tuple[dict[str, str], ...]:
        base_headers = {
            "Accept": "application/json, text/plain, */*",
            "User-Agent": self._USER_AGENT,
            "Connection": "close",
        }
        if json is not None and method.upper() in {"POST", "PUT", "PATCH"}:
            base_headers["Content-Type"] = "application/json"

        return (
            {**base_headers, "Authorization": f"Bearer {self.config.api_key}", "X-API-Key": self.config.api_key},
            {**base_headers, "Authorization": f"Bearer {self.config.api_key}"},
            {**base_headers, "X-API-Key": self.config.api_key},
        )

    async def _request_with_fallback(
        self,
        method: str,
        paths: tuple[str, ...],
        *,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        last_error: ValueError | None = None
        for path in paths:
            try:
                return await self._request(method, path, json=json)
            except ValueError as exc:
                last_error = exc
                continue
        if last_error is not None:
            raise last_error
        raise ValueError("VPN API request failed.")

    async def _request_method_path_fallback(
        self,
        attempts: tuple[tuple[str, str], ...],
        *,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        last_error: ValueError | None = None
        for method, path in attempts:
            try:
                return await self._request(method, path, json=json)
            except ValueError as exc:
                last_error = exc
                continue
        if last_error is not None:
            raise last_error
        raise ValueError("VPN API request failed.")

    async def _list_users(self) -> list[dict[str, Any]]:
        for path in ("/api/users",):
            try:
                data = await self._request("GET", path)
            except ValueError:
                continue
            users = data.get("users") if isinstance(data, dict) else None
            if isinstance(users, list):
                return [item for item in users if isinstance(item, dict)]
            if isinstance(data, list):
                return [item for item in data if isinstance(item, dict)]
        return []

    def _unwrap_response(self, data: Any) -> dict[str, Any]:
        if isinstance(data, dict) and isinstance(data.get("response"), dict):
            return data["response"]
        if isinstance(data, dict):
            return data
        return {"raw": data}

    def _gb_to_bytes(self, traffic_gb: int) -> int:
        return int(traffic_gb) * 1024 * 1024 * 1024

    def _bytes_to_gb(self, value: int | None) -> float | None:
        if value is None:
            return None
        return round(value / (1024 * 1024 * 1024), 2)

    def _to_int(self, value: Any) -> int | None:
        if value is None or value == "":
            return None
        try:
            return int(str(value))
        except (TypeError, ValueError):
            return None

    def _parse_datetime(self, value: Any) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            return None
