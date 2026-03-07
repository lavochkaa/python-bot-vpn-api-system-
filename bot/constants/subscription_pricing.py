from decimal import Decimal

# Price matrix by (duration_days, traffic_gb).
SUBSCRIPTION_PRICE_MATRIX: dict[tuple[int, int], Decimal] = {
    (30, 50): Decimal("109"),
    (30, 100): Decimal("189"),
    (30, 300): Decimal("249"),
    (90, 50): Decimal("299"),
    (90, 100): Decimal("519"),
    (90, 300): Decimal("689"),
    (180, 50): Decimal("599"),
    (180, 100): Decimal("999"),
    (180, 300): Decimal("1299"),
    (365, 50): Decimal("1099"),
    (365, 100): Decimal("1799"),
    (365, 300): Decimal("2399"),
}

DURATION_OPTIONS: tuple[int, ...] = (30, 90, 180, 365)
TRAFFIC_OPTIONS: tuple[int, ...] = (50, 100, 300)

# UI-facing constructor options in months.
DURATION_MONTH_OPTIONS: tuple[int, ...] = (1, 3, 6, 12)
DURATION_MONTH_TO_DAYS: dict[int, int] = {
    1: 30,
    3: 90,
    6: 180,
    12: 365,
}
