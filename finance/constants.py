from __future__ import annotations

CATEGORY_UNCATEGORISED = "uncategorised"

KNOWN_MERCHANT_CATEGORY_MAP: dict[str, str] = {
    # Eating out
    "mcdonalds": "eating out",
    "nandos": "eating out",
    "pizza hut": "eating out",
    "dominos": "eating out",
    "just eat": "eating out",
    "cox's at the lighthouse": "eating out",
    "new bodrum": "eating out",
    "greggs": "eating out",
    "alpha drive-thru": "eating out",
    # Coffee
    "perky beans": "coffee",
    "starbucks": "coffee",
    # Groceries
    "lidl": "groceries",
    "aldi": "groceries",
    "tesco": "groceries",
    "iceland": "groceries",
    "spar": "groceries",
    # Subscriptions
    "spotify": "subscriptions",
    "youtube premium": "subscriptions",
    "disney plus": "subscriptions",
    "microsoft 365": "subscriptions",
    "sky mobile": "subscriptions",
    "tesco mobile": "subscriptions",
    "oculus": "subscriptions",
    "plusnet": "subscriptions",
    # Gambling
    "sky betting and gaming": "gambling",
    # Shopping
    "amazon": "shopping",
    "superdrug": "shopping",
    "b&m": "shopping",
    "home bargains": "shopping",
    "card factory": "shopping",
    # Transport
    "stagecoach": "transport",
    "obn miperit": "transport",
    "obn mipermit": "transport",
    "euro car parks": "transport",
}

DEFAULT_BUDGET_TARGETS = [
    {"name": "weekly_discretionary", "period": "weekly", "amount_pence": 10700},
]

DEFAULT_DEBT_TRACKER = {
    "name": "natwest_card",
    "target_balance_pence": 0,
    "current_balance_pence": 331900,
    "monthly_payment_target_pence": 9300,
    "target_months": 36,
}

DEFAULT_EMERGENCY_FUND = {
    "name": "main",
    "current_balance_pence": 0,
    "target_balance_pence": 720000,
    "monthly_contribution_target_pence": 20000,
}
