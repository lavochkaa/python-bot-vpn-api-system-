from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal


@dataclass
class ProviderStatus:
    balance: Decimal | None
    is_online: bool | None  # None = couldn't check


class HostingProviderClient(ABC):
    @abstractmethod
    async def fetch_status(self) -> ProviderStatus: ...
