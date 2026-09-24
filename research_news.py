"""On-demand official company announcements, not a full-news or sentiment feed."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
import re

from market_http import http_get

TPE = timezone(timedelta(hours=8))
SOURCES = {
    "TWSE": "https://openapi.twse.com.tw/v1/opendata/t187ap04_L",
    "TPEx": "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap04_O",
}


def official_date(value):
    text = str(value or "").strip()
    if re.fullmatch(r"\d{7}", text):
        text = f"{int(text[:3]) + 1911}{text[3:]}"
    if re.fullmatch(r"\d{8}", text):
        return datetime.strptime(text, "%Y%m%d").date().isoformat()
    return None


def classify_event(title):
    cases = (
        (("財務報告", "財報", "營收", "損益"), "財務營運", "比較公告數值與前期、一次性項目；不能由標題判定優於預期。", "追蹤獲利、營業現金流與負債是否同步改善。"),
        (("收購", "併購", "投資", "增資"), "投資與資本", "檢查交易金額、資金來源、稀釋程度及尚待核准條件。", "以整合成本、回收年限、負債及營運結果驗證效益。"),
        (("澄清", "訴訟", "停工", "處分", "違約"), "不確定性事件", "先讀原公告核對涉事範圍；事件未釐清前不僅憑新聞增加曝險。", "追蹤實際損失、營運中斷與後續處理，不把傳聞當事實。"),
        (("股利", "除息", "除權"), "股利與公司行動", "核對除權息日期，區分價格調整與真正損益。", "檢查配息與現金流可持續性，配息率不等於總報酬。"),
    )
    for keywords, category, short, long in cases:
        if any(word in title for word in keywords):
            return {"category": category, "short_term_check": short, "long_term_check": long}
    return {"category": "待人工判讀", "short_term_check": "先核對公告全文、事件日期及是否已反映於價格。",
            "long_term_check": "確認是否實質影響營收、利潤、現金流或治理；未確認前不量化影響。"}


def parse_announcements(rows, ticker, source, as_of):
    if not isinstance(rows, list):
        raise ValueError("公告來源不是列表")
    events, invalid, future = [], 0, 0
    for raw in rows:
        if not isinstance(raw, dict):
            invalid += 1
            continue
        row = {str(key).strip(): value for key, value in raw.items()}
        code = str(row.get("公司代號") or row.get("SecuritiesCompanyCode") or "")
        if not code:
            invalid += 1
            continue
        if code != ticker:
            continue
        try:
            published_day = official_date(row.get("發言日期"))
            clock = str(row.get("發言時間") or "").strip()
            if not published_day or not re.fullmatch(r"\d{1,6}", clock):
                raise ValueError("缺發布時間")
            published = datetime.strptime(published_day + clock.zfill(6), "%Y-%m-%d%H%M%S").replace(tzinfo=TPE)
            title = str(row.get("主旨") or "").strip()
            if not title:
                raise ValueError("缺公告主旨")
            if published > as_of:
                future += 1
                continue
            event_day = official_date(row.get("事實發生日"))
            events.append({"ticker": ticker, "title": title[:1500], "published_at": published.isoformat(),
                           "event_date": event_day, "source": source, "source_url": SOURCES[source],
                           "source_link_kind": "官方來源資料集（以代號、發布時間、主旨定位原公告）",
                           "details": str(row.get("說明") or "")[:15000],
                           "interpretation": classify_event(title), "estimated_price_range": None,
                           "allocation": "新聞本身不決定部位；先通過價格計畫與投資組合風險檢查。"})
        except (ValueError, TypeError):
            invalid += 1
    return events, invalid, future


def fetch_company_events(ticker, as_of=None):
    if not re.fullmatch(r"\d{4,6}", str(ticker)):
        raise ValueError("公告查詢只接受股票代號")
    as_of = as_of or datetime.now(TPE)
    if not isinstance(as_of, datetime) or as_of.tzinfo is None:
        raise ValueError("查詢時間須包含時區")
    events, statuses = [], {}
    for source, url in SOURCES.items():
        try:
            response = http_get(url, timeout=10)
            response.raise_for_status()
            parsed, invalid, future = parse_announcements(response.json(), str(ticker), source, as_of)
            events.extend(parsed)
            statuses[source] = {"status": "partial" if invalid else "ok", "invalid_rows": invalid,
                                "future_rows_excluded": future, "source_url": url}
        except Exception as exc:
            statuses[source] = {"status": "unavailable", "error_type": type(exc).__name__, "source_url": url}
    unique = {(e["source"], e["published_at"], e["title"]): e for e in events}
    return {"as_of": as_of.isoformat(), "events": sorted(unique.values(), key=lambda e: e["published_at"], reverse=True)[:20],
            "source_status": statuses,
            "coverage_note": "僅官方端點目前提供的重大訊息，非完整媒體新聞/歷史新聞庫；空白不代表沒有風險。發布時間與事件日分開呈現。分類為關鍵字研究提示，不是利多利空判定；無事件研究時不捏造預期漲跌區間。"}
