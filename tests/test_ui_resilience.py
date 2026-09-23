import ast
import logging
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd


SOURCE_PATH = Path(__file__).resolve().parents[1] / "test.py"
SOURCE = SOURCE_PATH.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE, filename=str(SOURCE_PATH))


def _load_symbols(names, namespace):
    selected = []
    for node in TREE.body:
        node_name = getattr(node, "name", None)
        assigned_names = {
            target.id
            for target in getattr(node, "targets", [])
            if isinstance(target, ast.Name)
        }
        if node_name in names or assigned_names.intersection(names):
            selected.append(node)
    module = ast.Module(body=selected, type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(SOURCE_PATH), "exec"), namespace)
    return namespace


class _SessionState(dict):
    __getattr__ = dict.__getitem__
    __setattr__ = dict.__setitem__


class _StopRun(RuntimeError):
    pass


class UiResilienceTests(unittest.TestCase):
    def test_cards_label_disjoint_samples_without_fabricating_missing_win_rate(self):
        from ui_components import generate_cards_html

        frame = pd.DataFrame([{
            '代號': '2330', '名稱': '測試', 'Score': 80, '收盤價': 100, 'WinRate': None,
            'Backtest_Samples': 1, 'Backtest_Training_Samples': 1,
            'Backtest_Overall_Samples': 2, 'Validation_Samples': 1,
            'Entry_Status': '條件符合，待驗證', 'Entry_Ready': False,
        }])
        html = generate_cards_html(frame, safe_num=lambda value, default=0: float(value or default))
        self.assertIn('全期 2｜訓練 1｜驗證 1', html)
        self.assertIn('舊制技術回測', html)
        self.assertIn('條件符合，待驗證', html)
        self.assertNotIn('>0.0%</span>', html)
        self.assertNotIn('nan%', html)

    def test_manifest_read_failure_preserves_last_good_rows_and_provenance(self):
        state = _SessionState(
            scan_results=[{"代號": "2330"}],
            scan_date="2026-09-16",
            scan_limit=300,
            scan_results_synced_at=0,
        )
        namespace = {
            "time": time,
            "logging": logging,
            "st": SimpleNamespace(session_state=state),
            "safe_num": lambda value, fallback=0: fallback if value is None else value,
            "CLOUD_READ_TTL_SECONDS": {"market_data/daily_scan": 300},
            "load_cloud_doc": lambda *args, **kwargs: (_ for _ in ()).throw(
                RuntimeError("temporary Firestore outage")
            ),
        }
        _load_symbols({"hydrate_scan_results"}, namespace)

        rows = namespace["hydrate_scan_results"](force=True)

        self.assertEqual(rows, [{"代號": "2330"}])
        self.assertEqual(state.scan_date, "2026-09-16")
        self.assertEqual(state.scan_limit, 300)
        self.assertIn("保留上一版", state.cloud_last_error)

    def test_missing_document_is_distinct_from_a_firestore_read_error(self):
        state = _SessionState()
        fake_st = SimpleNamespace(session_state=state)

        class MissingDocument:
            exists = False

        class DocumentReference:
            def get(self):
                return MissingDocument()

        class CollectionReference:
            def document(self, _name):
                return DocumentReference()

        class Database:
            def collection(self, _name):
                return CollectionReference()

        namespace = {
            "time": time,
            "st": fake_st,
            "CLOUD_READ_TTL_SECONDS": {},
            "LOW_FIREBASE_READ_MODE": False,
            "db": Database(),
        }
        _load_symbols({"CloudDocumentReadError", "load_cloud_doc"}, namespace)
        self.assertEqual(
            namespace["load_cloud_doc"]("market_data", "daily_scan", raise_on_error=True),
            {},
        )
        self.assertIn("market_data/daily_scan:doc", state._cloud_doc_cache)

        class EmptyExistingDocument:
            exists = True

            def to_dict(self):
                return {}

        class EmptyDocumentReference:
            def get(self):
                return EmptyExistingDocument()

        class EmptyCollectionReference:
            def document(self, _name):
                return EmptyDocumentReference()

        class EmptyDatabase:
            def collection(self, _name):
                return EmptyCollectionReference()

        namespace["db"] = EmptyDatabase()
        state._cloud_doc_cache.clear()
        with self.assertRaises(namespace["CloudDocumentReadError"]):
            namespace["load_cloud_doc"](
                "market_data", "daily_scan", raise_on_error=True
            )
        self.assertNotIn("market_data/daily_scan:doc", state._cloud_doc_cache)

        class FailingDatabase:
            def collection(self, _name):
                raise PermissionError("denied")

        namespace["db"] = FailingDatabase()
        state._cloud_doc_cache.clear()
        with self.assertRaises(namespace["CloudDocumentReadError"]):
            namespace["load_cloud_doc"](
                "market_data", "daily_scan", raise_on_error=True
            )
        self.assertNotIn("market_data/daily_scan:doc", state._cloud_doc_cache)

    def test_incomplete_technical_history_stops_with_an_explicit_message(self):
        state = _SessionState()
        warnings = []
        captions = []
        fake_st = SimpleNamespace(
            session_state=state,
            warning=warnings.append,
            caption=captions.append,
            stop=lambda: (_ for _ in ()).throw(_StopRun()),
        )
        namespace = {
            "st": fake_st,
            "normalize_ticker": str,
        }
        _load_symbols({"MIN_ANALYSIS_BARS", "require_analysis_result"}, namespace)

        self.assertEqual(namespace["MIN_ANALYSIS_BARS"], 60)
        with self.assertRaises(_StopRun):
            namespace["require_analysis_result"](None, "2330", list(range(40)))
        self.assertIn("40 個交易日", warnings[0])
        self.assertIn("60 個交易日", warnings[0])
        self.assertIn("不會以缺值", captions[0])
        self.assertIn("len(df) < MIN_ANALYSIS_BARS", SOURCE)

    def test_finmind_futures_selector_never_mixes_contracts_or_sessions(self):
        namespace = {
            "pd": pd,
            "datetime": datetime,
            "timezone": timezone,
            "timedelta": timedelta,
        }
        _load_symbols({"select_finmind_txf_series"}, namespace)
        rows = [
            {"date": "2026-09-15", "futures_id": "TX", "contract_date": "202609", "trading_session": "position", "close": 24000},
            {"date": "2026-09-16", "futures_id": "TX", "contract_date": "202609", "trading_session": "position", "close": 24100},
            {"date": "2026-09-15", "futures_id": "TX", "contract_date": "202610", "trading_session": "position", "close": 25000},
            {"date": "2026-09-16", "futures_id": "TX", "contract_date": "202610", "trading_session": "position", "close": 25100},
            {"date": "2026-09-15", "futures_id": "TX", "contract_date": "202609", "trading_session": "after_market", "close": 26000},
            {"date": "2026-09-16", "futures_id": "TX", "contract_date": "202609", "trading_session": "after_market", "close": 26100},
        ]

        selected = namespace["select_finmind_txf_series"](
            rows,
            now_tpe=datetime(2026, 9, 16, 10, tzinfo=timezone(timedelta(hours=8))),
        )

        self.assertEqual(selected["contract_date"], "202609")
        self.assertEqual(selected["trading_session"], "position")
        self.assertEqual(selected["current"], 24100)
        self.assertEqual(selected["previous"], 24000)

    def test_finmind_futures_selector_fails_closed_without_series_keys(self):
        namespace = {
            "pd": pd,
            "datetime": datetime,
            "timezone": timezone,
            "timedelta": timedelta,
        }
        _load_symbols({"select_finmind_txf_series"}, namespace)
        self.assertIsNone(
            namespace["select_finmind_txf_series"](
                [{"date": "2026-09-16", "close": 24100}]
            )
        )


if __name__ == "__main__":
    unittest.main()
