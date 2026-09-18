import unittest
from unittest.mock import Mock, patch

import pandas as pd

import scanner


class ScannerFailFastTests(unittest.TestCase):
    def test_failed_lease_persists_only_the_exception_class(self):
        database = Mock()
        document = database.collection.return_value.document.return_value
        secret = "https://provider.invalid?token=super-secret"

        with patch.object(scanner, "db", database):
            scanner._finish_scan_lease(
                "2026-09-09",
                "failed",
                0,
                RuntimeError(secret),
            )

        saved = document.set.call_args.args[0]
        self.assertEqual(saved["error"], "RuntimeError")
        self.assertNotIn("super-secret", str(saved))

    def test_scheduled_scan_fails_when_firestore_is_missing(self):
        with patch.object(scanner, "db", None):
            with self.assertRaises(RuntimeError):
                scanner.run_daily_scan(force=True)

    def test_scan_fails_when_the_market_date_cannot_be_confirmed(self):
        empty_market = pd.DataFrame()
        with (
            patch.object(scanner, "db", object()),
            patch.object(scanner, "call_with_backoff", return_value=empty_market),
        ):
            with self.assertRaisesRegex(RuntimeError, "最新實際交易日"):
                scanner.run_daily_scan(force=True, allow_intraday=True)

    def test_force_does_not_publish_before_postclose(self):
        with (
            patch.object(scanner, "db", object()),
            patch.object(scanner, "should_run_postclose_scan", return_value=False),
            patch.object(scanner, "call_with_backoff") as market_loader,
        ):
            with self.assertRaisesRegex(RuntimeError, "盤中禁止"):
                scanner.run_daily_scan(force=True)
        market_loader.assert_not_called()


if __name__ == "__main__":
    unittest.main()
