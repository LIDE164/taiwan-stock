"""Build concise, evidence-based strategy guidance from analysis fields."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any


def _number(value: Any) -> float | None:
    try:
        parsed = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _price(value: float | None) -> str:
    return "--" if value is None else f"{value:,.2f}"


def _signed(value: float) -> str:
    return f"{value:+,.2f}"


def _risk_level(data: Mapping[str, Any], ma20: float | None) -> tuple[str, float | None]:
    atr_stop = _number(data.get("ATR_Stop"))
    if atr_stop is not None and atr_stop > 0:
        return "ATR 防守價", atr_stop
    if ma20 is not None and ma20 > 0:
        return "20MA", ma20
    return "防守價", None


def _context_sentence(data: Mapping[str, Any]) -> str:
    notes: list[str] = []
    institutional_status = str(data.get("Institutional_Status", "")).lower()
    whale_net = _number(data.get("Whale_Net"))
    whale_days = _number(data.get("Whale_Net_Days"))
    if institutional_status == "ok" and whale_net is not None and whale_net != 0:
        days = max(1, int(whale_days or 1))
        direction = "買超" if whale_net > 0 else "賣超"
        notes.append(f"法人近 {days} 日合計{direction} {abs(whale_net):,.0f} 張")

    revenue_status = str(data.get("Revenue_Status", "")).lower()
    yoy = _number(data.get("YoY"))
    if revenue_status == "ok" and yoy is not None:
        notes.append(f"最新營收年增 {_signed(yoy)}%")

    if not notes:
        return ""
    return f" 佐證／風險：{'；'.join(notes)}。"


def build_strategy_text(data: Mapping[str, Any] | None) -> str:
    """Return a factual strategy sentence with a trigger, target, and invalidation."""
    if not isinstance(data, Mapping):
        return "必要行情資料不足，本次不提供進出場建議。"

    score = _number(data.get("Score"))
    close = _number(data.get("收盤價"))
    ma20 = _number(data.get("20MA"))
    target = _number(data.get("ATR_Target"))
    rsi = _number(data.get("RSI"))
    volume_ratio = _number(data.get("Est_Vol_Ratio"))
    macd = _number(data.get("MACD柱"))
    previous_macd = _number(data.get("前日MACD柱"))
    pattern = str(data.get("Entry_Pattern", "")).strip()
    conflict = str(data.get("Signal_Conflict", "")).strip()
    volume_confirmed = data.get("Volume_Confirmed") is True
    risk_label, risk = _risk_level(data, ma20)
    context = _context_sentence(data)

    if score is None or close is None or close <= 0:
        return "必要行情或分數不足，本次不提供進出場建議。"

    ma20_text = _price(ma20)
    risk_text = _price(risk)
    target_text = _price(target)
    macd_rising = macd is not None and previous_macd is not None and macd > previous_macd
    above_ma20 = ma20 is not None and ma20 > 0 and close > ma20
    bias_to_ma20 = ((close / ma20) - 1) * 100 if above_ma20 else None
    volume_text = "量能資料不足"
    if volume_ratio is not None:
        qualifier = "量比" if volume_confirmed else "盤中估算量比"
        volume_text = f"{qualifier} {volume_ratio:.2f}×"

    if conflict == "高":
        trigger = (
            f"先等收盤重新站穩 20MA {ma20_text}"
            if not above_ma20 and ma20 is not None
            else "先等量價與 MACD 方向重新一致"
        )
        return (
            f"多空訊號衝突高：{trigger}後再觀察；跌破{risk_label} {risk_text} 視為失效。"
            f"{context}"
        )

    if pattern == "過熱追高型" or (rsi is not None and rsi >= 75):
        rsi_text = "--" if rsi is None else f"{rsi:.1f}"
        return (
            f"過熱勿追：RSI {rsi_text} 且上檔遇壓；等回測 20MA {ma20_text} 止穩再評估，"
            f"跌破{risk_label} {risk_text} 視為失效。{context}"
        )

    if pattern == "假突破風險型" or data.get("反彈遇壓") is True:
        return (
            f"上檔壓力待消化：只有站穩 20MA {ma20_text}、{volume_text}且 MACD 再轉強才觀察；"
            f"跌破{risk_label} {risk_text} 停止追蹤。{context}"
        )

    if pattern == "趨勢突破型":
        macd_text = "MACD 動能增強" if macd_rising else "MACD 尚未同步增強"
        return (
            f"趨勢突破：{volume_text}、{macd_text}；守住 20MA {ma20_text} 可續看 ATR 目標 "
            f"{target_text}，跌破{risk_label} {risk_text} 失效。{context}"
        )

    if pattern == "回測支撐型" or data.get("回測有撐") is True:
        macd_text = "MACD 維持增強" if macd_rising else "MACD 不再轉弱"
        return (
            f"回測確認：20MA {ma20_text} 附近出現支撐；收盤守穩且{macd_text}才觀察，"
            f"先看 {target_text}，跌破{risk_label} {risk_text} 失效。{context}"
        )

    if pattern == "低檔反彈型":
        rsi_text = "--" if rsi is None else f"{rsi:.1f}"
        return (
            f"低檔反彈而非確認反轉：RSI {rsi_text}，先站回 20MA {ma20_text} 且量能轉強再評估；"
            f"跌破{risk_label} {risk_text} 停止觀察。{context}"
        )

    if score >= 70 and above_ma20:
        return (
            f"多頭續強但不追價：收盤高於 20MA {bias_to_ma20:.2f}%，等回測 {ma20_text} 不破且"
            f"{volume_text}確認；先看 {target_text}，跌破{risk_label} {risk_text} 失效。{context}"
        )

    if score >= 60:
        trigger = f"站穩 20MA {ma20_text}" if not above_ma20 else f"守住 20MA {ma20_text}"
        return (
            f"偏多待確認：{trigger}且 MACD 動能增強後再觀察；先看 {target_text}，"
            f"跌破{risk_label} {risk_text} 失效。{context}"
        )

    if score >= 45:
        return (
            f"條件尚未完整：等待站穩 20MA {ma20_text}並出現量價同步，未確認前不追價；"
            f"跌破{risk_label} {risk_text} 停止觀察。{context}"
        )

    return (
        f"訊號不足，暫不進場：至少等收盤站穩 20MA {ma20_text}且 MACD 轉強；"
        f"{risk_label} {risk_text} 以下不建立部位。{context}"
    )
