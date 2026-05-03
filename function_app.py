import logging
import json
import os
import urllib.request
import uuid
from datetime import UTC, datetime, timedelta

import azure.functions as func

from finance.constants import KNOWN_MERCHANT_CATEGORY_MAP
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

JARVIS_WEBHOOK_URL = os.getenv("JARVIS_WEBHOOK_URL", "https://openclaw.tailc5daaa.ts.net/plugins/webhooks/finance-bot")
JARVIS_WEBHOOK_SECRET = os.getenv("JARVIS_WEBHOOK_SECRET", "")


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
    return "uncategorised"


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


def _notify_jarvis(payload: dict) -> None:
    """POST a financial event to Jarvis via the Funnel webhook."""
    if not JARVIS_WEBHOOK_SECRET:
        logger.info("event=notify_jarvis_skipped no secret configured")
        return
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            JARVIS_WEBHOOK_URL,
            data=data,
            headers={
                "Content-Type": "application/json",
                "x-openclaw-webhook-secret": JARVIS_WEBHOOK_SECRET,
            },
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
        logger.info("event=notify_jarvis_success type=%s", payload.get("action"))
    except Exception as exc:
        logger.warning("event=notify_jarvis_failed error=%s", exc)


def _json_response(payload: dict) -> func.HttpResponse:
    return func.HttpResponse(json.dumps(payload, ensure_ascii=True), status_code=200, mimetype="application/json")


# ── Webhooks ──────────────────────────────────────────────────────────────

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


# ── Data ingestion ─────────────────────────────────────────────────────────

@app.timer_trigger(schedule="0 0 * * * *", arg_name="timer", run_on_startup=False, use_monitor=True)
@app.queue_output(arg_name="alert_queue", queue_name="%ALERT_QUEUE_NAME%", connection="AzureWebJobsStorage")
def ingest_monzo(timer: func.TimerRequest, alert_queue: func.Out[str]) -> None:
    """Hourly: pull new Monzo transactions and check weekly spend threshold."""
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
    transactions = response.json().get("transactions", [])
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

    # Weekly overspend check
    week_start, week_end = _week_window_utc()
    week_start_iso = week_start.isoformat()
    overspend_key = f"overspend_alert:{week_start_iso}"
    weekly_spend = finance_repo.weekly_spend_pence(week_start_iso, week_end.isoformat())
    weekly_target = _weekly_target_pence()
    alert_sent = finance_repo.get_sync_cursor(overspend_key)
    if weekly_spend > weekly_target and not alert_sent:
        alert_queue.set(json.dumps({
            "type": "weekly_spend_overshoot",
            "weekly_spend_pence": weekly_spend,
            "target_pence": weekly_target,
            "week_start_iso": week_start_iso,
        }, ensure_ascii=True))
        finance_repo.set_sync_cursor(overspend_key, datetime.now(UTC).isoformat())

    logger.info("event=ingest_monzo_complete inserted=%s total=%s", inserted, len(transactions))


# ── Advice engine (replaced — posts to Jarvis) ────────────────────────────

@app.timer_trigger(schedule="0 0 7 * * 1", arg_name="timer", run_on_startup=False, use_monitor=True)
def advice_engine(timer: func.TimerRequest) -> None:
    """Weekly: gather financial summary and POST it to Jarvis for personalised advice."""
    del timer

    week_start, week_end = _week_window_utc()
    week_start_iso = week_start.isoformat()

    weekly_spend = finance_repo.weekly_spend_pence(week_start_iso, week_end.isoformat())
    weekly_target = _weekly_target_pence()

    breakdown = finance_repo.weekly_spend_breakdown(week_start_iso, week_end.isoformat())
    overspend_categories = [k for k, _ in sorted(breakdown.items(), key=lambda item: item[1], reverse=True)[:3]]

    # MBNA 0% balance transfer: £3,319 at £93/month, clears April 2029

    emergency = finance_repo.get_emergency_fund() or {}
    emergency_current = int(emergency.get("current_balance_pence", 0))
    emergency_target = int(emergency.get("target_balance_pence", settings.emergency_fund_target_pence))

    pot_balance = _get_pot_balance_pence() or 0
    last_advice = finance_repo.get_latest_advice() or {}

    # Post summary to Jarvis via Funnel webhook
    _notify_jarvis({
        "action": "weekly_financial_summary",
        "week_start_iso": week_start_iso,
        "data": {
            "weekly_spend_pence": weekly_spend,
            "weekly_target_pence": weekly_target,
            "overspend_categories": overspend_categories,
            "debt_balance_pence": 331900,
            "debt_monthly_payment_pence": 9300,
            "emergency_fund_pence": emergency_current,
            "emergency_fund_target_pence": emergency_target,
            "pot_balance_pence": pot_balance,
            "last_advice": last_advice.get("advice_text", ""),
        },
    })

    logger.info("event=advice_engine_posted week_start=%s", week_start_iso)


# ── Alert handler ─────────────────────────────────────────────────────────

@app.queue_trigger(arg_name="msg", queue_name="%ALERT_QUEUE_NAME%", connection="AzureWebJobsStorage")
def alert(msg: func.QueueMessage) -> None:
    """Process finance alerts: post to Monzo feed + notify Jarvis."""
    body = msg.get_body().decode("utf-8")
    payload = json.loads(body)
    event_type = str(payload.get("type", "")).strip().lower()

    if event_type == "weekly_spend_overshoot":
        weekly_spend = int(payload.get("weekly_spend_pence", 0))
        target = int(payload.get("target_pence", settings.weekly_discretionary_target_pence))
        over = max(0, weekly_spend - target)
        _send_feed_message(
            title=f"Weekly spend alert: {_currency(weekly_spend)}",
            body=f"You are {_currency(over)} over your weekly target. Tighten discretionary spending.",
            color="#D35400",
        )
        _notify_jarvis({
            "action": "alert",
            "type": "weekly_spend_overshoot",
            "weekly_spend_pence": weekly_spend,
            "target_pence": target,
        })
        logger.info("event=alert_weekly_spend_sent amount=%s", weekly_spend)
        return

    if event_type == "low_balance":
        balance_pence = int(payload.get("balance_pence", 0))
        _send_feed_message(
            title=f"Balance warning: {_currency(balance_pence)}",
            body="Low balance detected. Review upcoming direct debits and discretionary spend.",
            color="#C0392B",
        )
        _notify_jarvis({
            "action": "alert",
            "type": "low_balance",
            "balance_pence": balance_pence,
        })
        logger.info("event=alert_low_balance_sent balance=%s", balance_pence)
        return

    logger.warning("event=alert_unknown_type payload=%s", body)


# ── Dashboard APIs (consumed by Jarvis) ───────────────────────────────────

@app.route(route="dashboard/summary", methods=["GET"])
def dashboard_summary(req: func.HttpRequest) -> func.HttpResponse:
    """Current snapshot — polled by Jarvis weekly cron or on demand."""
    del req
    week_start, week_end = _week_window_utc()
    week_start_iso = week_start.isoformat()
    weekly_spend = finance_repo.weekly_spend_pence(week_start_iso, week_end.isoformat())
    weekly_target = _weekly_target_pence()

    # MBNA £3,319 at £93/month — clears April 2029
    debt_balance = 331900

    emergency = finance_repo.get_emergency_fund() or {}
    emergency_current = int(emergency.get("current_balance_pence", 0))
    emergency_target = int(emergency.get("target_balance_pence", settings.emergency_fund_target_pence))
    emergency_progress = 0 if emergency_target <= 0 else max(0.0, min(1.0, emergency_current / emergency_target))

    latest_advice = finance_repo.get_latest_advice() or {}
    pot_balance = _get_pot_balance_pence()

    return _json_response({
        "weekly_spend_pence": weekly_spend,
        "weekly_target_pence": weekly_target,
        "weekly_progress": min(1.0, weekly_spend / weekly_target) if weekly_target > 0 else 0,
        "debt": {
            "current_balance_pence": debt_balance,
            "monthly_payment_pence": 9300,
            "target_balance_pence": 0,
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
    })


@app.route(route="dashboard/transactions", methods=["GET"])
def dashboard_transactions(req: func.HttpRequest) -> func.HttpResponse:
    """Recent transactions — used by Jarvis for on-demand queries."""
    category = req.params.get("category")
    entries = finance_repo.list_recent_transactions(limit=20, category=category)
    payload = []
    for entity in entries:
        payload.append({
            "source": entity.get("PartitionKey"),
            "row_key": entity.get("RowKey"),
            "date_iso": entity.get("date_iso"),
            "merchant": entity.get("merchant"),
            "amount_pence": int(entity.get("amount_pence", 0) or 0),
            "category": entity.get("category", "uncategorised"),
            "currency": entity.get("currency", "GBP"),
        })
    return _json_response({"transactions": payload})
