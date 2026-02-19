import secrets
from bot.providers.vpn.base import VpnKeyProvider, VpnKeyData


class StubVpnKeyProvider(VpnKeyProvider):
    """Stub VPN key provider for development. Replace with real panel API."""

    async def issue_key(self, user_id: int, plan_slug: str) -> VpnKeyData:
        # TODO: integrate real VPN panel (Outline API, 3x-ui, Marzban, etc.)
        key = f"vpn://{plan_slug}/{user_id}/{secrets.token_hex(16)}"
        return VpnKeyData(key=key)

    async def revoke_key(self, key: str) -> None:
        # TODO: call VPN panel API to revoke key
        pass
