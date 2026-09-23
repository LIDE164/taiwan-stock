"""Reconstruct display-only old evidence for the latest completed scan.

Preparation is read-only. Apply uses an optimistic Firestore transaction and
preserves all current strategy statistics and frozen tracking/entry records.
"""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

import pandas as pd
import yfinance as yf

from backtest_reporting import primary_backtest_display
from chunked_firestore import build_chunk_documents
from legacy_backtest import calculate_legacy_backtest
from ranking_comparison import build_comparison_rows


def merge_evidence(rows, evidence, scan_date):
    """Only add authentic same-day legacy evidence and optional matching mini-K."""
    result = deepcopy(rows)
    by_ticker = {str(row.get("代號")): row for row in result}
    if len(by_ticker) != len(rows) or not set(evidence).issubset(by_ticker):
        raise ValueError("duplicate or unknown tickers")
    for row in result:
        if row.get("Data_Date") != scan_date:
            raise ValueError("mixed scan dates")
    for ticker, addition in evidence.items():
        if set(addition) - {"Legacy_Backtest", "Mini_K"}:
            raise ValueError("backfill cannot change current strategy fields")
        snapshot = addition.get("Legacy_Backtest")
        if (not isinstance(snapshot, dict) or snapshot.get("as_of_date") != scan_date
                or snapshot.get("data_through") != scan_date):
            raise ValueError("backfill requires same-day evidence")
        row = by_ticker[ticker]
        candidate = {**row, "Legacy_Backtest": addition.get("Legacy_Backtest")}
        if not primary_backtest_display(candidate)["available"]:
            raise ValueError("unavailable or mismatched legacy evidence")
        row["Legacy_Backtest"] = deepcopy(addition["Legacy_Backtest"])
        if "Mini_K" in addition:
            bars = addition["Mini_K"]
            if (not bars or bars[-1].get("date") != scan_date
                    or abs(float(bars[-1]["close"]) - float(row["收盤價"])) > .02):
                raise ValueError("mini-K does not match saved close/date")
            dates = [str(bar.get("date")) for bar in bars]
            if dates != sorted(set(dates)) or any(date > scan_date for date in dates):
                raise ValueError("invalid mini-K dates")
            for bar in bars:
                values = [float(bar[key]) for key in ("open", "high", "low", "close")]
                if not all(pd.notna(value) and 0 < value < float("inf") for value in values):
                    raise ValueError("invalid mini-K prices")
                if values[1] != max(values) or values[2] != min(values):
                    raise ValueError("inconsistent mini-K OHLC")
            row["Mini_K"] = deepcopy(bars[-30:])
    return result


def prepare(destination):
    import scanner
    manifest = scanner._load_daily_scan_doc()
    scan_date, rows = manifest["scan_date"], manifest["data"]
    lock = scanner.db.collection("system_locks").document("daily_scan").get().to_dict() or {}
    if lock.get("status") != "completed" or lock.get("trading_date") != scan_date:
        raise RuntimeError("latest scan is not complete")
    if scanner._records_content_hash(rows) != manifest.get("content_hash"):
        raise RuntimeError("snapshot changed during read")
    scanner.fetch_top_stocks(1000)  # Resolve listed/OTC symbols from official metadata.
    frames = scanner.fetch_stock_data_batch([row["代號"] for row in rows])
    selected = {row["代號"] for row in build_comparison_rows(rows)}
    evidence, statuses = {}, Counter()
    for index, row in enumerate(rows, 1):
        ticker = row["代號"]
        history = frames.get(ticker)
        if history is None:
            history = scanner.get_stock_data(ticker)
        snapshot = calculate_legacy_backtest(history, as_of_date=scan_date)
        statuses[snapshot["status"]] += 1
        if snapshot["status"] == "complete":
            snapshot["reconstructed_at"] = datetime.now(timezone.utc).isoformat()
            snapshot["price_source"] = "Yahoo Finance adjusted OHLCV (same basis as scanner)"
            evidence[ticker] = {"Legacy_Backtest": snapshot}
            if ticker in selected:
                symbol = scanner.MARKET_SYMBOL_CACHE.get(ticker, f"{ticker}.TW")
                raw = yf.Ticker(symbol).history(
                    start=str((pd.Timestamp(scan_date) - pd.Timedelta(days=65)).date()),
                    end=str((pd.Timestamp(scan_date) + pd.Timedelta(days=1)).date()),
                    auto_adjust=False,
                )
                raw = raw.loc[raw.index.strftime("%Y-%m-%d") <= scan_date]
                bars = scanner.build_mini_kbars(raw)
                if bars and bars[-1]["date"] == scan_date and abs(bars[-1]["close"] - row["收盤價"]) <= .02:
                    evidence[ticker]["Mini_K"] = bars
        if index % 20 == 0:
            print(f"Prepared {index}/{len(rows)}", flush=True)
    merged = merge_evidence(rows, evidence, scan_date)
    bundle = {"scan_date": scan_date, "expected_hash": manifest["content_hash"], "evidence": evidence}
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    from top10_telegram import render_executable_image
    comparison = build_comparison_rows(merged)
    destination.with_suffix(".png").write_bytes(render_executable_image(merged, scan_date, comparison_results=comparison))
    print(json.dumps({"date": scan_date, "statuses": statuses, "comparison": [
        {"ticker": row["代號"], "name": row["名稱"], "versions": row["Execution_Versions"],
         "legacy": primary_backtest_display(row), "bars": len(row.get("Mini_K", []))}
        for row in comparison
    ]}, ensure_ascii=False), flush=True)


def apply_bundle(path):
    import scanner
    bundle = json.loads(Path(path).read_text(encoding="utf-8"))
    manifest = scanner._load_daily_scan_doc()
    scan_date = bundle["scan_date"]
    if manifest.get("scan_date") != scan_date or manifest.get("content_hash") != bundle["expected_hash"]:
        raise RuntimeError("snapshot changed; prepare again")
    if scanner._records_content_hash(manifest["data"]) != bundle["expected_hash"]:
        raise RuntimeError("snapshot changed during read")
    merged = merge_evidence(manifest["data"], bundle["evidence"], scan_date)
    new_hash = scanner._records_content_hash(merged)
    if new_hash == bundle["expected_hash"]:
        return
    version = f"{scan_date}_legacy_{new_hash[:12]}"
    database = scanner.db
    current_ref = database.collection("market_data").document("daily_scan")
    history_ref = database.collection("daily_scan_history").document(scan_date)
    lock_ref = database.collection("system_locks").document("daily_scan")

    @scanner.firestore.transactional
    def commit(transaction):
        current = current_ref.get(transaction=transaction).to_dict() or {}
        historical = history_ref.get(transaction=transaction).to_dict() or {}
        lock = lock_ref.get(transaction=transaction).to_dict() or {}
        if lock.get("status") != "completed" or lock.get("trading_date") != scan_date:
            raise RuntimeError("scan started; do not overwrite")
        for value in (current, historical):
            if value.get("scan_date") != scan_date or value.get("content_hash") != bundle["expected_hash"]:
                raise RuntimeError("scan/history revision changed; do not overwrite")
        for ref, collection, prefix in (
            (current_ref, scanner.DAILY_SCAN_CHUNK_COLLECTION, "daily_scan"),
            (history_ref, scanner.DAILY_SCAN_HISTORY_CHUNK_COLLECTION, "daily_scan_history"),
        ):
            documents = build_chunk_documents(merged, prefix=prefix, version=version)
            for document_id, payload in documents:
                transaction.set(database.collection(collection).document(document_id), payload)
            transaction.update(ref, {
                "storage_schema": 2, "chunk_collection": collection,
                "chunk_ids": [document_id for document_id, _ in documents],
                "record_count": len(merged), "content_hash": new_hash,
                "legacy_backtest_backfilled_at": scanner.firestore.SERVER_TIMESTAMP,
            })
        # Prior chunks remain recoverable; no entry/position/performance docs touched.

    commit(database.transaction())
    verified = scanner._load_daily_scan_doc()
    if verified.get("content_hash") != new_hash or verified["data"] != merged:
        raise RuntimeError("post-write verification failed")
    print(json.dumps({"applied": len(bundle["evidence"]), "date": scan_date,
                      "new_fields_preserved": True, "verified": True}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--prepare", metavar="OUTPUT_JSON")
    action.add_argument("--apply", metavar="PREPARED_JSON")
    args = parser.parse_args()
    prepare(args.prepare) if args.prepare else apply_bundle(args.apply)
