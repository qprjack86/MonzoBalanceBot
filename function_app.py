import logging
import json
import uuid
from datetime import UTC, datetime

import azure.functions as func

from core.advice_engine import AdviceInput, build_prompt, generate_weekly_advice
from core.csv_ingestion import parse_csv
from core.finance_metrics import progress_percent, project_debt, week_window
from core.finance_schema import DEFAULT_DEBT_TRACKER, DEFAULT_EMERGENCY_FUND, utc_now_iso
from core.finance_settings import load_finance_settings
from core.monzo_client import MonzoClient, build_session
from core.settings import load_settings
from core.webhook_service import WebhookService
from stores.finance_table_store import FinanceTableStore
from stores.factory import build_state_store


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

settings = load_settings()
finance_settings = load_finance_settings()
store = build_state_store(settings)
monzo_client = MonzoClient(build_session(), settings.request_timeout)
service = WebhookService(settings, monzo_client, store)
finance_store = FinanceTableStore(finance_settings)


def _json_response(payload: dict, status_code: int = 200) -> func.HttpResponse:
    return func.HttpResponse(json.dumps(payload), status_code=status_code, mimetype="application/json")


def _finance_enabled() -> bool:
    return finance_settings.finance_features_enabled


def _finance_disabled_response() -> func.HttpResponse:
    return _json_response({"error": "Finance features are disabled for this app."}, status_code=404)


def _default_debt_entity() -> dict:
    entity = dict(DEFAULT_DEBT_TRACKER)
    entity["target_months"] = finance_settings.debt_target_months
    entity["monthly_payment_target_pence"] = finance_settings.debt_monthly_payment_target_pence
    entity["updated_at"] = utc_now_iso()
    return entity


def _default_emergency_entity() -> dict:
    entity = dict(DEFAULT_EMERGENCY_FUND)
    entity["target_pence"] = finance_settings.emergency_fund_target_pence
    entity["updated_at"] = utc_now_iso()
    return entity


def _try_get_pot_balance_pence() -> int:
    if not settings.monzo_account_id or not finance_settings.monzo_spending_pot_id:
        return 0
    try:
        access_token = service.get_monzo_access_token()
        response = monzo_client.list_pots(access_token, settings.monzo_account_id)
        response.raise_for_status()
        pots = response.json().get("pots", [])
        for pot in pots:
            if str(pot.get("id")) == finance_settings.monzo_spending_pot_id:
                return int(pot.get("balance") or 0)
    except Exception:
        logger.exception("event=pot_balance_lookup_failed")
    return 0


def _queue_overspend_alert_if_needed() -> None:
    week_start, week_end, week_key = week_window()
    weekly_spend = finance_store.get_weekly_discretionary_spend(week_start, week_end)
    target = finance_settings.weekly_discretionary_target_pence
    if weekly_spend <= target:
        return

    if not finance_store.try_mark_weekly_alert_sent("discretionary_overspend", week_key):
        return

    finance_store.enqueue_alert(
        {
            "type": "discretionary_overspend",
            "week_key": week_key,
            "weekly_spend_pence": weekly_spend,
            "target_pence": target,
        }
    )


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


@app.timer_trigger(schedule=finance_settings.ingest_monzo_schedule, arg_name="timer", run_on_startup=False, use_monitor=True)
def ingest_monzo(timer: func.TimerRequest) -> None:
    if not _finance_enabled():
        logger.info("event=ingest_monzo_skipped reason=finance_disabled")
        return
    if not settings.monzo_account_id:
        logger.error("event=ingest_monzo_missing_account_id")
        return

    try:
        finance_store.ensure_tables()
        access_token = service.get_monzo_access_token()
        since_dt = finance_store.get_monzo_sync_cursor()
        since_iso = since_dt.astimezone(UTC).isoformat()

        response = monzo_client.list_transactions(access_token, settings.monzo_account_id, since_iso=since_iso, limit=100)
        response.raise_for_status()

        payload = response.json()
        transactions = payload.get("transactions", [])
        inserted = 0
        max_seen_dt = since_dt

        for tx in transactions:
            tx_id = str(tx.get("id") or "")
            if not tx_id:
                continue

            occurred_at = str(tx.get("created") or utc_now_iso())
            merchant_obj = tx.get("merchant") if isinstance(tx.get("merchant"), dict) else {}
            merchant = str(merchant_obj.get("name") or tx.get("description") or "Unknown")
            amount = int(tx.get("amount") or 0)
            currency = str(tx.get("currency") or "GBP")
            description = str(tx.get("description") or merchant)

            entity = finance_store.make_transaction_entity(
                source="monzo",
                external_id=tx_id,
                occurred_at=occurred_at,
                merchant=merchant,
                amount_pence=amount,
                currency=currency,
                raw_description=description,
            )
            entity["account_id"] = str(tx.get("account_id") or "")

            is_new = finance_store.upsert_transaction(entity)
            if is_new:
                inserted += 1
                finance_store.enqueue_categorise(entity["PartitionKey"], entity["RowKey"])

            try:
                tx_dt = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
                if tx_dt.tzinfo is None:
                    tx_dt = tx_dt.replace(tzinfo=UTC)
                if tx_dt > max_seen_dt:
                    max_seen_dt = tx_dt
            except ValueError:
                pass

        finance_store.update_monzo_sync_cursor(max_seen_dt)
        _queue_overspend_alert_if_needed()
        logger.info("event=ingest_monzo_complete inserted=%s scanned=%s", inserted, len(transactions))
    except Exception:
        logger.exception("event=ingest_monzo_failed")


@app.blob_trigger(arg_name="blob", path=f"{finance_settings.csv_upload_container}/{{name}}", connection="AzureWebJobsStorage")
def ingest_csv(blob: func.InputStream) -> None:
    if not _finance_enabled():
        logger.info("event=ingest_csv_skipped reason=finance_disabled")
        return
    blob_name = blob.name.split("/")[-1]
    try:
        finance_store.ensure_tables()
        content = blob.read()

        if finance_store.mark_csv_file_seen(blob_name, content):
            logger.info("event=ingest_csv_duplicate_file blob=%s", blob_name)
            return

        source, parsed_transactions = parse_csv(content)
        if source == "unknown":
            logger.warning("event=ingest_csv_unknown_format blob=%s", blob_name)
            return

        inserted = 0
        latest_natwest_balance: int | None = None
        latest_natwest_balance_ts: str = ""
        for tx in parsed_transactions:
            entity = finance_store.make_transaction_entity(
                source=tx.source,
                external_id=tx.external_id,
                occurred_at=tx.occurred_at,
                merchant=tx.merchant,
                amount_pence=tx.amount_pence,
                currency=tx.currency,
                raw_description=tx.raw_description,
            )
            entity["source_file"] = blob_name
            entity["uploaded_at"] = utc_now_iso()
            if tx.balance_pence is not None:
                entity["natwest_balance_pence"] = tx.balance_pence
                if tx.occurred_at >= latest_natwest_balance_ts:
                    latest_natwest_balance_ts = tx.occurred_at
                    latest_natwest_balance = tx.balance_pence

            is_new = finance_store.upsert_transaction(entity)
            if not is_new:
                continue

            inserted += 1
            finance_store.enqueue_categorise(entity["PartitionKey"], entity["RowKey"])

        finance_store.record_source_upload(source=source, blob_name=blob_name)
        if source == "natwest" and latest_natwest_balance is not None:
            debt = finance_store.get_singleton(finance_settings.debt_tracker_table, "natwest", "primary") or _default_debt_entity()
            debt["current_balance_pence"] = latest_natwest_balance
            debt["updated_at"] = utc_now_iso()
            finance_store.upsert_singleton(finance_settings.debt_tracker_table, debt)

        _queue_overspend_alert_if_needed()
        logger.info(
            "event=ingest_csv_complete source=%s inserted=%s scanned=%s blob=%s",
            source,
            inserted,
            len(parsed_transactions),
            blob_name,
        )
    except Exception:
        logger.exception("event=ingest_csv_failed blob=%s", blob_name)


@app.queue_trigger(arg_name="msg", queue_name=finance_settings.categorise_queue_name, connection="AzureWebJobsStorage")
def categorise(msg: func.QueueMessage) -> None:
    if not _finance_enabled():
        logger.info("event=categorise_skipped reason=finance_disabled")
        return
    try:
        finance_store.ensure_tables()
        payload = json.loads(msg.get_body().decode("utf-8"))
        transaction_pk = str(payload.get("transaction_pk") or "")
        transaction_rk = str(payload.get("transaction_rk") or "")
        if not transaction_pk or not transaction_rk:
            logger.warning("event=categorise_invalid_message")
            return

        transaction = finance_store.get_transaction(transaction_pk, transaction_rk)
        if not transaction:
            logger.warning("event=categorise_missing_transaction pk=%s rk=%s", transaction_pk, transaction_rk)
            return

        merchant = str(transaction.get("merchant") or transaction.get("raw_description") or "")
        category = finance_store.find_category_for_merchant(merchant) or "uncategorised"

        transaction["category"] = category
        transaction["categorised_at"] = utc_now_iso()
        transaction["updated_at"] = utc_now_iso()
        finance_store.save_transaction(transaction)

        logger.info("event=categorise_complete category=%s merchant=%s", category, merchant)
    except Exception:
        logger.exception("event=categorise_failed")


@app.timer_trigger(schedule=finance_settings.sweep_pots_schedule, arg_name="timer", run_on_startup=False, use_monitor=True)
def sweep_pots(timer: func.TimerRequest) -> None:
    if not _finance_enabled():
        logger.info("event=sweep_pots_skipped reason=finance_disabled")
        return
    if not settings.monzo_account_id or not finance_settings.monzo_spending_pot_id:
        logger.warning("event=sweep_pots_missing_configuration")
        return

    _, _, week_key = week_window()
    dedupe_id = f"weekly-sweep-{week_key}"

    try:
        finance_store.ensure_tables()
        access_token = service.get_monzo_access_token()
        response = monzo_client.deposit_to_pot(
            access_token=access_token,
            pot_id=finance_settings.monzo_spending_pot_id,
            source_account_id=settings.monzo_account_id,
            amount_pence=finance_settings.monzo_sweep_amount_pence,
            dedupe_id=dedupe_id,
        )
        response.raise_for_status()
        finance_store.record_sweep(week_key, finance_settings.monzo_sweep_amount_pence, "success")
        logger.info("event=sweep_pots_success week_key=%s", week_key)
    except Exception as exc:
        finance_store.record_sweep(week_key, finance_settings.monzo_sweep_amount_pence, "failed", str(exc))
        logger.exception("event=sweep_pots_failed week_key=%s", week_key)


@app.timer_trigger(schedule=finance_settings.debt_tracker_schedule, arg_name="timer", run_on_startup=False, use_monitor=True)
def debt_tracker(timer: func.TimerRequest) -> None:
    if not _finance_enabled():
        logger.info("event=debt_tracker_skipped reason=finance_disabled")
        return
    try:
        finance_store.ensure_tables()
        debt = finance_store.get_singleton(finance_settings.debt_tracker_table, "natwest", "primary") or _default_debt_entity()

        latest_rows = finance_store.list_latest_transactions(limit=250)
        for tx in latest_rows:
            if str(tx.get("source") or "") != "natwest":
                continue
            if tx.get("natwest_balance_pence") is None:
                continue
            debt["current_balance_pence"] = int(tx.get("natwest_balance_pence") or 0)
            break

        current_balance = int(debt.get("current_balance_pence") or 0)
        monthly_target = int(debt.get("monthly_payment_target_pence") or finance_settings.debt_monthly_payment_target_pence)
        target_months = int(debt.get("target_months") or finance_settings.debt_target_months)

        projection = project_debt(current_balance, monthly_target, target_months)
        debt["months_remaining"] = projection.months_remaining
        debt["on_track"] = projection.monthly_payment_on_track
        debt["updated_at"] = utc_now_iso()
        finance_store.upsert_singleton(finance_settings.debt_tracker_table, debt)
        logger.info("event=debt_tracker_complete months_remaining=%s on_track=%s", projection.months_remaining, projection.monthly_payment_on_track)
    except Exception:
        logger.exception("event=debt_tracker_failed")


@app.timer_trigger(schedule=finance_settings.advice_engine_schedule, arg_name="timer", run_on_startup=False, use_monitor=True)
def advice_engine(timer: func.TimerRequest) -> None:
    if not _finance_enabled():
        logger.info("event=advice_engine_skipped reason=finance_disabled")
        return
    try:
        finance_store.ensure_tables()
        week_start, week_end, week_key = week_window()

        weekly_spend = finance_store.get_weekly_discretionary_spend(week_start, week_end)
        category_totals = finance_store.get_weekly_spend_by_category(week_start, week_end)
        budget_targets = finance_store.get_weekly_budget_targets()
        overspend_categories = [
            category for category, amount in category_totals.items() if amount > int(budget_targets.get(category, 0) or 0) > 0
        ]

        debt = finance_store.get_singleton(finance_settings.debt_tracker_table, "natwest", "primary") or _default_debt_entity()
        emergency = finance_store.get_singleton(finance_settings.emergency_fund_table, "main", "primary") or _default_emergency_entity()
        previous_advice = finance_store.get_latest_advice()

        months_remaining = int(debt.get("months_remaining") or finance_settings.debt_target_months)
        on_track = bool(debt.get("on_track", False))

        payload = AdviceInput(
            weekly_spend_pence=weekly_spend,
            weekly_target_pence=finance_settings.weekly_discretionary_target_pence,
            overspend_categories=overspend_categories,
            natwest_balance_pence=int(debt.get("current_balance_pence") or 0),
            months_remaining=months_remaining,
            target_months=int(debt.get("target_months") or finance_settings.debt_target_months),
            monthly_payment_on_track=on_track,
            emergency_fund_pence=int(emergency.get("current_balance_pence") or 0),
            emergency_fund_target_pence=int(emergency.get("target_pence") or finance_settings.emergency_fund_target_pence),
            pot_balance_pence=_try_get_pot_balance_pence(),
            previous_advice_summary=str((previous_advice or {}).get("followed_summary") or "unknown"),
        )

        prompt = build_prompt(payload)
        advice = generate_weekly_advice(finance_settings, payload, settings.request_timeout)
        finance_store.save_weekly_advice(week_key, advice, prompt)
        logger.info("event=advice_engine_complete week_key=%s", week_key)
    except Exception:
        logger.exception("event=advice_engine_failed")


@app.queue_trigger(arg_name="msg", queue_name=finance_settings.alert_queue_name, connection="AzureWebJobsStorage")
def alert(msg: func.QueueMessage) -> None:
    if not _finance_enabled():
        logger.info("event=alert_skipped reason=finance_disabled")
        return
    try:
        payload = json.loads(msg.get_body().decode("utf-8"))
        alert_type = str(payload.get("type") or "")
        if alert_type != "discretionary_overspend":
            logger.info("event=alert_ignored type=%s", alert_type)
            return

        if not settings.monzo_account_id:
            logger.warning("event=alert_missing_account")
            return

        weekly_spend = int(payload.get("weekly_spend_pence") or 0)
        target = int(payload.get("target_pence") or finance_settings.weekly_discretionary_target_pence)
        access_token = service.get_monzo_access_token()

        title = f"Weekly spend alert: GBP {weekly_spend / 100:.2f}"
        body = f"Target is GBP {target / 100:.2f}. Review discretionary spending this week."
        monzo_client.post_feed(
            access_token=access_token,
            account_id=settings.monzo_account_id,
            click_url="monzo://home",
            title=title,
            body=body,
            color="#E67E22",
        )

        finance_store.upsert_singleton(
            finance_settings.ingestion_state_table,
            {
                "PartitionKey": "alert",
                "RowKey": str(payload.get("week_key") or utc_now_iso()),
                "type": alert_type,
                "sent_at": utc_now_iso(),
                "weekly_spend_pence": weekly_spend,
                "target_pence": target,
            },
        )
        logger.info("event=alert_sent type=%s", alert_type)
    except Exception:
        logger.exception("event=alert_failed")


@app.route(route="finance_summary", methods=["GET"])
def finance_summary(req: func.HttpRequest) -> func.HttpResponse:
    if not _finance_enabled():
        return _finance_disabled_response()
    try:
        finance_store.ensure_tables()
        week_start, week_end, _ = week_window()
        weekly_spend = finance_store.get_weekly_discretionary_spend(week_start, week_end)
        weekly_target = finance_settings.weekly_discretionary_target_pence

        debt = finance_store.get_singleton(finance_settings.debt_tracker_table, "natwest", "primary") or _default_debt_entity()
        emergency = finance_store.get_singleton(finance_settings.emergency_fund_table, "main", "primary") or _default_emergency_entity()
        advice = finance_store.get_latest_advice() or {}
        uploads = finance_store.get_source_upload_status()

        current_debt = int(debt.get("current_balance_pence") or 0)
        starting_debt = int(debt.get("starting_balance_pence") or max(current_debt, 1))
        debt_paid = max(starting_debt - current_debt, 0)

        emergency_current = int(emergency.get("current_balance_pence") or 0)
        emergency_target = int(emergency.get("target_pence") or finance_settings.emergency_fund_target_pence)

        response_payload = {
            "weekly": {
                "spend_pence": weekly_spend,
                "target_pence": weekly_target,
                "progress_percent": progress_percent(weekly_spend, weekly_target),
            },
            "debt": {
                "current_balance_pence": current_debt,
                "target_months": int(debt.get("target_months") or finance_settings.debt_target_months),
                "months_remaining": int(debt.get("months_remaining") or finance_settings.debt_target_months),
                "on_track": bool(debt.get("on_track", False)),
                "progress_percent": progress_percent(debt_paid, starting_debt),
            },
            "emergency_fund": {
                "current_balance_pence": emergency_current,
                "target_pence": emergency_target,
                "progress_percent": progress_percent(emergency_current, emergency_target),
            },
            "pot": {
                "balance_pence": _try_get_pot_balance_pence(),
            },
            "advice": {
                "text": str(advice.get("advice") or "No advice generated yet."),
                "generated_at": advice.get("generated_at"),
            },
            "uploads": uploads,
        }
        return _json_response(response_payload)
    except Exception:
        logger.exception("event=finance_summary_failed")
        return _json_response({"error": "Failed to build summary."}, status_code=500)


@app.route(route="finance_transactions", methods=["GET"])
def finance_transactions(req: func.HttpRequest) -> func.HttpResponse:
    if not _finance_enabled():
        return _finance_disabled_response()
    try:
        finance_store.ensure_tables()
        category = req.params.get("category")
        limit_raw = req.params.get("limit", "20")
        limit = max(1, min(int(limit_raw), 100))

        transactions = finance_store.list_latest_transactions(limit=limit, category=category)
        payload = {
            "transactions": [
                {
                    "occurred_at": tx.get("occurred_at"),
                    "merchant": tx.get("merchant"),
                    "amount_pence": int(tx.get("amount_pence") or 0),
                    "category": tx.get("category"),
                    "source": tx.get("source"),
                }
                for tx in transactions
            ]
        }
        return _json_response(payload)
    except Exception:
        logger.exception("event=finance_transactions_failed")
        return _json_response({"error": "Failed to fetch transactions."}, status_code=500)


@app.route(route="finance_advice", methods=["GET"])
def finance_advice(req: func.HttpRequest) -> func.HttpResponse:
    if not _finance_enabled():
        return _finance_disabled_response()
    try:
        finance_store.ensure_tables()
        advice = finance_store.get_latest_advice() or {}
        return _json_response(
            {
                "advice": str(advice.get("advice") or "No advice generated yet."),
                "generated_at": advice.get("generated_at"),
            }
        )
    except Exception:
        logger.exception("event=finance_advice_failed")
        return _json_response({"error": "Failed to fetch advice."}, status_code=500)


@app.route(route="finance_upload_status", methods=["GET"])
def finance_upload_status(req: func.HttpRequest) -> func.HttpResponse:
    if not _finance_enabled():
        return _finance_disabled_response()
    try:
        finance_store.ensure_tables()
        uploads = finance_store.get_source_upload_status()
        return _json_response(uploads)
    except Exception:
        logger.exception("event=finance_upload_status_failed")
        return _json_response({"error": "Failed to fetch upload status."}, status_code=500)
