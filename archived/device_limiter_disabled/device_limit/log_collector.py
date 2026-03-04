from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)


class LogCollector:
    def __init__(self, path: str, poll_interval: float = 0.2, start_at_end: bool = True) -> None:
        self.path = path
        self.poll_interval = poll_interval
        self.start_at_end = start_at_end

    async def follow(self) -> AsyncIterator[str]:
        current_file = None
        current_inode = None
        seek_to_end = self.start_at_end
        missing_reported = False

        while True:
            try:
                stat = os.stat(self.path)
            except FileNotFoundError:
                if not missing_reported:
                    logger.warning("access log not found at path=%s; waiting for file...", self.path)
                    missing_reported = True
                if current_file is not None:
                    current_file.close()
                    current_file = None
                    current_inode = None
                await asyncio.sleep(self.poll_interval)
                continue

            if current_file is None or current_inode != stat.st_ino:
                if current_file is not None:
                    current_file.close()
                current_file = open(self.path, "r", encoding="utf-8", errors="ignore")
                current_inode = stat.st_ino
                missing_reported = False
                logger.info("access log attached: path=%s inode=%s", self.path, current_inode)
                if seek_to_end:
                    current_file.seek(0, os.SEEK_END)
                seek_to_end = False

            line = current_file.readline()
            if line:
                yield line
                continue

            await asyncio.sleep(self.poll_interval)
