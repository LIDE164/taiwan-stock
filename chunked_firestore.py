"""Small, dependency-free helpers for storing large Firestore record lists safely.

Firestore documents have a hard size limit.  The scanner therefore keeps a
small manifest in the historical document path and stores the variable-length
records in bounded chunk documents.  Readers still accept the legacy inline
list so deployments can migrate without a one-off data rewrite.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

STORAGE_SCHEMA_VERSION = 2
DEFAULT_MAX_CHUNK_BYTES = 180_000
DEFAULT_MAX_CHUNK_ITEMS = 75


def _encoded_size(value: Any) -> int:
    return len(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            default=str,
        ).encode("utf-8")
    )


def chunk_items(
    items: Sequence[Mapping[str, Any]],
    *,
    max_bytes: int = DEFAULT_MAX_CHUNK_BYTES,
    max_items: int = DEFAULT_MAX_CHUNK_ITEMS,
) -> list[list[dict[str, Any]]]:
    """Split mappings into chunks bounded by both encoded bytes and item count."""
    if max_bytes <= 0 or max_items <= 0:
        raise ValueError("chunk limits must be positive")

    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for raw_item in items:
        if not isinstance(raw_item, Mapping):
            raise TypeError("chunk items must be mappings")
        item = dict(raw_item)
        if _encoded_size({"items": [item]}) > max_bytes:
            raise ValueError("single record exceeds the configured Firestore chunk limit")

        candidate = [*current, item]
        if current and (
            len(candidate) > max_items
            or _encoded_size({"items": candidate}) > max_bytes
        ):
            chunks.append(current)
            current = [item]
        else:
            current = candidate

    if current:
        chunks.append(current)
    return chunks


def build_chunk_documents(
    items: Sequence[Mapping[str, Any]],
    *,
    prefix: str,
    version: str,
    max_bytes: int = DEFAULT_MAX_CHUNK_BYTES,
    max_items: int = DEFAULT_MAX_CHUNK_ITEMS,
) -> list[tuple[str, dict[str, Any]]]:
    """Return deterministic document ids and payloads for one manifest version."""
    safe_prefix = re.sub(r"[^A-Za-z0-9_-]+", "_", str(prefix)).strip("_") or "records"
    safe_version = re.sub(r"[^A-Za-z0-9_-]+", "_", str(version)).strip("_") or "current"
    chunks = chunk_items(items, max_bytes=max_bytes, max_items=max_items)
    total = len(chunks)
    return [
        (
            f"{safe_prefix}__{safe_version}__{index:03d}",
            {
                "storage_schema": STORAGE_SCHEMA_VERSION,
                "version": str(version),
                "chunk_index": index,
                "chunk_count": total,
                "item_count": len(chunk),
                "items": chunk,
            },
        )
        for index, chunk in enumerate(chunks)
    ]


def manifest_chunk_ids(manifest: Mapping[str, Any], ids_key: str) -> list[str]:
    raw_ids = manifest.get(ids_key, []) if isinstance(manifest, Mapping) else []
    if not isinstance(raw_ids, list):
        raise TypeError(f"invalid chunk manifest field: {ids_key}")
    ids = [str(item).strip() for item in raw_ids if str(item).strip()]
    if len(ids) != len(set(ids)):
        raise RuntimeError(f"duplicate chunk ids in manifest: {ids_key}")
    return ids


def load_chunked_items(
    database: Any,
    manifest: Mapping[str, Any],
    *,
    collection_name: str,
    ids_key: str,
    legacy_key: str,
) -> list[dict[str, Any]]:
    """Hydrate a schema-v2 manifest or return a validated legacy inline list."""
    schema = int(manifest.get("storage_schema") or 1) if isinstance(manifest, Mapping) else 1
    if schema < STORAGE_SCHEMA_VERSION:
        legacy = manifest.get(legacy_key, []) if isinstance(manifest, Mapping) else []
        return [dict(item) for item in legacy if isinstance(item, Mapping)] if isinstance(legacy, list) else []

    chunk_ids = manifest_chunk_ids(manifest, ids_key)
    expected_count = int(manifest.get("record_count") or 0)
    if expected_count and not chunk_ids:
        raise RuntimeError("chunk manifest has records but no chunk ids")
    if database is None:
        raise RuntimeError("Firestore is unavailable while loading chunked records")

    hydrated: list[dict[str, Any]] = []
    collection = database.collection(collection_name)
    for expected_index, chunk_id in enumerate(chunk_ids):
        snapshot = collection.document(chunk_id).get()
        if not snapshot.exists:
            raise RuntimeError(f"missing Firestore chunk: {collection_name}/{chunk_id}")
        payload = snapshot.to_dict() or {}
        if int(payload.get("chunk_index", expected_index)) != expected_index:
            raise RuntimeError(f"out-of-order Firestore chunk: {collection_name}/{chunk_id}")
        rows = payload.get("items", [])
        if not isinstance(rows, list):
            raise TypeError(f"invalid Firestore chunk payload: {collection_name}/{chunk_id}")
        hydrated.extend(dict(row) for row in rows if isinstance(row, Mapping))

    if len(hydrated) != expected_count:
        raise RuntimeError(
            f"chunked record count mismatch: expected {expected_count}, loaded {len(hydrated)}"
        )
    return hydrated
