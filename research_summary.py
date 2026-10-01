"""Concise three-facet Telegram presentation; no data loading or delivery."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
import math

from research_decision import build_trade_decision
from research_facets import build_research_facets

REPORT_SCHEMA = "daily_executable_research_v1"
MAX_MESSAGE_UNITS = 3500


def _text(value, limit=180):
    return " ".join(str(value or "").split())[:limit]


def _date(value):
    try:
        return date.fromisoformat(str(value)).isoformat()
    except (ValueError, TypeError):
        return None


def _plan_is_displayable(plan):
    if not isinstance(plan, Mapping) or plan.get("status") != "ok":
        return False
    values = [plan.get(key) for key in (
        "entry_low", "entry_high", "stop", "target", "shares", "modeled_stop_loss", "max_modeled_loss")]
    if any(isinstance(value, bool) for value in values):
        return False
    try:
        low, high, stop, target, shares, loss, cap = [float(value) for value in values if value is not None]
    except (TypeError, ValueError, OverflowError):
        return False
    return (all(math.isfinite(value) for value in (low, high, stop, target, shares, loss, cap))
            and 0 < stop < low <= high < target and shares >= 1 and shares.is_integer()
            and 0 < loss <= 5000 and cap == 5000)


def _versions(item):
    versions = item.get("versions", [])
    return versions if isinstance(versions, (list, tuple)) else []


def _version_label(item):
    versions = _versions(item)
    if "new" in versions and "legacy" in versions:
        return "新制＋舊制"
    if "new" in versions:
        return "新制"
    if "legacy" in versions:
        return "舊制比較"
    return "制度未確認"


def _points(facet, field, fallback):
    values = facet.get(field, []) if isinstance(facet, Mapping) else []
    if not isinstance(values, (list, tuple)):
        return fallback
    values = [_text(value, 70) for value in values if _text(value, 70)][:2]
    return "、".join(values) or fallback


def _decision_label(item, report):
    # Keep the existing conclusion rules. Facet positives never create an
    # approval, and malformed/expired input cannot produce a buy headline.
    if (report.get("forecast_period_elapsed") or not _date(report.get("analysis_date"))
            or not _date(report.get("forecast_date"))):
        return "不買"
    try:
        decision = build_trade_decision(item, report)
    except (TypeError, ValueError, KeyError, AttributeError):
        return "不買"
    if (not isinstance(decision, Mapping) or decision.get("code") != "buy"
            or not _plan_is_displayable(item.get("plan"))):
        return "不買"
    return "買（限價、條件式）"


def _item_message(item, report, conclusion):
    analysis_date = _date(report.get("analysis_date"))
    forecast_date = _date(report.get("forecast_date"))
    timing = "盤後資料，非即時"
    if report.get("forecast_period_elapsed"):
        timing = "已過期，僅供回顧"
    elif not analysis_date or not forecast_date:
        timing = "日期未確認，不作進場依據"
    lines = [
        f"{conclusion}｜{_text(item.get('ticker'), 12)} {_text(item.get('name'), 40)}｜{_version_label(item)}",
        f"分析 {analysis_date or '未確認'}｜適用 {forecast_date or '未確認'}｜{timing}",
    ]
    facets = build_research_facets(item, report)
    for key, label in (("technical", "技術面"), ("institutional", "籌碼面"), ("fundamental", "基本面")):
        facet: Mapping[str, object] = facets.get(key, {})
        advantages = _points(facet, "advantages", "暫無明確優點")
        risks = _points(facet, "risks", "已查指標未見明顯弱點，非無風險")
        lines.append(f"{label}｜優點：{advantages}；缺點／限制：{risks}")
    analysis_url = _text(item.get("analysis_url"), 700)
    lines.append("解析（開啟為最新頁面）：" + (analysis_url or "連結未提供"))
    return "\n".join(lines)


def format_compact_research_messages(report):
    """One summary plus one dated pros/cons message per candidate.

    Full research calculations remain in the report. This presentation never
    changes eligibility, risk rules, holdings, images, or the input report.
    """
    if report.get("schema") != REPORT_SCHEMA:
        raise ValueError("不支援的研究報告版本")
    items = report.get("items")
    if not isinstance(items, list) or any(not isinstance(item, Mapping) for item in items):
        raise ValueError("研究報告缺少有效個股資料")
    new_count = sum("new" in _versions(item) for item in items)
    legacy_count = sum("legacy" in _versions(item) for item in items)
    conclusions = [_decision_label(item, report) for item in items]
    buys = sum(value == "買（限價、條件式）" for value in conclusions)
    analysis_date, forecast_date = _date(report.get("analysis_date")), _date(report.get("forecast_date"))
    title = "三面優缺點｜精簡版"
    if report.get("forecast_period_elapsed"):
        title = "⚠️ 名單已過期｜三面優缺點僅供回顧"
    elif not forecast_date or not analysis_date:
        title = "⚠️ 日期未確認｜三面優缺點不作進場依據"
    counts = (f"條件式買 {buys}／不買 {len(items) - buys} 檔；新制 {new_count}、舊制 {legacy_count}（重疊只列一次）"
              if items else "沒有符合名單；不另補股票。")
    messages = ["\n".join([
        title,
        f"分析日 {analysis_date or '未確認'}｜適用日 {forecast_date or '未確認'}",
        counts,
        "僅列可核對的優缺點，缺資料明示；非即時訊號，亦非買進指令。",
        "原榜單資格與風控不變；每檔 NT$5,000 為模型停損，跳空仍可能超額。",
        "候選並非實際持倉；不自動下單。",
    ])]
    messages.extend(_item_message(item, report, conclusion) for item, conclusion in zip(items, conclusions))
    # Keep the advantages and limitations together in one bounded stock part.
    if any(len(message.encode("utf-16-le")) // 2 > MAX_MESSAGE_UNITS for message in messages):
        raise ValueError("精簡研究訊息超過長度上限")
    return messages
