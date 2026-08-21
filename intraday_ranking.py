"""Helpers for re-scoring a post-close ranking during the live session."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from app_security import normalize_ticker


def _number(value: Any) -> float | None:
    try:
        parsed = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def original_ranking_targets(
    records: Sequence[Mapping[str, Any]],
    limit: int | None = None,
) -> list[str]:
    """Keep the original ranking order and exclude invalid or duplicate tickers."""
    targets: list[str] = []
    seen: set[str] = set()
    target_limit = None if limit is None else max(0, int(limit))
    for row in records:
        if target_limit is not None and len(targets) >= target_limit:
            break
        if not isinstance(row, Mapping):
            continue
        ticker = normalize_ticker(row.get("代號", ""))
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        targets.append(ticker)
    return targets


def annotate_intraday_score(
    original: Mapping[str, Any] | None,
    recalculated: Mapping[str, Any],
) -> dict[str, Any]:
    """Merge a live result with its post-close baseline and expose the score delta."""
    baseline = dict(original or {})
    result = dict(baseline)
    result.update(dict(recalculated))

    original_score = _number(baseline.get("Score"))
    live_score = _number(result.get("Score"))
    result["Original_Score"] = original_score
    result["Score_Diff"] = (
        round(live_score - original_score, 2)
        if live_score is not None and original_score is not None
        else None
    )
    result["Intraday_Rescored"] = live_score is not None
    result["Score_Mode"] = "盤中參考分數"
    result["Score_Mode_Raw"] = "realtime"
    if live_score is not None and original_score is not None:
        result["Score_Source"] = f"盤中重新評分（盤後 {original_score:g} → 盤中 {live_score:g}）"
    else:
        result["Score_Source"] = "盤中重新評分"
    return result
