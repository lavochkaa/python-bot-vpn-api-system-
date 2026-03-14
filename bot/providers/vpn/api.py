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
                force_ipv4=settings.vpn_api_force_ipv4,
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
        username = f"tg_{user_id}"
        payload = await self.client.create_or_update_subscription(
            user_id=user_id,
            username=username,
            days=days,
            traffic_gb=traffic,
            device_limit=max(0, int(settings.vpn_api_hwid_device_limit or 3)),
            traffic_reset_strategy=(settings.vpn_api_traffic_reset_strategy or "NO_RESET").strip().upper(),
            internal_squad_uuid=(settings.vpn_api_internal_squad_uuid or "").strip() or None,
        )
        key = (
            self.client.build_subscription_url(payload)
            or settings.vpn_api_subscription_url_template.format(
                short_uuid=payload.get("shortUuid", ""),
                user_uuid=payload.get("uuid", ""),
                username=payload.get("username", username),
            )
        )
        if not key:
            raise ValueError("VPN API returned no key.")
        return VpnKeyData(key=key, meta=payload)

    async def revoke_key(self, key: str) -> None:
        return None

    def _resolve_user_ref(
        self,
        *,
        subscription_uuid: str | None = None,
        provider_subscription_id: str | None = None,
    ) -> str | None:
        for candidate in (subscription_uuid, provider_subscription_id):
            value = str(candidate or "").strip()
            if value.count("-") == 4 and len(value) >= 32:
                return value
        return None

    async def reset_user_traffic(
        self,
        *,
        user_id: int,
        subscription_uuid: str | None = None,
        provider_subscription_id: str | None = None,
    ) -> bool:
        target_uuid = self._resolve_user_ref(
            subscription_uuid=subscription_uuid,
            provider_subscription_id=provider_subscription_id,
        )
        if not target_uuid:
            existing = await self.client.find_user(telegram_id=user_id, username=f"tg_{user_id}")
            target_uuid = str(existing.get("uuid") or "") if existing else None
        if not target_uuid:
            return False
        return await self.client.reset_user_traffic(str(target_uuid))

    async def get_user_usage(
        self,
        *,
        user_id: int,
        subscription_uuid: str | None = None,
        provider_subscription_id: str | None = None,
    ) -> dict | None:
        target_uuid = self._resolve_user_ref(
            subscription_uuid=subscription_uuid,
            provider_subscription_id=provider_subscription_id,
        )
        return await self.client.get_user_usage(
            uuid=target_uuid,
            telegram_id=None if target_uuid else user_id,
            username=None if target_uuid else f"tg_{user_id}",
        )
