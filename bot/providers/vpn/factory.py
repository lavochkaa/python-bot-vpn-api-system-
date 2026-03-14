from functools import lru_cache

from bot.config import settings
from bot.providers.vpn.base import VpnKeyProvider


@lru_cache(maxsize=1)
def _build_cached_vpn_provider(
    provider: str,
    vpn_api_base_url: str,
    vpn_api_key: str,
    vpn_api_timeout_seconds: int,
    vpn_api_verify_ssl: bool,
    hiddify_api_key: str,
    hiddify_verify_ssl: bool,
) -> VpnKeyProvider:
    _ = (
        vpn_api_base_url,
        vpn_api_key,
        vpn_api_timeout_seconds,
        vpn_api_verify_ssl,
        hiddify_api_key,
        hiddify_verify_ssl,
    )
    if provider == "stub":
        from bot.providers.vpn.stub import StubVpnKeyProvider
        return StubVpnKeyProvider()
    if provider == "hiddify":
        from bot.providers.vpn.hiddify import HiddifyVpnKeyProvider
        return HiddifyVpnKeyProvider()
    from bot.providers.vpn.api import ApiVpnKeyProvider
    return ApiVpnKeyProvider()


def build_vpn_provider() -> VpnKeyProvider:
    provider = (settings.vpn_provider or "api").strip().lower()
    return _build_cached_vpn_provider(
        provider,
        settings.vpn_api_base_url,
        settings.vpn_api_key,
        settings.vpn_api_timeout_seconds,
        settings.vpn_api_verify_ssl,
        settings.hiddify_api_key,
        settings.hiddify_verify_ssl,
    )
