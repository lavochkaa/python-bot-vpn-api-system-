from __future__ import annotations

import asyncio
import logging

from device_limit.config import DeviceLimitSettings
from device_limit.db import DeviceLimitStore
from device_limit.firewall import FirewallManager
from device_limit.log_collector import LogCollector
from device_limit.parser import parse_event
from device_limit.tracker import DeviceTracker


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


async def run_daemon() -> None:
    cfg = DeviceLimitSettings.from_env()
    setup_logging(cfg.log_level)

    logger = logging.getLogger("device_limit.main")
    logger.info(
        "starting device_limitd access_log=%s ttl=%s block_ttl=%s ports=%s backend=auto",
        cfg.access_log_path,
        cfg.ttl_seconds,
        cfg.block_ttl_seconds,
        cfg.inbound_ports,
    )

    store = DeviceLimitStore(cfg.database_url)
    await store.ensure_schema()

    firewall = FirewallManager()
    tracker = DeviceTracker(store=store, firewall=firewall, cfg=cfg)
    collector = LogCollector(
        path=cfg.access_log_path,
        poll_interval=cfg.poll_interval_seconds,
        start_at_end=cfg.start_at_end,
    )

    async def consume_logs() -> None:
        async for line in collector.follow():
            event = parse_event(line)
            if event is None:
                continue
            try:
                await tracker.process_event(event)
            except Exception:
                logger.exception("failed to process event line=%r", line.strip())

    async def maintenance_loop() -> None:
        while True:
            await asyncio.sleep(cfg.maintenance_interval_seconds)
            try:
                await tracker.run_maintenance()
            except Exception:
                logger.exception("maintenance loop failure")

    try:
        await asyncio.gather(consume_logs(), maintenance_loop())
    finally:
        await store.close()


def main() -> None:
    asyncio.run(run_daemon())


if __name__ == "__main__":
    main()
