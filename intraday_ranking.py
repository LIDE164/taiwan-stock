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


def support_data_from_postclose_record(
    record: Mapping[str, Any] | None,
    *,
    current_price: float = 0.0,
) -> dict[str, Any]:
    """Reuse only reported post-close fundamentals/chip metadata during live scoring."""
    baseline = dict(record or {})
    eps_number = _number(baseline.get("EPS"))
    eps_value: float | str = eps_number if eps_number is not None else "無"
    industry = str(baseline.get("產業") or "一般產業")
    has_industry = industry not in ("一般產業", "無")
    fundamental_status = "ok" if eps_number is not None and has_industry else (
        "partial" if eps_number is not None or has_industry else "missing"
    )

    mom = _number(baseline.get("MoM"))
    yoy = _number(baseline.get("YoY"))
    revenue_source = str(baseline.get("Revenue_Source") or "")
    revenue_status = str(baseline.get("Revenue_Status") or "").lower()
    if revenue_status not in {"ok", "partial", "missing", "error"}:
        revenue_status = "ok" if revenue_source and (mom is not None or yoy is not None) else "missing"

    institutional_days = int(_number(baseline.get("Institutional_Days")) or 0)
    institutional_status = str(baseline.get("Institutional_Status") or "").lower()
    if institutional_status not in {"ok", "partial", "missing", "error"}:
        institutional_status = (
            "ok" if institutional_days > 0 and _number(baseline.get("Whale_Net")) is not None else "missing"
        )

    pe_value: float | str = "無"
    price_number = _number(current_price)
    if eps_number is not None and eps_number > 0 and price_number is not None and price_number > 0:
        pe_value = round(price_number / eps_number, 2)
    return {
        "EPS": eps_value,
        "EPS_Period": str(baseline.get("EPS_Period") or "missing"),
        "PE": pe_value,
        "Industry": industry,
        "_status": fundamental_status,
        "MoM": mom,
        "YoY": yoy,
        "Revenue_Period": str(baseline.get("Revenue_Period") or ""),
        "Revenue_Source": revenue_source,
        "_data_status": {"revenue": revenue_status},
        "_institutional_status": institutional_status,
        "Institutional_Source": str(baseline.get("Institutional_Source") or ""),
    }


def institutional_aggregate_from_record(
    record: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Expose a reported aggregate without inventing unavailable daily breakdowns."""
    baseline = dict(record or {})
    net = _number(baseline.get("Whale_Net"))
    days = int(_number(baseline.get("Whale_Net_Days")) or 0)
    if net is None or days <= 0:
        return None
    return {
        "net": int(net) if net.is_integer() else round(net, 2),
        "days": days,
        "source": str(baseline.get("Institutional_Source") or ""),
        "status": str(baseline.get("Institutional_Status") or "unknown"),
    }


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
    quote_source = str(result.get("Intraday_Quote_Source") or "").strip()
    source_suffix = f"；{quote_source}" if quote_source else ""
    if live_score is not None and original_score is not None:
        result["Score_Source"] = (
            f"盤中重新評分（盤後 {original_score:g} → 盤中 {live_score:g}{source_suffix}）"
        )
    else:
        result["Score_Source"] = "盤中重新評分"
    return result
