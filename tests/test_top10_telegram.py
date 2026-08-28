import io
import unittest

from PIL import Image

from top10_telegram import (
    build_executable_display_rows,
    build_tracking_performance_report,
    build_top10_display_rows,
    prediction_title,
    render_executable_image,
    render_tracking_performance_image,
    render_tracking_performance_images,
    render_top10_image,
    send_executable_photo,
    send_tracking_performance_photo,
    send_top10_photo,
)


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

    def test_prediction_title_uses_the_next_weekday(self):
        self.assertEqual(prediction_title("2026-08-26"), "8/27股票預測")
        self.assertEqual(prediction_title("2026-08-28"), "8/31股票預測")
        self.assertEqual(prediction_title("invalid"), "下一交易日股票預測")

    def test_empty_executable_top10_has_an_honest_empty_image(self):
        png = render_top10_image([], "2026-08-27")
        image = Image.open(io.BytesIO(png))
        self.assertEqual(image.size, (1080, 1400))

    def test_executable_list_filters_exact_status_and_keeps_original_rank(self):
        waiting = dict(self.rows[0], Rank=2, 代號="2317", Entry_Status="等待拉回")
        ready = dict(
            self.rows[0], Rank=26, Entry_Status="現在可執行",
            Entry_Low=1200, Entry_High=1235, Entry_Stop=1170, Entry_Target=1330, Entry_RRR=1.5,
        )
        rows = build_executable_display_rows([waiting, ready])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["entry_zone_text"], "1200–1235")
        self.assertNotIn("source_rank_text", rows[0])
        self.assertNotIn("rrr_text", rows[0])
        self.assertEqual(rows[0]["suggested_shares"], 78)
        self.assertLessEqual(rows[0]["estimated_loss"], 5000)

    def test_odd_lot_plan_caps_each_trade_loss_independently(self):
        ready = [
            dict(
                self.rows[0], 代號="1111", 收盤價=100, Entry_Status="現在可執行",
                Entry_Low=95, Entry_High=100, Entry_Stop=90, Entry_Target=115,
            ),
            dict(
                self.rows[0], 代號="2222", 收盤價=200, Entry_Status="現在可執行",
                Entry_Low=190, Entry_High=200, Entry_Stop=180, Entry_Target=230,
            ),
        ]
        rows = build_executable_display_rows(ready)
        self.assertTrue(all(row["suggested_shares"] > 0 for row in rows))
        self.assertEqual([row["suggested_shares"] for row in rows], [500, 250])
        self.assertTrue(all(row["estimated_loss"] <= 5000 for row in rows))
        self.assertGreater(sum(row["estimated_loss"] for row in rows), 9000)

    def test_position_size_is_unavailable_when_stop_is_not_below_current_price(self):
        ready = dict(
            self.rows[0], Entry_Status="現在可執行", Entry_Stop=1234, Entry_Target=1300,
        )
        row = build_executable_display_rows([ready])[0]
        self.assertEqual(row["suggested_shares"], 0)
        self.assertEqual(row["suggested_shares_text"], "無法計算")
        self.assertEqual(row["estimated_loss_text"], "--")

    def test_empty_executable_list_still_returns_an_honest_png(self):
        png = render_executable_image([dict(self.rows[0], Entry_Status="等待拉回")], "2026-08-27")
        image = Image.open(io.BytesIO(png))
        self.assertEqual(image.size, (1080, 1400))

    def test_tracking_report_uses_previous_analysis_and_excludes_rows_without_pnl(self):
        records = []
        positions = []
        for index in range(12):
            pnl = float(11 - index)
            records.append({
                "ticker": f"{1000 + index}", "name": f"股票{index}",
                "entry_date": "2026-08-27", "entry_price": 100, "mark_price": 100 + pnl,
                "daily_return_pct": pnl / 10, "pnl_pct": pnl, "data_status": "ok",
                "action": "HOLD", "entry_win_rate": 55, "entry_backtest_samples": 40,
            })
            positions.append({
                "ticker": f"{1000 + index}", "entry_date": "2026-08-27",
                "status": "OPEN", "pnl_pct": pnl,
            })
        records[-1]["data_status"] = "missing"
        records[-1]["daily_return_pct"] = 999
        records.append({
            "ticker": "TODAY", "name": "當日新榜", "entry_date": "2026-08-28",
            "daily_return_pct": None, "pnl_pct": 0, "data_status": "ok", "action": "ENTRY",
        })
        positions.append({
            "ticker": "TODAY", "entry_date": "2026-08-28", "status": "OPEN", "pnl_pct": 0,
        })
        records.append({
            "ticker": "OLD", "name": "舊基準", "entry_date": "2026-08-26",
            "daily_return_pct": 999, "pnl_pct": 999, "data_status": "ok", "action": "HOLD",
        })
        positions.append({
            "ticker": "OLD", "entry_date": "2026-08-26", "status": "OPEN", "pnl_pct": 999,
        })
        report = build_tracking_performance_report(records, positions, "2026-08-28")
        self.assertEqual(report["valid_count"], 11)
        self.assertEqual(report["missing_count"], 0)
        self.assertEqual(report["excluded_count"], 2)
        self.assertEqual(len(report["rows"]), 11)
        self.assertEqual(report["page_count"], 2)
        self.assertEqual(report["display_mode"], "已有當日損益的全部標的")
        self.assertNotIn("1011", [row["ticker"] for row in report["rows"]])
        self.assertNotIn("TODAY", [row["ticker"] for row in report["rows"]])
        self.assertNotIn("OLD", [row["ticker"] for row in report["rows"]])
        self.assertNotEqual(report["daily_average"], 999)
        first = next(row for row in report["rows"] if row["ticker"] == "1000")
        self.assertEqual(first["daily_price_change"], 11)
        self.assertEqual(first["holding_price_change"], 11)
        self.assertTrue(first["daily_price_change_text"].startswith("+"))
        self.assertEqual(first["holding_price_change_text"], "+11")

        pages = render_tracking_performance_images(records, positions, "2026-08-28")
        self.assertEqual(len(pages), 2)
        self.assertTrue(all(page.startswith(b"\x89PNG") for page in pages))

    def test_tracking_renderer_returns_a_valid_mobile_png(self):
        records = [{
            "ticker": "2330", "name": "台積電", "entry_date": "2026-08-27",
            "entry_price": 1230, "mark_price": 1234, "daily_return_pct": 1.2,
            "pnl_pct": 0.3, "data_status": "ok", "action": "ENTRY",
            "entry_win_rate": 56.7, "entry_backtest_samples": 42,
        }]
        positions = [{
            "ticker": "2330", "entry_date": "2026-08-27", "status": "OPEN", "pnl_pct": 0.3,
        }]
        png = render_tracking_performance_image(records, positions, "2026-08-28")
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

    def test_executable_sender_uses_a_separate_filename_and_caption(self):
        session = _Session()
        ready = dict(self.rows[0], Entry_Status="現在可執行")
        message_id = send_executable_photo([ready], "2026-08-27", "token", "chat", session=session)
        self.assertEqual(message_id, 321)
        _, kwargs = session.calls[0]
        self.assertEqual(kwargs["files"]["photo"][0], "executable-2026-08-27.png")
        self.assertIn("8/28股票預測", kwargs["data"]["caption"])

    def test_tracking_sender_uses_separate_filename_and_truthful_caption(self):
        session = _Session()
        records = [{
            "ticker": "2330", "name": "台積電", "entry_date": "2026-08-27",
            "entry_price": 100, "mark_price": 101, "daily_return_pct": 1,
            "pnl_pct": 1, "data_status": "ok", "action": "ENTRY",
        }]
        message_id = send_tracking_performance_photo(
            records,
            [{"ticker": "2330", "entry_date": "2026-08-27", "status": "OPEN", "pnl_pct": 1}],
            "2026-08-28",
            "token",
            "chat",
            session=session,
        )
        self.assertEqual(message_id, 321)
        _, kwargs = session.calls[0]
        self.assertEqual(kwargs["files"]["photo"][0], "tracking-performance-2026-08-28.png")
        self.assertIn("只採真實盤後行情", kwargs["data"]["caption"])

    def test_tracking_sender_sends_every_page(self):
        session = _Session()
        records = []
        positions = []
        for index in range(11):
            ticker = str(2000 + index)
            records.append({
                "ticker": ticker, "name": f"股票{index}", "entry_date": "2026-08-27",
                "entry_price": 100, "mark_price": 100, "daily_return_pct": 0,
                "pnl_pct": 0, "data_status": "ok", "action": "ENTRY",
            })
            positions.append({
                "ticker": ticker, "entry_date": "2026-08-27", "status": "OPEN", "pnl_pct": 0,
            })
        message_id = send_tracking_performance_photo(
            records, positions, "2026-08-28", "token", "chat", session=session,
        )
        self.assertEqual(message_id, 321)
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(
            session.calls[0][1]["files"]["photo"][0],
            "tracking-performance-2026-08-28-p1-of-2.png",
        )
        self.assertEqual(
            session.calls[1][1]["files"]["photo"][0],
            "tracking-performance-2026-08-28-p2-of-2.png",
        )


if __name__ == "__main__":
    unittest.main()
