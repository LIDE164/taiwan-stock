"""Independent, date-bounded replay of the technical model used on 2026-09-15.

Display evidence only: never replace executable_v3 fields or trading gates.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

import legacy_backtest_core as core

LEGACY_BACKTEST_SCHEMA = "legacy_2026_09_15"
LEGACY_BACKTEST_COMMIT = "027f459fdea2a79d04c77979f74c0b936cdbe370"
logger = logging.getLogger(__name__)


def calculate_legacy_backtest(frame: pd.DataFrame, *, as_of_date: str) -> dict[str, Any]:
    """Rebuild old indicators from OHLCV; missing/invalid data stays unavailable."""
    snapshot: dict[str, Any] = {
        "schema": LEGACY_BACKTEST_SCHEMA,
        "source_commit": LEGACY_BACKTEST_COMMIT,
        "as_of_date": str(as_of_date),
        "status": "unavailable",
        "lookback_days": core.BACKTEST_LOOKBACK_DAYS,
        "scope": core.BACKTEST_SCOPE,
    }
    try:
        cutoff = pd.Timestamp(as_of_date)
        if pd.isna(cutoff) or cutoff.strftime("%Y-%m-%d") != str(as_of_date):
            raise ValueError("as_of_date must be YYYY-MM-DD")
        if frame is None or frame.empty:
            snapshot["status"] = "insufficient_history"
            return snapshot
        if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.hasnans:
            raise ValueError("dated OHLCV history is required")
        dates = frame.index.tz_localize(None).normalize()
        history = frame.loc[dates <= cutoff, ["Open", "High", "Low", "Close", "Volume"]].copy()
        history.index = dates[dates <= cutoff]
        history = history.sort_index()
        if history.index.has_duplicates:
            raise ValueError("duplicate daily bars")
        snapshot["history_bars"] = len(history)
        if history.empty:
            snapshot["status"] = "insufficient_history"
            return snapshot
        snapshot["data_through"] = history.index[-1].strftime("%Y-%m-%d")
        if snapshot["data_through"] != str(as_of_date):
            snapshot["status"] = "stale_history"
            return snapshot
        history = history.apply(pd.to_numeric, errors="coerce")
        prices = history[["Open", "High", "Low", "Close"]]
        # Adjusted quotes can differ by one floating-point ULP at the candle
        # boundary. Accept numerical noise only; never alter or round prices.
        tolerance = prices.abs().max(axis=1).clip(lower=1) * np.finfo(float).eps * 8
        if (
            not np.isfinite(history.to_numpy(dtype=float)).all()
            or (prices <= 0).any().any() or (history["Volume"] < 0).any()
            or (history["High"] < prices.max(axis=1) - tolerance).any()
            or (history["Low"] > prices.min(axis=1) + tolerance).any()
        ):
            raise ValueError("invalid OHLCV history")
        if len(history) < 21:
            snapshot["status"] = "insufficient_history"
            return snapshot
        stats = core.calculate_historical_performance(core.apply_technical_indicators(history))
        count = int(stats["closed_signals"])
        snapshot.update({
            "status": "complete", "samples": count,
            "wins": int(stats["wins"]), "losses": int(stats["losses"]),
            "win_rate": float(stats["win_rate"]) if count else None,
            "raw_win_rate": float(stats["raw_win_rate"]) if count else None,
            "validation_samples": int(stats["validation_samples"]),
            "validation_win_rate": float(stats["validation_win_rate"]) if count else None,
        })
    except Exception as exc:
        # Legacy display must never abort the independent current scan.
        snapshot["status"] = "invalid_history"
        logger.warning("Legacy backtest unavailable (%s)", type(exc).__name__)
    return snapshot
