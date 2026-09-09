import unittest

from chunked_firestore import (
    STORAGE_SCHEMA_VERSION,
    _encoded_size,
    build_chunk_documents,
    chunk_items,
    load_chunked_items,
)


class _Snapshot:
    def __init__(self, value):
        self._value = value
        self.exists = value is not None

    def to_dict(self):
        return dict(self._value or {})


class _Document:
    def __init__(self, value):
        self._value = value

    def get(self):
        return _Snapshot(self._value)


class _Collection:
    def __init__(self, documents):
        self._documents = documents

    def document(self, document_id):
        return _Document(self._documents.get(document_id))


class _Database:
    def __init__(self, collections=None):
        self._collections = collections or {}

    def collection(self, collection_name):
        return _Collection(self._collections.get(collection_name, {}))


class ChunkedFirestoreTests(unittest.TestCase):
    def test_build_chunk_documents_respects_item_limit_and_metadata(self):
        records = [{"ticker": str(index)} for index in range(5)]

        documents = build_chunk_documents(
            records,
            prefix="top10 tracker",
            version="2026/09/07",
            max_bytes=10_000,
            max_items=2,
        )

        self.assertEqual(
            [document_id for document_id, _payload in documents],
            [
                "top10_tracker__2026_09_07__000",
                "top10_tracker__2026_09_07__001",
                "top10_tracker__2026_09_07__002",
            ],
        )
        self.assertEqual([payload["item_count"] for _, payload in documents], [2, 2, 1])
        self.assertTrue(
            all(payload["chunk_count"] == 3 for _, payload in documents)
        )
        self.assertTrue(
            all(
                payload["storage_schema"] == STORAGE_SCHEMA_VERSION
                for _, payload in documents
            )
        )

    def test_chunk_items_respects_encoded_byte_limit(self):
        records = [
            {"ticker": "2330", "note": "a" * 20},
            {"ticker": "2317", "note": "b" * 20},
            {"ticker": "2454", "note": "c" * 20},
        ]
        two_record_limit = _encoded_size({"items": records[:2]})

        chunks = chunk_items(
            records,
            max_bytes=two_record_limit,
            max_items=10,
        )

        self.assertEqual(chunks, [records[:2], records[2:]])
        self.assertLessEqual(_encoded_size({"items": chunks[0]}), two_record_limit)

    def test_single_record_over_limit_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "single record exceeds"):
            chunk_items(
                [{"ticker": "2330", "note": "台積電" * 30}],
                max_bytes=40,
                max_items=10,
            )

    def test_legacy_inline_records_are_hydrated_without_database(self):
        manifest = {
            "positions": [
                {"ticker": "2330"},
                "invalid",
                None,
                {"ticker": "2317"},
            ]
        }

        records = load_chunked_items(
            None,
            manifest,
            collection_name="unused",
            ids_key="position_chunk_ids",
            legacy_key="positions",
        )

        self.assertEqual(records, [{"ticker": "2330"}, {"ticker": "2317"}])

    def test_schema2_chunks_are_hydrated_in_manifest_order(self):
        expected = [
            {"ticker": "2330", "rank": 1},
            {"ticker": "2317", "rank": 2},
            {"ticker": "2454", "rank": 3},
        ]
        documents = build_chunk_documents(
            expected,
            prefix="daily",
            version="2026-09-07",
            max_bytes=10_000,
            max_items=2,
        )
        database = _Database({"daily_chunks": dict(documents)})
        manifest = {
            "storage_schema": STORAGE_SCHEMA_VERSION,
            "record_count": len(expected),
            "record_chunk_ids": [document_id for document_id, _ in documents],
        }

        records = load_chunked_items(
            database,
            manifest,
            collection_name="daily_chunks",
            ids_key="record_chunk_ids",
            legacy_key="records",
        )

        self.assertEqual(records, expected)

    def test_missing_schema2_chunk_is_rejected(self):
        database = _Database({"daily_chunks": {}})
        manifest = {
            "storage_schema": STORAGE_SCHEMA_VERSION,
            "record_count": 1,
            "record_chunk_ids": ["daily__2026-09-07__000"],
        }

        with self.assertRaisesRegex(RuntimeError, "missing Firestore chunk"):
            load_chunked_items(
                database,
                manifest,
                collection_name="daily_chunks",
                ids_key="record_chunk_ids",
                legacy_key="records",
            )

    def test_schema2_record_count_mismatch_is_rejected(self):
        chunk_id = "daily__2026-09-07__000"
        database = _Database(
            {
                "daily_chunks": {
                    chunk_id: {
                        "storage_schema": STORAGE_SCHEMA_VERSION,
                        "chunk_index": 0,
                        "chunk_count": 1,
                        "item_count": 1,
                        "items": [{"ticker": "2330"}],
                    }
                }
            }
        )
        manifest = {
            "storage_schema": STORAGE_SCHEMA_VERSION,
            "record_count": 2,
            "record_chunk_ids": [chunk_id],
        }

        with self.assertRaisesRegex(
            RuntimeError,
            "chunked record count mismatch: expected 2, loaded 1",
        ):
            load_chunked_items(
                database,
                manifest,
                collection_name="daily_chunks",
                ids_key="record_chunk_ids",
                legacy_key="records",
            )


if __name__ == "__main__":
    unittest.main()
