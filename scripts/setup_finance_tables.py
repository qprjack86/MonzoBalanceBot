from __future__ import annotations

from core.finance_schema import (
    DEFAULT_BUDGET_TARGETS,
    DEFAULT_DEBT_TRACKER,
    DEFAULT_EMERGENCY_FUND,
    build_category_seed_entities,
)
from core.finance_settings import load_finance_settings
from stores.finance_table_store import FinanceTableStore


def main() -> None:
    settings = load_finance_settings()
    store = FinanceTableStore(settings)

    store.ensure_tables()
    for entity in build_category_seed_entities():
        store.upsert_category_mapping(entity["merchant"], entity["category"], seeded=True)

    store.seed_budget_targets(DEFAULT_BUDGET_TARGETS)
    store.seed_singleton(settings.debt_tracker_table, DEFAULT_DEBT_TRACKER)
    store.seed_singleton(settings.emergency_fund_table, DEFAULT_EMERGENCY_FUND)

    print("Finance tables ready:")
    for table_name in store.required_table_names():
        print(f"- {table_name}")


if __name__ == "__main__":
    main()
