from __future__ import annotations

import os
from dataclasses import dataclass


DEFAULT_INBOUND_PORTS = [443, 8443]


def parse_ports(raw: str | None) -> list[int]:
    if not raw:
        return DEFAULT_INBOUND_PORTS.copy()
    ports: list[int] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if not chunk.isdigit():
            continue
        value = int(chunk)
        if 1 <= value <= 65535:
            ports.append(value)
    return sorted(set(ports)) or DEFAULT_INBOUND_PORTS.copy()


def parse_bool(raw: str | None, default: bool = False) -> bool:
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


@dataclass
class DeviceLimitSettings:
    database_url: str
    access_log_path: str
    ttl_seconds: int
    block_ttl_seconds: int
    inbound_ports: list[int]
    block_udp: bool
    poll_interval_seconds: float
    maintenance_interval_seconds: int
    stale_multiplier: int
    log_level: str
    start_at_end: bool

    @classmethod
    def from_env(cls) -> "DeviceLimitSettings":
        database_url = (
            (os.getenv("DEVICE_LIMIT_DATABASE_URL") or "").strip()
            or (os.getenv("DATABASE_URL") or "").strip()
        )
        if not database_url:
            raise RuntimeError("DEVICE_LIMIT_DATABASE_URL or DATABASE_URL must be configured")

        ttl_seconds = int((os.getenv("TTL_SECONDS") or "120").strip() or 120)
        block_ttl_seconds = int((os.getenv("BLOCK_TTL_SECONDS") or str(ttl_seconds)).strip() or ttl_seconds)
        maintenance_interval_seconds = int((os.getenv("MAINTENANCE_INTERVAL_SECONDS") or "10").strip() or 10)
        stale_multiplier = int((os.getenv("STALE_MULTIPLIER") or "10").strip() or 10)
        poll_interval_seconds = float((os.getenv("LOG_POLL_INTERVAL") or "0.2").strip() or 0.2)

        return cls(
            database_url=database_url,
            access_log_path=(os.getenv("ACCESS_LOG_PATH") or "/var/log/xray/access.log").strip(),
            ttl_seconds=max(1, ttl_seconds),
            block_ttl_seconds=max(1, block_ttl_seconds),
            inbound_ports=parse_ports(os.getenv("INBOUND_PORTS")),
            block_udp=parse_bool(os.getenv("BLOCK_UDP"), default=True),
            poll_interval_seconds=max(0.05, poll_interval_seconds),
            maintenance_interval_seconds=max(1, maintenance_interval_seconds),
            stale_multiplier=max(2, stale_multiplier),
            log_level=(os.getenv("DEVICE_LIMIT_LOG_LEVEL") or os.getenv("LOG_LEVEL") or "INFO").upper(),
            start_at_end=parse_bool(os.getenv("LOG_START_AT_END"), default=True),
        )
