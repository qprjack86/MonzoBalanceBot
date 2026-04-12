import unittest

import azure.functions as func

import function_app


class AzureAdapterTests(unittest.TestCase):
    def test_function_entrypoint_exists(self):
        self.assertTrue(callable(function_app.monzo_webhook))

    def test_health_entrypoint_exists(self):
        self.assertTrue(callable(function_app.health))

    def test_finance_trigger_entrypoints_exist(self):
        self.assertTrue(callable(function_app.ingest_monzo))
        self.assertTrue(callable(function_app.ingest_csv))
        self.assertTrue(callable(function_app.categorise))
        self.assertTrue(callable(function_app.sweep_pots))
        self.assertTrue(callable(function_app.debt_tracker))
        self.assertTrue(callable(function_app.advice_engine))
        self.assertTrue(callable(function_app.alert))

    def test_finance_http_entrypoints_exist(self):
        self.assertTrue(callable(function_app.finance_summary))
        self.assertTrue(callable(function_app.finance_transactions))
        self.assertTrue(callable(function_app.finance_advice))
        self.assertTrue(callable(function_app.finance_upload_status))

    def test_health_entrypoint_returns_ok_payload(self):
        req = func.HttpRequest(
            method="GET",
            url="http://localhost/api/health",
            headers={},
            params={},
            route_params={},
            body=b"",
        )

        response = function_app.health(req)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_body(), b'{"status":"ok"}')


if __name__ == "__main__":
    unittest.main()
