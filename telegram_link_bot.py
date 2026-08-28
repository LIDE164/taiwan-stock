"""Poll Telegram once and reply to stock queries with Streamlit analysis links."""

from __future__ import annotations

import hmac
import json
import os
import sys
from collections.abc import Mapping
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from telegram_links import HELP_TEXT, build_analysis_url, extract_stock_query, is_valid_stock_query


def _telegram_api(token: str, method: str, data: Mapping[str, Any]) -> dict[str, Any]:
    encoded = urlencode({key: str(value) for key, value in data.items()}).encode("utf-8")
    request = Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=encoded,
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Telegram API {method} 連線失敗（{type(error).__name__}）") from None
    if not isinstance(payload, Mapping) or not payload.get("ok"):
        raise RuntimeError(f"Telegram API {method} 未確認成功")
    return dict(payload)


def _send_text(
    token: str,
    chat_id: str,
    text: str,
    *,
    reply_to: int | None = None,
    analysis_url: str = "",
) -> None:
    data: dict[str, Any] = {"chat_id": chat_id, "text": text}
    if reply_to is not None:
        data["reply_parameters"] = json.dumps({"message_id": reply_to})
    if analysis_url:
        data["reply_markup"] = json.dumps({
            "inline_keyboard": [[{"text": "開啟股票解析", "url": analysis_url}]],
        }, ensure_ascii=False)
    _telegram_api(token, "sendMessage", data)


def _message_from_update(update: Mapping[str, Any]) -> Mapping[str, Any] | None:
    message = update.get("message")
    if not isinstance(message, Mapping):
        message = update.get("edited_message")
    return message if isinstance(message, Mapping) else None


def poll_once(token: str, allowed_chat_id: str, base_url: str = "") -> int:
    """Process currently pending messages and confirm only successfully handled updates."""
    webhook_info = _telegram_api(token, "getWebhookInfo", {})
    webhook_result = webhook_info.get("result")
    if isinstance(webhook_result, Mapping) and str(webhook_result.get("url") or "").strip():
        _telegram_api(token, "deleteWebhook", {"drop_pending_updates": "false"})

    response = _telegram_api(token, "getUpdates", {
        "timeout": 0,
        "limit": 100,
        "allowed_updates": json.dumps(["message", "edited_message"]),
    })
    raw_updates = response.get("result")
    updates = [row for row in raw_updates if isinstance(row, Mapping)] if isinstance(raw_updates, list) else []
    updates.sort(key=lambda row: int(row.get("update_id") or -1))
    confirmed_update_id: int | None = None
    replies = 0

    for update in updates:
        try:
            update_id = int(update.get("update_id"))
        except (TypeError, ValueError):
            continue
        message = _message_from_update(update)
        if message is None:
            confirmed_update_id = update_id
            continue
        chat = message.get("chat")
        chat_id = str(chat.get("id") or "").strip() if isinstance(chat, Mapping) else ""
        if not chat_id or not hmac.compare_digest(chat_id, allowed_chat_id):
            confirmed_update_id = update_id
            continue
        message_id_raw = message.get("message_id")
        message_id = int(message_id_raw) if isinstance(message_id_raw, (int, float)) else None
        text = str(message.get("text") or "").strip()
        command = text.lower().split("@", 1)[0]
        if command in {"/start", "/help"}:
            _send_text(token, chat_id, HELP_TEXT, reply_to=message_id)
        else:
            query = extract_stock_query(text)
            if not query or not is_valid_stock_query(query):
                _send_text(token, chat_id, "請輸入有效的股票名稱或 4～6 位數代號。\n\n" + HELP_TEXT, reply_to=message_id)
            else:
                analysis_url = build_analysis_url(query, base_url)
                _send_text(
                    token,
                    chat_id,
                    f"{query} 股票解析連結：\n{analysis_url}",
                    reply_to=message_id,
                    analysis_url=analysis_url,
                )
            replies += 1
        confirmed_update_id = update_id

    if confirmed_update_id is not None:
        _telegram_api(token, "getUpdates", {
            "offset": confirmed_update_id + 1,
            "timeout": 0,
            "limit": 1,
        })
    return replies


def main() -> int:
    token = str(os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = str(os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    base_url = str(os.getenv("ANALYSIS_BASE_URL") or "").strip()
    if not token or not chat_id:
        print("TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHAT_ID 未設定", file=sys.stderr)
        return 1
    count = poll_once(token, chat_id, base_url)
    print(f"processed_replies={count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
