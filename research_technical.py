"""Read-only, dated technical research; never changes ranking or tracker rules.

Prices supplied by callers are observations, not generated or forward-filled.
Synthetic prices belong only in tests.  Custom strategies are research baselines,
not fitted models and not substitutes for the production executable strategy.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from execution_costs import (
    DEFAULT_MAX_LOSS_PER_TRADE,
    DEFAULT_TAIWAN_STOCK_COST_MODEL,
    calculate_max_odd_lot_position,
    estimate_round_trip_net_profit,
)


STRATEGY_LABELS = {
    "existing": "現行可執行策略（唯讀重算）",
    "ma_cross": "20MA 向上穿越 60MA（研究基準）",
    "rsi_rebound": "RSI14 向上穿越 30（反彈，不是背離）",
}
MIN_DISPLAY_SAMPLES = 30
HOLD_SESSIONS = 9


def _date(value: Any) -> pd.Timestamp:
    result = pd.Timestamp(value)
    if pd.isna(result):
        raise ValueError("分析日期無效")
    if result.tzinfo is not None:
        result = result.tz_convert("Asia/Taipei").tz_localize(None)
    return result.normalize()


def _observations(frame: pd.DataFrame, as_of: Any) -> tuple[pd.DataFrame, pd.Timestamp]:
    cutoff = _date(as_of)
    required = ["Open", "High", "Low", "Close", "Volume"]
    if not isinstance(frame, pd.DataFrame) or not set(required).issubset(frame.columns):
        raise ValueError("缺少真實 OHLCV 欄位，無法分析")
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.hasnans:
        raise ValueError("行情必須具有交易日日期索引")
    result = frame[required].copy(deep=True)
    if result.index.tz is not None:
        result.index = result.index.tz_convert("Asia/Taipei").tz_localize(None)
    result.index = result.index.normalize()
    result = result.loc[result.index <= cutoff].sort_index()
    if result.empty or result.index.hasnans or result.index.has_duplicates:
        raise ValueError("日期範圍內無行情，或含無效／重複交易日")
    result = result.apply(pd.to_numeric, errors="coerce")
    values = result.to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("行情含缺值或非有限數值；不補零、不補造價格")
    if (result[["Open", "High", "Low", "Close"]] <= 0).any().any() or (result.Volume < 0).any():
        raise ValueError("行情含無效價格或成交量")
    # Permit only machine precision noise, not erroneous candle envelopes.
    tolerance = np.maximum(result.High.to_numpy(), 1.0) * np.finfo(float).eps * 8
    if (
        (result.High.to_numpy() + tolerance < result[["Open", "Low", "Close"]].max(axis=1).to_numpy()).any()
        or (result.Low.to_numpy() - tolerance > result[["Open", "High", "Close"]].min(axis=1).to_numpy()).any()
    ):
        raise ValueError("OHLC 高低價格矛盾，無法分析")
    return result, cutoff


def _indicators(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    close = result.Close
    result["ma20"] = close.rolling(20, min_periods=20).mean()
    result["ma60"] = close.rolling(60, min_periods=60).mean()
    change = close.diff()
    gain = change.clip(lower=0).rolling(14, min_periods=14).mean()
    loss = (-change.clip(upper=0)).rolling(14, min_periods=14).mean()
    result["rsi14"] = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    result.loc[(loss == 0) & (gain > 0), "rsi14"] = 100.0
    result.loc[(loss == 0) & (gain == 0), "rsi14"] = 50.0
    result["macd"] = close.ewm(span=12, adjust=False, min_periods=12).mean() - close.ewm(span=26, adjust=False, min_periods=26).mean()
    result["macd_signal"] = result.macd.ewm(span=9, adjust=False, min_periods=9).mean()
    result["macd_hist"] = result.macd - result.macd_signal
    true_range = pd.concat([
        result.High - result.Low,
        (result.High - close.shift()).abs(),
        (result.Low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    result["atr14"] = true_range.rolling(14, min_periods=14).mean()
    return result


def _number(value: Any) -> float | None:
    return float(value) if value is not None and pd.notna(value) and math.isfinite(float(value)) else None


def _timeframe(frame: pd.DataFrame, unit: str) -> dict[str, Any]:
    fields = ["close", "ma20", "ma60", "rsi14", "macd", "macd_signal", "macd_hist", "support20", "resistance20", "support60", "resistance60", "regression_slope20_pct"]
    result: dict[str, Any] = {field: None for field in fields}
    result.update(status="insufficient_history", bars=len(frame), date=None, trend="資料不足", signal="觀察，資料不足", conditions=[], limitations=[])
    if frame.empty:
        result["limitations"] = ["沒有已結束的行情區間"]
        return result
    indicators = _indicators(frame)
    last = indicators.iloc[-1]
    result.update(date=str(frame.index[-1].date()), close=float(last.Close))
    for field in ["ma20", "ma60", "rsi14", "macd", "macd_signal", "macd_hist"]:
        result[field] = _number(last[field])
    for count in (20, 60):
        if len(frame) >= count:
            result[f"support{count}"] = float(frame.Low.tail(count).min())
            result[f"resistance{count}"] = float(frame.High.tail(count).max())
    if len(frame) >= 20:
        closes = frame.Close.tail(20).to_numpy(dtype=float)
        result["regression_slope20_pct"] = float(np.polyfit(np.arange(20), closes, 1)[0] / closes.mean() * 100)
    result["limitations"] = [f"支撐／壓力是近 20／60 個{unit}高低值，不是人工趨勢線或保證成交價格", "RSI 採 14 期簡單平均；MACD 採 EMA 12／26／9；斜率為 20 期線性回歸／平均價格"]
    if len(frame) < 60:
        result["limitations"].append(f"僅有 {len(frame)} 個{unit}，60 期趨勢不足；缺少的指標不補值")
        return result
    result["status"] = "ok"
    close, ma20, ma60 = result["close"], result["ma20"], result["ma60"]
    histogram, rsi = result["macd_hist"], result["rsi14"]
    if close > ma20 > ma60:
        result["trend"] = "多頭排列"
        result["signal"] = "持有觀察候選"
        result["conditions"] = ["收盤高於 20MA、20MA 高於 60MA；持續確認價格守穩 20MA"]
        if histogram > 0 and 50 <= rsi < 70:
            result["signal"] = "買進條件候選，仍須檢查原可執行規則"
            result["conditions"].append("MACD 柱值為正且 RSI 在 50～70 間；不是立即買入指令")
        elif rsi >= 70:
            result["conditions"].append("RSI ≥ 70，動能偏熱；等待原策略進場區，不追價")
    elif close < ma20 and histogram < 0:
        result["trend"] = "短線轉弱"
        result["signal"] = "風險退出檢查候選"
        result["conditions"] = ["收盤跌破 20MA 且 MACD 柱值為負；核對原停損，不自行改寫停損價"]
    else:
        result["trend"] = "混合／整理"
        result["signal"] = "觀察，等待方向確認"
        result["conditions"] = ["均線與動能未同步，不據此新增可執行資格"]
    return result


def analyze_timeframes(frame: pd.DataFrame, as_of: Any) -> dict[str, Any]:
    """Analyze daily observations and calendar-completed Friday-ending weeks."""
    try:
        observed, cutoff = _observations(frame, as_of)
    except (TypeError, ValueError, OverflowError) as exc:
        return {"status": "unavailable", "reason": str(exc), "daily": {}, "weekly": {}, "notes": []}
    # W-FRI period end is Friday at midnight here.  A caller must supply only
    # completed daily candles; a midweek input never creates a partial week.
    weekly = observed.resample("W-FRI").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"})
    completed_through = min(cutoff, observed.index[-1])
    weekly = weekly.loc[weekly.index <= completed_through].dropna(subset=["Open", "High", "Low", "Close"])
    # The first resampled week may start halfway through supplied history.
    if not weekly.empty and observed.index[0].weekday() != 0:
        weekly = weekly.iloc[1:]
    notes = ["只使用分析日以前真實 OHLCV；呼叫端須排除尚未收盤日", "週線只納入日曆與已供行情均已越過的週五週期；假期可能讓最新週線延後顯示；起始非週一的第一週保守排除", "訊號是研究檢查條件，不改動現有每日榜單、交易計畫或績效追蹤"]
    if observed.index[-1] < cutoff:
        notes.append(f"最新觀測為 {observed.index[-1].date()}，不是分析日的新行情；也可能因休市而沒有更新")
    return {
        "status": "ok", "as_of": str(cutoff.date()), "data_date": str(observed.index[-1].date()),
        "daily": _timeframe(observed, "交易日"), "weekly": _timeframe(weekly, "週"),
        "notes": notes,
    }


def _summary(trades: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(trades)
    wins = sum(trade["net_profit"] > 0 for trade in trades)
    raw = wins / count * 100 if count else None
    interval = None
    if count:
        probability, z = wins / count, 1.959963984540054
        denominator = 1 + z * z / count
        center = (probability + z * z / (2 * count)) / denominator
        margin = z * math.sqrt(probability * (1 - probability) / count + z * z / (4 * count * count)) / denominator
        interval = [max(0.0, (center - margin) * 100), min(100.0, (center + margin) * 100)]
    gains = sum(max(trade["net_profit"], 0) for trade in trades)
    losses = -sum(min(trade["net_profit"], 0) for trade in trades)
    equity, peak, drawdown = 1.0, 1.0, 0.0
    for trade in trades:
        equity *= 1 + trade["return_pct"] / 100
        peak = max(peak, equity)
        drawdown = max(drawdown, (peak - equity) / peak * 100)
    return {
        "samples": count, "wins": wins, "win_rate_pct": raw if count >= MIN_DISPLAY_SAMPLES else None,
        "raw_win_rate_pct": raw, "confidence_interval_pct": interval,
        "sample_note": "樣本不足 30，不顯示精確勝率" if count < MIN_DISPLAY_SAMPLES else "歷史研究比例，不是未來獲利機率",
        "profit_factor": gains / losses if losses > 0 else None,
        "profit_factor_note": "淨獲利交易金額合計／淨虧損交易金額絕對值" if losses > 0 else "無已實現虧損或無樣本，獲利因子無法估計（不顯示無限大）",
        "max_drawdown_closed_trade_pct": drawdown if count else None,
        "net_profit": sum(trade["net_profit"] for trade in trades) if count else None,
        "avg_return_pct": sum(trade["return_pct"] for trade in trades) / count if count else None,
    }


def _custom_trades(observed: pd.DataFrame, strategy: str, lookback: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    data = _indicators(observed)
    trades: list[dict[str, Any]] = []
    diagnostics = {"signals": 0, "rejected_entry": 0, "incomplete": 0, "skipped_overlap": 0}
    next_signal = 0
    for index in range(max(60, len(data) - lookback), len(data)):
        current, previous = data.iloc[index], data.iloc[index - 1]
        signal = (previous.ma20 <= previous.ma60 and current.ma20 > current.ma60) if strategy == "ma_cross" else (previous.rsi14 <= 30 < current.rsi14)
        if not signal:
            continue
        diagnostics["signals"] += 1
        if index < next_signal:
            diagnostics["skipped_overlap"] += 1
            continue
        if index + 1 >= len(data):
            diagnostics["incomplete"] += 1
            continue
        entry_index = index + 1
        entry = float(data.Open.iloc[entry_index])
        stop, target = float(current.Close - current.atr14), float(current.Close + 2 * current.atr14)
        sizing = calculate_max_odd_lot_position(entry, stop, DEFAULT_MAX_LOSS_PER_TRADE) if 0 < stop < entry < target else None
        if sizing is None or sizing.shares < 1:
            diagnostics["rejected_entry"] += 1
            continue
        exit_index, exit_price, reason = None, None, None
        for position in range(entry_index, min(len(data), entry_index + HOLD_SESSIONS)):
            bar = data.iloc[position]
            if bar.Open <= stop:
                exit_price, reason = float(bar.Open), "跳空停損"
            elif bar.Open >= target:
                exit_price, reason = float(bar.Open), "跳空停利"
            elif bar.Low <= stop:
                exit_price = stop * (1 - DEFAULT_TAIWAN_STOCK_COST_MODEL.stop_slippage_rate)
                reason = "同日先算停損" if bar.High >= target else "停損"
            elif bar.High >= target:
                exit_price, reason = target, "停利"
            elif position == entry_index + HOLD_SESSIONS - 1:
                exit_price, reason = float(bar.Close), "到期出場"
            if reason is not None:
                exit_index = position
                break
        if exit_index is None:
            diagnostics["incomplete"] += 1
            # This actual open trade occupies the remainder of the sample.
            break
        net = estimate_round_trip_net_profit(entry, exit_price, sizing.shares)
        if net is None:
            raise ValueError("交易成本計算無效")
        cost = max(entry * sizing.shares * DEFAULT_TAIWAN_STOCK_COST_MODEL.buy_commission_rate, DEFAULT_TAIWAN_STOCK_COST_MODEL.minimum_commission)
        trades.append({
            "signal_date": str(data.index[index].date()), "entry_date": str(data.index[entry_index].date()),
            "exit_date": str(data.index[exit_index].date()), "entry_price": entry, "exit_price": exit_price,
            "stop_price": stop, "target_price": target, "shares": sizing.shares,
            "planned_net_risk": sizing.estimated_net_loss, "net_profit": net,
            "return_pct": net / (entry * sizing.shares + cost) * 100, "exit_reason": reason,
            "holding_days": exit_index - entry_index + 1,
        })
        # A close-of-exit-day signal may start a new next-session trade.
        next_signal = exit_index
    return trades, diagnostics


def _existing_trades(observed: pd.DataFrame, lookback: int) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    from analysis_core import apply_technical_indicators, calculate_historical_performance

    result = calculate_historical_performance(apply_technical_indicators(observed), lookback_days=lookback)
    trades = []
    for original in result.get("trades", []):
        trade = dict(original)
        entry_date = _date(trade["entry_date"])
        entry_index = observed.index.get_loc(entry_date)
        exit_index = entry_index + int(trade["holding_days"]) - 1
        execution = _number(trade.get("execution_exit_price", trade["exit_price"]))
        if execution is None:
            raise ValueError("既有回測缺少有效成交價格")
        net = estimate_round_trip_net_profit(trade["entry_price"], execution, trade["shares"])
        if net is None or exit_index >= len(observed):
            raise ValueError("既有回測紀錄不足以核對淨損益與出場日")
        trade.update(signal_date=str(_date(trade["signal_date"]).date()), entry_date=str(entry_date.date()), exit_date=str(observed.index[exit_index].date()), net_profit=net, exit_price=execution)
        trades.append(trade)
    return trades, result.get("diagnostics", {}), result.get("backtest_scope", "現行可執行策略")


def run_research_backtest(frame: pd.DataFrame, strategy: str, as_of: Any, lookback: int = 380) -> dict[str, Any]:
    """Return completed non-overlapping technical trades, without cloud writes."""
    try:
        if strategy not in STRATEGY_LABELS:
            raise ValueError("僅支援 existing、ma_cross、rsi_rebound；RSI 反彈不等同背離")
        if isinstance(lookback, bool) or not isinstance(lookback, int) or not 30 <= lookback <= 2500:
            raise ValueError("研究期間須為 30～2500 個交易日")
        observed, cutoff = _observations(frame, as_of)
        if len(observed) < 61:
            raise ValueError("至少需要 61 個已完成交易日，保留均線暖機資料")
        if strategy == "existing":
            trades, diagnostics, scope = _existing_trades(observed, lookback)
            assumptions = [scope, "既有交易價格保存至小數兩位；淨損益與獲利因子按保存成交價格及相同成本重新核對，可能有尾差"]
        else:
            trades, diagnostics = _custom_trades(observed, strategy, lookback)
            assumptions = [STRATEGY_LABELS[strategy], "收盘確認訊號，次一交易日開盤價進場；不使用訊號日之後的資料決定訊號", "訊號日收盤減 1 ATR14 為停損、加 2 ATR14 為目標；次日開盤不在停損與目標之間則跳過", "最多持有 9 個交易日；同根 K 線同時觸及停損／停利先計停損，未完成交易排除"]
        validation_count = min(max(0, len(trades) - 1), math.ceil(len(trades) * 0.3)) if len(trades) >= 2 else 0
        training = trades[:-validation_count] if validation_count else trades
        validation = trades[-validation_count:] if validation_count else []
        return {
            "status": "ok", "strategy": strategy, "strategy_label": STRATEGY_LABELS[strategy],
            "as_of": str(cutoff.date()), "data_date": str(observed.index[-1].date()),
            "window_start": str(observed.index[max(0, len(observed) - lookback)].date()), "window_end": str(observed.index[-1].date()),
            "assumptions": assumptions + ["每筆依停損價與含成本的 5,000 元模型風險計算股數，並非總資金或保證最高虧損", "買賣手續費各 0.1425%／最低 20 元，賣出稅 0.3%；非跳空停損滑價 0.05%", "僅技術 OHLCV；無當時點財報、籌碼或新聞，不拿最新資料回補歷史"],
            "metrics": {"overall": _summary(trades), "training": _summary(training), "validation": _summary(validation)},
            "trades": trades, "diagnostics": diagnostics,
            "notes": ["訓練／驗證僅按交易日期切分：最後約 30% 已完成交易為驗證，沒有自動調參或保證樣本外優勢", "最大回撤為已平倉交易報酬連乘曲線，不含持倉內浮動虧損，不是實際帳戶／全日權益回撤", "股數按單筆風險獨立計算，未模擬總資金上限、成交量限制、停牌、漲跌停排隊或市場衝擊", "不足 30 筆只呈現樣本與 95% Wilson 區間，原始勝率僅保留研究附錄；不以此宣稱高勝率", "回測只供研究，不修改目前每日榜單與績效圖"],
        }
    except (TypeError, ValueError, OverflowError, KeyError) as exc:
        return {"status": "unavailable", "strategy": strategy, "reason": str(exc), "metrics": {}, "trades": [], "notes": []}
