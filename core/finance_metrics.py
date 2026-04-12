from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True)
class DebtProjection:
    months_remaining: int
    monthly_payment_on_track: bool


def week_window(now: datetime | None = None) -> tuple[datetime, datetime, str]:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    # Monday as week start in UTC/GMT.
    start = (current - timedelta(days=current.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=7)
    week_key = start.strftime("%Y-%m-%d")
    return start, end, week_key


def project_debt(
    current_balance_pence: int,
    monthly_payment_target_pence: int,
    target_months: int,
) -> DebtProjection:
    payment = max(monthly_payment_target_pence, 1)
    months_remaining = (current_balance_pence + payment - 1) // payment
    return DebtProjection(
        months_remaining=months_remaining,
        monthly_payment_on_track=months_remaining <= target_months,
    )


def progress_percent(current: int, target: int) -> float:
    if target <= 0:
        return 0.0
    pct = (current / target) * 100
    if pct < 0:
        return 0.0
    if pct > 100:
        return 100.0
    return round(pct, 2)
