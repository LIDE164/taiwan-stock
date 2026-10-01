"""Independent, resumable delivery of a frozen daily research text report.

Never retry an ambiguous Telegram POST automatically: a timeout can mean that
Telegram received it. Only notifications state is written; scan/tracking records
and trading decisions are not changed here.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from uuid import uuid4

import requests

FORMAT_VERSION = "daily_research_three_facets_v3"


class DeliveryUncertain(RuntimeError):
    """Manual confirmation is required before repeating a possibly delivered part."""


class DeliveryRejected(RuntimeError):
    """Telegram explicitly rejected the request; retrying later is safe."""


def report_fingerprint(records, trading_date):
    payload = {"format": FORMAT_VERSION, "date": str(trading_date), "records": records}
    return hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True, default=str,
    ).encode("utf-8")).hexdigest()


def telegram_send_text(token, chat_id, message):
    """One POST, no logging of token, request URL, response body or credentials."""
    if not token or not chat_id:
        raise DeliveryRejected("Telegram 設定未完成")
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "disable_web_page_preview": True},
            timeout=(10, 35),
        )
        payload = response.json()
    except Exception:
        raise DeliveryUncertain("Telegram 未回傳可確認結果；請先確認是否已收件") from None
    if isinstance(payload, dict) and payload.get("ok") is False:
        # Do not expose a provider description that might contain supplied text.
        raise DeliveryRejected("Telegram 明確拒絕本次訊息")
    result = payload.get("result") if isinstance(payload, dict) else None
    message_id = result.get("message_id") if isinstance(result, dict) else None
    if (not response.ok or not isinstance(payload, dict) or payload.get("ok") is not True
            or not isinstance(message_id, int)
            or isinstance(message_id, bool) or message_id <= 0):
        raise DeliveryUncertain("Telegram 回應缺少有效收件編號；請先確認是否已收件")
    return message_id


class FirestoreReportStore:
    """Transactional per-date ownership prevents overlapping jobs from sending."""

    def __init__(self, db, trading_date):
        from firebase_admin import firestore

        self.db = db
        self.firestore = firestore
        self.ref = db.collection("notifications").document(f"daily_research_{trading_date}")

    def acquire(self, fingerprint, owner, *, resend=False):
        now = datetime.now(timezone.utc)

        @self.firestore.transactional
        def update(transaction):
            snap = self.ref.get(transaction=transaction)
            previous = snap.to_dict() or {} if snap.exists else {}
            expiry = previous.get("lease_until")
            if isinstance(expiry, datetime):
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=timezone.utc)
                if previous.get("owner") and expiry > now:
                    return None
            same = previous.get("fingerprint") == fingerprint
            if not resend and (previous.get("status") == "uncertain" or previous.get("in_flight")):
                raise DeliveryUncertain("上一則研究訊息收件狀態不明，暫停自動重送")
            if not resend and same and previous.get("status") == "sent":
                return None
            state = previous if same and not resend else {"sent_parts": {}, "parts": []}
            state.update({
                "fingerprint": fingerprint, "format": FORMAT_VERSION,
                "owner": owner, "lease_until": now + timedelta(minutes=30),
                "status": "preparing", "in_flight": "", "last_error": "",
                "attempted_at": now,
                "attempt_count": int(previous.get("attempt_count") or 0) + 1,
            })
            transaction.set(self.ref, state)
            return state

        return update(self.db.transaction())

    def save(self, owner, values, *, release=False):
        @self.firestore.transactional
        def update(transaction):
            snap = self.ref.get(transaction=transaction)
            previous = snap.to_dict() or {} if snap.exists else {}
            if previous.get("owner") != owner:
                raise RuntimeError("研究通知的寄送鎖已變更，停止寄送")
            payload = dict(values)
            payload["lease_until"] = datetime.now(timezone.utc) + timedelta(minutes=30)
            if release:
                payload.update(owner="", lease_until=None)
            transaction.set(self.ref, payload, merge=True)

        update(self.db.transaction())


def deliver_report(store, records, trading_date, *, build_messages, send_text, resend=False):
    """Freeze text before first send, resume explicit failures, deduplicate success.

    build_messages is called only for a new payload and returns (parts, metadata).
    Dependency injection keeps tests fully offline and unable to send real texts.
    """
    owner = uuid4().hex
    state = store.acquire(report_fingerprint(records, trading_date), owner, resend=resend)
    if state is None:
        return False
    in_flight = ""
    try:
        parts = state.get("parts") or []
        if not parts:
            parts, metadata = build_messages()
            if (not isinstance(parts, list) or not parts or len(parts) > 25
                    or any(not isinstance(part, str) or not part.strip()
                           or len(part.encode("utf-16-le")) // 2 > 3500 for part in parts)):
                raise ValueError("研究訊息內容或長度不合法")
            # Bound total state well below Firestore's 1 MiB document limit.
            if len(json.dumps(parts, ensure_ascii=False).encode("utf-8")) > 700_000:
                raise ValueError("研究訊息超過儲存上限")
            store.save(owner, {"parts": parts, "metadata": metadata,
                               "date": str(trading_date), "status": "ready"})
        sent = dict(state.get("sent_parts") or {})
        for index, part in enumerate(parts, 1):
            key = str(index)
            if sent.get(key):
                continue
            # Persist intent first: a crash after POST must not silently repeat it.
            store.save(owner, {"status": "sending", "in_flight": key})
            in_flight = key
            try:
                message_id = send_text(part)
            except DeliveryRejected:
                in_flight = ""
                raise
            if not isinstance(message_id, int) or isinstance(message_id, bool) or message_id <= 0:
                raise DeliveryUncertain("研究訊息缺少有效收件編號")
            sent[key] = message_id
            store.save(owner, {"sent_parts": sent, "in_flight": ""})
            in_flight = ""
        store.save(owner, {"status": "sent", "sent_parts": sent,
                           "part_count": len(parts), "in_flight": "", "last_error": "",
                           "sent_at": datetime.now(timezone.utc)}, release=True)
        return True
    except Exception as exc:
        store.save(owner, {"status": "uncertain" if in_flight else "failed",
                           "in_flight": in_flight, "last_error": type(exc).__name__}, release=True)
        raise
