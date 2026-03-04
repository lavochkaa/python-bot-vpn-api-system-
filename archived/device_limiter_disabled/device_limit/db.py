from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


class DeviceLimitStore:
    def __init__(self, database_url: str) -> None:
        self.engine = create_async_engine(database_url, echo=False)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def close(self) -> None:
        await self.engine.dispose()

    async def ensure_schema(self) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    ALTER TABLE users
                    ADD COLUMN IF NOT EXISTS max_devices INTEGER NOT NULL DEFAULT 2
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    ALTER TABLE users
                    ADD COLUMN IF NOT EXISTS subscription_uuid VARCHAR(64)
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS ix_users_subscription_uuid
                    ON users (subscription_uuid)
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS device_sessions (
                        id SERIAL PRIMARY KEY,
                        uuid TEXT NOT NULL,
                        client_ip TEXT NOT NULL,
                        last_seen TIMESTAMPTZ NOT NULL,
                        blocked_until TIMESTAMPTZ NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        CONSTRAINT uq_device_sessions_uuid_client_ip UNIQUE (uuid, client_ip)
                    )
                    """
                )
            )
            await conn.execute(
                text("CREATE INDEX IF NOT EXISTS ix_device_sessions_uuid ON device_sessions (uuid)")
            )
            await conn.execute(
                text("CREATE INDEX IF NOT EXISTS ix_device_sessions_last_seen ON device_sessions (last_seen)")
            )

    async def touch_session(self, uuid_value: str, client_ip: str, ts: datetime) -> None:
        async with self.session_factory() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO device_sessions (uuid, client_ip, last_seen, created_at)
                    VALUES (:uuid, :client_ip, :last_seen, CURRENT_TIMESTAMP)
                    ON CONFLICT (uuid, client_ip)
                    DO UPDATE SET last_seen = EXCLUDED.last_seen
                    """
                ),
                {"uuid": uuid_value, "client_ip": client_ip, "last_seen": ts},
            )
            await session.commit()

    async def get_active_ips(self, uuid_value: str, ttl_seconds: int) -> list[str]:
        async with self.session_factory() as session:
            result = await session.execute(
                text(
                    """
                    SELECT client_ip
                    FROM device_sessions
                    WHERE uuid = :uuid
                      AND last_seen >= (CURRENT_TIMESTAMP - (:ttl * interval '1 second'))
                      AND (blocked_until IS NULL OR blocked_until <= CURRENT_TIMESTAMP)
                    """
                ),
                {"uuid": uuid_value, "ttl": ttl_seconds},
            )
            return [str(row[0]) for row in result.all()]

    async def get_blocked_until(self, uuid_value: str, client_ip: str) -> datetime | None:
        async with self.session_factory() as session:
            result = await session.execute(
                text(
                    """
                    SELECT blocked_until
                    FROM device_sessions
                    WHERE uuid = :uuid
                      AND client_ip = :client_ip
                    LIMIT 1
                    """
                ),
                {"uuid": uuid_value, "client_ip": client_ip},
            )
            return result.scalar_one_or_none()

    async def get_max_devices(self, uuid_value: str, default_limit: int = 2) -> int:
        async with self.session_factory() as session:
            result = await session.execute(
                text(
                    """
                    SELECT max_devices
                    FROM users
                    WHERE subscription_uuid = :uuid
                    LIMIT 1
                    """
                ),
                {"uuid": uuid_value},
            )
            value = result.scalar_one_or_none()
            if value is None:
                return default_limit
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                return default_limit
            return parsed if parsed > 0 else default_limit

    async def set_blocked_until(self, uuid_value: str, client_ip: str, blocked_until: datetime | None) -> None:
        async with self.session_factory() as session:
            # Keep a row for blocked IPs so admin commands can see/reset them.
            await session.execute(
                text(
                    """
                    INSERT INTO device_sessions (uuid, client_ip, last_seen, blocked_until, created_at)
                    VALUES (:uuid, :client_ip, :last_seen, :blocked_until, CURRENT_TIMESTAMP)
                    ON CONFLICT (uuid, client_ip)
                    DO UPDATE SET blocked_until = EXCLUDED.blocked_until
                    """
                ),
                {
                    "uuid": uuid_value,
                    "client_ip": client_ip,
                    "last_seen": datetime.now(timezone.utc),
                    "blocked_until": blocked_until,
                },
            )
            await session.execute(
                text(
                    """
                    UPDATE device_sessions
                    SET blocked_until = :blocked_until
                    WHERE uuid = :uuid AND client_ip = :client_ip
                    """
                ),
                {
                    "blocked_until": blocked_until,
                    "uuid": uuid_value,
                    "client_ip": client_ip,
                },
            )
            await session.commit()

    async def get_expired_blocked_ips(self, now: datetime | None = None) -> list[str]:
        now = now or datetime.now(timezone.utc)
        async with self.session_factory() as session:
            result = await session.execute(
                text(
                    """
                    SELECT DISTINCT client_ip
                    FROM device_sessions
                    WHERE blocked_until IS NOT NULL
                      AND blocked_until <= :now
                    """
                ),
                {"now": now},
            )
            return [str(row[0]) for row in result.all()]

    async def clear_expired_blocks(self, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        async with self.session_factory() as session:
            await session.execute(
                text(
                    """
                    UPDATE device_sessions
                    SET blocked_until = NULL
                    WHERE blocked_until IS NOT NULL
                      AND blocked_until <= :now
                    """
                ),
                {"now": now},
            )
            await session.commit()

    async def cleanup_stale(self, ttl_seconds: int, multiplier: int = 10) -> None:
        threshold = datetime.now(timezone.utc) - timedelta(seconds=ttl_seconds * multiplier)
        async with self.session_factory() as session:
            await session.execute(
                text(
                    """
                    DELETE FROM device_sessions
                    WHERE last_seen < :threshold
                      AND blocked_until IS NULL
                    """
                ),
                {"threshold": threshold},
            )
            await session.commit()

    async def list_active(self, uuid_value: str, ttl_seconds: int) -> list[dict]:
        async with self.session_factory() as session:
            result = await session.execute(
                text(
                    """
                    SELECT client_ip, last_seen, blocked_until
                    FROM device_sessions
                    WHERE uuid = :uuid
                      AND last_seen >= (CURRENT_TIMESTAMP - (:ttl * interval '1 second'))
                    ORDER BY last_seen DESC
                    """
                ),
                {"uuid": uuid_value, "ttl": ttl_seconds},
            )
            return [dict(row._mapping) for row in result]

    async def delete_uuid_sessions(self, uuid_value: str) -> list[str]:
        async with self.session_factory() as session:
            blocked = await session.execute(
                text(
                    """
                    SELECT DISTINCT client_ip
                    FROM device_sessions
                    WHERE uuid = :uuid
                      AND blocked_until IS NOT NULL
                    """
                ),
                {"uuid": uuid_value},
            )
            ips = [str(row[0]) for row in blocked.all()]
            await session.execute(text("DELETE FROM device_sessions WHERE uuid = :uuid"), {"uuid": uuid_value})
            await session.commit()
            return ips


async def get_session(store: DeviceLimitStore) -> AsyncSession:
    async with store.session_factory() as session:
        yield session
