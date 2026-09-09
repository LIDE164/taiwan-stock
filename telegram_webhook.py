"""Cloud Run Telegram webhook for on-demand Taiwan stock analysis images."""

from __future__ import annotations

import hmac
import json
import logging
import os
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import requests
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

import scanner
from telegram_links import (
    HELP_TEXT,
    build_analysis_url,
    extract_stock_query,
    is_valid_stock_query,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
LOGGER = logging.getLogger(__name__)
UPDATE_MAX_ATTEMPTS = 3
UPDATE_LEASE_SECONDS = 120
UPDATE_TTL_DAYS = 30


def _secret(name: str) -> str:
    return str(os.getenv(name) or scanner.get_secret(name, "") or "").strip()


def _telegram_api(method: str, *, data: Mapping[str, Any], files: Mapping[str, Any] | None = None) -> dict[str, Any]:
    token = _secret("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Telegram Bot Token 未設定")
    response = requests.post(
        f"https://api.telegram.org/bot{token}/{method}",
        data=dict(data),
        files=dict(files or {}),
        timeout=90,
    )
    if response.status_code < 200 or response.status_code >= 300:
        raise RuntimeError(f"Telegram API {method} 回應 HTTP {response.status_code}")
    payload = response.json()
    if not isinstance(payload, Mapping) or not payload.get("ok"):
        raise RuntimeError(f"Telegram API {method} 未確認成功")
    return dict(payload)


def _send_text(chat_id: str, text: str, reply_to: int | None = None) -> None:
    data: dict[str, Any] = {"chat_id": chat_id, "text": text}
    if reply_to is not None:
        data["reply_parameters"] = f'{{"message_id":{reply_to}}}'
    _telegram_api("sendMessage", data=data)


def _send_analysis_link(chat_id: str, query: str, reply_to: int | None = None) -> None:
    analysis_url = build_analysis_url(query, _secret("ANALYSIS_BASE_URL"))
    data: dict[str, Any] = {
        "chat_id": chat_id,
        "text": f"{query} 股票解析連結：\n{analysis_url}",
        "reply_markup": json.dumps({
            "inline_keyboard": [[{"text": "開啟股票解析", "url": analysis_url}]],
        }, ensure_ascii=False),
    }
    if reply_to is not None:
        data["reply_parameters"] = f'{{"message_id":{reply_to}}}'
    _telegram_api("sendMessage", data=data)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _as_utc_datetime(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _claim_decision(
    payload: Mapping[str, Any] | None,
    *,
    now: datetime,
    max_attempts: int = UPDATE_MAX_ATTEMPTS,
) -> tuple[str, int]:
    """Return a transactional claim decision and the next/current attempt."""
    state = dict(payload or {})
    try:
        attempts = max(0, int(state.get("attempts") or 0))
    except (TypeError, ValueError):
        attempts = 0
    status = str(state.get("status") or "").strip().lower()
    if status in {"sent", "rejected", "failed_terminal"}:
        return "done", attempts
    if attempts >= max(1, int(max_attempts)):
        return "exhausted", attempts
    if status == "processing":
        lease_expires_at = _as_utc_datetime(state.get("lease_expires_at"))
        if lease_expires_at is not None and lease_expires_at > now:
            return "busy", attempts
    return "claim", attempts + 1


def _claim_update(update_id: int) -> int:
    """Claim an update transactionally.

    A positive return value is the attempt number, ``0`` means the update is
    already terminal, and ``-1`` means another worker still owns a live lease.
    Failed work and expired processing leases may be reclaimed, up to the
    bounded attempt limit.
    """
    if scanner.db is None:
        return -1
    reference = scanner.db.collection("telegram_bot_updates").document(str(update_id))
    transaction = scanner.db.transaction()
    now = _utcnow()

    @scanner.firestore.transactional
    def claim(transaction):
        snapshot = reference.get(transaction=transaction)
        existing = snapshot.to_dict() or {} if snapshot.exists else {}
        decision, attempt = _claim_decision(existing, now=now)
        if decision != "claim":
            return -1 if decision == "busy" else 0
        values = {
            "status": "processing",
            "update_id": update_id,
            "attempts": attempt,
            "lease_expires_at": now + timedelta(seconds=UPDATE_LEASE_SECONDS),
            "claimed_at": scanner.firestore.SERVER_TIMESTAMP,
            "last_error_kind": "",
        }
        if not snapshot.exists:
            values["created_at"] = scanner.firestore.SERVER_TIMESTAMP
        transaction.set(reference, values, merge=True)
        return attempt

    return int(claim(transaction))


def _finish_update(update_id: int, status: str, ticker: str = "", error_kind: str = "") -> None:
    if scanner.db is None:
        return
    now = _utcnow()
    scanner.db.collection("telegram_bot_updates").document(str(update_id)).set({
        "status": status,
        "ticker": ticker,
        "last_error_kind": error_kind,
        "finished_at": scanner.firestore.SERVER_TIMESTAMP,
        "lease_expires_at": now,
        # Administrators can enable a Firestore TTL policy for this field.
        "expire_at": now + timedelta(days=UPDATE_TTL_DAYS),
    }, merge=True)


def _handle_transient_failure(
    update_id: int,
    chat_id: str,
    message_id: int | None,
    attempt: int,
    error: Exception,
) -> bool:
    """Record retryable work and stop retrying after a small fixed budget."""
    error_kind = type(error).__name__
    LOGGER.exception("Telegram stock-link reply failed: %s", error_kind)
    if attempt < UPDATE_MAX_ATTEMPTS:
        _finish_update(update_id, "failed", error_kind=error_kind)
        return False

    try:
        _send_text(chat_id, "目前暫時無法產生解析連結，請稍後再試。", message_id)
    except Exception as final_error:  # noqa: BLE001 - final best-effort Telegram notice
        LOGGER.error("Telegram terminal failure notice failed: %s", type(final_error).__name__)
    _finish_update(update_id, "failed_terminal", error_kind=error_kind)
    return True


def _process_update(payload: Mapping[str, Any]) -> bool:
    update_id_raw = payload.get("update_id")
    try:
        update_id = int(update_id_raw)
    except (TypeError, ValueError):
        return True
    message = payload.get("message")
    if not isinstance(message, Mapping):
        message = payload.get("edited_message")
    if not isinstance(message, Mapping):
        return True
    chat = message.get("chat")
    if not isinstance(chat, Mapping):
        return True
    chat_id = str(chat.get("id") or "").strip()
    allowed_chat_id = _secret("TELEGRAM_ALLOWED_CHAT_ID") or _secret("TELEGRAM_CHAT_ID")
    if not chat_id or not allowed_chat_id or not hmac.compare_digest(chat_id, allowed_chat_id):
        LOGGER.warning("Ignored Telegram update from an unauthorized chat")
        return True
    claim_result = _claim_update(update_id)
    if claim_result < 0:
        return False
    if claim_result == 0:
        return True
    attempt = int(claim_result)

    message_id_raw = message.get("message_id")
    message_id = int(message_id_raw) if isinstance(message_id_raw, (int, float)) else None
    text = str(message.get("text") or "").strip()
    try:
        if text.lower().split("@", 1)[0] in {"/start", "/help"}:
            _send_text(chat_id, HELP_TEXT, message_id)
            _finish_update(update_id, "sent")
            return True
        if not text:
            _send_text(chat_id, "目前只接受股票代號或名稱文字。\n\n" + HELP_TEXT, message_id)
            _finish_update(update_id, "sent")
            return True
        query = extract_stock_query(text)
        if not query or not is_valid_stock_query(query):
            _send_text(chat_id, "請輸入有效的股票名稱或 4～6 位數代號。\n\n" + HELP_TEXT, message_id)
            _finish_update(update_id, "rejected")
            return True
        _send_analysis_link(chat_id, query, message_id)
        _finish_update(update_id, "sent", query)
        return True
    except ValueError as error:
        try:
            _send_text(chat_id, str(error), message_id)
            _finish_update(update_id, "rejected")
            return True
        except Exception as send_error:  # noqa: BLE001 - transport/provider failure is retryable
            return _handle_transient_failure(update_id, chat_id, message_id, attempt, send_error)
    except Exception as error:  # noqa: BLE001 - normalize all transient transport/provider failures
        return _handle_transient_failure(update_id, chat_id, message_id, attempt, error)


def _readiness_status() -> tuple[bool, dict[str, Any]]:
    missing = []
    if not _secret("TELEGRAM_BOT_TOKEN"):
        missing.append("TELEGRAM_BOT_TOKEN")
    if not (_secret("TELEGRAM_ALLOWED_CHAT_ID") or _secret("TELEGRAM_CHAT_ID")):
        missing.append("TELEGRAM_ALLOWED_CHAT_ID")
    if not _secret("TELEGRAM_WEBHOOK_SECRET"):
        missing.append("TELEGRAM_WEBHOOK_SECRET")
    firestore_ready = scanner.db is not None
    return not missing and firestore_ready, {
        "ok": not missing and firestore_ready,
        "firestore": firestore_ready,
        "missing": missing,
    }


async def healthz(_: Request) -> JSONResponse:
    ready, details = _readiness_status()
    return JSONResponse(details, status_code=200 if ready else 503)


async def readyz(_: Request) -> JSONResponse:
    ready, details = _readiness_status()
    return JSONResponse(details, status_code=200 if ready else 503)


async def telegram_webhook(request: Request) -> JSONResponse:
    expected = _secret("TELEGRAM_WEBHOOK_SECRET")
    if not expected:
        return JSONResponse({"ok": False}, status_code=503)
    provided = request.headers.get("x-telegram-bot-api-secret-token", "")
    if not hmac.compare_digest(provided, expected):
        return JSONResponse({"ok": False}, status_code=403)
    ready, _ = _readiness_status()
    if not ready:
        return JSONResponse({"ok": False}, status_code=503)
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 - malformed request bodies must remain a 400 response
        return JSONResponse({"ok": False}, status_code=400)
    if not isinstance(payload, Mapping):
        return JSONResponse({"ok": False}, status_code=400)
    try:
        handled = await run_in_threadpool(_process_update, payload)
    except Exception as error:
        LOGGER.exception("Telegram webhook processing failed: %s", type(error).__name__)
        return JSONResponse({"ok": False}, status_code=503)
    return JSONResponse({"ok": bool(handled)}, status_code=200 if handled else 503)


app = Starlette(routes=[
    Route("/healthz", healthz, methods=["GET"]),
    Route("/readyz", readyz, methods=["GET"]),
    Route("/telegram/webhook", telegram_webhook, methods=["POST"]),
])
