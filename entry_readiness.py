"""Pure entry-readiness rules shared by the scanner and Streamlit UI.

Ranking score answers whether a stock is worth tracking.  These helpers answer
the separate question of whether the current price is inside an executable
entry zone.  Missing inputs deliberately produce no price levels.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any


ENTRY_SCHEMA_VERSION = 1
READY_STATUS = "現在可執行"
WAIT_PULLBACK_STATUS = "等待拉回"
WAIT_TRIGGER_STATUS = "等待觸發"
INSUFFICIENT_STATUS = "條件不足"
LEGACY_STATUS = "待新掃描"

_OVERHEAT_PATTERNS = {"過熱追高型", "假突破風險型"}
_BREAKOUT_PATTERNS = {"趨勢突破型", "整理突破型", "低檔反彈型"}


def _number(value: Any) -> float | None:
    try:
        number = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "是", "confirmed"}
    return bool(value)


def _tick_size(price: float) -> float:
    if price < 10:
        return 0.01
    if price < 50:
        return 0.05
    if price < 100:
        return 0.1
    if price < 500:
        return 0.5
    if price < 1000:
        return 1.0
    return 5.0


def _tick_price(value: float, direction: str = "nearest") -> float:
    tick = _tick_size(max(value, 0.01))
    units = value / tick
    if direction == "floor":
        rounded = math.floor(units + 1e-9) * tick
    elif direction == "ceil":
        rounded = math.ceil(units - 1e-9) * tick
    else:
        rounded = round(units) * tick
    decimals = 2 if tick < 0.1 else (1 if tick < 1 else 0)
    return round(max(rounded, tick), decimals)


def _result(
    status: str,
    group: str,
    reason: str,
    *,
    plan_type: str = "",
    low: float | None = None,
    high: float | None = None,
    stop: float | None = None,
    target: float | None = None,
    no_chase: float | None = None,
) -> dict[str, Any]:
    has_levels = all(value is not None for value in (low, high, stop, target))
    return {
        "Entry_Schema": ENTRY_SCHEMA_VERSION,
        "Entry_Status": status,
        "Entry_Status_Group": group,
        "Entry_Ready": status == READY_STATUS,
        "Entry_Plan_Type": plan_type,
        "Entry_Low": low if has_levels else None,
        "Entry_High": high if has_levels else None,
        "Entry_Stop": stop if has_levels else None,
        "Entry_Target": target if has_levels else None,
        "Entry_RRR": 1.5 if has_levels else None,
        "No_Chase_Price": no_chase,
        "Entry_Reason": reason,
    }


def _legacy_result(record: Mapping[str, Any]) -> dict[str, Any]:
    score = _number(record.get("Score"))
    change = _number(record.get("漲跌幅"))
    pattern = str(record.get("Entry_Pattern") or "")
    if score is not None and score < 60:
        return _result(INSUFFICIENT_STATUS, "watch", "量化分數未達 60 分。")
    if pattern in _OVERHEAT_PATTERNS or (change is not None and change >= 7):
        return _result(
            WAIT_PULLBACK_STATUS,
            "wait",
            "單日漲幅或型態已偏熱；待新掃描補足 20MA、ATR 後再計算區間。",
        )
    return _result(
        LEGACY_STATUS,
        "watch",
        "既有榜單缺少 20MA 或 ATR；待新掃描補足後才判定進場條件。",
    )


def _levels(low: float, high: float, atr: float, no_chase: float) -> tuple[float, float, float, float] | None:
    low = _tick_price(low, "floor")
    high = _tick_price(min(high, no_chase), "ceil")
    if high < low:
        return None
    midpoint = (low + high) / 2
    stop = _tick_price(low - atr, "floor")
    if stop <= 0 or midpoint <= stop:
        return None
    target = _tick_price(midpoint + (midpoint - stop) * 1.5, "ceil")
    return low, high, stop, target


def _is_overheated(record: Mapping[str, Any], close: float) -> tuple[bool, str]:
    pattern = str(record.get("Entry_Pattern") or "")
    rsi = _number(record.get("RSI"))
    bias = _number(record.get("BIAS"))
    bb_up = _number(record.get("BB_UP"))
    change = _number(record.get("漲跌幅"))
    if pattern in _OVERHEAT_PATTERNS:
        return True, f"{pattern}，不追高。"
    if change is not None and change >= 7:
        return True, f"單日上漲 {change:.1f}%，不追高。"
    if rsi is not None and rsi >= 75:
        return True, f"RSI {rsi:.1f} 已過熱。"
    if bias is not None and bias > 7:
        return True, f"20MA 乖離 {bias:.1f}% 過大。"
    if bb_up is not None and bb_up > 0 and close >= bb_up * 0.98:
        return True, "股價已接近布林上軌。"
    return False, ""


def build_entry_readiness(
    record: Mapping[str, Any],
    *,
    intraday: bool = False,
    baseline_plan: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an honest entry plan from current technical values.

    During intraday evaluation, ``baseline_plan`` is the saved post-close plan;
    the live price can activate that plan but cannot move its trigger levels.
    """
    score = _number(record.get("Score"))
    close = _number(record.get("收盤價"))
    if score is not None and score < 60:
        return _result(INSUFFICIENT_STATUS, "watch", "量化分數未達 60 分。")
    if close is None or close <= 0:
        return _legacy_result(record)

    confidence = _number(record.get("Confidence"))
    conflict = str(record.get("Signal_Conflict") or "")
    volume_confirmed = _truthy(record.get("Volume_Confirmed"))
    overheated, overheat_reason = _is_overheated(record, close)

    if intraday and baseline_plan:
        low = _number(baseline_plan.get("Entry_Low"))
        high = _number(baseline_plan.get("Entry_High"))
        stop = _number(baseline_plan.get("Entry_Stop"))
        target = _number(baseline_plan.get("Entry_Target"))
        no_chase = _number(baseline_plan.get("No_Chase_Price"))
        plan_type = str(baseline_plan.get("Entry_Plan_Type") or "盤後計畫")
        if all(value is not None for value in (low, high, stop, target)):
            kwargs = dict(plan_type=plan_type, low=low, high=high, stop=stop, target=target, no_chase=no_chase)
            if no_chase is not None and close > no_chase:
                return _result(WAIT_PULLBACK_STATUS, "wait", f"現價 {close:.2f} 已超過禁止追高價。", **kwargs)
            if overheated:
                return _result(WAIT_PULLBACK_STATUS, "wait", overheat_reason, **kwargs)
            if conflict == "高":
                return _result(WAIT_TRIGGER_STATUS, "wait", "多空訊號衝突偏高，等待重新確認。", **kwargs)
            if low <= close <= high:
                if not volume_confirmed:
                    return _result(WAIT_TRIGGER_STATUS, "wait", "價格已進區間，但盤中量能尚未確認。", **kwargs)
                if confidence is not None and confidence < 70:
                    return _result(WAIT_TRIGGER_STATUS, "wait", f"資料信心僅 {confidence:.0f}%，暫不執行。", **kwargs)
                return _result(READY_STATUS, "ready", "價格已進入盤後規劃區間，且量能確認。", **kwargs)
            if close < low:
                return _result(WAIT_TRIGGER_STATUS, "wait", f"現價尚未進入 {low:g}–{high:g} 觀察區間。", **kwargs)
            return _result(WAIT_PULLBACK_STATUS, "wait", f"現價已高於 {low:g}–{high:g} 觀察區間。", **kwargs)

    ma20 = _number(record.get("20MA"))
    atr = _number(record.get("ATR"))
    high_price = _number(record.get("最高價"))
    if ma20 is None or ma20 <= 0 or atr is None or atr <= 0 or high_price is None or high_price <= 0:
        return _legacy_result(record)

    no_chase = _tick_price(close * 1.035, "floor")
    pattern = str(record.get("Entry_Pattern") or "一般觀察型")
    if overheated:
        plan_type = "pullback"
        level_values = _levels(ma20, ma20 + atr * 0.5, atr, no_chase)
        if level_values:
            return _result(WAIT_PULLBACK_STATUS, "wait", overheat_reason, plan_type=plan_type, no_chase=no_chase,
                           low=level_values[0], high=level_values[1], stop=level_values[2], target=level_values[3])
        return _result(WAIT_PULLBACK_STATUS, "wait", overheat_reason, no_chase=no_chase)

    if pattern in _BREAKOUT_PATTERNS:
        plan_type = "breakout"
        level_values = _levels(high_price, high_price + atr * 0.5, atr, no_chase)
        if not level_values:
            pullback = _levels(ma20, ma20 + atr * 0.5, atr, no_chase)
            if pullback:
                return _result(WAIT_PULLBACK_STATUS, "wait", "突破觸發價已超過禁止追高價，改等回測。",
                               plan_type="pullback", no_chase=no_chase, low=pullback[0], high=pullback[1], stop=pullback[2], target=pullback[3])
            return _result(WAIT_PULLBACK_STATUS, "wait", "突破觸發價已超過禁止追高價。", no_chase=no_chase)
        return _result(WAIT_TRIGGER_STATUS, "wait", "突破今日高點且量能延續後，才進入可執行區間。",
                       plan_type=plan_type, no_chase=no_chase, low=level_values[0], high=level_values[1], stop=level_values[2], target=level_values[3])

    level_values = _levels(ma20, ma20 + atr * 0.5, atr, no_chase)
    if not level_values:
        return _result(WAIT_PULLBACK_STATUS, "wait", "目前無法建立風險報酬合理的區間。", no_chase=no_chase)
    kwargs = dict(plan_type="pullback", no_chase=no_chase, low=level_values[0], high=level_values[1],
                  stop=level_values[2], target=level_values[3])
    if conflict == "高":
        return _result(WAIT_TRIGGER_STATUS, "wait", "多空訊號衝突偏高，等待重新確認。", **kwargs)
    if level_values[0] <= close <= level_values[1] and volume_confirmed:
        if confidence is not None and confidence < 70:
            return _result(WAIT_TRIGGER_STATUS, "wait", f"價格已進區間，但資料信心僅 {confidence:.0f}%。", **kwargs)
        return _result(READY_STATUS, "ready", "價格位於 20MA 回測區，且量能確認。", **kwargs)
    if close > level_values[1]:
        return _result(WAIT_PULLBACK_STATUS, "wait", "價格仍高於 20MA 回測區，不追價。", **kwargs)
    return _result(WAIT_TRIGGER_STATUS, "wait", "價格尚未站回 20MA 觀察區。", **kwargs)


def ensure_entry_readiness(record: Mapping[str, Any]) -> dict[str, Any]:
    """Preserve current-schema records and conservatively annotate legacy rows."""
    result = dict(record)
    if _number(result.get("Entry_Schema")) == ENTRY_SCHEMA_VERSION and result.get("Entry_Status"):
        return result
    result.update(build_entry_readiness(result))
    return result
