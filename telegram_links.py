"""Pure helpers for safe Telegram-to-Streamlit stock analysis links."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


DEFAULT_ANALYSIS_BASE_URL = "https://taiwan-stock-kkiczpnbzgqyyxowkx7c2u.streamlit.app/"
HELP_TEXT = (
    "請輸入台股代號或名稱，例如：\n"
    "2330\n台積電\n/stock 2330\n\n"
    "系統會回覆可直接開啟該股票解析頁的連結。"
)
_COMMAND_RE = re.compile(r"^/(?:stock|analyze)(?:@[A-Za-z0-9_]+)?\s*", re.IGNORECASE)
_DIGIT_TICKER_RE = re.compile(r"^\d{4,6}$")
_STOCK_NAME_RE = re.compile(r"^[0-9A-Za-z\u3400-\u9fff＊*+\-().·&]{1,30}$")


def extract_stock_query(text: Any) -> str:
    value = " ".join(str(text or "").strip().split())
    return _COMMAND_RE.sub("", value).strip()


def is_valid_stock_query(value: Any) -> bool:
    query = extract_stock_query(value).replace(" ", "")
    return bool(_DIGIT_TICKER_RE.fullmatch(query) or _STOCK_NAME_RE.fullmatch(query))


def build_analysis_url(query: Any, base_url: Any = "") -> str:
    """Build an HTTPS analysis URL with an encoded ticker or stock-name query."""
    stock_query = extract_stock_query(query).replace(" ", "")
    if not is_valid_stock_query(stock_query):
        raise ValueError("股票名稱或代號格式不正確")
    configured = str(base_url or DEFAULT_ANALYSIS_BASE_URL).strip()
    parsed = urlsplit(configured)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("ANALYSIS_BASE_URL 必須是 HTTPS 網址")
    params = dict(parse_qsl(parsed.query, keep_blank_values=False))
    params.pop("stock", None)
    params.pop("query", None)
    if _DIGIT_TICKER_RE.fullmatch(stock_query):
        params["stock"] = stock_query
    else:
        params["query"] = stock_query
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(params), ""))
