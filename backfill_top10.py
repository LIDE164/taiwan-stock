"""Backfill Top-10 tracking dates without reconstructing unavailable rankings.

Existing ``top10_history/{date}`` documents are the only source of historical
rankings.  Missing ranking days are written as explicit missing snapshots; the
script never applies today's fundamentals or institutional data to the past.
Position marks and settlements use actual historical OHLC bars when available.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
import yfinance as yf
from firebase_admin import firestore

import scanner
from app_security import normalize_ticker, safe_iso_date
from top10_tracker import update_positions_with_snapshots

TPE = timezone(timedelta(hours=8))


def _document_data(snapshot: Any) -> Any:
    payload = snapshot.to_dict() or {}
    return payload.get("data")


def load_rankings(db: Any) -> dict[str, list[dict[str, Any]]]:
    rankings: dict[str, list[dict[str, Any]]] = {}
    for snapshot in db.collection("top10_history").stream():
        date_text = safe_iso_date(snapshot.id)
        data = _document_data(snapshot)
        if date_text and isinstance(data, list) and data:
            rankings[date_text] = [dict(row) for row in data if isinstance(row, dict)]
    return rankings


def fetch_trading_dates(start_date: str, end_date: str) -> list[str]:
    end_exclusive = (datetime.fromisoformat(end_date) + timedelta(days=1)).strftime("%Y-%m-%d")
    history = yf.Ticker("^TWII").history(start=start_date, end=end_exclusive, auto_adjust=False)
    if history is None or history.empty:
        raise RuntimeError("無法取得加權指數交易日曆，已中止回補")
    dates = sorted({pd.Timestamp(value).strftime("%Y-%m-%d") for value in history.index})
    return [value for value in dates if start_date <= value <= end_date]


def fetch_price_frames(tickers: set[str]) -> dict[str, pd.DataFrame]:
    frames: dict[str, pd.DataFrame] = {}

    def fetch(ticker: str) -> tuple[str, pd.DataFrame | None]:
        return ticker, scanner.get_stock_data(ticker)

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        for ticker, frame in executor.map(fetch, sorted(tickers)):
            if frame is not None and not frame.empty:
                normalized = frame.copy()
                normalized.index = pd.to_datetime(normalized.index).strftime("%Y-%m-%d")
                frames[ticker] = normalized
    return frames


def quote_for_date(frame: pd.DataFrame | None, date_text: str) -> dict[str, Any] | None:
    if frame is None or date_text not in frame.index:
        return None
    row = frame.loc[date_text]
    if isinstance(row, pd.DataFrame):
        row = row.iloc[-1]
    quote: dict[str, float] = {}
    for name in ("Open", "High", "Low", "Close"):
        try:
            value = float(row.get(name))
        except (TypeError, ValueError):
            return None
        if not pd.notna(value) or value <= 0:
            return None
        quote[name] = value
    if quote["High"] < max(quote["Open"], quote["Close"]):
        return None
    if quote["Low"] > min(quote["Open"], quote["Close"]):
        return None
    return quote


def enrich_ranking_rows(
    rows: list[dict[str, Any]],
    date_text: str,
    price_frames: dict[str, pd.DataFrame],
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for rank, source in enumerate(rows, start=1):
        row = dict(source)
        ticker = normalize_ticker(row.get("代號"))
        quote = quote_for_date(price_frames.get(ticker), date_text)
        try:
            stored_close = float(str(row.get("收盤價")))
        except (TypeError, ValueError):
            stored_close = 0.0
        close_matches = bool(
            quote
            and stored_close > 0
            and abs(quote["Close"] - stored_close) / stored_close <= 0.02
        )
        row["Rank"] = int(row.get("Rank") or rank)
        row["Market_Data_Date"] = date_text
        if close_matches and quote is not None:
            row.update({
                "開盤價": quote["Open"],
                "最高價": quote["High"],
                "最低價": quote["Low"],
                "Original_Scan_Close": stored_close,
                "收盤價": quote["Close"],
                "OHLC_Source": "historical_market",
                "OHLC_Status": "ok",
            })
        else:
            row.update({
                "開盤價": None,
                "最高價": None,
                "最低價": None,
                "OHLC_Source": "missing",
                "OHLC_Status": "close_mismatch" if quote else "missing",
            })
        enriched.append(row)
    return enriched


def build_backfill(
    rankings: dict[str, list[dict[str, Any]]],
    trading_dates: list[str],
    price_frames: dict[str, pd.DataFrame],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], list[str]]:
    positions: list[dict[str, Any]] = []
    daily_payloads: dict[str, dict[str, Any]] = {}
    missing_ranking_dates: list[str] = []

    for date_text in trading_dates:
        top10_rows = rankings.get(date_text, [])
        if top10_rows:
            top10_rows = enrich_ranking_rows(top10_rows, date_text, price_frames)
            rankings[date_text] = top10_rows
        complete_rankings = sum(1 for row in top10_rows if row.get("OHLC_Status") == "ok")
        if not top10_rows:
            ranking_status = "missing"
            missing_ranking_dates.append(date_text)
        elif complete_rankings == len(top10_rows):
            ranking_status = "ok"
        elif complete_rankings == 0:
            ranking_status = "unverified"
        else:
            ranking_status = "partial"

        open_tickers = {
            normalize_ticker(position.get("ticker"))
            for position in positions
            if position.get("status") == "OPEN"
        }
        quotes = {}
        for ticker in open_tickers:
            quote = quote_for_date(price_frames.get(ticker), date_text)
            if quote is not None:
                quotes[ticker] = quote

        positions, snapshots = update_positions_with_snapshots(
            positions,
            top10_rows,
            quotes,
            date_text,
        )
        action_counts: dict[str, int] = {}
        for snapshot in snapshots:
            action = str(snapshot.get("action", "UNKNOWN"))
            action_counts[action] = action_counts.get(action, 0) + 1
            snapshot["ranking_status"] = ranking_status
            if ranking_status in ("missing", "unverified"):
                snapshot["is_top10"] = None
                snapshot["top10_rank"] = None

        missing_reason = ""
        if ranking_status == "missing":
            missing_reason = "原始 Top10 榜單快照不存在，未使用事後資料重算排名"
        elif ranking_status == "unverified":
            missing_reason = "原始榜單價格無法與當日市場行情核對，未建立新進場"
        elif ranking_status == "partial":
            missing_reason = f"僅 {complete_rankings}/{len(top10_rows)} 筆榜單價格可與當日行情核對"
        daily_payloads[date_text] = {
            "date": date_text,
            "records": snapshots,
            "ranking_status": ranking_status,
            "data_status": "ok" if ranking_status == "ok" else "partial",
            "missing_reason": missing_reason,
            "summary": {
                "tracked_count": len(snapshots),
                "open_count": len([position for position in positions if position.get("status") == "OPEN"]),
                "actions": action_counts,
            },
        }

    latest_date = trading_dates[-1]
    partial_ranking_dates = [date for date, payload in daily_payloads.items() if payload["ranking_status"] == "partial"]
    unverified_ranking_dates = [date for date, payload in daily_payloads.items() if payload["ranking_status"] == "unverified"]
    tracker_payload = {
        "positions": positions,
        "latest_date": latest_date,
        "latest_snapshots": daily_payloads[latest_date]["records"],
        "history_dates": sorted(trading_dates, reverse=True)[:120],
        "backfill_status": "partial" if missing_ranking_dates or partial_ranking_dates or unverified_ranking_dates else "complete",
        "missing_ranking_dates": missing_ranking_dates,
        "partial_ranking_dates": partial_ranking_dates,
        "unverified_ranking_dates": unverified_ranking_dates,
        "backfill_note": "只使用既存歷史榜單與實際 OHLC；缺失榜單日未事後重算排名",
    }
    return tracker_payload, daily_payloads, missing_ranking_dates


def write_backfill(
    db: Any,
    tracker_payload: dict[str, Any],
    daily_payloads: dict[str, dict[str, Any]],
    rankings: dict[str, list[dict[str, Any]]],
    missing_ranking_dates: list[str],
) -> None:
    now = datetime.now(TPE)
    tracker_ref = db.collection("market_data").document("top10_tracker")
    existing = tracker_ref.get().to_dict() or {}
    backup_id = now.strftime("%Y%m%d_%H%M%S")
    db.collection("top10_tracker_backups").document(backup_id).set({
        "data": existing,
        "created_at": firestore.SERVER_TIMESTAMP,
        "reason": "before_truthful_daily_backfill",
    })

    batch = db.batch()
    writes = 0

    def commit_if_needed() -> None:
        nonlocal batch, writes
        if writes >= 400:
            batch.commit()
            batch = db.batch()
            writes = 0

    for date_text, payload in daily_payloads.items():
        batch.set(
            db.collection("top10_tracking_history").document(date_text),
            {"data": payload, "update_time": firestore.SERVER_TIMESTAMP},
        )
        writes += 1
        commit_if_needed()

    for date_text, rows in rankings.items():
        if date_text not in daily_payloads:
            continue
        complete_count = sum(1 for row in rows if row.get("OHLC_Status") == "ok")
        batch.set(
            db.collection("top10_history").document(date_text),
            {
                "data": rows,
                "scan_date": date_text,
                "data_status": "ok" if complete_count == len(rows) else "partial",
                "ohlc_complete_count": complete_count,
                "ohlc_backfilled_at": firestore.SERVER_TIMESTAMP,
            },
            merge=True,
        )
        writes += 1
        commit_if_needed()

    for date_text in missing_ranking_dates:
        if date_text in rankings:
            continue
        batch.set(
            db.collection("top10_history").document(date_text),
            {
                "data": [],
                "scan_date": date_text,
                "data_status": "missing",
                "missing_reason": "原始掃描榜單不存在，未使用事後資料重算",
                "backfilled_at": firestore.SERVER_TIMESTAMP,
            },
        )
        writes += 1
        commit_if_needed()

    batch.set(
        tracker_ref,
        {"data": tracker_payload, "update_time": firestore.SERVER_TIMESTAMP},
    )
    writes += 1
    if writes:
        batch.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description="Truthful Top10 daily tracking backfill")
    parser.add_argument("--start", default="", help="YYYY-MM-DD; defaults to earliest stored ranking")
    parser.add_argument("--end", default="", help="YYYY-MM-DD; defaults to latest daily scan")
    parser.add_argument("--apply", action="store_true", help="write verified backfill to Firestore")
    args = parser.parse_args()

    db = scanner.db
    if db is None:
        raise RuntimeError("Firestore 未初始化")
    rankings = load_rankings(db)
    if not rankings:
        raise RuntimeError("沒有任何可用的 Top10 歷史榜單")
    daily_scan = db.collection("market_data").document("daily_scan").get().to_dict() or {}
    start_date = safe_iso_date(args.start) or min(rankings)
    end_date = safe_iso_date(args.end) or safe_iso_date(daily_scan.get("scan_date")) or max(rankings)
    if end_date < start_date:
        raise ValueError("結束日期不得早於開始日期")

    trading_dates = fetch_trading_dates(start_date, end_date)
    stored_tickers = {
        normalize_ticker(row.get("代號"))
        for rows in rankings.values()
        for row in rows
        if normalize_ticker(row.get("代號"))
    }
    # Populate the authoritative .TW/.TWO mapping before downloading history.
    scanner.fetch_top_stocks(1000)
    price_frames = fetch_price_frames(stored_tickers)
    tracker_payload, daily_payloads, missing_dates = build_backfill(
        rankings,
        trading_dates,
        price_frames,
    )
    quote_missing_count = sum(
        1
        for payload in daily_payloads.values()
        for row in payload["records"]
        if row.get("data_status") != "ok"
    )
    quote_missing_details = [
        f"{date_text}:{row.get('ticker')}:{row.get('action')}"
        for date_text, payload in daily_payloads.items()
        for row in payload["records"]
        if row.get("data_status") != "ok"
    ]
    ranking_ohlc_missing = [
        f"{date_text}:{row.get('代號')}:{row.get('OHLC_Status')}"
        for date_text, rows in rankings.items()
        if date_text in trading_dates
        for row in rows
        if row.get("OHLC_Status") != "ok"
    ]
    print(f"range={start_date}..{end_date}")
    print(f"trading_dates={len(trading_dates)} stored_rankings={len([d for d in trading_dates if d in rankings])}")
    print(f"missing_ranking_dates={missing_dates}")
    print(f"partial_ranking_dates={tracker_payload['partial_ranking_dates']}")
    print(f"unverified_ranking_dates={tracker_payload['unverified_ranking_dates']}")
    print(f"tracking_documents={len(daily_payloads)} positions={len(tracker_payload['positions'])}")
    print(f"quote_missing_records={quote_missing_count}")
    print(f"quote_missing_details={quote_missing_details}")
    print(f"ranking_ohlc_missing={ranking_ohlc_missing}")
    if not args.apply:
        print("dry_run=true (use --apply after verification)")
        return 0

    write_backfill(db, tracker_payload, daily_payloads, rankings, missing_dates)
    logging.info("Top10 回補完成，共寫入 %d 個交易日", len(daily_payloads))
    print("applied=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
