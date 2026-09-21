"""Explicit backtest sample scopes and read-only evidence diagnostics."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
import math
from typing import Any

from entry_readiness import (
    MIN_BACKTEST_SAMPLES, MIN_EXECUTION_SCORE, MIN_VALIDATION_SAMPLES, READY_STATUS, build_entry_readiness,
)


BACKTEST_FIELD_MAP = {
    "WinRate": "win_rate",
    "Backtest_Samples": "closed_signals",
    "Backtest_Scope": "backtest_scope",
    "Validation_WinRate": "validation_win_rate",
    "Validation_Samples": "validation_samples",
    "Validation_Wilson_Low": "validation_wilson_low",
    "Validation_Wilson_High": "validation_wilson_high",
    "Backtest_Net_Expectancy": "net_expectancy_pct",
    "Validation_Net_Expectancy": "validation_net_expectancy_pct",
    "Backtest_Max_Drawdown": "max_drawdown",
    "Backtest_Max_Consecutive_Losses": "max_consecutive_losses",
    "Backtest_Execution_Unresolved": "execution_unresolved",
    "Validation_Sample_Confidence": "validation_sample_confidence",
    "Backtest_Schema": "backtest_schema",
    "Backtest_Overall_Samples": "overall_samples",
    "Backtest_Overall_WinRate": "overall_win_rate",
    "Backtest_Training_Samples": "training_samples",
    "Backtest_Raw_WinRate": "training_raw_win_rate",
    "Validation_Raw_WinRate": "validation_raw_win_rate",
    "Backtest_Diagnostics": "diagnostics",
}
BACKTEST_SNAPSHOT_KEYS = (*BACKTEST_FIELD_MAP, "Model_Confidence", "Model_Confidence_Label")


def _count(value: Any) -> int | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if isinstance(value, bool) or not math.isfinite(number) or number < 0 or not number.is_integer():
        return None
    return int(number)


def backtest_record_fields(stats: Mapping[str, Any]) -> dict[str, Any]:
    """Preserve explicit totals; legacy fields keep their established meaning."""
    return {field: stats[key] for field, key in BACKTEST_FIELD_MAP.items() if key in stats}


def replace_backtest_snapshot(record: dict[str, Any], source: Mapping[str, Any]) -> None:
    """Replace as a unit: never attach today's split/diagnostics to a legacy rate."""
    snapshot = {key: source[key] for key in BACKTEST_SNAPSHOT_KEYS if key in source}
    for key in BACKTEST_SNAPSHOT_KEYS:
        record.pop(key, None)
    record.update(snapshot)


def reconcile_intraday_evidence(baseline: Mapping[str, Any], live: Mapping[str, Any]) -> dict[str, Any]:
    """Use one frozen historical snapshot for both live approval and its display."""
    result = {**baseline, **live}
    replace_backtest_snapshot(result, baseline)
    result.update(build_entry_readiness(result, intraday=True, baseline_plan=baseline))
    return result


def sample_breakdown(record: Mapping[str, Any]) -> dict[str, Any]:
    """Never reinterpret old inclusive samples as a disjoint training set."""
    primary = _count(record.get("Backtest_Samples"))
    overall = _count(record.get("Backtest_Overall_Samples"))
    validation = _count(record.get("Validation_Samples"))
    training = _count(record.get("Backtest_Training_Samples"))
    if training is None and overall is not None and validation is not None and primary is not None:
        if primary + validation == overall:
            training = primary
    consistent = not (
        training is not None and (
            (primary is not None and primary != training)
            or (overall is not None and validation is not None and training + validation != overall)
        )
    )
    if not consistent:
        training = None
    known_split = training is not None
    if not known_split:
        overall = overall if overall is not None else primary
    def text(value: int | None) -> str:
        return "--" if value is None else str(value)
    return {
        "overall": overall,
        "training": training,
        "validation": validation,
        "known_split": known_split,
        "consistent": consistent,
        "primary_label": "訓練校正勝率" if known_split else "原制校正勝率",
        "sample_text": (
            f"全期 {text(overall)}｜訓練 {text(training)}｜驗證 {text(validation)}"
            if known_split else f"原制 {text(primary)}｜驗證 {text(validation)}（{'未分離' if consistent else '分母不一致'}）"
        ),
        "compact_text": (
            f"全{text(overall)}/訓{text(training)}/驗{text(validation)}"
            if known_split else f"原制{text(primary)}/驗{text(validation)}"
        ),
    }


def backtest_diagnostic_rows(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    diagnostics = record.get("Backtest_Diagnostics")
    if not isinstance(diagnostics, Mapping):
        return []
    stages = {
        "evaluated": "可評估訊號日", "signal_pass": "分數達標",
        "plan_ready": "訊號日進場條件符合", "filled": "隔日觸價",
        "execution_unresolved": "成交順序不明（排除）", "incomplete": "尚未結束（排除）",
        "completed": "有效完成交易（全期）", "training": "訓練筆數", "validation": "驗證筆數",
    }
    return [{"階段": label, "筆數": value} for key, label in stages.items()
            if (value := _count(diagnostics.get(key))) is not None]


_EVIDENCE_FIELDS = {
    "WinRate", "Backtest_Samples", "Validation_WinRate", "Validation_Samples",
    "Backtest_Net_Expectancy", "Validation_Net_Expectancy", "Validation_Wilson_Low",
}


def evidence_observation(record: Mapping[str, Any], *, intraday: bool = False) -> dict[str, Any] | None:
    """Expose a paper-observation candidate without changing its trading approval."""
    if not any(key in record for key in _EVIDENCE_FIELDS):
        return None
    try:
        score = float(record.get("Score", 0))
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(score) or score < MIN_EXECUTION_SCORE:
        return None
    if intraday and record.get("Intraday_Quote_Status") != "realtime":
        return None
    baseline = record if intraday else None
    approved = build_entry_readiness(record, intraday=intraday, baseline_plan=baseline)
    if approved.get("Entry_Status") == READY_STATUS:
        return None
    without_evidence = {key: value for key, value in record.items() if key not in _EVIDENCE_FIELDS}
    price_plan = build_entry_readiness(without_evidence, intraday=intraday, baseline_plan=baseline)
    if price_plan.get("Entry_Status") != READY_STATUS:
        return None
    return {
        **dict(record),
        **price_plan,
        "Entry_Status": "條件符合，待驗證",
        "Entry_Status_Group": "validation",
        "Entry_Ready": False,
        "Entry_Reason": approved.get("Entry_Reason", "歷史證據不足"),
    }


def summarize_scan_evidence(records: Sequence[Mapping[str, Any]], *, intraday: bool = False) -> dict[str, Any]:
    sample_counts = [_count(row.get("Backtest_Samples")) for row in records]
    breakdowns = [sample_breakdown(row) for row in records]
    observations = [candidate for row in records if (candidate := evidence_observation(row, intraday=intraday))]
    observations.sort(key=lambda row: float(row.get("Score") or 0), reverse=True)
    return {
        "count": len(records),
        "zero_samples": sum(value == 0 for value in sample_counts),
        "one_sample": sum(value == 1 for value in sample_counts),
        "missing_samples": sum(value is None for value in sample_counts),
        "sample_gate_pass": sum(
            row["known_split"] and (row["training"] or 0) >= MIN_BACKTEST_SAMPLES
            and (row["validation"] or 0) >= MIN_VALIDATION_SAMPLES
            for row in breakdowns
        ),
        "ready_count": sum(row.get("Entry_Status") == READY_STATUS for row in records),
        "reasons": Counter(str(row.get("Entry_Reason") or "未提供判定") for row in records).most_common(8),
        "observations": observations,
    }
