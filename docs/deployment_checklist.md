# Production Deployment Checklist (rg-personal-finance)

This checklist is for deploying the finance tracker to Azure only, using managed identity + Key Vault references and staying within a GBP-equivalent of the USD 150 monthly credit budget.

## 1. Resource group and baseline resources

- Create resource group: `rg-personal-finance`.
- Create or reuse:
  - Azure Function App (Python)
  - Storage account (Blob + Queue + Table)
  - Azure Key Vault
  - Azure OpenAI resource with GPT-4o deployment
  - Azure Static Web App (Free tier)

## 2. Budget guardrails

- Create an Azure Budget scoped to `rg-personal-finance` with threshold at USD 120 and USD 145.
- Add Action Group notifications for budget alerts.
- Enable Cost Analysis and verify service-level spend weekly.

## 3. Managed identity on Function App

- Enable system-assigned managed identity on Function App.
- Capture principal ID and object ID.

## 4. RBAC assignments

Assign the Function App managed identity:

- Storage account scope:
  - `Storage Blob Data Contributor`
  - `Storage Queue Data Contributor`
  - `Storage Table Data Contributor`
- Key Vault scope:
  - `Key Vault Secrets User`
- Azure OpenAI resource scope:
  - `Cognitive Services OpenAI User`

## 5. Key Vault secrets

Store these in Key Vault:

- `MONZOCLIENTID`
- `MONZOCLIENTSECRET`
- `MONZOACCOUNTID`
- `MONZOREFRESHTOKEN`
- `WEBHOOKSECRET`
- `AZURE_OPENAI_ENDPOINT`
- `AZURE_OPENAI_DEPLOYMENT`
- Optional: `AZURE_OPENAI_API_KEY` (only if not using managed identity for OpenAI)

## 6. Function App settings (Key Vault references)

Set app settings as Key Vault references where secret values are used:

- `MONZOCLIENTID=@Microsoft.KeyVault(SecretUri=https://<vault>.vault.azure.net/secrets/MONZOCLIENTID/<version>)`
- `MONZOCLIENTSECRET=@Microsoft.KeyVault(SecretUri=https://<vault>.vault.azure.net/secrets/MONZOCLIENTSECRET/<version>)`
- `MONZOACCOUNTID=@Microsoft.KeyVault(SecretUri=https://<vault>.vault.azure.net/secrets/MONZOACCOUNTID/<version>)`
- `MONZOREFRESHTOKEN=@Microsoft.KeyVault(SecretUri=https://<vault>.vault.azure.net/secrets/MONZOREFRESHTOKEN/<version>)`
- `WEBHOOKSECRET=@Microsoft.KeyVault(SecretUri=https://<vault>.vault.azure.net/secrets/WEBHOOKSECRET/<version>)`
- `AZURE_OPENAI_ENDPOINT=@Microsoft.KeyVault(SecretUri=https://<vault>.vault.azure.net/secrets/AZURE_OPENAI_ENDPOINT/<version>)`
- `AZURE_OPENAI_DEPLOYMENT=@Microsoft.KeyVault(SecretUri=https://<vault>.vault.azure.net/secrets/AZURE_OPENAI_DEPLOYMENT/<version>)`

Set non-secret app settings directly:

- `CSV_UPLOADS_CONTAINER=csv-uploads`
- `CATEGORISE_QUEUE_NAME=categorise-jobs`
- `ALERT_QUEUE_NAME=finance-alerts`
- `WEEKLY_SWEEP_AMOUNT_PENCE=10700`
- `WEEKLY_DISCRETIONARY_TARGET_PENCE=10700`
- `DEBT_TARGET_MONTHS=36`
- `DEBT_MONTHLY_PAYMENT_TARGET_PENCE=9300`
- `EMERGENCY_FUND_TARGET_PENCE=720000`
- `MONZO_INGEST_LOOKBACK_DAYS=7`
- `AZURE_OPENAI_API_VERSION=2024-10-21`

Storage auth configuration:

- Preferred: `AzureWebJobsStorage__tableServiceUri=https://<storage>.table.core.windows.net`
- Keep `AzureWebJobsStorage` only if still needed for runtime binding compatibility in your environment.

## 7. Data plane bootstrap

- Run finance table setup script once:

```bash
python scripts/setup_finance_tables.py
```

- Verify these tables exist:
  - `Transactions`, `Categories`, `BudgetTargets`, `DebtTracker`, `EmergencyFund`
  - `UploadState`, `SyncState`, `Sweeps`, `AdviceHistory`

## 8. GitHub Actions secrets

Ensure repository secrets exist:

- Azure login secrets for Function App deploy workflow
- `AZURE_STATIC_WEB_APPS_API_TOKEN` for static web deployment
- Optional fallback: `AZUREWEBJOBSSTORAGE` or `AZUREWEBJOBSSTORAGE_ACCOUNTNAME`

## 9. Validation after deployment

- Trigger `GET /api/health` and verify status 200.
- Upload a NatWest CSV and confirm:
  - Blob trigger ran
  - `Transactions` rows inserted
  - `UploadState` row created
- Upload same file again and verify duplicate suppression.
- Confirm `dashboard/summary` and `dashboard/transactions` return data.
- Verify weekly advice record appears in `AdviceHistory` after Monday advice run.
- Verify Monday sweep logs a row in `Sweeps`.

## 10. Operational hygiene

- Rotate Monzo and webhook secrets quarterly.
- Keep `local.settings.json` out of source control.
- Review Azure spend and Function execution metrics weekly.
