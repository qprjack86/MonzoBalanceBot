# Monzo Balance Bot 🐕

Monzo Balance Bot listens to Monzo transaction webhooks and posts balance warnings back to your Monzo feed when your spendable balance drops below configurable thresholds. It now supports both Azure Functions and a platform-neutral FastAPI runtime.

![Status](https://img.shields.io/badge/status-active-brightgreen)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![Azure Functions](https://img.shields.io/badge/azure-functions-0078D4)

## Why this exists

Monzo already shows your balance, but it is easy to miss when spending quickly. This bot adds proactive alerts directly into your Monzo activity feed and (optionally) transaction notes so warnings appear exactly where you are looking.
It is a feature missing from the Monzo App (and one of the most requested features - https://community.monzo.com/t/notification-on-reaching-a-set-balance/153931)

## Features

- **Real-time webhook processing** for `transaction.created` events.
- **Two alert levels** (both configurable):
  - **Warning (Amber):** balance below `LIMIT_WARNING` (default `25000` pence / £250).
  - **Critical (Red):** balance below `LIMIT_CRITICAL` (default `10000` pence / £100).
- **Warning reminders:** while in warning state, send periodic reminders every `ALERT_FREQUENCY` qualifying transactions (default every 10).
- **Critical reminders:** while in critical state, send an alert on every qualifying transaction.
- **Idempotency protection** to avoid duplicate processing when webhook events are retried.
- **Token auto-refresh** with optimistic concurrency (ETag-safe updates in Azure Table Storage).
- **Webhook secret verification** via either:
  - Header: `X-Webhook-Secret` (recommended)
  - Query parameter: `secret_key` (legacy compatibility)
- **Health check endpoints** available at `/health` for both Azure Functions and FastAPI runtimes.
- **Correlation IDs** supported through `X-Correlation-ID` (request/response) for easier tracing.

### Financial Tracker

This repository now includes a personal finance tracker extension:

- `ingest_monzo` timer trigger (`hourly`) stores Monzo transactions in Azure Table Storage.
- `ingest_csv` blob trigger ingests NatWest and PayPal CSV exports from Blob Storage.
- CSV ingestion supports:
  - source auto-detection (NatWest vs PayPal)
  - file-level dedupe by SHA-256 hash
  - transaction-level dedupe via deterministic row keys
- `categorise` queue trigger applies merchant-to-category mappings.
- `sweep_pots` timer trigger runs Monday mornings and performs the weekly Monzo pot sweep.
- `debt_tracker` timer trigger recalculates debt months remaining and on-track status daily.
- `advice_engine` timer trigger generates weekly summary advice using Azure OpenAI (GPT-4o deployment).
- `alert` queue trigger sends overspend feed alerts when weekly discretionary spend crosses target.
- Dashboard HTTP endpoints:
  - `/api/finance_summary`
  - `/api/finance_transactions`
  - `/api/finance_advice`
  - `/api/finance_upload_status`
- Setup script to provision/seed finance tables: `scripts/setup_finance_tables.py`

Seeded categories include: eating out, groceries, subscriptions, gambling, coffee, shopping, transport, transfers, personal care (unknown merchants default to `uncategorised`).

## Architecture

- **Core logic:** Transport-agnostic Python service (`core/webhook_service.py`)
- **Runtime adapters:**
  - Azure Functions (`function_app.py`)
  - FastAPI (`app_fastapi.py`)
- **State & token store:** pluggable backend (`azure_table` default, `memory` local option)
- **External API:** Monzo API (`/oauth2/token`, `/balance`, `/feed`, `/transactions/{id}`)
- **Auth model:** Monzo OAuth2 refresh token + managed identity or storage connection string

## Prerequisites

- Python 3.10+
- Azure Table-capable storage configuration (`AzureWebJobsStorage` or `AzureWebJobsStorage__tableServiceUri`)
- Monzo Developer app credentials (Client ID / Client Secret)
- For Azure runtime: [Azure Functions Core Tools](https://learn.microsoft.com/azure/azure-functions/functions-run-local)
- For FastAPI runtime: `fastapi` + `uvicorn` (included in `requirements-fastapi.txt`)

## Configuration

Set these as Function App settings (or in `local.settings.json` when running locally):

| Variable | Required | Description | Example |
|---|---|---|---|
| `MONZOCLIENTID` | Yes | Monzo OAuth2 client ID | `oauth2client_000...` |
| `MONZOCLIENTSECRET` | Yes | Monzo OAuth2 client secret | `mnzpub...` |
| `MONZOACCOUNTID` | Yes | The Monzo account ID to monitor | `acc_000...` |
| `MONZOREFRESHTOKEN` | Yes* | Initial fallback refresh token used when storage is empty | `eyJ...` |
| `WEBHOOKSECRET` | Yes | Shared secret used to verify incoming webhook calls | `a1b2c3...` |
| `STATE_BACKEND` | No | State backend: `azure_table` (default) or `memory` | `memory` |
| `ALLOW_QUERY_SECRET` | No | Allow Monzo query-string secret auth (`true` default for Monzo compatibility) | `true` |
| `AzureWebJobsStorage` | Yes** | Storage connection string (local/dev or classic config) | `DefaultEndpointsProtocol=...` |
| `AzureWebJobsStorage__tableServiceUri` | Optional** | Table endpoint for managed identity auth in Azure | `https://<acct>.table.core.windows.net` |
| `LIMIT_WARNING` | No | Warning threshold in pence | `25000` |
| `LIMIT_CRITICAL` | No | Critical threshold in pence | `10000` |
| `ALERT_FREQUENCY` | No | Send a repeat alert every N qualifying transactions | `10` |

### Additional Finance Settings

| Variable | Required | Description | Default |
|---|---|---|---|
| `TRANSACTIONS_TABLE` | No | Transaction storage table | `Transactions` |
| `CATEGORIES_TABLE` | No | Merchant-category mappings | `Categories` |
| `BUDGET_TARGETS_TABLE` | No | Budget targets table | `BudgetTargets` |
| `DEBT_TRACKER_TABLE` | No | Debt tracking table | `DebtTracker` |
| `EMERGENCY_FUND_TABLE` | No | Emergency fund table | `EmergencyFund` |
| `INGESTION_STATE_TABLE` | No | Ingestion cursors and dedupe markers | `IngestionState` |
| `CSV_UPLOAD_CONTAINER` | No | Blob container for uploaded CSV files | `finance-uploads` |
| `CATEGORISE_QUEUE_NAME` | No | Queue used by categorisation worker | `categorise` |
| `INGEST_MONZO_SCHEDULE` | No | NCRONTAB schedule for Monzo ingestion | `0 0 * * * *` |
| `SWEEP_POTS_SCHEDULE` | No | NCRONTAB schedule for weekly Monzo pot sweep | `0 0 8 * * Mon` |
| `DEBT_TRACKER_SCHEDULE` | No | NCRONTAB schedule for debt tracking | `0 0 6 * * *` |
| `ADVICE_ENGINE_SCHEDULE` | No | NCRONTAB schedule for weekly advice | `0 0 7 * * Mon` |
| `ALERT_QUEUE_NAME` | No | Queue used by extended alert processing | `alerts` |
| `MONZO_SWEEP_AMOUNT_PENCE` | No | Weekly sweep amount in pence | `10700` |
| `MONZO_SPENDING_POT_ID` | Yes*** | Pot ID for sweep and pot-balance reporting | `pot_000...` |
| `DEBT_TARGET_MONTHS` | No | NatWest debt target duration in months | `36` |
| `DEBT_MONTHLY_PAYMENT_TARGET_PENCE` | No | Monthly payment target in pence | `9300` |
| `EMERGENCY_FUND_TARGET_PENCE` | No | Emergency fund goal in pence | `720000` |
| `AZURE_OPENAI_ENDPOINT` | Yes*** | Azure OpenAI resource endpoint | `https://<name>.openai.azure.com` |
| `AZURE_OPENAI_API_KEY` | Yes*** | Azure OpenAI API key (or Key Vault reference) | `<secret>` |
| `AZURE_OPENAI_DEPLOYMENT` | No | Azure OpenAI deployment name (GPT-4o) | `gpt-4o` |
| `AZURE_OPENAI_API_VERSION` | No | Azure OpenAI API version | `2024-10-21` |
| `WEEKLY_DISCRETIONARY_TARGET_PENCE` | No | Weekly spend target in pence | `10700` |

> Keep `local.settings.json` local only and never commit it.

\* Required initially. After first successful refresh+persist, storage becomes the source of truth.

\** You need either `AzureWebJobsStorage` _or_ `AzureWebJobsStorage__tableServiceUri` available in the environment where the app runs.

\*** Required for full financial tracker automation (sweep + advice engine).

## Local development

1. Create and activate a virtual environment.
2. Install dependencies for your chosen runtime:

```bash
# Core only
pip install -r requirements-core.txt

# Azure Functions runtime
pip install -r requirements-azure.txt

# FastAPI runtime
pip install -r requirements-fastapi.txt

# Everything for local development
pip install -r requirements-dev.txt
```

3. Configure environment variables listed above. For Azure local runtime, put them in `local.settings.json`. For local FastAPI development without Azure Storage, set `STATE_BACKEND=memory`.

### Run with Azure Functions

```bash
func start
```

Webhook URL: `http://localhost:7071/api/monzo_webhook`

### Provision finance tables

After setting storage configuration, run:

```bash
python scripts/setup_finance_tables.py
```

This creates and seeds:

- `Transactions`
- `Categories`
- `BudgetTargets`
- `DebtTracker`
- `EmergencyFund`
- `IngestionState`

### CSV export and upload flow (NatWest + PayPal)

1. Export your latest transaction CSV from the NatWest app.
2. Export your latest transaction/activity CSV from PayPal.
3. Upload each CSV file to Blob container `finance-uploads` (or your configured container).
4. Blob trigger `ingest_csv` runs automatically and stores normalized rows in `Transactions`.
5. New rows are queued for `categorise` to apply merchant mappings.

If the same CSV is uploaded again, file hash dedupe prevents double counting.

### Dashboard (Azure Static Web Apps)

Static frontend is under `staticwebapp/` (plain HTML, CSS, vanilla JS, mobile-first).

- Dashboard sections:
  - weekly spend progress
  - debt paydown progress
  - emergency fund progress
  - weekly advice
  - recent categorized transactions
  - NatWest/PayPal upload freshness

GitHub Actions workflow for frontend deploy: `.github/workflows/staticwebapp.yml`.

### Run with FastAPI

```bash
uvicorn app_fastapi:app --reload --host 0.0.0.0 --port 8000
```

Webhook URL: `http://localhost:8000/monzo_webhook`

### Test webhook endpoint locally

```bash
curl -X POST "http://localhost:8000/monzo_webhook?secret_key=TEST_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"type":"transaction.created","data":{"id":"tx_123","account_id":"acc_000"}}'
```

> Tip: use `X-Webhook-Secret` header validation. Monzo typically authenticates via query-string secret. Keep `ALLOW_QUERY_SECRET=true` unless you have an upstream gateway that injects/validates headers.

## Deployment

### Azure Functions

```bash
func azure functionapp publish <YOUR_APP_NAME>
```

> Monzo webhook compatibility: ensure `ALLOW_QUERY_SECRET=true` unless you have an upstream gateway injecting/validating `X-Webhook-Secret`.

Webhook URL:
- `https://<YOUR_APP_NAME>.azurewebsites.net/api/monzo_webhook`

### Azure Static Web App

Set GitHub secret `AZURE_STATIC_WEB_APPS_API_TOKEN` and push changes under `staticwebapp/`.

The workflow `.github/workflows/staticwebapp.yml` deploys the dashboard.

### Container / generic platforms

Build and run locally:

```bash
docker build -t monzo-balance-bot .
docker run --rm -p 8000:8000 --env-file .env monzo-balance-bot
```

Webhook URL:
- `https://<YOUR_HOST>/monzo_webhook`

This container target can be deployed to Cloud Run, ECS/Fargate, Azure Container Apps, Fly.io, or Kubernetes.


## CI

GitHub Actions runs a 3-job matrix:
- **core-tests** (core modules + webhook service unit tests)
- **azure-adapter-tests** (Azure adapter compile + import checks)
- **fastapi-adapter-tests** (FastAPI adapter compile + route checks)

Workflow file: `.github/workflows/ci.yml`.

Azure deploy workflow note: `.github/workflows/main_monzowatchdog-js.yml` now enforces that one of `AzureWebJobsStorage` or `AzureWebJobsStorage__accountName` is present before deploy. If the app setting is missing, provide repository secret `AZUREWEBJOBSSTORAGE` (connection string) or `AZUREWEBJOBSSTORAGE_ACCOUNTNAME` (RBAC account name).

Recommended Azure layout for this project:

- Resource group: `rg-personal-finance`
- Function App + Storage Account + Queue/Table/Blob in same subscription
- Key Vault with Managed Identity references for secrets
- Azure Static Web App (Free tier) for dashboard

## Getting a refresh token (one-time helper)

Use `get_token.py` locally to complete OAuth and print a `MONZOREFRESHTOKEN` value:

```bash
MONZO_CLIENT_ID=... MONZO_CLIENT_SECRET=... python get_token.py
```

This opens a browser, receives the callback at `http://localhost:8080/callback`, and logs a token you can store in Key Vault / app settings.

## Operations notes

- **Token rotation:** the function refreshes access tokens automatically and persists them to Table Storage.
- **Concurrency safety:** ETag checks handle simultaneous refresh attempts.
- **Duplicate webhooks:** dedupe is store-backed (`azure_table` or `memory`) to avoid repeated processing in close succession.
- **Alert behavior:** alerts trigger on threshold escalation and then periodically while still below threshold.

## Security recommendations

- Store `MONZOCLIENTSECRET` and `MONZOREFRESHTOKEN` in Azure Key Vault (or equivalent secret store).
- Store `AZURE_OPENAI_API_KEY` in Azure Key Vault and reference via Function App settings.
- Prefer managed identity with `AzureWebJobsStorage__tableServiceUri` in production.
- Use a long random `WEBHOOKSECRET` and rotate it periodically.
- If you can terminate/validate webhook auth at an upstream gateway, set `ALLOW_QUERY_SECRET=false` and use header-only auth in the app layer.
- Restrict Function App access and monitoring to trusted operators.

## Troubleshooting

- **401 Unauthorized from webhook:** verify `WEBHOOKSECRET` exactly matches what is sent.
- **No alerts:** check `MONZOACCOUNTID`, threshold settings, and function logs.
- **Refresh failures:** verify client ID/secret and seed refresh token are valid.
- **Storage errors:** confirm table endpoint or connection string configuration and permissions.

## License

Distributed under the MIT License.
