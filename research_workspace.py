"""Read-only research views. No scanner, notification, or persistence side effects."""

from __future__ import annotations

from copy import deepcopy
from datetime import date
import math

from backtest_reporting import primary_backtest_display, sample_breakdown
from entry_readiness import READY_STATUS, ensure_entry_readiness
from execution_costs import calculate_max_odd_lot_position, estimate_round_trip_net_profit


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def build_research_ideas(records, analysis_date, scope="", *, limit=5):
    """Explain current-rule candidates without changing the saved official ranking."""
    date.fromisoformat(analysis_date)
    if not 1 <= limit <= 5:
        raise ValueError("研究候選上限為 5 檔")
    matched = []
    rejected: dict[str, int] = {}
    scope = scope.strip().casefold()
    seen = set()
    for original in records:
        if not isinstance(original, dict):
            continue
        if scope and not any(scope in str(original.get(key, "")).casefold() for key in ("代號", "名稱", "產業")):
            continue
        ticker = str(original.get("代號", ""))
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        if original.get("Data_Date") != analysis_date:
            rejected["行情日期不一致"] = rejected.get("行情日期不一致", 0) + 1
            continue
        row = ensure_entry_readiness(deepcopy(original))
        if row.get("Entry_Status") != READY_STATUS:
            reason = str(row.get("Entry_Status") or "進場條件不足")
            rejected[reason] = rejected.get(reason, 0) + 1
            continue
        low, high, stop, target, score = [number(row.get(key)) for key in (
            "Entry_Low", "Entry_High", "Entry_Stop", "Entry_Target", "Score")]
        if any(value is None for value in (low, high, stop, target, score)) or not 0 < stop < low <= high < target:
            rejected["價格計畫不完整"] = rejected.get("價格計畫不完整", 0) + 1
            continue
        sizing = calculate_max_odd_lot_position(high, stop, 5000)
        if sizing is None or sizing.shares < 1:
            rejected["無可行風險股數"] = rejected.get("無可行風險股數", 0) + 1
            continue
        target_net = estimate_round_trip_net_profit(high, target, sizing.shares)
        legacy = primary_backtest_display(row)
        reasons = [str(reason) for reason in row.get("Reasons", [])] if isinstance(row.get("Reasons"), list) else []
        matched.append({
            "ticker": ticker, "name": row.get("名稱", ticker), "industry": row.get("產業", "未分類"),
            "date": analysis_date, "score": score, "entry_low": low, "entry_high": high,
            "stop": stop, "target": target, "gross_reward_risk": (target - high) / (high - stop),
            "net_reward_risk": target_net / sizing.estimated_net_loss if target_net is not None and sizing.estimated_net_loss > 0 else None,
            "max_shares": sizing.shares, "modeled_loss": sizing.estimated_net_loss,
            "entry_reason": row.get("Entry_Reason", ""), "reasons": reasons,
            "legacy_evidence": legacy, "current_samples": sample_breakdown(row),
            "fundamentals": {key: row.get(key) for key in (
                "EPS", "EPS_Period", "YoY", "MoM", "Revenue_Period", "Revenue_Status",
                "Financial_Period", "Financial_Source", "Financial_Status", "Financial_Risk_Level",
                "Financial_Operating_Margin", "Financial_Debt_Ratio", "Financial_Risk_Flags")},
            "institutional": {key: row.get(key) for key in ("Whale_Net", "Whale_Net_Days", "Institutional_Status")},
            "invalidation": f"價格不在 {low:g}–{high:g}、跌破 {stop:g}，或資料/量能/市場條件改變時重新檢查；不沿用舊訊號下單。",
        })
    matched.sort(key=lambda row: (-row["score"], row["ticker"]))
    selected = []
    industry_count: dict[str, int] = {}
    for row in matched:
        industry = row["industry"]
        if industry not in ("", "未分類", "一般產業") and industry_count.get(industry, 0) >= 2:
            continue
        selected.append(row)
        industry_count[industry] = industry_count.get(industry, 0) + 1
        if len(selected) >= limit:
            break
    return {"date": analysis_date, "scope": scope or "本次保存掃描範圍", "ideas": selected,
            "matched_count": len(matched), "rejected": rejected,
            "notice": "最多 5 個研究候選，並非保證高勝率，也不是獨立於原風控的新入榜來源。分數只代表條件評分；回測不是未來獲利機率。",
            "risk_notice": "每檔停損模型風險上限 NT$5,000，不是保證最多只虧 5,000；另需檢查總資金、整體曝險、跳空與流動性。"}


def parse_holdings(text):
    """Small manual text format, not a spreadsheet import or a cloud portfolio."""
    result = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) not in (2, 3) or not parts[0].isdigit() or not 4 <= len(parts[0]) <= 6:
            raise ValueError("每行請填：股票代號,配置百分比,產業（產業可省略）")
        weight = number(parts[1])
        if weight is None:
            raise ValueError("配置百分比須為有效數字")
        result.append({"ticker": parts[0], "weight_pct": weight,
                       "industry": parts[2] if len(parts) == 3 else "未分類"})
    if not result or len(result) > 20:
        raise ValueError("請輸入 1 至 20 檔實際持倉")
    return result
