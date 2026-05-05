import os
from dataclasses import dataclass


def _get_env(*keys: str, default=None):
    for key in keys:
        value = os.getenv(key)
        if value is not None and value != "":
            return value
    return default


def _env_bool(*keys: str, default: bool = False) -> bool:
    value = _get_env(*keys)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class Settings:
    monzo_client_id: str | None
    monzo_client_secret: str | None
    monzo_account_id: str | None
    monzo_refresh_token: str | None
    webhook_secret: str | None
    state_backend: str
    balance_limit_warning: int
    balance_limit_critical: int
    alert_frequency: int
    request_timeout: tuple[float, float]
    token_cache_ttl: int
    table_name: str
    partition_key: str
    row_key: str
    seen_ttl: int
    # Backwards-compatible default: Monzo usually authenticates webhooks via query-string secret.
    allow_query_secret: bool = True
    transactions_table_name: str = "Transactions"
    categories_table_name: str = "Categories"
    budget_targets_table_name: str = "BudgetTargets"
    debt_tracker_table_name: str = "DebtTracker"
    emergency_fund_table_name: str = "EmergencyFund"
    upload_state_table_name: str = "UploadState"
    sync_state_table_name: str = "SyncState"
    monzo_ingest_lookback_days: int = 7
    categorise_queue_name: str = "categorise-jobs"
    alert_queue_name: str = "finance-alerts"
    csv_uploads_container: str = "csv-uploads"
    monzo_spending_pot_id: str | None = None
    monzo_savings_pot_id: str | None = None
    monzo_monthly_pot_id: str | None = None
    monzo_sweep_float_pence: int = 5000
    weekly_sweep_amount_pence: int = 10700
    weekly_discretionary_target_pence: int = 10700
    debt_target_months: int = 36
    debt_monthly_payment_target_pence: int = 9300
    emergency_fund_target_pence: int = 720000
    azure_openai_endpoint: str | None = None
    azure_openai_api_version: str = "2024-10-21"
    azure_openai_deployment: str | None = None


def load_settings() -> Settings:
    return Settings(
        monzo_client_id=_get_env("MONZO_CLIENT_ID", "MONZOCLIENTID"),
        monzo_client_secret=_get_env("MONZO_CLIENT_SECRET", "MONZOCLIENTSECRET"),
        monzo_account_id=_get_env("MONZO_ACCOUNT_ID", "MONZOACCOUNTID"),
        monzo_refresh_token=_get_env("MONZO_REFRESH_TOKEN", "MONZOREFRESHTOKEN"),
        webhook_secret=_get_env("WEBHOOK_SECRET", "WEBHOOKSECRET"),
        state_backend=str(_get_env("STATE_BACKEND", default="azure_table")),
        allow_query_secret=_env_bool("ALLOW_QUERY_SECRET", default=True),
        balance_limit_warning=int(_get_env("BALANCE_LIMIT_WARNING", "LIMIT_WARNING", default=25000)),
        balance_limit_critical=int(_get_env("BALANCE_LIMIT_CRITICAL", "LIMIT_CRITICAL", default=10000)),
        alert_frequency=int(_get_env("ALERT_FREQUENCY", default=10)),
        request_timeout=(3.05, 10),
        token_cache_ttl=3000,
        table_name="monzotokens",
        partition_key="monzo",
        row_key="bot",
        seen_ttl=600,
        transactions_table_name=str(_get_env("TRANSACTIONS_TABLE_NAME", default="Transactions")),
        categories_table_name=str(_get_env("CATEGORIES_TABLE_NAME", default="Categories")),
        budget_targets_table_name=str(_get_env("BUDGET_TARGETS_TABLE_NAME", default="BudgetTargets")),
        debt_tracker_table_name=str(_get_env("DEBT_TRACKER_TABLE_NAME", default="DebtTracker")),
        emergency_fund_table_name=str(_get_env("EMERGENCY_FUND_TABLE_NAME", default="EmergencyFund")),
        upload_state_table_name=str(_get_env("UPLOAD_STATE_TABLE_NAME", default="UploadState")),
        sync_state_table_name=str(_get_env("SYNC_STATE_TABLE_NAME", default="SyncState")),
        monzo_ingest_lookback_days=int(_get_env("MONZO_INGEST_LOOKBACK_DAYS", default=7)),
        categorise_queue_name=str(_get_env("CATEGORISE_QUEUE_NAME", default="categorise-jobs")),
        alert_queue_name=str(_get_env("ALERT_QUEUE_NAME", default="finance-alerts")),
        csv_uploads_container=str(_get_env("CSV_UPLOADS_CONTAINER", default="csv-uploads")),
        monzo_spending_pot_id=_get_env("MONZO_SPENDING_POT_ID", "MONZOSPENDINGPOTID"),
        monzo_savings_pot_id=_get_env("MONZO_SAVINGS_POT_ID", "MONZOSAVINGSPOTID"),
        monzo_monthly_pot_id=_get_env("MONZO_MONTHLY_POT_ID", "MONZOMONTHLYPOTID"),
        monzo_sweep_float_pence=int(_get_env("MONZO_SWEEP_FLOAT_PENCE", default=5000)),
        weekly_sweep_amount_pence=int(_get_env("WEEKLY_SWEEP_AMOUNT_PENCE", default=10700)),
        weekly_discretionary_target_pence=int(_get_env("WEEKLY_DISCRETIONARY_TARGET_PENCE", default=10700)),
        debt_target_months=int(_get_env("DEBT_TARGET_MONTHS", default=36)),
        debt_monthly_payment_target_pence=int(_get_env("DEBT_MONTHLY_PAYMENT_TARGET_PENCE", default=9300)),
        emergency_fund_target_pence=int(_get_env("EMERGENCY_FUND_TARGET_PENCE", default=720000)),
    )
