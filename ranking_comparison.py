"""Read-only comparison of current approvals and frozen September 9 entry rules.

Comparison rows preserve the current strategy's execution fields. Only a copy
prepared explicitly for rendering may show legacy prices, and that copy is
always excluded from execution approval when it is legacy-only.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
import math
from typing import Any

from app_security import normalize_ticker
from entry_readiness import READY_STATUS
from legacy_entry_readiness import build_legacy_entry_plan


COMPARISON_BACKTEST_LABEL = "舊制技術回測；新制獨立累積"
_VERSION_LABELS = {"new": "新制", "legacy": "舊制"}


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(str(value).replace(",", ""))
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _positive_limit(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def build_comparison_rows(
    records: Sequence[Mapping[str, Any]],
    *,
    intraday: bool = False,
    limit_per_version: int = 10,
    max_per_industry: int = 2,
) -> list[dict[str, Any]]:
    """Independently select each version, then return their ticker-deduped union.

    The same current Score ranks both sets. This compares entry rules, not a
    reconstruction of the old scoring model or an invented legacy backtest.
    Only new approvals use the production industry's concentration limit. The
    September 9 selector had no industry cap; legacy rows remain comparison-only.
    """
    limit = min(10, _positive_limit(limit_per_version, "limit_per_version"))
    industry_limit = _positive_limit(max_per_industry, "max_per_industry")
    candidates: list[tuple[float, Mapping[str, Any]]] = []
    for record in records:
        if not isinstance(record, Mapping):
            continue
        ticker = normalize_ticker(record.get("代號"))
        score = _finite_number(record.get("Score"))
        if not ticker or score is None:
            continue
        candidates.append((score, record))
    candidates.sort(key=lambda item: item[0], reverse=True)

    rows: dict[str, dict[str, Any]] = {}
    for _, record in candidates:
        ticker = normalize_ticker(record.get("代號"))
        if ticker in rows:
            continue
        row = deepcopy(dict(record))
        row["代號"] = ticker
        legacy_plan = build_legacy_entry_plan(row, intraday=intraday)
        if not intraday:
            row["Legacy_Entry_Plan"] = legacy_plan
        # Preserve the saved post-close baseline even if today's live quote fails.
        row["Legacy_Entry_Evaluation"] = legacy_plan
        rows[ticker] = row

    selected: dict[str, list[str]] = {"new": [], "legacy": []}
    for version in selected:
        industry_counts: dict[str, int] = {}
        for ticker, row in rows.items():
            plan = row if version == "new" else row["Legacy_Entry_Evaluation"]
            if plan.get("Entry_Status") != READY_STATUS or plan.get("Entry_Ready") is False:
                continue
            if intraday and row.get("Intraday_Quote_Status") != "realtime":
                continue
            industry = str(row.get("產業") or "").strip()
            if version == "new" and industry and industry != "一般產業":
                if industry_counts.get(industry, 0) >= industry_limit:
                    continue
                industry_counts[industry] = industry_counts.get(industry, 0) + 1
            selected[version].append(ticker)
            if len(selected[version]) >= limit:
                break

    result: list[dict[str, Any]] = []
    for ticker, row in rows.items():
        versions = [version for version in ("new", "legacy") if ticker in selected[version]]
        if not versions:
            continue
        row["Execution_Versions"] = versions
        row["Execution_Version_Label"] = "・".join(_VERSION_LABELS[version] for version in versions)
        row["Comparison_Rank"] = len(result) + 1
        row["Comparison_Backtest_Label"] = COMPARISON_BACKTEST_LABEL
        if versions == ["legacy"] and row.get("Entry_Status") == READY_STATUS:
            row["Comparison_New_Reason"] = "新制進場條件符合，但未入選新制前 10／產業限額。"
        result.append(row)
    return result


def refresh_comparison_labels(record: Mapping[str, Any], *, intraday: bool = False) -> dict[str, Any]:
    """Revalidate cached labels after a quote change, preserving canonical plans."""
    result = deepcopy(dict(record))
    if "Execution_Versions" not in result:
        return result
    for field in (
        "Execution_Versions", "Execution_Version_Label", "Comparison_Rank",
        "Comparison_Backtest_Label", "Legacy_Entry_Evaluation",
        "Comparison_New_Reason",
    ):
        result.pop(field, None)
    refreshed = build_comparison_rows([result], intraday=intraday)
    return refreshed[0] if refreshed else result


def comparison_display_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Return a rendering-only copy; never mutate or promote a source approval."""
    result = deepcopy(dict(record))
    result["Comparison_Backtest_Label"] = COMPARISON_BACKTEST_LABEL
    versions = result.get("Execution_Versions")
    if versions != ["legacy"]:
        return result
    plan = result.get("Legacy_Entry_Evaluation", result.get("Legacy_Entry_Plan"))
    if not isinstance(plan, Mapping):
        return result
    current_reason = str(record.get("Comparison_New_Reason") or record.get("Entry_Reason") or record.get("Entry_Status") or "未提供判定")
    result["Comparison_New_Reason"] = current_reason
    result["Comparison_Display_Only"] = True
    for key in (
        "Entry_Schema", "Entry_Status", "Entry_Status_Group", "Entry_Ready", "Entry_Plan_Type",
        "Entry_Low", "Entry_High", "Entry_Stop", "Entry_Target", "Entry_RRR", "Entry_Net_RRR",
        "No_Chase_Price", "Entry_Reason",
    ):
        result.pop(key, None)
        if key in plan:
            result[key] = deepcopy(plan[key])
    result["Entry_Ready"] = False
    result["Entry_Status"] = "舊制可執行（比較）"
    result["Entry_Status_Group"] = "comparison"
    result["Entry_Reason"] = (
        f"舊制判定：{plan.get('Entry_Reason') or '符合 9/9 進場規則'}｜"
        f"原新制判定：{current_reason}｜勝率與樣本為{COMPARISON_BACKTEST_LABEL}。"
    )
    return result
