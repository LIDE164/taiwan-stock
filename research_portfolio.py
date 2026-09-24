"""Read-only portfolio research from explicitly entered holdings and real closes.

This module does not access broker accounts, infer holdings from research lists,
submit orders, alter ranking criteria, or create scheduled jobs.
"""

from datetime import date, datetime
from itertools import combinations
import math

import pandas as pd


MIN_CORRELATION_PAIRS = 30


def _finite_number(value, name, *, minimum=0.0, maximum=None):
    if isinstance(value, bool):
        raise ValueError(f"{name} 必須是有效數值")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必須是有效數值") from exc
    if not math.isfinite(result) or result < minimum or (maximum is not None and result > maximum):
        raise ValueError(f"{name} 超出有效範圍")
    return result


def _warning(code, message, *, severity="warning", **details):
    return {"code": code, "severity": severity, "message": message, **details}


def _clean_holdings(holdings):
    if not isinstance(holdings, (list, tuple)):
        raise ValueError("持倉必須是使用者填寫的清單")
    cleaned = []
    seen = set()
    for row in holdings:
        if not isinstance(row, dict):
            raise ValueError("每筆持倉必須包含代號與配置比例")
        ticker = str(row.get("ticker") or "").strip()
        if not ticker or ticker.casefold() in seen:
            raise ValueError("股票代號不可空白或重複")
        seen.add(ticker.casefold())
        weight = _finite_number(row.get("weight_pct"), "weight_pct", maximum=100.0)
        industry = str(row.get("industry") or "").strip()
        if industry.casefold() in {"unknown", "none", "nan", "未知", "未分類", "其他", "不明"}:
            industry = ""
        cleaned.append({
            "ticker": ticker, "weight_pct": weight, "industry": industry or None,
            **{key: row[key] for key in ("price", "stop", "shares") if key in row},
        })
    if math.fsum(row["weight_pct"] for row in cleaned) > 100.0 + 1e-9:
        raise ValueError("總配置不可超過 100%；不會自動正規化或假設槓桿")
    return cleaned


def _close_frame(close_history, tickers):
    if close_history is None:
        return pd.DataFrame(columns=tickers, dtype=float)
    if isinstance(close_history, pd.DataFrame):
        frame = close_history.copy()
    elif isinstance(close_history, dict):
        frame = pd.DataFrame({key: pd.Series(value) for key, value in close_history.items()})
    else:
        raise ValueError("close_history 需為以日期為索引、股票代號為欄位的收盤價資料")
    if not frame.empty:
        if isinstance(frame.index, pd.RangeIndex) or pd.api.types.is_numeric_dtype(frame.index.dtype):
            raise ValueError("收盤價資料必須包含真實日期，不能使用流水號")
        try:
            index = pd.DatetimeIndex(pd.to_datetime(frame.index, errors="raise"))
        except (TypeError, ValueError) as exc:
            raise ValueError("收盤價日期無效") from exc
        if index.hasnans:
            raise ValueError("收盤價日期不可缺失")
        if index.tz is not None:
            index = index.tz_convert("Asia/Taipei").tz_localize(None)
        frame.index = index.normalize()
        if frame.index.has_duplicates:
            raise ValueError("收盤價資料同一天不可重複")
    if frame.columns.has_duplicates:
        raise ValueError("收盤價代號不可重複")
    frame.columns = frame.columns.map(str)
    if frame.columns.has_duplicates:
        raise ValueError("收盤價代號不可重複")
    frame = frame.reindex(columns=tickers).sort_index()
    frame = frame.apply(pd.to_numeric, errors="coerce")
    frame = frame.where(frame.gt(0) & frame.lt(float("inf")))
    return frame


def _correlations(holdings, close_history):
    tickers = [row["ticker"] for row in holdings if row["weight_pct"] > 0]
    frame = _close_frame(close_history, tickers)
    # Keep missing sessions: a missing close must not become a zero return or a
    # multi-session return incorrectly paired with somebody else's daily move.
    returns = frame.pct_change(fill_method=None)
    returns = returns.where(returns.gt(-float("inf")) & returns.lt(float("inf")))
    pairs = []
    for left, right in combinations(tickers, 2):
        matched = returns[[left, right]].dropna()
        count = len(matched)
        value = None
        status = "insufficient_samples"
        if count >= MIN_CORRELATION_PAIRS:
            if matched[left].nunique() <= 1 or matched[right].nunique() <= 1:
                status = "undefined_constant_returns"
            else:
                computed = float(matched[left].corr(matched[right]))
                if math.isfinite(computed):
                    value = round(computed, 4)
                    status = "available"
                else:
                    status = "undefined"
        pairs.append({
            "left": left, "right": right, "correlation": value,
            "samples": count, "status": status,
            "from_date": matched.index[0].date().isoformat() if count else None,
            "through_date": matched.index[-1].date().isoformat() if count else None,
        })
    available = sum(pair["correlation"] is not None for pair in pairs)
    return {
        "status": "not_applicable" if not pairs else ("complete" if available == len(pairs) else "incomplete"),
        "minimum_samples": MIN_CORRELATION_PAIRS, "pairs": pairs,
        "pair_count": len(pairs), "available_pair_count": available,
        "method": "共同真實日期的日收盤報酬 Pearson 相關；缺值不填補，不代表未來相關性",
    }


def _stop_risk(holdings, equity):
    records = []
    warnings = []
    positive = [row for row in holdings if row["weight_pct"] > 0]
    for row in positive:
        ticker = row["ticker"]
        record = {"ticker": ticker, "status": "unknown", "risk_amount": None, "risk_pct_of_equity": None}
        try:
            price = _finite_number(row.get("price"), "price", minimum=0.00000001)
            stop = _finite_number(row.get("stop"), "stop", minimum=0.00000001)
            shares = _finite_number(row.get("shares"), "shares", minimum=1.0)
            if stop >= price or not shares.is_integer():
                raise ValueError("多頭停損須低於現價，股數須為正整數")
        except ValueError:
            record["reason"] = "需有效現價、低於現價的停損價與正整數持有股數"
            records.append(record)
            continue
        implied_weight = price * shares / equity * 100 if equity is not None else None
        if implied_weight is not None and abs(implied_weight - row["weight_pct"]) > 0.5:
            record["reason"] = "股數／現價與填入配置不一致，請核對後再計入組合風險"
            warnings.append(_warning("HOLDING_VALUE_MISMATCH", record["reason"], ticker=ticker))
            records.append(record)
            continue
        risk = (price - stop) * shares
        record.update({
            "status": "estimated", "risk_amount": round(risk, 2),
            "risk_pct_of_equity": round(risk / equity * 100, 4) if equity is not None else None,
        })
        records.append(record)
    known = [row for row in records if row["risk_amount"] is not None]
    complete = len(known) == len(records)
    amount = round(math.fsum(row["risk_amount"] for row in known), 2) if known else (0.0 if not positive else None)
    total = amount if complete else None
    return {
        "status": "complete" if complete else "incomplete", "positions": records,
        "known_position_count": len(known), "total_position_count": len(records),
        "known_risk_amount": amount, "total_risk_amount": total,
        "total_risk_pct": round(total / equity * 100, 4) if total is not None and equity is not None else None,
        "assumption": "僅估現價至停損價的未來價差風險；非最大虧損保證，未含跳空、滑價、稅費",
    }, warnings


def analyze_portfolio(
    holdings, close_history=None, *, equity=None, max_stock_weight_pct=25.0,
    max_sector_weight_pct=40.0, loss_tolerance_pct=10.0,
):
    """Analyze explicitly entered long-only allocation; unused weight is cash.

    Prices, share counts, and equity must use the same currency (TWD for Taiwan
    stocks). ``close_history`` is a date-indexed wide DataFrame, or a mapping of
    tickers to dated Series/dicts. No holdings or prices are fabricated.
    """
    cleaned = _clean_holdings(holdings)
    if equity is not None:
        equity = _finite_number(equity, "equity", minimum=0.00000001)
    stock_cap = _finite_number(max_stock_weight_pct, "max_stock_weight_pct", maximum=100.0)
    sector_cap = _finite_number(max_sector_weight_pct, "max_sector_weight_pct", maximum=100.0)
    tolerance = _finite_number(loss_tolerance_pct, "loss_tolerance_pct", maximum=100.0)
    stock_weight = math.fsum(row["weight_pct"] for row in cleaned)
    cash = max(0.0, 100.0 - stock_weight)
    warnings = []
    sectors: dict[str, float] = {}
    unknown = 0.0
    for row in cleaned:
        if row["weight_pct"] > stock_cap:
            warnings.append(_warning("STOCK_CONCENTRATION", "單一股票配置超過設定上限", ticker=row["ticker"], weight_pct=row["weight_pct"], limit_pct=stock_cap))
        if row["industry"] is None:
            unknown += row["weight_pct"]
        else:
            sectors[row["industry"]] = sectors.get(row["industry"], 0.0) + row["weight_pct"]
    for industry, weight in sectors.items():
        if weight > sector_cap:
            warnings.append(_warning("SECTOR_CONCENTRATION", "已知產業配置超過設定上限", industry=industry, weight_pct=weight, limit_pct=sector_cap))
    if unknown > 0:
        warnings.append(_warning("UNKNOWN_SECTOR_EXPOSURE", "部分持倉產業未知，不能判定產業集中風險已排除", unknown_weight_pct=round(unknown, 4)))
    correlations = _correlations(cleaned, close_history)
    if correlations["status"] == "incomplete":
        warnings.append(_warning("CORRELATION_INCOMPLETE", "部分持倉缺少至少 30 筆共同有效日報酬，相關性未知，不以 0 代替"))
    for pair in correlations["pairs"]:
        if pair["correlation"] is not None and pair["correlation"] >= 0.8:
            warnings.append(_warning("HIGH_HISTORICAL_CORRELATION", "歷史日報酬高度同向，股票數量不等於風險分散", left=pair["left"], right=pair["right"], correlation=pair["correlation"]))
    stress_loss = stock_weight * 0.20
    max_exposure = min(100.0, tolerance / 0.20)
    minimum_reduction = max(0.0, stock_weight - max_exposure)
    if stress_loss > tolerance:
        warnings.append(_warning("STRESS_EXCEEDS_TOLERANCE", "全體持股同跌 20% 的假設情境超過設定虧損容忍度", stress_loss_pct=round(stress_loss, 4), loss_tolerance_pct=tolerance))
    # A reduction-only illustration: never increase a holding, buy a substitute,
    # or assume leverage/derivative hedge effectiveness.
    targets = {row["ticker"]: min(row["weight_pct"], stock_cap) for row in cleaned}
    for industry in sectors:
        members = [row for row in cleaned if row["industry"] == industry]
        total = math.fsum(targets[row["ticker"]] for row in members)
        scale = min(1.0, sector_cap / total) if total else 1.0
        for row in members:
            targets[row["ticker"]] *= scale
    target_total = math.fsum(targets.values())
    scale = min(1.0, max_exposure / target_total) if target_total else 1.0
    targets = {ticker: weight * scale for ticker, weight in targets.items()}
    target_total = math.fsum(targets.values())
    stop_risk, stop_warnings = _stop_risk(cleaned, equity)
    warnings.extend(stop_warnings)
    return {
        "status": "ok", "source": "user_entered", "holdings": cleaned,
        "assumptions": {
            "equity": equity, "max_stock_weight_pct": stock_cap, "max_sector_weight_pct": sector_cap,
            "loss_tolerance_pct": tolerance, "long_only": True, "cash_return_pct": 0.0,
            "holdings_source": "僅使用手動輸入的真實持倉；不將榜單或模擬交易視為持倉",
            "not_included": "槓桿、放空、衍生品、融資利息、匯率、交易稅費與流動性衝擊未建模",
        },
        "total_stock_weight_pct": round(stock_weight, 4), "cash_weight_pct": round(cash, 4),
        "concentration": {
            "largest_stock_weight_pct": max((row["weight_pct"] for row in cleaned), default=0.0),
            "sectors": [{"industry": name, "weight_pct": round(weight, 4)} for name, weight in sorted(sectors.items())],
            "unknown_sector_weight_pct": round(unknown, 4),
            "sector_coverage_pct": round((stock_weight - unknown) / stock_weight * 100, 4) if stock_weight else 100.0,
            "sector_assessment_complete": unknown == 0,
        },
        "correlations": correlations,
        "stress_scenario": {
            "label": "假設所有多頭持股下跌 20%、現金報酬 0%；非價格預測",
            "stock_shock_pct": -20.0, "portfolio_return_pct": round(-stress_loss, 4),
            "estimated_loss_amount": round(equity * stress_loss / 100, 2) if equity is not None else None,
            "exceeds_tolerance": stress_loss > tolerance,
            "max_stock_exposure_for_tolerance_pct": round(max_exposure, 4),
            "minimum_stock_reduction_pct_points": round(minimum_reduction, 4),
        },
        "rebalance_proposal": {
            "mode": "reduction_only_illustration", "requires_user_confirmation": True,
            "orders_created": False, "sector_assessment_complete": unknown == 0,
            "holdings": [
                {"ticker": row["ticker"], "current_weight_pct": row["weight_pct"],
                 "illustrative_weight_pct": round(targets[row["ticker"]], 4),
                 "reduction_pct_points": round(row["weight_pct"] - targets[row["ticker"]], 4)}
                for row in cleaned
            ],
            "cash_weight_pct": round(100.0 - target_total, 4),
            "cash_increase_pct_points": round(stock_weight - target_total, 4),
            "scenario_return_pct": round(-target_total * 0.20, 4),
            "note": "僅示意降低超限曝險並增加現金，不自動交易、不推薦槓桿避險；未知產業仍需核對",
        },
        "stop_risk": stop_risk, "warnings": warnings,
    }


def build_daily_checklist(analysis_date, *, has_candidates=False):
    """Build a manual checklist for the supplied date, never a future session."""
    if isinstance(analysis_date, datetime):
        if analysis_date.tzinfo is not None:
            from zoneinfo import ZoneInfo
            analysis_date = analysis_date.astimezone(ZoneInfo("Asia/Taipei"))
        day = analysis_date.date()
    elif isinstance(analysis_date, date):
        day = analysis_date
    elif isinstance(analysis_date, str):
        try:
            day = date.fromisoformat(analysis_date)
        except ValueError as exc:
            raise ValueError("analysis_date 必須是 YYYY-MM-DD 日期") from exc
    else:
        raise ValueError("analysis_date 必須是 YYYY-MM-DD 日期")
    weekend = day.weekday() >= 5
    selection = (
        "候選尚未確認；先查看有效日期的研究名單，不假定已有或沒有候選"
        if has_candidates is None else
        "逐一確認候選是否仍符合入場區、停損與風險上限；榜單不是下單指令"
        if has_candidates else "目前無候選；保留現金，不為湊數放寬條件"
    )
    return {
        "analysis_date": day.isoformat(), "timezone": "Asia/Taipei", "mode": "manual_checklist",
        "trading_day_confirmed": False,
        "calendar_status": "weekend_unconfirmed" if weekend else "official_calendar_verification_required",
        "execution_allowed": False, "schedule_created": False, "orders_created": False,
        "items": [
            {"time": "08:30", "stage": "盤前", "checks": ["先查官方交易日曆與臨時休市公告，未確認交易日不執行", "確認行情、財報與新聞時間戳；缺資料標未知，不以 0 補值", selection]},
            {"time": "09:00", "stage": "開盤檢核", "checks": ["觀察真實成交與跳空，重新核對入場區與流動性", "超出預設價格或風險時暫停，不追價"]},
            {"time": "09:15", "stage": "盤中確認", "checks": ["以已完成的 K 棒確認量價與訊號；未收盤日 K 僅供即時觀察", "若擬交易，先人工確認股數、停損、成本與組合曝險"]},
            {"time": "11:30", "stage": "盤中複核", "checks": ["檢查持倉是否觸發既定失效條件；不任意放寬停損", "記錄已執行、未執行與錯失機會的客觀原因"]},
            {"time": "13:20", "stage": "收盤前", "checks": ["檢視未成交委託與隔夜曝險；如有委託，於券商端人工確認", "不因當日虧損追加無計畫部位"]},
            {"time": "13:30", "stage": "收盤檢核", "checks": ["等待官方盤後完整行情，不把盤中暫值視為最終收盤", "核對實際成交結果與原先計畫差異"]},
            {"time": "15:17", "stage": "盤後資料複核（自訂時間）", "checks": ["確認資料實際發布狀態；15:17 不代表所有來源已齊全", "保留現有每日榜單及績效圖，研究分析另列，不改入榜或結算規則", "以真實進出場記錄更新日誌；下次執行日期另依官方日曆確認"]},
        ],
        "warnings": [
            "此日期為週末，且未查驗特殊交易安排；僅供準備工作，不執行盤中步驟" if weekend else "尚未查驗官方交易日曆與臨時休市；須確認後才能套用當日檢核",
            "這是人工作業清單，不是自動下單，也未建立排程；不推算下一交易日",
        ],
    }
