from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
from bot.db.models import PromoCode, DiscountType, PromoTarget
from bot.repositories.promo import PromoRepository
from bot.repositories.user import UserRepository


class PromoService:
    def __init__(self, repo: PromoRepository, user_repo: UserRepository):
        self.repo = repo
        self.user_repo = user_repo

    async def validate_and_apply(
        self,
        code: str,
        user_id: int,
        base_amount: Decimal,
        target: PromoTarget | None = None,
    ) -> tuple[Decimal, PromoCode | None]:
        """
        Validate promo code and compute discounted amount.
        Returns (final_amount, promo).
        Raises ValueError with user-friendly message on invalid promo.
        """
        promo = await self.repo.get_by_code(code.upper())
        if not promo or not promo.is_active:
            raise ValueError("Промокод не найден или неактивен.")

        now = datetime.now(timezone.utc)
        if promo.valid_until and promo.valid_until < now:
            raise ValueError("Срок действия промокода истёк.")

        if promo.max_activations is not None and promo.activations_count >= promo.max_activations:
            raise ValueError("Промокод уже исчерпан.")

        if await self.repo.has_user_redeemed(promo.id, user_id):
            raise ValueError("Вы уже использовали этот промокод.")

        if target is not None and promo.target is not None and promo.target != target:
            raise ValueError("Этот промокод нельзя применить в выбранном разделе.")

        user = await self.user_repo.get_by_tg_id(user_id)
        if not user:
            raise ValueError("Пользователь не найден.")

        if promo.allowed_user_ids:
            allowed_ids = {
                int(chunk.strip()) for chunk in promo.allowed_user_ids.split(",") if chunk.strip().isdigit()
            }
            if user_id not in allowed_ids:
                raise ValueError("Этот промокод недоступен для вашего аккаунта.")

        if promo.allowed_segment and user.segment != promo.allowed_segment:
            raise ValueError("Этот промокод недоступен для вашего сегмента.")

        if promo.discount_type == DiscountType.percent:
            discount = base_amount * promo.discount_value / Decimal("100")
        else:
            discount = promo.discount_value

        final = max(base_amount - discount, Decimal("0"))
        final = final.quantize(Decimal("1"), rounding=ROUND_CEILING)
        return final, promo

    async def mark_redeemed(self, promo_id: int, user_id: int) -> None:
        """Mark promo as used by user and increment counter."""
        await self.repo.add_redemption(promo_id, user_id)
        promo = await self.repo.get(promo_id)
        if promo:
            promo.activations_count += 1
            await self.repo.save(promo)
