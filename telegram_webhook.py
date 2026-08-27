"""Cloud Run Telegram webhook for on-demand Taiwan stock analysis images."""

from __future__ import annotations

import hmac
import logging
import os
from collections.abc import Mapping
from typing import Any

import requests
from google.api_core.exceptions import AlreadyExists
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

import scanner
from telegram_stock_analysis import (
    HELP_TEXT,
    StockQueryError,
    get_stock_analysis,
    render_stock_analysis_image,
)


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
LOGGER = logging.getLogger(__name__)


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


def _send_typing(chat_id: str) -> None:
    try:
        _telegram_api("sendChatAction", data={"chat_id": chat_id, "action": "upload_photo"})
    except Exception:
        LOGGER.info("Telegram chat action failed; continuing with analysis")


def _send_analysis_photo(chat_id: str, record: Mapping[str, Any], reply_to: int | None = None) -> None:
    ticker = str(record.get("代號") or "").strip()
    name = str(record.get("名稱") or ticker).strip()
    data_date = str(record.get("Data_Date") or "").strip()
    status = str(record.get("Entry_Status") or "條件不足").strip()
    png = render_stock_analysis_image(record)
    data: dict[str, Any] = {
        "chat_id": chat_id,
        "caption": f"{ticker} {name}｜資料日 {data_date}\n進場狀態：{status}；缺失資料一律顯示 --。",
    }
    if reply_to is not None:
        data["reply_parameters"] = f'{{"message_id":{reply_to}}}'
    _telegram_api(
        "sendPhoto",
        data=data,
        files={"photo": (f"stock-analysis-{ticker}.png", png, "image/png")},
    )


def _claim_update(update_id: int) -> bool:
    if scanner.db is None:
        return True
    reference = scanner.db.collection("telegram_bot_updates").document(str(update_id))
    try:
        reference.create({"status": "processing", "update_id": update_id, "created_at": scanner.firestore.SERVER_TIMESTAMP})
        return True
    except AlreadyExists:
        return False


def _finish_update(update_id: int, status: str, ticker: str = "") -> None:
    if scanner.db is None:
        return
    scanner.db.collection("telegram_bot_updates").document(str(update_id)).set({
        "status": status,
        "ticker": ticker,
        "finished_at": scanner.firestore.SERVER_TIMESTAMP,
    }, merge=True)


def _process_update(payload: Mapping[str, Any]) -> None:
    update_id_raw = payload.get("update_id")
    try:
        update_id = int(update_id_raw)
    except (TypeError, ValueError):
        return
    message = payload.get("message")
    if not isinstance(message, Mapping):
        message = payload.get("edited_message")
    if not isinstance(message, Mapping):
        return
    chat = message.get("chat")
    if not isinstance(chat, Mapping):
        return
    chat_id = str(chat.get("id") or "").strip()
    allowed_chat_id = _secret("TELEGRAM_ALLOWED_CHAT_ID") or _secret("TELEGRAM_CHAT_ID")
    if not chat_id or not allowed_chat_id or not hmac.compare_digest(chat_id, allowed_chat_id):
        LOGGER.warning("Ignored Telegram update from an unauthorized chat")
        return
    if not _claim_update(update_id):
        return

    message_id_raw = message.get("message_id")
    message_id = int(message_id_raw) if isinstance(message_id_raw, (int, float)) else None
    text = str(message.get("text") or "").strip()
    try:
        if text.lower().split("@", 1)[0] in {"/start", "/help"}:
            _send_text(chat_id, HELP_TEXT, message_id)
            _finish_update(update_id, "sent")
            return
        if not text:
            _send_text(chat_id, "目前只接受股票代號或名稱文字。\n\n" + HELP_TEXT, message_id)
            _finish_update(update_id, "sent")
            return
        _send_typing(chat_id)
        record = get_stock_analysis(text)
        _send_analysis_photo(chat_id, record, message_id)
        _finish_update(update_id, "sent", str(record.get("代號") or ""))
    except StockQueryError as error:
        _send_text(chat_id, str(error), message_id)
        _finish_update(update_id, "rejected")
    except Exception as error:
        LOGGER.exception("Telegram stock analysis failed: %s", type(error).__name__)
        try:
            _send_text(chat_id, "目前資料來源暫時無法完成分析，請稍後再試。", message_id)
        finally:
            _finish_update(update_id, "failed")


async def healthz(_: Request) -> JSONResponse:
    return JSONResponse({"ok": True, "firestore": scanner.db is not None})


async def telegram_webhook(request: Request) -> JSONResponse:
    expected = _secret("TELEGRAM_WEBHOOK_SECRET")
    provided = request.headers.get("x-telegram-bot-api-secret-token", "")
    if not expected or not hmac.compare_digest(provided, expected):
        return JSONResponse({"ok": False}, status_code=403)
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"ok": False}, status_code=400)
    if not isinstance(payload, Mapping):
        return JSONResponse({"ok": False}, status_code=400)
    await run_in_threadpool(_process_update, payload)
    return JSONResponse({"ok": True})


app = Starlette(routes=[
    Route("/healthz", healthz, methods=["GET"]),
    Route("/telegram/webhook", telegram_webhook, methods=["POST"]),
])

