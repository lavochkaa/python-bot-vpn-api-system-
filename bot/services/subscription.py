from datetime import datetime, timezone, timedelta
from decimal import Decimal
import json
import logging
import re
from urllib.parse import urlparse
from uuid import UUID
from bot.db.models import BalanceLedger, Subscription, VpnKey
from bot.constants.subscription_pricing import SUBSCRIPTION_PRICE_MATRIX, DEVICE_LIMIT_PRICE_MULTIPLIERS
from bot.repositories.ledger import BalanceLedgerRepository
from bot.repositories.subscription import SubscriptionRepository
from bot.repositories.plan import PlanRepository
from bot.repositories.user import UserRepository
from bot.repositories.vpn_key import VpnKeyRepository
from bot.providers.vpn.base import VpnKeyProvider

logger = logging.getLogger(__name__)
UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{12}"
)


def _normalize_uuid(value: str | None) -> str | None:
    if not value:
        return None
    candidate = value.strip().lower()
    try:
        return str(UUID(candidate))
    except (ValueError, AttributeError):
        return None


def _uuid_from_key(key: str | None) -> str | None:
    if not key:
        return None
    match = UUID_RE.search(key)
    if match:
        return _normalize_uuid(match.group(0))
    if key.startswith(("http://", "https://")):
        parsed = urlparse(key)
        for chunk in parsed.path.split("/"):
            maybe = _normalize_uuid(chunk)
            if maybe:
                return maybe
    return None


def _uuid_from_meta(meta: dict | None) -> str | None:
    if not meta:
        return None
    scan: list[object] = [meta]
    while scan:
        node = scan.pop()
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, (dict, list)):
                    scan.append(v)
                elif isinstance(v, str):
                    if k.lower() in {"uuid", "user_uuid", "client_uuid", "subscription_uuid"}:
                        normalized = _normalize_uuid(v)
                        if normalized:
                            return normalized
                    match = UUID_RE.search(v)
                    if match:
                        normalized = _normalize_uuid(match.group(0))
                        if normalized:
                            return normalized
        elif isinstance(node, list):
            scan.extend(node)
    return None


class SubscriptionService:
    def __init__(
        self,
        sub_repo: SubscriptionRepository,
        plan_repo: PlanRepository,
        user_repo: UserRepository,
        ledger_repo: BalanceLedgerRepository,
        key_repo: VpnKeyRepository,
        vpn_provider: VpnKeyProvider,
    ):
        self.sub_repo = sub_repo
        self.plan_repo = plan_repo
        self.user_repo = user_repo
        self.ledger_repo = ledger_repo
        self.key_repo = key_repo
        self.vpn_provider = vpn_provider

    def calculate_constructor_price(
        self,
        plan_type: str,
        traffic_gb: int,
        duration_days: int,
        device_limit: int = 3,
    ) -> Decimal:
        """Server-side price calculation for constructor."""
        _ = plan_type  # plan type is kept for compatibility with existing flow/state.
        base_price = SUBSCRIPTION_PRICE_MATRIX.get((duration_days, traffic_gb))
        multiplier = DEVICE_LIMIT_PRICE_MULTIPLIERS.get(int(device_limit or 3))
        if base_price is None or multiplier is None:
            raise ValueError("Неверные параметры тарифа.")
        price = base_price * multiplier
        return price.quantize(Decimal("0.01"))

    async def purchase_with_balance(
        self,
        user_id: int,
        plan_id: int,
        final_price: Decimal,
        period_days: int | None = None,
        plan_type: str | None = None,
        traffic_gb: int | None = None,
        device_limit: int | None = None,
        build_preset: str | None = None,
    ) -> Subscription:
        session = self.user_repo.session
        try:
            user = await self.user_repo.get_by_tg_id_for_update(user_id)
            if not user:
                raise ValueError("Пользователь не найден.")
            if user.balance < final_price:
                raise ValueError("Недостаточно средств на балансе.")
            plan = await self.plan_repo.get(plan_id)
            if not plan:
                raise ValueError("Тариф не найден.")

            key_data = await self.vpn_provider.issue_key(
                user_id=user_id,
                plan_slug=plan.slug,
                traffic_gb=traffic_gb,
                duration_days=period_days,
                device_limit=device_limit,
                build_preset=build_preset,
            )
            provider_sub_id = str(
                key_data.meta.get("subscription_id")
                or key_data.meta.get("id")
                or key_data.meta.get("uuid")
                or ""
            ) or None
            payload_json = json.dumps(key_data.meta, ensure_ascii=False) if key_data.meta else None
            subscription_uuid = _uuid_from_meta(key_data.meta) or _uuid_from_key(key_data.key)
            if subscription_uuid:
                owner = await self.user_repo.get_by_subscription_uuid(subscription_uuid)
                if owner is None or owner.id == user.id:
                    user.subscription_uuid = subscription_uuid
                else:
                    logger.warning(
                        "Skip assigning duplicate subscription_uuid=%s to user_id=%s (already owned by user_id=%s)",
                        subscription_uuid,
                        user.id,
                        owner.id,
                    )

            user.balance -= final_price
            session.add(
                BalanceLedger(
                    user_id=user_id,
                    amount=-final_price,
                    reason="subscription_purchase",
                )
            )

            sub = await self.activate(
                user_id=user_id,
                plan_id=plan_id,
                period_days=period_days,
                plan_type=plan_type,
                traffic_gb=traffic_gb,
                device_limit=device_limit,
                build_preset=build_preset,
                final_price=final_price,
                provider_subscription_id=provider_sub_id,
                payload_json=payload_json,
                preissued_key=key_data.key,
            )
            if device_limit is not None:
                user.max_devices = int(device_limit)
            await session.commit()
            await session.refresh(sub)
            return sub
        except ValueError:
            await session.rollback()
            raise
        except Exception as exc:
            await session.rollback()
            logger.exception("Subscription activation failed for user_id=%s plan_id=%s", user_id, plan_id)
            raise ValueError("Не удалось активировать подписку. Средства не списаны.") from exc

    async def activate(
        self,
        user_id: int,
        plan_id: int,
        period_days: int | None = None,
        plan_type: str | None = None,
        traffic_gb: int | None = None,
        device_limit: int | None = None,
        build_preset: str | None = None,
        final_price: Decimal | None = None,
        provider_subscription_id: str | None = None,
        payload_json: str | None = None,
        preissued_key: str | None = None,
    ) -> Subscription:
        """
        Activate subscription for user:
        1. Deactivate current active subscription
        2. Create new subscription
        3. Issue VPN key
        """
        plan = await self.plan_repo.get(plan_id)
        if not plan:
            raise ValueError("Тариф не найден.")

        # Deactivate current subscription if exists
        current = await self.sub_repo.get_active(user_id)
        if current:
            current.is_active = False
            self.sub_repo.session.add(current)
            # TODO: optionally revoke old VPN key via vpn_provider.revoke_key()

        now = datetime.now(timezone.utc)
        subscription_days = period_days or plan.period_days
        sub = Subscription(
            user_id=user_id,
            plan_id=plan_id,
            is_active=True,
            started_at=now,
            expires_at=now + timedelta(days=subscription_days),
            duration_days=subscription_days,
            plan_type=plan_type,
            traffic_gb=traffic_gb,
            build_preset=build_preset,
            price=final_price,
            status="active",
            provider_subscription_id=provider_subscription_id,
            payload_json=payload_json,
        )
        self.sub_repo.session.add(sub)
        await self.sub_repo.session.flush()
        if device_limit is not None:
            user = await self.user_repo.get_by_tg_id_for_update(user_id)
            if user:
                user.max_devices = int(device_limit)
                self.sub_repo.session.add(user)

        if not preissued_key:
            raise ValueError("VPN key was not issued.")
        vpn_key = VpnKey(
            user_id=user_id,
            key=preissued_key,
            plan_id=plan_id,
            subscription_id=sub.id,
        )
        self.key_repo.session.add(vpn_key)

        return sub

    async def sync_existing_active_subscription(self, user_id: int) -> Subscription:
        session = self.user_repo.session
        try:
            user = await self.user_repo.get_by_tg_id_for_update(user_id)
            if not user:
                raise ValueError("Пользователь не найден.")

            sub = await self.sub_repo.get_active(user_id)
            if not sub or not sub.is_active:
                raise ValueError("Активная подписка не найдена.")

            now = datetime.now(timezone.utc)
            if sub.expires_at and sub.expires_at <= now:
                raise ValueError("Подписка уже истекла.")

            plan = sub.plan or await self.plan_repo.get(sub.plan_id)
            if not plan:
                raise ValueError("Тариф не найден.")

            if sub.expires_at:
                remaining_seconds = max(0, int((sub.expires_at - now).total_seconds()))
                remaining_days = max(1, (remaining_seconds + 86399) // 86400)
            else:
                remaining_days = max(1, int(sub.duration_days or plan.period_days or 30))

            key_data = await self.vpn_provider.issue_key(
                user_id=user_id,
                plan_slug=plan.slug,
                traffic_gb=sub.traffic_gb,
                duration_days=remaining_days,
                device_limit=user.max_devices if user else None,
                build_preset=sub.build_preset,
            )

            provider_sub_id = str(
                key_data.meta.get("subscription_id")
                or key_data.meta.get("id")
                or key_data.meta.get("uuid")
                or ""
            ) or None
            payload_json = json.dumps(key_data.meta, ensure_ascii=False) if key_data.meta else None
            subscription_uuid = _uuid_from_meta(key_data.meta) or _uuid_from_key(key_data.key)
            if subscription_uuid:
                owner = await self.user_repo.get_by_subscription_uuid(subscription_uuid)
                if owner is None or owner.id == user.id:
                    user.subscription_uuid = subscription_uuid
                else:
                    logger.warning(
                        "Skip assigning duplicate subscription_uuid=%s to user_id=%s (already owned by user_id=%s)",
                        subscription_uuid,
                        user.id,
                        owner.id,
                    )

            sub.provider_subscription_id = provider_sub_id
            sub.payload_json = payload_json

            existing_keys = await self.key_repo.get_user_keys(user_id, limit=1)
            if existing_keys:
                vpn_key = existing_keys[0]
                vpn_key.key = key_data.key
                vpn_key.plan_id = sub.plan_id
                vpn_key.subscription_id = sub.id
            else:
                vpn_key = VpnKey(
                    user_id=user_id,
                    key=key_data.key,
                    plan_id=sub.plan_id,
                    subscription_id=sub.id,
                )
            session.add(user)
            session.add(sub)
            session.add(vpn_key)
            await session.commit()
            await session.refresh(sub)
            return sub
        except ValueError:
            await session.rollback()
            raise
        except Exception as exc:
            await session.rollback()
            logger.exception("Active subscription sync failed for user_id=%s", user_id)
            raise ValueError("Не удалось синхронизировать подписку с новой панелью.") from exc
