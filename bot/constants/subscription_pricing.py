from decimal import Decimal

# Price matrix by (duration_days, traffic_gb).
SUBSCRIPTION_PRICE_MATRIX: dict[tuple[int, int], Decimal] = {
    (30, 50): Decimal("109"),
    (30, 150): Decimal("189"),
    (30, 500): Decimal("249"),
    (60, 50): Decimal("199"),
    (60, 150): Decimal("349"),
    (60, 500): Decimal("459"),
    (365, 50): Decimal("899"),
    (365, 150): Decimal("1499"),
    (365, 500): Decimal("2399"),
}

DURATION_OPTIONS: tuple[int, ...] = (30, 60, 365)
TRAFFIC_OPTIONS: tuple[int, ...] = (50, 150, 500)
