"""Pure helpers for daily-scan dates, idempotency, and data quality."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

GOOD_STATUSES = {"ok", "realtime", "confirmed"}
PARTIAL_STATUSES = {"partial", "estimated", "empty"}
MAX_FINAL_SCORE_GAIN = 9
DEFAULT_DAILY_SCAN_LIMIT = 300
DEFAULT_WEEKLY_SCAN_LIMIT = 500
TAIPEI_TIMEZONE = timezone(timedelta(hours=8))

DAILY_SCAN_STATUSES = {
    "running": ("執行中", "#FACC15"),
    "completed": ("完成", "#22C55E"),
    "failed": ("失敗", "#EF4444"),
}


def _date_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if hasattr(value, "to_pydatetime"):
        return value.to_pydatetime().date().isoformat()
    text = str(value).strip()
    return text[:10] if len(text) >= 10 else ""


def _valid_date_text(value: Any) -> str:
    text = _date_text(value)
    try:
        return date.fromisoformat(text).isoformat()
    except (TypeError, ValueError):
        return ""


def _taipei_datetime_text(value: Any) -> str:
    """Format Firestore/ISO timestamps in Taipei time without raising."""
    if value in (None, ""):
        return ""
    if hasattr(value, "to_pydatetime"):
        try:
            value = value.to_pydatetime()
        except (TypeError, ValueError, OverflowError):
            return ""
    if not isinstance(value, datetime):
        try:
            value = datetime.fromisoformat(str(value).strip())
        except (TypeError, ValueError, OverflowError):
            return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(TAIPEI_TIMEZONE).strftime("%Y-%m-%d %H:%M")


def safe_scan_error_summary(error: Any) -> str:
    """Return an allow-listed operational summary without exposing raw errors."""
    text = str(error or "").strip().lower()
    if not text:
        return "掃描執行失敗，請查看後端紀錄"

    categories = (
        (("credential", "permission", "unauthorized", "forbidden", "驗證", "權限"), "雲端服務驗證或權限異常"),
        (("firestore", "firebase", "google.api_core", "雲端"), "雲端服務暫時無法使用"),
        (("timeout", "timed out", "deadline", "逾時", "超時"), "外部資料來源回應逾時"),
        (("connection", "network", "dns", "連線", "網路"), "外部資料來源連線失敗"),
        (("rate limit", "too many requests", "429", "頻率", "限流"), "外部資料來源請求頻率受限"),
        (("僅取得", "不完整", "incomplete", "scan pool", "掃描池", "排行"), "掃描資料不完整"),
        (("empty", "為空", "無有效", "no result"), "掃描未產生有效結果"),
    )
    for keywords, summary in categories:
        if any(keyword in text for keyword in keywords):
            return summary
    return "掃描執行失敗，請查看後端紀錄"


def build_daily_scan_status(lock_doc: Mapping[str, Any] | None) -> dict[str, Any]:
    """Build a display-safe view model for ``system_locks/daily_scan``."""
    payload = dict(lock_doc or {}) if isinstance(lock_doc, Mapping) else {}
    raw_status = str(payload.get("status", "")).strip().lower()
    status_aliases = {"complete": "completed", "success": "completed", "error": "failed"}
    status_key = status_aliases.get(raw_status, raw_status)

    if status_key in DAILY_SCAN_STATUSES:
        status_label, status_color = DAILY_SCAN_STATUSES[status_key]
    elif payload:
        status_key, status_label, status_color = "unknown", "狀態不明", "#94A3B8"
    else:
        status_key, status_label, status_color = "missing", "尚無紀錄", "#94A3B8"

    try:
        result_count = int(payload["result_count"])
        if result_count < 0:
            result_count = None
    except (KeyError, TypeError, ValueError, OverflowError):
        result_count = None

    return {
        "status_key": status_key,
        "status_label": status_label,
        "status_color": status_color,
        "trading_date": _valid_date_text(payload.get("trading_date")) or "--",
        "result_count": result_count,
        "result_count_text": f"{result_count:,}" if result_count is not None else "--",
        "started_at": _taipei_datetime_text(payload.get("started_at")) or "--",
        "finished_at": _taipei_datetime_text(payload.get("finished_at")) or "--",
        "error_summary": safe_scan_error_summary(payload.get("error")) if status_key == "failed" else "",
    }


def latest_trading_date(index: Iterable[Any]) -> str:
    """Return the latest market date represented by an OHLCV index."""
    values = list(index)
    if not values:
        return ""
    return _date_text(values[-1])


def scan_universe_limit(
    trading_date: str,
    override: Any = None,
    *,
    daily_limit: int = DEFAULT_DAILY_SCAN_LIMIT,
    weekly_limit: int = DEFAULT_WEEKLY_SCAN_LIMIT,
) -> int:
    """Use the larger universe on Friday, with an explicit bounded override."""
    if override not in (None, ""):
        try:
            return max(1, min(1000, int(override)))
        except (TypeError, ValueError):
            pass
    try:
        scan_day = date.fromisoformat(str(trading_date)[:10])
    except ValueError:
        return int(daily_limit)
    return int(weekly_limit if scan_day.weekday() == 4 else daily_limit)


def previous_scan_state(
    previous_doc: Mapping[str, Any] | None,
    trading_date: str,
) -> tuple[dict[str, int], dict[str, int], bool]:
    """Preserve prior-day comparisons when a scan is rerun on the same day."""
    payload = dict(previous_doc or {})
    rows = payload.get("data", [])
    if not isinstance(rows, list):
        rows = []
    same_day = str(payload.get("scan_date", "")) == str(trading_date)

    streaks: dict[str, int] = {}
    ranks: dict[str, int] = {}
    for position, row in enumerate(rows, start=1):
        if not isinstance(row, Mapping):
            continue
        ticker = str(row.get("代號", "")).strip()
        if not ticker:
            continue
        try:
            streaks[ticker] = max(0, int(row.get("Streak", 0)))
        except (TypeError, ValueError):
            streaks[ticker] = 0

        rank_value = row.get("Prev_Rank", 999) if same_day else row.get("Rank", position)
        try:
            ranks[ticker] = int(rank_value)
        except (TypeError, ValueError):
            ranks[ticker] = 999
    return streaks, ranks, same_day


def next_streak(ticker: str, streaks: Mapping[str, int], same_day: bool) -> int:
    previous = max(0, int(streaks.get(str(ticker), 0)))
    if same_day and previous > 0:
        return previous
    return previous + 1


def should_complete_candidate(
    initial_score: int,
    advanced_pattern_signal: str = "",
    *,
    final_threshold: int = 45,
) -> bool:
    """Keep every candidate that can still reach the final-score boundary."""
    return str(advanced_pattern_signal) == "Buy" or int(initial_score) + MAX_FINAL_SCORE_GAIN >= final_threshold


def build_scan_quality(
    statuses: Mapping[str, str],
    *,
    institutional_days: int = 0,
) -> tuple[dict[str, str], int]:
    """Build a persisted quality map and a conservative confidence score."""
    quality = {str(key): str(value or "unknown").lower() for key, value in statuses.items()}
    quality["institutional"] = f"{institutional_days}d" if institutional_days > 0 else quality.get("institutional", "missing")

    penalty_units = 0.0
    for key, status in quality.items():
        if key == "institutional" and status.endswith("d"):
            continue
        if status in GOOD_STATUSES:
            continue
        penalty_units += 0.5 if status in PARTIAL_STATUSES else 1.0
    confidence = max(20, round(100 - penalty_units * 12))
    return quality, confidence
