"""Validation, escaping, and anonymous/authenticated data-scope helpers."""

from __future__ import annotations

import hashlib
import html
import re
from datetime import date
from typing import Any, Mapping
from urllib.parse import urlencode


_TICKER_RE = re.compile(r"^(?:[A-Z0-9]{2,10}|\^[A-Z0-9]{1,9})$")
_COLOR_RE = re.compile(r"^(?:#[0-9A-Fa-f]{3,8}|rgba?\([0-9.,% ]+\)|[a-zA-Z]{3,20})$")
_ALLOWED_MODES = {"intraday", "realtime", "post"}


def normalize_ticker(value: Any) -> str:
    """Normalize a Taiwan ticker and reject characters unsafe for URLs/HTML."""
    ticker = str(value or "").strip().upper()
    if ticker.endswith(".TWO"):
        ticker = ticker[:-4]
    elif ticker.endswith(".TW"):
        ticker = ticker[:-3]
    return ticker if _TICKER_RE.fullmatch(ticker) else ""


def safe_iso_date(value: Any) -> str:
    text = str(value or "").strip()
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return ""


def safe_mode(value: Any, default: str = "") -> str:
    mode = str(value or "").strip().lower()
    return mode if mode in _ALLOWED_MODES else default


def escape_html(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def safe_css_color(value: Any, default: str = "#94A3B8") -> str:
    color = str(value or "").strip()
    return color if _COLOR_RE.fullmatch(color) else default


def build_stock_url(ticker: Any, **params: Any) -> str:
    code = normalize_ticker(ticker)
    if not code:
        return "/"
    query: dict[str, str] = {"stock": code}
    for key, value in params.items():
        if value is None or value == "":
            continue
        query[str(key)] = str(value)
    return "/?" + urlencode(query)


def resolve_stock_identifier(
    value: Any,
    stock_names: Mapping[str, Any],
) -> tuple[str, str]:
    """Resolve a ticker or a unique stock-name match for public analysis links."""
    query = "".join(str(value or "").strip().split())
    ticker = normalize_ticker(query)
    if ticker:
        return ticker, "ok"
    normalized_query = query.casefold()
    if not normalized_query:
        return "", "empty"
    normalized_names = [
        (normalize_ticker(code), "".join(str(name or "").split()).casefold())
        for code, name in stock_names.items()
    ]
    exact = [code for code, name in normalized_names if code and name == normalized_query]
    if len(exact) == 1:
        return exact[0], "ok"
    partial = [code for code, name in normalized_names if code and normalized_query in name]
    if len(partial) == 1:
        return partial[0], "ok"
    if exact or partial:
        return "", "ambiguous"
    return "", "not_found"


def scoped_document_name(base_name: str, identity: Mapping[str, Any] | None, fallback: str) -> str:
    """Create a non-identifying Firestore document id for one user/session."""
    identity = identity or {}
    raw_identity = str(identity.get("sub") or identity.get("email") or fallback or "anonymous")
    digest = hashlib.sha256(raw_identity.encode("utf-8")).hexdigest()[:20]
    safe_base = re.sub(r"[^a-zA-Z0-9_-]", "_", str(base_name))[:40] or "data"
    return f"{safe_base}_{digest}"
