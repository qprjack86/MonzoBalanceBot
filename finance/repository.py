from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Optional

from azure.core.exceptions import ResourceNotFoundError
from azure.data.tables import TableClient, TableServiceClient, UpdateMode
from azure.identity import DefaultAzureCredential

from core.settings import Settings


@dataclass(frozen=True)
class TransactionRecord:
    source: str
    external_id: str
    amount_pence: int
    merchant: str
    date_iso: str
    category: str = "uncategorised"
    currency: str = "GBP"
    metadata: Optional[dict[str, Any]] = None


class FinanceRepository:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._service_client: Optional[TableServiceClient] = None

    def _get_service_client(self) -> TableServiceClient:
        if self._service_client:
            return self._service_client

        table_endpoint = os.environ.get("AzureWebJobsStorage__tableServiceUri")
        if table_endpoint:
            self._service_client = TableServiceClient(endpoint=table_endpoint, credential=DefaultAzureCredential())
            return self._service_client

        conn_str = os.environ.get("AzureWebJobsStorage")
        if not conn_str:
            raise RuntimeError("Storage configuration missing. Set AzureWebJobsStorage or AzureWebJobsStorage__tableServiceUri")
        self._service_client = TableServiceClient.from_connection_string(conn_str)
        return self._service_client

    def _table(self, table_name: str) -> TableClient:
        service = self._get_service_client()
        service.create_table_if_not_exists(table_name)
        return service.get_table_client(table_name)

    def _tx_row_key(self, record: TransactionRecord) -> str:
        raw = f"{record.source}|{record.external_id}|{record.date_iso}|{record.amount_pence}|{record.merchant.lower()}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def upsert_transaction(self, record: TransactionRecord) -> tuple[bool, str]:
        table = self._table(self.settings.transactions_table_name)
        row_key = self._tx_row_key(record)

        try:
            table.get_entity(partition_key=record.source, row_key=row_key)
            return False, row_key
        except ResourceNotFoundError:
            pass

        payload = {
            "PartitionKey": record.source,
            "RowKey": row_key,
            "external_id": record.external_id,
            "amount_pence": int(record.amount_pence),
            "merchant": record.merchant,
            "date_iso": record.date_iso,
            "category": record.category,
            "currency": record.currency,
            "metadata_json": json.dumps(record.metadata or {}, ensure_ascii=True),
            "created_at": datetime.now(UTC).isoformat(),
        }
        table.upsert_entity(payload, mode=UpdateMode.MERGE)
        return True, row_key

    def get_transaction(self, source: str, row_key: str) -> dict[str, Any]:
        table = self._table(self.settings.transactions_table_name)
        return table.get_entity(partition_key=source, row_key=row_key)

    def update_transaction_category(self, source: str, row_key: str, category: str) -> None:
        table = self._table(self.settings.transactions_table_name)
        table.upsert_entity(
            {
                "PartitionKey": source,
                "RowKey": row_key,
                "category": category,
                "categorised_at": datetime.now(UTC).isoformat(),
            },
            mode=UpdateMode.MERGE,
        )

    def get_categories_map(self) -> dict[str, str]:
        table = self._table(self.settings.categories_table_name)
        categories: dict[str, str] = {}
        for entity in table.list_entities():
            merchant_key = str(entity.get("merchant_key", "")).strip().lower()
            category = str(entity.get("category", "")).strip().lower()
            if merchant_key and category:
                categories[merchant_key] = category
        return categories

    def upsert_category(self, merchant_key: str, category: str) -> None:
        table = self._table(self.settings.categories_table_name)
        norm_key = merchant_key.strip().lower()
        table.upsert_entity(
            {
                "PartitionKey": "merchant",
                "RowKey": norm_key,
                "merchant_key": norm_key,
                "category": category.strip().lower(),
                "updated_at": datetime.now(UTC).isoformat(),
            },
            mode=UpdateMode.MERGE,
        )

    def upload_seen(self, source: str, file_hash: str) -> bool:
        table = self._table(self.settings.upload_state_table_name)
        row_key = f"{source}:{file_hash}"
        try:
            table.get_entity(partition_key="upload", row_key=row_key)
            return True
        except ResourceNotFoundError:
            return False

    def mark_upload_seen(self, source: str, blob_name: str, file_hash: str) -> None:
        table = self._table(self.settings.upload_state_table_name)
        row_key = f"{source}:{file_hash}"
        table.upsert_entity(
            {
                "PartitionKey": "upload",
                "RowKey": row_key,
                "source": source,
                "blob_name": blob_name,
                "file_hash": file_hash,
                "processed_at": datetime.now(UTC).isoformat(),
            },
            mode=UpdateMode.MERGE,
        )

    def set_sync_cursor(self, name: str, cursor_iso: str) -> None:
        table = self._table(self.settings.sync_state_table_name)
        table.upsert_entity(
            {
                "PartitionKey": "cursor",
                "RowKey": name,
                "cursor_iso": cursor_iso,
                "updated_at": datetime.now(UTC).isoformat(),
            },
            mode=UpdateMode.MERGE,
        )

    def get_sync_cursor(self, name: str) -> Optional[str]:
        table = self._table(self.settings.sync_state_table_name)
        try:
            entity = table.get_entity(partition_key="cursor", row_key=name)
            return entity.get("cursor_iso")
        except ResourceNotFoundError:
            return None

    def list_recent_transactions(self, limit: int = 20, category: Optional[str] = None) -> list[dict[str, Any]]:
        table = self._table(self.settings.transactions_table_name)
        entities = list(table.list_entities())
        if category:
            target = category.strip().lower()
            entities = [entity for entity in entities if str(entity.get("category", "")).strip().lower() == target]

        entities.sort(key=lambda item: str(item.get("date_iso", "")), reverse=True)
        return entities[:limit]

    def weekly_spend_pence(self, week_start_iso: str, week_end_iso: str) -> int:
        table = self._table(self.settings.transactions_table_name)
        total = 0
        for entity in table.list_entities():
            date_iso = str(entity.get("date_iso", ""))
            if not date_iso or date_iso < week_start_iso or date_iso > week_end_iso:
                continue

            category = str(entity.get("category", "")).strip().lower()
            if category in {"transfers", "uncategorised"}:
                continue

            amount_pence = int(entity.get("amount_pence", 0) or 0)
            if amount_pence < 0:
                total += abs(amount_pence)
        return total

    def weekly_spend_breakdown(self, week_start_iso: str, week_end_iso: str) -> dict[str, int]:
        table = self._table(self.settings.transactions_table_name)
        breakdown: dict[str, int] = {}
        for entity in table.list_entities():
            date_iso = str(entity.get("date_iso", ""))
            if not date_iso or date_iso < week_start_iso or date_iso > week_end_iso:
                continue

            category = str(entity.get("category", "uncategorised")).strip().lower() or "uncategorised"
            if category in {"transfers", "uncategorised"}:
                continue

            amount_pence = int(entity.get("amount_pence", 0) or 0)
            if amount_pence < 0:
                breakdown[category] = breakdown.get(category, 0) + abs(amount_pence)
        return breakdown

    def get_budget_target(self, period: str, name: str) -> Optional[dict[str, Any]]:
        table = self._table(self.settings.budget_targets_table_name)
        try:
            return table.get_entity(partition_key=period, row_key=name)
        except ResourceNotFoundError:
            return None

    def get_debt_tracker(self) -> Optional[dict[str, Any]]:
        table = self._table(self.settings.debt_tracker_table_name)
        try:
            return table.get_entity(partition_key="natwest", row_key="natwest_card")
        except ResourceNotFoundError:
            return None

    def upsert_debt_tracker(self, payload: dict[str, Any]) -> None:
        table = self._table(self.settings.debt_tracker_table_name)
        table.upsert_entity(
            {
                "PartitionKey": "natwest",
                "RowKey": "natwest_card",
                **payload,
                "updated_at": datetime.now(UTC).isoformat(),
            },
            mode=UpdateMode.MERGE,
        )

    def latest_source_balance_from_metadata(self, source: str, field_name: str = "balance_pence") -> Optional[int]:
        table = self._table(self.settings.transactions_table_name)
        latest_balance: Optional[int] = None
        latest_date = ""
        for entity in table.list_entities():
            if str(entity.get("PartitionKey", "")) != source:
                continue
            metadata_text = str(entity.get("metadata_json", "") or "{}")
            try:
                metadata = json.loads(metadata_text)
            except json.JSONDecodeError:
                metadata = {}
            if field_name not in metadata:
                continue
            date_iso = str(entity.get("date_iso", ""))
            if date_iso >= latest_date:
                latest_date = date_iso
                latest_balance = int(metadata[field_name])
        return latest_balance

    def get_emergency_fund(self) -> Optional[dict[str, Any]]:
        table = self._table(self.settings.emergency_fund_table_name)
        try:
            return table.get_entity(partition_key="emergency", row_key="main")
        except ResourceNotFoundError:
            return None

    def upsert_emergency_fund(self, payload: dict[str, Any]) -> None:
        table = self._table(self.settings.emergency_fund_table_name)
        table.upsert_entity(
            {
                "PartitionKey": "emergency",
                "RowKey": "main",
                **payload,
                "updated_at": datetime.now(UTC).isoformat(),
            },
            mode=UpdateMode.MERGE,
        )

    def save_weekly_advice(self, advice_text: str, week_start_iso: str) -> None:
        table = self._table("AdviceHistory")
        table.upsert_entity(
            {
                "PartitionKey": "advice",
                "RowKey": week_start_iso,
                "week_start_iso": week_start_iso,
                "advice_text": advice_text,
                "created_at": datetime.now(UTC).isoformat(),
            },
            mode=UpdateMode.MERGE,
        )

    def get_latest_advice(self) -> Optional[dict[str, Any]]:
        table = self._table("AdviceHistory")
        entities = [entity for entity in table.list_entities() if str(entity.get("PartitionKey", "")) == "advice"]
        if not entities:
            return None
        entities.sort(key=lambda item: str(item.get("week_start_iso", "")), reverse=True)
        return entities[0]

    def get_last_upload(self, source: str) -> Optional[dict[str, Any]]:
        table = self._table(self.settings.upload_state_table_name)
        entities = [
            entity
            for entity in table.list_entities()
            if str(entity.get("PartitionKey", "")) == "upload" and str(entity.get("source", "")) == source
        ]
        if not entities:
            return None
        entities.sort(key=lambda item: str(item.get("processed_at", "")), reverse=True)
        return entities[0]

    def record_sweep(self, amount_pence: int, result: str, pot_balance_pence: Optional[int]) -> None:
        table = self._table("Sweeps")
        now_iso = datetime.now(UTC).isoformat()
        table.upsert_entity(
            {
                "PartitionKey": "sweep",
                "RowKey": now_iso,
                "amount_pence": amount_pence,
                "result": result,
                "pot_balance_pence": pot_balance_pence,
                "created_at": now_iso,
            },
            mode=UpdateMode.MERGE,
        )

    def get_latest_sweep(self) -> Optional[dict[str, Any]]:
        table = self._table("Sweeps")
        entities = [entity for entity in table.list_entities() if str(entity.get("PartitionKey", "")) == "sweep"]
        if not entities:
            return None
        entities.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
        return entities[0]
