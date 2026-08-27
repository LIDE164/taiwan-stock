import io
import unittest

from PIL import Image

from top10_telegram import build_top10_display_rows, render_top10_image, send_top10_photo


class _Response:
    status_code = 200

    def json(self):
        return {"ok": True, "result": {"message_id": 321}}


class _Session:
    def __init__(self):
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _Response()


class Top10TelegramTests(unittest.TestCase):
    def setUp(self):
        self.rows = [{
            "Rank": 1,
            "代號": "2330",
            "名稱": "台積電",
            "產業": "半導體",
            "Score": 78,
            "評級": "強勢買進",
            "收盤價": 1234,
            "漲跌幅": 1.25,
            "WinRate": 56.78,
            "Backtest_Samples": 42,
            "Entry_Status": "等待拉回",
        }]

    def test_missing_samples_do_not_become_zero_percent_win_rate(self):
        rows = build_top10_display_rows([{
            "代號": "2317", "名稱": "鴻海", "WinRate": 0, "Backtest_Samples": 0,
        }])
        self.assertEqual(rows[0]["win_rate_text"], "--")
        self.assertEqual(rows[0]["sample_text"], "0")
        self.assertEqual(rows[0]["credibility"], "樣本嚴重不足")

        missing = build_top10_display_rows([{"代號": "2454", "名稱": "聯發科"}])
        self.assertEqual(missing[0]["win_rate_text"], "--")
        self.assertEqual(missing[0]["sample_text"], "--")
        self.assertEqual(missing[0]["credibility"], "資料缺失")

    def test_renderer_returns_a_valid_mobile_png(self):
        png = render_top10_image(self.rows * 10, "2026-08-27")
        image = Image.open(io.BytesIO(png))
        self.assertEqual(image.format, "PNG")
        self.assertEqual(image.size, (1080, 1400))

    def test_sender_posts_png_and_returns_message_id(self):
        session = _Session()
        message_id = send_top10_photo(
            self.rows,
            "2026-08-27",
            "secret-token",
            "12345",
            session=session,
        )
        self.assertEqual(message_id, 321)
        self.assertEqual(len(session.calls), 1)
        url, kwargs = session.calls[0]
        self.assertEqual(url, "https://api.telegram.org/botsecret-token/sendPhoto")
        self.assertEqual(kwargs["data"]["chat_id"], "12345")
        self.assertEqual(kwargs["files"]["photo"][2], "image/png")
        self.assertTrue(kwargs["files"]["photo"][1].startswith(b"\x89PNG"))

    def test_sender_requires_both_credentials(self):
        with self.assertRaisesRegex(RuntimeError, "TELEGRAM_BOT_TOKEN"):
            send_top10_photo(self.rows, "2026-08-27", "", "12345")


if __name__ == "__main__":
    unittest.main()
