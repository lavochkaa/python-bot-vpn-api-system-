from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from device_limit.config import DeviceLimitSettings
from device_limit.db import DeviceLimitStore
from device_limit.firewall import FirewallManager
from device_limit.parser import Event

logger = logging.getLogger(__name__)


class DeviceTracker:
    def __init__(self, store: DeviceLimitStore, firewall: FirewallManager, cfg: DeviceLimitSettings) -> None:
        self.store = store
        self.firewall = firewall
        self.cfg = cfg

    async def process_event(self, event: Event) -> None:
        now = datetime.now(timezone.utc)
        blocked_until = await self.store.get_blocked_until(event.uuid, event.client_ip)
        if blocked_until and blocked_until > now:
            # Ignore repeated attempts from already blocked IPs.
            return

        active_before = set(await self.store.get_active_ips(event.uuid, self.cfg.ttl_seconds))
        is_new_ip = event.client_ip not in active_before
        max_devices = await self.store.get_max_devices(event.uuid, default_limit=2)

        if is_new_ip and len(active_before) >= max_devices:
            blocked_until = now + timedelta(seconds=self.cfg.block_ttl_seconds)
            try:
                self.firewall.add_block(
                    ip=event.client_ip,
                    ports=self.cfg.inbound_ports,
                    ttl=self.cfg.block_ttl_seconds,
                    block_udp=self.cfg.block_udp,
                )
                await self.store.set_blocked_until(event.uuid, event.client_ip, blocked_until)
                logger.warning(
                    "blocked ip=%s uuid=%s active=%s limit=%s until=%s",
                    event.client_ip,
                    event.uuid,
                    len(active_before),
                    max_devices,
                    blocked_until.isoformat(),
                )
            except Exception:
                logger.exception("failed to block ip=%s for uuid=%s", event.client_ip, event.uuid)
            return

        # Allowed device: refresh activity.
        await self.store.touch_session(event.uuid, event.client_ip, event.ts)

    async def run_maintenance(self) -> None:
        now = datetime.now(timezone.utc)
        expired_ips = await self.store.get_expired_blocked_ips(now)
        for ip in sorted(set(expired_ips)):
            try:
                self.firewall.remove_block(ip=ip, ports=self.cfg.inbound_ports, block_udp=self.cfg.block_udp)
            except Exception:
                logger.exception("failed to remove block for ip=%s", ip)

        await self.store.clear_expired_blocks(now)
        await self.store.cleanup_stale(self.cfg.ttl_seconds, multiplier=self.cfg.stale_multiplier)
