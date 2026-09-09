import unittest

import pandas as pd

import backfill_top10
from chunked_firestore import load_chunked_items


class _Snapshot:
    def __init__(self, value):
        self._value = value
        self.exists = value is not None

    def to_dict(self):
        return dict(self._value or {})


class _Document:
    def __init__(self, collection, document_id):
        self._collection = collection
        self.id = document_id

    def get(self):
        return _Snapshot(self._collection.documents.get(self.id))

    def set(self, value, merge=False):
        existing = self._collection.documents.get(self.id)
        if merge and isinstance(existing, dict):
            existing.update(value)
        else:
            self._collection.documents[self.id] = dict(value)


class _Collection:
    def __init__(self, documents=None):
        self.documents = dict(documents or {})

    def document(self, document_id):
        return _Document(self, document_id)


class _Batch:
    def __init__(self):
        self.operations = []

    def set(self, reference, value, merge=False):
        self.operations.append(("set", reference, value, merge))

    def delete(self, reference):
        self.operations.append(("delete", reference, None, False))

    def commit(self):
        for operation, reference, value, merge in self.operations:
            if operation == "set":
                reference.set(value, merge=merge)
            else:
                reference._collection.documents.pop(reference.id, None)
        self.operations = []


class _Database:
    def __init__(self, collections=None):
        self.collections = {
            name: _Collection(documents)
            for name, documents in (collections or {}).items()
        }

    def collection(self, collection_name):
        return self.collections.setdefault(collection_name, _Collection())

    def batch(self):
        return _Batch()


class BackfillTop10StorageTests(unittest.TestCase):
    def test_build_backfill_fetches_the_expected_session_for_pending_entries(self):
        frame = pd.DataFrame(
            [
                {"Open": 99, "High": 102, "Low": 98, "Close": 100},
                {"Open": 101, "High": 105, "Low": 98, "Close": 102},
            ],
            index=pd.to_datetime(["2026-09-01", "2026-09-02"]),
        )
        rankings = {
            "2026-09-01": [{
                "代號": "2330", "名稱": "台積電", "收盤價": 100,
                "Entry_Low": 99, "Entry_High": 102,
                "Entry_Stop": 95, "Entry_Target": 110,
            }],
        }

        tracker, daily, missing = backfill_top10.build_backfill(
            rankings,
            ["2026-09-01", "2026-09-02"],
            {"2330": frame},
        )

        self.assertEqual(missing, ["2026-09-02"])
        self.assertEqual(tracker["positions"][0]["status"], "OPEN")
        self.assertEqual(tracker["positions"][0]["entry_date"], "2026-09-02")
        self.assertEqual(daily["2026-09-02"]["records"][0]["action"], "ENTRY")
        self.assertIn("cumulative_performance", daily["2026-09-02"])

    def test_write_backfill_uses_schema2_chunks_and_removes_stale_chunks(self):
        old_chunk_ids = ["top10_tracker__old__000", "top10_tracker__old__001"]
        existing = {
            "data": {
                "storage_schema": 2,
                "position_chunk_collection": "top10_tracker_chunks",
                "position_chunk_ids": old_chunk_ids,
                "record_count": 2,
            },
            "update_time": "old",
        }
        database = _Database({
            "market_data": {"top10_tracker": existing},
            "top10_tracker_chunks": {
                old_chunk_ids[0]: {"items": [{"ticker": "old-1"}]},
                old_chunk_ids[1]: {"items": [{"ticker": "old-2"}]},
            },
        })
        positions = [
            {"ticker": f"{index:04d}", "status": "OPEN"}
            for index in range(76)
        ]
        tracker_payload = {
            "positions": positions,
            "latest_date": "2026-09-07",
            "latest_snapshots": [],
            "history_dates": ["2026-09-07"],
            "backfill_status": "complete",
            "missing_ranking_dates": [],
            "partial_ranking_dates": [],
            "unverified_ranking_dates": [],
            "backfill_note": "test",
        }
        daily_payload = {
            "date": "2026-09-07",
            "records": [],
            "ranking_status": "ok",
        }
        rankings = {
            "2026-09-07": [{"代號": "2330", "OHLC_Status": "ok"}],
        }

        backfill_top10.write_backfill(
            database,
            tracker_payload,
            {"2026-09-07": daily_payload},
            rankings,
            [],
        )

        root = database.collection("market_data").documents["top10_tracker"]
        manifest = root["data"]
        self.assertEqual(manifest["storage_schema"], 2)
        self.assertEqual(manifest["position_chunk_collection"], "top10_tracker_chunks")
        self.assertEqual(manifest["record_count"], len(positions))
        self.assertEqual(len(manifest["position_chunk_ids"]), 2)
        self.assertNotIn("positions", manifest)
        self.assertEqual(len(manifest["content_hash"]), 64)

        hydrated = load_chunked_items(
            database,
            manifest,
            collection_name="top10_tracker_chunks",
            ids_key="position_chunk_ids",
            legacy_key="positions",
        )
        self.assertEqual(hydrated, positions)
        stored_chunks = database.collection("top10_tracker_chunks").documents
        self.assertTrue(all(chunk_id not in stored_chunks for chunk_id in old_chunk_ids))

        backups = database.collection("top10_tracker_backups").documents
        self.assertEqual(len(backups), 1)
        self.assertEqual(next(iter(backups.values()))["data"], existing)
        self.assertEqual(
            database.collection("top10_tracking_history").documents["2026-09-07"]["data"],
            daily_payload,
        )
        history = database.collection("top10_history").documents["2026-09-07"]
        self.assertEqual(history["data"], rankings["2026-09-07"])
        self.assertEqual(history["data_status"], "ok")


if __name__ == "__main__":
    unittest.main()
