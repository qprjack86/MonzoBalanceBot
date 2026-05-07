# Monzo Balance Bot 🐕

Monzo Balance Bot processes Monzo transaction webhooks and posts balance warnings to your Monzo feed when your spendable balance drops below configurable thresholds.

![Status](https://img.shields.io/badge/status-active-brightgreen)
![Python](https://img.shields.io/badge/python-3.13-blue)
![Azure Functions](https://img.shields.io/badge/azure-functions-FlexConsumption-0078D4)

## Why this exists

Monzo shows your balance, but it's easy to miss when spending quickly. This bot adds proactive alerts directly into your Monzo activity feed so warnings appear exactly where you're looking.

## Features

- **Real-time webhook processing** for `transaction.created` events.
- **Two alert levels** (both configurable via app settings):
  - **Warning (Amber):** balance below `LIMIT_WARNING` (default £250).
  - **Critical (Red):** balance below `LIMIT_CRITICAL` (default £100).
- **Periodic reminders** while in warning/critical state.
- **Webhook secret verification** via `X-Webhook-Secret` header.
- **Token auto-refresh** with optimistic concurrency (ETag-safe) in Azure Table Storage.
- **Dashboard API** with FUNCTION-level auth key for balance/spend data.

## Architecture

- **Runtime:** Azure Functions (Python 3.13, FlexConsumption)
- **Core logic:** `core/webhook_service.py`
- **Monzo API client:** `core/monzo_client.py`
- **State store:** Azure Table Storage (`azure_table` backend)
- **Secrets:** Azure Key Vault with managed identity auth
- **Dashboard endpoints** (`/api/dashboard/summary`, `/api/dashboard/transactions`) require function key

## Functions

| Function | Trigger | Purpose |
|---|---|---|
| `monzo_webhook` | HTTP (POST) | Receives `transaction.created` events from Monzo |
| `dashboard_summary` | HTTP (GET) | Returns weekly spend, debt, emergency fund snapshot (requires function key) |
| `dashboard_transactions` | HTTP (GET) | Returns recent transactions (requires function key) |
| `health` | HTTP (GET) | Health check |
| `oauth_callback` | HTTP (GET) | Monzo OAuth2 callback |
| `ingest_monzo` | Timer (hourly) | Pulls recent Monzo transactions |
| `advice_engine` | Timer (weekly Mon) | Generates financial advice |
| `bills_sweep` | Timer (19th monthly) | Sweeps excess balance to savings |
| `payday_topup` | Timer (22-25th monthly) | Payday pot top-up |
| `alert` | Queue | Processes financial alerts |

## Configuration

Set these as Function App settings (or in `local.settings.json`):

| Variable | Required | Description |
|---|---|---|
| `MONZOCLIENTID` | Yes | Monzo OAuth2 client ID |
| `MONZOCLIENTSECRET` | Yes | Monzo OAuth2 client secret |
| `MONZOACCOUNTID` | Yes | Monzo account ID to monitor |
| `MONZOREFRESHTOKEN` | Yes* | Initial fallback refresh token |
| `WEBHOOKSECRET` | Yes | Shared secret for webhook verification |
| `LIMIT_WARNING` | No | Warning threshold in pence (default 25000) |
| `LIMIT_CRITICAL` | No | Critical threshold in pence (default 10000) |
| `ALERT_FREQUENCY` | No | Repeat alert every N qualifying transactions (default 10) |
| `WEEKLY_DISCRETIONARY_TARGET_PENCE` | No | Weekly discretionary target (default 10700) |
| `DEBT_TARGET_MONTHS` | No | Debt clearance window in months (default 36) |
| `DEBT_MONTHLY_PAYMENT_TARGET_PENCE` | No | Monthly debt payment target (default 9300) |
| `EMERGENCY_FUND_TARGET_PENCE` | No | Emergency fund target (default 720000) |
| `MONZO_SPENDING_POT_ID` | No | Weekly spending pot ID |
| `MONZO_SAVINGS_POT_ID` | No | Savings pot ID |
| `MONZO_MONTHLY_POT_ID` | No | Monthly discretionary pot ID |
| `MONZO_SWEEP_FLOAT_PENCE` | No | Float to leave after bills sweep (default 5000) |
| `MONZO_INGEST_LOOKBACK_DAYS` | No | Initial lookback days (default 7) |
| `JARVIS_WEBHOOK_URL` | No | Webhook URL for Jarvis notifications |
| `JARVIS_WEBHOOK_SECRET` | No | Secret for Jarvis webhook |

*Required initially. After first successful token refresh, Azure Table Storage becomes the source of truth.

All secrets should be stored in Azure Key Vault and referenced via `@Microsoft.KeyVault(SecretUri=...)`.

## Local development

```bash
# Clone and set up
pip install -r requirements-dev.txt

# Configure in local.settings.json
# Run with Azure Functions Core Tools
func start

# Or run FastAPI variant
uvicorn app_fastapi:app --reload --host 0.0.0.0 --port 8000
```

## Security

- All secrets in Azure Key Vault with managed identity access.
- Storage account: Azure AD auth only, shared key access disabled, TLS 1.2, network firewall (default deny).
- Key Vault: RBAC auth, network firewall (default deny), soft delete + purge protection.
- Dashboard endpoints: FUNCTION-level auth key required.
- Webhook endpoints: secret verification via `X-Webhook-Secret` header.
- Deployments via secure zip deploy only (GitHub Actions disabled).
