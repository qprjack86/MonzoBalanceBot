from __future__ import annotations

import os
from datetime import UTC, datetime

from azure.data.tables import TableServiceClient, UpdateMode
from azure.identity import DefaultAzureCredential

from finance.constants import (
    DEFAULT_BUDGET_TARGETS,
    DEFAULT_DEBT_TRACKER,
    DEFAULT_EMERGENCY_FUND,
    KNOWN_MERCHANT_CATEGORY_MAP,
)

TABLES = [
    "Transactions",
    "Categories",
    "BudgetTargets",
    "DebtTracker",
    "EmergencyFund",
    "UploadState",
    "SyncState",
    "Sweeps",
    "AdviceHistory",
]


def build_service_client() -> TableServiceClient:
    endpoint = os.environ.get("AzureWebJobsStorage__tableServiceUri")
    if endpoint:
        return TableServiceClient(endpoint=endpoint, credential=DefaultAzureCredential())

    conn_str = os.environ.get("AzureWebJobsStorage")
    if not conn_str:
        raise RuntimeError("Set AzureWebJobsStorage or AzureWebJobsStorage__tableServiceUri before running setup")
    return TableServiceClient.from_connection_string(conn_str)


def main() -> None:
    service = build_service_client()

    for table_name in TABLES:
        service.create_table_if_not_exists(table_name)

    categories = service.get_table_client("Categories")
    for merchant, category in KNOWN_MERCHANT_CATEGORY_MAP.items():
        categories.upsert_entity(
            {
                "PartitionKey": "merchant",
                "RowKey": merchant,
                "merchant_key": merchant,
                "category": category,
                "seeded_at": datetime.now(UTC).isoformat(),
            },
            mode=UpdateMode.MERGE,
        )

    budget_targets = service.get_table_client("BudgetTargets")
    for target in DEFAULT_BUDGET_TARGETS:
        budget_targets.upsert_entity(
            {
                "PartitionKey": target["period"],
                "RowKey": target["name"],
                "name": target["name"],
                "period": target["period"],
                "amount_pence": target["amount_pence"],
                "seeded_at": datetime.now(UTC).isoformat(),
            },
            mode=UpdateMode.MERGE,
        )

    debt_tracker = service.get_table_client("DebtTracker")
    debt_tracker.upsert_entity(
        {
            "PartitionKey": "natwest",
            "RowKey": DEFAULT_DEBT_TRACKER["name"],
            **DEFAULT_DEBT_TRACKER,
            "seeded_at": datetime.now(UTC).isoformat(),
        },
        mode=UpdateMode.MERGE,
    )

    emergency_fund = service.get_table_client("EmergencyFund")
    emergency_fund.upsert_entity(
        {
            "PartitionKey": "emergency",
            "RowKey": DEFAULT_EMERGENCY_FUND["name"],
            **DEFAULT_EMERGENCY_FUND,
            "seeded_at": datetime.now(UTC).isoformat(),
        },
        mode=UpdateMode.MERGE,
    )

    print("Finance table setup complete.")


if __name__ == "__main__":
    main()
