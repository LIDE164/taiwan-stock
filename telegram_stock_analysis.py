"""Resolve Telegram stock queries and render truthful single-stock analysis images."""

from __future__ import annotations

import io
import re
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

import pandas as pd
from PIL import Image, ImageDraw

import scanner
from analysis_core import build_score_input
from app_security import normalize_ticker
from entry_readiness import build_entry_readiness
from scan_state import build_scan_quality, latest_trading_date
from scoring import get_decision_score
from telegram_links import HELP_TEXT, extract_stock_query
from top10_telegram import (
    IMAGE_HEIGHT,
    IMAGE_WIDTH,
    PER_TRADE_MAX_LOSS,
    _clean_text,
    _fit_text,
    _font,
    _number,
    _position_size_for_max_loss,
    prediction_title,
)


_DIGIT_TICKER_RE = re.compile(r"^\d{4,6}$")


class StockQueryError(ValueError):
    """A display-safe query resolution error."""


def _scan_rows() -> tuple[list[dict[str, Any]], str]:
    if scanner.db is None:
        return [], ""
    snapshot = scanner.db.collection("market_data").document("daily_scan").get()
    payload = snapshot.to_dict() or {} if snapshot.exists else {}
    rows = [dict(row) for row in payload.get("data", []) if isinstance(row, Mapping)]
    return rows, str(payload.get("scan_date") or "")


def _name_map(rows: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    if not scanner.INDUSTRY_CACHE:
        scanner.build_industry_cache()
    names = {
        normalize_ticker(code): str(name).strip()
        for code, name in scanner.INDUSTRY_CACHE.items()
        if normalize_ticker(code) and str(name).strip()
    }
    for row in rows:
        ticker = normalize_ticker(row.get("代號"))
        name = str(row.get("名稱") or "").strip()
        if ticker and name:
            names[ticker] = name
    return names


def resolve_stock_query(
    text: Any,
    rows: Sequence[Mapping[str, Any]],
    names: Mapping[str, str] | None = None,
) -> tuple[str, str]:
    query = extract_stock_query(text)
    if not query:
        raise StockQueryError(HELP_TEXT)
    if _DIGIT_TICKER_RE.fullmatch(query):
        ticker = normalize_ticker(query)
        if not ticker:
            raise StockQueryError("股票代號格式不正確，請輸入 4～6 位數字。")
        name_map = dict(names or _name_map(rows))
        name = name_map.get(ticker, "")
        if not name:
            raise StockQueryError(f"找不到股票代號 {ticker}，請確認是否為上市或上櫃股票。")
        return ticker, name

    normalized_query = query.replace(" ", "").lower()
    name_map = dict(names or _name_map(rows))
    exact = [
        (ticker, name)
        for ticker, name in name_map.items()
        if name.replace(" ", "").lower() == normalized_query
    ]
    if len(exact) == 1:
        return exact[0]
    partial = [
        (ticker, name)
        for ticker, name in name_map.items()
        if normalized_query in name.replace(" ", "").lower()
    ]
    if len(partial) == 1:
        return partial[0]
    if partial:
        choices = "、".join(f"{ticker} {name}" for ticker, name in partial[:5])
        raise StockQueryError(f"名稱不夠明確，請改輸入代號：{choices}")
    raise StockQueryError(f"找不到「{query}」，請輸入完整股票名稱或代號。")


def _market_snapshot() -> dict[str, float]:
    try:
        frame = scanner.yf.Ticker("^TWII").history(period="4mo").dropna(subset=["Close"])
        if len(frame) < 60:
            return {}
        close = pd.to_numeric(frame["Close"], errors="coerce")
        return {
            "TWII_Close": float(close.iloc[-1]),
            "TWII_MA20": float(close.rolling(20).mean().iloc[-1]),
            "TWII_MA60": float(close.rolling(60).mean().iloc[-1]),
        }
    except Exception:
        return {}


def analyze_stock_fresh(ticker: str, name: str = "") -> dict[str, Any]:
    """Build a post-close record for one requested stock without inventing missing providers."""
    frame = scanner.get_stock_data(ticker)
    if frame is None or len(frame) < 60:
        raise StockQueryError(f"{ticker} 目前無法取得足夠行情資料，請稍後再試。")
    latest = frame.iloc[-1]
    previous = frame.iloc[-2]
    close = float(latest["Close"])
    fundamental = scanner.get_fundamental_and_industry_data(ticker, close)
    revenue = scanner.get_finmind_revenue(ticker, with_meta=True)
    institutional, institutional_status = scanner.get_institutional_trading(ticker, with_status=True)
    fund = {
        "EPS": fundamental.get("EPS"),
        "EPS_Period": fundamental.get("EPS_Period", "missing"),
        "MoM": revenue.get("mom"),
        "YoY": revenue.get("yoy"),
        **_market_snapshot(),
    }
    data = build_score_input(frame, fund)
    if not data:
        raise StockQueryError(f"{ticker} 技術欄位不足，未產生分析。")
    institutional_days = min(3, len(institutional))
    whale_net = (
        sum(int(row.get("單日合計(張)") or 0) for row in institutional[:institutional_days])
        if institutional_days
        else None
    )
    quality, confidence = build_scan_quality({
        "price": "ok",
        "fundamental": fundamental.get("_status", "missing"),
        "revenue": revenue.get("status", "missing"),
        "institutional": institutional_status,
        "market": "ok" if fund.get("TWII_Close") else "missing",
    }, institutional_days=len(institutional))
    data.update({
        "Whale_Net": whale_net,
        "Data_Quality": quality,
        "Confidence": confidence,
        "最高價": float(latest["High"]),
        "最低價": float(latest["Low"]),
        "ATR": float(latest.get("ATR", 0)),
    })
    score, label, reasons, feature = get_decision_score(data, fund, mode="post", with_reason=True)
    backtest = scanner.calc_winrate(frame)
    trading_date = latest_trading_date(frame.index)
    result = {
        "代號": ticker,
        "名稱": name or scanner.INDUSTRY_CACHE.get(ticker, ticker),
        "Data_Date": trading_date,
        "Analysis_Source": "即時重新計算",
        "Score": score,
        "評級": label,
        "產業": fundamental.get("Industry", "一般產業"),
        "開盤價": round(float(latest["Open"]), 2),
        "最高價": round(float(latest["High"]), 2),
        "最低價": round(float(latest["Low"]), 2),
        "收盤價": round(close, 2),
        "漲跌幅": round((close - float(previous["Close"])) / float(previous["Close"]) * 100, 2),
        "5MA": round(float(latest.get("5MA", 0)), 2),
        "20MA": round(float(latest.get("20MA", 0)), 2),
        "60MA": round(float(latest.get("60MA", 0)), 2),
        "MACD柱": round(float(latest.get("MACD_Hist", 0)), 3),
        "RSI": round(float(latest.get("RSI", 0)), 1),
        "ADX": round(float(latest.get("ADX", 0)), 1),
        "BIAS": round(float(data.get("BIAS", 0)), 2),
        "ATR": round(float(latest.get("ATR", 0)), 2),
        "WinRate": backtest.get("win_rate"),
        "Backtest_Samples": backtest.get("closed_signals"),
        "Backtest_Scope": backtest.get("backtest_scope"),
        "Whale_Net": whale_net,
        "Whale_Net_Days": institutional_days,
        "Institutional_Status": institutional_status,
        "Institutional_Rows": [{
            "date": row.get("_date", ""),
            "foreign": row.get("外資(張)"),
            "trust": row.get("投信(張)"),
            "dealer": row.get("自營商(張)"),
            "total": row.get("單日合計(張)"),
            "source": row.get("_source", ""),
        } for row in institutional[:5]],
        "EPS": fundamental.get("EPS"),
        "EPS_Period": fundamental.get("EPS_Period", "missing"),
        "MoM": revenue.get("mom"),
        "YoY": revenue.get("yoy"),
        "Revenue_Period": revenue.get("period", ""),
        "Revenue_Status": revenue.get("status", "missing"),
        "Confidence": confidence,
        "Data_Quality": quality,
        "Feature": feature,
        "Reasons": reasons,
        "Entry_Pattern": data.get("Entry_Pattern", ""),
        "Signal_Conflict": data.get("Signal_Conflict", ""),
        "Est_Vol_Ratio": data.get("Est_Vol_Ratio"),
        "Volume_Confirmed": bool(data.get("Volume_Confirmed")),
        "Tomorrow_Plan": data.get("Tomorrow_Plan", {}),
    }
    result.update(build_entry_readiness(result))
    return result


def get_stock_analysis(text: Any) -> dict[str, Any]:
    rows, scan_date = _scan_rows()
    names = _name_map(rows)
    ticker, name = resolve_stock_query(text, rows, names)
    cached = next((dict(row) for row in rows if normalize_ticker(row.get("代號")) == ticker), None)
    if cached:
        cached["名稱"] = name or cached.get("名稱") or ticker
        cached["Data_Date"] = cached.get("Data_Date") or scan_date
        cached["Analysis_Source"] = "最新正式掃描"
        # The ranked scan stores only the fields needed by the list. Enrich the image from
        # authentic OHLC history; a provider failure simply leaves the optional fields blank.
        frame = scanner.get_stock_data(ticker)
        if frame is not None and not frame.empty:
            latest = frame.iloc[-1]
            cached.update({
                "5MA": round(float(latest.get("5MA", 0)), 2),
                "20MA": round(float(latest.get("20MA", 0)), 2),
                "60MA": round(float(latest.get("60MA", 0)), 2),
                "MACD柱": round(float(latest.get("MACD_Hist", 0)), 3),
                "RSI": round(float(latest.get("RSI", 0)), 1),
                "ADX": round(float(latest.get("ADX", 0)), 1),
                "BIAS": round(float(latest.get("BIAS_20", 0)), 2),
                "ATR": round(float(latest.get("ATR", 0)), 2),
            })
        return cached
    return analyze_stock_fresh(ticker, name)


def _display_number(value: Any, decimals: int = 1, *, signed: bool = False) -> str:
    number = _number(value)
    if number is None:
        return "--"
    prefix = "+" if signed and number > 0 else ""
    return f"{prefix}{number:,.{decimals}f}"


def _credibility(samples: int | None) -> str:
    if samples is None:
        return "資料缺失"
    if samples < 10:
        return "樣本嚴重不足"
    if samples < 30:
        return "僅供參考"
    if samples < 50:
        return "中等可信"
    return "統計較穩定"


def _wrap_lines(draw: ImageDraw.ImageDraw, text: str, font, width: int, limit: int) -> list[str]:
    source = " ".join(str(text or "").split())
    if not source:
        return []
    lines: list[str] = []
    current = ""
    for char in source:
        candidate = current + char
        if current and draw.textlength(candidate, font=font) > width:
            lines.append(current)
            current = char
            if len(lines) == limit:
                break
        else:
            current = candidate
    if current and len(lines) < limit:
        lines.append(current)
    if len(lines) == limit and sum(len(line) for line in lines) < len(source):
        lines[-1] = _fit_text(draw, lines[-1] + "…", font, width)
    return lines


def render_stock_analysis_image(record: Mapping[str, Any]) -> bytes:
    """Render one stock analysis without converting unavailable fields to zero."""
    data = dict(record)
    ticker = _clean_text(data.get("代號"))
    name = _clean_text(data.get("名稱"), ticker)
    data_date = _clean_text(data.get("Data_Date"), date.today().isoformat())
    score = _number(data.get("Score"))
    close = _number(data.get("收盤價"))
    stop = _number(data.get("Entry_Stop"))
    shares, _, estimated_loss = _position_size_for_max_loss(close, stop, PER_TRADE_MAX_LOSS)
    samples_number = _number(data.get("Backtest_Samples"))
    samples = int(samples_number) if samples_number is not None and samples_number >= 0 else None
    win_rate = _number(data.get("WinRate")) if samples and samples > 0 else None
    status = _clean_text(data.get("Entry_Status"), "條件不足")
    status_color = "#FCA5A5" if status == "現在可執行" else "#FBBF24"

    image = Image.new("RGB", (IMAGE_WIDTH, IMAGE_HEIGHT), "#070D1A")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((30, 26, IMAGE_WIDTH - 30, 160), radius=28, fill="#0F172A", outline="#1E293B", width=2)
    draw.text((62, 48), prediction_title(data_date), font=_font(20, True), fill="#60A5FA")
    draw.text((62, 82), _fit_text(draw, f"{ticker} {name}", _font(43, True), 610), font=_font(43, True), fill="#F8FAFC")
    draw.text((IMAGE_WIDTH - 62, 47), f"資料日 {data_date}", font=_font(20, True), fill="#FBBF24", anchor="ra")
    draw.text((IMAGE_WIDTH - 62, 88), "--" if score is None else f"{score:g} 分", font=_font(42, True), fill="#F87171", anchor="ra")
    draw.text((IMAGE_WIDTH - 62, 133), _clean_text(data.get("Analysis_Source"), "可驗證資料"), font=_font(17), fill="#94A3B8", anchor="ra")

    draw.rounded_rectangle((42, 184, 1038, 330), radius=22, fill="#0F172A", outline="#29364D", width=2)
    draw.text((68, 205), status, font=_font(28, True), fill=status_color)
    draw.text((68, 247), _fit_text(draw, _clean_text(data.get("Entry_Reason"), "尚無進場說明"), _font(20), 910), font=_font(20), fill="#CBD5E1")
    summary = (
        (68, "現價", "--" if close is None else f"{close:g}", "#F8FAFC"),
        (250, "漲跌", f"{_display_number(data.get('漲跌幅'), 1, signed=True)}%", "#F87171"),
        (430, "評級", _clean_text(data.get("評級"), "--").replace("🟢", "").replace("🟡", "").replace("⚪", "").strip(), "#4ADE80"),
        (670, "型態", _clean_text(data.get("Entry_Pattern"), "--"), "#A5B4FC"),
        (880, "信心", f"{_display_number(data.get('Confidence'), 0)}%", "#60A5FA"),
    )
    for x, label, value, color in summary:
        draw.text((x, 286), label, font=_font(15), fill="#64748B")
        draw.text((x, 306), _fit_text(draw, value, _font(19, True), 155), font=_font(19, True), fill=color)

    draw.rounded_rectangle((42, 352, 528, 640), radius=22, fill="#0F172A", outline="#1E293B", width=2)
    draw.text((68, 376), "技術面", font=_font(27, True), fill="#60A5FA")
    technical = (
        ("5MA", _display_number(data.get("5MA"), 2)),
        ("20MA", _display_number(data.get("20MA"), 2)),
        ("60MA", _display_number(data.get("60MA"), 2)),
        ("RSI", _display_number(data.get("RSI"), 1)),
        ("ADX", _display_number(data.get("ADX"), 1)),
        ("20MA乖離", f"{_display_number(data.get('BIAS'), 2, signed=True)}%"),
        ("MACD柱", _display_number(data.get("MACD柱", data.get("MACD_Hist")), 3, signed=True)),
        ("ATR", _display_number(data.get("ATR"), 2)),
    )
    for index, (label, value) in enumerate(technical):
        column, row = index % 2, index // 2
        x, y = 68 + column * 230, 424 + row * 50
        draw.text((x, y), label, font=_font(16), fill="#64748B")
        draw.text((x + 105, y), value, font=_font(19, True), fill="#E2E8F0")

    draw.rounded_rectangle((550, 352, 1038, 640), radius=22, fill="#0F172A", outline="#3F2631", width=2)
    draw.text((576, 376), "執行與資金控管", font=_font(27, True), fill="#F87171")
    low = _number(data.get("Entry_Low"))
    high = _number(data.get("Entry_High"))
    entry_zone = f"{low:g}–{high:g}" if low is not None and high is not None else "--"
    execution = (
        ("建議買入區", entry_zone, "#F8FAFC"),
        ("停損", _display_number(stop, 2), "#4ADE80"),
        ("策略目標", _display_number(data.get("Entry_Target"), 2), "#F87171"),
        ("建議零股", f"{shares:,} 股" if shares > 0 else "無法計算", "#FBBF24"),
        ("停損最大虧損", "--" if estimated_loss is None else f"${estimated_loss:,.0f}", "#F8FAFC"),
    )
    for index, (label, value, color) in enumerate(execution):
        y = 423 + index * 40
        draw.text((576, y), label, font=_font(16), fill="#64748B")
        draw.text((770, y), value, font=_font(19, True), fill=color)

    draw.rounded_rectangle((42, 662, 528, 930), radius=22, fill="#0F172A", outline="#1E293B", width=2)
    draw.text((68, 686), "基本面與營收", font=_font(27, True), fill="#A5B4FC")
    eps = _number(data.get("EPS"))
    mom = _number(data.get("MoM"))
    yoy = _number(data.get("YoY"))
    fundamentals = (
        ("EPS", "--" if eps is None else f"{eps:g}"),
        ("月增 MoM", "--" if mom is None else f"{mom:+.2f}%"),
        ("年增 YoY", "--" if yoy is None else f"{yoy:+.2f}%"),
        ("營收月份", _clean_text(data.get("Revenue_Period"), "--")),
        ("產業", _clean_text(data.get("產業"), "--")),
    )
    for index, (label, value) in enumerate(fundamentals):
        y = 738 + index * 37
        draw.text((68, y), label, font=_font(16), fill="#64748B")
        draw.text((230, y), _fit_text(draw, value, _font(19, True), 255), font=_font(19, True), fill="#E2E8F0")

    draw.rounded_rectangle((550, 662, 1038, 930), radius=22, fill="#0F172A", outline="#1E293B", width=2)
    draw.text((576, 686), "籌碼與回測", font=_font(27, True), fill="#4ADE80")
    whale = _number(data.get("Whale_Net"))
    whale_days = _number(data.get("Whale_Net_Days"))
    chip = "--" if whale is None else f"{whale:+,.0f} 張 / {int(whale_days or 0)}日"
    stats = (
        ("三大法人合計", chip),
        ("籌碼資料", _clean_text(data.get("Institutional_Status"), "missing")),
        ("技術勝率", "--" if win_rate is None else f"{win_rate:.1f}%"),
        ("回測樣本", "--" if samples is None else f"{samples}｜{_credibility(samples)}"),
        ("訊號衝突", _clean_text(data.get("Signal_Conflict"), "--")),
    )
    for index, (label, value) in enumerate(stats):
        y = 738 + index * 37
        draw.text((576, y), label, font=_font(16), fill="#64748B")
        draw.text((748, y), _fit_text(draw, value, _font(19, True), 255), font=_font(19, True), fill="#E2E8F0")

    draw.rounded_rectangle((42, 952, 1038, 1238), radius=22, fill="#0F172A", outline="#1E293B", width=2)
    draw.text((68, 976), "量化解析重點", font=_font(27, True), fill="#FBBF24")
    reasons = data.get("Reasons") if isinstance(data.get("Reasons"), Sequence) and not isinstance(data.get("Reasons"), str) else []
    if not reasons:
        reasons = [_clean_text(data.get("Feature"), "目前沒有可驗證的解析原因")]
    y = 1025
    for reason in list(reasons)[:4]:
        cleaned = _clean_text(reason).replace("🔥", "").replace("✅", "").replace("⚠️", "").strip()
        cleaned = re.sub(r"^[^0-9A-Za-z\u4e00-\u9fff]+", "", cleaned)
        lines = _wrap_lines(draw, f"• {cleaned}", _font(18), 920, 2)
        for line in lines:
            draw.text((70, y), line, font=_font(18), fill="#CBD5E1")
            y += 27
        y += 7
        if y > 1200:
            break

    draw.line((54, 1275, IMAGE_WIDTH - 54, 1275), fill="#1E293B", width=2)
    draw.text((54, 1295), "資料缺失一律顯示 --；不以 0 或推估值冒充真實資料。", font=_font(17), fill="#94A3B8")
    draw.text((54, 1327), "股數依每筆停損價差最多虧損 $5,000 計算；未計滑價、手續費與交易稅。", font=_font(17), fill="#FBBF24")
    draw.text((IMAGE_WIDTH - 54, 1363), "僅供研究參考", font=_font(17, True), fill="#F87171", anchor="ra")

    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()
