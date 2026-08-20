"""Shared, retry-aware HTTP access for public market-data providers."""

from __future__ import annotations

import threading
import time
import ssl
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


RETRYABLE_STATUS_CODES = (429, 500, 502, 503, 504)
_THREAD_LOCAL = threading.local()


class _LegacyCertificateChainAdapter(HTTPAdapter):
    """Keep TLS verification while tolerating TPEX's legacy chain metadata."""

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        context = ssl.create_default_context()
        strict_flag = getattr(ssl, "VERIFY_X509_STRICT", 0)
        if strict_flag:
            context.verify_flags &= ~strict_flag
        pool_kwargs["ssl_context"] = context
        self.ssl_context = context
        super().init_poolmanager(connections, maxsize, block=block, **pool_kwargs)


def _build_session() -> requests.Session:
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=0.6,
        status_forcelist=RETRYABLE_STATUS_CODES,
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
    session = requests.Session()
    session.headers.update({"User-Agent": "taiwan-stock-radar/1.0"})
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    tpex_adapter = _LegacyCertificateChainAdapter(
        max_retries=retry,
        pool_connections=5,
        pool_maxsize=10,
    )
    session.mount("https://www.tpex.org.tw/", tpex_adapter)
    return session


def get_http_session() -> requests.Session:
    """Return one Session per worker thread; requests.Session is not thread-safe."""
    session = getattr(_THREAD_LOCAL, "session", None)
    if session is None:
        session = _build_session()
        _THREAD_LOCAL.session = session
    return session


def http_get(url: str, *, timeout: float = 10, **kwargs: Any) -> requests.Response:
    """GET with bounded retries, exponential backoff, and Retry-After support."""
    return get_http_session().get(url, timeout=timeout, **kwargs)


def call_with_backoff(operation, *, attempts: int = 3, backoff_factor: float = 0.5):
    """Retry non-requests providers such as yfinance with a bounded backoff."""
    attempts = max(1, int(attempts))
    for attempt in range(attempts):
        try:
            return operation()
        except Exception:
            if attempt + 1 >= attempts:
                raise
            time.sleep(max(0.0, backoff_factor) * (2 ** attempt))
