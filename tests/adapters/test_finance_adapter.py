import json
import unittest
from datetime import UTC, datetime
from unittest.mock import patch

import azure.functions as func

import function_app


class _FakeQueueMessage:
    def __init__(self, payload: dict):
        self._payload = payload

    def get_body(self):
        return json.dumps(self._payload).encode("utf-8")


class FinanceAdapterTests(unittest.TestCase):
    def test_dashboard_summary_returns_expected_shape(self):
        req = func.HttpRequest(
            method="GET",
            url="http://localhost/api/dashboard/summary",
            headers={},
            params={},
            route_params={},
            body=b"",
        )

        week_start = datetime(2026, 4, 13, 0, 0, 0, tzinfo=UTC)
        week_end = datetime(2026, 4, 20, 0, 0, 0, tzinfo=UTC)

        with (
            patch("function_app._week_window_utc", return_value=(week_start, week_end)),
            patch("function_app._get_pot_balance_pence", return_value=22100),
            patch.object(function_app.finance_repo, "weekly_spend_pence", return_value=9500),
            patch.object(function_app.finance_repo, "get_budget_target", return_value={"amount_pence": 10700}),
            patch.object(function_app.finance_repo, "get_debt_tracker", return_value={"current_balance_pence": 300000, "months_remaining": 33, "target_months": 36, "on_track": True}),
            patch.object(function_app.finance_repo, "get_emergency_fund", return_value={"current_balance_pence": 120000, "target_balance_pence": 720000}),
            patch.object(function_app.finance_repo, "get_latest_advice", return_value={"week_start_iso": week_start.isoformat(), "advice_text": "Keep meal prep going."}),
            patch.object(function_app.finance_repo, "get_last_upload", side_effect=[{"processed_at": "2026-04-12T10:00:00+00:00"}, {"processed_at": "2026-04-12T11:00:00+00:00"}]),
        ):
            response = function_app.dashboard_summary(req)

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.get_body())
        self.assertEqual(payload["weekly_spend_pence"], 9500)
        self.assertEqual(payload["weekly_target_pence"], 10700)
        self.assertTrue(payload["debt"]["on_track"])
        self.assertEqual(payload["pot_balance_pence"], 22100)
        self.assertIn("advice", payload)
        self.assertIn("last_uploads", payload)

    def test_dashboard_transactions_uses_category_filter(self):
        req = func.HttpRequest(
            method="GET",
            url="http://localhost/api/dashboard/transactions?category=coffee",
            headers={},
            params={"category": "coffee"},
            route_params={},
            body=b"",
        )

        with patch.object(
            function_app.finance_repo,
            "list_recent_transactions",
            return_value=[
                {
                    "PartitionKey": "monzo",
                    "RowKey": "row1",
                    "date_iso": "2026-04-13T10:30:00+00:00",
                    "merchant": "Perky Beans",
                    "amount_pence": -450,
                    "category": "coffee",
                    "currency": "GBP",
                }
            ],
        ) as mock_list:
            response = function_app.dashboard_transactions(req)

        self.assertEqual(response.status_code, 200)
        mock_list.assert_called_once_with(limit=20, category="coffee")
        payload = json.loads(response.get_body())
        self.assertEqual(len(payload["transactions"]), 1)
        self.assertEqual(payload["transactions"][0]["category"], "coffee")

    def test_alert_weekly_spend_overshoot_calls_feed(self):
        msg = _FakeQueueMessage(
            {
                "type": "weekly_spend_overshoot",
                "weekly_spend_pence": 12200,
                "target_pence": 10700,
            }
        )

        with patch("function_app._send_feed_message") as mock_send:
            function_app.alert(msg)

        mock_send.assert_called_once()


if __name__ == "__main__":
    unittest.main()
