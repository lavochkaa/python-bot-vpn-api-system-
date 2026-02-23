from bot.clients.vpn_client import VpnApiClient, VpnClientConfig
from bot.config import settings
from bot.providers.vpn.base import VpnKeyData, VpnKeyProvider


class ApiVpnKeyProvider(VpnKeyProvider):
    def __init__(self):
        self.client = VpnApiClient(
            VpnClientConfig(
                base_url=settings.vpn_api_base_url,
                api_key=settings.vpn_api_key,
                timeout_seconds=settings.vpn_api_timeout_seconds,
                verify_ssl=settings.vpn_api_verify_ssl,
            )
        )

    async def issue_key(
        self,
        user_id: int,
        plan_slug: str,
        traffic_gb: int | None = None,
        duration_days: int | None = None,
        build_preset: str | None = None,
    ) -> VpnKeyData:
        days = int(duration_days or 30)
        traffic = int(traffic_gb or 50)
        payload = await self.client.create_subscription(user_id=user_id, days=days, traffic_gb=traffic)
        key = (
            payload.get("key")
            or payload.get("subscription_url")
            or payload.get("url")
            or payload.get("link")
        )
        if not key:
            raise ValueError("VPN API returned no key.")
        return VpnKeyData(key=key, meta=payload)

    async def revoke_key(self, key: str) -> None:
        return None
