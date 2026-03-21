import asyncio
import random
import socket
import ssl
from collections.abc import Iterable

import aiohttp


RETRYABLE_HTTP_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})
RETRYABLE_TRANSPORT_ERRORS = (
    asyncio.TimeoutError,
    aiohttp.ClientConnectionError,
    aiohttp.ClientConnectorError,
    aiohttp.ClientOSError,
    aiohttp.ClientPayloadError,
    aiohttp.ServerDisconnectedError,
    aiohttp.ServerTimeoutError,
    ConnectionResetError,
    TimeoutError,
)


def build_client_timeout(total_seconds: float) -> aiohttp.ClientTimeout:
    total = max(1.0, float(total_seconds))
    connect = min(4.0, max(1.5, total * 0.3))
    sock_connect = connect
    sock_read = max(2.0, min(total - 0.5, total * 0.8))
    return aiohttp.ClientTimeout(
        total=total,
        connect=connect,
        sock_connect=sock_connect,
        sock_read=sock_read,
    )


def build_connector(
    *,
    verify_ssl: bool,
    force_ipv4: bool,
    limit: int = 32,
    limit_per_host: int = 8,
    keepalive_timeout: float = 30.0,
    ttl_dns_cache: int = 300,
) -> aiohttp.TCPConnector:
    ssl_config: bool | ssl.SSLContext
    if verify_ssl:
        ssl_config = True
    else:
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        ssl_config = ssl_context

    return aiohttp.TCPConnector(
        ssl=ssl_config,
        family=socket.AF_INET if force_ipv4 else socket.AF_UNSPEC,
        enable_cleanup_closed=True,
        use_dns_cache=True,
        ttl_dns_cache=ttl_dns_cache,
        keepalive_timeout=keepalive_timeout,
        limit=limit,
        limit_per_host=limit_per_host,
    )


def is_retryable_http_status(status: int) -> bool:
    return status in RETRYABLE_HTTP_STATUSES


def is_retryable_transport_error(exc: BaseException) -> bool:
    return isinstance(exc, RETRYABLE_TRANSPORT_ERRORS)


def backoff_delay_seconds(
    attempt: int,
    *,
    base: float = 0.2,
    cap: float = 2.0,
) -> float:
    slot = min(cap, base * (2 ** max(0, attempt - 1)))
    return random.uniform(0.0, slot)


async def sleep_with_jitter(
    attempt: int,
    *,
    base: float = 0.2,
    cap: float = 2.0,
) -> None:
    await asyncio.sleep(backoff_delay_seconds(attempt, base=base, cap=cap))


def first_matching_error(errors: Iterable[BaseException]) -> BaseException | None:
    for error in errors:
        if error is not None:
            return error
    return None
