import asyncio
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import telegram_webhook


class _Request:
    def __init__(self, payload=None, secret="webhook-secret"):
        self._payload = payload or {}
        self.headers = {"x-telegram-bot-api-secret-token": secret}

    async def json(self):
        return self._payload


class TelegramWebhookReliabilityTests(unittest.TestCase):
    def test_failed_and_stale_processing_updates_have_a_bounded_retry(self):
        now = datetime(2026, 9, 7, tzinfo=UTC)
        self.assertEqual(
            telegram_webhook._claim_decision(
                {"status": "failed", "attempts": 1}, now=now
            ),
            ("claim", 2),
        )
        self.assertEqual(
            telegram_webhook._claim_decision(
                {
                    "status": "processing",
                    "attempts": 1,
                    "lease_expires_at": now - timedelta(seconds=1),
                },
                now=now,
            ),
            ("claim", 2),
        )
        self.assertEqual(
            telegram_webhook._claim_decision(
                {
                    "status": "processing",
                    "attempts": 1,
                    "lease_expires_at": now + timedelta(seconds=1),
                },
                now=now,
            ),
            ("busy", 1),
        )
        self.assertEqual(
            telegram_webhook._claim_decision(
                {"status": "failed", "attempts": telegram_webhook.UPDATE_MAX_ATTEMPTS},
                now=now,
            ),
            ("exhausted", telegram_webhook.UPDATE_MAX_ATTEMPTS),
        )
        self.assertEqual(
            telegram_webhook._claim_decision(
                {"status": "sent", "attempts": 1}, now=now
            ),
            ("done", 1),
        )

    def test_transient_failure_requests_a_retry_before_attempt_limit(self):
        payload = {
            "update_id": 99,
            "message": {"message_id": 7, "chat": {"id": 123}, "text": "2330"},
        }
        with (
            patch.object(
                telegram_webhook,
                "_secret",
                side_effect=lambda name: "123" if "CHAT_ID" in name else "secret",
            ),
            patch.object(telegram_webhook, "_claim_update", return_value=2),
            patch.object(telegram_webhook, "_send_analysis_link", side_effect=RuntimeError("temporary")),
            patch.object(telegram_webhook, "_finish_update") as finish,
            patch.object(telegram_webhook.LOGGER, "exception"),
        ):
            handled = telegram_webhook._process_update(payload)
        self.assertFalse(handled)
        finish.assert_called_once_with(99, "failed", error_kind="RuntimeError")

    def test_final_attempt_is_terminal_and_sends_one_failure_notice(self):
        payload = {
            "update_id": 99,
            "message": {"message_id": 7, "chat": {"id": 123}, "text": "2330"},
        }
        with (
            patch.object(
                telegram_webhook,
                "_secret",
                side_effect=lambda name: "123" if "CHAT_ID" in name else "secret",
            ),
            patch.object(
                telegram_webhook,
                "_claim_update",
                return_value=telegram_webhook.UPDATE_MAX_ATTEMPTS,
            ),
            patch.object(telegram_webhook, "_send_analysis_link", side_effect=RuntimeError("temporary")),
            patch.object(telegram_webhook, "_send_text") as send_text,
            patch.object(telegram_webhook, "_finish_update") as finish,
            patch.object(telegram_webhook.LOGGER, "exception"),
        ):
            handled = telegram_webhook._process_update(payload)
        self.assertTrue(handled)
        send_text.assert_called_once_with("123", "目前暫時無法產生解析連結，請稍後再試。", 7)
        finish.assert_called_once_with(99, "failed_terminal", error_kind="RuntimeError")

    def test_health_is_unready_when_a_required_dependency_is_missing(self):
        values = {
            "TELEGRAM_BOT_TOKEN": "",
            "TELEGRAM_ALLOWED_CHAT_ID": "123",
            "TELEGRAM_WEBHOOK_SECRET": "secret",
        }
        with (
            patch.object(telegram_webhook, "_secret", side_effect=lambda name: values.get(name, "")),
            patch.object(telegram_webhook.scanner, "db", object()),
        ):
            response = asyncio.run(telegram_webhook.healthz(None))
        self.assertEqual(response.status_code, 503)
        self.assertIn(b"TELEGRAM_BOT_TOKEN", response.body)

    def test_health_is_ready_when_required_dependencies_exist(self):
        values = {
            "TELEGRAM_BOT_TOKEN": "token",
            "TELEGRAM_ALLOWED_CHAT_ID": "123",
            "TELEGRAM_WEBHOOK_SECRET": "secret",
        }
        with (
            patch.object(telegram_webhook, "_secret", side_effect=lambda name: values.get(name, "")),
            patch.object(telegram_webhook.scanner, "db", object()),
        ):
            response = asyncio.run(telegram_webhook.readyz(None))
        self.assertEqual(response.status_code, 200)

    def test_health_is_unready_without_firestore(self):
        values = {
            "TELEGRAM_BOT_TOKEN": "token",
            "TELEGRAM_ALLOWED_CHAT_ID": "123",
            "TELEGRAM_WEBHOOK_SECRET": "secret",
        }
        with (
            patch.object(telegram_webhook, "_secret", side_effect=lambda name: values.get(name, "")),
            patch.object(telegram_webhook.scanner, "db", None),
        ):
            response = asyncio.run(telegram_webhook.healthz(None))
        self.assertEqual(response.status_code, 503)
        self.assertIn(b'"firestore":false', response.body)

    def test_webhook_returns_503_so_telegram_retries_transient_work(self):
        payload = {
            "update_id": 99,
            "message": {"message_id": 7, "chat": {"id": 123}, "text": "2330"},
        }
        with (
            patch.object(telegram_webhook, "_secret", return_value="webhook-secret"),
            patch.object(telegram_webhook, "_readiness_status", return_value=(True, {"ok": True})),
            patch.object(telegram_webhook, "_process_update", return_value=False),
        ):
            response = asyncio.run(telegram_webhook.telegram_webhook(_Request(payload)))
        self.assertEqual(response.status_code, 503)


if __name__ == "__main__":
    unittest.main()
