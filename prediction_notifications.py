"""Hourly Telegram prices for a frozen prediction list; no ranking writes."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
from importlib import import_module
import json
import logging
import os

from app_security import normalize_ticker
from market_calendar import is_scheduled_session, next_scheduled_session
from prediction_health_checks import has_completed_due_notifications
from prediction_quotes import fetch_prediction_quotes
from prediction_watch import notification_slot, select_prediction_rows, format_prediction_messages
from research_delivery import DeliveryRejected, FirestoreReportStore, deliver_report, telegram_send_text

TPE = timezone(timedelta(hours=8))
WATCH_SCHEMA = "prediction_watchlist_v1"
FORMAT_VERSION = "prediction_hourly_prices_v1"


class QuotesPending(RuntimeError):
    """Wait for a later scheduled attempt without sending invented prices."""


class SlotExpired(DeliveryRejected):
    """No POST was attempted: the current clock left this notification slot."""


def _safe_date(value):
    try:
        return value if isinstance(value, str) and date.fromisoformat(value).isoformat() == value else "unknown"
    except ValueError:
        return "unknown"


class PredictionNotReady(ValueError):
    """Safe structured diagnostics; never include source payloads or secrets."""

    def __init__(self, reason, manifest, lock, now):
        analysis = _safe_date(manifest.get("scan_date")) if isinstance(manifest, Mapping) else "unknown"
        forecast = next_scheduled_session(analysis) if analysis != "unknown" else None
        status = lock.get("status") if isinstance(lock, Mapping) else None
        self.details = {"reason": reason, "analysis_date": analysis,
                        "forecast_date": forecast.isoformat() if forecast else "unknown",
                        "today": now.astimezone(TPE).date().isoformat(),
                        "scan_status": status if status in ("completed", "running", "failed") else "unknown"}
        super().__init__(reason)


def _compact_rows(rows):
    keys = ("代號", "名稱", "Data_Date", "Revenue_Source", "Institutional_Source",
            "Execution_Versions", "Execution_Version_Label")
    return [{key: deepcopy(row.get(key)) for key in keys} for row in rows]


def _validate_watchlist(value, trading_date):
    if (not isinstance(value, Mapping) or value.get("schema") != WATCH_SCHEMA
            or value.get("trading_date") != trading_date):
        raise ValueError("預測追蹤快照格式或日期不符")
    next_day = next_scheduled_session(value.get("analysis_date"))
    rows = value.get("rows")
    if (next_day is None or next_day.isoformat() != trading_date or not isinstance(rows, list)
            or len(rows) > 20):
        raise ValueError("追蹤快照並非當天預測名單")
    seen = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("追蹤股票資料格式錯誤")
        ticker = normalize_ticker(row.get("代號"))
        if not ticker or ticker in seen or row.get("Data_Date") != value["analysis_date"]:
            raise ValueError("追蹤股票代碼重複或資料日期不符")
        seen.add(ticker)
    return deepcopy(dict(value))


def _prepare_watchlist(manifest, lock, now):
    if (not isinstance(manifest, Mapping) or not isinstance(lock, Mapping) or lock.get("status") != "completed"
            or lock.get("trading_date") != manifest.get("scan_date")):
        raise PredictionNotReady("scan_incomplete", manifest, lock, now)
    if next_scheduled_session(manifest.get("scan_date")) != now.astimezone(TPE).date():
        raise PredictionNotReady("prediction_date_mismatch", manifest, lock, now)
    rows = select_prediction_rows(manifest, now)
    value = {"schema": WATCH_SCHEMA, "trading_date": now.astimezone(TPE).date().isoformat(),
             "analysis_date": manifest["scan_date"], "rows": _compact_rows(rows),
             "frozen_at": now.isoformat()}
    return _validate_watchlist(value, value["trading_date"])


def load_watchlist(db, now, *, load_manifest, load_lock, persist):
    """First use freezes the exact image selector; subsequent hours never re-rank."""
    from firebase_admin import firestore

    day = now.astimezone(TPE).date().isoformat()
    ref = db.collection("prediction_watchlists").document(day)
    snap = ref.get()
    if snap.exists:
        return _validate_watchlist(snap.to_dict(), day)
    candidate = _prepare_watchlist(load_manifest(), load_lock(), now)
    if not persist:
        return candidate

    @firestore.transactional
    def freeze(transaction):
        current = ref.get(transaction=transaction)
        if current.exists:
            return _validate_watchlist(current.to_dict(), day)
        transaction.set(ref, candidate)
        return candidate

    return freeze(db.transaction())


def run_prediction_notifications(*, db, load_manifest, load_lock, send_text,
                                 load_quotes=fetch_prediction_quotes, clock=None, dry_run=True):
    """Send only the current slot, at most once; no historical hourly backfill."""
    clock = clock or (lambda: datetime.now(TPE))
    now = clock()
    slot = notification_slot(now)
    if slot is None:
        return {"status": "outside_session"}
    if db is None:
        raise RuntimeError("Firestore 未初始化，無法確認名單與通知狀態")
    now = now.astimezone(TPE)
    day = now.date().isoformat()
    watchlist = load_watchlist(db, now, load_manifest=load_manifest, load_lock=load_lock, persist=not dry_run)
    rows = watchlist["rows"]
    if not rows:
        return {"status": "empty_prediction", "trading_date": day, "slot": slot}

    def still_current():
        current = clock().astimezone(TPE)
        if current.date().isoformat() != day or notification_slot(current) != slot:
            raise SlotExpired("排程時段已過，略過舊時段通知")
        return current

    def build_messages():
        current = still_current()
        quotes = load_quotes(rows, now_tpe=current, closing=slot == "close")
        current = still_current()
        # Public quotes can lag the auction. Retry the same close slot rather
        # than relabel a 13:20 quote as the 13:30 closing price.
        all_final = all(isinstance(quotes.get(row["代號"]), Mapping)
                        and quotes[row["代號"]].get("status") == "ok"
                        and quotes[row["代號"]].get("final") is True for row in rows)
        if not dry_run and slot == "close" and not all_final and (current.hour, current.minute) < (14, 5):
            raise QuotesPending("當日收盤尚未全部確認，等待本時段備援")
        wait_until = 35 if slot == "0900" else 20
        if (not dry_run and slot != "close" and current.minute < wait_until
                and not any(isinstance(value, Mapping) and value.get("status") == "ok" for value in quotes.values())):
            raise QuotesPending("尚無當日有效行情，等待本時段備援")
        messages = format_prediction_messages(rows, quotes, analysis_date=watchlist["analysis_date"],
                                               trading_date=day, slot=slot, now=current)
        return messages, {"analysis_date": watchlist["analysis_date"], "trading_date": day,
                          "slot": slot, "stock_count": len(rows), "quoted_at": current.isoformat(),
                          "all_closes_confirmed": all_final if slot == "close" else None}

    if dry_run:
        messages, metadata = build_messages()
        return {"status": "preview", "messages": messages, "metadata": metadata}

    def guarded_send(message):
        still_current()
        return send_text(message)

    # One stable key per day/slot, independent of changing quotes, new scores,
    # source restatements, or the unrelated daily-research format version.
    fingerprint = hashlib.sha256(f"{WATCH_SCHEMA}|{day}|{slot}".encode()).hexdigest()
    # Longer than the 10-minute cloud job timeout, shorter than the 15-minute
    # backup interval. A killed pre-send fetch must not lock out the whole hour;
    # persisted in-flight/uncertain POSTs still block automatic retransmission.
    store = FirestoreReportStore(db, f"{day}_{slot}", namespace="prediction_prices",
                                 format_version=FORMAT_VERSION, lease_minutes=12)
    try:
        sent = deliver_report(store, [], day, build_messages=build_messages, send_text=guarded_send,
                              fingerprint=fingerprint)
    except QuotesPending:
        return {"status": "quotes_pending", "trading_date": day, "slot": slot}
    except SlotExpired:
        return {"status": "slot_expired", "trading_date": day, "slot": slot}
    return {"status": "sent" if sent else "already_sent_or_busy", "trading_date": day,
            "slot": slot, "stock_count": len(rows)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--send", action="store_true", help="Send the current slot; default is read-only preview")
    parser.add_argument("--scheduled", action="store_true", help="Local scheduled run; missed windows fail visibly")
    args = parser.parse_args()
    # A delayed scheduled job is not a successful delivery. Holidays and manual
    # previews still skip quietly; the workflow handles failure alerts separately.
    now = datetime.now(TPE)
    if notification_slot(now) is None:
        missed = (args.send and (args.scheduled or os.getenv("GITHUB_EVENT_NAME") == "schedule")
                  and is_scheduled_session(now.date()) is not False)
        if missed:
            try:
                scanner = import_module("scanner")
                completed = has_completed_due_notifications(scanner.db, now)
            except Exception:
                completed = False
            if completed:
                print(json.dumps({"status": "already_delivered", "checked_at": now.isoformat()}))
                return
            print(json.dumps({"status": "missed_window", "checked_at": now.isoformat()}))
            logging.error("行情排程到達時已不在可寄送時段，或交易日曆未確認；未補造過時行情")
            raise SystemExit(1)
        print(json.dumps({"status": "outside_session", "checked_at": now.isoformat()}))
        return
    scanner = import_module("scanner")

    def load_lock():
        snapshot = scanner.db.collection("system_locks").document("daily_scan").get()
        return snapshot.to_dict() if snapshot.exists else {}

    def sender(message):
        token, chat_id = scanner._telegram_credentials()
        return telegram_send_text(token, chat_id, message)

    try:
        result = run_prediction_notifications(db=scanner.db, load_manifest=scanner._load_daily_scan_doc,
                                              load_lock=load_lock, send_text=sender, dry_run=not args.send)
    except PredictionNotReady as exc:
        logging.error("行情通知名單未就緒：%s", json.dumps(exc.details, ensure_ascii=False))
        raise SystemExit(1) from None
    except Exception as exc:
        # A public-data/client exception must not expose credentials or URLs.
        logging.error("預測名單行情通知失敗（%s）；不自動強制重送", type(exc).__name__)
        raise SystemExit(1) from None
    print(json.dumps(result, ensure_ascii=False, default=str))
    if args.send and result["status"] == "slot_expired":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
