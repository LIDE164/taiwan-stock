"""Render the daily Top-10 ranking as a mobile-friendly PNG and send it to Telegram."""

from __future__ import annotations

import io
import math
import os
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from functools import lru_cache
from typing import Any

import requests
from PIL import Image, ImageDraw, ImageFont

from entry_readiness import build_entry_summary


IMAGE_WIDTH = 1080
IMAGE_HEIGHT = 1400
EXECUTABLE_IMAGE_HEIGHT = 1800
EXECUTABLE_CARD_HEIGHT = 140
EXECUTABLE_CARD_GAP = 8
CARD_LEFT = 42
CARD_WIDTH = 996
CARD_HEIGHT = 100
CARD_GAP = 10
PER_TRADE_MAX_LOSS = 5000.0
TRACKING_PERFORMANCE_START_DATE = "2026-08-27"
TRACKING_PERFORMANCE_FIRST_DATE = "2026-08-28"


def _number(value: Any) -> float | None:
    try:
        parsed = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _clean_text(value: Any, fallback: str = "--") -> str:
    text = " ".join(str(value or "").split()).strip()
    return text or fallback


def prediction_title(analysis_date: Any) -> str:
    """Format the next weekday after an ISO analysis date as an M/D prediction title."""
    try:
        parsed = date.fromisoformat(str(analysis_date).strip()[:10])
    except (TypeError, ValueError):
        return "下一交易日股票預測"
    prediction_date = parsed + timedelta(days=1)
    while prediction_date.weekday() >= 5:
        prediction_date += timedelta(days=1)
    return f"{prediction_date.month}/{prediction_date.day}股票預測"


def _credibility(sample_count: int | None) -> tuple[str, str]:
    if sample_count is None:
        return "資料缺失", "#94A3B8"
    if sample_count < 10:
        return "樣本嚴重不足", "#F87171"
    if sample_count < 30:
        return "僅供參考", "#FACC15"
    if sample_count < 50:
        return "中等可信", "#60A5FA"
    return "統計較穩定", "#4ADE80"


def _normalize_mini_kbars(value: Any, limit: int = 30) -> list[dict[str, float]]:
    """Keep only complete, internally consistent OHLC bars; never synthesize candles."""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    bars: list[dict[str, float]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        open_price = _number(item.get("open"))
        high_price = _number(item.get("high"))
        low_price = _number(item.get("low"))
        close_price = _number(item.get("close"))
        if any(number is None or number <= 0 for number in (open_price, high_price, low_price, close_price)):
            continue
        if high_price < max(open_price, close_price) or low_price > min(open_price, close_price):
            continue
        bars.append({
            "open": open_price,
            "high": high_price,
            "low": low_price,
            "close": close_price,
        })
    return bars[-max(1, int(limit)):]


def build_top10_display_rows(results: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Create truthful display values; missing backtests never become a 0% win rate."""
    rows: list[dict[str, Any]] = []
    for fallback_rank, record in enumerate(results[:10], start=1):
        rank_number = _number(record.get("Rank"))
        rank = int(rank_number) if rank_number is not None and rank_number > 0 else fallback_rank
        ticker = _clean_text(record.get("代號"))
        name = _clean_text(record.get("名稱"), ticker)
        score = _number(record.get("Score"))
        close = _number(record.get("收盤價"))
        change = _number(record.get("漲跌幅"))
        samples_number = _number(record.get("Backtest_Samples"))
        samples = int(samples_number) if samples_number is not None and samples_number >= 0 else None
        win_rate = _number(record.get("WinRate"))
        if samples is None or samples <= 0 or win_rate is None or not 0 <= win_rate <= 100:
            win_rate = None
        credibility, credibility_color = _credibility(samples)
        rating = _clean_text(record.get("評級"), "觀察")
        for marker in ("🟢", "🟡", "⚪", "🔴"):
            rating = rating.replace(marker, "").strip()
        rows.append({
            "rank": rank,
            "ticker": ticker,
            "name": name,
            "industry": _clean_text(record.get("產業"), "未分類"),
            "score_text": "--" if score is None else f"{score:g} 分",
            "rating": rating,
            "close_text": "--" if close is None else f"{close:g}",
            "change_text": "--" if change is None else f"{change:+.1f}%",
            "change_value": change,
            "win_rate_text": "--" if win_rate is None else f"{win_rate:.1f}%",
            "sample_text": "--" if samples is None else str(samples),
            "credibility": credibility,
            "credibility_color": credibility_color,
            "entry_status": _clean_text(record.get("Entry_Status"), "條件未提供"),
        })
    return rows


def _position_size_for_max_loss(
    entry_price: float | None,
    stop_price: float | None,
    max_loss: float,
) -> tuple[int, float | None, float | None]:
    """Size one odd-lot position so its price loss at the stop stays within max_loss."""
    if entry_price is None or stop_price is None or max_loss <= 0:
        return 0, None, None
    risk_per_share = entry_price - stop_price
    if entry_price <= 0 or stop_price <= 0 or risk_per_share <= 0:
        return 0, None, None
    shares = math.floor(max_loss / risk_per_share)
    if shares <= 0:
        return 0, risk_per_share, 0.0
    return shares, risk_per_share, shares * risk_per_share


def build_executable_display_rows(
    results: Sequence[Mapping[str, Any]],
    max_loss_per_trade: float = PER_TRADE_MAX_LOSS,
) -> list[dict[str, Any]]:
    """Return executable records sized to a maximum NT$ loss for each individual trade."""
    executable = [
        record for record in results
        if str(record.get("Entry_Status") or "").strip() == "現在可執行"
    ][:10]
    rows: list[dict[str, Any]] = []
    for display_rank, record in enumerate(executable, start=1):
        score = _number(record.get("Score"))
        close = _number(record.get("收盤價"))
        change = _number(record.get("漲跌幅"))
        low = _number(record.get("Entry_Low"))
        high = _number(record.get("Entry_High"))
        stop = _number(record.get("Entry_Stop"))
        target = _number(record.get("Entry_Target"))
        samples_number = _number(record.get("Backtest_Samples"))
        samples = int(samples_number) if samples_number is not None and samples_number >= 0 else None
        win_rate = _number(record.get("WinRate"))
        if samples is None or samples <= 0 or win_rate is None or not 0 <= win_rate <= 100:
            win_rate = None
        credibility, credibility_color = _credibility(samples)
        shares, risk_per_share, estimated_loss = _position_size_for_max_loss(
            close,
            stop,
            max_loss_per_trade,
        )
        rows.append({
            "display_rank": display_rank,
            "ticker": _clean_text(record.get("代號")),
            "name": _clean_text(record.get("名稱"), _clean_text(record.get("代號"))),
            "industry": _clean_text(record.get("產業"), "未分類"),
            "score_text": "--" if score is None else f"{score:g} 分",
            "close_value": close,
            "close_change_text": (
                "--" if close is None else f"{close:g}"
            ) + (
                " / --" if change is None else f" / {change:+.1f}%"
            ),
            "change_value": change,
            "entry_zone_text": (
                f"{low:g}–{high:g}"
                if low is not None and high is not None and low > 0 and high >= low
                else "--"
            ),
            "stop_text": "--" if stop is None or stop <= 0 else f"{stop:g}",
            "target_text": "--" if target is None or target <= 0 else f"{target:g}",
            "suggested_shares": shares,
            "suggested_shares_text": f"{shares:,} 股" if shares > 0 else "無法計算",
            "risk_per_share": risk_per_share,
            "risk_per_share_text": "--" if risk_per_share is None else f"${risk_per_share:,.2f}",
            "estimated_loss": estimated_loss,
            "estimated_loss_text": "--" if estimated_loss is None else f"${estimated_loss:,.0f}",
            "win_rate_text": "--" if win_rate is None else f"{win_rate:.1f}%",
            "sample_credibility_text": (
                "資料缺失" if samples is None else f"樣本 {samples}｜{credibility}"
            ),
            "credibility_color": credibility_color,
            "analysis": build_entry_summary(record),
            "mini_kbars": _normalize_mini_kbars(record.get("Mini_K")),
        })
    return rows


def _concentration_text(rows: Sequence[Mapping[str, Any]]) -> str:
    counts: dict[str, int] = {}
    for row in rows:
        industry = _clean_text(row.get("industry"), "")
        if industry:
            counts[industry] = counts.get(industry, 0) + 1
    if not counts:
        return ""
    industry, count = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0]
    share = count / len(rows) * 100 if rows else 0
    if count < 3 or share < 50:
        return ""
    return f"產業集中提醒：{industry} {count}/{len(rows)}（{share:.0f}%）"


def _mean(values: Sequence[float]) -> float | None:
    return (sum(values) / len(values)) if values else None


def _percent_text(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "--"
    return f"{value:+.{digits}f}%"


def _price_change_text(value: float | None) -> str:
    if value is None:
        return "--"
    if abs(value) < 0.005:
        return "0"
    return f"{value:+.2f}".rstrip("0").rstrip(".")


def _money_text(value: float | None) -> str:
    if value is None:
        return "--"
    return f"NT${value:+,.0f}"


_TRACKING_IMMUTABLE_FALLBACK_FIELDS = (
    "signal_date",
    "execution_schema",
    "entry_plan_type",
    "planned_entry_low",
    "planned_entry_high",
    "stop_price",
    "target_price",
    "max_risk_amount",
    "signal_score",
    "signal_rank",
    "signal_change_pct",
    "signal_industry",
    "signal_rsi",
    "signal_bias",
    "signal_volume_ratio",
    "signal_conflict",
    "signal_pattern",
    "signal_confidence",
    "entry_win_rate",
    "entry_backtest_samples",
    "entry_backtest_scope",
    "entry_backtest_status",
)


def _tracking_identity(row: Mapping[str, Any]) -> str:
    existing = str(row.get("position_id") or "").strip()
    if existing:
        return existing
    ticker = str(row.get("ticker") or "").strip()
    origin_date = str(row.get("signal_date") or row.get("entry_date") or "").strip()
    return f"{ticker}:{origin_date}"


def _tracking_action_label(row: Mapping[str, Any]) -> str:
    action = str(row.get("action") or "")
    if action == "ENTRY":
        schema = _number(row.get("execution_schema")) or 1
        return "區間成交" if schema >= 2 else "收盤進場（舊）"
    return {
        "SIGNAL": "待次日觸價",
        "ENTRY_EXPIRED": "進場訊號失效",
        "HOLD": "持有",
        "TAKE_PROFIT": "停利",
        "STOP_LOSS": "停損",
        "DATA_MISSING": "行情缺漏",
        "EXIT": "已出場",
    }.get(action, _clean_text(action))


def _tracking_as_of_status(row: Mapping[str, Any]) -> str:
    """Recover legacy daily status only from that day's recorded action."""
    status = str(row.get("status") or "").strip()
    if status:
        return status
    return {
        "ENTRY": "OPEN",
        "HOLD": "OPEN",
        "TAKE_PROFIT": "CLOSED_TP",
        "STOP_LOSS": "CLOSED_SL",
        "SIGNAL": "PENDING",
        "ENTRY_EXPIRED": "EXPIRED",
    }.get(str(row.get("action") or ""), "")


def _decline_category(value: Any) -> str:
    text = _clean_text(value, "")
    if not text:
        return ""
    for needle, label in (
        ("觸發策略停損", "策略停損"),
        ("跳空走弱", "跳空走弱"),
        ("盤中賣壓", "盤中賣壓"),
        ("大盤同步", "大盤同步"),
        ("弱於大盤", "弱於大盤"),
        ("入榜日已漲", "入榜日偏強"),
    ):
        if needle in text:
            return label
    return "一般走弱"


def build_tracking_performance_report(
    records: Sequence[Mapping[str, Any]],
    positions: Sequence[Mapping[str, Any]],
    trading_date: str,
) -> dict[str, Any]:
    """Build a date-faithful report from the supplied daily tracker records.

    Current positions are deliberately not a source of status, prices, or P/L: a
    later stop must never rewrite an older report.  They may only repair missing
    immutable signal-time metadata in legacy daily rows.
    """
    date_text = str(trading_date)

    def in_tracking_window(row: Mapping[str, Any]) -> bool:
        origin_date = str(row.get("signal_date") or row.get("entry_date") or "").strip()
        record_date = str(row.get("date") or date_text).strip()
        return (
            TRACKING_PERFORMANCE_START_DATE <= origin_date <= date_text
            and record_date <= date_text
        )

    position_metadata = {
        _tracking_identity(row): dict(row)
        for row in positions
        if isinstance(row, Mapping) and _tracking_identity(row) != ":"
    }

    candidate_records: list[dict[str, Any]] = []
    for source in records:
        if not isinstance(source, Mapping) or not in_tracking_window(source):
            continue
        row = dict(source)
        fallback = position_metadata.get(_tracking_identity(row), {})
        for key in _TRACKING_IMMUTABLE_FALLBACK_FIELDS:
            if row.get(key) in (None, "") and fallback.get(key) not in (None, ""):
                row[key] = fallback[key]
        candidate_records.append(row)

    daily_records = [
        row
        for row in candidate_records
        if str(row.get("entry_date") or "") < date_text
        and str(row.get("action") or "") not in {"SIGNAL", "ENTRY_EXPIRED"}
        and _tracking_as_of_status(row) not in {"PENDING", "EXPIRED"}
        and str(row.get("data_status") or "") == "ok"
        and _number(row.get("daily_return_pct")) is not None
        and _number(row.get("pnl_pct")) is not None
    ]
    valid_daily_returns = [
        value
        for row in daily_records
        if str(row.get("data_status") or "") == "ok"
        and (value := _number(row.get("daily_return_pct"))) is not None
    ]
    open_positions = [row for row in daily_records if _tracking_as_of_status(row) == "OPEN"]
    open_returns = [
        value
        for row in open_positions
        if (value := _number(row.get("pnl_pct"))) is not None
    ]
    closed_positions = [
        row for row in daily_records
        if _tracking_as_of_status(row) in {"CLOSED_TP", "CLOSED_SL"}
    ]
    closed_returns = [
        value
        for row in closed_positions
        if (value := _number(row.get("pnl_pct"))) is not None
    ]
    closed_wins = sum(value > 0 for value in closed_returns)
    display_rows: list[dict[str, Any]] = []
    for row in daily_records:
        ticker = _clean_text(row.get("ticker"))
        if ticker == "--":
            continue
        pnl = _number(row.get("pnl_pct"))
        daily_return = (
            _number(row.get("daily_return_pct"))
            if str(row.get("data_status") or "") == "ok"
            else None
        )
        entry = _number(row.get("entry_price"))
        mark = _number(row.get("mark_price")) if str(row.get("data_status") or "") == "ok" else None
        daily_price_change = _number(row.get("daily_price_change"))
        previous_mark = _number(row.get("previous_mark_price"))
        if daily_price_change is None and mark is not None and previous_mark is not None:
            daily_price_change = mark - previous_mark
        if (
            daily_price_change is None
            and mark is not None
            and entry is not None
            and str(row.get("entry_date") or "") == TRACKING_PERFORMANCE_START_DATE
            and date_text == TRACKING_PERFORMANCE_FIRST_DATE
        ):
            daily_price_change = mark - entry
        if daily_price_change is None and mark is not None and daily_return is not None:
            previous_ratio = 1 + daily_return / 100
            if previous_ratio > 0:
                daily_price_change = mark - (mark / previous_ratio)
        holding_price_change = (
            mark - entry
            if mark is not None and entry is not None
            else None
        )
        sample_number = _number(row.get("entry_backtest_samples"))
        samples = int(sample_number) if sample_number is not None and sample_number >= 0 else None
        win_rate = _number(row.get("entry_win_rate"))
        if samples is None or samples <= 0 or win_rate is None or not 0 <= win_rate <= 100:
            win_rate = None
        credibility, credibility_color = _credibility(samples)
        diagnostic = _clean_text(row.get("decline_diagnostic"), "")
        net_pnl = _number(row.get("net_pnl_amount"))
        estimated_cost = _number(row.get("estimated_transaction_cost"))
        benchmark_return = _number(row.get("benchmark_return_pct"))
        excess_return = _number(row.get("excess_return_pct"))
        display_rows.append({
            "ticker": ticker,
            "name": _clean_text(row.get("name"), ticker),
            "entry_date": _clean_text(row.get("entry_date")),
            "action": _tracking_action_label(row),
            "status": _tracking_as_of_status(row),
            "entry_text": "--" if entry is None else f"{entry:g}",
            "mark_text": "--" if mark is None else f"{mark:g}",
            "daily_return": daily_return,
            "daily_return_text": _percent_text(daily_return),
            "daily_price_change": daily_price_change,
            "daily_price_change_text": _price_change_text(daily_price_change),
            "pnl": pnl,
            "pnl_text": _percent_text(pnl),
            "holding_price_change": holding_price_change,
            "holding_price_change_text": _price_change_text(holding_price_change),
            "backtest_text": (
                "--"
                if win_rate is None
                else f"{win_rate:.1f}% / {samples}"
            ),
            "credibility": credibility,
            "credibility_color": credibility_color,
            "decline_diagnostic": diagnostic or None,
            "net_pnl_amount": net_pnl,
            "net_pnl_text": _money_text(net_pnl),
            "estimated_transaction_cost": estimated_cost,
            "estimated_cost_text": _money_text(estimated_cost),
            "benchmark_return": benchmark_return,
            "excess_return": excess_return,
            "signal_industry": _clean_text(row.get("signal_industry"), "") or None,
            "execution_schema": int(_number(row.get("execution_schema")) or 1),
            "stop_price": _number(row.get("stop_price")),
            "target_price": _number(row.get("target_price")),
            "data_status": str(row.get("data_status") or "missing"),
        })

    display_rows.sort(
        key=lambda row: row["pnl"] if row["pnl"] is not None else float("-inf"),
        reverse=True,
    )
    display_mode = "已成交且已有跨日損益的全部標的"

    missing_count = sum(
        str(row.get("entry_date") or "") < date_text
        and str(row.get("data_status") or "") != "ok"
        and _tracking_as_of_status(row) not in {"PENDING", "EXPIRED"}
        for row in candidate_records
    )
    excluded_count = len(candidate_records) - len(daily_records)
    actions: dict[str, int] = {}
    for row in candidate_records:
        action = str(row.get("action") or "UNKNOWN")
        actions[action] = actions.get(action, 0) + 1

    industry_counts: dict[str, int] = {}
    for row in daily_records:
        industry = _clean_text(row.get("signal_industry"), "")
        if industry:
            industry_counts[industry] = industry_counts.get(industry, 0) + 1
    industry_sample_count = sum(industry_counts.values())
    largest_industry = None
    largest_industry_count = 0
    if industry_counts:
        largest_industry, largest_industry_count = sorted(
            industry_counts.items(), key=lambda item: (-item[1], item[0])
        )[0]
    largest_industry_share = (
        largest_industry_count / industry_sample_count * 100
        if largest_industry and industry_sample_count
        else None
    )

    benchmark_returns = [
        value for row in daily_records
        if (value := _number(row.get("benchmark_return_pct"))) is not None
    ]
    excess_returns = [
        value for row in daily_records
        if (value := _number(row.get("excess_return_pct"))) is not None
    ]
    strategy_count = sum(int(_number(row.get("execution_schema")) or 1) >= 2 for row in daily_records)
    legacy_count = len(daily_records) - strategy_count
    decline_counts: dict[str, int] = {}
    losing_count = 0
    hot_entry_losing_count = 0
    for row in daily_records:
        daily_return = _number(row.get("daily_return_pct"))
        if daily_return is None or daily_return >= 0:
            continue
        losing_count += 1
        signal_change = _number(row.get("signal_change_pct"))
        if signal_change is not None and signal_change >= 3:
            hot_entry_losing_count += 1
        category = _decline_category(row.get("decline_diagnostic"))
        if category:
            decline_counts[category] = decline_counts.get(category, 0) + 1
    leading_decline_category = None
    leading_decline_count = 0
    if decline_counts:
        leading_decline_category, leading_decline_count = sorted(
            decline_counts.items(), key=lambda item: (-item[1], item[0])
        )[0]
    realized_win_rate = (
        closed_wins / len(closed_returns) * 100 if closed_returns else None
    )
    return {
        "date": str(trading_date),
        "start_date": TRACKING_PERFORMANCE_START_DATE,
        "tracked_count": len(daily_records),
        "valid_count": len(daily_records),
        "missing_count": missing_count,
        "excluded_count": excluded_count,
        "daily_average": _mean(valid_daily_returns),
        "daily_positive_count": sum(value > 0 for value in valid_daily_returns),
        "daily_sample_count": len(valid_daily_returns),
        "open_count": len(open_positions),
        "open_average": _mean(open_returns),
        "closed_count": len(closed_positions),
        "realized_win_rate": realized_win_rate,
        "realized_average": _mean(closed_returns),
        "actions": actions,
        "pending_count": sum(
            _tracking_as_of_status(row) == "PENDING" for row in candidate_records
        ),
        "expired_count": sum(
            _tracking_as_of_status(row) == "EXPIRED" for row in candidate_records
        ),
        "strategy_count": strategy_count,
        "legacy_count": legacy_count,
        "industry_sample_count": industry_sample_count,
        "largest_industry": largest_industry,
        "largest_industry_count": largest_industry_count,
        "largest_industry_share": largest_industry_share,
        "benchmark_average": _mean(benchmark_returns),
        "benchmark_sample_count": len(benchmark_returns),
        "excess_average": _mean(excess_returns),
        "excess_sample_count": len(excess_returns),
        "losing_count": losing_count,
        "decline_categories": decline_counts,
        "leading_decline_category": leading_decline_category,
        "leading_decline_count": leading_decline_count,
        "hot_entry_losing_count": hot_entry_losing_count,
        "rows": display_rows,
        "display_mode": display_mode,
        "page_count": max(1, math.ceil(len(display_rows) / 10)),
    }


def _font_candidates(bold: bool) -> tuple[str, ...]:
    configured = os.getenv("TOP10_FONT_PATH", "").strip()
    common = (
        "C:/Windows/Fonts/msjhbd.ttc" if bold else "C:/Windows/Fonts/msjh.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc" if bold else
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/System/Library/Fonts/PingFang.ttc",
    )
    return ((configured,) if configured else ()) + common


@lru_cache(maxsize=32)
def _font(size: int, bold: bool = False):
    for candidate in _font_candidates(bold):
        if candidate and os.path.exists(candidate):
            try:
                return ImageFont.truetype(candidate, size=size)
            except OSError:
                continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _fit_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    value = _clean_text(text)
    if draw.textlength(value, font=font) <= max_width:
        return value
    suffix = "…"
    while value and draw.textlength(value + suffix, font=font) > max_width:
        value = value[:-1]
    return (value + suffix) if value else suffix


def _rank_colors(rank: int) -> tuple[str, str]:
    if rank == 1:
        return "#F59E0B", "#1F2937"
    if rank == 2:
        return "#CBD5E1", "#1F2937"
    if rank == 3:
        return "#D97706", "#FFF7ED"
    return "#334155", "#E2E8F0"


def _draw_mini_candles(
    draw: ImageDraw.ImageDraw,
    bars: Sequence[Mapping[str, float]],
    box: tuple[int, int, int, int],
    current_price: float | None = None,
) -> None:
    """Draw a truthful compact OHLC chart, or an explicit missing-data state."""
    left, top, right, bottom = box
    draw.rounded_rectangle(box, radius=10, fill="#0A1222", outline="#263449", width=1)
    if not bars:
        draw.text((left + 9, top + 5), "近30日 K", font=_font(11, True), fill="#64748B")
        draw.text(
            ((left + right) // 2, (top + bottom) // 2 + 7),
            "K線資料不足",
            font=_font(13, True),
            fill="#94A3B8",
            anchor="mm",
        )
        return

    lows = [float(bar["low"]) for bar in bars]
    highs = [float(bar["high"]) for bar in bars]
    low_price, high_price = min(lows), max(highs)
    high_level = f"{high_price:.2f}".rstrip("0").rstrip(".")
    low_level = f"{low_price:.2f}".rstrip("0").rstrip(".")
    period_label = "近30日 K" if len(bars) >= 30 else f"近{len(bars)}/30日 K"
    draw.text((left + 9, top + 4), period_label, font=_font(10, True), fill="#64748B")
    if current_price is not None and math.isfinite(current_price) and current_price > 0:
        current_level = f"{current_price:.2f}".rstrip("0").rstrip(".")
        draw.text(
            ((left + right) // 2, top + 3),
            f"現價 {current_level}",
            font=_font(10, True),
            fill="#60A5FA",
            anchor="ma",
        )
    draw.text((right - 9, top + 3), f"最高 {high_level}", font=_font(10, True), fill="#F87171", anchor="ra")
    draw.text((right - 9, bottom - 15), f"最低 {low_level}", font=_font(10, True), fill="#4ADE80", anchor="ra")

    chart_left, chart_top = left + 9, top + 20
    chart_right, chart_bottom = right - 9, bottom - 18
    span = high_price - low_price
    if span <= 0:
        span = max(high_price * 0.01, 0.01)
        low_price -= span / 2
        high_price += span / 2

    def y(price: float) -> int:
        ratio = (high_price - price) / (high_price - low_price)
        return int(round(chart_top + ratio * (chart_bottom - chart_top)))

    current_y: int | None = None
    if current_price is not None and math.isfinite(current_price) and current_price > 0:
        current_y = max(chart_top, min(y(current_price), chart_bottom))
        for segment_left in range(chart_left, chart_right, 10):
            draw.line(
                (segment_left, current_y, min(segment_left + 5, chart_right), current_y),
                fill="#2563EB",
                width=1,
            )

    slot = (chart_right - chart_left) / max(len(bars), 1)
    body_half_width = max(1, min(4, int(slot * 0.28)))
    centers: list[int] = []
    for index, bar in enumerate(bars):
        center_x = int(round(chart_left + slot * (index + 0.5)))
        centers.append(center_x)
        open_price = float(bar["open"])
        close_price = float(bar["close"])
        color = "#F87171" if close_price > open_price else ("#4ADE80" if close_price < open_price else "#CBD5E1")
        draw.line((center_x, y(float(bar["high"])), center_x, y(float(bar["low"]))), fill=color, width=1)
        body_top, body_bottom = sorted((y(open_price), y(close_price)))
        if body_top == body_bottom:
            draw.line((center_x - body_half_width, body_top, center_x + body_half_width, body_top), fill=color, width=2)
        else:
            draw.rectangle(
                (center_x - body_half_width, body_top, center_x + body_half_width, body_bottom),
                fill=color,
            )

    high_index = max(range(len(bars)), key=lambda index: float(bars[index]["high"]))
    low_index = min(range(len(bars)), key=lambda index: float(bars[index]["low"]))
    high_point = (centers[high_index], y(highs[high_index]))
    low_point = (centers[low_index], y(lows[low_index]))
    draw.line((chart_left, chart_top, chart_right, chart_top), fill="#4B2530", width=1)
    draw.line((chart_left, chart_bottom, chart_right, chart_bottom), fill="#164E3B", width=1)
    draw.ellipse((high_point[0] - 3, high_point[1] - 3, high_point[0] + 3, high_point[1] + 3), fill="#F87171")
    draw.ellipse((low_point[0] - 3, low_point[1] - 3, low_point[0] + 3, low_point[1] + 3), fill="#4ADE80")


def render_top10_image(results: Sequence[Mapping[str, Any]], trading_date: str) -> bytes:
    """Return a PNG report containing at most ten executable ranking rows."""
    rows = build_top10_display_rows(results)

    image = Image.new("RGB", (IMAGE_WIDTH, IMAGE_HEIGHT), "#070D1A")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((30, 26, IMAGE_WIDTH - 30, 145), radius=28, fill="#0F172A", outline="#1E293B", width=2)
    draw.text((62, 48), "TAIWAN STOCK RADAR", font=_font(18, True), fill="#60A5FA")
    draw.text((62, 76), "每日可執行 Top 10", font=_font(39, True), fill="#F8FAFC")
    draw.text((IMAGE_WIDTH - 62, 53), _clean_text(trading_date), font=_font(24, True), fill="#FBBF24", anchor="ra")
    draw.text((IMAGE_WIDTH - 62, 91), f"現在可執行 {len(rows)} 檔｜依量化分數排序", font=_font(19), fill="#94A3B8", anchor="ra")
    concentration_text = _concentration_text(rows)
    if concentration_text:
        draw.text((IMAGE_WIDTH - 62, 119), concentration_text, font=_font(14, True), fill="#FBBF24", anchor="ra")

    start_y = 168
    if not rows:
        draw.rounded_rectangle((70, 245, IMAGE_WIDTH - 70, 950), radius=36, fill="#0F172A", outline="#1E293B", width=2)
        draw.text((IMAGE_WIDTH // 2, 485), "今日沒有符合", font=_font(32, True), fill="#94A3B8", anchor="mm")
        draw.text((IMAGE_WIDTH // 2, 555), "「現在可執行」條件的股票", font=_font(42, True), fill="#F8FAFC", anchor="mm")
        draw.text((IMAGE_WIDTH // 2, 645), "不會使用等待拉回、等待量能或條件不足的股票補滿 Top 10", font=_font(20), fill="#64748B", anchor="mm")
    else:
        for index, row in enumerate(rows):
            top = start_y + index * (CARD_HEIGHT + CARD_GAP)
            bottom = top + CARD_HEIGHT
            draw.rounded_rectangle(
                (CARD_LEFT, top, CARD_LEFT + CARD_WIDTH, bottom),
                radius=18,
                fill="#0F172A",
                outline="#1E293B",
                width=2,
            )
            badge_fill, badge_text = _rank_colors(row["rank"])
            draw.ellipse((58, top + 24, 108, top + 74), fill=badge_fill)
            draw.text((83, top + 49), str(row["rank"]), font=_font(23, True), fill=badge_text, anchor="mm")

            stock_text = _fit_text(draw, f"{row['ticker']}  {row['name']}", _font(27, True), 330)
            draw.text((128, top + 14), stock_text, font=_font(27, True), fill="#F8FAFC")

            entry_text = _fit_text(draw, row["entry_status"], _font(18, True), 220)
            draw.rounded_rectangle((470, top + 12, 716, top + 45), radius=16, fill="#172033", outline="#334155")
            draw.text((593, top + 28), entry_text, font=_font(18, True), fill="#FCA5A5", anchor="mm")

            score_color = "#F87171" if row["score_text"] != "--" else "#94A3B8"
            draw.text((1000, top + 13), row["score_text"], font=_font(29, True), fill=score_color, anchor="ra")
            draw.text((1000, top + 51), _fit_text(draw, row["rating"], _font(17, True), 155), font=_font(17, True), fill="#4ADE80", anchor="ra")

            change = row["change_value"]
            change_color = "#94A3B8" if change is None else ("#F87171" if change >= 0 else "#4ADE80")
            labels = (
                (128, "收盤", row["close_text"], "#E2E8F0"),
                (265, "漲跌", row["change_text"], change_color),
                (405, "技術勝率", row["win_rate_text"], "#60A5FA"),
                (576, "樣本", row["sample_text"], "#E2E8F0"),
                (682, "可信度", row["credibility"], row["credibility_color"]),
                (880, "產業", _fit_text(draw, row["industry"], _font(17, True), 115), "#A5B4FC"),
            )
            for x, label, value, color in labels:
                draw.text((x, top + 57), label, font=_font(14), fill="#64748B")
                draw.text((x, top + 76), value, font=_font(17, True), fill=color)

    footer_y = start_y + 10 * (CARD_HEIGHT + CARD_GAP) + 14
    draw.line((54, footer_y, IMAGE_WIDTH - 54, footer_y), fill="#1E293B", width=2)
    draw.text((54, footer_y + 18), "技術勝率為逐步前推回測結果，不代表未來績效。缺失資料一律顯示 --。", font=_font(17), fill="#94A3B8")
    draw.text((IMAGE_WIDTH - 54, footer_y + 18), "僅供研究參考", font=_font(17, True), fill="#FBBF24", anchor="ra")

    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def render_executable_image(results: Sequence[Mapping[str, Any]], trading_date: str) -> bytes:
    """Render the stocks whose saved post-close entry plan is executable now."""
    rows = build_executable_display_rows(results)
    image = Image.new("RGB", (IMAGE_WIDTH, EXECUTABLE_IMAGE_HEIGHT), "#070D1A")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((30, 26, IMAGE_WIDTH - 30, 145), radius=28, fill="#0F172A", outline="#1E293B", width=2)
    draw.text((62, 48), "EXECUTABLE WATCHLIST", font=_font(18, True), fill="#F87171")
    draw.text((62, 76), prediction_title(trading_date), font=_font(39, True), fill="#F8FAFC")
    draw.text((IMAGE_WIDTH - 62, 53), f"分析日 {_clean_text(trading_date)}", font=_font(22, True), fill="#FBBF24", anchor="ra")
    draw.text(
        (IMAGE_WIDTH - 62, 91),
        f"{len(rows)} 檔｜每檔停損風險上限 $5,000",
        font=_font(19),
        fill="#94A3B8",
        anchor="ra",
    )
    concentration_text = _concentration_text(rows)
    if concentration_text:
        draw.text((IMAGE_WIDTH - 62, 119), concentration_text, font=_font(14, True), fill="#FBBF24", anchor="ra")

    if not rows:
        draw.rounded_rectangle((70, 245, IMAGE_WIDTH - 70, 950), radius=36, fill="#0F172A", outline="#1E293B", width=2)
        draw.text((IMAGE_WIDTH // 2, 485), "今日沒有符合", font=_font(32, True), fill="#94A3B8", anchor="mm")
        draw.text((IMAGE_WIDTH // 2, 555), "「現在可執行」條件的股票", font=_font(42, True), fill="#F8FAFC", anchor="mm")
        draw.text((IMAGE_WIDTH // 2, 645), "系統不會把等待拉回、等待量能或條件不足的股票塞入名單", font=_font(20), fill="#64748B", anchor="mm")
    else:
        start_y = 168
        for index, row in enumerate(rows):
            top = start_y + index * (EXECUTABLE_CARD_HEIGHT + EXECUTABLE_CARD_GAP)
            bottom = top + EXECUTABLE_CARD_HEIGHT
            draw.rounded_rectangle(
                (CARD_LEFT, top, CARD_LEFT + CARD_WIDTH, bottom),
                radius=18,
                fill="#0F172A",
                outline="#3F2631",
                width=2,
            )
            draw.ellipse((58, top + 20, 108, top + 70), fill="#7F1D1D")
            draw.text((83, top + 45), str(row["display_rank"]), font=_font(23, True), fill="#FECACA", anchor="mm")
            stock_font = _font(25, True)
            stock_text = _fit_text(draw, f"{row['ticker']}  {row['name']}", stock_font, 210)
            draw.text((128, top + 10), stock_text, font=stock_font, fill="#F8FAFC")
            score_x = min(350, int(128 + draw.textlength(stock_text, font=stock_font) + 14))
            draw.text((score_x, top + 12), row["score_text"], font=_font(22, True), fill="#F87171")
            sample_text = _fit_text(draw, row["sample_credibility_text"], _font(15, True), 165)
            draw.text((420, top + 16), sample_text, font=_font(15, True), fill=row["credibility_color"])
            analysis = _fit_text(draw, f"解析｜{row['analysis']}", _font(15, True), 455)
            draw.text((128, top + 49), analysis, font=_font(15, True), fill="#CBD5E1")
            _draw_mini_candles(
                draw,
                row["mini_kbars"],
                (600, top + 8, 1018, top + 88),
                row["close_value"],
            )

            change = row["change_value"]
            current_color = "#E2E8F0" if change is None else ("#F87171" if change >= 0 else "#4ADE80")
            labels = (
                (128, "現價 / 漲跌", row["close_change_text"], current_color),
                (275, "建議買入區間", row["entry_zone_text"], "#F8FAFC"),
                (430, "建議零股", row["suggested_shares_text"], "#FBBF24"),
                (555, "停損最大虧損", row["estimated_loss_text"], "#F8FAFC"),
                (685, "風險停損", row["stop_text"], "#4ADE80"),
                (800, "策略目標", row["target_text"], "#F87171"),
                (910, "技術勝率", row["win_rate_text"], "#60A5FA"),
            )
            for x, label, value, color in labels:
                draw.text((x, top + 96), label, font=_font(12), fill="#64748B")
                draw.text((x, top + 116), value, font=_font(15, True), fill=color)

    footer_y = 1670
    draw.line((54, footer_y, IMAGE_WIDTH - 54, footer_y), fill="#1E293B", width=2)
    draw.text((54, footer_y + 14), "建議股數 = floor($5,000 ÷（現價 − 停損價）)，每檔分別計算。", font=_font(16), fill="#94A3B8")
    draw.text((54, footer_y + 43), "每檔停損價差損失不超過 $5,000；未計滑價、手續費與交易稅。", font=_font(17, True), fill="#FBBF24")
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def _return_color(value: float | None) -> str:
    if value is None:
        return "#94A3B8"
    return "#F87171" if value >= 0 else "#4ADE80"


def render_tracking_performance_image(
    records: Sequence[Mapping[str, Any]],
    positions: Sequence[Mapping[str, Any]],
    trading_date: str,
    *,
    page_index: int = 0,
) -> bytes:
    """Render one page of the daily equal-weight tracking performance."""
    report = build_tracking_performance_report(records, positions, trading_date)
    page_count = report["page_count"]
    page_index = min(max(int(page_index), 0), page_count - 1)
    page_start = page_index * 10
    rows = report["rows"][page_start:page_start + 10]
    image = Image.new("RGB", (IMAGE_WIDTH, IMAGE_HEIGHT), "#070D1A")
    draw = ImageDraw.Draw(image)

    draw.rounded_rectangle(
        (30, 26, IMAGE_WIDTH - 30, 145),
        radius=28,
        fill="#0F172A",
        outline="#1E293B",
        width=2,
    )
    draw.text((62, 48), "DAILY TRACKING PERFORMANCE", font=_font(18, True), fill="#FBBF24")
    draw.text((62, 76), "每日追蹤績效", font=_font(39, True), fill="#F8FAFC")
    draw.text((IMAGE_WIDTH - 62, 53), report["date"], font=_font(24, True), fill="#FBBF24", anchor="ra")
    draw.text(
        (IMAGE_WIDTH - 62, 91),
        f"{report['start_date']} 分析名單起｜真實盤後 OHLC",
        font=_font(18),
        fill="#94A3B8",
        anchor="ra",
    )

    relative_label = "相對大盤" if report["excess_average"] is not None else "有效行情"
    relative_value = (
        _percent_text(report["excess_average"])
        if report["excess_average"] is not None
        else f"{report['valid_count']} 檔"
    )
    relative_color = (
        _return_color(report["excess_average"])
        if report["excess_average"] is not None
        else "#60A5FA"
    )
    summary_cards = (
        (42, "今日有損益", f"{report['tracked_count']} 檔", "#E2E8F0"),
        (296, relative_label, relative_value, relative_color),
        (550, "平均單日", _percent_text(report["daily_average"]), _return_color(report["daily_average"])),
        (804, "持有均報酬", _percent_text(report["open_average"]), _return_color(report["open_average"])),
    )
    for left, label, value, color in summary_cards:
        draw.rounded_rectangle((left, 168, left + 234, 275), radius=20, fill="#0F172A", outline="#1E293B", width=2)
        draw.text((left + 18, 184), label, font=_font(16), fill="#64748B")
        draw.text((left + 18, 218), value, font=_font(28, True), fill=color)

    realized_text = (
        "--"
        if report["realized_win_rate"] is None
        else f"{report['realized_win_rate']:.1f}%"
    )
    action_text = "｜".join(
        f"{label} {report['actions'].get(action, 0)}"
        for action, label in (
            ("SIGNAL", "訊號"),
            ("ENTRY", "成交"),
            ("HOLD", "持有"),
            ("ENTRY_EXPIRED", "失效"),
            ("TAKE_PROFIT", "停利"),
            ("STOP_LOSS", "停損"),
        )
        if report["actions"].get(action, 0)
    ) or "本日沒有追蹤動作"
    draw.rounded_rectangle((42, 292, 1038, 372), radius=18, fill="#111827", outline="#1E293B", width=2)
    draw.text(
        (64, 307),
        _fit_text(draw, action_text, _font(16, True), 610),
        font=_font(16, True),
        fill="#CBD5E1",
    )
    draw.text(
        (1016, 307),
        f"歷史結算 {report['closed_count']} 筆｜勝率 {realized_text}",
        font=_font(18, True),
        fill="#60A5FA" if report["realized_win_rate"] is not None else "#94A3B8",
        anchor="ra",
    )
    context_parts = [
        f"未有跨日損益排除 {report['excluded_count']} 檔",
        f"待成交 {report['pending_count']}／失效 {report['expired_count']}",
    ]
    if report["largest_industry"]:
        context_parts.append(
            f"最大產業 {report['largest_industry']} "
            f"{report['largest_industry_count']}/{report['industry_sample_count']} "
            f"({report['largest_industry_share']:.0f}%)"
        )
    if report["benchmark_average"] is not None:
        context_parts.append(f"大盤 {_percent_text(report['benchmark_average'])}")
    draw.text(
        (64, 340),
        _fit_text(draw, "｜".join(context_parts), _font(15), 940),
        font=_font(15),
        fill="#FACC15" if report["excluded_count"] else "#64748B",
    )

    if not rows:
        draw.rounded_rectangle((70, 430, IMAGE_WIDTH - 70, 1050), radius=36, fill="#0F172A", outline="#1E293B", width=2)
        draw.text((IMAGE_WIDTH // 2, 680), "目前沒有可顯示的追蹤紀錄", font=_font(36, True), fill="#F8FAFC", anchor="mm")
        draw.text((IMAGE_WIDTH // 2, 745), "當日新入榜尚無損益，將於下一交易日納入", font=_font(21), fill="#94A3B8", anchor="mm")
    else:
        start_y = 394
        row_height = 78
        row_gap = 8
        for index, row in enumerate(rows):
            top = start_y + index * (row_height + row_gap)
            bottom = top + row_height
            outline = "#3F2631" if (row["pnl"] or 0) >= 0 else "#17392C"
            draw.rounded_rectangle((42, top, 1038, bottom), radius=16, fill="#0F172A", outline=outline, width=2)
            stock_text = _fit_text(draw, f"{row['ticker']}  {row['name']}", _font(23, True), 290)
            draw.text((62, top + 9), stock_text, font=_font(23, True), fill="#F8FAFC")
            draw.rounded_rectangle((356, top + 8, 478, top + 38), radius=14, fill="#172033")
            draw.text((417, top + 23), _fit_text(draw, row["action"], _font(15, True), 105), font=_font(15, True), fill="#CBD5E1", anchor="mm")
            detail_text = f"{row['entry_date']}｜進 {row['entry_text']} → 追蹤 {row['mark_text']}"
            if row["decline_diagnostic"]:
                detail_text += f"｜觀察：{row['decline_diagnostic']}"
            draw.text(
                (62, top + 48),
                _fit_text(draw, detail_text, _font(14), 490),
                font=_font(14),
                fill="#64748B",
            )

            performance_columns = (
                (
                    570,
                    "今日",
                    row["daily_price_change_text"],
                    row["daily_return_text"],
                    _return_color(row["daily_return"]),
                ),
                (
                    720,
                    "持有",
                    row["holding_price_change_text"],
                    row["pnl_text"],
                    _return_color(row["pnl"]),
                ),
            )
            for x, label, price_change, percent_change, color in performance_columns:
                draw.text((x, top + 5), label, font=_font(12), fill="#64748B")
                draw.text((x, top + 24), price_change, font=_font(16, True), fill=color)
                draw.text((x, top + 47), f"({percent_change})", font=_font(15, True), fill=color)
            draw.text((875, top + 9), "入榜勝率/樣本", font=_font(13), fill="#64748B")
            draw.text((875, top + 35), row["backtest_text"], font=_font(18, True), fill=row["credibility_color"])
            if row["net_pnl_amount"] is not None or row["estimated_transaction_cost"] is not None:
                net_text = f"淨 {row['net_pnl_text']}｜費 {row['estimated_cost_text'].replace('NT$', '')}"
                draw.text(
                    (875, top + 59),
                    _fit_text(draw, net_text, _font(11), 145),
                    font=_font(11),
                    fill=_return_color(row["net_pnl_amount"]),
                )

    footer_y = 1270
    draw.line((54, footer_y, IMAGE_WIDTH - 54, footer_y), fill="#1E293B", width=2)
    observation_footer = "平均績效採等權計算；跌幅標籤為價格觀察，不代表已證實原因。"
    if report["leading_decline_category"]:
        observation_footer = (
            f"跌幅觀察：{report['leading_decline_category']} "
            f"{report['leading_decline_count']}/{report['losing_count']}"
        )
        if report["hot_entry_losing_count"]:
            observation_footer += (
                f"；入榜日漲幅≥3% {report['hot_entry_losing_count']}/{report['losing_count']}"
            )
        observation_footer += "（僅觀察，非因果）"
    draw.text(
        (54, footer_y + 15),
        _fit_text(draw, observation_footer, _font(15), 770),
        font=_font(15),
        fill="#94A3B8",
    )
    draw.text(
        (IMAGE_WIDTH - 54, footer_y + 15),
        f"第 {page_index + 1} / {page_count} 頁",
        font=_font(16, True),
        fill="#60A5FA",
        anchor="ra",
    )
    strategy_footer = "新版依凍結進場區、停損與目標；僅次一交易日觸區成交。"
    if report["legacy_count"]:
        strategy_footer += " 舊版仍沿用 +15%/-10%。"
    draw.text((54, footer_y + 46), strategy_footer, font=_font(14, True), fill="#FBBF24")
    draw.text((IMAGE_WIDTH - 54, footer_y + 46), "僅供研究參考", font=_font(16, True), fill="#FBBF24", anchor="ra")

    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def render_tracking_performance_images(
    records: Sequence[Mapping[str, Any]],
    positions: Sequence[Mapping[str, Any]],
    trading_date: str,
) -> list[bytes]:
    """Render every tracked stock across readable ten-row Telegram pages."""
    report = build_tracking_performance_report(records, positions, trading_date)
    return [
        render_tracking_performance_image(
            records,
            positions,
            trading_date,
            page_index=page_index,
        )
        for page_index in range(report["page_count"])
    ]


def _send_photo_bytes(
    png: bytes,
    filename: str,
    caption: str,
    bot_token: Any,
    chat_id: Any,
    session: requests.Session | None,
) -> int | None:
    token = str(bot_token or "").strip()
    target_chat = str(chat_id or "").strip()
    if not token or not target_chat:
        raise RuntimeError("Telegram 設定缺少 TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHAT_ID")
    client = session or requests.Session()
    try:
        response = client.post(
            f"https://api.telegram.org/bot{token}/sendPhoto",
            data={"chat_id": target_chat, "caption": caption},
            files={"photo": (filename, png, "image/png")},
            timeout=30,
        )
    except Exception as error:
        raise RuntimeError(f"Telegram 圖片發送連線失敗（{type(error).__name__}）") from None
    if response.status_code < 200 or response.status_code >= 300:
        raise RuntimeError(f"Telegram 圖片發送失敗（HTTP {response.status_code}）")
    try:
        payload = response.json()
    except (TypeError, ValueError):
        raise RuntimeError("Telegram 回應格式錯誤") from None
    if not isinstance(payload, Mapping) or not payload.get("ok"):
        raise RuntimeError("Telegram API 未確認圖片發送成功")
    result = payload.get("result")
    message_id = result.get("message_id") if isinstance(result, Mapping) else None
    return int(message_id) if isinstance(message_id, (int, float)) else None


def _send_document_bytes(
    png: bytes,
    filename: str,
    caption: str,
    bot_token: Any,
    chat_id: Any,
    session: requests.Session | None,
) -> int | None:
    """Send a PNG as a document so Telegram does not recompress the image."""
    token = str(bot_token or "").strip()
    target_chat = str(chat_id or "").strip()
    if not token or not target_chat:
        raise RuntimeError("Telegram 設定缺少 TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHAT_ID")
    client = session or requests.Session()
    try:
        response = client.post(
            f"https://api.telegram.org/bot{token}/sendDocument",
            data={"chat_id": target_chat, "caption": caption},
            files={"document": (filename, png, "image/png")},
            timeout=30,
        )
    except Exception as error:
        raise RuntimeError(f"Telegram 原圖發送連線失敗（{type(error).__name__}）") from None
    if response.status_code < 200 or response.status_code >= 300:
        raise RuntimeError(f"Telegram 原圖發送失敗（HTTP {response.status_code}）")
    try:
        payload = response.json()
    except (TypeError, ValueError):
        raise RuntimeError("Telegram 回應格式錯誤") from None
    if not isinstance(payload, Mapping) or not payload.get("ok"):
        raise RuntimeError("Telegram API 未確認原圖發送成功")
    result = payload.get("result")
    message_id = result.get("message_id") if isinstance(result, Mapping) else None
    return int(message_id) if isinstance(message_id, (int, float)) else None


def send_top10_photo(
    results: Sequence[Mapping[str, Any]],
    trading_date: str,
    bot_token: Any,
    chat_id: Any,
    *,
    session: requests.Session | None = None,
) -> int | None:
    """Send the rendered ranking through Telegram's sendPhoto API."""
    png = render_top10_image(results, trading_date)
    return _send_photo_bytes(
        png,
        f"top10-{trading_date}.png",
        f"台股每日可執行 Top 10｜{trading_date}\n僅納入現在可執行標的；技術勝率為歷史回測，僅供研究參考。",
        bot_token,
        chat_id,
        session,
    )


def send_executable_photo(
    results: Sequence[Mapping[str, Any]],
    trading_date: str,
    bot_token: Any,
    chat_id: Any,
    *,
    session: requests.Session | None = None,
) -> int | None:
    """Send the executable prediction as a lossless PNG document."""
    rows = build_executable_display_rows(results)
    png = render_executable_image(results, trading_date)
    count_text = f"共 {len(rows)} 檔" if rows else "今日無符合標的"
    return _send_document_bytes(
        png,
        f"executable-{trading_date}.png",
        f"{prediction_title(trading_date)}｜分析日 {trading_date}\n{count_text}；請依圖片停損控管風險。",
        bot_token,
        chat_id,
        session,
    )


def send_tracking_performance_photo(
    records: Sequence[Mapping[str, Any]],
    positions: Sequence[Mapping[str, Any]],
    trading_date: str,
    bot_token: Any,
    chat_id: Any,
    *,
    session: requests.Session | None = None,
) -> int | None:
    """Send every daily tracking-performance page through Telegram."""
    report = build_tracking_performance_report(records, positions, trading_date)
    pages = render_tracking_performance_images(records, positions, trading_date)
    daily_average = _percent_text(report["daily_average"])
    message_ids: list[int | None] = []
    for page_number, png in enumerate(pages, start=1):
        page_suffix = "" if len(pages) == 1 else f"-p{page_number}-of-{len(pages)}"
        message_ids.append(_send_photo_bytes(
            png,
            f"tracking-performance-{trading_date}{page_suffix}.png",
            (
                f"每日追蹤績效｜{trading_date}｜第 {page_number}/{len(pages)} 頁\n"
                f"從 {TRACKING_PERFORMANCE_START_DATE} 分析名單起；本日有損益 {report['tracked_count']} 檔、"
                f"平均單日 {daily_average}；只採真實盤後行情。"
            ),
            bot_token,
            chat_id,
            session,
        ))
    return message_ids[0] if message_ids else None
