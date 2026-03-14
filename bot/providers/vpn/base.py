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
        device_limit: int | None = None,
        build_preset: str | None = None,
    ) -> VpnKeyData:
        """Issue a new VPN key for the user."""

    @abstractmethod
    async def revoke_key(self, key: str) -> None:
        """Revoke an existing VPN key."""

    async def reset_user_traffic(
        self,
        *,
        user_id: int,
        subscription_uuid: str | None = None,
        provider_subscription_id: str | None = None,
    ) -> bool:
        """
        Reset user traffic counter in provider panel.
        Returns True when provider confirms/accepts reset action.
        """
        return False

    async def get_user_usage(
        self,
        *,
        user_id: int,
        subscription_uuid: str | None = None,
        provider_subscription_id: str | None = None,
    ) -> dict | None:
        """
        Return provider-side usage info when available.
        Expected keys (optional): current_usage_gb, usage_limit_gb.
        """
        return None
