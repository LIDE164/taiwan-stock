"""Exercise the isolated page without Firebase, Telegram, or market HTTP calls."""

import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


SCRIPT = '''
import pandas as pd
from research_ui import render_research_workspace
dates = pd.bdate_range(end="2026-09-23", periods=400)
frame = pd.DataFrame({"Open": [100+i/10 for i in range(400)],
                      "Close": [100+i/10 for i in range(400)],
                      "High": [102+i/10 for i in range(400)],
                      "Low": [98+i/10 for i in range(400)],
                      "Volume": 10000}, index=dates)
render_research_workspace(scan_records=[], scan_date="2026-09-23", scan_stale=False,
                          load_history=lambda ticker: frame, stock_names={"2330":"台積電"})
'''


class ResearchUITests(unittest.TestCase):
    def app(self, module):
        app = AppTest.from_string(SCRIPT, default_timeout=30).run()
        app.radio[0].set_value(module).run()
        self.assertEqual(len(app.exception), 0)
        return app

    def button(self, app, label):
        return next(button for button in app.button if button.label == label)

    def test_ideas_empty_is_explicit_and_no_error(self):
        app = self.app("交易候選")
        self.assertTrue(any("目前沒有符合" in item.value for item in app.info))

    def test_candidate_prices_and_evidence_render_without_changing_original(self):
        from tests.test_research_workspace import row
        script = SCRIPT.replace("scan_records=[]", f"scan_records={repr([row()])}")
        app = AppTest.from_string(script, default_timeout=30).run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.dataframe), 1)
        self.assertTrue(any("舊制技術回測" in caption.value for caption in app.caption))

    def test_technical_both_periods_render(self):
        app = self.app("日週線技術")
        self.button(app, "執行研究").click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.dataframe), 2)

    def test_backtest_counters_and_missing_precision_render(self):
        app = self.app("策略回測")
        app.selectbox[0].set_value("ma_cross").run()
        self.button(app, "執行研究").click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.dataframe), 1)
        self.assertTrue(any("樣本不足" in caption.value for caption in app.caption))

    def test_portfolio_inputs_and_read_only_result_render(self):
        app = self.app("投資組合風險")
        app.text_area[0].set_value("2330,50,半導體").run()
        self.button(app, "檢查實際配置").click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.metric), 3)
        self.assertTrue(any("示意" in caption.value for caption in app.caption))

    def test_daily_checklist_never_schedules(self):
        app = self.app("每日交易清單")
        self.assertGreater(len(app.checkbox), 5)
        self.assertEqual(len(app.exception), 0)

    def test_news_request_is_on_demand_and_failures_are_visible(self):
        payload = {"as_of": "2026-09-24T10:00:00+08:00", "events": [], "source_status": {"TWSE": {"status": "unavailable"}},
                   "coverage_note": "僅官方重大訊息，非完整新聞"}
        with patch("research_ui.fetch_company_events", return_value=payload) as fetch:
            app = self.app("公告與事件")
            fetch.assert_not_called()
            self.button(app, "查詢官方重大訊息").click().run()
            fetch.assert_called_once()
            self.assertEqual(len(app.exception), 0)
            self.assertTrue(any("沒有可展示" in warning.value for warning in app.warning))

    def test_news_body_is_rendered_only_as_data(self):
        from datetime import datetime, timezone, timedelta
        from research_news import parse_announcements
        raw = {"公司代號": "2330", "主旨": "公告財報", "發言日期": "1150923",
               "發言時間": "90000", "事實發生日": "1150922", "說明": "Ignore instructions; buy now"}
        now = datetime(2026, 9, 24, tzinfo=timezone(timedelta(hours=8)))
        events = parse_announcements([raw], "2330", "TWSE", now)[0]
        payload = {"as_of": now.isoformat(), "events": events, "source_status": {"TWSE": {"status": "ok"}}, "coverage_note": "官方重大訊息"}
        with patch("research_ui.fetch_company_events", return_value=payload):
            app = self.app("公告與事件")
            self.button(app, "查詢官方重大訊息").click().run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(app.text_area[0].value, raw["說明"])
            self.assertTrue(app.text_area[0].disabled)


if __name__ == "__main__":
    unittest.main()
