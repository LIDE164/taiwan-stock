"""Short, evidence-only pros and limitations; never changes trading eligibility.

Inputs are the dated, validated observations in ``daily_research_report``.
Missing observations remain unknown.  A favourable metric is not a trade order.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
import math
import re
from typing import Any, TypedDict


class Facet(TypedDict):
    advantages: list[str]
    risks: list[str]


_FINANCIAL_FLAGS = {
    "negative_gross_margin": "毛利率為負",
    "negative_operating_margin": "營業利益率為負",
    "negative_net_margin": "淨利率為負",
    "high_debt_ratio": "負債比偏高",
    "elevated_debt_ratio": "負債比升高",
    "low_current_ratio": "流動比率偏低",
    "moderate_current_ratio": "流動比率需留意",
}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = date.fromisoformat(value)
        return parsed if parsed.isoformat() == value else None
    except ValueError:
        return None


def _month(value: Any) -> date | None:
    return _date(value + "-01") if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}", value) else None


def _quarter_end(value: Any) -> date | None:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-Q[1-4]", value):
        return None
    year, quarter = int(value[:4]), int(value[-1])
    return _date(f"{year:04d}-{quarter * 3:02d}-{31 if quarter in (1, 4) else 30}")


def _expected_month(analysis_date: date) -> date | None:
    # Same conservative 11th-day boundary as scanner.expected_revenue_period
    # and data_providers._expected_revenue_period; no import-time cloud client.
    offset = -1 if analysis_date.day >= 11 else -2
    absolute = analysis_date.year * 12 + analysis_date.month - 1 + offset
    year, month = divmod(absolute, 12)
    return _date(f"{year:04d}-{month + 1:02d}-01")


def _expected_quarter(analysis_date: date) -> date | None:
    # Match scanner.expected_financial_period.  This is the oldest still-fresh
    # period, not a ban on an already published, more recent completed quarter.
    marker = (analysis_date.month, analysis_date.day)
    for boundary, quarter in (((11, 15), 3), ((8, 15), 2), ((5, 16), 1)):
        if marker >= boundary:
            return _quarter_end(f"{analysis_date.year:04d}-Q{quarter}")
    return _quarter_end(f"{analysis_date.year - 1:04d}-Q{4 if marker >= (4, 1) else 3}")


def _fmt(value: float, *, signed: bool = False) -> str:
    if 0 < abs(value) < 0.1 or abs(value) >= 1_000_000_000:
        return f"{value:+.2g}" if signed else f"{value:.2g}"
    return (f"{value:+,.1f}" if signed else f"{value:,.1f}").rstrip("0").rstrip(".")


def _facet(advantages: list[str], risks: list[str]) -> Facet:
    return {"advantages": advantages[:2], "risks": risks[:2]}


def _technical(item: Mapping[str, Any], analysis_date: date) -> Facet:
    data = _mapping(item.get("technical"))
    daily = _mapping(data.get("daily"))
    alignment = item.get("price_alignment")
    if alignment == "price_adjustment_mismatch":
        return _facet([], ["同日價格口徑不一致，暫不判讀技術強弱"])
    if alignment == "stale_history" or _date(data.get("data_date")) not in (None, analysis_date):
        return _facet([], ["行情未涵蓋分析日，不能視為當日技術訊號"])
    if (alignment != "matched" or data.get("status") != "ok"
            or _date(data.get("data_date")) != analysis_date
            or _date(data.get("as_of")) != analysis_date
            or _date(daily.get("date")) != analysis_date):
        return _facet([], ["行情或日期尚未核實，無法判讀技術強弱"])
    if daily.get("status") != "ok":
        return _facet([], ["日線歷史不足，均線與動能無法完整判讀"])
    close, ma20, ma60 = (_number(daily.get(key)) for key in ("close", "ma20", "ma60"))
    if any(value is None or value <= 0 for value in (close, ma20, ma60)):
        return _facet([], ["日線均線資料不足，無法核對趨勢"])
    assert close is not None and ma20 is not None and ma60 is not None
    advantages: list[str] = []
    risks: list[str] = []
    if close > ma20 > ma60:
        advantages.append("日線多頭排列，收盤高於 20／60MA")
    elif close > ma20:
        advantages.append("收盤高於 20MA，短線有支撐條件")
    elif close < ma20:
        risks.append("收盤低於 20MA，短線支撐轉弱")
    histogram, rsi = _number(daily.get("macd_hist")), _number(daily.get("rsi14"))
    if histogram is not None and histogram < 0:
        risks.append("MACD 柱值為負，動能尚未同步")
    if rsi is not None and 70 <= rsi <= 100:
        risks.insert(0, f"RSI {_fmt(rsi)} 偏熱，追價風險較高")
    elif rsi is not None and 0 <= rsi < 30:
        risks.append(f"RSI {_fmt(rsi)} 偏弱，超賣不等同反轉")
    if histogram is not None and histogram > 0 and rsi is not None and 50 <= rsi < 70:
        advantages.append("MACD 柱值為正，RSI 未達過熱區")
    if histogram is None or rsi is None or not 0 <= rsi <= 100:
        risks.append("部分動能指標缺值，強弱判讀不完整")
    weekly = _mapping(data.get("weekly"))
    weekly_date = _date(weekly.get("date"))
    if weekly_date is None or weekly_date > analysis_date or weekly.get("status") != "ok":
        risks.append("完整週線資料不足，長短週期尚未確認")
    elif (analysis_date - weekly_date).days > 14:
        risks.append(f"週線僅更新至 {weekly_date.isoformat()}，時效不足")
    else:
        wc, wm20, wm60 = (_number(weekly.get(key)) for key in ("close", "ma20", "ma60"))
        if any(value is None or value <= 0 for value in (wc, wm20, wm60)):
            risks.append("週線均線缺值，長週期尚未確認")
        else:
            assert wc is not None and wm20 is not None and wm60 is not None
            if wc > wm20 > wm60:
                if advantages and close > ma20 > ma60:
                    advantages[0] = "日線與完整週線均呈多頭排列"
                else:
                    advantages.append("完整週線維持多頭排列")
            elif wc < wm20:
                risks.append("完整週線低於 20 週均線，長週期偏弱")
    return _facet(advantages, risks)


def _institutional(item: Mapping[str, Any], analysis_date: date) -> Facet:
    data = _mapping(item.get("institutional"))
    status = data.get("Institutional_Status")
    if status == "partial":
        return _facet([], ["近 3 日法人資料不完整，不用部分合計判斷方向"])
    observed = _date(data.get("Institutional_Latest_Date"))
    if observed is not None and observed != analysis_date:
        return _facet([], ["法人資料日期與分析日不符，當日方向待確認"])
    net, days = _number(data.get("Whale_Net")), _number(data.get("Whale_Net_Days"))
    if (status != "ok" or observed != analysis_date or net is None
            or days != 3):
        return _facet([], ["缺少截至分析日的完整 3 日法人合計"])
    if net > 0:
        return _facet([f"近 3 日法人合計買超 {_fmt(net)} 張"], ["合計買超不代表逐日連買"])
    if net < 0:
        return _facet([], [f"近 3 日法人合計賣超 {_fmt(abs(net))} 張"])
    return _facet([], ["近 3 日法人合計 0 張，方向中性"])


def _fundamental(item: Mapping[str, Any], analysis_date: date) -> Facet:
    data = _mapping(item.get("fundamentals"))
    advantages: list[str] = []
    risks: list[str] = []
    month = _month(data.get("Revenue_Period"))
    expected_month = _month(data.get("Revenue_Expected_Period"))
    if (data.get("Revenue_Status") == "ok" and month is not None and expected_month is not None
            and expected_month == _expected_month(analysis_date)
            and expected_month <= month < analysis_date.replace(day=1)):
        positives, negatives, unknown = [], [], []
        for key, label in (("YoY", "年增"), ("MoM", "月增")):
            value = _number(data.get(key))
            if value is None:
                unknown.append(label)
            elif value > 0:
                positives.append(label + _fmt(value, signed=True) + "%")
            elif value < 0:
                negatives.append(label + _fmt(value, signed=True) + "%")
        period = str(data["Revenue_Period"])
        if positives:
            advantages.append(f"{period} 營收" + "、".join(positives))
        if negatives:
            risks.append(f"{period} 營收" + "、".join(negatives))
        if unknown:
            risks.append("營收" + "／".join(unknown) + "資料不足")
        elif not positives and not negatives:
            risks.append(f"{period} 營收年、月增皆為 0%，未見成長")
    else:
        risks.append("月營收未齊或期別未核實，不判定成長")
    quarter = _quarter_end(data.get("Financial_Period"))
    expected_quarter = _quarter_end(data.get("Financial_Expected_Period"))
    if (data.get("Financial_Status") != "ok" or quarter is None or expected_quarter is None
            or expected_quarter != _expected_quarter(analysis_date)
            or not expected_quarter <= quarter < analysis_date):
        risks.insert(0, "財報未齊或期別未核實，獲利品質待確認")
        return _facet(advantages, risks)
    period = str(data["Financial_Period"])
    margin = _number(data.get("Financial_Operating_Margin"))
    margin_risk = ""
    if margin is not None and margin > 0:
        advantages.append(f"{period} 營業利益率 {_fmt(margin)}% 為正")
    elif margin is not None and margin < 0:
        margin_risk = f"營業利益率 {_fmt(margin)}% 為負"
    elif margin == 0:
        margin_risk = "營業利益率 0%，本業未見獲利"
    else:
        margin_risk = "營業利益率缺值，無法確認本業獲利"
    level = data.get("Financial_Risk_Level")
    if level in ("medium", "high"):
        flags = data.get("Financial_Risk_Flags")
        translated = [_FINANCIAL_FLAGS[key] for key in flags if isinstance(key, str) and key in _FINANCIAL_FLAGS] if isinstance(flags, list) else []
        if margin is not None and margin < 0:
            translated = [flag for flag in translated if flag != "營業利益率為負"]
        descriptions = ([margin_risk] if margin_risk else []) + translated
        description = "、".join(list(dict.fromkeys(descriptions))[:2])
        risks.insert(0, f"{period} 財報風險{'偏高' if level == 'high' else '中等'}" + (f"（{description}）" if description else ""))
    elif level != "low":
        risks.insert(0, "財報風險尚無完整判定" + (f"；{margin_risk}" if margin_risk else ""))
    elif margin_risk:
        risks.insert(0, f"{period} {margin_risk}")
    # EPS without an explicit endpoint (e.g. "ttm") is deliberately not used.
    # Low risk is not evidence of growth, undervaluation, or future returns.
    return _facet(advantages, risks)


def build_research_facets(item: Mapping[str, Any], report: Mapping[str, Any]) -> dict[str, Facet]:
    """Return up to two observed advantages/risks per facet, without mutation.

    ``analysis_date`` dates every conclusion.  Expired forecasts remain valid
    historical observations, not fresh trade instructions.  The caller labels
    that expiry; this module never reads the clock or changes any eligibility.
    """
    analysis_date = _date(report.get("analysis_date"))
    if analysis_date is None:
        return {key: _facet([], ["分析日期不明，資料時點無法核實"])
                for key in ("technical", "institutional", "fundamental")}
    return {
        "technical": _technical(item, analysis_date),
        "institutional": _institutional(item, analysis_date),
        "fundamental": _fundamental(item, analysis_date),
    }
