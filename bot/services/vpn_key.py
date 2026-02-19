from bot.db.models import VpnKey, VpnKeyStatus
from bot.repositories.vpn_key import VpnKeyRepository
from bot.providers.vpn.base import VpnKeyProvider


class VpnKeyService:
    def __init__(self, repo: VpnKeyRepository, provider: VpnKeyProvider):
        self.repo = repo
        self.provider = provider

    async def get_user_keys(self, user_id: int) -> list[VpnKey]:
        return await self.repo.get_user_keys(user_id)

    async def revoke_key(self, key_id: int) -> None:
        """Revoke a VPN key both in the panel and in DB."""
        key = await self.repo.get(key_id)
        if not key:
            raise ValueError("Ключ не найден.")
        await self.provider.revoke_key(key.key)
        key.status = VpnKeyStatus.revoked
        await self.repo.save(key)
