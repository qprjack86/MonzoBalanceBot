import unittest
from datetime import UTC, datetime

from core.finance_metrics import progress_percent, project_debt, week_window


class FinanceMetricsTests(unittest.TestCase):
    def test_progress_percent_bounds(self):
        self.assertEqual(progress_percent(50, 100), 50.0)
        self.assertEqual(progress_percent(-1, 100), 0.0)
        self.assertEqual(progress_percent(200, 100), 100.0)

    def test_project_debt_on_track(self):
        projection = project_debt(current_balance_pence=331900, monthly_payment_target_pence=9300, target_months=36)
        self.assertEqual(projection.months_remaining, 36)
        self.assertTrue(projection.monthly_payment_on_track)

    def test_week_window_uses_monday_start(self):
        current = datetime(2026, 4, 12, 12, 0, tzinfo=UTC)
        start, end, week_key = week_window(current)
        self.assertEqual(start.weekday(), 0)
        self.assertEqual((end - start).days, 7)
        self.assertEqual(week_key, "2026-04-06")


if __name__ == "__main__":
    unittest.main()
