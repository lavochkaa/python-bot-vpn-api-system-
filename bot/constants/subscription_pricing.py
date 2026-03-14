from decimal import Decimal

# Price matrix by (duration_days, traffic_gb).
SUBSCRIPTION_PRICE_MATRIX: dict[tuple[int, int], Decimal] = {
    (30, 50): Decimal("109"),
    (30, 150): Decimal("189"),
    (30, 500): Decimal("249"),
    (30, 100): Decimal("189"),
    (30, 300): Decimal("249"),
    (90, 50): Decimal("299"),
    (90, 150): Decimal("519"),
    (90, 500): Decimal("689"),
    (90, 100): Decimal("519"),
    (90, 300): Decimal("689"),
    (180, 50): Decimal("599"),
    (180, 150): Decimal("999"),
    (180, 500): Decimal("1299"),
    (180, 100): Decimal("999"),
    (180, 300): Decimal("1299"),
    (365, 50): Decimal("1099"),
    (365, 150): Decimal("1799"),
    (365, 500): Decimal("2399"),
    (365, 100): Decimal("1799"),
    (365, 300): Decimal("2399"),
}

DURATION_OPTIONS: tuple[int, ...] = (30, 90, 180, 365)
TRAFFIC_OPTIONS: tuple[int, ...] = (50, 150, 500)
DEVICE_LIMIT_OPTIONS: tuple[int, ...] = (3, 5, 10)
DEVICE_LIMIT_PRICE_MULTIPLIERS: dict[int, Decimal] = {
    3: Decimal("1.00"),
    5: Decimal("1.25"),
    10: Decimal("1.50"),
}

# UI-facing constructor options in months.
DURATION_MONTH_OPTIONS: tuple[int, ...] = (1, 3, 6, 12)
DURATION_MONTH_TO_DAYS: dict[int, int] = {
    1: 30,
    3: 90,
    6: 180,
    12: 365,
}
