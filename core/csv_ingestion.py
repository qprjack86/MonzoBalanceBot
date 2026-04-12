from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation


@dataclass(frozen=True)
class ParsedTransaction:
    source: str
    external_id: str
    occurred_at: str
    merchant: str
    amount_pence: int
    currency: str
    raw_description: str
    balance_pence: int | None = None


def _clean_header(name: str) -> str:
    return " ".join((name or "").strip().lower().split())


def _parse_decimal_to_pence(raw: str) -> int:
    cleaned = (raw or "").strip().replace(",", "")
    if cleaned == "":
        return 0
    try:
        return int((Decimal(cleaned) * 100).quantize(Decimal("1")))
    except (InvalidOperation, ValueError):
        return 0


def _parse_datetime(raw: str) -> str:
    candidate = (raw or "").strip()
    if not candidate:
        return datetime.now(UTC).isoformat()

    formats = [
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y %H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%d %b %Y",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(candidate, fmt)
            return dt.replace(tzinfo=UTC).isoformat()
        except ValueError:
            continue

    try:
        dt = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC).isoformat()
    except ValueError:
        return datetime.now(UTC).isoformat()


def detect_csv_source(headers: list[str]) -> str:
    normalized = {_clean_header(h) for h in headers}
    if {"transaction id", "gross"}.issubset(normalized):
        return "paypal"

    natwest_signals = {"date", "description"}
    if natwest_signals.issubset(normalized) and (
        "amount" in normalized or "debit amount" in normalized or "credit amount" in normalized
    ):
        return "natwest"

    return "unknown"


def parse_csv(content: bytes) -> tuple[str, list[ParsedTransaction]]:
    text = content.decode("utf-8-sig", errors="ignore")
    reader = csv.DictReader(io.StringIO(text))
    headers = reader.fieldnames or []
    source = detect_csv_source(headers)
    rows = list(reader)

    if source == "natwest":
        return source, _parse_natwest_rows(rows)
    if source == "paypal":
        return source, _parse_paypal_rows(rows)
    return source, []


def _parse_natwest_rows(rows: list[dict[str, str]]) -> list[ParsedTransaction]:
    parsed: list[ParsedTransaction] = []
    for idx, row in enumerate(rows, start=1):
        date_raw = row.get("Date") or row.get("date") or ""
        description = (row.get("Description") or row.get("description") or "").strip() or "Unknown"

        amount_raw = row.get("Amount") or row.get("amount")
        if amount_raw is None or str(amount_raw).strip() == "":
            debit_raw = row.get("Debit Amount") or row.get("debit amount") or "0"
            credit_raw = row.get("Credit Amount") or row.get("credit amount") or "0"
            debit = abs(_parse_decimal_to_pence(str(debit_raw)))
            credit = abs(_parse_decimal_to_pence(str(credit_raw)))
            amount_pence = credit - debit
        else:
            amount_pence = _parse_decimal_to_pence(str(amount_raw))

        tx_id = (
            row.get("Transaction ID")
            or row.get("transaction id")
            or row.get("Reference")
            or row.get("reference")
            or f"natwest-row-{idx}"
        )

        parsed.append(
            ParsedTransaction(
                source="natwest",
                external_id=str(tx_id),
                occurred_at=_parse_datetime(str(date_raw)),
                merchant=description,
                amount_pence=amount_pence,
                currency=(row.get("Currency") or row.get("currency") or "GBP").strip() or "GBP",
                raw_description=description,
                balance_pence=_parse_natwest_balance(row),
            )
        )
    return parsed


def _parse_natwest_balance(row: dict[str, str]) -> int | None:
    for key in ["Balance", "balance", "Running Balance", "running balance"]:
        raw = row.get(key)
        if raw is None or str(raw).strip() == "":
            continue
        return _parse_decimal_to_pence(str(raw))
    return None


def _parse_paypal_rows(rows: list[dict[str, str]]) -> list[ParsedTransaction]:
    parsed: list[ParsedTransaction] = []
    for idx, row in enumerate(rows, start=1):
        date_raw = row.get("Date") or row.get("date") or ""
        time_raw = row.get("Time") or row.get("time") or ""
        occurred = _parse_datetime(f"{date_raw} {time_raw}".strip())

        gross = _parse_decimal_to_pence(str(row.get("Gross") or row.get("gross") or "0"))
        fee = _parse_decimal_to_pence(str(row.get("Fee") or row.get("fee") or "0"))
        net = _parse_decimal_to_pence(str(row.get("Net") or row.get("net") or "0"))

        amount_pence = net if net != 0 else gross - fee
        name = (row.get("Name") or row.get("name") or "Unknown").strip() or "Unknown"
        tx_id = row.get("Transaction ID") or row.get("transaction id") or f"paypal-row-{idx}"

        parsed.append(
            ParsedTransaction(
                source="paypal",
                external_id=str(tx_id),
                occurred_at=occurred,
                merchant=name,
                amount_pence=amount_pence,
                currency=(row.get("Currency") or row.get("currency") or "GBP").strip() or "GBP",
                raw_description=(row.get("Type") or row.get("type") or "PayPal transaction"),
                balance_pence=None,
            )
        )
    return parsed
