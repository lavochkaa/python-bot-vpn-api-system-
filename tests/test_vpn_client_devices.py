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


def test_should_trust_scoped_device_payload_when_count_fits_limit() -> None:
    client = _client()

    payload = {
        "items": [
            {"deviceOs": "iOS", "deviceModel": "iPhone 15 Pro"},
            {"deviceOs": "Android", "deviceModel": "2312FPCA6G"},
        ]
    }

    assert client._should_trust_scoped_device_payload(payload, trusted_device_limit=3) is True


def test_should_not_trust_scoped_device_payload_when_count_exceeds_limit() -> None:
    client = _client()

    payload = {
        "items": [
            {"deviceOs": "Windows", "deviceModel": "Desktop"},
            {"deviceOs": "iOS", "deviceModel": "iPhone 15 Pro"},
            {"deviceOs": "Android", "deviceModel": "2312FPCA6G"},
            {"deviceOs": "macOS", "deviceModel": "Mac"},
        ]
    }

    assert client._should_trust_scoped_device_payload(payload, trusted_device_limit=3) is False


def test_get_connected_devices_accepts_plausible_direct_payload_without_owner_fields() -> None:
    client = _client()

    user = {
        "uuid": "target-user-uuid",
        "id": 42,
        "hwidDeviceLimit": 3,
        "hwidDevices": [
            {"deviceOs": "iOS", "deviceModel": "iPhone 15 Pro"},
            {"deviceOs": "Android", "deviceModel": "2312FPCA6G"},
        ],
    }

    devices = asyncio.run(client._get_connected_devices(user))

    assert devices == [
        "iOS - iPhone 15 Pro",
        "Android - 2312FPCA6G",
    ]


def test_get_connected_devices_accepts_plausible_query_payload_without_owner_fields() -> None:
    client = _client()

    async def fake_request_optional_json(
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
    ) -> dict[str, object] | None:
        _ = (method, path)
        if params == {"userUuid": "target-user-uuid"}:
            return {
                "items": [
                    {"deviceOs": "iOS", "deviceModel": "iPhone 15 Pro"},
                    {"deviceOs": "Android", "deviceModel": "2312FPCA6G"},
                ]
            }
        return None

    client._request_optional_json = fake_request_optional_json  # type: ignore[method-assign]

    devices = asyncio.run(
        client._get_connected_devices(
            {
                "uuid": "target-user-uuid",
                "id": 42,
                "hwidDeviceLimit": 3,
            }
        )
    )

    assert devices == [
        "iOS - iPhone 15 Pro",
        "Android - 2312FPCA6G",
    ]


def test_pick_default_internal_squad_uuid_prefers_lowest_view_position() -> None:
    client = _client()

    payload = {
        "internalSquads": [
            {"uuid": "squad-a", "viewPosition": 20},
            {"uuid": "squad-b", "viewPosition": 10},
        ]
    }

    assert client._pick_default_internal_squad_uuid(payload) == "squad-b"


def test_pick_default_internal_squad_uuid_falls_back_to_first_available() -> None:
    client = _client()

    payload = {
        "items": [
            {"uuid": "squad-a"},
            {"uuid": "squad-b"},
        ]
    }

    assert client._pick_default_internal_squad_uuid(payload) == "squad-a"


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
