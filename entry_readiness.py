"""Pure entry-readiness rules shared by the scanner and Streamlit UI.

Ranking score answers whether a stock is worth tracking.  These helpers answer
the separate question of whether the current price is inside an executable
entry zone.  Missing inputs deliberately produce no price levels.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any


ENTRY_SCHEMA_VERSION = 2
MIN_EXECUTION_SCORE = 65
MIN_EFFECTIVE_REWARD_RISK = 1.30
MIN_BACKTEST_SAMPLES = 15
MIN_VALIDATION_SAMPLES = 5
MIN_BACKTEST_WIN_RATE = 40.0
MIN_VALIDATION_WIN_RATE = 40.0
READY_STATUS = "現在可執行"
WAIT_VOLUME_STATUS = "等待量能確認"
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


def build_entry_summary(record: Mapping[str, Any]) -> str:
    """Return one concise, evidence-based reason plus the most important caution."""
    pattern = str(record.get("Entry_Pattern") or "").strip()
    if pattern in {"趨勢突破型", "整理突破型"}:
        main = "型態突破確認"
    elif pattern == "低檔反彈型":
        main = "低檔反彈轉強"
    elif pattern == "回測支撐型":
        main = "20MA支撐確認"
    else:
        main = "進入20MA回測區"

    conflict = str(record.get("Signal_Conflict") or "").strip()
    market_regime = str(record.get("Market_Regime") or "").strip()
    rsi = _number(record.get("RSI"))
    bias = _number(record.get("BIAS"))
    whale_net = _number(record.get("Whale_Net"))
    whale_days = max(1, int(_number(record.get("Whale_Net_Days")) or 1))
    volume_ratio = _number(record.get("Est_Vol_Ratio"))

    if conflict in {"中", "高"}:
        detail = "訊號分歧，嚴守停損"
    elif market_regime == "空頭":
        detail = "大盤在月季線下，降低部位"
    elif rsi is not None and rsi >= 70:
        detail = f"RSI {rsi:.0f}偏高，避免追價"
    elif bias is not None and bias >= 5:
        detail = f"乖離{bias:.1f}%偏高，避免追價"
    elif whale_net is not None and whale_net >= 100:
        detail = f"法人{whale_days}日買超{whale_net:,.0f}張"
    elif whale_net is not None and whale_net <= -100:
        detail = f"法人{whale_days}日賣超{abs(whale_net):,.0f}張"
    elif volume_ratio is not None:
        detail = f"量比{volume_ratio:.2f}×已確認"
    else:
        original = " ".join(str(record.get("Entry_Reason") or "").split()).strip()
        if original:
            return original
        detail = "量能已確認"
    return f"{main}｜{detail}"


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
    rrr: float = 1.5,
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
        "Entry_RRR": round(rrr, 2) if has_levels else None,
        "No_Chase_Price": no_chase,
        "Entry_Reason": reason,
    }


def _legacy_result(record: Mapping[str, Any]) -> dict[str, Any]:
    score = _number(record.get("Score"))
    change = _number(record.get("漲跌幅"))
    pattern = str(record.get("Entry_Pattern") or "")
    if score is not None and score < MIN_EXECUTION_SCORE:
        reason = (
            "量化分數未達 60 分。"
            if score < 60
            else f"目前 {score:g} 分屬一般觀察，未達 {MIN_EXECUTION_SCORE} 分可執行門檻。"
        )
        return _result(INSUFFICIENT_STATUS, "watch", reason)
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


def _levels(
    low: float,
    high: float,
    atr: float,
    no_chase: float,
    *,
    stop_atr_mult: float = 1.0,
    reward_risk: float = 1.5,
) -> tuple[float, float, float, float] | None:
    low = _tick_price(low, "floor")
    high = _tick_price(min(high, no_chase), "ceil")
    if high < low:
        return None
    midpoint = (low + high) / 2
    if stop_atr_mult <= 0 or reward_risk <= 0:
        return None
    stop = _tick_price(low - atr * stop_atr_mult, "floor")
    if stop <= 0 or midpoint <= stop:
        return None
    target = _tick_price(midpoint + (midpoint - stop) * reward_risk, "ceil")
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


def _volume_wait_reason(record: Mapping[str, Any]) -> str:
    """Return why volume is not execution-ready, or an empty string."""
    if not _truthy(record.get("Volume_Confirmed")):
        return "盤中量能尚未確認。"
    volume_ratio = _number(record.get("Est_Vol_Ratio"))
    if volume_ratio is None:
        return "缺少量比資料，暫不執行。"
    if volume_ratio < 1.1:
        return f"價格已進觀察區，但量比僅 {volume_ratio:.2f}，尚未達 1.10。"
    return ""


def _execution_risk_reason(record: Mapping[str, Any]) -> str:
    """Return a hard execution veto using only facts available at decision time."""
    market_regime = str(record.get("Market_Regime") or "").strip()
    market_return = _number(record.get("Market_Return"))
    if market_regime == "空頭":
        return "大盤仍在空頭結構，暫停新增多單。"
    if market_return is not None and market_return <= -1.0:
        return f"大盤當日下跌 {abs(market_return):.1f}%，系統性賣壓偏高。"
    if market_regime == "震盪" and market_return is not None and market_return <= -0.5:
        return f"大盤震盪且當日下跌 {abs(market_return):.1f}%，等待市場止穩。"

    risk_level = str(record.get("Financial_Risk_Level") or "").strip().lower()
    operating_income = _number(record.get("Financial_Operating_Income"))
    net_income = _number(record.get("Financial_Net_Income"))
    eps = _number(record.get("EPS"))
    mom = _number(record.get("MoM"))
    yoy = _number(record.get("YoY"))
    if risk_level == "high":
        return "最新季度財報屬高風險，暫不列為可執行。"
    if operating_income is not None and net_income is not None and operating_income < 0 and net_income < 0:
        return "最新季度營業與本期損益皆為負，先等待獲利改善。"
    if eps is not None and eps < 0 and mom is not None and yoy is not None and mom < 0 and yoy < 0:
        return "EPS 為負且月營收雙減，基本面風險尚未改善。"

    sell_streak = _number(record.get("Institutional_Sell_Streak"))
    whale_net = _number(record.get("Whale_Net"))
    foreign_net = _number(record.get("Foreign_Net"))
    trust_net = _number(record.get("Trust_Net"))
    if sell_streak is not None and sell_streak >= 3 and whale_net is not None and whale_net < 0:
        return f"法人已連續賣超 {int(sell_streak)} 日，籌碼尚未止穩。"
    if (
        whale_net is not None and whale_net < 0
        and foreign_net is not None and foreign_net < 0
        and trust_net is not None and trust_net < 0
    ):
        return "外資與投信近三日同步賣超，暫不逆勢進場。"

    backtest_fields = (
        "WinRate", "Backtest_Samples", "Validation_WinRate", "Validation_Samples"
    )
    if any(key in record for key in backtest_fields):
        backtest_rate = _number(record.get("WinRate"))
        backtest_samples = _number(record.get("Backtest_Samples"))
        validation_rate = _number(record.get("Validation_WinRate"))
        validation_samples = _number(record.get("Validation_Samples"))
        if backtest_samples is None or backtest_samples < MIN_BACKTEST_SAMPLES:
            return f"策略回測樣本未達 {MIN_BACKTEST_SAMPLES} 筆，僅列觀察。"
        if validation_samples is None or validation_samples < MIN_VALIDATION_SAMPLES:
            return f"近期驗證樣本未達 {MIN_VALIDATION_SAMPLES} 筆，僅列觀察。"
        if backtest_rate is None or backtest_rate < MIN_BACKTEST_WIN_RATE:
            return f"策略回測勝率未達 {MIN_BACKTEST_WIN_RATE:.0f}%，暫不執行。"
        if validation_rate is None or validation_rate < MIN_VALIDATION_WIN_RATE:
            return f"近期驗證勝率未達 {MIN_VALIDATION_WIN_RATE:.0f}%，暫不執行。"
    return ""


def _reward_risk_at_price(price: float, stop: float, target: float) -> float | None:
    risk = price - stop
    reward = target - price
    if risk <= 0 or reward <= 0:
        return None
    return reward / risk


def build_entry_readiness(
    record: Mapping[str, Any],
    *,
    intraday: bool = False,
    baseline_plan: Mapping[str, Any] | None = None,
    stop_atr_mult: float = 1.0,
    reward_risk: float = 1.5,
) -> dict[str, Any]:
    """Build an honest entry plan from current technical values.

    During intraday evaluation, ``baseline_plan`` is the saved post-close plan;
    the live price can activate that plan but cannot move its trigger levels.
    """
    score = _number(record.get("Score"))
    close = _number(record.get("收盤價"))
    if score is not None and score < MIN_EXECUTION_SCORE:
        reason = (
            "量化分數未達 60 分。"
            if score < 60
            else f"目前 {score:g} 分屬一般觀察，未達 {MIN_EXECUTION_SCORE} 分可執行門檻。"
        )
        return _result(INSUFFICIENT_STATUS, "watch", reason)
    if close is None or close <= 0:
        return _legacy_result(record)

    confidence = _number(record.get("Data_Completeness"))
    if confidence is None:
        confidence = _number(record.get("Confidence"))
    conflict = str(record.get("Signal_Conflict") or "")
    overheated, overheat_reason = _is_overheated(record, close)
    execution_risk_reason = _execution_risk_reason(record)

    if intraday and baseline_plan:
        low = _number(baseline_plan.get("Entry_Low"))
        high = _number(baseline_plan.get("Entry_High"))
        stop = _number(baseline_plan.get("Entry_Stop"))
        target = _number(baseline_plan.get("Entry_Target"))
        no_chase = _number(baseline_plan.get("No_Chase_Price"))
        plan_type = str(baseline_plan.get("Entry_Plan_Type") or "盤後計畫")
        if low is not None and high is not None and stop is not None and target is not None:
            baseline_rrr = _number(baseline_plan.get("Entry_RRR")) or reward_risk
            kwargs: dict[str, Any] = dict(
                plan_type=plan_type,
                low=low,
                high=high,
                stop=stop,
                target=target,
                no_chase=no_chase,
                rrr=baseline_rrr,
            )
            if execution_risk_reason:
                return _result(WAIT_TRIGGER_STATUS, "wait", execution_risk_reason, **kwargs)
            if no_chase is not None and close > no_chase:
                return _result(WAIT_PULLBACK_STATUS, "wait", f"現價 {close:.2f} 已超過禁止追高價。", **kwargs)
            if overheated:
                return _result(WAIT_PULLBACK_STATUS, "wait", overheat_reason, **kwargs)
            if conflict == "高":
                return _result(WAIT_TRIGGER_STATUS, "wait", "多空訊號衝突偏高，等待重新確認。", **kwargs)
            if low <= close <= high:
                volume_wait_reason = _volume_wait_reason(record)
                if volume_wait_reason:
                    return _result(WAIT_VOLUME_STATUS, "wait", volume_wait_reason, **kwargs)
                if confidence is not None and confidence < 70:
                    return _result(WAIT_TRIGGER_STATUS, "wait", f"資料完整度僅 {confidence:.0f}%，暫不執行。", **kwargs)
                actual_rrr = _reward_risk_at_price(close, stop, target)
                if actual_rrr is None or actual_rrr < MIN_EFFECTIVE_REWARD_RISK:
                    return _result(
                        WAIT_PULLBACK_STATUS,
                        "wait",
                        f"現價風險報酬比未達 {MIN_EFFECTIVE_REWARD_RISK:.1f}，等待更佳價格。",
                        **kwargs,
                    )
                return _result(READY_STATUS, "ready", build_entry_summary(record), **kwargs)
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
        level_values = _levels(
            ma20,
            ma20 + atr * 0.5,
            atr,
            no_chase,
            stop_atr_mult=stop_atr_mult,
            reward_risk=reward_risk,
        )
        if level_values:
            return _result(WAIT_PULLBACK_STATUS, "wait", overheat_reason, plan_type=plan_type, no_chase=no_chase,
                           low=level_values[0], high=level_values[1], stop=level_values[2], target=level_values[3],
                           rrr=reward_risk)
        return _result(WAIT_PULLBACK_STATUS, "wait", overheat_reason, no_chase=no_chase)

    if pattern in _BREAKOUT_PATTERNS:
        plan_type = "breakout"
        level_values = _levels(
            high_price,
            high_price + atr * 0.5,
            atr,
            no_chase,
            stop_atr_mult=stop_atr_mult,
            reward_risk=reward_risk,
        )
        if not level_values:
            pullback = _levels(
                ma20,
                ma20 + atr * 0.5,
                atr,
                no_chase,
                stop_atr_mult=stop_atr_mult,
                reward_risk=reward_risk,
            )
            if pullback:
                return _result(WAIT_PULLBACK_STATUS, "wait", "突破觸發價已超過禁止追高價，改等回測。",
                               plan_type="pullback", no_chase=no_chase, low=pullback[0], high=pullback[1],
                               stop=pullback[2], target=pullback[3], rrr=reward_risk)
            return _result(WAIT_PULLBACK_STATUS, "wait", "突破觸發價已超過禁止追高價。", no_chase=no_chase)
        return _result(WAIT_TRIGGER_STATUS, "wait", "突破今日高點且量能延續後，才進入可執行區間。",
                       plan_type=plan_type, no_chase=no_chase, low=level_values[0], high=level_values[1],
                       stop=level_values[2], target=level_values[3], rrr=reward_risk)

    level_values = _levels(
        ma20,
        ma20 + atr * 0.5,
        atr,
        no_chase,
        stop_atr_mult=stop_atr_mult,
        reward_risk=reward_risk,
    )
    if not level_values:
        return _result(WAIT_PULLBACK_STATUS, "wait", "目前無法建立風險報酬合理的區間。", no_chase=no_chase)
    level_kwargs: dict[str, Any] = dict(
        plan_type="pullback",
        no_chase=no_chase,
        low=level_values[0],
        high=level_values[1],
        stop=level_values[2],
        target=level_values[3],
        rrr=reward_risk,
    )
    if execution_risk_reason:
        return _result(WAIT_TRIGGER_STATUS, "wait", execution_risk_reason, **level_kwargs)
    if conflict == "高":
        return _result(WAIT_TRIGGER_STATUS, "wait", "多空訊號衝突偏高，等待重新確認。", **level_kwargs)
    if level_values[0] <= close <= level_values[1]:
        volume_wait_reason = _volume_wait_reason(record)
        if volume_wait_reason:
            return _result(WAIT_VOLUME_STATUS, "wait", volume_wait_reason, **level_kwargs)
        if confidence is not None and confidence < 70:
            return _result(WAIT_TRIGGER_STATUS, "wait", f"價格已進區間，但資料完整度僅 {confidence:.0f}%。", **level_kwargs)
        actual_rrr = _reward_risk_at_price(close, level_values[2], level_values[3])
        if actual_rrr is None or actual_rrr < MIN_EFFECTIVE_REWARD_RISK:
            return _result(
                WAIT_PULLBACK_STATUS,
                "wait",
                f"現價風險報酬比未達 {MIN_EFFECTIVE_REWARD_RISK:.1f}，等待更佳價格。",
                **level_kwargs,
            )
        return _result(READY_STATUS, "ready", build_entry_summary(record), **level_kwargs)
    if close > level_values[1]:
        return _result(WAIT_PULLBACK_STATUS, "wait", "價格仍高於 20MA 回測區，不追價。", **level_kwargs)
    return _result(WAIT_TRIGGER_STATUS, "wait", "價格尚未站回 20MA 觀察區。", **level_kwargs)


def ensure_entry_readiness(record: Mapping[str, Any]) -> dict[str, Any]:
    """Preserve current-schema records and conservatively annotate legacy rows."""
    result = dict(record)
    if _number(result.get("Entry_Schema")) == ENTRY_SCHEMA_VERSION and result.get("Entry_Status"):
        return result
    result.update(build_entry_readiness(result))
    return result
