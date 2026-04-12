from __future__ import annotations

from datetime import UTC, datetime


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


DEFAULT_BUDGET_TARGETS = [
    {
        "PartitionKey": "weekly",
        "RowKey": "discretionary",
        "target_pence": 10700,
        "period": "weekly",
        "category": "discretionary",
    }
]


DEFAULT_DEBT_TRACKER = {
    "PartitionKey": "natwest",
    "RowKey": "primary",
    "target_months": 36,
    "monthly_payment_target_pence": 9300,
    "starting_balance_pence": 331900,
    "current_balance_pence": 331900,
    "updated_at": utc_now_iso(),
}


DEFAULT_EMERGENCY_FUND = {
    "PartitionKey": "main",
    "RowKey": "primary",
    "target_pence": 720000,
    "current_balance_pence": 0,
    "monthly_contribution_target_pence": 20000,
    "updated_at": utc_now_iso(),
}


CATEGORY_SEED_MAP: dict[str, list[str]] = {
    "eating out": [
        "McDonalds",
        "Nandos",
        "Pizza Hut",
        "Dominos",
        "Just Eat",
        "Cox's at the Lighthouse",
        "New Bodrum",
        "Greggs",
        "Alpha Drive-Thru",
    ],
    "coffee": ["Perky Beans", "Starbucks"],
    "groceries": ["Lidl", "Aldi", "Tesco", "Iceland", "Spar"],
    "subscriptions": [
        "Spotify",
        "YouTube Premium",
        "Disney Plus",
        "Microsoft 365",
        "Sky Mobile",
        "Tesco Mobile",
        "Oculus",
        "Plusnet",
    ],
    "gambling": ["Sky Betting and Gaming"],
    "shopping": ["Amazon", "Superdrug", "B&M", "Home Bargains", "Card Factory"],
    "transport": ["Stagecoach", "OBN MiPermit", "Euro Car Parks"],
}


def normalize_merchant(merchant: str) -> str:
    cleaned = " ".join((merchant or "").strip().lower().split())
    return cleaned


def build_category_seed_entities() -> list[dict]:
    entities: list[dict] = []
    now = utc_now_iso()
    for category, merchants in CATEGORY_SEED_MAP.items():
        for merchant in merchants:
            normalized = normalize_merchant(merchant)
            entities.append(
                {
                    "PartitionKey": "merchant",
                    "RowKey": normalized,
                    "merchant": merchant,
                    "category": category,
                    "seeded": True,
                    "updated_at": now,
                }
            )
    return entities
