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

### Personal Finance Tracker (Phase 1) settings

Add these app settings for the new ingestion + categorisation flow:

| Variable | Required | Description | Example |
|---|---|---|---|
| `CSV_UPLOADS_CONTAINER` | Yes | Blob container for NatWest/PayPal CSV uploads | `csv-uploads` |
| `CATEGORISE_QUEUE_NAME` | Yes | Queue used to fan out categorisation jobs | `categorise-jobs` |
| `TRANSACTIONS_TABLE_NAME` | No | Transactions table | `Transactions` |
| `CATEGORIES_TABLE_NAME` | No | Merchant category mapping table | `Categories` |
| `BUDGET_TARGETS_TABLE_NAME` | No | Budget target table | `BudgetTargets` |
| `DEBT_TRACKER_TABLE_NAME` | No | Debt tracker table | `DebtTracker` |
| `EMERGENCY_FUND_TABLE_NAME` | No | Emergency fund table | `EmergencyFund` |
| `UPLOAD_STATE_TABLE_NAME` | No | CSV dedupe/upload state table | `UploadState` |
| `SYNC_STATE_TABLE_NAME` | No | Ingestion cursor table | `SyncState` |
| `MONZO_INGEST_LOOKBACK_DAYS` | No | Initial lookback when no Monzo cursor exists | `7` |

### Personal Finance Tracker (Phase 2) settings

| Variable | Required | Description | Example |
|---|---|---|---|
| `ALERT_QUEUE_NAME` | No | Queue for finance alerts (overspend, low balance extensions) | `finance-alerts` |
| `MONZO_SPENDING_POT_ID` | Yes* | Monzo pot ID used for weekly sweep | `pot_000...` |
| `WEEKLY_SWEEP_AMOUNT_PENCE` | No | Weekly pot sweep amount in pence | `10700` |
| `WEEKLY_DISCRETIONARY_TARGET_PENCE` | No | Weekly discretionary target fallback in pence | `10700` |
| `DEBT_TARGET_MONTHS` | No | Debt clearance target window in months | `36` |
| `DEBT_MONTHLY_PAYMENT_TARGET_PENCE` | No | Monthly debt payment target in pence | `9300` |
| `EMERGENCY_FUND_TARGET_PENCE` | No | Emergency fund target in pence | `720000` |
| `AZURE_OPENAI_ENDPOINT` | Yes** | Azure OpenAI endpoint | `https://<name>.openai.azure.com` |
| `AZURE_OPENAI_DEPLOYMENT` | Yes** | Azure OpenAI deployment name (GPT-4o) | `gpt-4o` |
| `AZURE_OPENAI_API_VERSION` | No | Azure OpenAI API version | `2024-10-21` |
| `AZURE_OPENAI_API_KEY` | Optional** | Key auth fallback (prefer managed identity token auth) | `...` |

\* Required for `sweep_pots` and pot balance on dashboard.

\** Required for `advice_engine`.

See schema details in `docs/azure_table_schema.md` and structure in `docs/PROJECT_STRUCTURE.md`.

### Set up Azure Tables and seed baseline data

Run this once per environment:

```bash
python scripts/setup_finance_tables.py
```

This creates and seeds:
- `Transactions`, `Categories`, `BudgetTargets`, `DebtTracker`, `EmergencyFund`
- Operational tables used by ingestion (`UploadState`, `SyncState`, `Sweeps`, `AdviceHistory`)

\* Required initially. After first successful refresh+persist, storage becomes the source of truth.

\** You need either `AzureWebJobsStorage` _or_ `AzureWebJobsStorage__tableServiceUri` available in the environment where the app runs.

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

### CSV export and upload flow (NatWest + PayPal)

1. Export a transactions CSV from NatWest mobile app.
2. Export activity CSV from PayPal website.
3. Upload each file to the blob container defined by `CSV_UPLOADS_CONTAINER`.
4. Blob trigger `ingest_csv` auto-detects source format (`natwest` or `paypal`) and stores normalised rows in Table Storage.
5. Duplicate uploads are ignored using a SHA256 file hash persisted in `UploadState`.
6. New rows are pushed to `CATEGORISE_QUEUE_NAME` and categorised by merchant mapping in the `categorise` queue trigger.

## Finance function inventory

- `ingest_monzo` - Timer trigger hourly. Pulls Monzo transactions, stores to `Transactions`, and raises weekly overspend alert events.
- `ingest_csv` - Blob trigger for NatWest/PayPal CSV uploads. Auto-detects source and enforces file-hash dedupe.
- `categorise` - Queue trigger. Applies merchant mapping from `Categories` and writes category back to `Transactions`.
- `sweep_pots` - Timer trigger every Monday at 08:00 UTC. Sweeps GBP 107 (default) into configured Monzo pot.
- `debt_tracker` - Timer trigger daily. Updates debt metrics from latest NatWest balance metadata.
- `advice_engine` - Timer trigger every Monday at 07:00 UTC. Generates weekly advice with Azure OpenAI (GPT-4o deployment).
- `alert` - Queue trigger for financial alert notifications in Monzo feed (includes weekly overspend signal).

## Dashboard API endpoints

- `GET /api/dashboard/summary`
- `GET /api/dashboard/transactions`
- `GET /api/dashboard/transactions?category=<category>`

These are consumed by the static web dashboard in `staticwebapp/`.

## Static web dashboard (Azure Static Web Apps Free)

Frontend files are in `staticwebapp/` and use plain HTML/CSS/vanilla JS.

- Local static preview:

```bash
cd staticwebapp
python -m http.server 4173
```

- By default frontend calls `/api/...`.
- If your Function App is hosted separately, set `window.FINANCE_API_BASE` before loading `app.js` or adjust `staticwebapp/app.js`.
- GitHub Actions deployment workflow: `.github/workflows/staticwebapp.yml`.

## Secrets and identity

- Keep all secrets in Azure Key Vault and reference them in Function App settings.
- Prefer managed identity for Azure Table and Azure OpenAI auth.
- `local.settings.json` is for local development only and must never be committed.
- Use `docs/deployment_checklist.md` for the full production rollout checklist in `rg-personal-finance`.

## Production readiness checklist

- Follow `docs/deployment_checklist.md` end-to-end for Key Vault references, RBAC, OpenAI permissions, and post-deploy validation.
- Set an Azure budget on `rg-personal-finance` to stay under the monthly USD 150 credit envelope.

### Run with Azure Functions

```bash
func start
```

Webhook URL: `http://localhost:7071/api/monzo_webhook`

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
