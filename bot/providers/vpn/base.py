from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class VpnKeyData:
    key: str
    meta: dict = field(default_factory=dict)


class VpnKeyProvider(ABC):
    @abstractmethod
    async def issue_key(
        self,
        user_id: int,
        plan_slug: str,
        traffic_gb: int | None = None,
        duration_days: int | None = None,
        build_preset: str | None = None,
    ) -> VpnKeyData:
        """Issue a new VPN key for the user."""

    @abstractmethod
    async def revoke_key(self, key: str) -> None:
        """Revoke an existing VPN key."""
