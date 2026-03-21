import asyncio

from bot.clients.vpn_client import VpnApiClient, VpnClientConfig


def _client() -> VpnApiClient:
    return VpnApiClient(
        VpnClientConfig(
            base_url="https://example.com",
            api_key="test-key",
        )
    )


def test_extract_connected_devices_ignores_unscoped_global_payload() -> None:
    client = _client()

    payload = {
        "items": [
            {"deviceOs": "iOS", "deviceModel": "iPhone 15 Pro"},
            {"deviceOs": "Android", "deviceModel": "2312FPCA6G"},
        ]
    }

    devices = client._extract_connected_devices(
        payload,
        user_uuid="target-user-uuid",
        user_id="42",
        scoped_payload=False,
    )

    assert devices == []


def test_extract_connected_devices_accepts_scoped_payload_without_owner_fields() -> None:
    client = _client()

    payload = {
        "items": [
            {"deviceOs": "iOS", "deviceModel": "iPhone 15 Pro"},
            {"deviceOs": "Android", "deviceModel": "2312FPCA6G"},
        ]
    }

    devices = client._extract_connected_devices(
        payload,
        user_uuid="target-user-uuid",
        user_id="42",
        scoped_payload=True,
    )

    assert devices == [
        "iOS - iPhone 15 Pro",
        "Android - 2312FPCA6G",
    ]


def test_extract_connected_devices_filters_global_payload_by_owner() -> None:
    client = _client()

    payload = {
        "items": [
            {"userUuid": "other-user", "deviceOs": "Windows", "deviceModel": "Desktop"},
            {"userUuid": "target-user-uuid", "deviceOs": "iOS", "deviceModel": "iPhone 14 Pro"},
            {"ownerId": 42, "deviceOs": "macOS", "deviceModel": "Mac"},
        ]
    }

    devices = client._extract_connected_devices(
        payload,
        user_uuid="target-user-uuid",
        user_id="42",
        scoped_payload=False,
    )

    assert devices == [
        "iOS - iPhone 14 Pro",
        "macOS - Mac",
    ]


def test_extract_connected_devices_does_not_trust_nested_user_payload_without_owner_fields() -> None:
    client = _client()

    payload = {
        "uuid": "target-user-uuid",
        "id": 42,
        "hwidDevices": [
            {"deviceOs": "Windows", "deviceModel": "Desktop"},
            {"deviceOs": "iOS", "deviceModel": "iPhone 15 Pro"},
        ],
    }

    devices = client._extract_connected_devices(
        payload,
        user_uuid="target-user-uuid",
        user_id="42",
        scoped_payload=False,
    )

    assert devices == []


def test_get_user_usage_counts_only_filtered_devices() -> None:
    client = _client()

    async def fake_find_user(**_: object) -> dict[str, object]:
        return {
            "uuid": "target-user-uuid",
            "id": 42,
            "trafficUsedBytes": 1024,
            "trafficLimitBytes": 2048,
            "total": 19,
        }

    async def fake_get_connected_devices(user: dict[str, object]) -> list[str]:
        assert user["uuid"] == "target-user-uuid"
        return [
            "iOS - iPhone 14 Pro",
            "macOS - Mac",
        ]

    client.find_user = fake_find_user  # type: ignore[method-assign]
    client._get_connected_devices = fake_get_connected_devices  # type: ignore[method-assign]

    usage = asyncio.run(client.get_user_usage(uuid="target-user-uuid"))

    assert usage is not None
    assert usage["connected_devices_count"] == 2
