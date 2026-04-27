import asyncio
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp

from bot.clients.http import (
    build_client_timeout,
    build_connector,
    first_matching_error,
    is_retryable_http_status,
    is_retryable_transport_error,
    sleep_with_jitter,
)


logger = logging.getLogger(__name__)


@dataclass
class VpnClientConfig:
    base_url: str
    api_key: str
    timeout_seconds: int = 20
    verify_ssl: bool = True
    force_ipv4: bool = True
    connect_pool_limit: int = 32
    connect_pool_limit_per_host: int = 8
    keepalive_timeout_seconds: float = 30.0
    dns_cache_ttl_seconds: int = 300
    max_retries: int = 2
    retry_base_seconds: float = 0.2
    retry_cap_seconds: float = 2.0


class VpnApiClient:
    _USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/145.0.0.0 Safari/537.36"
    )
    _RETRYABLE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "PATCH", "DELETE"})
    _STATUS_HEADER_FALLBACK = frozenset({401, 403})

    def __init__(self, config: VpnClientConfig):
        if not config.base_url:
            raise ValueError("VPN_API_BASE_URL is not configured.")
        if not config.api_key:
            raise ValueError("VPN_API_KEY is not configured.")
        self.config = config
        self._session: aiohttp.ClientSession | None = None
        self._session_lock = asyncio.Lock()
        self._request_semaphore = asyncio.Semaphore(max(1, self.config.connect_pool_limit_per_host))

    def _build_url(self, path: str) -> str:
        base = self.config.base_url.rstrip("/")
        normalized_path = "/" + path.lstrip("/")
        if base.endswith("/api") and normalized_path.startswith("/api/"):
            normalized_path = normalized_path[4:]
        return base + normalized_path

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
        traffic_reset_strategy = self._normalize_traffic_reset_strategy(traffic_reset_strategy)
        internal_squad_uuid = await self._resolve_internal_squad_uuid(internal_squad_uuid)

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
                payload["activeInternalSquads"] = [internal_squad_uuid]
            update_attempts: tuple[tuple[str, str], ...] = (
                ("PATCH", "/api/users"),
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
            payload["activeInternalSquads"] = [internal_squad_uuid]
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
        # Fast path: uuid known → single request, no list scan
        if uuid:
            try:
                return await self._request("GET", f"/api/users/{uuid}")
            except ValueError:
                pass

        # Fallback: search by telegramId / username via list
        users = await self._list_users()
        for user in users:
            if not isinstance(user, dict):
                continue
            if telegram_id is not None and self._to_int(user.get("telegramId")) == int(telegram_id):
                return user
            if username and str(user.get("username") or "").strip() == username:
                return user
        return None

    async def get_user_usage(self, *, uuid: str | None = None, telegram_id: int | None = None, username: str | None = None) -> dict[str, Any] | None:
        user = await self.find_user(uuid=uuid, telegram_id=telegram_id, username=username)
        if not user:
            return None
        user_traffic = user.get("userTraffic") if isinstance(user.get("userTraffic"), dict) else {}
        used_bytes = self._extract_used_bytes(user, user_traffic)
        limit_bytes = self._extract_limit_bytes(user, user_traffic)
        if used_bytes is None and limit_bytes is None:
            return None
        expire_at = self._parse_datetime(
            user.get("expireAt")
            or user.get("expiresAt")
            or user.get("expire_at")
            or user_traffic.get("expireAt")
            or user_traffic.get("expiresAt")
            or user_traffic.get("expire_at")
        )
        connected_devices = await self._get_connected_devices(user)
        connected_devices_count = len(connected_devices)
        return {
            "current_usage_gb": self._bytes_to_gb(used_bytes),
            "usage_limit_gb": self._bytes_to_gb(limit_bytes),
            "expire_at": expire_at,
            "device_limit": self._extract_device_limit(user),
            "last_user_agent": user.get("subLastUserAgent"),
            "connected_devices_count": connected_devices_count,
            "connected_devices": connected_devices,
            "last_opened_at": user.get("subLastOpenedAt"),
        }

    async def reset_user_traffic(self, uuid: str) -> bool:
        for method, path in (
            ("POST", f"/api/users/{uuid}/actions/reset-traffic"),
            ("POST", f"/users/{uuid}/actions/reset-traffic"),
            ("POST", f"/users/{uuid}/reset-traffic"),
            ("POST", f"/api/users/{uuid}/reset-traffic"),
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
        url = self._build_url(path)
        headers_candidates = self._build_headers_candidates(method, json=json)
        errors: list[BaseException] = []
        session = await self._ensure_session()

        async with self._request_semaphore:
            for header_idx, headers in enumerate(headers_candidates, start=1):
                attempt = 0
                while True:
                    try:
                        async with session.request(method, url, headers=headers, json=json) as response:
                            text = await response.text()
                            if response.status >= 400:
                                if (
                                    response.status in self._STATUS_HEADER_FALLBACK
                                    and header_idx < len(headers_candidates)
                                ):
                                    break
                                if (
                                    method.upper() in self._RETRYABLE_METHODS
                                    and is_retryable_http_status(response.status)
                                    and attempt < self.config.max_retries
                                ):
                                    attempt += 1
                                    await sleep_with_jitter(
                                        attempt,
                                        base=self.config.retry_base_seconds,
                                        cap=self.config.retry_cap_seconds,
                                    )
                                    continue
                                logger.error("VPN API error %s %s: %s %s", method, url, response.status, text)
                                raise ValueError(f"VPN API error: HTTP {response.status}")
                            ctype = (response.headers.get("Content-Type") or "").lower()
                            if "application/json" in ctype:
                                data = await response.json()
                                return self._unwrap_response(data)
                            return {"raw": text}
                    except ValueError:
                        raise
                    except Exception as exc:
                        errors.append(exc)
                        if (
                            method.upper() in self._RETRYABLE_METHODS
                            and is_retryable_transport_error(exc)
                            and attempt < self.config.max_retries
                        ):
                            attempt += 1
                            logger.warning(
                                "VPN API retry %s %s attempt=%s reason=%s",
                                method,
                                url,
                                attempt,
                                exc,
                            )
                            await sleep_with_jitter(
                                attempt,
                                base=self.config.retry_base_seconds,
                                cap=self.config.retry_cap_seconds,
                            )
                            continue
                        break

        last_error = first_matching_error(errors)
        if last_error is not None:
            logger.exception("VPN API request failed %s %s", method, url, exc_info=last_error)
            raise ValueError("VPN API is unavailable.") from last_error
        raise ValueError("VPN API is unavailable.")

    def _build_headers_candidates(self, method: str, *, json: dict[str, Any] | None = None) -> tuple[dict[str, str], ...]:
        base_headers = {
            "Accept": "application/json, text/plain, */*",
            "User-Agent": self._USER_AGENT,
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
        deadline = time.monotonic() + max(6.0, float(self.config.timeout_seconds) * 1.5)
        for path in paths:
            if time.monotonic() >= deadline:
                break
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
        deadline = time.monotonic() + max(6.0, float(self.config.timeout_seconds) * 1.5)
        for method, path in attempts:
            if time.monotonic() >= deadline:
                break
            try:
                return await self._request(method, path, json=json)
            except ValueError as exc:
                last_error = exc
                continue
        if last_error is not None:
            raise last_error
        raise ValueError("VPN API request failed.")

    async def _request_optional_json(self, method: str, path: str, *, params: dict[str, str] | None = None) -> Any | None:
        url = self._build_url(path)
        headers_candidates = self._build_headers_candidates(method)
        session = await self._ensure_session()

        async with self._request_semaphore:
            for header_idx, headers in enumerate(headers_candidates, start=1):
                attempt = 0
                while True:
                    try:
                        async with session.request(method, url, headers=headers, params=params) as response:
                            if response.status >= 400:
                                if response.status in self._STATUS_HEADER_FALLBACK and header_idx < len(headers_candidates):
                                    break
                                if (
                                    method.upper() in self._RETRYABLE_METHODS
                                    and is_retryable_http_status(response.status)
                                    and attempt < self.config.max_retries
                                ):
                                    attempt += 1
                                    await sleep_with_jitter(
                                        attempt,
                                        base=self.config.retry_base_seconds,
                                        cap=self.config.retry_cap_seconds,
                                    )
                                    continue
                                break
                            ctype = (response.headers.get("Content-Type") or "").lower()
                            if "application/json" not in ctype:
                                return None
                            return await response.json()
                    except Exception as exc:
                        if (
                            method.upper() in self._RETRYABLE_METHODS
                            and is_retryable_transport_error(exc)
                            and attempt < self.config.max_retries
                        ):
                            attempt += 1
                            await sleep_with_jitter(
                                attempt,
                                base=self.config.retry_base_seconds,
                                cap=self.config.retry_cap_seconds,
                            )
                            continue
                        break
        return None

    async def _get_connected_devices(self, user: dict[str, Any]) -> list[str]:
        uuid = str(user.get("uuid") or "").strip()
        if not uuid:
            return []

        # Remnawave: /api/hwid/devices/{uuid} → response.devices[]
        payload = await self._request_optional_json("GET", f"/api/hwid/devices/{uuid}")
        if isinstance(payload, dict):
            inner = payload.get("response", payload)
            raw_devices = inner.get("devices") if isinstance(inner, dict) else None
            if isinstance(raw_devices, list):
                labels: list[str] = []
                seen: set[str] = set()
                for d in raw_devices:
                    if not isinstance(d, dict):
                        continue
                    # Build a human-readable label from device fields
                    model = str(d.get("deviceModel") or "").strip()
                    platform = str(d.get("platform") or "").strip()
                    os_ver = str(d.get("osVersion") or "").strip()
                    label = model or platform
                    if label and os_ver:
                        label = f"{label} {os_ver}"
                    if not label:
                        label = str(d.get("hwid") or d.get("userAgent") or "").strip()[:24]
                    if label and label not in seen:
                        seen.add(label)
                        labels.append(label)
                return labels

        return []

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is not None and not self._session.closed:
            return self._session

        async with self._session_lock:
            if self._session is not None and not self._session.closed:
                return self._session
            connector = build_connector(
                verify_ssl=self.config.verify_ssl,
                force_ipv4=self.config.force_ipv4,
                limit=self.config.connect_pool_limit,
                limit_per_host=self.config.connect_pool_limit_per_host,
                keepalive_timeout=self.config.keepalive_timeout_seconds,
                ttl_dns_cache=self.config.dns_cache_ttl_seconds,
            )
            self._session = aiohttp.ClientSession(
                timeout=build_client_timeout(self.config.timeout_seconds),
                connector=connector,
                connector_owner=True,
            )
            return self._session

    def _extract_connected_devices(
        self,
        payload: Any,
        *,
        user_uuid: str | None = None,
        user_id: str | None = None,
        scoped_payload: bool = False,
    ) -> list[str]:
        if payload is None:
            return []

        candidates = self._extract_device_candidates(payload)

        result: list[str] = []
        for item in candidates:
            if not isinstance(item, dict):
                continue
            if not self._device_matches_user(
                item,
                user_uuid=user_uuid,
                user_id=user_id,
                scoped_payload=scoped_payload,
            ):
                continue
            device_info = item.get("deviceInfo") if isinstance(item.get("deviceInfo"), dict) else {}
            os_name = (
                item.get("deviceOs")
                or item.get("xDeviceOs")
                or item.get("os")
                or item.get("platform")
                or item.get("devicePlatform")
                or item.get("clientType")
                or item.get("device_type")
                or item.get("osName")
                or device_info.get("os")
                or device_info.get("platform")
                or device_info.get("deviceOs")
            )
            model_name = (
                item.get("deviceModel")
                or item.get("xDeviceModel")
                or item.get("model")
                or item.get("deviceName")
                or item.get("name")
                or item.get("friendlyName")
                or item.get("clientName")
                or item.get("hwid")
                or item.get("userAgent")
                or item.get("subLastUserAgent")
                or item.get("deviceName")
                or item.get("device")
                or device_info.get("model")
                or device_info.get("name")
                or device_info.get("deviceModel")
            )
            if not os_name and not model_name:
                continue
            parts = [str(part).strip() for part in (os_name, model_name) if str(part or "").strip()]
            label = " - ".join(parts)
            if label:
                result.append(label)
        return list(dict.fromkeys(result))

    def _extract_device_candidates(self, payload: Any) -> list[Any]:
        candidates: list[Any] = []
        if isinstance(payload, list):
            candidates = payload
        elif isinstance(payload, dict):
            nested_device_lists = (
                payload.get("hwidDevices"),
                payload.get("userHwidDevices"),
            )
            for value in nested_device_lists:
                if isinstance(value, list):
                    candidates = value
                    break
            for key in ("response", "devices", "items", "data", "results"):
                if candidates:
                    break
                value = payload.get(key)
                if isinstance(value, list):
                    candidates = value
                    break
                if isinstance(value, dict):
                    for inner_direct_key in (
                        "hwidDevices",
                        "userHwidDevices",
                    ):
                        inner_direct_value = value.get(inner_direct_key)
                        if isinstance(inner_direct_value, list):
                            candidates = inner_direct_value
                            break
                    if candidates:
                        break
                    for inner_key in ("devices", "items", "data", "results"):
                        inner_value = value.get(inner_key)
                        if isinstance(inner_value, list):
                            candidates = inner_value
                            break
                    if candidates:
                        break
        return candidates

    def _should_trust_scoped_device_payload(
        self,
        payload: Any,
        *,
        trusted_device_limit: int | None,
    ) -> bool:
        if payload is None:
            return False
        limit = self._to_int(trusted_device_limit)
        if limit is None or limit <= 0:
            return False
        candidates = self._extract_device_candidates(payload)
        if not candidates:
            return False
        device_count = self._count_unique_devices(candidates)
        if device_count is None:
            return False
        return device_count <= limit

    def _device_matches_user(
        self,
        item: dict[str, Any],
        *,
        user_uuid: str | None = None,
        user_id: str | None = None,
        scoped_payload: bool = False,
    ) -> bool:
        if not user_uuid and not user_id:
            return True

        nested_user = item.get("user") if isinstance(item.get("user"), dict) else {}
        candidates = (
            item.get("userUuid"),
            item.get("uuid"),
            item.get("ownerUuid"),
            item.get("user_uuid"),
            item.get("owner_uuid"),
            nested_user.get("uuid"),
        )
        if user_uuid and any(str(value or "").strip() == user_uuid for value in candidates):
            return True

        id_candidates = (
            item.get("userId"),
            item.get("ownerId"),
            item.get("user_id"),
            item.get("owner_id"),
            nested_user.get("id"),
        )
        if user_id and any(str(value or "").strip() == user_id for value in id_candidates):
            return True

        # If payload is already scoped to a single user, it may omit ownership fields entirely.
        scoped_keys = ("deviceOs", "deviceModel", "xDeviceOs", "xDeviceModel", "hwid", "deviceInfo", "userAgent")
        if scoped_payload and any(key in item for key in scoped_keys):
            return True

        return False

    def _extract_device_count(self, payload: Any) -> int | None:
        if not isinstance(payload, dict):
            return None

        for key in (
            "connectedDevicesCount",
            "connected_devices_count",
            "hwidDevicesCount",
            "devicesCount",
            "deviceCount",
            "total",
        ):
            value = self._to_int(payload.get(key))
            if value is not None:
                return value

        for key in (
            "hwidDevices",
            "userHwidDevices",
            "devices",
        ):
            value = payload.get(key)
            if isinstance(value, list):
                return self._count_unique_devices(value)

        response = payload.get("response")
        if isinstance(response, dict):
            return self._extract_device_count(response)
        return None

    def _count_unique_devices(self, items: list[Any]) -> int | None:
        unique_keys: set[str] = set()
        plain_count = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            plain_count += 1
            key = str(
                item.get("hwid")
                or item.get("deviceHwId")
                or item.get("deviceId")
                or item.get("deviceUUID")
                or item.get("userAgent")
                or item.get("subLastUserAgent")
                or ""
            ).strip()
            if key:
                unique_keys.add(key)
        if unique_keys:
            return len(unique_keys)
        return plain_count or None

    async def _resolve_internal_squad_uuid(self, internal_squad_uuid: str | None) -> str | None:
        explicit_uuid = str(internal_squad_uuid or "").strip()
        if explicit_uuid:
            return explicit_uuid

        for path in (
            "/api/internal-squads",
            "/internal-squads",
            "/api/internalSquads",
            "/internalSquads",
        ):
            payload = await self._request_optional_json("GET", path)
            resolved_uuid = self._pick_default_internal_squad_uuid(payload)
            if resolved_uuid:
                return resolved_uuid
        return None

    def _pick_default_internal_squad_uuid(self, payload: Any) -> str | None:
        candidates: list[dict[str, Any]] = []
        if isinstance(payload, list):
            candidates = [item for item in payload if isinstance(item, dict)]
        elif isinstance(payload, dict):
            for key in ("internalSquads", "items", "data", "results"):
                value = payload.get(key)
                if isinstance(value, list):
                    candidates = [item for item in value if isinstance(item, dict)]
                    break
            if not candidates:
                response = payload.get("response")
                if isinstance(response, dict):
                    return self._pick_default_internal_squad_uuid(response)
                if isinstance(response, list):
                    candidates = [item for item in response if isinstance(item, dict)]

        if not candidates:
            return None

        for item in candidates:
            squad_uuid = self._extract_internal_squad_uuid(item)
            if not squad_uuid:
                continue
            squad_name = str(
                item.get("name")
                or item.get("title")
                or item.get("remark")
                or item.get("label")
                or ""
            ).strip().lower()
            if squad_name in {"default-squad", "default squad", "default"}:
                return squad_uuid

        for item in candidates:
            if any(
                bool(item.get(key))
                for key in ("isDefault", "default", "is_default", "defaultSquad", "is_default_squad")
            ):
                squad_uuid = self._extract_internal_squad_uuid(item)
                if squad_uuid:
                    return squad_uuid

        positioned_candidates: list[tuple[int, str]] = []
        for item in candidates:
            squad_uuid = self._extract_internal_squad_uuid(item)
            if not squad_uuid:
                continue
            view_position = self._to_int(item.get("viewPosition"))
            if view_position is not None:
                positioned_candidates.append((view_position, squad_uuid))

        if positioned_candidates:
            positioned_candidates.sort(key=lambda item: item[0])
            return positioned_candidates[0][1]

        for item in candidates:
            squad_uuid = self._extract_internal_squad_uuid(item)
            if squad_uuid:
                return squad_uuid
        return None

    def _extract_internal_squad_uuid(self, payload: dict[str, Any]) -> str | None:
        for key in ("uuid", "internalSquadUuid", "squadUuid", "id"):
            value = str(payload.get(key) or "").strip()
            if value:
                return value
        return None

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

    def _merge_user_payloads(
        self,
        primary: dict[str, Any] | None,
        secondary: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not primary:
            return secondary
        if not secondary:
            return primary

        merged = dict(secondary)
        for key, value in primary.items():
            if isinstance(value, dict) and isinstance(secondary.get(key), dict):
                merged[key] = self._merge_user_payloads(value, secondary.get(key))
                continue
            if self._has_meaningful_value(value):
                merged[key] = value
        return merged

    def _has_meaningful_value(self, value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return value.strip() != ""
        if isinstance(value, (list, tuple, set, dict)):
            return len(value) > 0
        return True

    def _extract_used_bytes(self, user: dict[str, Any], user_traffic: dict[str, Any]) -> int | None:
        candidates = (
            user.get("trafficUsedBytes"),
            user.get("usedTrafficBytes"),
            user.get("usedTraffic"),
            user.get("currentUsageBytes"),
            user.get("totalUsedBytes"),
            user.get("trafficUsageBytes"),
            user.get("usedTrafficHuman"),
            user.get("trafficUsed"),
            user.get("trafficUsedHuman"),
            user.get("currentUsage"),
            user_traffic.get("usedTrafficBytes"),
            user_traffic.get("lifetimeUsedTrafficBytes"),
            user_traffic.get("currentUsageBytes"),
            user_traffic.get("totalUsedBytes"),
            user_traffic.get("usedTraffic"),
            user_traffic.get("usedTrafficHuman"),
            user_traffic.get("currentUsage"),
            user_traffic.get("lifetimeUsedTraffic"),
        )
        for value in candidates:
            parsed = self._parse_size_to_bytes(value)
            if parsed is not None:
                return parsed

        uploaded = self._parse_size_to_bytes(
            user.get("uploadBytes")
            or user.get("uploadedBytes")
            or user.get("upload")
            or user.get("uploaded")
            or user_traffic.get("uploadBytes")
            or user_traffic.get("uploadedBytes")
            or user_traffic.get("upload")
            or user_traffic.get("uploaded")
        )
        downloaded = self._parse_size_to_bytes(
            user.get("downloadBytes")
            or user.get("downloadedBytes")
            or user.get("download")
            or user.get("downloaded")
            or user_traffic.get("downloadBytes")
            or user_traffic.get("downloadedBytes")
            or user_traffic.get("download")
            or user_traffic.get("downloaded")
        )
        if uploaded is not None or downloaded is not None:
            return int(uploaded or 0) + int(downloaded or 0)
        return None

    def _extract_limit_bytes(self, user: dict[str, Any], user_traffic: dict[str, Any]) -> int | None:
        for value in (
            user.get("trafficLimitBytes"),
            user.get("limitTrafficBytes"),
            user.get("trafficBytes"),
            user.get("traffic"),
            user.get("trafficLimit"),
            user.get("trafficLimitHuman"),
            user.get("lifetimeTraffic"),
            user_traffic.get("trafficLimitBytes"),
            user_traffic.get("limitTrafficBytes"),
            user_traffic.get("trafficLimit"),
            user_traffic.get("trafficLimitHuman"),
            user_traffic.get("lifetimeTraffic"),
        ):
            parsed = self._parse_size_to_bytes(value)
            if parsed is not None:
                return parsed
        return None

    def _extract_device_limit(self, user: dict[str, Any]) -> int | None:
        user_traffic = user.get("userTraffic") if isinstance(user.get("userTraffic"), dict) else {}
        for value in (
            user.get("hwidDeviceLimit"),
            user.get("deviceLimit"),
            user.get("devicesLimit"),
            user.get("maxDevices"),
            user.get("hwid_limit"),
            user_traffic.get("hwidDeviceLimit"),
            user_traffic.get("deviceLimit"),
        ):
            parsed = self._to_int(value)
            if parsed is not None:
                return parsed
        return None

    def _gb_to_bytes(self, traffic_gb: int) -> int:
        return int(traffic_gb) * 1024 * 1024 * 1024

    def _normalize_traffic_reset_strategy(self, value: str) -> str:
        normalized = str(value or "").strip().upper()
        aliases = {
            "MONTHLY": "MONTH",
            "MONTH": "MONTH",
            "WEEKLY": "WEEK",
            "DAILY": "DAY",
        }
        return aliases.get(normalized, normalized or "NO_RESET")

    def _bytes_to_gb(self, value: int | None) -> float | None:
        if value is None:
            return None
        return round(value / (1024 * 1024 * 1024), 2)

    def _parse_size_to_bytes(self, value: Any) -> int | None:
        direct = self._to_int(value)
        if direct is not None:
            return direct
        if value is None:
            return None

        text = str(value).strip()
        if not text:
            return None

        match = re.search(r"([0-9]+(?:[.,][0-9]+)?)\s*([kmgtpe]?i?b)?", text, re.IGNORECASE)
        if not match:
            return None

        number_raw = match.group(1).replace(",", ".")
        unit_raw = (match.group(2) or "b").lower()
        try:
            number = float(number_raw)
        except ValueError:
            return None

        multipliers = {
            "b": 1,
            "kb": 1000,
            "mb": 1000 ** 2,
            "gb": 1000 ** 3,
            "tb": 1000 ** 4,
            "pb": 1000 ** 5,
            "kib": 1024,
            "mib": 1024 ** 2,
            "gib": 1024 ** 3,
            "tib": 1024 ** 4,
            "pib": 1024 ** 5,
        }
        multiplier = multipliers.get(unit_raw)
        if multiplier is None:
            return None
        return int(number * multiplier)

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
