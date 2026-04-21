"""Factory: build a HostingProviderClient from a TrackedServer record."""
from __future__ import annotations

import json
import logging

from bot.providers.hosting.base import HostingProviderClient
from bot.providers.hosting.beget import BegetClient
from bot.providers.hosting.manual import ManualClient
from bot.providers.hosting.yandex_cloud import YandexCloudClient

logger = logging.getLogger(__name__)

_MANUAL_TYPES = {"firstbyte", "play2go", "manual"}


def build_hosting_client(server) -> HostingProviderClient | None:
    """Return appropriate HostingProviderClient for *server*, or None if unsupported.

    Args:
        server: TrackedServer ORM instance.
    """
    provider_type: str = (server.provider_type or "manual").lower()

    if provider_type == "beget":
        login = server.provider_login or ""
        secret = server.provider_secret or ""
        if not login or not secret:
            logger.warning(
                "Server %s (id=%s): Beget credentials missing (login=%r)",
                server.name,
                server.id,
                login,
            )
            return None
        return BegetClient(login=login, password=secret)

    if provider_type == "yandex_cloud":
        folder_id = server.provider_login or ""
        api_key = server.provider_secret or ""
        billing_account_id: str | None = None
        if server.provider_extra:
            try:
                extra = json.loads(server.provider_extra)
                billing_account_id = extra.get("billing_account_id")
            except (json.JSONDecodeError, TypeError):
                pass
        return YandexCloudClient(
            folder_id=folder_id,
            api_key=api_key,
            billing_account_id=billing_account_id,
        )

    if provider_type in _MANUAL_TYPES:
        return ManualClient()

    logger.warning(
        "Server %s (id=%s): unknown provider_type=%r, using ManualClient",
        server.name,
        server.id,
        provider_type,
    )
    return ManualClient()
