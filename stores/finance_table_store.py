from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any, Optional

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.data.tables import TableClient, TableServiceClient, UpdateMode
from azure.identity import DefaultAzureCredential
from azure.storage.queue import QueueClient

from core.finance_settings import FinanceSettings
from core.finance_schema import normalize_merchant, utc_now_iso


logger = logging.getLogger(__name__)


class FinanceTableStore:
    def __init__(self, settings: FinanceSettings):
        self.settings = settings
        self._table_clients: dict[str, TableClient] = {}
        self._queue_clients: dict[str, QueueClient] = {}

    def ensure_tables(self) -> None:
        for table_name in self.required_table_names():
            self._get_table_client(table_name)

    def required_table_names(self) -> list[str]:
        return [
            self.settings.transactions_table,
            self.settings.categories_table,
            self.settings.budget_targets_table,
            self.settings.debt_tracker_table,
            self.settings.emergency_fund_table,
            self.settings.ingestion_state_table,
        ]

    def _get_table_client(self, table_name: str) -> TableClient:
        if table_name in self._table_clients:
            return self._table_clients[table_name]

        table_endpoint = os.environ.get("AzureWebJobsStorage__tableServiceUri")
        if table_endpoint:
            credential = DefaultAzureCredential()
            client = TableClient(endpoint=table_endpoint, credential=credential, table_name=table_name)
        else:
            conn_str = os.environ.get("AzureWebJobsStorage")
            if not conn_str:
                raise RuntimeError("AzureWebJobsStorage is required for table operations.")
            service = TableServiceClient.from_connection_string(conn_str)
            client = service.get_table_client(table_name)

        try:
            client.create_table()
        except Exception:
            pass

        self._table_clients[table_name] = client
        return client

    def _get_queue_client(self, queue_name: str) -> QueueClient:
        if queue_name in self._queue_clients:
            return self._queue_clients[queue_name]

        conn_str = os.environ.get("AzureWebJobsStorage")
        if conn_str:
            queue_client = QueueClient.from_connection_string(conn_str, queue_name=queue_name)
        else:
            account_name = os.environ.get("AzureWebJobsStorage__accountName")
            if not account_name:
                raise RuntimeError("AzureWebJobsStorage or AzureWebJobsStorage__accountName is required for queue operations.")
            queue_url = f"https://{account_name}.queue.core.windows.net/{queue_name}"
            queue_client = QueueClient(queue_url, credential=DefaultAzureCredential())

        try:
            queue_client.create_queue()
        except Exception:
            pass

        self._queue_clients[queue_name] = queue_client
        return queue_client

    def enqueue_categorise(self, transaction_pk: str, transaction_rk: str) -> None:
        payload = json.dumps({"transaction_pk": transaction_pk, "transaction_rk": transaction_rk})
        queue_client = self._get_queue_client(self.settings.categorise_queue_name)
        queue_client.send_message(payload)

    def enqueue_alert(self, payload: dict[str, Any]) -> None:
        queue_client = self._get_queue_client(self.settings.alert_queue_name)
        queue_client.send_message(json.dumps(payload))

    def mark_csv_file_seen(self, blob_name: str, content: bytes) -> bool:
        digest = sha256(content).hexdigest()
        table = self._get_table_client(self.settings.ingestion_state_table)
        entity = {
            "PartitionKey": "csv_file",
            "RowKey": digest,
            "blob_name": blob_name,
            "seen_at": utc_now_iso(),
        }
        try:
            table.create_entity(entity)
            return False
        except ResourceExistsError:
            return True

    def record_source_upload(self, source: str, blob_name: str) -> None:
        table = self._get_table_client(self.settings.ingestion_state_table)
        table.upsert_entity(
            {
                "PartitionKey": "last_upload",
                "RowKey": source.lower(),
                "blob_name": blob_name,
                "uploaded_at": utc_now_iso(),
            },
            mode=UpdateMode.MERGE,
        )

    def get_monzo_sync_cursor(self) -> datetime:
        table = self._get_table_client(self.settings.ingestion_state_table)
        try:
            entity = table.get_entity(partition_key="monzo", row_key="last_sync")
            raw = str(entity.get("cursor_iso"))
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ResourceNotFoundError:
            return datetime.now(UTC) - timedelta(hours=self.settings.monzo_ingest_lookback_hours)

    def update_monzo_sync_cursor(self, cursor: datetime) -> None:
        table = self._get_table_client(self.settings.ingestion_state_table)
        table.upsert_entity(
            {
                "PartitionKey": "monzo",
                "RowKey": "last_sync",
                "cursor_iso": cursor.astimezone(UTC).isoformat(),
                "updated_at": utc_now_iso(),
            },
            mode=UpdateMode.MERGE,
        )

    def upsert_transaction(self, entity: dict[str, Any]) -> bool:
        table = self._get_table_client(self.settings.transactions_table)
        try:
            table.create_entity(entity)
            return True
        except ResourceExistsError:
            return False

    def get_transaction(self, partition_key: str, row_key: str) -> Optional[dict[str, Any]]:
        table = self._get_table_client(self.settings.transactions_table)
        try:
            return table.get_entity(partition_key=partition_key, row_key=row_key)
        except ResourceNotFoundError:
            return None

    def save_transaction(self, entity: dict[str, Any]) -> None:
        table = self._get_table_client(self.settings.transactions_table)
        table.upsert_entity(entity, mode=UpdateMode.MERGE)

    def find_category_for_merchant(self, merchant: str) -> Optional[str]:
        table = self._get_table_client(self.settings.categories_table)
        normalized = normalize_merchant(merchant)
        if not normalized:
            return None
        try:
            entity = table.get_entity(partition_key="merchant", row_key=normalized)
            return str(entity.get("category"))
        except ResourceNotFoundError:
            return None

    def upsert_category_mapping(self, merchant: str, category: str, seeded: bool = False) -> None:
        table = self._get_table_client(self.settings.categories_table)
        normalized = normalize_merchant(merchant)
        if not normalized:
            return
        table.upsert_entity(
            {
                "PartitionKey": "merchant",
                "RowKey": normalized,
                "merchant": merchant,
                "category": category,
                "seeded": seeded,
                "updated_at": utc_now_iso(),
            },
            mode=UpdateMode.MERGE,
        )

    def seed_budget_targets(self, entities: list[dict[str, Any]]) -> None:
        table = self._get_table_client(self.settings.budget_targets_table)
        for entity in entities:
            table.upsert_entity(entity, mode=UpdateMode.MERGE)

    def seed_singleton(self, table_name: str, entity: dict[str, Any]) -> None:
        table = self._get_table_client(table_name)
        table.upsert_entity(entity, mode=UpdateMode.MERGE)

    def get_singleton(self, table_name: str, partition_key: str, row_key: str) -> Optional[dict[str, Any]]:
        table = self._get_table_client(table_name)
        try:
            return table.get_entity(partition_key=partition_key, row_key=row_key)
        except ResourceNotFoundError:
            return None

    def upsert_singleton(self, table_name: str, entity: dict[str, Any]) -> None:
        table = self._get_table_client(table_name)
        table.upsert_entity(entity, mode=UpdateMode.MERGE)

    def list_latest_transactions(self, limit: int = 20, category: Optional[str] = None) -> list[dict[str, Any]]:
        table = self._get_table_client(self.settings.transactions_table)
        rows = list(table.list_entities())
        filtered = rows
        if category:
            normalized = category.strip().lower()
            filtered = [row for row in rows if str(row.get("category", "")).strip().lower() == normalized]

        filtered.sort(key=lambda r: str(r.get("occurred_at") or ""), reverse=True)
        return filtered[:limit]

    def list_transactions_between(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        table = self._get_table_client(self.settings.transactions_table)
        start_iso = start.astimezone(UTC).isoformat()
        end_iso = end.astimezone(UTC).isoformat()
        results: list[dict[str, Any]] = []
        for row in table.list_entities():
            occurred_at = str(row.get("occurred_at") or "")
            if not occurred_at:
                continue
            try:
                dt = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
            except ValueError:
                continue

            if start_iso <= dt.astimezone(UTC).isoformat() < end_iso:
                results.append(row)
        return results

    def get_weekly_discretionary_spend(self, week_start: datetime, week_end: datetime) -> int:
        transactions = self.list_transactions_between(week_start, week_end)
        spend = 0
        for tx in transactions:
            amount = int(tx.get("amount_pence") or 0)
            is_discretionary = bool(tx.get("is_discretionary", True))
            if is_discretionary and amount < 0:
                spend += abs(amount)
        return spend

    def get_weekly_spend_by_category(self, week_start: datetime, week_end: datetime) -> dict[str, int]:
        transactions = self.list_transactions_between(week_start, week_end)
        totals: dict[str, int] = {}
        for tx in transactions:
            amount = int(tx.get("amount_pence") or 0)
            if amount >= 0:
                continue
            category = str(tx.get("category") or "uncategorised").strip().lower()
            totals[category] = totals.get(category, 0) + abs(amount)
        return totals

    def try_mark_weekly_alert_sent(self, alert_type: str, week_key: str) -> bool:
        table = self._get_table_client(self.settings.ingestion_state_table)
        entity = {
            "PartitionKey": "weekly_alert",
            "RowKey": f"{alert_type}:{week_key}",
            "sent_at": utc_now_iso(),
        }
        try:
            table.create_entity(entity)
            return True
        except ResourceExistsError:
            return False

    def record_sweep(self, week_key: str, amount_pence: int, status: str, detail: str = "") -> None:
        table = self._get_table_client(self.settings.ingestion_state_table)
        table.upsert_entity(
            {
                "PartitionKey": "sweep",
                "RowKey": week_key,
                "amount_pence": amount_pence,
                "status": status,
                "detail": detail,
                "updated_at": utc_now_iso(),
            },
            mode=UpdateMode.MERGE,
        )

    def get_latest_advice(self) -> Optional[dict[str, Any]]:
        table = self._get_table_client(self.settings.ingestion_state_table)
        rows = [row for row in table.list_entities() if str(row.get("PartitionKey")) == "advice"]
        if not rows:
            return None
        rows.sort(key=lambda r: str(r.get("generated_at") or ""), reverse=True)
        return rows[0]

    def save_weekly_advice(self, week_key: str, advice: str, prompt: str) -> None:
        table = self._get_table_client(self.settings.ingestion_state_table)
        table.upsert_entity(
            {
                "PartitionKey": "advice",
                "RowKey": week_key,
                "advice": advice,
                "prompt": prompt,
                "generated_at": utc_now_iso(),
            },
            mode=UpdateMode.MERGE,
        )

    def get_source_upload_status(self) -> dict[str, Optional[str]]:
        table = self._get_table_client(self.settings.ingestion_state_table)
        statuses: dict[str, Optional[str]] = {"natwest": None, "paypal": None}
        for source in statuses:
            try:
                row = table.get_entity(partition_key="last_upload", row_key=source)
                statuses[source] = str(row.get("uploaded_at") or "") or None
            except ResourceNotFoundError:
                statuses[source] = None
        return statuses

    def get_weekly_budget_targets(self) -> dict[str, int]:
        table = self._get_table_client(self.settings.budget_targets_table)
        targets: dict[str, int] = {}
        for row in table.list_entities():
            if str(row.get("PartitionKey") or "") != "weekly":
                continue
            category = str(row.get("category") or row.get("RowKey") or "").strip().lower()
            target = int(row.get("target_pence") or 0)
            if category:
                targets[category] = target
        return targets

    @staticmethod
    def make_transaction_entity(
        source: str,
        external_id: str,
        occurred_at: str,
        merchant: str,
        amount_pence: int,
        currency: str,
        raw_description: str,
        category: str = "uncategorised",
    ) -> dict[str, Any]:
        normalized_merchant = normalize_merchant(merchant or raw_description or "unknown")
        row_key_seed = f"{source}|{external_id}|{occurred_at}|{normalized_merchant}|{amount_pence}"
        row_key = sha256(row_key_seed.encode("utf-8")).hexdigest()
        partition_key = source.lower()
        now = utc_now_iso()
        return {
            "PartitionKey": partition_key,
            "RowKey": row_key,
            "source": source.lower(),
            "external_id": external_id,
            "occurred_at": occurred_at,
            "merchant": merchant or "Unknown",
            "merchant_normalized": normalized_merchant,
            "amount_pence": amount_pence,
            "currency": currency,
            "category": category,
            "raw_description": raw_description,
            "created_at": now,
            "updated_at": now,
            "is_discretionary": True,
        }
