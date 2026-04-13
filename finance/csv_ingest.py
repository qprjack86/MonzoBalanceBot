from __future__ import annotations

import csv
import hashlib
import io
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Iterable

from finance.repository import TransactionRecord


def file_sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def detect_source(headers: list[str]) -> str:
    lowered = {h.strip().lower() for h in headers}
    if {"date", "type", "description", "value"}.issubset(lowered):
        return "natwest"
    if {"date", "name", "type", "currency", "gross"}.issubset(lowered):
        return "paypal"
    raise ValueError("Unrecognised CSV schema. Expected NatWest or PayPal export format.")


def parse_csv_transactions(payload: bytes) -> tuple[str, list[TransactionRecord]]:
    text = payload.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("CSV file is empty or missing headers")

    headers = [h or "" for h in reader.fieldnames]
    source = detect_source(headers)

    records: list[TransactionRecord] = []
    for idx, row in enumerate(reader, start=2):
        if source == "natwest":
            record = _parse_natwest_row(row, idx)
        else:
            record = _parse_paypal_row(row, idx)

        if record:
            records.append(record)

    return source, records


def _parse_natwest_row(row: dict[str, str], line_no: int) -> TransactionRecord | None:
    date_raw = (row.get("Date") or row.get("date") or "").strip()
    desc = (row.get("Description") or row.get("description") or "").strip()
    amount_raw = (row.get("Value") or row.get("value") or "").strip()
    balance_raw = (row.get("Balance") or row.get("balance") or "").strip()
    tx_type = (row.get("Type") or row.get("type") or "").strip()

    if not date_raw or not desc or not amount_raw:
        return None

    date_iso = _normalise_date(date_raw)
    amount_pence = _to_pence(amount_raw)
    # NatWest exports may represent outgoing transactions with either sign by format; keep outgoing as negative for spend reporting.
    if tx_type.lower() in {"debit", "card payment", "payment"} and amount_pence > 0:
        amount_pence *= -1

    external_id = f"natwest:{line_no}:{date_iso}:{amount_pence}:{desc.lower()}"
    metadata = {"line_no": line_no, "tx_type": tx_type}
    if balance_raw:
        metadata["balance_pence"] = _to_pence(balance_raw)

    return TransactionRecord(
        source="natwest",
        external_id=external_id,
        amount_pence=amount_pence,
        merchant=desc,
        date_iso=date_iso,
        metadata=metadata,
    )


def _parse_paypal_row(row: dict[str, str], line_no: int) -> TransactionRecord | None:
    date_raw = (row.get("Date") or row.get("date") or "").strip()
    name = (row.get("Name") or row.get("name") or "").strip()
    gross_raw = (row.get("Gross") or row.get("gross") or "").strip()
    tx_type = (row.get("Type") or row.get("type") or "").strip()
    currency = (row.get("Currency") or row.get("currency") or "GBP").strip().upper()

    if not date_raw or not name or not gross_raw:
        return None

    date_iso = _normalise_date(date_raw)
    amount_pence = _to_pence(gross_raw)

    external_id = f"paypal:{line_no}:{date_iso}:{amount_pence}:{name.lower()}"
    return TransactionRecord(
        source="paypal",
        external_id=external_id,
        amount_pence=amount_pence,
        merchant=name,
        date_iso=date_iso,
        currency=currency,
        metadata={"line_no": line_no, "tx_type": tx_type},
    )


def _normalise_date(raw: str) -> str:
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d %b %Y", "%d %B %Y", "%d/%m/%Y %H:%M:%S"):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.replace(tzinfo=UTC).isoformat()
        except ValueError:
            continue
    raise ValueError(f"Unsupported date format: {raw}")


def _to_pence(amount_raw: str) -> int:
    cleaned = amount_raw.replace(",", "").replace("£", "").strip()
    try:
        amount = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(f"Invalid amount: {amount_raw}") from exc
    return int(amount * 100)


def build_categorise_messages(source: str, inserted_rows: Iterable[str]) -> list[str]:
    return [f"{source}|{row_key}" for row_key in inserted_rows]
