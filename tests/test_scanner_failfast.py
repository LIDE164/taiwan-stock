import unittest
from unittest.mock import patch

import scanner


class ScannerFailFastTests(unittest.TestCase):
    def test_scheduled_scan_fails_when_firestore_is_missing(self):
        with patch.object(scanner, "db", None):
            with self.assertRaises(RuntimeError):
                scanner.run_daily_scan(force=True)


if __name__ == "__main__":
    unittest.main()
