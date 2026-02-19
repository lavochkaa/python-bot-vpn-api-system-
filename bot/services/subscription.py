from datetime import datetime, timezone, timedelta
from decimal import Decimal
from bot.db.models import BalanceLedger, Subscription, VpnKey
from bot.repositories.ledger import BalanceLedgerRepository
from bot.repositories.subscription import SubscriptionRepository
from bot.repositories.plan import PlanRepository
from bot.repositories.user import UserRepository
from bot.repositories.vpn_key import VpnKeyRepository
from bot.providers.vpn.base import VpnKeyProvider


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

    async def purchase_with_balance(
        self,
        user_id: int,
        plan_id: int,
        final_price: Decimal,
        period_days: int | None = None,
    ) -> Subscription:
        # TODO: wrap debit+activation into a single DB transaction after repositories stop auto-committing.
        user = await self.user_repo.get_by_tg_id_for_update(user_id)
        if not user:
            raise ValueError("Пользователь не найден.")
        if user.balance < final_price:
            raise ValueError("Недостаточно средств на балансе.")

        user.balance -= final_price
        await self.user_repo.session.commit()
        await self.user_repo.session.refresh(user)
        await self.ledger_repo.save(
            BalanceLedger(
                user_id=user_id,
                amount=-final_price,
                reason="subscription_purchase",
            )
        )
        return await self.activate(user_id=user_id, plan_id=plan_id, period_days=period_days)

    async def activate(self, user_id: int, plan_id: int, period_days: int | None = None) -> Subscription:
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
            await self.sub_repo.save(current)
            # TODO: optionally revoke old VPN key via vpn_provider.revoke_key()

        now = datetime.now(timezone.utc)
        subscription_days = period_days or plan.period_days
        sub = Subscription(
            user_id=user_id,
            plan_id=plan_id,
            is_active=True,
            started_at=now,
            expires_at=now + timedelta(days=subscription_days),
        )
        await self.sub_repo.save(sub)

        # Issue VPN key automatically
        key_data = await self.vpn_provider.issue_key(user_id, plan.slug)
        vpn_key = VpnKey(
            user_id=user_id,
            key=key_data.key,
            plan_id=plan_id,
            subscription_id=sub.id,
        )
        await self.key_repo.save(vpn_key)

        return sub
