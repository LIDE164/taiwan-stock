import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


logger = logging.getLogger(__name__)

BACKTEST_LOOKBACK_DAYS = 90
BACKTEST_HOLD_DAYS = 9
BACKTEST_MIN_GAP_DAYS = 5
BACKTEST_SCORE_THRESHOLD = 60


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or pd.isna(value):
            return default
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return default


def apply_technical_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add the indicators used by the scanner, chart, and Streamlit app."""
    df = df.copy()
    if df.empty:
        return df

    for col in ["Open", "High", "Low", "Close", "Volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    close = df["Close"]
    high = df["High"]
    low = df["Low"]

    df["5MA"] = close.rolling(5).mean()
    df["10MA"] = close.rolling(10).mean()
    df["20MA"] = close.rolling(20).mean()
    df["60MA"] = close.rolling(60).mean()

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df["MACD"] = ema12 - ema26
    df["Signal"] = df["MACD"].ewm(span=9, adjust=False).mean()
    df["MACD_Hist"] = df["MACD"] - df["Signal"]

    df["STD20"] = close.rolling(20).std()
    df["BB_UP"] = df["20MA"] + 2 * df["STD20"]
    df["BB_DN"] = df["20MA"] - 2 * df["STD20"]
    df["BIAS_20"] = ((close - df["20MA"]) / df["20MA"].replace(0, np.nan) * 100).fillna(0)
    df["BIAS"] = df["BIAS_20"]

    low_9 = low.rolling(9).min()
    high_9 = high.rolling(9).max()
    rsv = ((close - low_9) / (high_9 - low_9).replace(0, np.nan) * 100).fillna(50)
    df["K"] = rsv.ewm(com=2, adjust=False).mean()
    df["D"] = df["K"].ewm(com=2, adjust=False).mean()
    df["J"] = 3 * df["K"] - 2 * df["D"]

    delta = close.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    ema_up = up.ewm(com=13, adjust=False).mean()
    ema_down = down.ewm(com=13, adjust=False).mean()
    rs = ema_up / ema_down.replace(0, np.nan)
    df["RSI"] = (100 - (100 / (1 + rs))).fillna(50)

    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    df["TR"] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df["ATR"] = df["TR"].rolling(14).mean().bfill().fillna(close * 0.03)

    up_move = high - high.shift(1)
    down_move = low.shift(1) - low
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    atr = df["ATR"].replace(0, np.nan)
    plus_di = 100 * (pd.Series(plus_dm, index=df.index).ewm(span=14, adjust=False).mean() / atr)
    minus_di = 100 * (pd.Series(minus_dm, index=df.index).ewm(span=14, adjust=False).mean() / atr)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    df["ADX"] = dx.ewm(span=14, adjust=False).mean().bfill().fillna(20)

    return df


def build_score_input(
    df: pd.DataFrame,
    fund: Optional[Dict[str, Any]] = None,
    *,
    index: int = -1,
) -> Dict[str, Any]:
    fund = fund or {}
    if df is None or len(df) < 2:
        return {}

    end_pos = len(df) + index + 1 if index < 0 else index + 1
    end_pos = max(0, min(len(df), end_pos))
    work_df = df.iloc[:end_pos]
    if len(work_df) < 2:
        return {}

    t = work_df.iloc[-1]
    p = work_df.iloc[-2]
    t_close = safe_float(t.get("Close"))
    t_open = safe_float(t.get("Open"), t_close)
    t_high = safe_float(t.get("High"), t_close)
    t_low = safe_float(t.get("Low"), t_close)
    p_close = safe_float(p.get("Close"), t_close)
    p_open = safe_float(p.get("Open"), p_close)
    body_len = abs(t_close - t_open)
    volume_avg_5 = safe_float(work_df["Volume"].tail(5).mean()) if "Volume" in work_df else 0.0

    red_engulfing = (
        p_open > p_close
        and t_close > t_open
        and t_close > p_open
        and t_open < p_close
    )
    black_engulfing = (
        p_close > p_open
        and t_open > t_close
        and t_open > p_close
        and t_close < p_open
    )
    has_support = (
        min(t_close, t_open) - t_low > body_len * 1.5
        and safe_float(t.get("Volume")) > volume_avg_5
    )
    hit_pressure = t_high - max(t_close, t_open) > body_len * 1.5
    vol_ratio = safe_float(t.get("Volume")) / volume_avg_5 if volume_avg_5 > 0 else 0.0
    box_high, box_low, box_range_pct = 0.0, 0.0, 0.0
    box_breakout = False
    breakout_volume_ok = False
    if len(work_df) >= 11:
        box_window = work_df.iloc[-11:-1]
        box_high = safe_float(box_window["High"].max())
        box_low = safe_float(box_window["Low"].min())
        box_range_pct = (box_high - box_low) / box_low * 100 if box_low > 0 else 0.0
        breakout_volume_ok = vol_ratio >= 1.2
        box_breakout = bool(box_range_pct < 12 and t_close > box_high and breakout_volume_ok and not hit_pressure)
    roc_20 = 0.0
    if len(work_df) >= 20:
        base = safe_float(work_df["Close"].iloc[-20])
        if base:
            roc_20 = (t_close - base) / base * 100
    ma5_up_today = bool(len(work_df) >= 6 and t_close > safe_float(work_df["Close"].iloc[-6], t_close))
    tomorrow_turn_price = safe_float(work_df["Close"].iloc[-4], t_close) if len(work_df) >= 4 else t_close

    trend_quality = 0
    if t_close > safe_float(t.get("20MA"), t_close):
        trend_quality += 1
    if safe_float(t.get("20MA"), t_close) > safe_float(t.get("60MA"), t_close):
        trend_quality += 1
    if safe_float(t.get("MACD_Hist")) > safe_float(p.get("MACD_Hist")):
        trend_quality += 1
    if safe_float(t.get("ADX")) >= 25:
        trend_quality += 1
    momentum_score = round((trend_quality / 4) * 100, 1)

    bullish_count = sum([
        t_close > safe_float(t.get("20MA"), t_close),
        safe_float(t.get("MACD_Hist")) > safe_float(p.get("MACD_Hist")),
        safe_float(t.get("Volume")) > volume_avg_5 * 1.1 if volume_avg_5 > 0 else False,
        red_engulfing,
        has_support,
        ma5_up_today,
    ])
    bearish_count = sum([
        t_close < safe_float(t.get("20MA"), t_close),
        safe_float(t.get("MACD_Hist")) <= safe_float(p.get("MACD_Hist")),
        safe_float(t.get("RSI"), 50) >= 75,
        black_engulfing,
        hit_pressure,
        t_close < tomorrow_turn_price if tomorrow_turn_price > 0 else False,
    ])
    conflict_score = min(bullish_count, bearish_count) / max(bullish_count, bearish_count, 1)
    if conflict_score >= 0.55:
        signal_conflict = "高"
    elif conflict_score >= 0.3:
        signal_conflict = "中"
    else:
        signal_conflict = "低"

    if hit_pressure and safe_float(t.get("RSI"), 50) >= 75:
        entry_pattern = "過熱追高型"
    elif t_close > safe_float(t.get("20MA"), t_close) and safe_float(t.get("Volume")) > volume_avg_5 * 1.5 and safe_float(t.get("MACD_Hist")) > safe_float(p.get("MACD_Hist")):
        entry_pattern = "趨勢突破型"
    elif has_support and t_close > safe_float(t.get("20MA"), t_close):
        entry_pattern = "回測支撐型"
    elif safe_float(t.get("RSI"), 50) <= 35 and t_close > p_close:
        entry_pattern = "低檔反彈型"
    elif t_close > safe_float(t.get("20MA"), t_close) and hit_pressure:
        entry_pattern = "假突破風險型"
    else:
        entry_pattern = "一般觀察型"
    if box_breakout and entry_pattern == "一般觀察型":
        entry_pattern = "整理突破型"
    confidence = max(45, int(100 - conflict_score * 30 - (10 if entry_pattern in ["過熱追高型", "假突破風險型"] else 0)))
    ma20_val = safe_float(t.get("20MA"), t_close)
    tomorrow_plan = {
        "明日觸發": f"突破今日高點 {t_high:.2f} 且量能延續",
        "觀察支撐": f"收盤 {t_close:.2f} / 20MA {ma20_val:.2f}",
        "失效價": f"跌破 {min(t_low, ma20_val):.2f}",
        "禁止追高價": f"開高超過 {t_close * 1.035:.2f} 不追",
        "建議型態": entry_pattern,
    }

    return {
        "ADX": safe_float(t.get("ADX")),
        "ROC_20": roc_20,
        "訊號": t_close > safe_float(t.get("20MA"), t_close),
        "收盤價": t_close,
        "BB_DN": safe_float(t.get("BB_DN"), t_close),
        "BB_UP": safe_float(t.get("BB_UP"), t_close),
        "BIAS": safe_float(t.get("BIAS_20", t.get("BIAS", 0))),
        "MoM": safe_float(fund.get("MoM")),
        "YoY": safe_float(fund.get("YoY")),
        "成交量": safe_float(t.get("Volume")),
        "5日均量": volume_avg_5,
        "MACD柱": safe_float(t.get("MACD_Hist")),
        "前日MACD柱": safe_float(p.get("MACD_Hist")),
        "紅吞": red_engulfing,
        "黑吞": black_engulfing,
        "回測有撐": has_support,
        "反彈遇壓": hit_pressure,
        "5MA": safe_float(t.get("5MA"), t_close),
        "20MA": safe_float(t.get("20MA"), t_close),
        "5MA已上彎": ma5_up_today,
        "明日5MA扣抵價": round(tomorrow_turn_price, 2),
        "5日線即將上彎": ma5_up_today,
        "J值": safe_float(t.get("J"), 50),
        "RSI": safe_float(t.get("RSI"), 50),
        "Momentum_Score": momentum_score,
        "Est_Vol_Ratio": round(vol_ratio, 2),
        "Volume_Confirmed": True,
        "Bullish_Count": bullish_count,
        "Bearish_Count": bearish_count,
        "Signal_Conflict": signal_conflict,
        "Conflict_Score": round(conflict_score, 2),
        "Entry_Pattern": entry_pattern,
        "Box_Breakout": box_breakout,
        "Box_Days": 10,
        "Box_Range_Pct": round(box_range_pct, 2),
        "Breakout_Volume_OK": breakout_volume_ok,
        "Tomorrow_Plan": tomorrow_plan,
        "Confidence": confidence,
        "Data_Quality": {"price": "ok", "volume": "confirmed", "source": "chart_history"},
    }


def is_strategy_signal(
    df_slice: pd.DataFrame,
    fund: Optional[Dict[str, Any]] = None,
    mode: str = "post",
    score_threshold: int = BACKTEST_SCORE_THRESHOLD,
) -> Tuple[bool, int]:
    if df_slice is None or len(df_slice) < 20:
        return False, 0
    from scoring import get_decision_score

    data = build_score_input(df_slice, fund or {})
    score, _, _, _ = get_decision_score(data, fund or {}, mode=mode, with_reason=False)
    return score >= score_threshold, score


def summarize_winrate(wins: int, total: int) -> Dict[str, Any]:
    if total <= 0:
        return {
            "raw_win_rate": 0.0,
            "adjusted_win_rate": 0.0,
            "wilson_low": 0.0,
            "wilson_high": 0.0,
            "sample_confidence": "無樣本",
        }

    raw = wins / total
    prior_wins = 5
    prior_total = 10
    bayes = (wins + prior_wins) / (total + prior_total)
    reliability = min(1.0, total / 30)
    adjusted = bayes * (1 - reliability) + raw * reliability

    z = 1.96
    denom = 1 + z * z / total
    center = (raw + z * z / (2 * total)) / denom
    margin = z * ((raw * (1 - raw) / total + z * z / (4 * total * total)) ** 0.5) / denom
    if total < 10:
        sample_confidence = "低"
    elif total < 30:
        sample_confidence = "中"
    else:
        sample_confidence = "高"

    return {
        "raw_win_rate": round(raw * 100, 1),
        "adjusted_win_rate": round(adjusted * 100, 1),
        "wilson_low": round(max(0.0, center - margin) * 100, 1),
        "wilson_high": round(min(1.0, center + margin) * 100, 1),
        "sample_confidence": sample_confidence,
    }


def _trade_result(
    future_df: pd.DataFrame,
    target_price: float,
    stop_price: float,
    entry_price: float,
    *,
    fee_rate: float,
) -> Optional[Dict[str, Any]]:
    last_close = entry_price
    exit_price = entry_price
    exit_reason = "未出場"
    holding_days = 0
    for _, row in future_df.iterrows():
        holding_days += 1
        open_price = safe_float(row.get("Open"), last_close)
        low = safe_float(row.get("Low"))
        high = safe_float(row.get("High"))
        close = safe_float(row.get("Close"))

        if open_price <= stop_price:
            exit_price = open_price
            exit_reason = "跳空停損"
            break
        if open_price >= target_price:
            exit_price = open_price
            exit_reason = "跳空停利"
            break

        hit_stop = low <= stop_price
        hit_target = high >= target_price
        if hit_stop and hit_target:
            exit_price = stop_price
            exit_reason = "同日先算停損"
            break
        if hit_stop:
            exit_price = stop_price
            exit_reason = "停損"
            break
        if hit_target:
            exit_price = target_price
            exit_reason = "停利"
            break

        last_close = close
        exit_price = close

    if future_df.empty:
        return None

    net_return = ((exit_price * (1 - fee_rate)) - (entry_price * (1 + fee_rate))) / (entry_price * (1 + fee_rate)) * 100
    return {
        "win": net_return > 0,
        "return_pct": round(net_return, 2),
        "entry_price": round(entry_price, 2),
        "exit_price": round(exit_price, 2),
        "target_price": round(target_price, 2),
        "stop_price": round(stop_price, 2),
        "exit_reason": exit_reason,
        "holding_days": holding_days,
    }


def _trade_outcome(
    future_df: pd.DataFrame,
    target_price: float,
    stop_price: float,
    entry_price: float,
    *,
    fee_rate: float,
) -> Optional[bool]:
    result = _trade_result(
        future_df,
        target_price,
        stop_price,
        entry_price,
        fee_rate=fee_rate,
    )
    return None if result is None else bool(result["win"])


def calculate_historical_performance(
    df_slice: pd.DataFrame,
    target_mult: float = 1.5,
    stop_mult: float = 1.0,
    *,
    lookback_days: int = BACKTEST_LOOKBACK_DAYS,
    hold_days: int = BACKTEST_HOLD_DAYS,
    min_gap_days: int = BACKTEST_MIN_GAP_DAYS,
    fund: Optional[Dict[str, Any]] = None,
    score_threshold: int = BACKTEST_SCORE_THRESHOLD,
    fee_rate: float = 0.001425,
    slippage_rate: float = 0.0005,
) -> Dict[str, Any]:
    empty = {
        "win_rate": 0.0,
        "closed_signals": 0,
        "wins": 0,
        "losses": 0,
        "buy_dates": [],
        "avg_return": 0.0,
        "max_drawdown": 0.0,
        "max_consecutive_losses": 0,
        "raw_win_rate": 0.0,
        "adjusted_win_rate": 0.0,
        "wilson_low": 0.0,
        "wilson_high": 0.0,
        "sample_confidence": "無樣本",
        "trades": [],
    }
    if df_slice is None or len(df_slice) < 21:
        return empty

    recent = df_slice.tail(lookback_days)
    last_buy_idx = -999
    start_idx = len(df_slice) - len(recent)
    trades: List[Dict[str, Any]] = []
    buy_dates: List[Any] = []

    for idx in range(len(recent)):
        actual_idx = start_idx + idx
        if actual_idx - last_buy_idx < min_gap_days:
            continue
        if actual_idx + 1 >= len(df_slice):
            continue

        temp_df = df_slice.iloc[: actual_idx + 1]
        signal, score = is_strategy_signal(temp_df, fund or {}, score_threshold=score_threshold)
        if not signal:
            continue

        signal_row = temp_df.iloc[-1]
        entry_idx = actual_idx + 1
        entry_row = df_slice.iloc[entry_idx]
        raw_entry_price = safe_float(entry_row.get("Open"), safe_float(entry_row.get("Close")))
        entry_price = raw_entry_price * (1 + slippage_rate)
        atr_val = safe_float(signal_row.get("ATR"), entry_price * 0.03)
        if entry_price <= 0 or atr_val <= 0:
            continue

        target_price = entry_price + atr_val * target_mult
        stop_price = entry_price - atr_val * stop_mult
        future_df = df_slice.iloc[entry_idx : entry_idx + hold_days]
        result = _trade_result(
            future_df,
            target_price,
            stop_price,
            entry_price,
            fee_rate=fee_rate,
        )
        if result is None:
            continue

        last_buy_idx = entry_idx
        result["signal_date"] = df_slice.index[actual_idx]
        result["entry_date"] = df_slice.index[entry_idx]
        result["score"] = score
        trades.append(result)
        buy_dates.append(df_slice.index[entry_idx])

    if not trades:
        return empty

    wins = sum(1 for t in trades if t["win"])
    losses = len(trades) - wins
    returns = [safe_float(t.get("return_pct")) for t in trades]
    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    max_consecutive_losses = 0
    current_losses = 0
    for ret in returns:
        equity *= 1 + ret / 100
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - equity) / peak * 100)
        if ret <= 0:
            current_losses += 1
            max_consecutive_losses = max(max_consecutive_losses, current_losses)
    else:
            current_losses = 0
    winrate_stats = summarize_winrate(wins, len(trades))

    return {
        "win_rate": winrate_stats["adjusted_win_rate"],
        "closed_signals": len(trades),
        "wins": wins,
        "losses": losses,
        "buy_dates": buy_dates,
        "avg_return": round(float(np.mean(returns)), 2),
        "max_drawdown": round(max_drawdown, 2),
        "max_consecutive_losses": max_consecutive_losses,
        **winrate_stats,
        "trades": trades,
    }


def calculate_historical_winrate(
    df_slice: pd.DataFrame,
    target_mult: float = 1.5,
    stop_mult: float = 1.0,
    *,
    lookback_days: int = BACKTEST_LOOKBACK_DAYS,
    hold_days: int = BACKTEST_HOLD_DAYS,
    min_gap_days: int = BACKTEST_MIN_GAP_DAYS,
    fund: Optional[Dict[str, Any]] = None,
    score_threshold: int = BACKTEST_SCORE_THRESHOLD,
    fee_rate: float = 0.001425,
    slippage_rate: float = 0.0005,
) -> Tuple[float, int, int, List[Any]]:
    result = calculate_historical_performance(
        df_slice,
        target_mult,
        stop_mult,
        lookback_days=lookback_days,
        hold_days=hold_days,
        min_gap_days=min_gap_days,
        fund=fund,
        score_threshold=score_threshold,
        fee_rate=fee_rate,
        slippage_rate=slippage_rate,
    )
    return result["win_rate"], result["closed_signals"], result["wins"], result["buy_dates"]
