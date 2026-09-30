"""Short, decision-first Telegram presentation; no data loading or delivery."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
import math

from research_decision import build_trade_decision

REPORT_SCHEMA = "daily_executable_research_v1"
MAX_MESSAGE_UNITS = 3500


def _text(value, limit=180):
    return " ".join(str(value or "").split())[:limit]


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _price(value):
    number = _number(value)
    return "未提供" if number is None else f"{number:,.2f}".rstrip("0").rstrip(".")


def _date(value):
    try:
        return date.fromisoformat(str(value)).isoformat()
    except (ValueError, TypeError):
        return None


def _plan_is_displayable(plan):
    """Do not print a buy instruction if its required price fields are absent."""
    if not isinstance(plan, Mapping) or plan.get("status") != "ok":
        return False
    values = [_number(plan.get(key)) for key in (
        "entry_low", "entry_high", "stop", "target", "shares", "modeled_stop_loss")]
    if any(value is None for value in values):
        return False
    low, high, stop, target, shares, loss = values
    return (0 < stop < low <= high < target and shares >= 1 and shares.is_integer()
            and 0 < loss <= 5000 and _number(plan.get("max_modeled_loss")) == 5000)


def _display_decision(item, report):
    decision = build_trade_decision(item, report)
    if not isinstance(decision, Mapping):
        decision = {}
    code = "buy" if decision.get("code") == "buy" else "no_buy"
    reasons = decision.get("reasons", [])
    if not isinstance(reasons, (list, tuple)):
        reasons = []
    reasons = [_text(reason, 100) for reason in reasons if _text(reason, 100)][:2]
    next_step = _text(decision.get("next_step"), 140) or "等待資料補齊後重新判定，不先下單。"
    # Presentation is defensive as well: historical or undated reports must
    # never carry actionable-looking order quantities, even if malformed.
    if report.get("forecast_period_elapsed"):
        code, reasons = "no_buy", ["此名單適用時段已過，只能回顧，不能當作今日買進訊號。"]
        next_step = "等待最新盤後榜單，重新確認價格與資格。"
    elif not _date(report.get("forecast_date")) or not _date(report.get("analysis_date")):
        code, reasons = "no_buy", ["分析日或適用交易日未確認，資訊不足。"]
        next_step = "確認資料日期與下一交易日後再判斷。"
    elif code == "buy" and not _plan_is_displayable(item.get("plan")):
        code, reasons = "no_buy", ["進場、停損、目標或含成本股數資料不完整，無法核算風險。"]
        next_step = "先補齊並核對價格及風險計算，不先下單。"
    elif code == "buy" and not reasons:
        code, reasons = "no_buy", ["買進理由未提供，資訊不足。"]
        next_step = "先取得可核對的買進依據，不先下單。"
    return {"code": code, "label": "買（限價、條件式）" if code == "buy" else "不買",
            "reasons": reasons or ["資訊不足，尚無可驗證的買進依據。"], "next_step": next_step}


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


def _item_message(item, decision, forecast_date):
    lines = [f"{decision['label']}｜{_text(item.get('ticker'), 12)} {_text(item.get('name'), 40)}｜{_version_label(item)}",
             f"適用 {forecast_date or '未確認'}｜盤後計畫，非即時"]
    lines.extend("原因：" + reason for reason in decision["reasons"])
    if decision["code"] == "buy":
        plan = item["plan"]
        lines.extend([
            f"限價區 {_price(plan['entry_low'])}–{_price(plan['entry_high'])}｜停損 {_price(plan['stop'])}｜目標 {_price(plan['target'])}",
            f"股數上限 {int(plan['shares']):,} 股（依區間上限）；含成本模型停損 NT${_price(plan['modeled_stop_loss'])}／每檔上限 5,000",
        ])
    lines.append("下一步：" + decision["next_step"])
    analysis_url = _text(item.get("analysis_url"), 900)
    lines.append("解析（開啟為最新頁面）：" + (analysis_url or "連結未提供"))
    return "\n".join(lines)


def format_compact_research_messages(report):
    """One short summary plus one self-contained decision per candidate.

    Full research calculations remain in the report; this pure formatter does
    not change ranking eligibility, risk rules, holdings, or the saved report.
    """
    if report.get("schema") != REPORT_SCHEMA:
        raise ValueError("不支援的研究報告版本")
    items = report.get("items")
    if not isinstance(items, list) or any(not isinstance(item, Mapping) for item in items):
        raise ValueError("研究報告缺少有效個股資料")
    decisions = [_display_decision(item, report) for item in items]
    buys = sum(decision["code"] == "buy" for decision in decisions)
    new_count = sum("new" in _versions(item) for item in items)
    legacy_count = sum("legacy" in _versions(item) for item in items)
    analysis_date, forecast_date = _date(report.get("analysis_date")), _date(report.get("forecast_date"))
    title = "交易結論｜精簡版"
    if report.get("forecast_period_elapsed"):
        title = "⚠️ 名單已過期｜僅供回顧，全部不買"
    elif not forecast_date or not analysis_date:
        title = "⚠️ 日期未確認｜資訊不足，全部不買"
    counts = (f"條件式買 {buys} 檔／不買 {len(items) - buys} 檔；新制 {new_count}、舊制 {legacy_count}（重疊只列一次）"
              if items else "沒有符合名單：不買，等待下一次掃描。")
    messages = ["\n".join([
        title,
        f"分析日 {analysis_date or '未確認'}｜適用日 {forecast_date or '未確認'}",
        counts,
        "模型判定，非獲利保證；盤後資料非即時，進場前重驗價格與新制資格。",
        "每檔 NT$5,000 是含成本模型停損，跳空或流動性不足仍可能超額。",
        "候選並非實際持倉；不自動下單。",
    ])]
    messages.extend(_item_message(item, decision, forecast_date) for item, decision in zip(items, decisions))
    # Each stock remains a single message. Fixed field bounds above preserve
    # complete decision context instead of splitting buy/stop instructions.
    if any(len(message.encode("utf-16-le")) // 2 > MAX_MESSAGE_UNITS for message in messages):
        raise ValueError("精簡研究訊息超過長度上限")
    return messages
