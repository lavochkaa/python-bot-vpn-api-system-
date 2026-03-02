from decimal import Decimal

# Price matrix by (duration_days, traffic_gb).
SUBSCRIPTION_PRICE_MATRIX: dict[tuple[int, int], Decimal] = {
    (30, 50): Decimal("109"),
    (30, 150): Decimal("189"),
    (30, 500): Decimal("249"),
    (90, 50): Decimal("299"),
    (90, 150): Decimal("519"),
    (90, 500): Decimal("689"),
    (180, 50): Decimal("599"),
    (180, 150): Decimal("999"),
    (180, 500): Decimal("1299"),
    (365, 50): Decimal("1099"),
    (365, 150): Decimal("1799"),
    (365, 500): Decimal("2399"),
}

DURATION_OPTIONS: tuple[int, ...] = (30, 90, 180, 365)
TRAFFIC_OPTIONS: tuple[int, ...] = (50, 150, 500)
