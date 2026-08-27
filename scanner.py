# scanner.py - 雲端自動掃描機器人
import firebase_admin
from firebase_admin import credentials, firestore
import yfinance as yf
import pandas as pd
import concurrent.futures
import logging
import os
import argparse
import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timezone, timedelta
from typing import Any
import streamlit as st

# 引入共用核心演算法
from analysis_core import BACKTEST_LOOKBACK_DAYS, ENG_TO_TW_INDUSTRY, apply_technical_indicators, build_score_input, calculate_historical_performance
from app_security import normalize_ticker
from data_providers import fetch_institutional_rows, fetch_revenue_growth
from entry_readiness import build_entry_readiness
from market_http import call_with_backoff, http_get
from scan_state import (
    build_scan_quality,
    latest_trading_date,
    next_streak,
    previous_scan_state,
    scan_universe_limit,
    should_complete_candidate,
)
from scoring import get_decision_score
from top10_tracker import (
    backfill_entry_backtest_snapshots,
    build_top10_history_rows,
    update_positions_with_snapshots,
)
from top10_telegram import send_top10_photo

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def get_secret(name, default=""):
    try:
        if st.secrets and name in st.secrets:
            return st.secrets[name]
    except Exception:
        pass
    try:
        import tomllib
        secrets_path = os.path.join(".streamlit", "secrets.toml")
        if os.path.exists(secrets_path):
            with open(secrets_path, "rb") as f:
                secrets = tomllib.load(f)
                if name in secrets:
                    return secrets[name]
    except Exception:
        pass
    return os.getenv(name, default)


def init_firestore():
    try:
        firebase_admin.get_app()
        return firestore.client()
    except ValueError:
        try:
            firebase_secrets = get_secret("firebase")
            if not firebase_secrets:
                raise ValueError("無法讀取 firebase 金鑰設定")
            firebase_admin.initialize_app(credentials.Certificate(dict(firebase_secrets)))
            return firestore.client()
        except Exception as e:
            logging.error("Firebase 初始化失敗: %s", e)
            return None
    except Exception as e:
        logging.error("Firebase 初始化失敗: %s", e)
        return None


db = init_firestore()
FINMIND_TOKEN = get_secret("FINMIND_TOKEN")

# ENG_TO_TW_INDUSTRY 已移至 analysis_core.py 統一管理，此處直接 import

INDUSTRY_CACHE: dict[str, str] = {}
MARKET_SYMBOL_CACHE: dict[str, str] = {}
def build_industry_cache():
    global INDUSTRY_CACHE
    logging.info("📦 正在建立全市場產業快取字典...")
    try:
        res = http_get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", timeout=10)
        res.raise_for_status()
        if res.status_code == 200:
            for item in res.json(): INDUSTRY_CACHE[item['Code']] = item.get('Name', '')
    except Exception as e:
        logging.warning("上市名稱快取建立失敗: %s", e)
    try:
        res2 = http_get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes", timeout=10)
        res2.raise_for_status()
        if res2.status_code == 200:
            for item in res2.json(): INDUSTRY_CACHE[item['SecuritiesCompanyCode']] = item.get('CompanyName', '')
    except Exception as e:
        logging.warning("上櫃名稱快取建立失敗: %s", e)

def get_fundamental_and_industry_data(ticker_number, current_price=0):
    base_ticker = normalize_ticker(ticker_number)
    eps_val, ind = None, "一般產業"
    eps_period = "missing"
    source_ok = False
    info = {}
    try:
        info = call_with_backoff(lambda: yf.Ticker(f"{base_ticker}.TW").info, attempts=2)
        if not info or 'industry' not in info:
            info = call_with_backoff(lambda: yf.Ticker(f"{base_ticker}.TWO").info, attempts=2)
        source_ok = bool(info)
        raw_sector = info.get("sector", "")
        if raw_sector in ENG_TO_TW_INDUSTRY: ind = ENG_TO_TW_INDUSTRY[raw_sector]
        elif info.get("industry") in ENG_TO_TW_INDUSTRY: ind = ENG_TO_TW_INDUSTRY[info.get("industry")]
        if 'trailingEps' in info and info['trailingEps'] is not None:
            eps_val = str(round(info['trailingEps'], 2))
            eps_period = "ttm"
    except Exception as e:
        logging.debug("Yahoo 基本面資料取得不完整 %s: %s", base_ticker, e)
    if ind == "一般產業" or eps_val is None:
        try:
            response = http_get(f"https://ws.cnyes.com/twstock/api/v1/company/profile/{base_ticker}", timeout=5)
            response.raise_for_status()
            res_cnyes = response.json()
            if 'data' in res_cnyes and 'categoryName' in res_cnyes['data']: ind = res_cnyes['data']['categoryName']
            if eps_val is None and res_cnyes.get("data", {}).get("eps") is not None:
                eps_val = str(round(float(res_cnyes["data"]["eps"]), 2))
                eps_period = "provider"
            source_ok = source_ok or bool(res_cnyes.get("data"))
        except Exception as e:
            logging.debug("CNYES 基本面資料取得不完整 %s: %s", base_ticker, e)
    if eps_val is not None and ind != "一般產業":
        status = "ok"
    elif source_ok:
        status = "partial"
    else:
        status = "missing"
    return {"EPS": eps_val, "EPS_Period": eps_period, "Industry": ind, "_status": status}

def is_financial_stock(stock, industry=""):
    s = normalize_ticker(stock)
    ind = str(industry).strip()
    if s.startswith("28"):
        return True
    financial_keywords = ["金融", "銀行", "保險", "金控", "證券", "期貨", "Financial"]
    return any(k in ind for k in financial_keywords)

def get_finmind_revenue(ticker, with_status=False, with_meta=False):
    payload = fetch_revenue_growth(ticker, FINMIND_TOKEN)
    if with_meta:
        return payload
    result = (payload["mom"], payload["yoy"])
    return (*result, payload["status"]) if with_status else result

def fetch_top_stocks(limit=500):
    limit = max(1, min(1000, int(limit)))
    all_stocks = []
    global MARKET_SYMBOL_CACHE
    MARKET_SYMBOL_CACHE = {}
    logging.info("🔍 正在獲取上市與上櫃成交量排行...")
    try:
        res = http_get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", timeout=10)
        res.raise_for_status()
        df_twse = pd.DataFrame(res.json())
        df_twse['TradeVolume'] = pd.to_numeric(df_twse['TradeVolume'].astype(str).str.replace(',', '', regex=False), errors='coerce')
        df_twse['Symbol'] = df_twse['Code'].astype(str) + ".TW"
        all_stocks.append(df_twse[['Code', 'TradeVolume', 'Symbol']])
    except Exception as e:
        logging.warning("上市成交量名單取得失敗: %s", e)
    try:
        res2 = http_get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes", timeout=10)
        res2.raise_for_status()
        df_tpex = pd.DataFrame(res2.json())
        tpex_volume_column = "TradingShares" if "TradingShares" in df_tpex.columns else "TradingVolume"
        df_tpex = df_tpex.rename(columns={'SecuritiesCompanyCode': 'Code', tpex_volume_column: 'TradeVolume'})
        if 'TradeVolume' not in df_tpex.columns:
            raise ValueError("櫃買行情缺少成交量欄位")
        df_tpex['TradeVolume'] = pd.to_numeric(df_tpex['TradeVolume'].astype(str).str.replace(',', '', regex=False), errors='coerce')
        df_tpex['Symbol'] = df_tpex['Code'].astype(str) + ".TWO"
        all_stocks.append(df_tpex[['Code', 'TradeVolume', 'Symbol']])
    except Exception as e:
        logging.warning("上櫃成交量名單取得失敗: %s", e)

    if all_stocks:
        df_all = pd.concat(all_stocks, ignore_index=True)
        df_all['Code'] = df_all['Code'].astype(str)
        df_all = df_all[df_all['Code'].str.match(r'^\d{4}$')]
        ranked_all = df_all.sort_values(by='TradeVolume', ascending=False).drop_duplicates('Code', keep='first')
        MARKET_SYMBOL_CACHE.update(dict(zip(ranked_all['Code'], ranked_all['Symbol'])))
        ranked = ranked_all.head(limit)
        return ranked['Code'].tolist()
    return []


def fetch_top_500():
    """Compatibility wrapper for callers that still request the old fixed universe."""
    return fetch_top_stocks(500)


def build_scan_pool(ranked_tickers, limit, core_tickers=None):
    """Keep the configured universe size while guaranteeing core names are included."""
    limit = max(1, int(limit))
    raw_core = core_tickers
    if raw_core is None:
        raw_core = str(get_secret("CORE_TICKERS", "2330,2317,2454")).split(",")
    core = []
    for ticker in raw_core:
        normalized = normalize_ticker(ticker)
        if normalized and normalized not in core:
            core.append(normalized)

    pool = []
    for ticker in ranked_tickers:
        normalized = normalize_ticker(ticker)
        if normalized and normalized not in pool:
            pool.append(normalized)
        if len(pool) >= limit:
            break
    for ticker in core:
        if ticker in pool:
            continue
        if len(pool) >= limit:
            removable = next((idx for idx in range(len(pool) - 1, -1, -1) if pool[idx] not in core), None)
            if removable is None:
                continue
            pool.pop(removable)
        pool.append(ticker)
    return pool[:limit]

def get_stock_data(ticker_number):
    try:
        preferred_symbol = MARKET_SYMBOL_CACHE.get(str(ticker_number), f"{ticker_number}.TW")
        df = call_with_backoff(lambda: yf.Ticker(preferred_symbol).history(period="2y"), attempts=2).dropna(subset=['Close'])
        if df.empty and preferred_symbol.endswith(".TW"):
            df = call_with_backoff(lambda: yf.Ticker(f"{ticker_number}.TWO").history(period="2y"), attempts=2).dropna(subset=['Close'])
        if df.empty or len(df) < 20: return None
        
        df.index = pd.to_datetime(df.index.strftime('%Y-%m-%d'))
        df = df[~df.index.duplicated(keep='last')]
        return apply_technical_indicators(df)
    except Exception as e:
        logging.warning("股價資料處理失敗 %s: %s", ticker_number, e)
        return None


def fetch_stock_data_batch(tickers, chunk_size=50):
    """Fetch OHLCV in chunks; individual downloads remain the fallback path."""
    result = {}
    codes = [str(ticker) for ticker in tickers]
    for start in range(0, len(codes), chunk_size):
        chunk_codes = codes[start:start + chunk_size]
        symbols = [MARKET_SYMBOL_CACHE.get(code, f"{code}.TW") for code in chunk_codes]
        try:
            raw = call_with_backoff(lambda: yf.download(
                symbols, period="2y", group_by="ticker", auto_adjust=True,
                progress=False, threads=True,
            ), attempts=2)
        except Exception as e:
            logging.warning("批次行情下載失敗（%s...）: %s", chunk_codes[0], e)
            continue
        if raw is None or raw.empty:
            continue

        for code, symbol in zip(chunk_codes, symbols):
            try:
                if isinstance(raw.columns, pd.MultiIndex):
                    if symbol in raw.columns.get_level_values(0):
                        frame = raw[symbol].copy()
                    elif symbol in raw.columns.get_level_values(1):
                        frame = raw.xs(symbol, axis=1, level=1).copy()
                    else:
                        continue
                else:
                    frame = raw.copy()
                frame = frame.dropna(subset=['Close'])
                if len(frame) < 20:
                    continue
                frame.index = pd.to_datetime(frame.index.strftime('%Y-%m-%d'))
                frame = frame[~frame.index.duplicated(keep='last')]
                result[code] = apply_technical_indicators(frame)
            except Exception as e:
                logging.debug("批次行情解析失敗 %s: %s", code, e)
    logging.info("批次行情成功載入 %d/%d 檔。", len(result), len(codes))
    return result

# ⭐ 補上法人籌碼抓取功能
def get_institutional_trading(ticker, with_status=False):
    rows, status = fetch_institutional_rows(ticker, FINMIND_TOKEN)
    compact = [{
        "日期": str(row.get("date", ""))[-5:].replace("-", "/"),
        "外資(張)": int(row["foreign"]),
        "投信(張)": int(row["trust"]),
        "自營商(張)": int(row["dealer"]),
        "單日合計(張)": int(row["total"]),
        "_date": str(row.get("date", "")),
        "_source": row.get("source", ""),
    } for row in rows]
    return (compact, status) if with_status else compact

# ⭐ 補上歷史勝率簡易精算器
def calc_winrate(df_slice):
    return calculate_historical_performance(df_slice, 1.5, 1.0, lookback_days=BACKTEST_LOOKBACK_DAYS)

def should_run_postclose_scan(now_tpe=None):
    now_tpe = now_tpe or datetime.now(timezone(timedelta(hours=8)))
    if os.getenv("FORCE_SCAN") == "1":
        return True
    if now_tpe.weekday() >= 5:
        return False
    postclose_time = now_tpe.replace(hour=14, minute=30, second=0, microsecond=0)
    return now_tpe >= postclose_time


def _load_daily_scan_doc():
    if db is None:
        return {}
    try:
        snapshot = db.collection("market_data").document("daily_scan").get()
        return snapshot.to_dict() or {} if snapshot.exists else {}
    except Exception as e:
        logging.error("讀取既有掃描資料失敗: %s", e)
        raise RuntimeError("無法讀取既有 daily_scan，已中止以避免破壞排名與連續天數") from e


def _acquire_scan_lease(trading_date, force=False, lease_minutes=120):
    """Acquire a Firestore-backed lease so scheduled jobs cannot overlap."""
    if db is None:
        return True
    # Reuse one document instead of accumulating one lock document per day.
    lock_ref = db.collection("system_locks").document("daily_scan")
    now_utc = datetime.now(timezone.utc)
    expires_at = now_utc + timedelta(minutes=lease_minutes)

    try:
        transaction = db.transaction()

        @firestore.transactional
        def acquire(transaction):
            snapshot = lock_ref.get(transaction=transaction)
            if snapshot.exists:
                payload = snapshot.to_dict() or {}
                status = payload.get("status")
                previous_expiry = payload.get("expires_at")
                if isinstance(previous_expiry, datetime):
                    if previous_expiry.tzinfo is None:
                        previous_expiry = previous_expiry.replace(tzinfo=timezone.utc)
                    if status == "running" and previous_expiry > now_utc:
                        return False
                if status == "completed" and payload.get("trading_date") == trading_date and not force:
                    return False
            transaction.set(lock_ref, {
                "status": "running",
                "trading_date": trading_date,
                "started_at": firestore.SERVER_TIMESTAMP,
                "expires_at": expires_at,
            })
            return True

        return bool(acquire(transaction))
    except Exception as e:
        logging.error("取得掃描鎖失敗，為避免重複掃描本次中止: %s", e)
        raise RuntimeError("無法取得 Firestore 掃描鎖") from e


def _finish_scan_lease(trading_date, status, result_count=0, error=""):
    if db is None:
        return
    try:
        db.collection("system_locks").document("daily_scan").set({
            "status": status,
            "trading_date": trading_date,
            "result_count": int(result_count),
            "error": str(error)[:500],
            "finished_at": firestore.SERVER_TIMESTAMP,
        }, merge=True)
    except Exception as e:
        logging.error("更新掃描鎖狀態失敗: %s", e)


def _telegram_credentials() -> tuple[Any, Any]:
    nested = get_secret("telegram", {})
    nested = nested if isinstance(nested, Mapping) else {}
    token = (
        get_secret("TELEGRAM_BOT_TOKEN")
        or get_secret("TELEGRAM_TOKEN")
        or nested.get("bot_token")
        or nested.get("token")
    )
    chat_id = (
        get_secret("TELEGRAM_CHAT_ID")
        or get_secret("TELEGRAM_USER_ID")
        or nested.get("chat_id")
    )
    return token, chat_id


def _top10_notification_fingerprint(top10_results, trading_date):
    payload = {
        "date": str(trading_date),
        "rows": build_top10_history_rows(top10_results[:10]),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def send_daily_top10_notification(top10_results, trading_date, *, resend=False):
    """Send once per distinct daily ranking and persist delivery state in Firestore."""
    if db is None:
        raise RuntimeError("Firestore 未初始化，無法確認 Telegram 通知狀態")
    top10 = list(top10_results[:10])
    if not top10:
        raise RuntimeError("Top10 榜單為空，已取消 Telegram 通知")
    fingerprint = _top10_notification_fingerprint(top10, trading_date)
    notification_ref = db.collection("notifications").document(f"daily_top10_{trading_date}")
    previous = notification_ref.get()
    previous_data = previous.to_dict() or {} if previous.exists else {}
    if not resend and previous_data.get("status") == "sent" and previous_data.get("fingerprint") == fingerprint:
        logging.info("%s Top10 Telegram 圖片已發送，略過重複通知。", trading_date)
        return False

    token, chat_id = _telegram_credentials()
    message_id = send_top10_photo(top10, trading_date, token, chat_id)
    notification_ref.set({
        "date": trading_date,
        "status": "sent",
        "fingerprint": fingerprint,
        "message_id": message_id,
        "ranking_count": len(top10),
        "sent_at": firestore.SERVER_TIMESTAMP,
    }, merge=True)
    logging.info("✅ %s Top10 圖片已發送至 Telegram（message_id=%s）。", trading_date, message_id)
    return True


def update_top10_tracker(top10_results, trading_date=None):
    if db is None: return
    try:
        date_str = trading_date or datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d')
        tracker_ref = db.collection("market_data").document("top10_tracker")
        doc = tracker_ref.get()
        positions = []
        history_dates = []
        missing_ranking_dates = []
        partial_ranking_dates = []
        unverified_ranking_dates = []
        if doc.exists:
            data_field = doc.to_dict().get("data", {})
            # Fallback for old structure if necessary
            positions = data_field.get("positions", doc.to_dict().get("positions", []))
            history_dates = data_field.get("history_dates", [])
            missing_ranking_dates = data_field.get("missing_ranking_dates", [])
            partial_ranking_dates = data_field.get("partial_ranking_dates", [])
            unverified_ranking_dates = data_field.get("unverified_ranking_dates", [])

        missing_entry_dates = sorted({
            str(position.get("entry_date", ""))
            for position in positions
            if isinstance(position, dict)
            and position.get("entry_date")
            and position.get("entry_backtest_samples") is None
            and position.get("entry_backtest_status") != "missing"
        })
        entry_history_by_date = {}
        for entry_date in missing_entry_dates:
            try:
                history_doc = db.collection("top10_history").document(entry_date).get()
                if history_doc.exists:
                    history_data = (history_doc.to_dict() or {}).get("data", [])
                    if isinstance(history_data, list):
                        entry_history_by_date[entry_date] = history_data
            except Exception as history_error:
                logging.warning("讀取 %s Top10 入榜原始資料失敗，保留缺值: %s", entry_date, history_error)
        positions = backfill_entry_backtest_snapshots(positions, entry_history_by_date)
        
        top10_tickers = {str(row.get("代號", "")) for row in top10_results}
        quotes = {}
        for position in positions:
            ticker = str(position.get("ticker", ""))
            if position.get("status") != "OPEN" or ticker in top10_tickers:
                continue
            frame = get_stock_data(ticker)
            if frame is not None and not frame.empty:
                latest = frame.iloc[-1]
                quote_date = pd.Timestamp(frame.index[-1]).strftime("%Y-%m-%d")
                if quote_date != date_str:
                    logging.warning("追蹤行情日期不符 %s：預期 %s，取得 %s", ticker, date_str, quote_date)
                    continue
                quotes[ticker] = {
                    "Open": latest.get("Open"),
                    "High": latest.get("High"),
                    "Low": latest.get("Low"),
                    "Close": latest.get("Close"),
                }
        all_positions, daily_snapshots = update_positions_with_snapshots(
            positions, top10_results, quotes, date_str
        )
        history_dates = sorted({str(item) for item in history_dates if item} | {date_str}, reverse=True)[:120]
        action_counts: dict[str, int] = {}
        for snapshot in daily_snapshots:
            snapshot["ranking_status"] = "ok"
            action = str(snapshot.get("action", "UNKNOWN"))
            action_counts[action] = action_counts.get(action, 0) + 1
        remaining_missing_dates = sorted({
            str(item) for item in missing_ranking_dates if item and str(item) != date_str
        })
        remaining_partial_dates = sorted({
            str(item) for item in partial_ranking_dates if item and str(item) != date_str
        })
        remaining_unverified_dates = sorted({
            str(item) for item in unverified_ranking_dates if item and str(item) != date_str
        })
        has_historical_gaps = bool(remaining_missing_dates or remaining_partial_dates or remaining_unverified_dates)

        tracker_payload = {
            "positions": all_positions,
            "latest_date": date_str,
            "latest_snapshots": daily_snapshots,
            "history_dates": history_dates,
            "backfill_status": "partial" if has_historical_gaps else "complete",
            "missing_ranking_dates": remaining_missing_dates,
            "partial_ranking_dates": remaining_partial_dates,
            "unverified_ranking_dates": remaining_unverified_dates,
            "backfill_note": "歷史缺漏榜單未使用事後資料重算排名" if has_historical_gaps else "",
        }
        history_payload = {
            "date": date_str,
            "records": daily_snapshots,
            "ranking_status": "ok",
            "data_status": "ok",
            "missing_reason": "",
            "summary": {
                "tracked_count": len(daily_snapshots),
                "open_count": len([p for p in all_positions if p.get("status") == "OPEN"]),
                "actions": action_counts,
            },
        }
        history_ref = db.collection("top10_tracking_history").document(date_str)
        batch = db.batch()
        batch.set(tracker_ref, {"data": tracker_payload, "update_time": firestore.SERVER_TIMESTAMP})
        batch.set(history_ref, {"data": history_payload, "update_time": firestore.SERVER_TIMESTAMP})
        batch.commit()
        logging.info("自動追蹤紀錄已更新，目前未平倉檔數: %d", len([p for p in all_positions if p.get("status")=="OPEN"]))
    except Exception as e:
        logging.error("更新 top10_tracker 失敗: %s", e)
        raise

def run_daily_scan(force=False, *, allow_local=False, send_telegram=True, resend_telegram=False):
    force = bool(force or os.getenv("FORCE_SCAN") == "1")
    if db is None and not allow_local:
        raise RuntimeError("Firestore 初始化失敗；排程掃描已中止，避免 GitHub Actions 誤判成功")
    if not force and not should_run_postclose_scan():
        logging.info("尚未到台北時間 14:30 盤後掃描時間，本次略過。")
        return []

    twii_close, twii_ma20, twii_ma60 = 0.0, 0.0, 0.0
    twii_df = None
    try:
        twii_df = call_with_backoff(lambda: yf.Ticker("^TWII").history(period="4mo"), attempts=3)
        if not twii_df.empty and len(twii_df) >= 60:
            twii_df['MA20'] = twii_df['Close'].rolling(20).mean()
            twii_df['MA60'] = twii_df['Close'].rolling(60).mean()
            twii_close = float(twii_df['Close'].iloc[-1])
            twii_ma20 = float(twii_df['MA20'].iloc[-1])
            twii_ma60 = float(twii_df['MA60'].iloc[-1])
    except Exception as e:
        logging.error("雷達獲取大盤加權指數失敗: %s", e)

    scan_date_str = latest_trading_date(twii_df.index) if twii_df is not None and not twii_df.empty else ""
    if not scan_date_str or twii_close <= 0:
        logging.error("無法確認最新實際交易日，本次不寫入掃描結果。")
        return []

    previous_payload = _load_daily_scan_doc()
    if not _acquire_scan_lease(scan_date_str, force=force):
        if previous_payload.get("scan_date") == scan_date_str:
            logging.info("%s 已完成或正在掃描，直接沿用既有結果。", scan_date_str)
            if send_telegram:
                send_daily_top10_notification(
                    previous_payload.get("data", [])[:10],
                    scan_date_str,
                    resend=resend_telegram,
                )
            return previous_payload.get("data", [])
        logging.info("%s 掃描工作已由其他執行個體處理，本次略過。", scan_date_str)
        return []

    universe_limit = scan_universe_limit(scan_date_str, get_secret("SCAN_LIMIT", ""))
    scan_profile = "weekly_500" if universe_limit == 500 else f"daily_{universe_limit}"
    logging.info("🚀 開始執行 %s 雷達掃描（%s 檔，%s）...", scan_date_str, universe_limit, scan_profile)
    try:
        build_industry_cache()
        ranked_tickers = fetch_top_stocks(universe_limit)
        if len(ranked_tickers) < universe_limit:
            raise RuntimeError(
                f"成交量排行僅取得 {len(ranked_tickers)}/{universe_limit} 檔，已中止以避免產生不完整榜單"
            )
        pool = build_scan_pool(ranked_tickers, universe_limit)
        if len(pool) != universe_limit:
            raise RuntimeError(
                f"掃描池去重後僅 {len(pool)}/{universe_limit} 檔，已中止以避免產生不完整榜單"
            )
        price_data = fetch_stock_data_batch(pool)
    except Exception as e:
        _finish_scan_lease(scan_date_str, "failed", 0, str(e))
        raise
    scan_results = []
    previous_streaks, previous_ranks, same_day_rerun = previous_scan_state(previous_payload, scan_date_str)

    def process_stock(stock):
        df = price_data.get(stock)
        if df is None:
            df = get_stock_data(stock)
        if df is not None:
            t = df.iloc[-1]
            p = df.iloc[-2]
            t_close, t_open, t_high, t_low = t['Close'], t['Open'], t['High'], t['Low']
            p_close, p_open = p['Close'], p['Open']
            
            basic_tech_sc = t_close > t.get('20MA', t_close)
            if not basic_tech_sc and t.get('MACD_Hist', 0) < 0:
                return None

            f_data = get_fundamental_and_industry_data(stock, t_close)
            if is_financial_stock(stock, f_data.get('Industry', '')):
                return None
            revenue = get_finmind_revenue(stock, with_meta=True)
            mom, yoy, revenue_status = revenue["mom"], revenue["yoy"], revenue["status"]
            fund = {
                "EPS": f_data.get('EPS'),
                "EPS_Period": f_data.get('EPS_Period', 'missing'),
                "MoM": mom,
                "YoY": yoy,
                "TWII_Close": twii_close,
                "TWII_MA20": twii_ma20,
                "TWII_MA60": twii_ma60,
            }
            data = build_score_input(df, fund)
            initial_quality, initial_confidence = build_scan_quality({
                "price": "ok",
                "fundamental": f_data.get("_status", "unknown"),
                "revenue": revenue_status,
                "institutional": "pending",
                "market": "ok",
            })
            data["Data_Quality"] = initial_quality
            data["Confidence"] = initial_confidence
            initial_score, _, _, _ = get_decision_score(data, fund, mode="post", with_reason=False)

            has_buy_pattern = data.get("Advanced_Pattern_Signal") == "Buy"
            # 法人最高 +6 分，pending→完整的信心修正最高再 +3 分。
            if should_complete_candidate(initial_score, data.get("Advanced_Pattern_Signal", "")):
                inst, inst_status = get_institutional_trading(stock, with_status=True)
                whale_days = min(3, len(inst))
                whale_net = sum([int(str(x['單日合計(張)']).replace(',', '')) for x in inst[:whale_days]]) if inst else None
                quality, confidence = build_scan_quality({
                    "price": "ok",
                    "fundamental": f_data.get("_status", "unknown"),
                    "revenue": revenue_status,
                    "institutional": inst_status,
                    "market": "ok",
                }, institutional_days=len(inst))
                data["Whale_Net"] = whale_net
                data["Data_Quality"] = quality
                data["Confidence"] = confidence
                sc, label, rs, feature = get_decision_score(data, fund, mode="post", with_reason=True)
                if sc <= 0:
                    return None
                if sc < 45 and not has_buy_pattern:
                    return None

                backtest = calc_winrate(df)
                data.update({
                    "Score": sc,
                    "最高價": float(t_high),
                    "最低價": float(t_low),
                    "ATR": float(t.get("ATR", 0)),
                })
                entry_plan = build_entry_readiness(data)
                result = {
                    "代號": stock, "名稱": INDUSTRY_CACHE.get(stock, stock),
                    "Data_Date": scan_date_str,
                    "Score": sc, "評級": label, "產業": f_data['Industry'], 
                    "開盤價": round(t_open, 2), "最高價": round(t_high, 2), "最低價": round(t_low, 2),
                    "收盤價": round(t_close, 2), "WinRate": backtest["win_rate"], "Whale_Net": whale_net,
                    "Whale_Net_Days": whale_days,
                    "漲跌幅": round((t_close - p_close)/p_close*100, 2),
                    "Feature": feature, "Reasons": rs, "Backtest_Samples": backtest["closed_signals"],
                    "Backtest_Scope": backtest["backtest_scope"],
                    "Validation_WinRate": backtest["validation_win_rate"],
                    "Validation_Samples": backtest["validation_samples"],
                    "EPS": fund['EPS'], "EPS_Period": fund['EPS_Period'],
                    "MoM": fund['MoM'], "YoY": fund['YoY'],
                    "Revenue_Period": revenue.get("period", ""),
                    "Revenue_Source": revenue.get("source", ""),
                    "Advanced_Pattern": data.get("Advanced_Pattern", ""),
                    "Advanced_Pattern_Signal": data.get("Advanced_Pattern_Signal", ""),
                    "Confidence": confidence, "Data_Quality": quality, "Institutional_Days": len(inst),
                    "Institutional_Status": inst_status,
                    "Institutional_Source": inst[0].get("_source", "") if inst else "",
                    "Institutional_Rows": [{
                        "date": row.get("_date", ""),
                        "foreign": row.get("外資(張)"),
                        "trust": row.get("投信(張)"),
                        "dealer": row.get("自營商(張)"),
                        "total": row.get("單日合計(張)"),
                        "source": row.get("_source", ""),
                    } for row in inst[:5]],
                    "Score_Mode": "盤後正式分數", "Score_Mode_Raw": "post", "Score_Source": "盤後規則計分",
                    "RRR": 1.5, "RRR_Source": "strategy_default",
                    "20MA": round(float(data.get("20MA", 0)), 2),
                    "BB_UP": round(float(data.get("BB_UP", 0)), 2),
                    "RSI": round(float(data.get("RSI", 0)), 1),
                    "BIAS": round(float(data.get("BIAS", 0)), 2),
                    "ATR": round(float(data.get("ATR", 0)), 2),
                    "Entry_Pattern": data.get("Entry_Pattern", ""),
                    "Signal_Conflict": data.get("Signal_Conflict", ""),
                    "Est_Vol_Ratio": data.get("Est_Vol_Ratio"),
                    "Volume_Confirmed": bool(data.get("Volume_Confirmed")),
                    "Tomorrow_Plan": data.get("Tomorrow_Plan", {}),
                    "Streak": next_streak(stock, previous_streaks, same_day_rerun),
                    "Prev_Rank": previous_ranks.get(stock, 999),
                }
                result.update(entry_plan)
                return result
        return None

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            for res in executor.map(process_stock, pool):
                if res: scan_results.append(res)

        scan_results = sorted(scan_results, key=lambda x: (x['Score'], x['漲跌幅']), reverse=True)

        for idx, res in enumerate(scan_results):
            curr_rank = idx + 1
            res["Rank"] = curr_rank
            res["Rank_Diff"] = res["Prev_Rank"] - curr_rank if res["Prev_Rank"] != 999 else "NEW"

        if not scan_results:
            raise RuntimeError("掃描結果為空，保留既有 daily_scan，避免以空資料覆寫")

        if db is None:
            logging.warning("allow_local=True：Firestore 未初始化，僅回傳本機掃描結果。")
            return scan_results

        db.collection("market_data").document("daily_scan").set({
            "data": scan_results,
            "scan_date": scan_date_str,
            "scan_limit": universe_limit,
            "universe_size": len(pool),
            "scan_profile": scan_profile,
            "update_time": firestore.SERVER_TIMESTAMP
        })

        top10 = scan_results[:10]
        history_data = build_top10_history_rows(top10)
        db.collection("top10_history").document(scan_date_str).set({
            "data": history_data,
            "scan_date": scan_date_str,
            "scan_limit": universe_limit,
            "scan_profile": scan_profile,
            "update_time": firestore.SERVER_TIMESTAMP,
        })
        logging.info("已記錄 %s 前十名完整榜單", scan_date_str)
        update_top10_tracker(top10, scan_date_str)
    except Exception as e:
        _finish_scan_lease(scan_date_str, "failed", len(scan_results), str(e))
        logging.exception("全市場掃描失敗: %s", e)
        raise

    _finish_scan_lease(scan_date_str, "completed", len(scan_results))
    if send_telegram:
        send_daily_top10_notification(top10, scan_date_str, resend=resend_telegram)
    logging.info(f"✅ 掃描完成！共篩選出 {len(scan_results)} 檔標的。")
    return scan_results

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Taiwan stock post-close scanner")
    parser.add_argument("--allow-local", action="store_true", help="允許無 Firestore 僅輸出本機結果")
    parser.add_argument("--skip-telegram", action="store_true", help="維護時略過 Telegram Top10 圖片")
    parser.add_argument("--resend-telegram", action="store_true", help="即使榜單內容相同仍重新發送圖片")
    args = parser.parse_args()
    run_daily_scan(
        allow_local=args.allow_local,
        send_telegram=not args.skip_telegram,
        resend_telegram=args.resend_telegram,
    )
