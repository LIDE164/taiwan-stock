"""Transactional, resumable Telegram image delivery across independent runners.

Keep the historical daily_* document keys and fingerprints. A durable page
intent precedes every POST; ambiguity requires explicit human-authorized resend.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from research_delivery import DeliveryRejected, DeliveryUncertain

SCHEMA = "transactional_images_v1"


def _positive_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _pages(value, page_count):
    if not isinstance(value, Mapping):
        raise DeliveryUncertain("已保存的圖片收件紀錄格式不明，停止自動重送")
    pages = dict(value)
    valid_keys = {str(index) for index in range(1, page_count + 1)}
    if (not set(pages).issubset(valid_keys) or not all(_positive_int(item) for item in pages.values())
            or len(set(pages.values())) != len(pages)):
        raise DeliveryUncertain("已保存的圖片頁碼或收件編號不明，停止自動重送")
    return pages


class FirestoreImageStore:
    """One transactional owner per existing notification document, across PCs/CI."""

    def __init__(self, db, namespace, trading_date, *, clock=None):
        from firebase_admin import firestore

        if db is None:
            raise RuntimeError("Firestore 未初始化，禁止繞過圖片防重複鎖")
        if namespace not in {"daily_top10", "daily_executable", "daily_tracking_performance"}:
            raise ValueError("不支援的每日圖片通知類別")
        self.db = db
        self.firestore = firestore
        self.namespace = namespace
        self.date = str(trading_date)
        self.ref = db.collection("notifications").document(f"{namespace}_{self.date}")
        self.clock = clock or (lambda: datetime.now(UTC))

    def acquire(self, fingerprint, owner, *, page_count, resend=False):
        if not _positive_int(page_count) or page_count > 1000:
            raise ValueError("圖片頁數無效")
        now = self.clock()

        @self.firestore.transactional
        def update(transaction):
            snapshot = self.ref.get(transaction=transaction)
            previous = snapshot.to_dict() or {} if snapshot.exists else {}
            if not isinstance(previous, Mapping):
                raise DeliveryUncertain("圖片寄送狀態格式不明，停止自動重送")
            expiry = previous.get("lease_until")
            if previous.get("owner"):
                if not isinstance(expiry, datetime):
                    raise DeliveryUncertain("圖片寄送鎖的期限無法確認")
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=UTC)
                if expiry > now:
                    return None
            same = previous.get("fingerprint") == fingerprint
            if not resend:
                if previous.get("status") == "uncertain" or previous.get("in_flight"):
                    raise DeliveryUncertain("上一張圖片收件狀態不明，請先確認收件再決定重送")
                # Historical pending/failed records did not persist POST intent.
                # Their exception could have occurred after Telegram accepted it.
                if (previous.get("status") in {"pending", "failed", "sending"}
                        and previous.get("image_delivery_schema") != SCHEMA):
                    raise DeliveryUncertain("舊版圖片寄送未留下可確認的傳送狀態，停止自動重送")
            pages = {}
            if same and not resend:
                if previous.get("status") == "sent":
                    if previous.get("date") != self.date:
                        raise DeliveryUncertain("圖片標示已送出但收件日期不一致，禁止盲目重送")
                    if self.namespace != "daily_tracking_performance":
                        if _positive_int(previous.get("message_id")):
                            return None
                    else:
                        confirmed = _pages(previous.get("sent_pages", {}), page_count)
                        if (_positive_int(previous.get("page_count"))
                                and previous.get("page_count") == page_count and len(confirmed) == page_count):
                            return None
                    raise DeliveryUncertain("圖片標示已送出但完整收件編號缺漏，禁止盲目重送")
                if (not _positive_int(previous.get("page_count", page_count))
                        or previous.get("page_count", page_count) != page_count):
                    raise DeliveryUncertain("相同圖片指紋的頁數不一致")
                pages = _pages(previous.get("sent_pages", {}), page_count)
            state = {
                "date": self.date, "fingerprint": fingerprint, "image_delivery_schema": SCHEMA,
                "owner": owner, "lease_until": now + timedelta(minutes=30),
                "status": "preparing", "in_flight": "", "last_error": "",
                "attempt_count": int(previous.get("attempt_count") or 0) + 1,
                "attempted_at": now, "page_count": page_count, "sent_pages": pages,
            }
            transaction.set(self.ref, state)
            return state

        return update(self.db.transaction())

    def save(self, owner, values, *, release=False):
        @self.firestore.transactional
        def update(transaction):
            snapshot = self.ref.get(transaction=transaction)
            previous = snapshot.to_dict() or {} if snapshot.exists else {}
            if previous.get("owner") != owner:
                raise RuntimeError("圖片寄送鎖已由其他工作取得，停止傳送")
            payload = dict(values)
            payload["lease_until"] = self.clock() + timedelta(minutes=30)
            if release:
                payload.update(owner="", lease_until=None)
            transaction.set(self.ref, payload, merge=True)

        update(self.db.transaction())


def deliver_images(store, fingerprint, *, page_count, send_pages, metadata=None, resend=False):
    """Call a renderer/transport with skip, before-POST and receipt callbacks.

    Rendering failures before the before-POST callback are safe to retry.
    Callback receipts are mandatory; an opaque sender return is not sufficient.
    """
    owner = uuid4().hex
    state = store.acquire(fingerprint, owner, page_count=page_count, resend=resend)
    if state is None:
        return False
    sent = _pages(state.get("sent_pages", {}), page_count)
    in_flight = ""

    def before_page(page_number, total):
        nonlocal in_flight
        if (not _positive_int(page_number) or page_number > page_count
                or not _positive_int(total) or total != page_count
                or str(page_number) in sent or in_flight):
            raise ValueError("圖片傳送頁碼、順序或總頁數不一致")
        key = str(page_number)
        # If this write is ambiguous, no POST happens; persistent intent remains
        # conservatively blocked until a human confirms the delivery state.
        in_flight = key
        store.save(owner, {"status": "sending", "in_flight": key})

    def after_page(page_number, message_id, total):
        nonlocal in_flight
        key = str(page_number)
        if (key != in_flight or not _positive_int(total) or total != page_count or not _positive_int(message_id)
                or message_id in sent.values()):
            raise DeliveryUncertain("圖片缺少有效且對應傳送意圖的收件編號")
        updated = {**sent, key: message_id}
        store.save(owner, {"sent_pages": updated, "in_flight": "", "last_sent_page": page_number,
                           "last_page_sent_at": store.clock()})
        sent.update(updated)
        in_flight = ""

    try:
        if len(sent) < page_count:
            send_pages({int(page) for page in sent}, before_page, after_page)
        if len(sent) != page_count or in_flight:
            raise DeliveryUncertain("圖片尚未取得所有頁面的明確收件紀錄")
        store.save(owner, {
            **(metadata or {}), "date": store.date, "status": "sent",
            "message_id": sent["1"], "sent_pages": sent, "page_count": page_count,
            "in_flight": "", "last_error": "", "sent_at": store.clock(),
        }, release=True)
        return True
    except Exception as exc:
        if isinstance(exc, DeliveryRejected):
            in_flight = ""
        # Never copy exception text, tokens, URLs or provider payloads to state.
        store.save(owner, {"status": "uncertain" if in_flight or isinstance(exc, DeliveryUncertain) else "failed",
                           "in_flight": in_flight, "last_error": type(exc).__name__}, release=True)
        raise
