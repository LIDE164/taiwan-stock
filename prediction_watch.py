"""Pure, date-bound hourly price notices for the existing prediction watchlist.

This module neither fetches quotes nor changes entry eligibility. A delayed or
unconfirmed quote is never promoted to a current trade or a closing price.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, time, timedelta, timezone
import math
from typing import Any

from app_security import normalize_ticker
from market_calendar import is_scheduled_session, next_scheduled_session
from ranking_comparison import build_comparison_rows

TPE = timezone(timedelta(hours=8))
MAX_MESSAGE_UNITS = 3500
SLOT_LABELS = {
    "0900": "09:00 開盤", "1000": "10時盤中更新", "1100": "11時盤中更新",
    "1200": "12時盤中更新", "1300": "13時盤中更新", "close": "13:30 收盤",
}


def _local_now(now: datetime) -> datetime:
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be an aware datetime")
    return now.astimezone(TPE)


def _date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.isoformat() == value else None


def notification_slot(now: datetime) -> str | None:
    """Return only the slot current at ``now``; never backfill a missed hour."""
    local = _local_now(now)
    if is_scheduled_session(local.date()) is not True:
        return None
    clock = local.time()
    if time(13, 35) <= clock < time(14, 30):
        return "close"
    if time(13, 5) <= clock < time(13, 35):
        return "1300"
    if 9 <= local.hour <= 12 and local.minute >= 5:
        return f"{local.hour:02d}00"
    return None


def select_prediction_rows(manifest: Mapping[str, Any], now: datetime) -> list[dict[str, Any]]:
    """Use the unchanged new/legacy union for this exact forecast trading day."""
    local = _local_now(now)
    if not isinstance(manifest, Mapping):
        raise ValueError("prediction manifest must be a mapping")
    analysis = _date(manifest.get("scan_date"))
    if (analysis is None or is_scheduled_session(analysis) is not True
            or is_scheduled_session(local.date()) is not True
            or next_scheduled_session(analysis) != local.date()):
        raise ValueError("prediction manifest does not apply to today's scheduled session")
    records = manifest.get("data")
    if not isinstance(records, list):
        raise ValueError("prediction records must be a list")
    if any(not isinstance(row, Mapping) or row.get("Data_Date") != analysis.isoformat()
           for row in records):
        raise ValueError("prediction records have missing or mixed analysis dates")
    return build_comparison_rows(records)


def _text(value: Any, limit: int) -> str:
    result = " ".join(str(value or "").split())
    return result if len(result) <= limit else result[:limit - 1] + "…"


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _price(value: float | None) -> str:
    if value is None:
        return "--"
    # Bound pathological inputs without truncating a long number into a new price.
    if value >= 1e9 or value < 0.0001:
        return f"{value:.6g}"
    return f"{value:,.4f}".rstrip("0").rstrip(".")


def _change(price: float | None, baseline: float | None) -> str:
    if price is None or baseline is None:
        return "--"
    change = (price / baseline - 1) * 100
    return f"{change:+.2f}%" if math.isfinite(change) and abs(change) < 1e9 else "--"


def _versions(row: Mapping[str, Any]) -> str:
    values = row.get("Execution_Versions")
    if not isinstance(values, (list, tuple)):
        return "制度未確認"
    labels = [label for key, label in (("new", "新制"), ("legacy", "舊制")) if key in values]
    return "＋".join(labels) or "制度未確認"


def _quote_values(quote: Any, day: date, now: datetime) -> tuple[dict[str, Any] | None, str]:
    if not isinstance(quote, Mapping) or quote.get("status") not in ("ok", "unavailable"):
        return None, "行情無法取得"
    if _date(quote.get("date")) != day:
        return None, "非當日行情，不採用"
    raw_time = quote.get("observed_at")
    try:
        observed = datetime.fromisoformat(raw_time) if isinstance(raw_time, str) else None
    except ValueError:
        observed = None
    if observed is None or observed.tzinfo is None or observed.utcoffset() is None:
        return None, "行情時間未確認"
    observed = observed.astimezone(TPE)
    if observed.date() != day or observed > now:
        return None, "行情日期／時間不符，不採用"
    if not time(9) <= observed.time() < time(13, 31):
        return None, "非一般交易時段行情，不採用"
    price = _number(quote.get("price")) if quote.get("status") == "ok" else None
    previous = _number(quote.get("previous_close"))
    if "previous_close_date" in quote:
        previous_date = _date(quote.get("previous_close_date"))
        if (previous_date is None or is_scheduled_session(previous_date) is not True
                or next_scheduled_session(previous_date) != day):
            previous = None
    reason = "" if price is not None else (
        "行情無法取得" if quote.get("status") == "unavailable" else "當日成交價未確認"
    )
    return {
        "price": price, "open": _number(quote.get("open")),
        "previous_close": previous,
        "observed": observed, "source": _text(quote.get("source"), 36) or "來源未提供",
        "final": price is not None and quote.get("final") is True and observed.time() >= time(13, 30),
        "delayed": now - observed > timedelta(minutes=20),
    }, reason


def _stock_lines(row: Mapping[str, Any], quote: Any, day: date, slot: str, now: datetime) -> str:
    ticker = normalize_ticker(row.get("代號"))
    if not ticker:
        raise ValueError("prediction row has invalid ticker")
    title = f"{ticker} {_text(row.get('名稱'), 28) or '名稱未提供'}｜{_versions(row)}"
    values, reason = _quote_values(quote, day, now)
    if values is None:
        label = "收盤待確認" if slot == "close" else "現價"
        return f"{title}\n開盤 --｜{label} --\n較開盤 --｜較昨收 --\n{reason}；不以預測價代替。"
    price, opening, previous = values["price"], values["open"], values["previous_close"]
    if slot == "close":
        label = "收盤" if values["final"] else (
            "收盤待確認（最新已知價）" if price is not None else "收盤待確認"
        )
    else:
        label = "現價"
    if values["final"]:
        state = "當日收盤已確認"
    elif price is None:
        state = reason + ("；僅保留已驗證開盤價" if opening is not None else "；不以預測價代替")
    else:
        state = "延遲／舊報價" if values["delayed"] else "最新取得報價，非逐筆即時"
    return (
        f"{title}\n開盤 {_price(opening)}｜{label} {_price(price)}\n"
        f"較開盤 {_change(price, opening)}｜較昨收 {_change(price, previous)}\n"
        f"行情 {values['observed']:%H:%M:%S}｜{values['source']}｜{state}"
    )


def format_prediction_messages(
    rows: Sequence[Mapping[str, Any]], quotes: Mapping[str, Any], *,
    analysis_date: str, trading_date: str, slot: str, now: datetime,
) -> list[str]:
    """Build bounded, self-contained notices, preserving every watchlist stock."""
    local = _local_now(now)
    analysis, trading = _date(analysis_date), _date(trading_date)
    if (analysis is None or trading is None or is_scheduled_session(analysis) is not True
            or trading != local.date() or next_scheduled_session(analysis) != trading
            or slot not in SLOT_LABELS or notification_slot(local) != slot):
        raise ValueError("notice dates or slot do not match the current scheduled session")
    if (not isinstance(rows, (list, tuple)) or not isinstance(quotes, Mapping)
            or any(not isinstance(row, Mapping) or row.get("Data_Date") != analysis_date for row in rows)):
        raise ValueError("notice rows/quotes are incomplete or have mixed dates")
    header = (
        f"預測名單股價追蹤｜{SLOT_LABELS[slot]}\n"
        f"來源：{analysis_date} 盤後預測名單｜適用：{trading_date}\n"
        f"取得時間：{local:%Y-%m-%d %H:%M:%S}（台北）；行情時間逐檔列示"
    )
    footer = "原榜單交易資格及風控不變；股價通知不是進場訊號，不自動下單。"
    if not rows:
        return [f"{header}\n\n本日沒有預測名單股票，不另補股票。\n\n{footer}"]
    messages: list[str] = []
    blocks: list[str] = []
    for row in rows:
        ticker = normalize_ticker(row.get("代號"))
        block = _stock_lines(row, quotes.get(ticker), trading, slot, local)
        candidate = f"{header}\n\n" + "\n\n".join([*blocks, block]) + f"\n\n{footer}"
        if blocks and (len(blocks) == 10 or len(candidate.encode("utf-16-le")) // 2 >= MAX_MESSAGE_UNITS):
            messages.append(f"{header}\n\n" + "\n\n".join(blocks) + f"\n\n{footer}")
            blocks = []
        blocks.append(block)
    messages.append(f"{header}\n\n" + "\n\n".join(blocks) + f"\n\n{footer}")
    if any(len(message.encode("utf-16-le")) // 2 >= MAX_MESSAGE_UNITS for message in messages):
        raise ValueError("notice exceeds Telegram message size limit")
    return messages
