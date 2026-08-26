import unittest
from unittest.mock import patch

import function_app
from stores.memory_store import MemoryStore


class PotSpendDedupeTests(unittest.TestCase):
    """Regression tests for the duplicate pot spend notification bug (Aug 2026).

    Root cause: 4 concurrent webhook invocations all read the same stale
    last_known_pot_balance before any wrote it back (read-modify-write race),
    and pot-transfer events (merchant = pot_xxx) had no filter, so all 4 sent
    a Telegram message for one £6.00 spend.
    """

    def setUp(self):
        self.store = MemoryStore()
        self.cursor = {}
        self.messages = []
        patchers = [
            patch.object(function_app, "store", self.store),
            patch.object(function_app, "finance_repo"),
        ]
        self._patchers = patchers
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)
        function_app.finance_repo.get_sync_cursor.side_effect = lambda k: self.cursor.get(k)
        function_app.finance_repo.set_sync_cursor.side_effect = (
            lambda k, v: self.cursor.__setitem__(k, str(v))
        )
        self._balance_patcher = patch.object(function_app, "_get_pot_balance_pence", return_value=4400)
        self._balance_patcher.start()
        self.addCleanup(self._balance_patcher.stop)
        self._send_patcher = patch.object(function_app, "_send_telegram_message", side_effect=self.messages.append)
        self._send_patcher.start()
        self.addCleanup(self._send_patcher.stop)
        self._jarvis_patcher = patch.object(function_app, "_notify_jarvis", return_value=None)
        self._jarvis_patcher.start()
        self.addCleanup(self._jarvis_patcher.stop)
        # baseline: £50.00 before the spend
        self.cursor["last_known_pot_balance"] = "5000"

    def _event(self, tx_id, merchant_id, category="eating_out", name=None):
        return {
            "type": "transaction.created",
            "data": {
                "id": tx_id,
                "merchant": {"id": merchant_id, "name": name or merchant_id},
                "category": category,
            },
        }

    def test_four_events_for_one_spend_send_once(self):
        """Simulates the exact 13:27:09 incident: 2 pot-transfer + 2 merchant events."""
        transfer = self._event("tx_pot_1", "pot_0000B5a1", category="transfers", name="pot_0000B5a1")
        merchant = self._event("tx_merch_1", "merch_0000Wetherspoon", name="Wetherspoon")

        # Pot-transfer events arrive first (as observed in App Insights)
        function_app._check_pot_card_spend(transfer)
        function_app._check_pot_card_spend(transfer)  # duplicate delivery
        function_app._check_pot_card_spend(merchant)
        function_app._check_pot_card_spend(merchant)  # duplicate delivery

        self.assertEqual(len(self.messages), 1)
        self.assertIn("Wetherspoon", self.messages[0])
        self.assertIn("£44.00 remaining", self.messages[0])

    def test_pot_transfer_never_notifies(self):
        transfer = self._event("tx_pot_2", "pot_0000B5a1", category="transfers", name="pot_0000B5a1")
        function_app._check_pot_card_spend(transfer)
        self.assertEqual(len(self.messages), 0)

    def test_duplicate_merchant_delivery_sends_once(self):
        merchant = self._event("tx_merch_2", "merch_0000Wetherspoon", name="Wetherspoon")
        function_app._check_pot_card_spend(merchant)
        function_app._check_pot_card_spend(merchant)
        self.assertEqual(len(self.messages), 1)

    def test_unrelated_transaction_does_not_notify(self):
        # Main-account spend with no pot movement: cursor baseline == current balance
        self.cursor["last_known_pot_balance"] = "4400"
        other = self._event("tx_other_1", "merch_0000Tesco", name="Tesco")
        function_app._check_pot_card_spend(other)
        self.assertEqual(len(self.messages), 0)

    def test_claim_is_exclusive(self):
        self.assertTrue(self.store.claim("pot_spend:abc", 600))
        self.assertFalse(self.store.claim("pot_spend:abc", 600))


if __name__ == "__main__":
    unittest.main()