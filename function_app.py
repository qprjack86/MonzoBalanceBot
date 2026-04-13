import logging
import json
import math
import os
import uuid
from datetime import UTC, datetime, timedelta

import azure.functions as func
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient

from finance.constants import CATEGORY_UNCATEGORISED, KNOWN_MERCHANT_CATEGORY_MAP
from finance.csv_ingest import file_sha256, parse_csv_transactions
from finance.repository import FinanceRepository, TransactionRecord
from core.monzo_client import MonzoClient, build_session
from core.settings import load_settings
from core.webhook_service import WebhookService
from stores.factory import build_state_store


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

settings = load_settings()
store = build_state_store(settings)
monzo_client = MonzoClient(build_session(), settings.request_timeout)
service = WebhookService(settings, monzo_client, store)
finance_repo = FinanceRepository(settings)


def _seed_categories_if_missing() -> dict[str, str]:
    existing = finance_repo.get_categories_map()
    if existing:
        return existing

    for merchant_key, category in KNOWN_MERCHANT_CATEGORY_MAP.items():
        finance_repo.upsert_category(merchant_key, category)
    return finance_repo.get_categories_map()


def _pick_category(merchant: str, categories_map: dict[str, str]) -> str:
    merchant_norm = merchant.strip().lower()
    for merchant_key, category in categories_map.items():
        if merchant_key and merchant_key in merchant_norm:
            return category
    return CATEGORY_UNCATEGORISED


def _parse_iso_datetime(value: str) -> datetime:
    normalised = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalised).astimezone(UTC)


def _week_window_utc(reference: datetime | None = None) -> tuple[datetime, datetime]:
    now = (reference or datetime.now(UTC)).astimezone(UTC)
    start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=7)
    return start, end


def _currency(amount_pence: int) -> str:
    return f"GBP {amount_pence / 100:.2f}"


def _send_feed_message(title: str, body: str, color: str = "#E67E22") -> None:
    account_id = settings.monzo_account_id
    if not account_id:
        logger.warning("event=feed_skipped_missing_account")
        return

    access_token = service.get_monzo_access_token()
    monzo_client.post_feed(
        access_token=access_token,
        account_id=account_id,
        click_url="monzo://home",
        title=title,
        body=body,
        color=color,
    )


def _get_pot_balance_pence() -> int | None:
    account_id = settings.monzo_account_id
    pot_id = settings.monzo_spending_pot_id
    if not account_id or not pot_id:
        return None

    try:
        access_token = service.get_monzo_access_token()
        response = monzo_client.list_pots(access_token, account_id)
        response.raise_for_status()
        pots = response.json().get("pots", [])
        for pot in pots:
            if str(pot.get("id")) == pot_id:
                return int(pot.get("balance", 0) or 0)
    except Exception as exc:
        logger.warning("event=pot_balance_lookup_failed error=%s", exc)
    return None


def _weekly_target_pence() -> int:
    target = finance_repo.get_budget_target("weekly", "weekly_discretionary")
    if target:
        return int(target.get("amount_pence", settings.weekly_discretionary_target_pence) or settings.weekly_discretionary_target_pence)
    return settings.weekly_discretionary_target_pence


def _json_response(payload: dict) -> func.HttpResponse:
    return func.HttpResponse(json.dumps(payload, ensure_ascii=True), status_code=200, mimetype="application/json")


@app.route(route="monzo_webhook", methods=["POST"])
def monzo_webhook(req: func.HttpRequest) -> func.HttpResponse:
    correlation_id = req.headers.get("X-Correlation-ID") or str(uuid.uuid4())
    try:
        body = req.get_json()
    except ValueError:
        return func.HttpResponse("Invalid JSON", status_code=400, headers={"X-Correlation-ID": correlation_id})

    result = service.handle_webhook(
        headers=dict(req.headers),
        query=dict(req.params),
        body=body,
        correlation_id=correlation_id,
    )
    return func.HttpResponse(result.body, status_code=result.status_code, headers={"X-Correlation-ID": correlation_id})


@app.route(route="health", methods=["GET"])
def health(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse('{"status":"ok"}', status_code=200, mimetype="application/json")


@app.timer_trigger(schedule="0 0 * * * *", arg_name="timer", run_on_startup=False, use_monitor=True)
@app.queue_output(arg_name="alert_queue", queue_name="%ALERT_QUEUE_NAME%", connection="AzureWebJobsStorage")
def ingest_monzo(timer: func.TimerRequest, alert_queue: func.Out[str]) -> None:
    del timer
    account_id = settings.monzo_account_id
    if not account_id:
        logger.error("event=ingest_monzo_missing_account")
        return

    access_token = service.get_monzo_access_token()
    cursor_iso = finance_repo.get_sync_cursor("monzo_transactions_since")
    if not cursor_iso:
        cursor_iso = (datetime.now(UTC) - timedelta(days=settings.monzo_ingest_lookback_days)).isoformat()

    response = monzo_client.list_transactions(access_token, account_id, since=cursor_iso)
    response.raise_for_status()
    payload = response.json()
    transactions = payload.get("transactions", [])
    categories_map = _seed_categories_if_missing()

    inserted = 0
    latest_created = _parse_iso_datetime(cursor_iso)
    for tx in transactions:
        created = tx.get("created") or cursor_iso
        latest_created = max(latest_created, _parse_iso_datetime(created))

        merchant_name = (
            (tx.get("merchant") or {}).get("name")
            or tx.get("description")
            or "Unknown"
        )
        record = TransactionRecord(
            source="monzo",
            external_id=tx.get("id", ""),
            amount_pence=int(tx.get("amount", 0)),
            merchant=merchant_name,
            date_iso=created,
            category=_pick_category(merchant_name, categories_map),
            currency=str(tx.get("currency", "GBP")).upper(),
            metadata={
                "local_amount": tx.get("local_amount"),
                "settled": tx.get("settled"),
                "notes": tx.get("notes"),
            },
        )
        was_inserted, _ = finance_repo.upsert_transaction(record)
        if was_inserted:
            inserted += 1

    finance_repo.set_sync_cursor("monzo_transactions_since", latest_created.isoformat())

    week_start, week_end = _week_window_utc()
    week_start_iso = week_start.isoformat()
    overspend_key = f"overspend_alert:{week_start_iso}"
    weekly_spend = finance_repo.weekly_spend_pence(week_start_iso, week_end.isoformat())
    weekly_target = _weekly_target_pence()
    alert_sent = finance_repo.get_sync_cursor(overspend_key)
    if weekly_spend > weekly_target and not alert_sent:
        alert_queue.set(
            json.dumps(
                {
                    "type": "weekly_spend_overshoot",
                    "weekly_spend_pence": weekly_spend,
                    "target_pence": weekly_target,
                    "week_start_iso": week_start_iso,
                },
                ensure_ascii=True,
            )
        )
        finance_repo.set_sync_cursor(overspend_key, datetime.now(UTC).isoformat())

    logger.info("event=ingest_monzo_complete inserted=%s total=%s", inserted, len(transactions))


@app.blob_trigger(arg_name="blob", path="%CSV_UPLOADS_CONTAINER%/{name}", connection="AzureWebJobsStorage")
@app.queue_output(arg_name="categorise_queue", queue_name="%CATEGORISE_QUEUE_NAME%", connection="AzureWebJobsStorage")
def ingest_csv(blob: func.InputStream, categorise_queue: func.Out[str]) -> None:
    payload = blob.read()
    file_hash = file_sha256(payload)

    source, records = parse_csv_transactions(payload)
    if finance_repo.upload_seen(source, file_hash):
        logger.info("event=ingest_csv_duplicate_upload source=%s blob=%s", source, blob.name)
        return

    inserted_keys: list[str] = []
    for record in records:
        was_inserted, row_key = finance_repo.upsert_transaction(record)
        if was_inserted:
            inserted_keys.append(row_key)

    finance_repo.mark_upload_seen(source, blob.name, file_hash)

    if inserted_keys:
        categorise_queue.set(json.dumps({"source": source, "row_keys": inserted_keys}, ensure_ascii=True))
    logger.info(
        "event=ingest_csv_complete source=%s blob=%s parsed=%s inserted=%s",
        source,
        blob.name,
        len(records),
        len(inserted_keys),
    )


@app.queue_trigger(arg_name="msg", queue_name="%CATEGORISE_QUEUE_NAME%", connection="AzureWebJobsStorage")
def categorise(msg: func.QueueMessage) -> None:
    body = msg.get_body().decode("utf-8")
    payload = json.loads(body)

    source = payload.get("source")
    row_keys = payload.get("row_keys", [])
    if not source or not isinstance(row_keys, list):
        logger.warning("event=categorise_invalid_payload body=%s", body)
        return

    categories_map = _seed_categories_if_missing()
    updated = 0

    for row_key in row_keys:
        entity = finance_repo.get_transaction(source, row_key)
        merchant = str(entity.get("merchant", ""))
        category = _pick_category(merchant, categories_map)
        finance_repo.update_transaction_category(source, row_key, category)
        updated += 1

    logger.info("event=categorise_complete source=%s updated=%s", source, updated)


@app.timer_trigger(schedule="0 0 8 * * 1", arg_name="timer", run_on_startup=False, use_monitor=True)
def sweep_pots(timer: func.TimerRequest) -> None:
    del timer
    account_id = settings.monzo_account_id
    pot_id = settings.monzo_spending_pot_id
    if not account_id or not pot_id:
        logger.warning("event=sweep_pots_skipped_missing_config")
        return

    access_token = service.get_monzo_access_token()
    week_start, _ = _week_window_utc()
    dedupe_id = f"weekly-sweep-{week_start.date().isoformat()}"

    response = monzo_client.deposit_into_pot(
        access_token=access_token,
        pot_id=pot_id,
        source_account_id=account_id,
        amount_pence=settings.weekly_sweep_amount_pence,
        dedupe_id=dedupe_id,
    )
    response.raise_for_status()
    pot_balance = _get_pot_balance_pence()
    finance_repo.record_sweep(settings.weekly_sweep_amount_pence, "success", pot_balance)
    logger.info("event=sweep_pots_complete amount_pence=%s", settings.weekly_sweep_amount_pence)


@app.timer_trigger(schedule="0 5 0 * * *", arg_name="timer", run_on_startup=False, use_monitor=True)
def debt_tracker(timer: func.TimerRequest) -> None:
    del timer
    current = finance_repo.get_debt_tracker() or {}

    latest_natwest_balance = finance_repo.latest_source_balance_from_metadata("natwest")
    current_balance = int(
        latest_natwest_balance
        if latest_natwest_balance is not None
        else current.get("current_balance_pence", 331900)
    )
    monthly_target = int(current.get("monthly_payment_target_pence", settings.debt_monthly_payment_target_pence))
    target_months = int(current.get("target_months", settings.debt_target_months))
    months_remaining = math.ceil(current_balance / monthly_target) if monthly_target > 0 else target_months
    on_track = months_remaining <= target_months

    finance_repo.upsert_debt_tracker(
        {
            "name": "natwest_card",
            "current_balance_pence": current_balance,
            "monthly_payment_target_pence": monthly_target,
            "target_months": target_months,
            "months_remaining": months_remaining,
            "on_track": on_track,
        }
    )
    logger.info("event=debt_tracker_complete balance=%s months_remaining=%s", current_balance, months_remaining)


@app.timer_trigger(schedule="0 0 7 * * 1", arg_name="timer", run_on_startup=False, use_monitor=True)
def advice_engine(timer: func.TimerRequest) -> None:
    del timer
    if not settings.azure_openai_endpoint or not settings.azure_openai_deployment:
        logger.warning("event=advice_engine_skipped_missing_openai_config")
        return

    week_start, week_end = _week_window_utc()
    week_start_iso = week_start.isoformat()
    weekly_spend = finance_repo.weekly_spend_pence(week_start_iso, week_end.isoformat())
    weekly_target = _weekly_target_pence()

    breakdown = finance_repo.weekly_spend_breakdown(week_start_iso, week_end.isoformat())
    overspend_categories = [k for k, _ in sorted(breakdown.items(), key=lambda item: item[1], reverse=True)[:3]]

    debt = finance_repo.get_debt_tracker() or {}
    natwest_balance = int(debt.get("current_balance_pence", 331900))
    months_remaining = int(debt.get("months_remaining", settings.debt_target_months))
    on_track = bool(debt.get("on_track", False))

    emergency = finance_repo.get_emergency_fund() or {}
    emergency_current = int(emergency.get("current_balance_pence", 0))
    emergency_target = int(emergency.get("target_balance_pence", settings.emergency_fund_target_pence))

    pot_balance = _get_pot_balance_pence() or 0
    previous_advice = finance_repo.get_latest_advice()
    previous_advice_summary = (previous_advice or {}).get("advice_text", "No previous advice recorded.")

    prompt = (
        "Weekly financial summary:\n"
        f"- Weekly discretionary spend: £{weekly_spend / 100:.2f} vs £{weekly_target / 100:.2f} target\n"
        f"- Overspend categories: {', '.join(overspend_categories) if overspend_categories else 'none'}\n"
        f"- NatWest balance: £{natwest_balance / 100:.2f}, months remaining: {months_remaining} of 36, monthly payment on track: {on_track}\n"
        f"- Emergency fund: £{emergency_current / 100:.2f} of £{emergency_target / 100:.2f} target\n"
        f"- Monzo pot balance: £{pot_balance / 100:.2f}\n"
        f"- Last weeks advice was followed: {previous_advice_summary}\n"
        "Provide brief, specific, actionable advice for the coming week in plain English. Keep it under 150 words."
    )

    response_text = ""
    api_key = os.getenv("AZURE_OPENAI_API_KEY")
    if api_key:
        from openai import AzureOpenAI

        client = AzureOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=api_key,
            api_version=settings.azure_openai_api_version,
        )
        completion = client.chat.completions.create(
            model=settings.azure_openai_deployment,
            messages=[
                {"role": "system", "content": "You are a practical UK personal finance coach."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=220,
            temperature=0.3,
        )
        response_text = completion.choices[0].message.content or ""
    else:
        from azure.identity import DefaultAzureCredential, get_bearer_token_provider
        from openai import AzureOpenAI

        token_provider = get_bearer_token_provider(DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default")
        client = AzureOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,
            azure_ad_token_provider=token_provider,
            api_version=settings.azure_openai_api_version,
        )
        completion = client.chat.completions.create(
            model=settings.azure_openai_deployment,
            messages=[
                {"role": "system", "content": "You are a practical UK personal finance coach."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=220,
            temperature=0.3,
        )
        response_text = completion.choices[0].message.content or ""

    finance_repo.save_weekly_advice(response_text.strip(), week_start_iso)
    logger.info("event=advice_engine_complete week_start=%s", week_start_iso)


@app.queue_trigger(arg_name="msg", queue_name="%ALERT_QUEUE_NAME%", connection="AzureWebJobsStorage")
def alert(msg: func.QueueMessage) -> None:
    body = msg.get_body().decode("utf-8")
    payload = json.loads(body)
    event_type = str(payload.get("type", "")).strip().lower()

    if event_type == "weekly_spend_overshoot":
        weekly_spend = int(payload.get("weekly_spend_pence", 0))
        target = int(payload.get("target_pence", settings.weekly_discretionary_target_pence))
        over = max(0, weekly_spend - target)
        _send_feed_message(
            title=f"Weekly spend alert: {_currency(weekly_spend)}",
            body=f"You are {_currency(over)} over your weekly target. Tighten discretionary spending this week.",
            color="#D35400",
        )
        logger.info("event=alert_weekly_spend_sent amount=%s", weekly_spend)
        return

    if event_type == "low_balance":
        balance_pence = int(payload.get("balance_pence", 0))
        _send_feed_message(
            title=f"Balance warning: {_currency(balance_pence)}",
            body="Low balance detected. Review upcoming direct debits and discretionary spend.",
            color="#C0392B",
        )
        logger.info("event=alert_low_balance_sent balance=%s", balance_pence)
        return

    logger.warning("event=alert_unknown_type payload=%s", body)


@app.route(route="dashboard/summary", methods=["GET"])
def dashboard_summary(req: func.HttpRequest) -> func.HttpResponse:
    del req
    week_start, week_end = _week_window_utc()
    week_start_iso = week_start.isoformat()
    weekly_spend = finance_repo.weekly_spend_pence(week_start_iso, week_end.isoformat())
    weekly_target = _weekly_target_pence()

    debt = finance_repo.get_debt_tracker() or {}
    debt_balance = int(debt.get("current_balance_pence", 331900))
    debt_start = 331900
    debt_progress = 0 if debt_start <= 0 else max(0.0, min(1.0, (debt_start - debt_balance) / debt_start))

    emergency = finance_repo.get_emergency_fund() or {}
    emergency_current = int(emergency.get("current_balance_pence", 0))
    emergency_target = int(emergency.get("target_balance_pence", settings.emergency_fund_target_pence))
    emergency_progress = 0 if emergency_target <= 0 else max(0.0, min(1.0, emergency_current / emergency_target))

    latest_advice = finance_repo.get_latest_advice() or {}
    last_natwest = finance_repo.get_last_upload("natwest") or {}
    last_paypal = finance_repo.get_last_upload("paypal") or {}
    pot_balance = _get_pot_balance_pence()

    return _json_response(
        {
            "weekly_spend_pence": weekly_spend,
            "weekly_target_pence": weekly_target,
            "weekly_progress": min(1.0, weekly_spend / weekly_target) if weekly_target > 0 else 0,
            "debt": {
                "current_balance_pence": debt_balance,
                "months_remaining": int(debt.get("months_remaining", settings.debt_target_months)),
                "target_months": int(debt.get("target_months", settings.debt_target_months)),
                "on_track": bool(debt.get("on_track", False)),
                "progress": debt_progress,
            },
            "emergency_fund": {
                "current_balance_pence": emergency_current,
                "target_balance_pence": emergency_target,
                "progress": emergency_progress,
            },
            "pot_balance_pence": pot_balance,
            "advice": {
                "week_start_iso": latest_advice.get("week_start_iso"),
                "text": latest_advice.get("advice_text", "No advice yet."),
            },
            "last_uploads": {
                "natwest": last_natwest.get("processed_at"),
                "paypal": last_paypal.get("processed_at"),
            },
        }
    )


@app.route(route="dashboard/transactions", methods=["GET"])
def dashboard_transactions(req: func.HttpRequest) -> func.HttpResponse:
    category = req.params.get("category")
    entries = finance_repo.list_recent_transactions(limit=20, category=category)
    payload = []
    for entity in entries:
        payload.append(
            {
                "source": entity.get("PartitionKey"),
                "row_key": entity.get("RowKey"),
                "date_iso": entity.get("date_iso"),
                "merchant": entity.get("merchant"),
                "amount_pence": int(entity.get("amount_pence", 0) or 0),
                "category": entity.get("category", "uncategorised"),
                "currency": entity.get("currency", "GBP"),
            }
        )

    return _json_response({"transactions": payload})


@app.route(route="upload_csv", methods=["POST"])
def upload_csv(req: func.HttpRequest) -> func.HttpResponse:
    """Accept a CSV file upload from the dashboard and write it to blob storage.

    The existing ingest_csv blob trigger will fire automatically once the blob lands.
    """
    body = req.get_body()
    if not body:
        return func.HttpResponse(
            json.dumps({"error": "empty body"}, ensure_ascii=True),
            status_code=400,
            mimetype="application/json",
        )

    # Derive a safe filename: prefer Content-Disposition header, fall back to UUID
    content_disposition = req.headers.get("Content-Disposition", "")
    filename = None
    for part in content_disposition.split(";"):
        part = part.strip()
        if part.startswith("filename="):
            filename = part[len("filename="):].strip().strip('"')
            break
    if not filename:
        filename = f"upload-{uuid.uuid4()}.csv"

    table_uri = os.environ.get("AzureWebJobsStorage__tableServiceUri", "")
    if table_uri:
        # Managed-identity path: derive blob endpoint from table endpoint
        blob_endpoint = table_uri.replace(".table.", ".blob.")
        blob_service = BlobServiceClient(account_url=blob_endpoint, credential=DefaultAzureCredential())
    else:
        conn_str = os.environ.get("AzureWebJobsStorage", "")
        blob_service = BlobServiceClient.from_connection_string(conn_str)

    container_name = os.environ.get("CSV_UPLOADS_CONTAINER", "csv-uploads")
    blob_client = blob_service.get_blob_client(container=container_name, blob=filename)
    blob_client.upload_blob(body, overwrite=True)

    logger.info("event=upload_csv_received filename=%s bytes=%s", filename, len(body))
    return _json_response({"status": "queued", "filename": filename})
