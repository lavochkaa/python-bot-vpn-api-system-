from bot.config import settings
from bot.providers.vpn.base import VpnKeyProvider


def build_vpn_provider() -> VpnKeyProvider:
    provider = (settings.vpn_provider or "api").strip().lower()
    if provider == "stub":
        from bot.providers.vpn.stub import StubVpnKeyProvider
        return StubVpnKeyProvider()
    if provider == "hiddify":
        from bot.providers.vpn.hiddify import HiddifyVpnKeyProvider
        return HiddifyVpnKeyProvider()
    from bot.providers.vpn.api import ApiVpnKeyProvider
    return ApiVpnKeyProvider()
