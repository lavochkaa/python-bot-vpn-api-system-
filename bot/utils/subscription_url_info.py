import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import aiohttp


@dataclass
class SubscriptionUrlInfo:
    current_usage_gb: float | None
    usage_limit_gb: float | None
    expire_at: datetime | None


_CACHE_TTL_SECONDS = 30.0
_CACHE: dict[str, tuple[float, SubscriptionUrlInfo | None]] = {}
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/145.0.0.0 Safari/537.36"
)


def _bytes_to_gb(value: int | None) -> float | None:
    if value is None:
        return None
    return round(value / (1024 * 1024 * 1024), 2)


def _to_int(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(value.strip())
    except (TypeError, ValueError):
        return None


def _parse_subscription_userinfo(header_value: str | None) -> SubscriptionUrlInfo | None:
    if not header_value:
        return None

    fields: dict[str, str] = {}
    for chunk in header_value.split(";"):
        if "=" not in chunk:
            continue
        key, value = chunk.split("=", 1)
        fields[key.strip().lower()] = value.strip()

    upload = _to_int(fields.get("upload")) or 0
    download = _to_int(fields.get("download")) or 0
    total = _to_int(fields.get("total"))
    expire_raw = _to_int(fields.get("expire"))

    expire_at = None
    if expire_raw and expire_raw > 0:
        expire_at = datetime.fromtimestamp(expire_raw, tz=timezone.utc)

    usage_limit_gb = _bytes_to_gb(total) if total and total > 0 else None
    return SubscriptionUrlInfo(
        current_usage_gb=_bytes_to_gb(upload + download),
        usage_limit_gb=usage_limit_gb,
        expire_at=expire_at,
    )


async def fetch_subscription_url_info(url: str | None, *, timeout_seconds: float = 3.0) -> SubscriptionUrlInfo | None:
    clean_url = str(url or "").strip()
    if not clean_url.startswith(("https://", "http://")):
        return None

    cached = _CACHE.get(clean_url)
    if cached and cached[0] > time.monotonic():
        return cached[1]

    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "*/*",
        "Connection": "close",
    }

    info: SubscriptionUrlInfo | None = None
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for method in ("HEAD", "GET"):
            try:
                async with session.request(method, clean_url, headers=headers, allow_redirects=True) as response:
                    header_value = (
                        response.headers.get("subscription-userinfo")
                        or response.headers.get("Subscription-Userinfo")
                    )
                    info = _parse_subscription_userinfo(header_value)
                    if method == "GET":
                        await response.read()
                    if info is not None:
                        break
            except aiohttp.ClientError:
                continue

    _CACHE[clean_url] = (time.monotonic() + _CACHE_TTL_SECONDS, info)
    return info


def merge_usage_info(
    api_usage_info: dict[str, Any] | None,
    subscription_url_info: SubscriptionUrlInfo | None,
) -> dict[str, Any] | None:
    if api_usage_info is None and subscription_url_info is None:
        return None

    merged = dict(api_usage_info or {})
    if subscription_url_info is not None:
        if subscription_url_info.current_usage_gb is not None:
            merged["current_usage_gb"] = subscription_url_info.current_usage_gb
        if subscription_url_info.usage_limit_gb is not None:
            merged["usage_limit_gb"] = subscription_url_info.usage_limit_gb
        if subscription_url_info.expire_at is not None:
            merged["expire_at"] = subscription_url_info.expire_at
    return merged
