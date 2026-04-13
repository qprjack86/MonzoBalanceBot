# Azure Table Storage Schema (Phase 1)

## Required Tables

- `Transactions`
- `Categories`
- `BudgetTargets`
- `DebtTracker`
- `EmergencyFund`

## Operational Tables Added for Ingestion

- `UploadState` (CSV dedupe and upload timestamps)
- `SyncState` (Monzo cursor)
- `Sweeps` (reserved for weekly pot sweep logging)
- `AdviceHistory` (reserved for weekly advice history)

## Entity Shapes

### Transactions
- PartitionKey: source (`monzo`, `natwest`, `paypal`)
- RowKey: SHA256 hash of `source|external_id|date|amount|merchant`
- Fields:
  - `external_id`
  - `amount_pence`
  - `merchant`
  - `date_iso`
  - `category`
  - `currency`
  - `metadata_json`
  - `created_at`
  - `categorised_at` (added later by categoriser)

### Categories
- PartitionKey: `merchant`
- RowKey: lower-cased merchant key
- Fields:
  - `merchant_key`
  - `category`
  - `seeded_at` / `updated_at`

### BudgetTargets
- PartitionKey: period (`weekly` / `monthly`)
- RowKey: target name
- Fields:
  - `name`
  - `period`
  - `amount_pence`

### DebtTracker
- PartitionKey: `natwest`
- RowKey: debt profile (`natwest_card`)
- Fields:
  - `current_balance_pence`
  - `target_balance_pence`
  - `monthly_payment_target_pence`
  - `target_months`

### EmergencyFund
- PartitionKey: `emergency`
- RowKey: profile (`main`)
- Fields:
  - `current_balance_pence`
  - `target_balance_pence`
  - `monthly_contribution_target_pence`

### UploadState
- PartitionKey: `upload`
- RowKey: `{source}:{sha256}`
- Fields:
  - `source`
  - `blob_name`
  - `file_hash`
  - `processed_at`

### SyncState
- PartitionKey: `cursor`
- RowKey: cursor name (`monzo_transactions_since`)
- Fields:
  - `cursor_iso`
  - `updated_at`

## Setup Script

Run once per environment after storage is available:

```bash
python scripts/setup_finance_tables.py
```

The script creates all tables idempotently and seeds:
- Known merchant category mappings
- Weekly discretionary budget target (£107)
- NatWest debt tracker baseline (£3,319 with £93/month target)
- Emergency fund baseline (£7,200 target)
