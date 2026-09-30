"""Conservative report conclusions, never new ranking or broker approvals.

A saved new-rule approval is necessary, never sufficient for an unconditional
live order. The positive label is a dated limit-price plan requiring execution
checks; legacy scores and legacy win rates cannot create a buy conclusion.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import math

from entry_readiness import (
    ENTRY_SCHEMA_VERSION, MIN_BACKTEST_SAMPLES, MIN_EFFECTIVE_REWARD_RISK,
    MIN_VALIDATION_SAMPLES, READY_STATUS,
)
from execution_costs import estimate_round_trip_net_profit, estimate_stop_loss
from market_calendar import next_scheduled_session


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _brief(value, limit=80):
    return " ".join(str(value or "").split())[:limit]


def build_trade_decision(item, report):
    """Return buy/no_buy plus at most two facts and one actionable next check."""
    reasons = []
    next_step = "等待新制合格及資料完整後再評估；不要因分數高追買。"
    try:
        analysis_day = date.fromisoformat(report["analysis_date"])
        forecast_day = date.fromisoformat(report["forecast_date"])
        generated = datetime.fromisoformat(report["generated_at"])
        if generated.tzinfo is None:
            raise ValueError("missing timezone")
        generated = generated.astimezone(timezone(timedelta(hours=8)))
        if (item.get("price_date") != analysis_day.isoformat()
                or next_scheduled_session(analysis_day) != forecast_day
                or generated.date() < analysis_day):
            raise ValueError("date mismatch")
        if (report.get("forecast_period_elapsed")
                or generated.date() > forecast_day
                or (generated.date() == forecast_day and (generated.hour, generated.minute) >= (13, 30))):
            reasons.append("這份計畫的交易時段已過，不當作新的買入依據。")
            next_step = "等待下一份完成盤後掃描的名單。"
    except (KeyError, ValueError, TypeError):
        reasons.append("分析日或適用交易日無法確認。")
        next_step = "等待日期完整且有效的最新榜單。"

    approved = item.get("new_approved") is True and "new" in item.get("versions", [])
    if not approved:
        reasons.append(_brief(item.get("original_new_reason")) or "只有舊制比較資格，尚未通過新制風控。")
        if next_step.startswith("等待新制"):
            original_reason = str(item.get("original_new_reason") or "")
            if "樣本" in original_reason:
                next_step = f"等新制訓練至少 {MIN_BACKTEST_SAMPLES} 筆、驗證至少 {MIN_VALIDATION_SAMPLES} 筆且通過其他風控再看。"
            elif "財報" in original_reason and "風險" in original_reason:
                next_step = "等財報風險改善且通過新制，再重新評估。"
            elif "資料" in original_reason:
                next_step = "等缺漏的籌碼、營收或財報資料補齊，再重新評估。"
    evidence = item.get("execution_evidence") or {}
    if approved and (evidence.get("schema") != ENTRY_SCHEMA_VERSION
                     or evidence.get("ready") is not True
                     or evidence.get("status") != READY_STATUS
                     or evidence.get("critical_ready") is not True
                     or evidence.get("critical_issues")
                     or evidence.get("rechecked_status") != READY_STATUS):
        reasons.append(_brief(evidence.get("rechecked_reason")) or "新制資格或關鍵資料未完整核實。")

    plan = item.get("plan") or {}
    low, high, stop, target, shares = [_number(plan.get(key)) for key in (
        "entry_low", "entry_high", "stop", "target", "shares")]
    valid = (plan.get("status") == "ok" and all(value is not None for value in (low, high, stop, target, shares))
             and 0 < stop < low <= high < target and shares >= 1 and shares.is_integer())
    if not valid:
        reasons.append("進場、停損或股數無法安全核算。")
    else:
        loss = estimate_stop_loss(high, stop, shares)
        profit = estimate_round_trip_net_profit(high, target, shares)
        if loss is None or profit is None or not 0 < loss.estimated_net_loss <= 5000:
            reasons.append("核算後的每檔模型停損風險超標或無法確認。")
        elif profit / loss.estimated_net_loss < MIN_EFFECTIVE_REWARD_RISK - 1e-9:
            reasons.append("含成本後的預期報酬相對停損風險不足，等待更佳進場價。")

    technical = item.get("technical") or {}
    if item.get("price_alignment") != "matched" or technical.get("data_date") != report.get("analysis_date"):
        reasons.append("行情缺漏、日期不符或還原價格不一致。")
    elif technical.get("status") != "ok" or any(
            technical.get(unit, {}).get("status") != "ok" for unit in ("daily", "weekly")):
        reasons.append("日線或週線資料不足，尚不能確認方向。")
    elif any(technical[unit].get("trend") == "短線轉弱" for unit in ("daily", "weekly")):
        reasons.append("日線或週線仍轉弱，先等方向止穩。")

    funds, inst = item.get("fundamentals") or {}, item.get("institutional") or {}
    if funds.get("Financial_Risk_Level") == "high":
        reasons.append("最新財報風險偏高，先等營運改善。")
    elif (funds.get("Revenue_Status") != "ok" or funds.get("Financial_Status") != "ok"
          or funds.get("Financial_Risk_Level") not in ("low", "medium")
          or inst.get("Institutional_Status") != "ok"):
        reasons.append("營收、財報或法人籌碼尚未完整確認。")

    backtest = item.get("backtest") or {}
    if backtest.get("status") != "ok":
        reasons.append("歷史回測無法驗證，不以高分替代證據。")
    else:
        metrics = backtest.get("metrics") or {}
        for label, minimum in (("training", MIN_BACKTEST_SAMPLES), ("validation", MIN_VALIDATION_SAMPLES)):
            section = metrics.get(label) or {}
            count, net = _number(section.get("samples")), _number(section.get("net_profit"))
            if count is None or count < minimum or net is None or net <= 0:
                reasons.append("重算回測的樣本或成本後結果尚不足以支持買入。")
                break
    news = item.get("news") or {}
    statuses = news.get("source_status") or {}
    if news.get("warnings") or any(statuses.get(source, {}).get("status") != "ok" for source in ("TWSE", "TPEx")):
        reasons.append("公告來源未完整核實，先確認是否有新風險。")
    elif news.get("events"):
        reasons.append("有新公告待核對影響，尚不能只靠標題決定買入。")

    if reasons:
        return {"code": "no_buy", "label": "不買", "reasons": list(dict.fromkeys(reasons))[:2],
                "next_step": next_step}
    return {
        "code": "buy", "label": "買（限價、條件式）",
        "reasons": ["已通過原新制資格，價格與資料核對一致。", "含成本風控符合，日週線未出現轉弱。"],
        "next_step": "僅在進場區且開盤價量仍符合時考慮；超價不追，先核對自身持倉曝險。",
    }
