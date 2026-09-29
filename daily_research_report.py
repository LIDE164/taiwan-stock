"""Dated, read-only research for the existing new/legacy executable union.

This report is a separate rules-based calculation, not a ranking source, an
order generator, an LLM call, or a replacement for the daily performance chart.
Loaders are injected so networking and notification persistence stay outside.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
from itertools import combinations
import math

import pandas as pd

from backtest_reporting import primary_backtest_display, sample_breakdown
from execution_costs import calculate_max_odd_lot_position, estimate_round_trip_net_profit
from ranking_comparison import build_comparison_rows, comparison_display_record
from research_news import SOURCES, classify_event
from research_portfolio import build_daily_checklist
from research_technical import analyze_timeframes, run_research_backtest
from telegram_links import build_analysis_url

SCHEMA = "daily_executable_research_v1"
TPE = timezone(timedelta(hours=8))
MAX_MESSAGE_UNITS = 3500
PRICE_TOLERANCE = 0.02
MIN_SAMPLES = 30


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _text(value, limit=180):
    # Data fields are text only: never parse them as commands or Telegram HTML.
    return " ".join(str(value or "").split())[:limit]


def _time(value):
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("時間必須包含時區")
    return parsed


def _cut_history(frame, analysis_date):
    if not isinstance(frame, pd.DataFrame) or not isinstance(frame.index, pd.DatetimeIndex):
        raise ValueError("行情缺少交易日索引")
    result = frame.copy(deep=True)
    if result.index.tz is not None:
        result.index = result.index.tz_convert("Asia/Taipei").tz_localize(None)
    result.index = result.index.normalize()
    if result.index.hasnans or result.index.has_duplicates:
        raise ValueError("行情日期無效或重複")
    return result.loc[result.index <= pd.Timestamp(analysis_date)].sort_index()


def _risk_plan(display):
    low, high, stop, target = [_number(display.get(key)) for key in (
        "Entry_Low", "Entry_High", "Entry_Stop", "Entry_Target")]
    if any(value is None for value in (low, high, stop, target)) or not 0 < stop < low <= high < target:
        return {"status": "unavailable", "reason": "保存的進場、停損與目標價格不完整或矛盾"}
    sizing = calculate_max_odd_lot_position(high, stop, 5000)
    if sizing is None or sizing.shares < 1:
        return {"status": "unavailable", "reason": "含成本後沒有符合模型風險的可行股數"}
    net_target = estimate_round_trip_net_profit(high, target, sizing.shares)
    return {
        "status": "ok", "entry_low": low, "entry_high": high, "stop": stop, "target": target,
        "sizing_entry_price": high, "shares": sizing.shares,
        "modeled_stop_loss": sizing.estimated_net_loss, "max_modeled_loss": 5000,
        "estimated_entry_notional": high * sizing.shares,
        "estimated_target_net_profit": net_target,
        "gross_reward_risk": (target - high) / (high - stop),
        "net_reward_risk": net_target / sizing.estimated_net_loss if net_target is not None else None,
        "reason": _text(display.get("Entry_Reason"), 220),
        "basis": "保存榜單價格；以進場區上限核算，並非即時成交報價",
    }


def _news(ticker, now, load_news):
    result = {"queried_at": now.isoformat(), "events": [], "source_status": {}, "warnings": [],
              "coverage_note": "僅官方端點目前提供的重大訊息，非完整新聞庫；沒有公告不代表沒有風險。最新公告不納入歷史回測。"}
    try:
        raw = load_news(ticker, now)
        if not isinstance(raw, Mapping):
            raise ValueError("公告回應格式錯誤")
        as_of = _time(raw.get("as_of"))
        if as_of > now:
            raise ValueError("公告查詢時間超前")
        result["queried_at"] = as_of.isoformat()
        if now - as_of > timedelta(days=1):
            result["warnings"].append("公告快取超過一天，不能視為最新完整資訊")
        statuses = raw.get("source_status", {})
        if not isinstance(statuses, Mapping):
            statuses = {}
        for source, url in SOURCES.items():
            state = statuses.get(source, {})
            valid = isinstance(state, Mapping) and state.get("source_url") == url
            status = state.get("status") if valid else "unavailable"
            if status not in ("ok", "partial", "unavailable"):
                status = "unavailable"
            result["source_status"][source] = {"status": status, "source_url": url}
            if status != "ok":
                result["warnings"].append(f"{source} 公告來源{'不完整' if status == 'partial' else '無法核實'}")
        events = raw.get("events", [])
        if not isinstance(events, list):
            raise ValueError("公告項目格式錯誤")
        rejected = 0
        for event in events:
            try:
                if not isinstance(event, Mapping):
                    raise ValueError("公告格式錯誤")
                event_source = event.get("source")
                if (not isinstance(event_source, str) or event_source not in SOURCES or event.get("source_url") != SOURCES[event_source]
                        or str(event.get("ticker")) != ticker
                        or result["source_status"][event_source]["status"] == "unavailable"):
                    raise ValueError("公告來源或股票不符")
                published = _time(event.get("published_at"))
                if published > as_of or published > now:
                    raise ValueError("公告時間超前")
                title = _text(event.get("title"), 300)
                if not title:
                    raise ValueError("公告缺主旨")
                event_date = event.get("event_date")
                if event_date is not None:
                    event_date = date.fromisoformat(str(event_date)).isoformat()
                result["events"].append({
                    "title": title, "published_at": published.isoformat(), "event_date": event_date,
                    "source": event_source, "source_url": SOURCES[event_source],
                    "interpretation": classify_event(title), "estimated_price_range": None,
                })
            except (ValueError, TypeError, KeyError):
                rejected += 1
        result["events"].sort(key=lambda event: _time(event["published_at"]), reverse=True)
        result["events"] = result["events"][:2]
        if rejected:
            result["warnings"].append(f"排除 {rejected} 筆來源／發布時間無法核實的公告")
    except Exception as exc:
        # No raw exception text: a loader may include a credential-bearing URL.
        result["events"] = []
        result["warnings"].append(f"公告無法取得或驗證（{type(exc).__name__}），不等同無新聞")
    return result


def _candidate_correlations(histories, *, tickers=None):
    tickers = list(dict.fromkeys(tickers if tickers is not None else histories))
    if len(tickers) < 2:
        return []
    # Keep absent histories as all-missing columns so coverage is measured
    # against every candidate pair, not only the successful downloads.
    closes = pd.DataFrame(histories).reindex(columns=tickers).sort_index().tail(253)
    returns = closes.pct_change(fill_method=None)
    pairs = []
    for left, right in combinations(closes.columns, 2):
        observed = returns[[left, right]].dropna()
        value = None
        if len(observed) >= MIN_SAMPLES and all(observed[column].nunique() > 1 for column in (left, right)):
            value = _number(observed[left].corr(observed[right]))
        pairs.append({"left": left, "right": right, "samples": len(observed),
                      "correlation": round(value, 4) if value is not None else None,
                      "through_date": observed.index[-1].date().isoformat() if len(observed) else None})
    return pairs


def _checklist(analysis_date, has_candidates):
    from market_calendar import CALENDAR_SOURCE, next_scheduled_session

    next_day = next_scheduled_session(date.fromisoformat(analysis_date))
    if next_day is None:
        return {"date": None, "items": [], "calendar_source": CALENDAR_SOURCE,
                "notice": "下一交易日尚未由已知官方日曆確認；不猜測日期、不執行。"}
    plan = build_daily_checklist(next_day.isoformat(), has_candidates=has_candidates)
    return {"date": next_day.isoformat(), "items": plan["items"], "calendar_source": CALENDAR_SOURCE,
            "notice": "依已知交易日曆排定；仍需盤前核對臨時休市。僅人工檢查清單，不自動下單。"}


def build_daily_research_report(records, trading_date, *, load_history, load_news, now=None):
    """Research every saved executable-comparison candidate without mutation.

    ``load_history(ticker)`` supplies dated completed OHLCV. ``load_news(ticker,
    aware_now)`` returns the official ``fetch_company_events`` response shape.
    Saved price plans and historical entry evidence remain frozen and separate
    from fetched adjusted price research and currently published announcements.
    """
    analysis_date = date.fromisoformat(trading_date).isoformat()
    now = now or datetime.now(TPE)
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("報告產生時間須包含時區")
    now = now.astimezone(TPE)
    if date.fromisoformat(analysis_date) > now.date():
        raise ValueError("分析日期不可超過報告產生日期")
    valid = [row for row in records if isinstance(row, Mapping) and row.get("Data_Date") == analysis_date]
    rows = build_comparison_rows(valid)
    items, histories = [], {}
    for row in rows:
        ticker = row["代號"]
        display = comparison_display_record(row)
        evidence = primary_backtest_display(row)
        if evidence.get("samples") is None or evidence["samples"] < MIN_SAMPLES:
            evidence["win_rate"] = None
        item = {
            "ticker": ticker, "name": _text(row.get("名稱") or ticker, 40),
            "industry": _text(row.get("產業") or "未分類", 50), "score": _number(row.get("Score")),
            "versions": list(row["Execution_Versions"]), "version_label": row["Execution_Version_Label"],
            "new_approved": "new" in row["Execution_Versions"],
            "original_new_reason": _text(row.get("Comparison_New_Reason") or row.get("Entry_Reason"), 180),
            "saved_close": _number(row.get("收盤價")), "price_date": analysis_date,
            "plan": _risk_plan(display), "legacy_evidence": evidence,
            "saved_current_samples": sample_breakdown(row),
            "fundamentals": {key: row.get(key) for key in (
                "EPS", "EPS_Period", "EPS_Source", "YoY", "MoM", "Revenue_Period", "Revenue_Status",
                "Revenue_Expected_Period", "Financial_Period", "Financial_Source", "Financial_Status", "Financial_Risk_Level",
                "Financial_Operating_Margin", "Financial_Debt_Ratio")},
            "institutional": {key: row.get(key) for key in (
                "Whale_Net", "Whale_Net_Days", "Institutional_Status", "Institutional_Latest_Date", "Institutional_Source")},
            "analysis_url": build_analysis_url(ticker), "warnings": [],
        }
        try:
            frame = _cut_history(load_history(ticker), analysis_date)
            item["technical"] = analyze_timeframes(frame, analysis_date)
            if item["technical"]["status"] != "ok":
                # This is a controlled diagnostic from our local OHLCV
                # validator, not an external loader exception. Preserve it:
                # a contradictory candle is not the same as missing history.
                reason = _text(item["technical"].get("reason"), 300) or "行情完整性驗證未通過"
                item["price_alignment"] = "unavailable"
                item["backtest"] = {"status": "unavailable", "reason": reason, "metrics": {}}
                item["warnings"].append("行情驗證未通過：" + reason + "；不刪除異常 K 棒或補造價格。")
            else:
                fresh = item["technical"]["data_date"] == analysis_date
                item["history_close"] = _number(frame.Close.iloc[-1])
                matching = fresh and item["saved_close"] is not None and abs(item["history_close"] - item["saved_close"]) <= PRICE_TOLERANCE + 1e-10
                item["price_alignment"] = "matched" if matching else "price_adjustment_mismatch" if fresh else "stale_history"
                if not matching:
                    item["warnings"].append(
                        "保存收盤與現查同日價格不一致（可能除權息／資料修訂）；技術回測是獨立行情口徑，不套用到原價格計畫。"
                        if fresh else "現查行情未涵蓋分析日；不將舊行情冒認當日技術分析。")
                item["backtest"] = {key: value for key, value in run_research_backtest(frame, "existing", analysis_date).items()
                                    if key != "trades"}
                if fresh:
                    histories[ticker] = frame.Close.astype(float)
        except Exception as exc:
            item["price_alignment"] = "unavailable"
            item["technical"] = {"status": "unavailable", "reason": f"行情無法取得或驗證（{type(exc).__name__}）"}
            item["backtest"] = {"status": "unavailable", "reason": "沒有可核對的完整歷史行情", "metrics": {}}
            item["warnings"].append("歷史行情無法取得或驗證，不補造價格或回測")
        item["news"] = _news(ticker, now, load_news)
        items.append(item)
    counts = Counter(item["industry"] for item in items)
    checklist = _checklist(analysis_date, bool(items))
    forecast_date = date.fromisoformat(checklist["date"]) if checklist["date"] else None
    forecast_elapsed = forecast_date is not None and (
        now.date() > forecast_date or (now.date() == forecast_date and (now.hour, now.minute) >= (13, 30)))
    return {
        "schema": SCHEMA, "analysis_date": analysis_date, "generated_at": now.isoformat(),
        "forecast_date": checklist["date"],
        "forecast_period_elapsed": forecast_elapsed,
        "stock_count": len(items), "new_count": sum(item["new_approved"] for item in items),
        "legacy_count": sum("legacy" in item["versions"] for item in items), "items": items,
        "excluded_date_rows": len(records) - len(valid),
        "candidate_risk": {"industry_counts": dict(counts), "correlations": _candidate_correlations(histories, tickers=[item["ticker"] for item in items]),
                           "actual_holdings_known": False, "portfolio_stress_return_pct": None,
                           "notice": "這是候選名單，不是實際持倉；未提供真實配置與總資金，不假設等權、不計算個人下跌 20% 損失或自動再平衡。"},
        "checklist": checklist,
        "notice": "規則式核算，非保證高勝率或自動下單。保留原每日榜單與績效圖，研究不改入榜、停損或結算規則；舊制僅比較，新制繼續獨立累積。",
    }


def _fmt(value, suffix="", digits=2):
    numeric = _number(value)
    return "未提供" if numeric is None else f"{numeric:,.{digits}f}".rstrip("0").rstrip(".") + suffix


def _rate(summary):
    count = summary.get("samples")
    if _number(count) is None:
        return "樣本未提供"
    if count < MIN_SAMPLES:
        return f"樣本 {count}，不足 30 不顯示精確勝率"
    return f"樣本 {count}／歷史勝率 {_fmt(summary.get('win_rate_pct'), '%', 1)}"


def _technical_line(technical, unit):
    series = technical.get(unit, {})
    label = "日線" if unit == "daily" else "完整週線"
    if not series:
        return f"{label}：無法核算"
    return (f"{label} {_text(series.get('date'), 10)}：{_text(series.get('trend'), 25)}；"
            f"20期支撐 {_fmt(series.get('support20'))}／壓力 {_fmt(series.get('resistance20'))}，"
            f"20MA {_fmt(series.get('ma20'))}／60MA {_fmt(series.get('ma60'))}，RSI {_fmt(series.get('rsi14'), '', 1)}／MACD柱 {_fmt(series.get('macd_hist'))}；"
            f"{_text(series.get('signal'), 45)}")


def _improvement(backtest):
    if backtest.get("status") != "ok":
        return "先核對來源與異常交易日，取得可驗證行情後再比較策略；不刪除壞 K 棒、補造價格或新增交易資格。"
    metrics = backtest["metrics"]
    overall, validation = metrics["overall"], metrics["validation"]
    count, validation_count = overall.get("samples", 0), validation.get("samples", 0)
    if not count:
        return "目前沒有已結束樣本，先檢查訊號、成交與未結束交易診斷，持續累積；不可把 0 筆當作沒有風險。"
    if count < MIN_SAMPLES or validation_count < MIN_SAMPLES:
        return f"全期 {count}／獨立驗證 {validation_count} 筆仍不足穩健比較；先固定規則累積樣本與實際績效，不為提高勝率調參。"
    factor = _number(validation.get("profit_factor"))
    if factor is not None and factor <= 1:
        return "驗證獲利因子未大於 1，尚未呈現成本後正優勢；逐筆檢查成本與虧損型態，再以未用過的期間驗證，勿放寬門檻。"
    return "已有足量分期樣本，下一步比較訓練／驗證成本後報酬及最大回撤，並用新資料持續驗證；不把歷史優勢保證到未來。"


def _item_message(item, analysis_date):
    plan, funds = item["plan"], item["fundamentals"]
    lines = [f"{item['ticker']} {item['name']}｜{item['version_label']}｜分數 {_fmt(item['score'])}",
             f"六合一規則式核算｜價格截至 {analysis_date}"]
    if not item["new_approved"]:
        lines.append("僅舊制比較，未列入新制可執行／不新增績效持倉。")
        lines.append("原新制未入榜原因：" + (item["original_new_reason"] or "保存資料未提供判定原因"))
    lines.append("1. 交易計畫／基本面")
    if plan["status"] == "ok":
        lines.extend([
            f"保存進場 {_fmt(plan['entry_low'])}–{_fmt(plan['entry_high'])}；停損 {_fmt(plan['stop'])}；目標 {_fmt(plan['target'])}",
            f"按區間上限計 {plan['shares']:,} 股，模型含成本停損損失 NT${plan['modeled_stop_loss']:,.0f}；毛／淨報酬風險比 {_fmt(plan['gross_reward_risk'])}／{_fmt(plan['net_reward_risk'])}",
            f"條件：{_text(plan['reason'], 140)}",
        ])
        if plan["net_reward_risk"] is not None and plan["net_reward_risk"] < 1:
            lines.append("含成本目標淨利小於模型停損損失，僅比較觀察；本核算不升級為買入訊號。")
    else:
        lines.append(plan["reason"])
    lines.append(f"保存 EPS {_fmt(funds.get('EPS'))}（{_text(funds.get('EPS_Period'), 20) or '期間未提供'}／來源 {_text(funds.get('EPS_Source'), 30) or '未提供'}），非本次更新。")
    lines.append(f"保存營收年增 {_fmt(funds.get('YoY'), '%')}／月增 {_fmt(funds.get('MoM'), '%')}；期間 {_text(funds.get('Revenue_Period'), 20) or '未提供'}／狀態 {_text(funds.get('Revenue_Status'), 30) or '未提供'}。")
    lines.append(f"財報期間 {_text(funds.get('Financial_Period'), 20) or '未提供'}／狀態 {_text(funds.get('Financial_Status'), 30) or '未提供'}／風險 {_text(funds.get('Financial_Risk_Level'), 15) or '未提供'}／來源 {_text(funds.get('Financial_Source'), 40) or '未提供'}；過期／未知不當作已驗證，現金流與存貨細節仍待核對。")
    institutional = item["institutional"]
    lines.append(f"法人保存合計 {_fmt(institutional.get('Whale_Net'))} 張／{_fmt(institutional.get('Whale_Net_Days'))} 日；截至 {_text(institutional.get('Institutional_Latest_Date'), 10) or '未提供'}／狀態 {_text(institutional.get('Institutional_Status'), 30) or '未提供'}；缺值不補零，非即時籌碼。")
    lines.extend(["2. 日／週線", _technical_line(item["technical"], "daily"), _technical_line(item["technical"], "weekly")])
    for warning in item["warnings"]:
        lines.append("注意：" + _text(warning, 140))
    news = item["news"]
    lines.append(f"3. 公告影響（現查 {news['queried_at']}，與價格日分開）")
    if not news["events"]:
        lines.append("目前來源未提供可驗證公告；不等同沒有新聞或風險。")
    for event in news["events"]:
        interpretation = event["interpretation"]
        lines.extend([
            f"{event['published_at']}｜{_text(event['title'], 100)}",
            f"短期：{_text(interpretation['short_term_check'], 95)} 長期：{_text(interpretation['long_term_check'], 95)}",
            "官方來源資料集：" + event["source_url"],
        ])
    if news["warnings"]:
        lines.append("公告限制：" + "；".join(_text(warning, 70) for warning in news["warnings"]))
    lines.append("無事件研究，不估造新聞漲跌區間／不據此增加部位。")
    legacy = item["legacy_evidence"]
    lines.append("4. 回測（分數不是勝率）")
    lines.append("保存舊制：" + _rate({"samples": legacy.get("samples"), "win_rate_pct": legacy.get("win_rate")}))
    lines.append("保存新制：" + item["saved_current_samples"]["sample_text"])
    backtest = item["backtest"]
    if backtest["status"] == "ok":
        metrics = backtest["metrics"]
        has_closed_trades = (_number(metrics["overall"].get("samples")) or 0) > 0
        factor = metrics["overall"].get("profit_factor") if has_closed_trades else None
        drawdown = metrics["overall"].get("max_drawdown_closed_trade_pct") if has_closed_trades else None
        lines.extend(["現行策略唯讀重算：" + _rate(metrics["overall"]),
                      "訓練：" + _rate(metrics["training"]) + "；驗證：" + _rate(metrics["validation"]),
                      f"獲利因子 {_fmt(factor)}；已平倉曲線最大回撤 {_fmt(drawdown, '%')}（不含持倉內浮虧）。"])
        lines.append(f"回測區間 {_text(backtest.get('window_start'), 10) or '未提供'}–{_text(backtest.get('window_end'), 10) or '未提供'}；僅歷史 OHLCV，不將最新基本面／籌碼／新聞回填歷史。")
    else:
        lines.append("重算回測無法提供：" + _text(backtest.get("reason"), 100))
    lines.append("研究改進：" + _improvement(backtest))
    lines.extend([
        "5. 部位／組合：每檔 NT$5,000 是含成本模型停損風險，不是投入金額或最大虧損保證。真實持倉未知，組合曝險與下跌 20% 壓力測試待提供配置。",
        "6. 執行：盤前重驗價格、公告及新制資格；離開進場區／跌破停損／資料變動即重新檢核，不追價、不放寬停損。",
        "解析連結（開啟時顯示最新頁面，非本報告凍結內容）：" + item["analysis_url"],
    ])
    return "\n".join(lines)


def _split_units(text, limit=MAX_MESSAGE_UNITS):
    """Split plain text on lines first, with a Unicode-codepoint safe fallback."""
    chunks, current = [], ""
    for line in text.splitlines(keepends=True):
        for character in line:
            if len((current + character).encode("utf-16-le")) // 2 > limit:
                chunks.append(current.rstrip())
                current = ""
            current += character
    if current.strip():
        chunks.append(current.rstrip())
    return chunks


def format_research_messages(report):
    """Return separate additive plain-text Telegram messages; no sending here."""
    if report.get("schema") != SCHEMA:
        raise ValueError("不支援的研究報告版本")
    date_text = report["analysis_date"]
    summary = (
        f"可執行榜單｜六合一規則式核算\n價格／榜單分析日：{date_text}\n"
        f"預排預測日：{report.get('forecast_date') or '待官方日曆確認'}（保存盤後計畫，尚非盤中即時再確認）\n"
        f"報告與公告查詢時間：{report['generated_at']}\n"
        f"共 {report['stock_count']} 檔；新制 {report['new_count']}／舊制 {report['legacy_count']}（重疊股票只分析一次）。\n"
        + ("新制沒有可執行股票；以下舊制僅供比較。\n" if report["new_count"] == 0 and report["stock_count"] else "")
        + ("本分析日新舊制均無符合名單，不硬湊交易機會。\n" if not report["stock_count"] else "")
        + ("預排交易時段已過，本報告為指定榜單回顧／補充分析，非下一交易日新名單；需等待最新盤後掃描。\n"
           if report.get("forecast_period_elapsed") else "")
        + report["notice"]
    )
    messages = [summary]
    messages.extend(_item_message(item, date_text) for item in report["items"])
    risk = report["candidate_risk"]
    lines = ["候選群組風險與每日人工檢查清單", risk["notice"],
             "候選產業檔數（非資金權重）：" + ("、".join(f"{industry} {count} 檔" for industry, count in risk["industry_counts"].items()) or "無候選")]
    pairs = risk["correlations"]
    high = [pair for pair in pairs if pair["correlation"] is not None and pair["correlation"] >= 0.8]
    if high:
        lines.append("近 252 個觀測間隔日報酬高度同向（Pearson，非未來保證）：")
        lines.extend(f"{p['left']}／{p['right']}：{p['correlation']:.2f}，共同樣本 {p['samples']}" for p in high[:10])
        if len(high) > 10:
            lines.append(f"另 {len(high) - 10} 組高度同向，詳見完整研究資料。")
    known = sum(pair["correlation"] is not None for pair in pairs)
    lines.append(f"相關性可核算 {known}/{len(pairs)} 組；不足 30 筆共同報酬或固定報酬時標未知，不補 0。")
    checklist = report["checklist"]
    lines.append("預排執行日：" + (checklist["date"] or "待日曆確認"))
    lines.append(checklist["notice"])
    if checklist.get("calendar_source"):
        lines.append("預定交易日曆來源：" + checklist["calendar_source"])
    for item in checklist["items"]:
        lines.append(item["time"] + " " + item["stage"] + "：" + "；".join(item["checks"]))
    lines.append("成本假設：買賣手續費各 0.1425%（每筆最低 NT$20）、賣出稅 0.3%、非跳空停損滑價 0.05%。跳空與流動性仍可能使虧損超過模型上限。未建立或修改任何實際委託。")
    lines.append("回測改進檢核：持續分開累積訓練／驗證與實際績效，檢查成本後優勢及落差；樣本不足不微調到漂亮勝率，也不為補滿名單放寬風控。")
    messages.append("\n".join(lines))
    return [chunk for message in messages for chunk in _split_units(message)]
