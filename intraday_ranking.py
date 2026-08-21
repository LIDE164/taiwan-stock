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


def institutional_rows_from_record(
    record: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Restore only explicitly persisted daily institutional rows.

    The aggregate fallback is intentionally not split into fabricated investor
    categories.  Rows are returned only when every category was saved by the
    original provider response.
    """
    baseline = dict(record or {})
    raw_rows = baseline.get("Institutional_Rows", [])
    if not isinstance(raw_rows, list):
        return []
    fallback_source = str(baseline.get("Institutional_Source") or "")
    restored: list[dict[str, Any]] = []
    for row in raw_rows[:5]:
        if not isinstance(row, Mapping):
            continue
        foreign = _number(row.get("foreign", row.get("外資(張)")))
        trust = _number(row.get("trust", row.get("投信(張)")))
        dealer = _number(row.get("dealer", row.get("自營商(張)")))
        if any(value is None for value in (foreign, trust, dealer)):
            continue
        total = _number(row.get("total", row.get("單日合計(張)")))
        if total is None:
            total = foreign + trust + dealer
        date_text = str(row.get("date", row.get("日期", ""))).strip()
        if not date_text:
            continue
        display_date = date_text[-5:].replace("-", "/") if "-" in date_text else date_text
        restored.append({
            "日期": display_date,
            "外資(張)": int(foreign),
            "投信(張)": int(trust),
            "自營商(張)": int(dealer),
            "單日合計(張)": int(total),
            "_source": str(row.get("source", row.get("_source", fallback_source)) or fallback_source),
        })
    return restored


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
