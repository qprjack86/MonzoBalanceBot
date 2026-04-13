from __future__ import annotations

import os
from dataclasses import dataclass


def _get_env(*keys: str, default=None):
    for key in keys:
        value = os.getenv(key)
        if value is not None and value != "":
            return value
    return default


def _env_int(*keys: str, default: int) -> int:
    value = _get_env(*keys)
    if value is None:
        return default
    return int(str(value).strip())


def _env_bool(*keys: str, default: bool = False) -> bool:
    value = _get_env(*keys)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class FinanceSettings:
    finance_features_enabled: bool
    transactions_table: str
    categories_table: str
    budget_targets_table: str
    debt_tracker_table: str
    emergency_fund_table: str
    ingestion_state_table: str
    csv_upload_container: str
    categorise_queue_name: str
    alert_queue_name: str
    ingest_monzo_schedule: str
    monzo_ingest_lookback_hours: int
    weekly_discretionary_target_pence: int
    monzo_spending_pot_id: str | None
    monzo_sweep_amount_pence: int
    sweep_pots_schedule: str
    debt_tracker_schedule: str
    advice_engine_schedule: str
    debt_target_months: int
    debt_monthly_payment_target_pence: int
    emergency_fund_target_pence: int
    azure_openai_endpoint: str | None
    azure_openai_api_key: str | None
    azure_openai_deployment: str | None
    azure_openai_api_version: str


def load_finance_settings() -> FinanceSettings:
    return FinanceSettings(
        finance_features_enabled=_env_bool("FINANCE_FEATURES_ENABLED", default=False),
        transactions_table=str(_get_env("TRANSACTIONS_TABLE", default="Transactions")),
        categories_table=str(_get_env("CATEGORIES_TABLE", default="Categories")),
        budget_targets_table=str(_get_env("BUDGET_TARGETS_TABLE", default="BudgetTargets")),
        debt_tracker_table=str(_get_env("DEBT_TRACKER_TABLE", default="DebtTracker")),
        emergency_fund_table=str(_get_env("EMERGENCY_FUND_TABLE", default="EmergencyFund")),
        ingestion_state_table=str(_get_env("INGESTION_STATE_TABLE", default="IngestionState")),
        csv_upload_container=str(_get_env("CSV_UPLOAD_CONTAINER", default="finance-uploads")),
        categorise_queue_name=str(_get_env("CATEGORISE_QUEUE_NAME", default="categorise")),
        alert_queue_name=str(_get_env("ALERT_QUEUE_NAME", default="alerts")),
        ingest_monzo_schedule=str(_get_env("INGEST_MONZO_SCHEDULE", default="0 0 * * * *")),
        monzo_ingest_lookback_hours=_env_int("MONZO_INGEST_LOOKBACK_HOURS", default=3),
        weekly_discretionary_target_pence=_env_int("WEEKLY_DISCRETIONARY_TARGET_PENCE", default=10700),
        monzo_spending_pot_id=_get_env("MONZO_SPENDING_POT_ID"),
        monzo_sweep_amount_pence=_env_int("MONZO_SWEEP_AMOUNT_PENCE", default=10700),
        sweep_pots_schedule=str(_get_env("SWEEP_POTS_SCHEDULE", default="0 0 8 * * Mon")),
        debt_tracker_schedule=str(_get_env("DEBT_TRACKER_SCHEDULE", default="0 0 6 * * *")),
        advice_engine_schedule=str(_get_env("ADVICE_ENGINE_SCHEDULE", default="0 0 7 * * Mon")),
        debt_target_months=_env_int("DEBT_TARGET_MONTHS", default=36),
        debt_monthly_payment_target_pence=_env_int("DEBT_MONTHLY_PAYMENT_TARGET_PENCE", default=9300),
        emergency_fund_target_pence=_env_int("EMERGENCY_FUND_TARGET_PENCE", default=720000),
        azure_openai_endpoint=_get_env("AZURE_OPENAI_ENDPOINT"),
        azure_openai_api_key=_get_env("AZURE_OPENAI_API_KEY"),
        azure_openai_deployment=_get_env("AZURE_OPENAI_DEPLOYMENT", default="gpt-4o"),
        azure_openai_api_version=str(_get_env("AZURE_OPENAI_API_VERSION", default="2024-10-21")),
    )
