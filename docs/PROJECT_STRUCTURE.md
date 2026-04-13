# Personal Finance Tracker Project Structure

```text
.
├── app_fastapi.py
├── function_app.py
├── core/
│   ├── monzo_client.py
│   ├── settings.py
│   └── webhook_service.py
├── finance/
│   ├── __init__.py
│   ├── constants.py              # category seeds + initial targets
│   ├── csv_ingest.py             # CSV source detection and parsing
│   └── repository.py             # Azure Table Storage persistence
├── scripts/
│   └── setup_finance_tables.py   # creates tables and seed baseline rows
├── stores/
├── tests/
├── docs/
│   ├── PROJECT_STRUCTURE.md
│   └── azure_table_schema.md
├── staticwebapp/
│   ├── index.html
│   ├── app.js
│   ├── styles.css
│   └── staticwebapp.config.json
└── .github/workflows/
    └── staticwebapp.yml
```

## New Functions Added in Phase 1

- `ingest_monzo` (timer trigger, hourly)
- `ingest_csv` (blob trigger + queue output)
- `categorise` (queue trigger)

## New Functions Added in Phase 2

- `sweep_pots` (timer trigger, Mondays 08:00 UTC)
- `debt_tracker` (timer trigger, daily)
- `advice_engine` (timer trigger, Mondays 07:00 UTC)
- `alert` (queue trigger)
- `dashboard/summary` (HTTP GET)
- `dashboard/transactions` (HTTP GET)
