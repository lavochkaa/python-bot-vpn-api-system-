import secrets
from bot.providers.vpn.base import VpnKeyProvider, VpnKeyData


class StubVpnKeyProvider(VpnKeyProvider):
    """Stub VPN key provider for development. Replace with real panel API."""

    async def issue_key(
        self,
        user_id: int,
        plan_slug: str,
        traffic_gb: int | None = None,
        duration_days: int | None = None,
        device_limit: int | None = None,
        build_preset: str | None = None,
    ) -> VpnKeyData:
        # TODO: integrate real VPN panel (Outline API, 3x-ui, Marzban, etc.)
        _ = device_limit
        key = f"vpn://{plan_slug}/{user_id}/{secrets.token_hex(16)}"
        return VpnKeyData(key=key)

    async def revoke_key(self, key: str) -> None:
        # TODO: call VPN panel API to revoke key
        pass

    async def reset_user_traffic(
        self,
        *,
        user_id: int,
        subscription_uuid: str | None = None,
        provider_subscription_id: str | None = None,
    ) -> bool:
        _ = (user_id, subscription_uuid, provider_subscription_id)
        return False
