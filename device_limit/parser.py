from __future__ import annotations

import ipaddress
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{12}"
)
IP_RE = re.compile(
    r"(?:\b(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}\b)"
    r"|(?:\b(?:[0-9a-fA-F]{1,4}:){2,7}[0-9a-fA-F]{1,4}\b)"
)


@dataclass
class Event:
    uuid: str
    client_ip: str
    ts: datetime


def _normalize_uuid(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(UUID(value.strip()))
    except (ValueError, AttributeError):
        return None


def _normalize_ip(value: str | None) -> str | None:
    if not value:
        return None
    raw = value.strip().strip("[]")
    if ":" in raw and "." in raw and raw.count(":") > 1:
        raw = raw.rsplit(":", 1)[0]
    if raw.count(":") == 1 and raw.count(".") == 3:
        raw = raw.split(":", 1)[0]
    try:
        return str(ipaddress.ip_address(raw))
    except ValueError:
        return None


def _parse_ts(value: object) -> datetime | None:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return None
        if candidate.endswith("Z"):
            candidate = candidate[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(candidate)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _scan_json(node: object) -> tuple[str | None, str | None, datetime | None]:
    found_uuid: str | None = None
    found_ip: str | None = None
    found_ts: datetime | None = None
    stack: list[object] = [node]

    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            for key, value in current.items():
                lk = str(key).lower()
                if isinstance(value, (dict, list)):
                    stack.append(value)
                    continue
                if found_uuid is None and isinstance(value, str):
                    if lk in {"uuid", "user_uuid", "client_uuid", "subscription_uuid", "email"}:
                        found_uuid = _normalize_uuid(value) or found_uuid
                    if found_uuid is None:
                        match = UUID_RE.search(value)
                        if match:
                            found_uuid = _normalize_uuid(match.group(0))
                if found_ip is None and isinstance(value, str):
                    if lk in {"ip", "client_ip", "remote_addr", "source", "addr"}:
                        found_ip = _normalize_ip(value) or found_ip
                    if found_ip is None:
                        ip_match = IP_RE.search(value)
                        if ip_match:
                            found_ip = _normalize_ip(ip_match.group(0))
                if found_ts is None and lk in {
                    "time",
                    "timestamp",
                    "ts",
                    "date",
                    "logged_at",
                    "last_seen",
                }:
                    found_ts = _parse_ts(value)
        elif isinstance(current, list):
            stack.extend(current)

    return found_uuid, found_ip, found_ts


def parse_event(line: str) -> Event | None:
    raw = line.strip()
    if not raw:
        return None

    found_uuid: str | None = None
    found_ip: str | None = None
    found_ts: datetime | None = None

    if raw.startswith("{") and raw.endswith("}"):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = None
        if payload is not None:
            found_uuid, found_ip, found_ts = _scan_json(payload)

    if found_uuid is None:
        uuid_match = UUID_RE.search(raw)
        if uuid_match:
            found_uuid = _normalize_uuid(uuid_match.group(0))

    if found_ip is None:
        ip_match = IP_RE.search(raw)
        if ip_match:
            found_ip = _normalize_ip(ip_match.group(0))

    if found_uuid is None or found_ip is None:
        return None

    return Event(uuid=found_uuid, client_ip=found_ip, ts=found_ts or datetime.now(timezone.utc))
