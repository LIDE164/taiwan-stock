"""Render the daily Top-10 ranking as a mobile-friendly PNG and send it to Telegram."""

from __future__ import annotations

import io
import math
import os
from collections.abc import Mapping, Sequence
from functools import lru_cache
from typing import Any

import requests
from PIL import Image, ImageDraw, ImageFont


IMAGE_WIDTH = 1080
IMAGE_HEIGHT = 1400
CARD_LEFT = 42
CARD_WIDTH = 996
CARD_HEIGHT = 100
CARD_GAP = 10


def _number(value: Any) -> float | None:
    try:
        parsed = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _clean_text(value: Any, fallback: str = "--") -> str:
    text = " ".join(str(value or "").split()).strip()
    return text or fallback


def _credibility(sample_count: int | None) -> tuple[str, str]:
    if sample_count is None:
        return "資料缺失", "#94A3B8"
    if sample_count < 10:
        return "樣本嚴重不足", "#F87171"
    if sample_count < 30:
        return "僅供參考", "#FACC15"
    if sample_count < 50:
        return "中等可信", "#60A5FA"
    return "統計較穩定", "#4ADE80"


def build_top10_display_rows(results: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Create truthful display values; missing backtests never become a 0% win rate."""
    rows: list[dict[str, Any]] = []
    for fallback_rank, record in enumerate(results[:10], start=1):
        rank_number = _number(record.get("Rank"))
        rank = int(rank_number) if rank_number is not None and rank_number > 0 else fallback_rank
        ticker = _clean_text(record.get("代號"))
        name = _clean_text(record.get("名稱"), ticker)
        score = _number(record.get("Score"))
        close = _number(record.get("收盤價"))
        change = _number(record.get("漲跌幅"))
        samples_number = _number(record.get("Backtest_Samples"))
        samples = int(samples_number) if samples_number is not None and samples_number >= 0 else None
        win_rate = _number(record.get("WinRate"))
        if samples is None or samples <= 0 or win_rate is None or not 0 <= win_rate <= 100:
            win_rate = None
        credibility, credibility_color = _credibility(samples)
        rating = _clean_text(record.get("評級"), "觀察")
        for marker in ("🟢", "🟡", "⚪", "🔴"):
            rating = rating.replace(marker, "").strip()
        rows.append({
            "rank": rank,
            "ticker": ticker,
            "name": name,
            "industry": _clean_text(record.get("產業"), "未分類"),
            "score_text": "--" if score is None else f"{score:g} 分",
            "rating": rating,
            "close_text": "--" if close is None else f"{close:g}",
            "change_text": "--" if change is None else f"{change:+.1f}%",
            "change_value": change,
            "win_rate_text": "--" if win_rate is None else f"{win_rate:.1f}%",
            "sample_text": "--" if samples is None else str(samples),
            "credibility": credibility,
            "credibility_color": credibility_color,
            "entry_status": _clean_text(record.get("Entry_Status"), "條件未提供"),
        })
    return rows


def _font_candidates(bold: bool) -> tuple[str, ...]:
    configured = os.getenv("TOP10_FONT_PATH", "").strip()
    common = (
        "C:/Windows/Fonts/msjhbd.ttc" if bold else "C:/Windows/Fonts/msjh.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc" if bold else
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/System/Library/Fonts/PingFang.ttc",
    )
    return ((configured,) if configured else ()) + common


@lru_cache(maxsize=32)
def _font(size: int, bold: bool = False):
    for candidate in _font_candidates(bold):
        if candidate and os.path.exists(candidate):
            try:
                return ImageFont.truetype(candidate, size=size)
            except OSError:
                continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _fit_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    value = _clean_text(text)
    if draw.textlength(value, font=font) <= max_width:
        return value
    suffix = "…"
    while value and draw.textlength(value + suffix, font=font) > max_width:
        value = value[:-1]
    return (value + suffix) if value else suffix


def _rank_colors(rank: int) -> tuple[str, str]:
    if rank == 1:
        return "#F59E0B", "#1F2937"
    if rank == 2:
        return "#CBD5E1", "#1F2937"
    if rank == 3:
        return "#D97706", "#FFF7ED"
    return "#334155", "#E2E8F0"


def render_top10_image(results: Sequence[Mapping[str, Any]], trading_date: str) -> bytes:
    """Return a PNG report containing at most ten ranking rows."""
    rows = build_top10_display_rows(results)
    if not rows:
        raise ValueError("Top10 榜單為空，不能生成 Telegram 圖片")

    image = Image.new("RGB", (IMAGE_WIDTH, IMAGE_HEIGHT), "#070D1A")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((30, 26, IMAGE_WIDTH - 30, 145), radius=28, fill="#0F172A", outline="#1E293B", width=2)
    draw.text((62, 48), "TAIWAN STOCK RADAR", font=_font(18, True), fill="#60A5FA")
    draw.text((62, 76), "每日量化 Top 10", font=_font(39, True), fill="#F8FAFC")
    draw.text((IMAGE_WIDTH - 62, 53), _clean_text(trading_date), font=_font(24, True), fill="#FBBF24", anchor="ra")
    draw.text((IMAGE_WIDTH - 62, 91), "盤後正式榜單｜依量化分數排序", font=_font(19), fill="#94A3B8", anchor="ra")

    start_y = 168
    for index, row in enumerate(rows):
        top = start_y + index * (CARD_HEIGHT + CARD_GAP)
        bottom = top + CARD_HEIGHT
        draw.rounded_rectangle(
            (CARD_LEFT, top, CARD_LEFT + CARD_WIDTH, bottom),
            radius=18,
            fill="#0F172A",
            outline="#1E293B",
            width=2,
        )
        badge_fill, badge_text = _rank_colors(row["rank"])
        draw.ellipse((58, top + 24, 108, top + 74), fill=badge_fill)
        draw.text((83, top + 49), str(row["rank"]), font=_font(23, True), fill=badge_text, anchor="mm")

        stock_text = _fit_text(draw, f"{row['ticker']}  {row['name']}", _font(27, True), 330)
        draw.text((128, top + 14), stock_text, font=_font(27, True), fill="#F8FAFC")

        entry_text = _fit_text(draw, row["entry_status"], _font(18, True), 220)
        entry_color = "#FCA5A5" if row["entry_status"] == "現在可執行" else "#FDE68A"
        if row["entry_status"] in ("條件不足", "條件未提供", "待新掃描"):
            entry_color = "#CBD5E1"
        draw.rounded_rectangle((470, top + 12, 716, top + 45), radius=16, fill="#172033", outline="#334155")
        draw.text((593, top + 28), entry_text, font=_font(18, True), fill=entry_color, anchor="mm")

        score_color = "#F87171" if row["score_text"] != "--" else "#94A3B8"
        draw.text((1000, top + 13), row["score_text"], font=_font(29, True), fill=score_color, anchor="ra")
        draw.text((1000, top + 51), _fit_text(draw, row["rating"], _font(17, True), 155), font=_font(17, True), fill="#4ADE80", anchor="ra")

        change = row["change_value"]
        change_color = "#94A3B8" if change is None else ("#F87171" if change >= 0 else "#4ADE80")
        labels = (
            (128, "收盤", row["close_text"], "#E2E8F0"),
            (265, "漲跌", row["change_text"], change_color),
            (405, "技術勝率", row["win_rate_text"], "#60A5FA"),
            (576, "樣本", row["sample_text"], "#E2E8F0"),
            (682, "可信度", row["credibility"], row["credibility_color"]),
            (880, "產業", _fit_text(draw, row["industry"], _font(17, True), 115), "#A5B4FC"),
        )
        for x, label, value, color in labels:
            draw.text((x, top + 57), label, font=_font(14), fill="#64748B")
            draw.text((x, top + 76), value, font=_font(17, True), fill=color)

    footer_y = start_y + 10 * (CARD_HEIGHT + CARD_GAP) + 14
    draw.line((54, footer_y, IMAGE_WIDTH - 54, footer_y), fill="#1E293B", width=2)
    draw.text((54, footer_y + 18), "技術勝率為逐步前推回測結果，不代表未來績效。缺失資料一律顯示 --。", font=_font(17), fill="#94A3B8")
    draw.text((IMAGE_WIDTH - 54, footer_y + 18), "僅供研究參考", font=_font(17, True), fill="#FBBF24", anchor="ra")

    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def send_top10_photo(
    results: Sequence[Mapping[str, Any]],
    trading_date: str,
    bot_token: Any,
    chat_id: Any,
    *,
    session: requests.Session | None = None,
) -> int | None:
    """Send the rendered ranking through Telegram's sendPhoto API."""
    token = str(bot_token or "").strip()
    target_chat = str(chat_id or "").strip()
    if not token or not target_chat:
        raise RuntimeError("Telegram 設定缺少 TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHAT_ID")

    png = render_top10_image(results, trading_date)
    client = session or requests.Session()
    try:
        response = client.post(
            f"https://api.telegram.org/bot{token}/sendPhoto",
            data={
                "chat_id": target_chat,
                "caption": f"台股每日 Top 10｜{trading_date}\n盤後正式榜單；技術勝率為歷史回測，僅供研究參考。",
            },
            files={"photo": (f"top10-{trading_date}.png", png, "image/png")},
            timeout=30,
        )
    except Exception as error:
        raise RuntimeError(f"Telegram 圖片發送連線失敗（{type(error).__name__}）") from None

    if response.status_code < 200 or response.status_code >= 300:
        raise RuntimeError(f"Telegram 圖片發送失敗（HTTP {response.status_code}）")
    try:
        payload = response.json()
    except (TypeError, ValueError):
        raise RuntimeError("Telegram 回應格式錯誤") from None
    if not isinstance(payload, Mapping) or not payload.get("ok"):
        raise RuntimeError("Telegram API 未確認圖片發送成功")
    result = payload.get("result")
    message_id = result.get("message_id") if isinstance(result, Mapping) else None
    return int(message_id) if isinstance(message_id, (int, float)) else None
