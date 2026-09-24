"""An opt-in Streamlit research page; never writes market or trading documents."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app_security import resolve_stock_identifier
from research_news import fetch_company_events
from research_portfolio import analyze_portfolio, build_daily_checklist
from research_technical import analyze_timeframes, run_research_backtest
from research_workspace import build_research_ideas, parse_holdings

TPE = timezone(timedelta(hours=8))
PROMPT_PATH = Path(__file__).parent / "docs" / "trading_research_prompt.md"


def shown(value, suffix=""):
    return "資料不足" if value is None else f"{value:g}{suffix}" if isinstance(value, (float, int)) else str(value)


def _download(report, name):
    st.download_button("下載本次研究 JSON", json.dumps(report, ensure_ascii=False, indent=2, default=str),
                       file_name=f"research-{name}.json", mime="application/json", key=f"export_{name}")


def _stock_query(stock_names):
    query = st.text_input("股票名稱或代號（指數可填 ^TWII）", value="2330", key="research_stock_query")
    if query.strip() == "^TWII":
        return "^TWII"
    ticker, status = resolve_stock_identifier(query, stock_names)
    if status != "ok":
        st.info("找不到或名稱有歧義，請填明確股票代號。")
        return None
    return ticker


def _dates(scan_date):
    now = datetime.now(TPE)
    # Only completed saved sessions by default; never feed today's live bar as a closed bar.
    ceiling = now.date() if now.hour >= 15 else now.date() - timedelta(days=1)
    try:
        default = min(pd.Timestamp(scan_date).date(), ceiling) if scan_date else ceiling
    except (ValueError, TypeError):
        default = ceiling
    return st.date_input("分析截止日（只取當日以前的行情）", value=default,
                         max_value=ceiling, key="research_as_of").isoformat()


def _chart(frame, as_of, weekly=False):
    visible = frame.copy()
    visible.index = pd.to_datetime(visible.index).tz_localize(None)
    visible = visible.loc[visible.index.normalize() <= pd.Timestamp(as_of)]
    if weekly:
        last_observed = visible.index.max().normalize() if not visible.empty else pd.Timestamp(as_of)
        weekly_cutoff = min(last_observed, pd.Timestamp(as_of))
        visible = visible.resample("W-FRI").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"})
        visible = visible.loc[visible.index <= weekly_cutoff].dropna()
    visible = visible.tail(60)
    fig = go.Figure(go.Candlestick(x=visible.index, open=visible["Open"], high=visible["High"],
                                  low=visible["Low"], close=visible["Close"], name="週線" if weekly else "日線"))
    fig.update_layout(height=300, margin=dict(l=10, r=10, t=20, b=10), xaxis_rangeslider_visible=False)
    st.plotly_chart(fig, width="stretch", key="research_weekly_chart" if weekly else "research_daily_chart")


def render_research_workspace(*, scan_records, scan_date, scan_stale, load_history, stock_names):
    st.title("🔬 交易研究工作台")
    st.info("獨立研究版：不更動每日榜單、績效圖、新舊制規則或 Telegram 排程；不自動下單。")
    st.caption("規則式計算與官方資料查詢，不呼叫付費 AI API；各模組按需執行，結果只保留於本次工作階段。")
    with st.expander("合併後的 6 合 1 提示詞／使用原則"):
        prompt = PROMPT_PATH.read_text(encoding="utf-8")
        st.markdown(prompt)
        st.download_button("下載完整提示詞", prompt, "trading-research-prompt.md", mime="text/markdown")
    module = st.radio("研究模組", ["交易候選", "日週線技術", "公告與事件", "策略回測", "投資組合風險", "每日交易清單"], horizontal=True)
    outputs = st.session_state.setdefault("research_outputs", {})
    if module == "交易候選":
        st.caption(f"依保存掃描資料研究：{scan_date or '日期未確認'}；最多 5 檔，僅現行新制合格候選。不是今天全市場即時掃描。")
        scope = st.text_input("限定股票／名稱／產業（空白代表本次掃描範圍）", key="research_scope")
        if not scan_date or scan_stale:
            st.warning("保存掃描資料尚未確認為最新完整版本，暫不列可執行研究候選；原榜單仍保留。")
            return
        report = build_research_ideas(scan_records, scan_date, scope)
        st.write(report["notice"])
        if not report["ideas"]:
            st.info("目前沒有符合現行條件的候選，不以舊制或放寬風控補滿。")
        for item in report["ideas"]:
            with st.expander(f"{item['ticker']} {item['name']}｜條件評分 {item['score']:g}", expanded=True):
                st.write(item["entry_reason"])
                evidence = item["legacy_evidence"]
                samples = evidence.get("samples")
                rate = evidence.get("win_rate") if samples is not None and samples >= 30 else None
                st.caption(f"舊制技術回測：{shown(rate, '%')}｜樣本 {shown(samples)}｜{evidence.get('credibility', '資料不足')}；與新制及實際績效分開判讀。")
                st.dataframe(pd.DataFrame([{
                    "進場區間": f"{item['entry_low']:g}–{item['entry_high']:g}", "停損": item["stop"], "目標": item["target"],
                    "毛 RRR": round(item["gross_reward_risk"], 2), "含成本 RRR": shown(item["net_reward_risk"]),
                    "風險股數上限": item["max_shares"], "模型停損金額": round(item["modeled_loss"], 2),
                    "估計買入本金（另計費用）": round(item["entry_high"] * item["max_shares"], 2),
                }]), hide_index=True, width="stretch")
                for reason in item["reasons"][:6]:
                    st.text(reason)
                st.warning(item["invalidation"])
                st.caption("以下是保存快照，不代表今天即時更新；財報未涵蓋的現金流／應收存貨分析仍待補充。")
                st.json({"基本面": item["fundamentals"], "籌碼面": item["institutional"], "新制樣本": item["current_samples"]})
        st.caption(report["risk_notice"])
        st.write("未列出原因統計", report["rejected"])
        _download(report, "ideas")
    elif module in ("日週線技術", "策略回測"):
        ticker = _stock_query(stock_names)
        as_of = _dates(scan_date)
        strategy, lookback = "existing", 380
        if module == "策略回測":
            strategy = st.selectbox("策略（獨立研究，不覆寫原模型）", ["existing", "ma_cross", "rsi_rebound"],
                                    format_func=lambda s: {"existing": "現有模型", "ma_cross": "均線交叉研究基準", "rsi_rebound": "RSI 反彈研究基準（不是背離）"}[s])
            lookback = int(st.number_input("回看交易日數", min_value=90, max_value=500, value=380, step=10))
            st.caption("自訂策略限台股成本口徑；指數本身不能直接交易，不用台股稅費假裝指數實盤回測。")
        key = f"{module}:{ticker}:{as_of}:{strategy}:{lookback}"
        if st.button("執行研究", disabled=not ticker):
            try:
                if module == "策略回測" and ticker == "^TWII":
                    raise ValueError("指數可做技術分析；回測請先選擇可交易商品，不能套用股票交易成本。")
                with st.spinner("讀取行情與計算；不更新原榜單…"):
                    frame = load_history(ticker)
                    report = (analyze_timeframes(frame, as_of) if module == "日週線技術"
                              else run_research_backtest(frame, strategy, as_of, lookback=lookback))
                    outputs[key] = (report, frame)
            except Exception as exc:
                outputs.pop(key, None)
                st.error(f"本次研究未完成：{type(exc).__name__}：{exc}")
        if key in outputs:
            report, frame = outputs[key]
            if report.get("status") not in ("ok", "complete"):
                st.warning(report.get("reason") or "資料不足，無法形成完整結論。")
            if module == "日週線技術":
                for period, title in (("daily", "日線"), ("weekly", "已結束週線")):
                    section = report.get(period, {})
                    st.subheader(title)
                    st.caption(f"資料日 {section.get('date', '--')}｜{section.get('bars', 0)} 根；MA20/60以本段週期計算。")
                    if section.get("status") == "ok":
                        _chart(frame, as_of, weekly=period == "weekly")
                    st.dataframe(pd.DataFrame([{label: shown(section.get(field)) for field, label in (
                        ("close", "收盤"), ("ma20", "MA20"), ("ma60", "MA60"), ("rsi14", "RSI14"),
                        ("macd_hist", "MACD柱"), ("support20", "20期最低"), ("resistance20", "20期最高"),
                        ("regression_slope20_pct", "20期回歸斜率%"))}]), hide_index=True)
                    st.write(section.get("signal", "資料不足"))
                    for text in section.get("conditions", []) + section.get("limitations", []):
                        st.text(text)
            else:
                summaries = []
                for group, title in (("overall", "全部已結束"), ("training", "前段研究"), ("validation", "後段驗證")):
                    values = report.get("metrics", {}).get(group, {})
                    summaries.append({"分組": title, "樣本數": values.get("samples", 0),
                                      "勝率": shown(values.get("win_rate_pct"), "%"),
                                      "勝率區間": str(values.get("confidence_interval_pct") or "資料不足"),
                                      "獲利因子": shown(values.get("profit_factor")),
                                      "僅平倉序列回撤%": shown(values.get("max_drawdown_closed_trade_pct")),
                                      "平均淨報酬%": shown(values.get("avg_return_pct"))})
                st.dataframe(pd.DataFrame(summaries), hide_index=True, width="stretch")
                st.caption("樣本不足 30 時摘要不顯示精確勝率；區間仍非未來保證。最大回撤僅平倉序列，非逐日市值資金曲線。")
                for text in report.get("assumptions", []):
                    st.text(text)
                with st.expander("診斷、交易明細與研究附錄"):
                    st.json(report)
            for text in report.get("notes", []):
                st.text(text)
            _download(report, "technical" if module == "日週線技術" else "backtest")
    elif module == "公告與事件":
        ticker = _stock_query(stock_names)
        key = f"news:{ticker}"
        if st.button("查詢官方重大訊息", disabled=not ticker or ticker == "^TWII"):
            with st.spinner("查詢官方資料來源…"):
                outputs[key] = fetch_company_events(ticker)
        if key in outputs:
            report = outputs[key]
            st.caption(f"查詢截至 {report['as_of']}")
            st.info(report["coverage_note"])
            st.json(report["source_status"])
            if not report["events"]:
                st.warning("目前來源中沒有可展示的匹配公告；請先檢查上方來源是否成功，不代表無重大風險。")
            for event in report["events"]:
                with st.expander(event["title"], expanded=True):
                    st.caption(f"發布 {event['published_at']}｜事件日 {event['event_date'] or '未提供'}｜{event['source']}")
                    st.link_button("官方公告資料集", event["source_url"])
                    st.caption(event["source_link_kind"])
                    st.write("短期檢查：", event["interpretation"]["short_term_check"])
                    st.write("長期檢查：", event["interpretation"]["long_term_check"])
                    st.write(event["allocation"])
                    st.caption("無可靠事件研究：不輸出預期漲跌幅／目標價。")
                    st.text_area("公告內容（原始資料）", event["details"], height=130, disabled=True,
                                 key=f"event_body:{event['source']}:{event['published_at']}:{event['title']}")
            _download(report, "events")
    elif module == "投資組合風險":
        st.caption("只輸入您的實際持倉；不讀取 Top10、模擬單或真實日誌。數字輸入 25 代表 25%，剩餘為現金。不保存到雲端。")
        text = st.text_area("每行：股票代號,配置百分比,產業（可省略）", placeholder="2330,25,半導體\n2317,15,電子組裝", key="research_holdings")
        cols = st.columns(4)
        equity = cols[0].number_input("總資金 NT$", min_value=1.0, value=100000.0, step=10000.0)
        stock_cap = cols[1].number_input("單股配置上限%", min_value=1.0, max_value=100.0, value=25.0)
        sector_cap = cols[2].number_input("單產業配置上限%", min_value=1.0, max_value=100.0, value=40.0)
        tolerance = cols[3].number_input("壓力情境可接受損失%", min_value=0.1, max_value=100.0, value=10.0)
        key = f"portfolio:{text}:{equity}:{stock_cap}:{sector_cap}:{tolerance}"
        if st.button("檢查實際配置"):
            try:
                holdings = parse_holdings(text)
                # Validate weights before any network work.
                analyze_portfolio(holdings, equity=equity)
                series = {}
                with st.spinner("核對持倉共同歷史日期…"):
                    for position in holdings:
                        frame = load_history(position["ticker"])
                        if frame is not None and not frame.empty:
                            completed = datetime.now(TPE).date() - timedelta(days=1)
                            subset = frame.loc[pd.to_datetime(frame.index).date <= completed]
                            series[position["ticker"]] = subset["Close"].tail(121)
                    outputs[key] = analyze_portfolio(holdings, pd.DataFrame(series), equity=equity,
                                                     max_stock_weight_pct=stock_cap, max_sector_weight_pct=sector_cap,
                                                     loss_tolerance_pct=tolerance)
            except Exception as exc:
                outputs.pop(key, None)
                st.error(f"無法完成配置檢查：{type(exc).__name__}：{exc}")
        if key in outputs:
            report = outputs[key]
            stress = report["stress_scenario"]
            cols = st.columns(3)
            cols[0].metric("股票／現金", f"{report['total_stock_weight_pct']:g}%／{report['cash_weight_pct']:g}%")
            cols[1].metric("假設全持股跌 20%", f"{stress['portfolio_return_pct']:+g}%")
            cols[2].metric("情境損失 NT$", shown(stress["estimated_loss_amount"]))
            st.caption(stress["label"])
            for warning in report["warnings"]:
                st.warning(warning["message"])
            plan = report["rebalance_proposal"]
            st.subheader("減少超限曝險的示意（不執行交易）")
            st.caption(plan["note"])
            st.dataframe(pd.DataFrame(plan["holdings"]).rename(columns={
                "ticker": "代號", "current_weight_pct": "目前%", "illustrative_weight_pct": "示意調整後%",
                "reduction_pct_points": "減少百分點"}), hide_index=True, width="stretch")
            st.write(f"示意現金比例：{plan['cash_weight_pct']:g}%；增加 {plan['cash_increase_pct_points']:g} 個百分點。")
            st.subheader("歷史相關性（非下跌時的保證）")
            pairs = report["correlations"].get("pairs", [])
            if pairs:
                st.dataframe(pd.DataFrame(pairs), hide_index=True, width="stretch")
            with st.expander("計算假設與資料完整度"):
                st.json(report)
            _download(report, "portfolio")
    else:
        plan_date = st.date_input("計畫日期（須自行確認交易所休市日）", value=datetime.now(TPE).date(), key="research_plan_date")
        report = build_daily_checklist(plan_date.isoformat(), has_candidates=None)
        st.warning("人工作業清單，不建立排程或下單。先到候選模組確認名單；是否交易日須另外核對。")
        for item in report["items"]:
            with st.expander(f"{item['time']}｜{item['stage']}", expanded=True):
                for index, text in enumerate(item["checks"]):
                    st.checkbox(text, key=f"research_plan:{plan_date}:{item['time']}:{index}")
        for text in report.get("warnings", []):
            st.write(text)
        _download(report, "checklist")
