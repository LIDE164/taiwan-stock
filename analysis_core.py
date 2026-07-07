import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


logger = logging.getLogger(__name__)


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

    t = df.iloc[index]
    p = df.iloc[index - 1]
    t_close = safe_float(t.get("Close"))
    t_open = safe_float(t.get("Open"), t_close)
    t_high = safe_float(t.get("High"), t_close)
    t_low = safe_float(t.get("Low"), t_close)
    p_close = safe_float(p.get("Close"), t_close)
    p_open = safe_float(p.get("Open"), p_close)
    body_len = abs(t_close - t_open)
    volume_avg_5 = safe_float(df["Volume"].tail(5).mean()) if "Volume" in df else 0.0

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
    roc_20 = 0.0
    if len(df) >= 20:
        base = safe_float(df["Close"].iloc[-20])
        if base:
            roc_20 = (t_close - base) / base * 100

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
        "5日線即將上彎": t_close >= safe_float(df["Close"].iloc[-5], t_close) if len(df) >= 5 else False,
        "J值": safe_float(t.get("J"), 50),
    }


def is_strategy_signal(
    df_slice: pd.DataFrame,
    fund: Optional[Dict[str, Any]] = None,
    mode: str = "post",
    score_threshold: int = 60,
) -> Tuple[bool, int]:
    if df_slice is None or len(df_slice) < 20:
        return False, 0
    from scoring import get_decision_score

    data = build_score_input(df_slice, fund or {})
    score, _, _, _ = get_decision_score(data, fund or {}, mode=mode, with_reason=False)
    return score >= score_threshold, score


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
    lookback_days: int = 90,
    hold_days: int = 9,
    min_gap_days: int = 5,
    fund: Optional[Dict[str, Any]] = None,
    score_threshold: int = 60,
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

    return {
        "win_rate": round(wins / len(trades) * 100, 1),
        "closed_signals": len(trades),
        "wins": wins,
        "losses": losses,
        "buy_dates": buy_dates,
        "avg_return": round(float(np.mean(returns)), 2),
        "max_drawdown": round(max_drawdown, 2),
        "max_consecutive_losses": max_consecutive_losses,
        "trades": trades,
    }


def calculate_historical_winrate(
    df_slice: pd.DataFrame,
    target_mult: float = 1.5,
    stop_mult: float = 1.0,
    *,
    lookback_days: int = 90,
    hold_days: int = 9,
    min_gap_days: int = 5,
    fund: Optional[Dict[str, Any]] = None,
    score_threshold: int = 60,
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
