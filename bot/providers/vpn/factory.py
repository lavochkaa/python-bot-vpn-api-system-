from bot.config import settings
from bot.providers.vpn.base import VpnKeyProvider
from bot.providers.vpn.api import ApiVpnKeyProvider
from bot.providers.vpn.hiddify import HiddifyVpnKeyProvider
from bot.providers.vpn.stub import StubVpnKeyProvider


def build_vpn_provider() -> VpnKeyProvider:
    provider = (settings.vpn_provider or "api").strip().lower()
    if provider == "stub":
        return StubVpnKeyProvider()
    if provider == "hiddify":
        return HiddifyVpnKeyProvider()
    return ApiVpnKeyProvider()
