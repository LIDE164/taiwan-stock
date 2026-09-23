"""Audited, opt-in recovery of false reverse-date tracking exclusions.

Preparation only reads cloud data and writes a local backup/preview. Apply uses
an optimistic transaction; it never changes entry rules or historical trades.
"""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date as calendar_date
import hashlib
import json
import math
from pathlib import Path

from chunked_firestore import build_chunk_documents, load_chunked_items
from top10_tracker import build_cumulative_performance_summary, update_positions_with_snapshots


def digest(value):
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, default=str,
    ).encode("utf-8")).hexdigest()


def valid_bar(bar):
    try:
        o, h, low, c = (float(bar[key]) for key in ("Open", "High", "Low", "Close"))
        return all(math.isfinite(v) and v > 0 for v in (o, h, low, c)) and low <= min(o, c) <= max(o, c) <= h
    except (KeyError, TypeError, ValueError):
        return False


def same_price(left, right, tolerance=.0001):
    try:
        a, b = float(left), float(right)
        return all(math.isfinite(v) and v > 0 for v in (a, b)) and abs(a - b) <= tolerance
    except (ValueError, TypeError):
        return False


def restore_reverse_gaps(positions, prior_records, current_records, prior_date, date):
    """Restore only the exact reverse-date failure, with complete prior evidence."""
    for value in (prior_date, date):
        if calendar_date.fromisoformat(value).isoformat() != value:
            raise ValueError("repair requires ISO dates")
    prior = {r["position_id"]: r for r in prior_records}
    current = {r["position_id"]: r for r in current_records}
    if len(prior) != len(prior_records) or len(current) != len(current_records):
        raise ValueError("duplicate snapshot IDs")
    restored = []
    for position in positions:
        if not (
            position.get("status") == "EXCLUDED_DATA_GAP"
            and position.get("resolution_date") == date
            and position.get("execution_data_gap_from") == prior_date
            and position.get("execution_data_gap_through", "") < prior_date < date
            and position.get("execution_data_gap_through")
        ):
            continue
        through = position["execution_data_gap_through"]
        if calendar_date.fromisoformat(through).isoformat() != through:
            raise ValueError("invalid gap date")
        pid = position["position_id"]
        before, failed = prior.get(pid, {}), current.get(pid, {})
        if not (
            before.get("date") == prior_date and before.get("status") == "OPEN"
            and before.get("action") == "HOLD" and before.get("data_status") == "ok"
            and failed.get("action") == "EXECUTION_DATA_GAP"
            and failed.get("date") == date and position.get("last_tracked_date") == date
            and valid_bar({key.title(): before.get(key) for key in ("open", "high", "low", "close")})
        ):
            raise ValueError(f"no complete prior evidence for {pid}")
        normalized = {**position, "signal_date": position.get("signal_date") or position.get("entry_date"),
                      "execution_schema": int(position.get("execution_schema") or 1),
                      "signal_snapshot": position.get("signal_snapshot", {})}
        for key in ("ticker", "signal_date", "entry_date", "execution_schema", "entry_win_rate",
                    "entry_backtest_samples", "entry_backtest_scope", "entry_backtest_status",
                    "signal_snapshot", "shares", "holding_session_count"):
            if normalized.get(key) != before.get(key):
                raise ValueError(f"immutable entry evidence changed: {pid}/{key}")
        for key in ("entry_price", "highest_price", "lowest_price"):
            if not same_price(position.get(key), before.get(key)):
                raise ValueError(f"frozen plan changed: {pid}/{key}")
        for key, multiplier in (("stop_price", .9), ("target_price", 1.15)):
            level = (float(position[key]) if normalized["execution_schema"] >= 2
                     else float(position["entry_price"]) * multiplier)
            if not same_price(level, before.get(key)):
                raise ValueError(f"frozen plan changed: {pid}/{key}")
        if not same_price(position.get("current_price"), before.get("mark_price")):
            raise ValueError(f"prior mark changed: {pid}")
        result = deepcopy(position)
        result.update(status="OPEN", last_tracked_date=prior_date, last_snapshot=deepcopy(before))
        for key in ("pnl_pct", "gross_pnl_amount", "estimated_transaction_cost", "net_pnl_amount", "net_pnl_pct"):
            result[key] = before.get(key)
        for key in ("resolution_date", "resolution_reason", "execution_data_gap_date",
                    "execution_data_gap_from", "execution_data_gap_through"):
            if before.get(key) is None:
                result.pop(key, None)
            else:
                result[key] = before[key]
        restored.append(result)
    if not restored:
        raise ValueError("no proven reverse-date exclusions to repair")
    return restored


def replay_bundle(bundle):
    date, prior_date = bundle["date"], bundle["prior_date"]
    backup = bundle["backup"]
    tracker = backup["tracker"]["data"]
    prior_history, history = backup["prior_history"]["data"], backup["history"]["data"]
    benchmark = bundle["benchmark"]
    if benchmark.get("date") != date or benchmark.get("previous_trading_date") != prior_date:
        raise ValueError("repair requires confirmed consecutive benchmark sessions")
    if (prior_history.get("date") != prior_date
            or not same_price(benchmark.get("previous_close"), prior_history["benchmark"].get("close"), .02)):
        raise ValueError("benchmark does not match saved prior evidence")
    positions = backup["positions"]
    restored = restore_reverse_gaps(positions, prior_history["records"], history["records"], prior_date, date)
    quotes = bundle["quotes"]
    for position in restored:
        ticker = position["ticker"]
        quote = quotes.get(ticker, {})
        if (quote.get("date") != date or not valid_bar(quote)
                or quote.get("previous_date") != prior_date
                or not same_price(quote.get("previous_close"), position.get("current_price"), .02)):
            raise ValueError(f"missing or inconsistent daily price evidence: {ticker}")
    # Empty input ranking deliberately prevents historical/new admissions.
    repaired, snapshots = update_positions_with_snapshots(restored, [], quotes, date, benchmark=benchmark)
    if any(s.get("data_status") != "ok" for s in snapshots) or len(snapshots) != len(restored):
        raise ValueError("replay did not produce complete authentic daily records")
    by_id = {p["position_id"]: p for p in repaired}
    records_by_id = {s["position_id"]: s for s in snapshots}
    for s in snapshots:
        original = next(r for r in history["records"] if r["position_id"] == s["position_id"])
        for key in ("ranking_status", "is_top10", "top10_rank", "score"):
            if key in original:
                s[key] = original[key]
        by_id[s["position_id"]]["last_snapshot"] = deepcopy(s)
    merged = [by_id.get(p["position_id"], deepcopy(p)) for p in positions]
    records = [records_by_id.get(r["position_id"], deepcopy(r)) for r in history["records"]]
    cumulative = build_cumulative_performance_summary(merged, date)
    metadata = {"reason": "reversed_benchmark_predecessor", "prior_date": prior_date,
                "affected_position_ids": sorted(by_id), "backup_digest": digest(backup)}
    new_tracker = deepcopy(tracker)
    new_tracker.update(content_hash=digest(merged), latest_snapshots=records,
                       latest_benchmark=benchmark, cumulative_performance=cumulative, chronology_repair=metadata)
    new_history = deepcopy(history)
    new_history.update(records=records, benchmark=benchmark, cumulative_performance=cumulative,
                       chronology_repair=metadata)
    new_history["summary"] = {
        "tracked_count": len(records), "open_count": sum(p.get("status") == "OPEN" for p in merged),
        "pending_count": sum(p.get("status") == "PENDING" for p in merged),
        "unresolved_count": sum(p.get("status") in {"UNRESOLVED", "EXCLUDED_UNRESOLVED"} for p in merged),
        "data_gap_count": sum(p.get("status") == "EXCLUDED_DATA_GAP" for p in merged),
        "actions": dict(Counter(r["action"] for r in records)),
    }
    return merged, new_tracker, new_history


def prepare(destination, date, prior_date):
    import pandas as pd
    import yfinance as yf
    import scanner
    database = scanner.db
    paths = {"tracker": "market_data/top10_tracker", "history": f"top10_tracking_history/{date}",
             "prior_history": f"top10_tracking_history/{prior_date}", "lock": "system_locks/daily_scan"}
    backup = {name: database.document(path).get().to_dict() for name, path in paths.items()}
    if backup["lock"].get("status") != "completed" or backup["lock"].get("trading_date") != date:
        raise RuntimeError("scan must be completed for the requested date")
    tracker = backup["tracker"]["data"]
    if tracker.get("latest_date") != date:
        raise RuntimeError("only latest-day false exclusions may be repaired")
    backup["positions"] = load_chunked_items(database, tracker, collection_name=scanner.TRACKER_CHUNK_COLLECTION,
                                           ids_key="position_chunk_ids", legacy_key="positions")
    if scanner._records_content_hash(backup["positions"]) != tracker["content_hash"]:
        raise RuntimeError("position hash mismatch")
    backup["chunks"] = {cid: database.collection(scanner.TRACKER_CHUNK_COLLECTION).document(cid).get().to_dict()
                        for cid in tracker["position_chunk_ids"]}
    restored = restore_reverse_gaps(backup["positions"], backup["prior_history"]["data"]["records"],
                                   backup["history"]["data"]["records"], prior_date, date)
    frame = yf.Ticker("^TWII").history(period="4mo")
    frame = frame.loc[frame.index.strftime("%Y-%m-%d") <= date]
    prior_benchmark = backup["prior_history"]["data"]["benchmark"]
    frame = scanner.reconcile_benchmark_history(frame, [prior_benchmark])
    benchmark = scanner.build_benchmark_context(frame)
    if benchmark["date"] != date or not same_price(benchmark.get("close"), tracker["latest_benchmark"].get("close"), .02):
        raise RuntimeError("current benchmark differs from saved source")
    benchmark["recovered_previous_source"] = f"top10_tracking_history/{prior_date}"
    scanner.fetch_top_stocks(1000)

    def fetch(position):
        ticker = position["ticker"]
        stock = scanner.get_stock_data(ticker)
        if stock is None or stock.empty:
            raise RuntimeError(f"missing authentic stock history: {ticker}")
        dates = stock.index.strftime("%Y-%m-%d")
        today_rows, prior_rows = stock.loc[dates == date], stock.loc[dates == prior_date]
        if len(today_rows) != 1 or len(prior_rows) != 1:
            raise RuntimeError(f"missing/duplicate actual dated OHLC: {ticker}")
        latest = today_rows.iloc[0]
        return ticker, {**{key: float(latest[key]) for key in ("Open", "High", "Low", "Close")},
                        "date": date, "previous_date": prior_date,
                        "previous_close": float(prior_rows.iloc[0]["Close"]),
                        "source": "Yahoo Finance adjusted OHLC (same basis as scanner)"}

    with ThreadPoolExecutor(max_workers=4) as executor:
        quotes = dict(executor.map(fetch, restored))
    bundle = {"date": date, "prior_date": prior_date, "backup": backup, "benchmark": benchmark,
              "quotes": quotes, "created_at": pd.Timestamp.now(tz="UTC").isoformat()}
    positions, new_tracker, history = replay_bundle(bundle)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(bundle, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    from top10_telegram import render_tracking_performance_images, build_tracking_performance_report
    report = build_tracking_performance_report(history["records"], positions, date,
                                              cumulative_summary=history["cumulative_performance"])
    images = render_tracking_performance_images(history["records"], positions, date,
                                               cumulative_summary=history["cumulative_performance"])
    for index, image in enumerate(images, 1):
        destination.with_name(f"{destination.stem}-{index}.png").write_bytes(image)
    print(json.dumps({"prepared": len(restored), "date": date, "summary": history["summary"],
                      "benchmark": benchmark, "tracked_count": report["tracked_count"],
                      "pages": len(images), "unchanged_other_positions": len(positions) - len(restored)}, ensure_ascii=False))


def apply_bundle(path):
    import scanner
    bundle = json.loads(Path(path).read_text(encoding="utf-8"))
    positions, tracker, history = replay_bundle(bundle)
    date, prior_date = bundle["date"], bundle["prior_date"]
    database, backup = scanner.db, bundle["backup"]
    paths = {"tracker": "market_data/top10_tracker", "history": f"top10_tracking_history/{date}",
             "prior_history": f"top10_tracking_history/{prior_date}", "lock": "system_locks/daily_scan"}
    documents = build_chunk_documents(positions, prefix="top10_tracker",
                                      version=f"{date}_chronology_{tracker['content_hash'][:12]}")
    tracker["position_chunk_ids"] = [cid for cid, _ in documents]

    @scanner.firestore.transactional
    def commit(transaction):
        current = {name: database.document(target).get(transaction=transaction).to_dict()
                   for name, target in paths.items()}
        chunks = {cid: database.collection(scanner.TRACKER_CHUNK_COLLECTION).document(cid).get(transaction=transaction).to_dict()
                  for cid in backup["chunks"]}
        for name, value in current.items():
            if digest(value) != digest(backup[name]):
                raise RuntimeError(f"cloud {name} changed; prepare again")
        if digest(chunks) != digest(backup["chunks"]):
            raise RuntimeError("position chunks changed; prepare again")
        if current["lock"].get("status") != "completed" or current["lock"].get("trading_date") != date:
            raise RuntimeError("scan is not completed")
        for cid, payload in documents:
            transaction.set(database.collection(scanner.TRACKER_CHUNK_COLLECTION).document(cid), payload)
        for name, payload in (("tracker", tracker), ("history", history)):
            transaction.update(database.document(paths[name]), {"data": payload, "update_time": scanner.firestore.SERVER_TIMESTAMP})
        # Original chunks are retained for recovery. Prior history/closed trades are untouched.

    commit(database.transaction())
    verified = database.document(paths["tracker"]).get().to_dict()["data"]
    actual = load_chunked_items(database, verified, collection_name=scanner.TRACKER_CHUNK_COLLECTION,
                               ids_key="position_chunk_ids", legacy_key="positions")
    if actual != positions or database.document(paths["history"]).get().to_dict()["data"] != history:
        raise RuntimeError("repair verification failed")
    print(json.dumps({"applied": len(tracker["chronology_repair"]["affected_position_ids"]),
                      "date": date, "verified": True, "new_hash": tracker["content_hash"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--prepare", metavar="OUTPUT_JSON")
    action.add_argument("--apply", metavar="PREPARED_JSON")
    parser.add_argument("--date")
    parser.add_argument("--prior-date")
    args = parser.parse_args()
    if args.prepare:
        if not args.date or not args.prior_date:
            parser.error("preparation requires --date and --prior-date")
        prepare(args.prepare, args.date, args.prior_date)
    else:
        apply_bundle(args.apply)
