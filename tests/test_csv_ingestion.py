import unittest

from core.csv_ingestion import detect_csv_source, parse_csv


class CsvIngestionTests(unittest.TestCase):
    def test_detect_paypal_headers(self):
        source = detect_csv_source(["Date", "Transaction ID", "Gross", "Fee", "Net"])
        self.assertEqual(source, "paypal")

    def test_detect_natwest_headers(self):
        source = detect_csv_source(["Date", "Description", "Amount"])
        self.assertEqual(source, "natwest")

    def test_parse_natwest_with_balance(self):
        csv_content = "Date,Description,Amount,Balance\n2026-04-10,Test Merchant,-12.34,3306.66\n"
        source, rows = parse_csv(csv_content.encode("utf-8"))

        self.assertEqual(source, "natwest")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].amount_pence, -1234)
        self.assertEqual(rows[0].balance_pence, 330666)


if __name__ == "__main__":
    unittest.main()
