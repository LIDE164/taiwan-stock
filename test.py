# 最後修改時間: 2026-07-07 (修復群組顯示、分數同步、雷達清單與營收籌碼回補防呆版)
import firebase_admin
from firebase_admin import credentials, firestore
import yfinance as yf
import streamlit as st
import pandas as pd
import requests
import time
import os
from datetime import datetime, timezone, timedelta
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import re
import concurrent.futures
import numpy as np
import logging
from streamlit_autorefresh import st_autorefresh

# 引入自訂繪圖函式與共用大腦核心演算法
from analysis_core import BACKTEST_LOOKBACK_DAYS, ENG_TO_TW_INDUSTRY, apply_technical_indicators, calculate_historical_performance, calculate_historical_winrate
from charts import draw_professional_chart
from scoring import get_decision_score
try:
    from ui_components import (
        credibility_label,
        generate_cards_html as build_cards_html,
        render_app_style,
        render_home_side_panel,
        render_market_status_cards,
        render_metric_grid,
        render_stock_hero,
    )
except Exception as ui_import_error:
    def credibility_label(sample_count):
        try:
            n = int(sample_count)
        except (TypeError, ValueError):
            return "--", "#94A3B8"
        if n < 10:
            return "樣本嚴重不足", "#EF4444"
        if n < 30:
            return "僅供參考", "#FACC15"
        if n < 50:
            return "中等可信", "#60A5FA"
        return "統計較穩定", "#22C55E"

    def render_app_style(is_light_mode=False):
        app_bg = "#f4f6f9" if is_light_mode else "#0b1120"
        st.markdown(f"<style>.stApp {{ background-color:{app_bg}; }} a.stock-card-link {{ text-decoration:none; color:inherit; display:block; }}</style>", unsafe_allow_html=True)
        st.caption(f"UI 模組載入失敗，已使用內建備援版：{ui_import_error}")

    def render_market_status_cards(items):
        cols = st.columns(len(items))
        for col, item in zip(cols, items):
            with col:
                st.metric(item.get("label", ""), item.get("value", "--"), item.get("sub", ""))

    def render_home_side_panel(title, rows, empty_text="暫無資料"):
        st.markdown(f"**{title}**")
        if not rows:
            st.caption(empty_text)
        for row in rows[:6]:
            st.write(f"{row.get('title', '')}  {row.get('value', '')}")
            st.caption(row.get("sub", ""))

    def render_stock_hero(data, target, name, strategy_text):
        st.markdown(f"## {target} {name}")
        st.caption(f"{data.get('產業', '一般產業')}｜{data.get('Score_Mode', '盤後正式分數')}｜資料信心 {data.get('Confidence', 100)}%")
        st.metric("現價", data.get("收盤價", "--"), f"{data.get('漲跌幅', 0):+.2f}%")
        st.info(f"建議策略：{strategy_text}")

    def render_metric_grid(metrics):
        cols = st.columns(len(metrics))
        for col, metric in zip(cols, metrics):
            with col:
                st.metric(metric.get("label", ""), metric.get("value", "--"), metric.get("sub", ""))

    def build_cards_html(df_disp, **kwargs):
        html = ""
        for _, row in df_disp.iterrows():
            code = row.get("代號", "")
            name = row.get("名稱", "")
            score = row.get("Score", 0)
            html += f"<a href='/?stock={code}' class='stock-card-link'><div style='background:#0F172A; border:1px solid #1E293B; border-radius:10px; padding:14px; margin-bottom:10px; color:#E2E8F0;'><b>{code} {name}</b><span style='float:right; color:#EF4444; font-weight:900;'>{score}分</span><br><span style='color:#94A3B8;'>歷史勝率 {row.get('WinRate', '--')}%｜樣本 {row.get('Backtest_Samples', '--')}</span></div></a>"
        return html

logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(levelname)s - %(message)s')

def get_secret(name, default=""):
    try:
        value = st.secrets.get(name, "")
    except Exception:
        value = ""
    return value or os.getenv(name, default)


FINMIND_TOKEN = get_secret("FINMIND_TOKEN")
FUGLE_API_KEY = get_secret("FUGLE_API_KEY")
LIVE_SCORE_CACHE_SECONDS = 30
POST_ANALYSIS_CACHE_SECONDS = 21600
DEFAULT_RADAR_TICKERS = ["2330", "2317", "2454", "2308", "2382", "3231", "2891", "6176", "3094"]
LOW_FIREBASE_READ_MODE = True
CLOUD_READ_TTL_SECONDS = {
    "market_data/daily_scan": 21600,
    "user_settings/fav_groups": 600,
    "user_data/simulated_orders": 600,
}

st.set_page_config(page_title="專業交易雷達", layout="wide", initial_sidebar_state="collapsed")

st.markdown('''
<head>
    <link rel="manifest" href="/manifest.json">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
</head>
''', unsafe_allow_html=True)

st.sidebar.title("⚙️ 介面設定")
is_light_mode = st.sidebar.toggle("🌞 黑白底色切換", False, key="toggle_theme_mode")
if LOW_FIREBASE_READ_MODE:
    st.sidebar.caption("Firebase 低讀取模式：開啟")

if st.sidebar.button("🗑️ 強制清除快取資料", use_container_width=True):
    st.cache_data.clear()
    if "scan_results" in st.session_state: del st.session_state["scan_results"]
    if "scan_results_is_local" in st.session_state: del st.session_state["scan_results_is_local"]
    if "_cloud_doc_cache" in st.session_state: del st.session_state["_cloud_doc_cache"]
    if "_analysis_session_cache" in st.session_state: del st.session_state["_analysis_session_cache"]
    st.sidebar.success("已清除暫存，請重整網頁！")

render_app_style(is_light_mode)

STOCK_NAMES = { "2330": "台積電", "2317": "鴻海", "2454": "聯發科", "2308": "台達電", "2382": "廣達", "3231": "緯創", "2891": "中信金"}

@st.cache_data(ttl=86400)
def get_all_tw_stock_names_v3():
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    names = STOCK_NAMES.copy()
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", timeout=10, verify=False)
        if res.status_code == 200:
            for i in res.json(): names[i['Code']] = i['Name']
    except: pass
    try:
        res2 = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes", timeout=10, verify=False)
        if res2.status_code == 200:
            for i in res2.json(): names[i['SecuritiesCompanyCode']] = i['CompanyName']
    except: pass
    return names

CURRENT_STOCK_NAMES = get_all_tw_stock_names_v3()

def get_stock_name(ticker):
    ticker_str = str(ticker).strip().upper().replace(".TW", "").replace(".TWO", "")
    return CURRENT_STOCK_NAMES.get(ticker_str, ticker_str)

def normalize_ticker(ticker):
    return str(ticker).strip().upper().replace(".TW", "").replace(".TWO", "")

def is_realtime_score_record(record):
    if not isinstance(record, dict):
        return False
    text = " ".join(str(record.get(k, "")) for k in ["Score_Mode_Raw", "Score_Mode", "Score_Source"])
    return "realtime" in text.lower() or "盤中" in text

def safe_num(value, default=0.0):
    try:
        if value is None or pd.isna(value):
            return default
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return default

def get_favorite_stock_set():
    favs = set()
    for stocks in st.session_state.get('fav_groups', {}).values():
        favs.update(normalize_ticker(s) for s in stocks)
    return favs

def get_simulated_order_stock_set():
    return {normalize_ticker(o.get("ticker", "")) for o in st.session_state.get("simulated_orders", []) if o.get("ticker")}

def get_market_progress(now_tpe=None):
    now_tpe = now_tpe or datetime.now(timezone(timedelta(hours=8)))
    start = now_tpe.replace(hour=9, minute=0, second=0, microsecond=0)
    end = now_tpe.replace(hour=13, minute=30, second=0, microsecond=0)
    if now_tpe <= start:
        return 0.0
    if now_tpe >= end:
        return 1.0
    return max(0.0, min(1.0, (now_tpe - start).total_seconds() / (end - start).total_seconds()))

def get_market_state(now_tpe=None):
    now_tpe = now_tpe or datetime.now(timezone(timedelta(hours=8)))
    if now_tpe.weekday() >= 5:
        return "holiday"
    preopen = now_tpe.replace(hour=8, minute=30, second=0, microsecond=0)
    start = now_tpe.replace(hour=9, minute=0, second=0, microsecond=0)
    end = now_tpe.replace(hour=13, minute=30, second=0, microsecond=0)
    if now_tpe < preopen:
        return "closed"
    if now_tpe < start:
        return "preopen"
    if now_tpe <= end:
        return "open"
    return "closed"

def is_regular_market_open(now_tpe=None):
    return get_market_state(now_tpe) == "open"

def resolve_score_mode(request_intraday=False):
    market_state = get_market_state()
    if request_intraday and market_state == "open":
        return "realtime", "盤中參考分數", True
    return "post", "盤後正式分數", False

def build_data_quality(price_status="ok", volume_status="ok", institutional_days=0, revenue_status="ok", macro_status=None, txf_status="ok"):
    macro_status = macro_status or {}
    quality = {
        "price": price_status,
        "volume": volume_status,
        "institutional": f"{institutional_days}日" if institutional_days else "cached_or_missing",
        "revenue": revenue_status,
        "macro": "ok" if macro_status and all(v == "ok" for v in macro_status.values()) else "partial",
        "txf": txf_status,
    }
    missing_count = 0
    for status in [price_status, volume_status, revenue_status, txf_status]:
        if status not in ("ok", "realtime", "confirmed", "estimated"):
            missing_count += 1
    missing_count += sum(1 for v in macro_status.values() if v != "ok")
    if institutional_days == 0:
        missing_count += 1
    confidence = max(20, 100 - missing_count * 12)
    return quality, confidence

def adjust_intraday_volume(volume, avg_volume_5d, is_intraday=False):
    volume = safe_num(volume)
    avg_volume_5d = safe_num(avg_volume_5d)
    progress = get_market_progress()
    if not is_intraday or avg_volume_5d <= 0:
        return volume, volume / avg_volume_5d if avg_volume_5d > 0 else 1.0, True
    projected = volume / max(progress, 0.18)
    confirmed = progress >= 0.82 or volume >= avg_volume_5d * 1.1
    effective_volume = volume if confirmed else min(projected, avg_volume_5d * 1.05)
    return effective_volume, effective_volume / avg_volume_5d, confirmed

def render_sidebar_favorites(container):
    link_color = "#333" if is_light_mode else "#e2e8f0"
    with container.container():
        st.title("⭐ 我的自選群組")
        fav_groups = st.session_state.get('fav_groups', {})
        if fav_groups:
            for g_name, g_stocks in fav_groups.items():
                stocks = [normalize_ticker(s) for s in g_stocks]
                with st.expander(f"📁 {g_name} ({len(stocks)} 檔)"):
                    for s in stocks:
                        s_name = get_stock_name(s)
                        st.markdown(f"- <a href='/?stock={s}' target='_self' style='text-decoration:none; color:{link_color}; font-weight:bold;'>{s} {s_name}</a>", unsafe_allow_html=True)
        else:
            st.info("尚未加入任何標的")

st.sidebar.title("🔍 快速搜尋")
with st.sidebar.form(key="search_form"):
    search_input = st.text_input("隱藏", placeholder="輸入股票代號或中文名稱...", label_visibility="collapsed")
    submit_search = st.form_submit_button("送出搜尋", use_container_width=True)
    
if submit_search and search_input:
    s_val = search_input.strip().replace(" ", "")
    target_ticker = None
    if re.match(r'^[A-Za-z0-9]+$', s_val): target_ticker = s_val.upper()
    else:
        for code, name in CURRENT_STOCK_NAMES.items():
            if s_val in name: target_ticker = code; break
    if target_ticker:
        st.session_state.current_stock = target_ticker
        st.session_state.page = "analysis"
        st.session_state.date_offset = 0
        st.rerun() 

st.sidebar.divider()
st.sidebar.title("⏱️ 盤中即時跳動")
_market_open_now = is_regular_market_open()
if _market_open_now:
    auto_refresh = st.sidebar.toggle("🟢 開啟自動更新 (每30秒)", False, key="auto_refresh_toggle")
    if auto_refresh: st_autorefresh(interval=30000, limit=None)
else:
    st.sidebar.caption("🔴 非交易時段，無需自動刷新")
    auto_refresh = False

st.sidebar.divider()
st.sidebar.title("🛒 模擬交易中心")
if st.sidebar.button("📋 經理人績效儀表板", use_container_width=True):
    st.session_state.page = "simulated_orders"; st.rerun()

st.sidebar.divider()
fav_sidebar_slot = st.sidebar.empty()

if not firebase_admin._apps:
    try:
        cert_dict = dict(st.secrets["firebase"])
        cred = credentials.Certificate(cert_dict)
        firebase_admin.initialize_app(cred)
    except Exception as e:
        st.session_state.cloud_last_error = f"Firebase 初始化失敗：{e}"
        logging.error(f"Firebase 初始化失敗: {e}")

try:
    db = firestore.client()
except Exception as e:
    db = None
    st.session_state.cloud_last_error = f"Firestore client 建立失敗：{e}"

def load_cloud_data(collection_name, document_name, default_data):
    target = f"{collection_name}/{document_name}"
    cache_key = f"{collection_name}:{document_name}"
    now_ts = time.time()
    if "_cloud_doc_cache" not in st.session_state:
        st.session_state._cloud_doc_cache = {}
    ttl = CLOUD_READ_TTL_SECONDS.get(target, 300)
    cached_entry = st.session_state._cloud_doc_cache.get(cache_key)
    if LOW_FIREBASE_READ_MODE and cached_entry and now_ts - cached_entry.get("ts", 0) <= ttl:
        return cached_entry.get("value", default_data)
    if db is None:
        if collection_name == "market_data":
            st.session_state.cloud_last_error = "Firebase 未初始化，無法讀取雲端掃描名單"
        return default_data
    try:
        doc = db.collection(collection_name).document(document_name).get()
        if not doc.exists:
            if collection_name == "market_data":
                st.session_state.cloud_last_error = f"{target} 文件不存在"
            st.session_state._cloud_doc_cache[cache_key] = {"value": default_data, "ts": now_ts}
            return default_data
        value = doc.to_dict().get('data', default_data)
        st.session_state._cloud_doc_cache[cache_key] = {"value": value, "ts": now_ts}
        if collection_name == "market_data":
            if isinstance(value, list) and len(value) == 0:
                st.session_state.cloud_last_error = f"{target} 的 data 欄位是空清單"
            else:
                st.session_state.cloud_last_error = ""
        return value
    except Exception as e:
        if collection_name == "market_data":
            st.session_state.cloud_last_error = f"讀取 {target} 失敗：{e}"
        st.session_state._cloud_doc_cache[cache_key] = {"value": default_data, "ts": now_ts}
    return default_data

def save_cloud_data(collection_name, document_name, data):
    cache_key = f"{collection_name}:{document_name}"
    if "_cloud_doc_cache" not in st.session_state:
        st.session_state._cloud_doc_cache = {}
    st.session_state._cloud_doc_cache[cache_key] = {"value": data, "ts": time.time()}
    if db is None: return
    try: db.collection(collection_name).document(document_name).set({'data': data})
    except: pass

def load_analysis_cache(ticker, max_age_seconds=900):
    cache_key = normalize_ticker(ticker)
    if "_analysis_session_cache" not in st.session_state:
        st.session_state._analysis_session_cache = {}
    local_cached = st.session_state._analysis_session_cache.get(cache_key)
    if isinstance(local_cached, dict):
        try:
            saved_at = datetime.fromisoformat(local_cached.get("saved_at", ""))
            if saved_at.tzinfo is None:
                saved_at = saved_at.replace(tzinfo=timezone(timedelta(hours=8)))
            age = (datetime.now(timezone(timedelta(hours=8))) - saved_at).total_seconds()
            if age <= max_age_seconds:
                return local_cached
        except Exception:
            pass
    if LOW_FIREBASE_READ_MODE:
        return None
    cached = load_cloud_data("analysis_cache", cache_key, None)
    if not isinstance(cached, dict):
        return None
    try:
        saved_at = datetime.fromisoformat(cached.get("saved_at", ""))
        if saved_at.tzinfo is None:
            saved_at = saved_at.replace(tzinfo=timezone(timedelta(hours=8)))
        age = (datetime.now(timezone(timedelta(hours=8))) - saved_at).total_seconds()
        if age <= max_age_seconds:
            return cached
    except Exception:
        return None
    return None

def save_analysis_cache(ticker, payload):
    if not isinstance(payload, dict):
        return
    compact = dict(payload)
    compact["saved_at"] = datetime.now(timezone(timedelta(hours=8))).isoformat()
    if "_analysis_session_cache" not in st.session_state:
        st.session_state._analysis_session_cache = {}
    st.session_state._analysis_session_cache[normalize_ticker(ticker)] = compact
    if LOW_FIREBASE_READ_MODE or db is None:
        return
    save_cloud_data("analysis_cache", normalize_ticker(ticker), compact)

def hydrate_scan_results(force=False):
    if force or "scan_results" not in st.session_state or not st.session_state.scan_results:
        data = load_cloud_data("market_data", "daily_scan", [])
        st.session_state.scan_results = data if isinstance(data, list) else []
    return st.session_state.get("scan_results", [])

def restore_nav_pool(min_score=60):
    records = hydrate_scan_results()
    if not records:
        return []
    valid_results = [x for x in records if x.get('Score', 0) >= min_score]
    if not valid_results:
        valid_results = records
    df_nav = pd.DataFrame(valid_results)
    if df_nav.empty or '代號' not in df_nav.columns:
        return []
    sort_cols = [c for c in ['Score', '漲跌幅'] if c in df_nav.columns]
    if sort_cols:
        df_nav = df_nav.sort_values(by=sort_cols, ascending=[False] * len(sort_cols))
    df_nav = df_nav.head(100).copy()
    df_nav['代號'] = df_nav['代號'].astype(str).map(normalize_ticker)
    st.session_state.nav_pool_data = df_nav.to_dict('records')
    st.session_state.nav_pool = df_nav['代號'].tolist()
    return st.session_state.nav_pool_data

def get_radar_targets(records=None, limit=200):
    targets = []
    records = records or []
    for row in records[:limit]:
        code = normalize_ticker(row.get("代號", ""))
        if code:
            targets.append(code)
    targets.extend(st.session_state.get("custom_pool", []))
    targets.extend(get_favorite_stock_set())
    targets.extend(DEFAULT_RADAR_TICKERS)
    seen, unique = set(), []
    for ticker in targets:
        code = normalize_ticker(ticker)
        if code and code not in seen:
            seen.add(code)
            unique.append(code)
    return unique

if 'page' not in st.session_state: st.session_state.page = "home"
if 'current_stock' not in st.session_state: st.session_state.current_stock = "2330"
if 'view_days' not in st.session_state: st.session_state.view_days = 30
if 'date_offset' not in st.session_state: st.session_state.date_offset = 0
if 'custom_pool' not in st.session_state: st.session_state.custom_pool = ["2330", "2317", "2454", "2382", "3231", "2891"]

if 'simulated_orders' not in st.session_state:
    st.session_state.simulated_orders = load_cloud_data("user_data", "simulated_orders", [])
if 'fav_groups' not in st.session_state:
    st.session_state.fav_groups = load_cloud_data("user_settings", "fav_groups", {"預設群組": ["1802", "2330", "1785"]})
st.session_state.fav_groups = {
    name: [normalize_ticker(s) for s in stocks]
    for name, stocks in st.session_state.fav_groups.items()
}
render_sidebar_favorites(fav_sidebar_slot)

if 'stock' in st.query_params:
    q_stock = normalize_ticker(st.query_params['stock'])
    q_mode = str(st.query_params.get('mode', '')).lower()
    if q_mode in ("intraday", "realtime"):
        _, q_score_mode_label, q_is_intraday = resolve_score_mode(True)
        st.session_state.is_intraday = q_is_intraday
        st.session_state.score_mode_label = q_score_mode_label
    if st.session_state.get('last_q_stock') != q_stock:
        st.session_state.date_offset = 0
    st.session_state.current_stock = q_stock
    st.session_state.page = "analysis"
    st.session_state.last_q_stock = q_stock

# ENG_TO_TW_INDUSTRY 已統一定義於 analysis_core.py，此處不再重複定義

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_twse_index_history():
    try:
        df = yf.Ticker("^TWII").history(period="1y")
        if not df.empty:
            df.index = pd.to_datetime(df.index.strftime('%Y-%m-%d'))
            df = df[~df.index.duplicated(keep='last')]
            return df[['Open', 'High', 'Low', 'Close', 'Volume']]
    except: return None


@st.cache_data(ttl=3600, show_spinner=False)
def _get_ohlcv_base(ticker_number):
    """Layer 1: Fetch and cache raw OHLCV from yfinance (slow, cache 1hr)."""
    base_ticker = str(ticker_number).strip().upper().replace(".TW", "").replace(".TWO", "")
    def fetch_clean(sym):
        try:
            d = yf.Ticker(sym).history(period="1y").dropna(subset=['Close'])
            if len(d) >= 20:
                d.index = pd.to_datetime(d.index.strftime('%Y-%m-%d'))
                d = d[~d.index.duplicated(keep='last')]
                return d
        except: return None
    if base_ticker == "^TWII":
        return fetch_twse_index_history()
    df = fetch_clean(f"{base_ticker}.TW")
    if df is None: df = fetch_clean(f"{base_ticker}.TWO")
    return df


@st.cache_data(ttl=60, show_spinner=False)
def get_stock_data(ticker_number):
    """Layer 2: Apply indicators & merge intraday quote (fast, cache 60s)."""
    base_ticker = str(ticker_number).strip().upper().replace(".TW", "").replace(".TWO", "")
    base_df = _get_ohlcv_base(ticker_number)
    if base_df is None: return None
    df = base_df.copy()  # 必須 copy，避免修改快取的唯讀 DataFrame
    
    try:
        market_state = get_market_state()
        if base_ticker != "^TWII" and market_state == "open" and FUGLE_API_KEY:
            url = f"https://api.fugle.tw/marketdata/v1.0/stock/intraday/quote/{base_ticker}"
            res = requests.get(url, headers={'X-API-KEY': FUGLE_API_KEY}, timeout=3)
            if res.status_code == 200:
                q = res.json()
                c_price = float(q.get('closePrice', q.get('lastPrice', df['Close'].iloc[-1])))
                now_tpe = datetime.now(timezone(timedelta(hours=8)))
                total = q.get('total', {}) or {}
                live_volume = float(total.get('tradeVolume', 0) or 0)
                live_value = float(total.get('tradeValue', total.get('tradeValueAmount', 0)) or 0)
                real_vwap = live_value / live_volume if live_volume > 0 and live_value > 0 else 0
                dt_live = pd.to_datetime(now_tpe.strftime('%Y-%m-%d'))
                if dt_live not in df.index:
                    new_row = pd.DataFrame({'Open': [float(q.get('openPrice', c_price))], 'High': [float(q.get('highPrice', c_price))], 'Low': [float(q.get('lowPrice', c_price))], 'Close': [c_price], 'Volume': [live_volume]}, index=[dt_live])
                    if 0 < real_vwap < c_price * 2:
                        new_row['VWAP'] = real_vwap
                    df = pd.concat([df, new_row])
                else:
                    df.loc[dt_live, 'Close'] = c_price
                    df.loc[dt_live, 'High'] = max(float(df.loc[dt_live, 'High']), float(q.get('highPrice', c_price)))
                    df.loc[dt_live, 'Low'] = min(float(df.loc[dt_live, 'Low']), float(q.get('lowPrice', c_price)))
                    df.loc[dt_live, 'Volume'] = max(float(df.loc[dt_live, 'Volume']), live_volume)
                    if 0 < real_vwap < c_price * 2:
                        df.loc[dt_live, 'VWAP'] = real_vwap
    except: pass

    try:
        return apply_technical_indicators(df)
    except Exception as e:
        logging.warning(f"技術指標計算失敗 {ticker_number}: {e}")
        df['ATR'] = df['Close'] * 0.03
        df['ADX'] = 20
        df['RSI'] = 50
        return df

@st.cache_data(ttl=86400, show_spinner=False)
def get_fundamental_and_industry_data(ticker_number, current_price=0):
    base_ticker = str(ticker_number).strip().upper().replace(".TW", "").replace(".TWO", "")
    eps_val, pe_val, ind = "無", "無", "一般產業"
    try:
        info = yf.Ticker(f"{base_ticker}.TW").info
        if not info or 'industry' not in info: info = yf.Ticker(f"{base_ticker}.TWO").info

        raw_sector = info.get("sector", "")
        if raw_sector in ENG_TO_TW_INDUSTRY: ind = ENG_TO_TW_INDUSTRY[raw_sector]
        elif info.get("industry") in ENG_TO_TW_INDUSTRY: ind = ENG_TO_TW_INDUSTRY[info.get("industry")]

        if 'trailingEps' in info and info['trailingEps'] is not None:
            eps_val = str(round(info['trailingEps'], 2))
    except: pass

    # 單次呼叫 CNYES API，同時取得產業名稱和 EPS，避免重複請求
    if ind == "一般產業" or eps_val == "無":
        try:
            res_cnyes = requests.get(
                f"https://ws.cnyes.com/twstock/api/v1/company/profile/{base_ticker}", timeout=3
            ).json()
            cnyes_data = res_cnyes.get('data', {})
            if ind == "一般產業" and 'categoryName' in cnyes_data:
                ind = cnyes_data['categoryName']
            if eps_val == "無" and 'eps' in cnyes_data:
                try: eps_val = f"{float(cnyes_data['eps']):.2f}"
                except: pass
        except: pass

    if eps_val != "無" and current_price > 0:
        try: pe_val = str(round(float(current_price) / float(eps_val), 2)) if float(eps_val) > 0 else "虧損"
        except: pass
    return {"EPS": eps_val, "PE": pe_val, "Industry": ind}

@st.cache_data(ttl=86400, show_spinner=False)
def get_finmind_chip_and_revenue(ticker):
    big_player_ratio, mom, yoy = 0.0, 0.0, 0.0
    base_ticker = str(ticker).strip().upper().replace(".TW", "").replace(".TWO", "")
    if not FINMIND_TOKEN:
        return round(big_player_ratio, 2), round(mom, 2), round(yoy, 2)
    try:
        start_date_chip = (datetime.now() - timedelta(days=60)).strftime('%Y-%m-%d')
        try:
            url_chip = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockHoldingSharesPer&data_id={base_ticker}&start_date={start_date_chip}&token={FINMIND_TOKEN}"
            res_chip = requests.get(url_chip, timeout=5).json()
            if 'data' in res_chip and len(res_chip['data']) > 0:
                latest_date = max([x.get('date', '') for x in res_chip['data']])
                for x in res_chip['data']:
                    if x.get('date') == latest_date and int(x.get('HoldingSharesLevel', 0)) >= 12:
                        big_player_ratio += float(str(x.get('percent', 0)).replace(',', ''))
        except: pass

        start_date_rev = (datetime.now() - timedelta(days=500)).strftime('%Y-%m-%d')
        try:
            url_rev = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockMonthRevenue&data_id={base_ticker}&start_date={start_date_rev}&token={FINMIND_TOKEN}"
            res_rev = requests.get(url_rev, timeout=5).json()
            if 'data' in res_rev and len(res_rev['data']) > 0:
                df_rev = pd.DataFrame(res_rev['data']).sort_values(by='date').reset_index(drop=True)
                df_rev['revenue'] = pd.to_numeric(df_rev['revenue'], errors='coerce').fillna(0)
                if len(df_rev) >= 2 and df_rev['revenue'].iloc[-2] > 0:
                    mom = (df_rev['revenue'].iloc[-1] - df_rev['revenue'].iloc[-2]) / df_rev['revenue'].iloc[-2] * 100
                if len(df_rev) >= 13 and df_rev['revenue'].iloc[-13] > 0:
                    yoy = (df_rev['revenue'].iloc[-1] - df_rev['revenue'].iloc[-13]) / df_rev['revenue'].iloc[-13] * 100
        except: pass
    except: pass
    return round(big_player_ratio, 2), round(mom, 2), round(yoy, 2)

@st.cache_data(ttl=5, show_spinner=False) 
def get_twii_quote():
    tz_tpe = timezone(timedelta(hours=8))
    update_time_str = datetime.now(tz_tpe).strftime('%Y/%m/%d %H:%M:%S')
    fallback_curr, fallback_change = 0, 0
    try:
        df = yf.Ticker("^TWII").history(period="1mo").dropna(subset=['Close'])
        if not df.empty and len(df) >= 2:
            fallback_curr = float(df['Close'].iloc[-1])
            fallback_change = float(df['Close'].iloc[-1] - df['Close'].iloc[-2])
    except: pass
    return fallback_curr, fallback_change, update_time_str

def is_plausible_txf_price(price, previous=None, reference_index=None):
    price = safe_num(price, None)
    previous = safe_num(previous, None)
    reference_index = safe_num(reference_index, None)
    if price is None or not (10000 < price < 100000):
        return False
    if previous is not None and previous > 0 and abs(price - previous) / previous > 0.08:
        return False
    if reference_index is not None and reference_index > 10000 and abs(price - reference_index) / reference_index > 0.03:
        return False
    return True
    # 台指期近月通常不應長時間偏離加權指數太多；超過 3% 多半是抓到錯合約/錯欄位。
    if reference_index is not None and reference_index > 10000 and abs(price - reference_index) / reference_index > 0.03:
        return False
    return True

@st.cache_resource(show_spinner=False)
def init_shioaji_api(api_key, secret_key, simulation):
    try:
        import shioaji as sj
        api = sj.Shioaji(simulation=simulation)
        api.login(api_key, secret_key)
        return api
    except Exception as e:
        logging.error(f"Shioaji 登入失敗: {e}")
        return None

@st.cache_data(ttl=60, show_spinner=False)
def get_txf_quote(reference_index=None):
    shioaji_key = get_secret("SHIOAJI_API_KEY")
    shioaji_secret = get_secret("SHIOAJI_SECRET_KEY")
    shioaji_sim = get_secret("SHIOAJI_SIMULATION", "false").lower() == "true"
    
    if shioaji_key and shioaji_secret:
        try:
            api = init_shioaji_api(shioaji_key, shioaji_secret, shioaji_sim)
            if api:
                contract = None
                try:
                    contract = api.Contracts.Futures.TXF.TXFR1
                except AttributeError:
                    try:
                        contract = api.Contracts.Futures['TXF']['TXFR1']
                    except Exception:
                        pass
                
                if contract:
                    snapshots = api.snapshots([contract])
                    if snapshots:
                        snap = snapshots[0]
                        curr = getattr(snap, "close", 0.0)
                        change = getattr(snap, "change_price", 0.0)
                        if curr > 0:
                            snap_ts = getattr(snap, "ts", 0)
                            import datetime as dt
                            if snap_ts > 0:
                                try:
                                    ts_len = len(str(snap_ts))
                                    if ts_len >= 16:
                                        snap_time = dt.datetime.fromtimestamp(snap_ts / 1e9, tz=timezone(timedelta(hours=8))).strftime('%Y/%m/%d %H:%M')
                                    else:
                                        snap_time = dt.datetime.fromtimestamp(snap_ts, tz=timezone(timedelta(hours=8))).strftime('%Y/%m/%d %H:%M')
                                except Exception:
                                    snap_time = datetime.now(timezone(timedelta(hours=8))).strftime('%Y/%m/%d %H:%M')
                            else:
                                snap_time = datetime.now(timezone(timedelta(hours=8))).strftime('%Y/%m/%d %H:%M')
                            
                            prev = curr - change if change is not None else curr
                            if is_plausible_txf_price(curr, prev, reference_index):
                                return curr, change or 0.0, f"Shioaji TX ({contract.code})", snap_time
        except Exception as e:
            logging.error(f"Shioaji 取得期貨報價失敗: {e}")

    for symbol in ["TXF.TW", "FITX.TW", "TX=F"]:
        try:
            df = yf.Ticker(symbol).history(period="5d").dropna(subset=['Close'])
            if len(df) >= 2:
                curr = float(df['Close'].iloc[-1])
                prev = float(df['Close'].iloc[-2])
                if is_plausible_txf_price(curr, prev, reference_index):
                    return curr, curr - prev, symbol, df.index[-1].strftime('%Y/%m/%d')
        except Exception:
            pass
    if FINMIND_TOKEN:
        try:
            start_date = (datetime.now() - timedelta(days=20)).strftime('%Y-%m-%d')
            url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanFuturesDaily&data_id=TX&start_date={start_date}&token={FINMIND_TOKEN}"
            res = requests.get(url, timeout=5).json()
            rows = res.get("data", [])
            if rows:
                df = pd.DataFrame(rows).sort_values(by="date")
                close_col = next((c for c in ["close", "settlement_price", "Close"] if c in df.columns), None)
                if close_col and len(df) >= 2:
                    df[close_col] = pd.to_numeric(df[close_col], errors="coerce")
                    df = df.dropna(subset=[close_col])
                    df = df[(df[close_col] > 10000) & (df[close_col] < 100000)]
                    if len(df) >= 2:
                        curr = float(df[close_col].iloc[-1])
                        prev = float(df[close_col].iloc[-2])
                        if is_plausible_txf_price(curr, prev, reference_index):
                            return curr, curr - prev, "FinMind TX", str(df["date"].iloc[-1])
        except Exception:
            pass
    return None, None, "資料源受限", "暫無資料"

@st.cache_data(ttl=5, show_spinner=False)
def get_stock_live_time(ticker): return datetime.now(timezone(timedelta(hours=8))).strftime('%Y/%m/%d %H:%M:%S')

@st.cache_data(ttl=3600, show_spinner=False)
def get_institutional_trading(ticker):
    if not FINMIND_TOKEN:
        return []
    try:
        url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInstitutionalInvestorsBuySell&data_id={ticker}&start_date={(datetime.now() - timedelta(days=20)).strftime('%Y-%m-%d')}&token={FINMIND_TOKEN}"
        res = requests.get(url, timeout=5).json()
        if res.get('msg') == 'success' and len(res.get('data', [])) > 0:
            df = pd.DataFrame(res['data'])
            df['net'] = (df['buy'] - df['sell']) / 1000  
            df['type'] = '其他'
            df.loc[df['name'].str.contains('Foreign|外資', case=False, na=False), 'type'] = '外資'
            df.loc[df['name'].str.contains('Trust|投信', case=False, na=False), 'type'] = '投信'
            df.loc[df['name'].str.contains('Dealer|自營', case=False, na=False), 'type'] = '自營商'
            pivot = df.groupby(['date', 'type'])['net'].sum().unstack(fill_value=0).reset_index()
            for col in ['外資', '投信', '自營商']:
                if col not in pivot.columns: pivot[col] = 0
            pivot['單日合計'] = pivot['外資'] + pivot['投信'] + pivot['自營商']
            return [{"日期": r['date'][-5:].replace("-", "/"), "外資(張)": int(r['外資']), "投信(張)": int(r['投信']), "自營商(張)": int(r['自營商']), "單日合計(張)": int(r['單日合計'])} for _, r in pivot.sort_values('date', ascending=False).head(10).iterrows()]
    except: pass
    return []

@st.cache_data(ttl=300, show_spinner=False)
def get_global_macro_data():
    data = {"global_time": datetime.now(timezone(timedelta(hours=8))).strftime('%Y/%m/%d %H:%M:%S'), "status": {}}
    for t, url in {"^SOX": "https://finance.yahoo.com/quote/^SOX", "^VIX": "https://finance.yahoo.com/quote/^VIX", "TWD=X": "https://finance.yahoo.com/quote/TWD=X"}.items():
        try:
            df = yf.Ticker(t).history(period="5d").dropna(subset=['Close'])
            if len(df) >= 2:
                c, p = float(df['Close'].iloc[-1]), float(df['Close'].iloc[-2])
                data[t] = {"price": c, "pct": (c-p)/p*100 if p != 0 else 0, "time": df.index[-1].strftime('%Y/%m/%d'), "url": url, "status": "ok"}
                data["status"][t] = "ok"
            else:
                data[t] = {"price": None, "pct": None, "time": "暫無資料", "url": url, "status": "missing"}
                data["status"][t] = "missing"
        except:
            data[t] = {"price": None, "pct": None, "time": "暫無資料", "url": url, "status": "missing"}
            data["status"][t] = "missing"
    try:
        twii_ref, _, _ = get_twii_quote()
        txf_price, txf_change, txf_symbol, txf_time = get_txf_quote(twii_ref)
        if txf_price is not None and txf_change is not None:
            prev = txf_price - txf_change
            data["TX=F"] = {"price": txf_price, "pct": txf_change / prev * 100 if prev else 0, "time": txf_time, "url": txf_symbol, "status": "ok"}
            data["status"]["TX=F"] = "ok"
        else:
            data["TX=F"] = {"price": None, "pct": None, "time": "暫無資料", "url": txf_symbol, "status": "missing"}
            data["status"]["TX=F"] = "missing"
    except Exception:
        data["TX=F"] = {"price": None, "pct": None, "time": "暫無資料", "url": "資料源受限", "status": "missing"}
        data["status"]["TX=F"] = "missing"
    return data

def open_pred_logic(twii_df, twii_close, twii_change, twii_time_str=""):
    macro_data = get_global_macro_data()
    if twii_df is None or len(twii_df) < 2: return "資料不足", "無法分析", "資料不足", "無法預測", "", "", 50, macro_data
    t_open, t_close, p_close = twii_df['Open'].iloc[-1], twii_df['Close'].iloc[-1], twii_df['Close'].iloc[-2]
    if twii_close > 0:
        t_close = twii_close
        p_close = twii_close - twii_change
    
    last_dt_str = twii_time_str.split(" ")[0] if twii_time_str else datetime.now(timezone(timedelta(hours=8))).strftime('%Y/%m/%d')
    next_dt = datetime.strptime(last_dt_str, '%Y/%m/%d') + timedelta(days=1) if '/' in last_dt_str else datetime.now(timezone(timedelta(hours=8)))
    while next_dt.weekday() >= 5: next_dt += timedelta(days=1)
    
    today_title, today_desc = "⚖️ 平盤震盪", "大盤開在平盤附近，量價關係呈現縮量，盤勢陷入震盪整理。"
    if t_open > p_close * 1.003:
        if t_close > t_open: today_title, today_desc = "🔥 開高走高", "受激勵跳空開高，配合量能放大，盤勢偏多。"
        else: today_title, today_desc = "⚠️ 開高走低", "跳空開高後遭遇短線獲利了結賣壓，呈現高檔回落。"
    elif t_open < p_close * 0.997:
        if t_close > t_open: today_title, today_desc = "💪 開低走高", "開低但低檔承接買盤強勁，出現開低走高收紅K。"
        else: today_title, today_desc = "🩸 開低走低", "大盤弱勢開低，恐慌指數上升引發停損賣壓，盤勢偏空。"

    risk_score = 50 
    if t_close < (twii_df['5MA'].iloc[-1] if '5MA' in twii_df.columns else t_close): risk_score += 15
    else: risk_score -= 10
    sox_pct = macro_data.get('^SOX', {}).get('pct')
    vix_price = macro_data.get('^VIX', {}).get('price')
    twd_pct = macro_data.get('TWD=X', {}).get('pct')
    txf_pct = macro_data.get('TX=F', {}).get('pct')
    if sox_pct is not None and sox_pct < -2.0: risk_score += 20
    if vix_price is not None and vix_price > 20: risk_score += 20
    if twd_pct is not None and twd_pct > 0.4: risk_score += 8
    if txf_pct is not None and txf_pct < -0.5: risk_score += 10
    missing_macro = sum(1 for v in macro_data.get("status", {}).values() if v != "ok")
    risk_score += missing_macro * 3
    risk_score = max(5, min(95, int(risk_score))) 
    
    if risk_score < 40: tmr_title, tmr_desc = "偏多風險傾向", f"台股短均仍有支撐，外部風險未明顯升高；留意台指期與匯率是否延續。"
    elif risk_score < 70: tmr_title, tmr_desc = "中性震盪風險", f"外部變數或短線位置未完全同步，建議用支撐/壓力區間觀察，不用單點預測開盤。"
    else: tmr_title, tmr_desc = "偏空警戒風險", f"短線或外部風險因子偏弱，隔日先觀察支撐是否守住，避免追高。"
    return today_title, today_desc, tmr_title, tmr_desc, last_dt_str, next_dt.strftime('%Y/%m/%d'), risk_score, macro_data

def render_index_board():
    try:
        twii_close, twii_change, twii_time_str = get_twii_quote()
        txf_close, txf_change, txf_symbol, txf_time = get_txf_quote(twii_close)
        twii_color = '#ef4444' if twii_change >= 0 else '#22c55e'
        txf_available = txf_close is not None and txf_change is not None
        txf_color = '#ef4444' if (txf_change or 0) >= 0 else '#22c55e'
        txf_price_text = f"{txf_close:,.0f}" if txf_available else "資料源受限"
        txf_change_text = f"{'↑' if txf_change > 0 else '↓'} {abs(txf_change):.0f}" if txf_available else "請改用 FinMind/券商源"
        twii_df_for_pred = get_stock_data("^TWII")
        today_title, today_desc, tmr_title, tmr_desc, last_dt_str, next_dt_str, risk_score, macro = open_pred_logic(twii_df_for_pred, twii_close, twii_change, twii_time_str)
        sox = macro.get('^SOX', {"price": None, "pct": None})
        vix = macro.get('^VIX', {"price": None, "pct": None})
        twd = macro.get('TWD=X', {"price": None, "pct": None})
        bar_color = "#22c55e" if risk_score < 40 else ("#facc15" if risk_score < 70 else "#ef4444")
        render_market_status_cards([
            {"label": "台股加權", "value": f"{twii_close:,.0f}", "sub": f"{'+' if twii_change > 0 else ''}{twii_change:.0f}", "color": twii_color},
            {"label": f"台指期 ({txf_symbol})", "value": txf_price_text, "sub": txf_change_text, "color": txf_color},
            {"label": "費城半導體", "value": "--" if sox.get("price") is None else f"{sox.get('price'):,.1f}", "sub": "--" if sox.get("pct") is None else f"{sox.get('pct'):+.2f}%", "color": "#ef4444" if (sox.get("pct") or 0) >= 0 else "#22c55e"},
            {"label": "VIX", "value": "--" if vix.get("price") is None else f"{vix.get('price'):,.2f}", "sub": "--" if vix.get("pct") is None else f"{vix.get('pct'):+.2f}%", "color": "#22c55e" if vix.get("pct") is not None and vix.get("pct") <= 0 else "#ef4444"},
            {"label": "美元台幣", "value": "--" if twd.get("price") is None else f"{twd.get('price'):,.3f}", "sub": "--" if twd.get("pct") is None else ("台幣貶值" if twd.get("pct") > 0 else "台幣升值"), "color": "#facc15"},
            {"label": "今日風險分數", "value": f"{risk_score}%", "sub": tmr_title, "color": bar_color},
        ])
        st.markdown(
            f"""
<div style="background:#0F172A; border:1px solid #1E293B; border-radius:10px; padding:12px 14px; margin:10px 0 14px 0;">
  <div style="display:flex; justify-content:space-between; gap:14px; align-items:center; flex-wrap:wrap;">
    <div style="flex:1; min-width:260px;">
      <span style="color:#FACC15; font-weight:900;">盤勢分析 ({last_dt_str})</span>
      <span style="color:#E2E8F0; font-weight:900; margin-left:10px;">{today_title}</span>
      <span style="color:#94A3B8; margin-left:8px; font-size:0.86rem;">{today_desc}</span>
    </div>
    <div style="flex:1; min-width:260px;">
      <span style="color:#60A5FA; font-weight:900;">次日開盤 ({next_dt_str})</span>
      <span style="color:{bar_color}; font-weight:900; margin-left:10px;">{tmr_title}</span>
      <span style="color:#94A3B8; margin-left:8px; font-size:0.86rem;">{tmr_desc}</span>
    </div>
  </div>
  <div style="width:100%; height:8px; background-color:#1E293B; border-radius:6px; overflow:hidden; margin-top:10px;">
    <div style="width:{risk_score}%; height:100%; background-color:{bar_color};"></div>
  </div>
</div>
""",
            unsafe_allow_html=True,
        )
        if st.button("🔄 手動更新即時大盤報價", use_container_width=True):
            st.cache_data.clear()
            st.rerun()
    except: st.error(f"大盤儀表板加載中...")

def get_dynamic_theme(ticker, industry):
    ind = str(industry).strip() if pd.notna(industry) and industry != "無" else "一般產業"
    for kw, ic in { "半導體": "⚙️", "電子": "⚡", "綠能": "🌱", "航運": "🚢", "金融": "💰", "AI": "💡", "機器人": "🤖" }.items():
        if kw in ind: return (ind, ic)
    return (ind, "🏷️")

@st.cache_data(ttl=5, show_spinner=False) 
def analyze_today(df, ticker_number, inst_data=None, is_light_mode=False, pre_fund=None, cached_doc=None, is_intraday=False):
    if df is None or len(df) < 5: return None
    t, p = df.iloc[-1], df.iloc[-2]
    score_mode, score_mode_label, effective_intraday = resolve_score_mode(is_intraday)
    
    if pre_fund:
        fund = pre_fund
    else:
        fund = get_fundamental_and_industry_data(ticker_number, round(t['Close'], 2))
        bp_ratio, mom, yoy = get_finmind_chip_and_revenue(ticker_number)
        fund['BigPlayer'], fund['MoM'], fund['YoY'] = bp_ratio, mom, yoy
        
    macro = get_global_macro_data()
    fund['VIX'] = macro.get('^VIX', {}).get('price', 0)
        
    t_open, t_close, t_high, t_low = float(t['Open']), float(t['Close']), float(t['High']), float(t['Low'])
    p_open, p_close = float(p['Open']), float(p['Close'])
    
    red_mask = (df['Open'].shift(1) > df['Close'].shift(1)) & (df['Close'] > df['Open']) & (df['Close'] > df['Open'].shift(1)) & (df['Open'] < df['Close'].shift(1))
    black_mask = (df['Close'].shift(1) > df['Open'].shift(1)) & (df['Open'] > df['Close']) & (df['Open'] > df['Close'].shift(1)) & (df['Close'] < df['Open'].shift(1))

    whale_tag, whale_net_buy = "主力觀望", 0
    f_net_10d, t_net_10d, d_net_10d = 0, 0, 0
    inst_days = len(inst_data) if inst_data else 0
    # 法人資料有幾天算幾天，避免少於 3 天時把籌碼歸零
    if inst_data:
        f_net_10d = sum([int(str(x['外資(張)']).replace(',', '')) for x in inst_data])
        t_net_10d = sum([int(str(x['投信(張)']).replace(',', '')) for x in inst_data])
        d_net_10d = sum([int(str(x['自營商(張)']).replace(',', '')) for x in inst_data])
        sample_days = min(3, inst_days)
        f_net = sum([int(str(x['外資(張)']).replace(',', '')) for x in inst_data[:sample_days]])
        t_net = sum([int(str(x['投信(張)']).replace(',', '')) for x in inst_data[:sample_days]])
        d_net = sum([int(str(x['自營商(張)']).replace(',', '')) for x in inst_data[:sample_days]])
        whale_net_buy = f_net + t_net + d_net
    elif cached_doc:
        whale_net_buy = cached_doc.get('Whale_Net', 0)

    theme_name, theme_icon = get_dynamic_theme(ticker_number, fund['Industry'])
    ohlc_avg = (t_open + t_high + t_low + t_close) / 4
    price_anchor = safe_num(t.get('VWAP'), 0)
    price_dev_source = "real_vwap" if price_anchor > 0 else "ohlc_avg"
    if price_anchor <= 0:
        price_anchor = ohlc_avg
    price_dev = (t_close - price_anchor) / price_anchor * 100 if price_anchor > 0 else 0
    if effective_intraday and len(df) >= 6:
        avg_vol_5 = df['Volume'].iloc[-6:-1].mean()
    else:
        avg_vol_5 = df['Volume'].tail(5).mean()
    effective_volume, est_vol_ratio, volume_confirmed = adjust_intraday_volume(t['Volume'], avg_vol_5, effective_intraday)
    
    intraday_score = max(10, min(99, int(40 + (price_dev*10) + (20 if est_vol_ratio>1.5 else (10 if est_vol_ratio>1.0 else -10)))))
    flow = "大單敲進" if est_vol_ratio > 1.5 and t_close > price_anchor else "內外盤拉扯"

    body_len = abs(t_close - t_open)
    lower_shadow = min(t_close, t_open) - t_low
    upper_shadow = t_high - max(t_close, t_open)
    
    trend_quality = 0
    if t_close > t.get('20MA', t_close): trend_quality += 1
    if t.get('20MA', t_close) > t.get('60MA', t_close): trend_quality += 1
    if t.get('MACD_Hist', 0) > p.get('MACD_Hist', 0): trend_quality += 1
    if t.get('ADX', 0) >= 25: trend_quality += 1
    momentum_score = round((trend_quality / 4) * 100, 1)

    has_support = (lower_shadow > body_len * 1.5) and (effective_volume > avg_vol_5) and volume_confirmed
    hit_pressure = (upper_shadow > body_len * 1.5)
    ma5_up_today = bool(len(df) >= 6 and float(df['Close'].iloc[-1]) > float(df['Close'].iloc[-6]))
    tomorrow_turn_price = float(df['Close'].iloc[-4]) if len(df) >= 4 else t_close
    bullish_count = sum([
        t_close > t.get('20MA', t_close),
        t.get('MACD_Hist', 0) > p.get('MACD_Hist', 0),
        effective_volume > avg_vol_5 * 1.1 if avg_vol_5 > 0 else False,
        bool(red_mask.iloc[-1]),
        has_support,
        ma5_up_today,
    ])
    bearish_count = sum([
        t_close < t.get('20MA', t_close),
        t.get('MACD_Hist', 0) <= p.get('MACD_Hist', 0),
        t.get('RSI', 50) >= 75,
        bool(black_mask.iloc[-1]),
        hit_pressure,
        t_close < tomorrow_turn_price if tomorrow_turn_price > 0 else False,
    ])
    conflict_score = min(bullish_count, bearish_count) / max(bullish_count, bearish_count, 1)
    signal_conflict = "高" if conflict_score >= 0.55 else ("中" if conflict_score >= 0.3 else "低")
    if hit_pressure and t.get('RSI', 50) >= 75:
        entry_pattern = "過熱追高型"
    elif t_close > t.get('20MA', t_close) and est_vol_ratio > 1.5 and t.get('MACD_Hist', 0) > p.get('MACD_Hist', 0):
        entry_pattern = "趨勢突破型"
    elif has_support and t_close > t.get('20MA', t_close):
        entry_pattern = "回測支撐型"
    elif t.get('RSI', 50) <= 35 and t_close > p_close:
        entry_pattern = "低檔反彈型"
    elif t_close > t.get('20MA', t_close) and hit_pressure:
        entry_pattern = "假突破風險型"
    else:
        entry_pattern = "一般觀察型"
    data_quality, confidence = build_data_quality(
        price_status="realtime" if effective_intraday else "ok",
        volume_status="confirmed" if volume_confirmed else "estimated",
        institutional_days=inst_days,
        revenue_status="ok" if "MoM" in fund and "YoY" in fund else "missing",
        macro_status=macro.get("status", {}),
        txf_status=macro.get("status", {}).get("TX=F", "missing")
    )

    data = {
        "代號": ticker_number, "名稱": get_stock_name(ticker_number), "ticker_raw": ticker_number,
        "產業": fund['Industry'], "昨日收盤價": round(p_close, 2), "收盤價": round(t_close, 2), 
        "漲跌": round(t_close - p_close, 2), "漲跌幅": round((t_close - p_close) / p_close * 100, 2), 
        "成交量": int(effective_volume), "原始成交量": int(t['Volume']), "5日均量": int(avg_vol_5),
        "5MA": round(t.get('5MA', t_close), 2), "10MA": round(t.get('10MA', t_close), 2), 
        "20MA": round(t.get('20MA', t_close), 2), "60MA": round(t.get('60MA', t_close), 2),
        "BB_UP": round(t.get('BB_UP', t_close), 2), "BB_DN": round(t.get('BB_DN', t_close), 2), 
        "BIAS": round(t.get('BIAS_20', 0), 2), "MACD柱": round(t.get('MACD_Hist', 0), 3), "前日MACD柱": round(p.get('MACD_Hist', 0), 3),
        "K": round(t.get('K', 50), 2), "D": round(t.get('D', 50), 2), "J值": round(t.get('J', 50), 2),
        "ADX": round(t.get('ADX', 0), 1), "RSI": round(t.get('RSI', 50), 1),
        "ROC_20": round((t_close - float(df['Close'].iloc[-20])) / float(df['Close'].iloc[-20]) * 100 if len(df)>=20 else 0, 2), 
        "MoM": fund.get('MoM', 0), "YoY": fund.get('YoY', 0), 
        "ForeignNet10d": f_net_10d, "TrustNet10d": t_net_10d, "DealerNet10d": d_net_10d, 
        "紅吞": bool(red_mask.iloc[-1]), "黑吞": bool(black_mask.iloc[-1]),
        "訊號": t_close > t.get('20MA', t_close), 
        "回測有撐": has_support,
        "反彈遇壓": hit_pressure,
        "5MA已上彎": ma5_up_today, "明日5MA扣抵價": round(tomorrow_turn_price, 2),
        "5日線即將上彎": ma5_up_today,
        "Whale_Net": whale_net_buy, "Theme_Name": theme_name, "Theme_Icon": theme_icon,
        "Price_Dev": price_dev, "Price_Dev_Source": price_dev_source, "Ohlc_Avg_Dev": price_dev if price_dev_source == "ohlc_avg" else 0,
        "VWAP_Dev": price_dev if price_dev_source == "real_vwap" else 0, "Est_Vol_Ratio": est_vol_ratio, "Volume_Confirmed": volume_confirmed, "Flow": flow, "Intraday_Score": intraday_score, "Momentum_Score": momentum_score,
        "Institutional_Days": inst_days, "Data_Quality": data_quality, "Confidence": confidence,
        "Signal_Conflict": signal_conflict, "Conflict_Score": round(conflict_score, 2), "Entry_Pattern": entry_pattern,
        "ATR": round(t.get('ATR', t_close*0.03), 2),
        "ATR_Target": round(t_close + (t.get('ATR', t_close*0.03)*1.5), 1), "ATR_Stop": round(t_close - (t.get('ATR', t_close*0.03)*1.0), 1),
        "RRR": 1.5, "Intraday_Signal": "強勢越過均價線" if t_close > price_anchor and est_vol_ratio > 1.3 and volume_confirmed else ("穩守均價線" if t_close > price_anchor else "跌破均價線")
    }
    
    sc, label, rs, feature = get_decision_score(
        data, 
        fund, 
        inst_data, 
        mode=score_mode, 
        with_reason=True
    )
    
    data['Score'] = sc
    data['評級'] = label
    data['Reasons'] = rs
    data['Feature'] = feature
    data['WinRate'] = cached_doc.get('WinRate', 0.0) if cached_doc else 0.0
    data['Score_Mode'] = score_mode_label
    data['Score_Mode_Raw'] = score_mode

    return data

def calculate_historical_winrate_interactive(
    df_slice, 
    target_mult, 
    stop_mult, 
    score_threshold=60, 
    enable_trailing=False, 
    filter_low_conf=False, 
    filter_high_conflict=False
):
    result = calculate_historical_performance(
        df_slice, 
        target_mult, 
        stop_mult, 
        score_threshold=score_threshold,
        enable_trailing=enable_trailing,
        filter_low_conf=filter_low_conf,
        filter_high_conflict=filter_high_conflict
    )
    return result["win_rate"], result["closed_signals"], result["wins"], result["buy_dates"], result

def generate_comprehensive_analysis(data, inst_data, sc, f_data, is_light_mode=False):
    t_text_c = "#333" if is_light_mode else "#e2e8f0"
    card_bg = "#f4f6f9" if is_light_mode else "#0f172a"
    sum_bg = "rgba(0,0,0,0.05)" if is_light_mode else "rgba(30,41,59,0.5)"
    b_col = "#ddd" if is_light_mode else "#1e293b"

    if sc >= 60: text_desc = "目前系統判定該股具備強大的波段上漲動能，各項技術與資金指標皆已表態，屬於勝率較高之強勢多頭格局，建議可設定好停損後伺機介入。"
    elif sc >= 45: text_desc = "目前該股動能逐漸加溫，但可能有部分指標過熱或尚未完全突破，屬於偏多觀察階段，建議留意後續量能變化。"
    else: text_desc = "目前該股動能偏弱或陷入盤整，風險大於預期報酬，建議維持空手觀望，等待更明確的型態出現。"
    
    tech_html = f"<div style='border: 1px solid {b_col}; border-radius: 8px; padding: 15px; margin-bottom: 15px; background-color: {card_bg};'>"
    tech_html += f"<h4 style='color: #60a5fa; margin-top: 0; font-size: 1.2rem;'>💯 技術面</h4>"
    quality = data.get("Data_Quality", {})
    confidence = data.get("Confidence", 100)
    missing_quality = [k for k, v in quality.items() if v not in ("ok", "realtime", "confirmed") and not str(v).endswith("日")]
    quality_text = "資料完整" if not missing_quality else "需留意：" + "、".join(missing_quality)
    tech_html += f"<div style='display:flex; gap:8px; flex-wrap:wrap; margin-bottom:10px; font-size:0.82rem;'>"
    tech_html += f"<span style='border:1px solid {b_col}; border-radius:6px; padding:4px 8px; color:{t_text_c}; background-color:{sum_bg};'>信心 {confidence}%</span>"
    tech_html += f"<span style='border:1px solid {b_col}; border-radius:6px; padding:4px 8px; color:{t_text_c}; background-color:{sum_bg};'>{quality_text}</span>"
    tech_html += f"</div>"
    
    tech_html += f"<ul style='line-height: 1.6; margin-top: 10px; font-size: 0.95rem; color: {t_text_c}; list-style-type: none; padding-left: 0;'>"
    for r in data.get('Reasons', []):
        if "✅" in r or "🔥" in r or "🚀" in r or "💰" in r or "📈" in r or "🏦" in r or "👑" in r or "🧨" in r: 
            tech_html += f"<li style='margin-bottom: 5px;'><span style='color:#ef4444; font-weight:bold;'>{r}</span></li>"
        elif "⚠️" in r or "🚨" in r or "🩸" in r or "📦" in r: 
            tech_html += f"<li style='margin-bottom: 5px;'><span style='color:#22c55e;'><b>{r}</b></span></li>"
        else:
            tech_html += f"<li style='margin-bottom: 5px;'>{r}</li>"
    tech_html += f"</ul>"
    
    tech_html += f"<div style='background-color: {sum_bg}; padding: 12px; border-radius: 6px; border-left: 4px solid #60a5fa; font-size: 0.95rem; color: {t_text_c}; margin-top: 15px;'><b>【總結】</b>{text_desc}</div>"
    tech_html += f"</div>"

    chip_res_text = "中立觀望"
    tables_html = ""
    th_color = "#ccc" if not is_light_mode else "#555"
    def get_c(val): return "#ef4444" if val > 0 else ("#22c55e" if val < 0 else t_text_c)

    f_net = data.get('ForeignNet10d', 0)
    t_net = data.get('TrustNet10d', 0)
    d_net = data.get('DealerNet10d', 0)
    
    if inst_data:
        sample_days = min(3, len(inst_data))
        f_net_today = sum([int(str(x['外資(張)']).replace(',', '')) for x in inst_data[:sample_days]])
        t_net_today = sum([int(str(x['投信(張)']).replace(',', '')) for x in inst_data[:sample_days]])
        if f_net_today > 0 and t_net_today > 0: chip_res_text = "🔥 外資跟投信都在買，籌碼正集中到大戶法人手上，走勢穩定。"
        elif f_net_today < 0 and t_net_today < 0: chip_res_text = "⚠️ 外資跟投信同步倒貨，籌碼有鬆動流向散戶的疑慮。"
        else: chip_res_text = "⚖️ 法人多空步調不一，一方買一方賣，籌碼處於換手震盪階段。"

        tables_html += f"<div style='display: flex; gap: 15px; flex-wrap: wrap; margin-top: 15px; width: 100%;'>"
        tables_html += f"<div style='flex: 1; min-width: 260px; border: 1px solid {b_col}; border-radius: 6px; padding: 15px; background-color: {sum_bg};'>"
        tables_html += f"<div style='font-weight: bold; color: {t_text_c}; font-size: 1rem; margin-bottom: 15px;'>🎯 進階籌碼監控 (真實數據)</div>"
        tables_html += f"<div style='font-size: 0.9rem; font-weight: bold; margin-bottom: 10px; color: {t_text_c};'>⚖️ 法人資料近 {len(inst_data)} 日可用，累積買賣超</div>"
        tables_html += f"<div style='display: flex; justify-content: space-between; font-size: 0.85rem; margin-bottom: 8px;'><span>外資及陸資</span><span style='color: {get_c(f_net)}; font-weight: bold;'>{'+' if f_net>0 else ''}{f_net:,} 張</span></div>"
        tables_html += f"<div style='display: flex; justify-content: space-between; font-size: 0.85rem; margin-bottom: 8px;'><span>投信</span><span style='color: {get_c(t_net)}; font-weight: bold;'>{'+' if t_net>0 else ''}{t_net:,} 張</span></div>"
        tables_html += f"<div style='display: flex; justify-content: space-between; font-size: 0.85rem;'><span>自營商</span><span style='color: {get_c(d_net)}; font-weight: bold;'>{'+' if d_net>0 else ''}{d_net:,} 張</span></div></div>"
        
        tables_html += f"<div style='flex: 1.5; min-width: 320px;'><div style='font-weight: bold; color: {t_text_c}; font-size: 0.95rem; margin-bottom: 10px;'>⏳ 近 {min(5, len(inst_data))} 日三大法人逐日買賣超明細 (張)</div>"
        tables_html += f"<table style='width: 100%; text-align: center; border-collapse: collapse; font-size: 0.9rem; border: 1px solid {b_col}; color: {t_text_c};'>"
        tables_html += f"<tr style='background-color: {sum_bg}; color: {th_color};'><th style='border: 1px solid {b_col}; padding: 8px 4px;'>日期</th><th style='border: 1px solid {b_col}; padding: 8px 4px;'>外資</th><th style='border: 1px solid {b_col}; padding: 8px 4px;'>投信</th><th style='border: 1px solid {b_col}; padding: 8px 4px;'>自營商</th><th style='border: 1px solid {b_col}; padding: 8px 4px;'>合計</th></tr>"
        
        for row in inst_data[:5]:
            tables_html += f"<tr><td style='border: 1px solid {b_col}; padding: 8px 4px;'>{row['日期']}</td><td style='border: 1px solid {b_col}; padding: 8px 4px; color: {get_c(row['外資(張)'])}; font-weight: 500;'>{row['外資(張)']}</td><td style='border: 1px solid {b_col}; padding: 8px 4px; color: {get_c(row['投信(張)'])}; font-weight: 500;'>{row['投信(張)']}</td><td style='border: 1px solid {b_col}; padding: 8px 4px; color: {get_c(row['自營商(張)'])}; font-weight: 500;'>{row['自營商(張)']}</td><td style='border: 1px solid {b_col}; padding: 8px 4px; color: {get_c(row['單日合計(張)'])}; font-weight: 500;'>{row['單日合計(張)']}</td></tr>"
        tables_html += f"</table><div style='text-align: right; font-size: 0.75rem; color: #888; margin-top: 10px;'>來源: FinMind API</div></div></div>"
    else:
        tables_html = f"<div style='color: {t_text_c}; font-size: 0.9rem; padding: 10px; border: 1px dashed {b_col}; border-radius: 6px;'>目前暫無籌碼資料，籌碼信心偏低，請以技術與基本面交叉確認。</div>"

    chip_html = f"<div style='border: 1px solid {b_col}; border-radius: 8px; padding: 15px; margin-bottom: 15px; background-color: {card_bg};'>"
    chip_html += f"<h4 style='color: #facc15; margin-top: 0; font-size: 1.2rem;'>🏦 籌碼面分析</h4>{tables_html}"
    chip_html += f"<div style='background-color: {sum_bg}; padding: 12px; border-radius: 6px; border-left: 4px solid #facc15; font-size: 0.95rem; color: {t_text_c}; margin-top: 15px;'><b>【總結】</b>{chip_res_text}</div></div>"

    fund_bullets = []
    eps = f_data.get('EPS', '無')
    pe = f_data.get('PE', '無')
    ind = f_data.get('Industry', '一般產業')
    
    yahoo_news_url = f"https://tw.stock.yahoo.com/quote/{data['代號']}/news"
    fund_bullets.append(f"⚪ <b>產業趨勢/題材</b>：隸屬【{ind}】板塊，受惠於市場趨勢發展。 <a href='{yahoo_news_url}' target='_blank' style='color:#60a5fa; text-decoration:none;'>[🔗Yahoo新聞解析]</a>")
    
    mom_c = "#ef4444" if data.get('MoM', 0) > 0 else ("#22c55e" if data.get('MoM', 0) < 0 else t_text_c)
    yoy_c = "#ef4444" if data.get('YoY', 0) > 0 else ("#22c55e" if data.get('YoY', 0) < 0 else t_text_c)
    fund_bullets.append(f"⚪ <b>最新月營收動能</b>：月增 (MoM) <span style='color:{mom_c}; font-weight:bold;'>{data.get('MoM', 0):.2f}%</span>，年增 (YoY) <span style='color:{yoy_c}; font-weight:bold;'>{data.get('YoY', 0):.2f}%</span>。")
    fund_bullets.append(f"⚪ <b>當季EPS</b>：<b>{eps}</b> 元。 | <b>本益比 (PE)</b>：<b>{pe}</b> 倍。")
    
    try: 
        eps_f, float_pe = float(eps), float(pe) if pe != "無" else 999
        pe_low, pe_high = 10, 20
        if any(k in ind for k in ["半導體", "電子", "AI", "軟體"]): pe_low, pe_high = 15, 30
        elif any(k in ind for k in ["金融", "銀行", "保險"]): pe_low, pe_high = 8, 16
        elif any(k in ind for k in ["航運", "鋼鐵", "營建"]): pe_low, pe_high = 6, 14
        valuation = "偏低" if float_pe < pe_low else ("合理" if float_pe <= pe_high else "偏高")
        growth = "轉強" if data.get('YoY', 0) > 10 and data.get('MoM', 0) > 0 else ("持平" if data.get('YoY', 0) >= 0 else "衰退")
        profit = "穩定" if eps_f > 0 else "虧損/不足"
        if eps_f <= 0:
            fund_res = f"🩸 估值：資料不足｜成長：{growth}｜獲利：{profit}，需嚴防營運風險。"
        elif valuation == "偏高":
            fund_res = f"⚠️ 估值：{valuation}（產業參考 {pe_low}-{pe_high} 倍）｜成長：{growth}｜獲利：{profit}，需留意追高風險。"
        else:
            fund_res = f"🔥 估值：{valuation}（產業參考 {pe_low}-{pe_high} 倍）｜成長：{growth}｜獲利：{profit}，基本面支撐較完整。"
    except: fund_res = "⚪ 基礎財報數據不足，暫以技術與籌碼面為主。"

    fund_html = f"<div style='border: 1px solid {b_col}; border-radius: 8px; padding: 15px; margin-bottom: 15px; background-color: {card_bg};'>"
    fund_html += f"<h4 style='color: #c084fc; margin-top: 0; font-size: 1.2rem;'>📑 基本面分析</h4><ul style='font-size: 0.95rem; line-height: 1.6; color: {t_text_c}; list-style-type: none; padding-left: 0;'>"
    for b in fund_bullets: fund_html += f"<li style='margin-bottom:5px;'>{b}</li>"
    fund_html += f"</ul><div style='background-color: {sum_bg}; padding: 12px; border-radius: 6px; border-left: 4px solid #c084fc; font-size: 0.95rem; color: {t_text_c};'><b>【總結】</b>{fund_res}</div></div>"

    return tech_html + chip_html + fund_html

def generate_cards_html(df_disp, is_intraday=False):
    return build_cards_html(
        df_disp,
        is_intraday=is_intraday,
        favorite_set=get_favorite_stock_set(),
        simulated_set=get_simulated_order_stock_set(),
        normalize_ticker=normalize_ticker,
        get_stock_name=get_stock_name,
        safe_num=safe_num,
        is_realtime_score_record=is_realtime_score_record,
        score_mode_label=st.session_state.get("score_mode_label", "盤後正式分數"),
    )

# ==========================================
# 🚀 頁面路由控制中心
# ==========================================
if st.session_state.page == "home":
    st.markdown("<h2 style='text-align: center; color: #818cf8; margin-bottom: 20px;'>極致精準：100分量化雷達</h2>", unsafe_allow_html=True)
    
    render_index_board()
    st.markdown("<br>", unsafe_allow_html=True)
    
    if "scan_results" not in st.session_state or not st.session_state.scan_results:
        with st.spinner("🔮 正在自 Firebase 同步全市場量化名單..."): 
            hydrate_scan_results(force=True)
    if not st.session_state.get("scan_results"):
        st.session_state.scan_results = [{"代號": t, "名稱": get_stock_name(t), "Score": 0, "產業": "一般產業"} for t in get_radar_targets([])]
        st.session_state.scan_results_is_local = True
    else:
        st.session_state.scan_results_is_local = False
            
    if st.session_state.scan_results:
        fetch_time = datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
        source_badge = "🧭 雲端名單空白，改用本機備援池即時計算" if st.session_state.get("scan_results_is_local") else "☁️ 最新雲端資料庫讀取時間"
        st.markdown(f"<div style='font-size:0.85rem; color:#64748b; margin-bottom:15px; font-weight:bold;'>{source_badge}：{fetch_time}</div>", unsafe_allow_html=True)
        if st.session_state.get("scan_results_is_local") and st.session_state.get("cloud_last_error"):
            st.caption(f"Firebase 狀態：{st.session_state.cloud_last_error}")

        st.markdown("<div class='terminal-card' style='margin-bottom:12px;'>", unsafe_allow_html=True)
        st.markdown("<div class='section-title'>雷達篩選器</div>", unsafe_allow_html=True)
        col_m1, col_m2 = st.columns([1.4, 1])
        with col_m1:
            st.caption("引擎模式")
            radar_mode = st.radio("引擎模式：", ["盤後波段精算", "盤中動能快篩"], horizontal=True, label_visibility="collapsed")
        with col_m2:
            st.caption("自選群組")
            only_favorites = st.toggle("⭐ 只看自選群組", value=False)
        st.markdown("</div>", unsafe_allow_html=True)
        requested_intraday = "盤中" in radar_mode
        score_mode, score_mode_label, is_intraday = resolve_score_mode(requested_intraday)
        st.session_state.is_intraday = is_intraday
        st.session_state.score_mode_label = score_mode_label
        if requested_intraday and not is_intraday:
            st.caption("目前非台股交易時段，系統已自動改採盤後正式分數。")
        
        cached_list = list(st.session_state.get('scan_results', []))
        use_local_fallback = st.session_state.get("scan_results_is_local", False)
        cloud_count = 0 if use_local_fallback else len(cached_list)
        
        if is_intraday or use_local_fallback:
            with st.spinner("⚡ 混合動力引擎啟動：即時運算 100 分模型 (約需 3-5 秒)..."):
                fb_df = pd.DataFrame(cached_list)
                targets = get_radar_targets(cached_list)
                live_data = []
                
                def process_live(ticker):
                    df = get_stock_data(ticker)
                    if df is not None:
                        base = next((x for x in cached_list if str(x['代號']) == str(ticker)), None)
                        analysis_cache = load_analysis_cache(ticker, LIVE_SCORE_CACHE_SECONDS)
                        cached_data = analysis_cache.get("data") if analysis_cache else None
                        if is_intraday and isinstance(cached_data, dict) and cached_data.get("Score_Mode_Raw") == "realtime":
                            cached_data = dict(cached_data)
                            cached_data["Score_Source"] = "解析快取"
                            return cached_data

                        if analysis_cache and analysis_cache.get("fund"):
                            fund = analysis_cache.get("fund")
                            inst_data = analysis_cache.get("inst_data", [])
                        else:
                            inst_data = get_institutional_trading(ticker)
                            fund = get_fundamental_and_industry_data(ticker, df['Close'].iloc[-1])
                            bp_ratio, mom, yoy = get_finmind_chip_and_revenue(ticker)
                            fund['BigPlayer'], fund['MoM'], fund['YoY'] = bp_ratio, mom, yoy
                        res = analyze_today(df, ticker, inst_data, False, fund, cached_doc=base, is_intraday=is_intraday)
                        if res:
                            bt_preview = calculate_historical_performance(df.tail(BACKTEST_LOOKBACK_DAYS), 1.5, 1.0)
                            res["WinRate"] = bt_preview.get("win_rate", res.get("WinRate", 0.0))
                            res["Backtest_Samples"] = bt_preview.get("closed_signals", 0)
                            res["Score_Source"] = "盤中重算" if is_intraday else "本機備援重算"
                            save_analysis_cache(ticker, {"data": res, "fund": fund, "inst_data": inst_data})
                            return res
                    return None
                    
                with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                    for r in executor.map(process_live, targets):
                        if r: live_data.append(r)
                df_results = pd.DataFrame(live_data) if live_data else fb_df
        else:
            df_results = pd.DataFrame(cached_list)
        mode_count = len(df_results)

        if only_favorites:
            favorite_set = get_favorite_stock_set()
            if favorite_set and '代號' in df_results.columns:
                df_results = df_results[df_results['代號'].astype(str).map(normalize_ticker).isin(favorite_set)]
            else:
                df_results = df_results.iloc[0:0]
        favorite_count = len(df_results)
        
        if '產業' not in df_results.columns:
            df_results['產業'] = "一般產業"
        available_themes = ["全部產業"] + sorted(list(set(df_results['產業'].dropna().unique()) - {"一般產業"}))
        st.markdown("<div class='terminal-card' style='margin-bottom:12px;'>", unsafe_allow_html=True)
        col_f1, col_f2 = st.columns([1.6, 1])
        with col_f1:
            st.caption("產業過濾")
            selected_theme = st.radio("產業過濾：", available_themes, horizontal=True, label_visibility="collapsed")
        with col_f2:
            st.caption("排序")
            sort_mode = st.radio("排序：", ["AI分數", "歷史勝率", "資料信心"], horizontal=True, label_visibility="collapsed")
        st.markdown("</div>", unsafe_allow_html=True)
        if selected_theme != "全部產業": df_results = df_results[df_results['產業'] == selected_theme]
        industry_count = len(df_results)
            
        if not df_results.empty: 
            df_results = df_results[df_results['Score'] >= 60]
            score_count = len(df_results)
            for col, default in {"Score": 0, "漲跌幅": 0, "WinRate": 0, "Confidence": 100}.items():
                if col not in df_results.columns:
                    df_results[col] = default
                df_results[col] = pd.to_numeric(df_results[col], errors="coerce").fillna(default)
            sort_map = {
                "AI分數": ["Score", "漲跌幅"],
                "歷史勝率": ["WinRate", "Score", "漲跌幅"],
                "資料信心": ["Confidence", "Score", "漲跌幅"],
            }
            df_disp = df_results.sort_values(by=sort_map.get(sort_mode, ["Score", "漲跌幅"]), ascending=[False] * len(sort_map.get(sort_mode, ["Score", "漲跌幅"]))).head(100)
            
            st.session_state.nav_pool = df_disp['代號'].tolist()
            st.session_state.nav_pool_data = df_disp.to_dict('records') 
            
            st.markdown(f"<div style='font-size:0.8rem; color:#94a3b8; border-bottom:1px solid #1e293b; padding-bottom:8px; margin-bottom:16px;'>⚡ 引擎運算完成 | 雲端 {cloud_count} 檔 → 模式 {mode_count} 檔 → 自選 {favorite_count} 檔 → 產業 {industry_count} 檔 → 60分以上 {score_count} 檔 | 顯示 {len(df_disp)} 檔</div>", unsafe_allow_html=True)
            if not df_disp.empty:
                left_dash, mid_dash, right_dash = st.columns([1.05, 2.1, 1.05])
                with left_dash:
                    market_rows = [
                        {"title": "掃描來源", "value": "本機" if use_local_fallback else "雲端", "sub": f"符合條件 {score_count} 檔", "color": "#60A5FA"},
                        {"title": "目前模式", "value": "盤中" if is_intraday else "盤後", "sub": score_mode_label, "color": "#FACC15"},
                        {"title": "排序口徑", "value": sort_mode, "sub": selected_theme, "color": "#94A3B8"},
                    ]
                    render_home_side_panel("市場總覽", market_rows)
                with mid_dash:
                    st.markdown("<div class='section-title'>AI 雷達清單</div>", unsafe_allow_html=True)
                    st.markdown(generate_cards_html(df_disp, is_intraday), unsafe_allow_html=True)
                with right_dash:
                    favorite_set = get_favorite_stock_set()
                    fav_rows = []
                    for _, row in df_disp.head(20).iterrows():
                        if normalize_ticker(row.get("代號", "")) in favorite_set:
                            code = normalize_ticker(row.get("代號", ""))
                            name = get_stock_name(code)
                            display_title = f"{code} {name}" if code != name else code
                            fav_rows.append({"title": display_title, "value": f"{safe_num(row.get('Score'), 0):.0f}分", "sub": row.get("Feature", "一般狀態"), "color": "#FACC15"})
                    
                    mover_rows = []
                    for _, r in df_disp.sort_values(by="漲跌幅", ascending=False).head(3).iterrows():
                        code = normalize_ticker(r.get('代號', ''))
                        name = get_stock_name(code)
                        display_title = f"{code} {name}" if code != name else code
                        mover_rows.append({"title": display_title, "value": f"{safe_num(r.get('漲跌幅'), 0):+.2f}%", "sub": r.get("Feature", "一般狀態"), "color": "#EF4444" if safe_num(r.get('漲跌幅'), 0) >= 0 else "#22C55E"})

                    order_rows = []
                    for o in st.session_state.get("simulated_orders", [])[:3]:
                        ticker = o.get('ticker')
                        curr_price_str = ""
                        pl_str = ""
                        days_str = ""
                        stop_dist_str = ""
                        try:
                            if not df_results.empty and '代號' in df_results.columns:
                                match = df_results[df_results['代號'].astype(str).apply(normalize_ticker) == normalize_ticker(ticker)]
                                if not match.empty:
                                    cp = safe_num(match['收盤價'].values[0])
                                    bp = safe_num(o.get('buy_price', cp))
                                    curr_price_str = f" 現價{cp:.1f}"
                                    if bp > 0:
                                        pl_pct = (cp - bp) / bp * 100
                                        pl_str = f" {'▲' if pl_pct>=0 else '▼'}{abs(pl_pct):.1f}%"
                        except: pass
                        try:
                            buy_time = o.get('time', '')
                            if buy_time:
                                buy_dt = datetime.fromisoformat(buy_time[:10]).replace(tzinfo=None)
                                hold_days = (datetime.now() - buy_dt).days
                                days_str = f"持倉{hold_days}天"
                        except: pass
                        try:
                            sp = safe_num(o.get('stop_price', 0))
                            cp_val = safe_num(o.get('curr_price', safe_num(o.get('buy_price', 0))))
                            if sp > 0 and cp_val > 0:
                                stop_dist = (cp_val - sp) / cp_val * 100
                                stop_dist_str = f" 離停{stop_dist:.1f}%"
                        except: pass
                        sub_text = " | ".join(filter(None, [days_str, stop_dist_str, f"目標 {o.get('target_price', '--')}"]))
                        order_rows.append({"title": f"{ticker} {o.get('name', '')}{curr_price_str}{pl_str}", "value": f"停損 {o.get('stop_price', '--')}", "sub": sub_text, "color": "#60A5FA"})
                    render_home_side_panel("我的自選", fav_rows, "目前顯示名單沒有自選股")
                    render_home_side_panel("今日異動", mover_rows)
                    render_home_side_panel("模擬交易提醒", order_rows, "目前沒有模擬交易")
            else:
                st.info("目前沒有 60 分以上標的。")
        else:
            score_count = 0
            st.markdown(f"<div style='font-size:0.8rem; color:#94a3b8; border-bottom:1px solid #1e293b; padding-bottom:8px; margin-bottom:16px;'>⚡ 篩選過程 | 雲端 {cloud_count} 檔 → 模式 {mode_count} 檔 → 自選 {favorite_count} 檔 → 產業 {industry_count} 檔 → 60分以上 {score_count} 檔</div>", unsafe_allow_html=True)
            st.info("此條件下暫無標的。")
    else: st.info("💡 雲端資料庫目前無暫存數據。")

# ==========================================
# 📊 模擬交易中心 2.0：經理人績效儀表板
# ==========================================
elif st.session_state.page == "simulated_orders":
    st.markdown("<h2 style='text-align: center; color: #818cf8; margin-bottom: 20px;'>📊 經理人績效儀表板 2.0</h2>", unsafe_allow_html=True)
    
    col_home, col_clear = st.columns([1, 1])
    with col_home:
        if st.button("🏠 回雷達總機", use_container_width=True):
            st.query_params.clear()
            st.session_state.page = "home"
            st.rerun()
    with col_clear:
        if st.button("🗑️ 清空所有紀錄", use_container_width=True):
            st.session_state.simulated_orders = []
            save_cloud_data("user_data", "simulated_orders", [])
            st.success("已清除所有紀錄！"); st.rerun()
            
    orders = st.session_state.get('simulated_orders', [])
    if not orders: st.info("目前沒有模擬下單紀錄，去解析頁面建立你的第一筆策略單吧！")
    else:
        if "delete_order_id" in st.session_state:
            st.session_state.simulated_orders = [o for o in orders if o.get('id') != st.session_state.delete_order_id]
            save_cloud_data("user_data", "simulated_orders", st.session_state.simulated_orders)
            del st.session_state["delete_order_id"]; st.rerun()
            
        total_cost, total_value, wins = 0, 0, 0
        order_metrics = []
        
        for order in orders:
            df_temp = get_stock_data(order['ticker'])
            curr_price = float(df_temp['Close'].iloc[-1]) if df_temp is not None else order['buy_price']
            
            if 'highest_price' not in order: order['highest_price'] = order['buy_price']
            if curr_price > order['highest_price']: 
                order['highest_price'] = curr_price
                save_cloud_data("user_data", "simulated_orders", st.session_state.simulated_orders)
                
            pl_val = curr_price - order['buy_price']
            pl_pct = (pl_val / order['buy_price']) * 100
            
            total_cost += order['buy_price'] * 1000 
            total_value += curr_price * 1000
            if pl_val > 0: wins += 1
            
            order_metrics.append({"name": order['name'], "pct": pl_pct, "color": "#ef4444" if pl_pct >= 0 else "#22c55e"})
            order['curr_price'] = curr_price
            order['pl_pct'] = pl_pct

        total_pl = total_value - total_cost
        total_pl_pct = (total_pl / total_cost) * 100 if total_cost > 0 else 0
        win_rate = (wins / len(orders)) * 100 if len(orders) > 0 else 0
        
        st.markdown(f"""
        <div style='display: grid; grid-template-columns: repeat(3, 1fr); gap: 15px; margin-bottom: 25px;'>
            <div style='background-color: rgba(30,41,59,0.5); padding: 15px; border-radius: 10px; text-align: center; border: 1px solid #1e293b;'>
                <div style='color: #94a3b8; font-size: 0.9rem; margin-bottom: 5px;'>投資組合總損益</div>
                <div style='color: {'#ef4444' if total_pl>=0 else '#22c55e'}; font-size: 1.8rem; font-weight: bold; font-family: monospace;'>{'+' if total_pl>0 else ''}{total_pl:,.0f} 元</div>
                <div style='color: {'#ef4444' if total_pl>=0 else '#22c55e'}; font-size: 0.9rem;'>({'+' if total_pl_pct>0 else ''}{total_pl_pct:.2f}%)</div>
            </div>
            <div style='background-color: rgba(30,41,59,0.5); padding: 15px; border-radius: 10px; text-align: center; border: 1px solid #1e293b;'>
                <div style='color: #94a3b8; font-size: 0.9rem; margin-bottom: 5px;'>當前整體勝率</div>
                <div style='color: #facc15; font-size: 1.8rem; font-weight: bold; font-family: monospace;'>{win_rate:.1f}%</div>
                <div style='color: #64748b; font-size: 0.9rem;'>(賺: {wins} / 總: {len(orders)})</div>
            </div>
            <div style='background-color: rgba(30,41,59,0.5); padding: 15px; border-radius: 10px; text-align: center; border: 1px solid #1e293b;'>
                <div style='color: #94a3b8; font-size: 0.9rem; margin-bottom: 5px;'>總投入市值</div>
                <div style='color: #e2e8f0; font-size: 1.8rem; font-weight: bold; font-family: monospace;'>{total_value:,.0f} 元</div>
                <div style='color: #64748b; font-size: 0.9rem;'>(假設每檔 1 張)</div>
            </div>
        </div>
        """, unsafe_allow_html=True)
        
        if order_metrics:
            df_m = pd.DataFrame(order_metrics)
            fig = go.Figure(data=[go.Bar(x=df_m['name'], y=df_m['pct'], marker_color=df_m['color'])])
            fig.update_layout(title="個股當前報酬率分佈 (%)", template="plotly_dark", height=300, margin=dict(l=10, r=10, t=40, b=10), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})

        st.markdown("<h4 style='color: #818cf8; border-bottom: 1px solid #1e293b; padding-bottom: 10px; margin-top: 20px;'>📝 策略明細清單</h4>", unsafe_allow_html=True)
        
        for idx, order in enumerate(orders):
            pl_col = "#ef4444" if order['pl_pct'] >= 0 else "#22c55e"
            with st.container(border=False):
                html = f"<div style='background-color: #0f172a; border: 1px solid #1e293b; border-radius: 12px; padding: 16px; margin-bottom: 14px;'><div style='display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 12px;'>"
                html += f"<a href='/?stock={order['ticker']}' target='_self' style='text-decoration:none;'><div style='display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; margin-bottom: 4px;'><span style='color: #f8fafc; font-weight: bold; font-size: 1.25rem;'>{order['name']}</span><span style='color: #64748b; font-family: monospace; font-size: 0.9rem;'>{order['ticker']}</span></div><div style='font-size: 0.75rem; color: #64748b;'>下單時間: {order['time']}</div></a>"
                html += f"<div style='text-align: right;'><div style='font-size: 0.8rem; color: #94a3b8; margin-bottom: 2px;'>最新現價 / 報酬率</div><div style='font-size: 1.3rem; font-weight: bold; font-family: monospace; color: {pl_col}; line-height: 1.1;'>{order['curr_price']:.1f}</div><div style='font-size: 0.85rem; font-weight: bold; font-family: monospace; color: {pl_col}; margin-top: 4px;'>{'+' if order['pl_pct']>0 else ''}{order['pl_pct']:.2f}%</div></div></div>"
                html += f"<div style='display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; background-color: rgba(0,0,0,0.2); border: 1px solid rgba(255,255,255,0.05); padding: 10px; border-radius: 8px;'>"
                html += f"<div style='display: flex; flex-direction: column; align-items: center;'><span style='font-size: 0.7rem; color: #64748b; margin-bottom: 4px;'>買進成本</span><span style='font-size: 1rem; font-weight: bold; color: #e2e8f0; font-family: monospace;'>{order['buy_price']:.1f}</span></div>"
                html += f"<div style='display: flex; flex-direction: column; align-items: center;'><span style='font-size: 0.7rem; color: #64748b; margin-bottom: 4px;'>創高紀錄</span><span style='font-size: 1rem; font-weight: bold; color: #facc15; font-family: monospace;'>{order['highest_price']:.1f}</span></div>"
                html += f"<div style='display: flex; flex-direction: column; align-items: center;'><span style='font-size: 0.7rem; color: #64748b; margin-bottom: 4px;'>風報比參數</span><span style='font-size: 1rem; font-weight: bold; color: #34d399; font-family: monospace;'>1 : {order.get('rrr', 1.5)}</span></div></div>"
                st.markdown(html, unsafe_allow_html=True)
                if st.button(f"❌ 刪除此單 ({order['name']})", key=f"btn_del_{order['id']}_{idx}"):
                    st.session_state.delete_order_id = order['id']; st.rerun()

# ==========================================
# 🚀 進入單一個股解析頁面 
# ==========================================
elif st.session_state.page == "analysis":
    target = normalize_ticker(st.session_state.current_stock)
    st.session_state.current_stock = target
    c_name = get_stock_name(target)

    if not st.session_state.get('nav_pool_data'):
        restore_nav_pool()
    n_pool = st.session_state.get('nav_pool', [])
    p_stk = n_pool[n_pool.index(target) - 1] if target in n_pool and n_pool.index(target) > 0 else None
    n_stk = n_pool[n_pool.index(target) + 1] if target in n_pool and n_pool.index(target) < len(n_pool) - 1 else None

    c1, c2, c3 = st.columns([1, 1, 1])
    with c1:
        if p_stk and st.button(f"⬅ 上一檔", use_container_width=True): st.session_state.update({"current_stock": p_stk}); st.rerun()
    with c2:
        if st.button("🏠 回雷達總機", use_container_width=True):
            st.query_params.clear()
            st.session_state.page = "home"
            st.rerun()
    with c3:
        if n_stk and st.button(f"下一檔 ➡", use_container_width=True): st.session_state.update({"current_stock": n_stk}); st.rerun()

    def set_view_days(days): st.session_state.view_days = days
    chart_days_param = str(st.query_params.get("days", st.session_state.view_days))
    if chart_days_param in ("30", "60", "90"):
        st.session_state.view_days = int(chart_days_param)
    def chart_flag(name, default=True):
        raw_value = str(st.query_params.get(name, "1" if default else "0")).lower()
        return raw_value not in ("0", "false", "off", "no")
    def chart_control_url(days=None, show_buy=None, show_sup=None, show_signals=None):
        mode_param = str(st.query_params.get("mode", "")).strip()
        q_days = days if days is not None else st.session_state.view_days
        q_buy = "1" if (chart_flag("show_buy", True) if show_buy is None else show_buy) else "0"
        q_sup = "1" if (chart_flag("show_sup", True) if show_sup is None else show_sup) else "0"
        q_sig = "1" if (chart_flag("show_signals", True) if show_signals is None else show_signals) else "0"
        mode_piece = f"&mode={mode_param}" if mode_param else ""
        return f"/?stock={target}&days={q_days}&show_buy={q_buy}&show_sup={q_sup}&show_signals={q_sig}{mode_piece}"
    
    df_chart = get_stock_data(target)
    if df_chart is not None and len(df_chart) >= 14:
        df_slice = df_chart.iloc[:len(df_chart) + st.session_state.date_offset] if st.session_state.date_offset < 0 else df_chart
        current_list = st.session_state.get('nav_pool_data', []) or []
        cached_list = st.session_state.get('scan_results', [])
        cached_doc = next((x for x in current_list if normalize_ticker(x.get('代號', '')) == target), None)
        if cached_doc is None:
            cached_doc = next((x for x in cached_list if normalize_ticker(x.get('代號', '')) == target), None)
        query_mode = str(st.query_params.get('mode', '')).lower()
        requested_analysis_intraday = query_mode in ("intraday", "realtime")
        _, _, inferred_intra = resolve_score_mode(requested_analysis_intraday or is_realtime_score_record(cached_doc))
        is_intra = bool(st.session_state.get('is_intraday', False) or inferred_intra)
        if is_intra:
            st.session_state.is_intraday = True
            st.session_state.score_mode_label = "盤中參考分數"
        force_key = f"force_analysis_refresh_{target}"
        force_analysis_refresh = st.session_state.pop(force_key, False)
        cached_analysis = None if force_analysis_refresh else load_analysis_cache(target, LIVE_SCORE_CACHE_SECONDS if is_intra else POST_ANALYSIS_CACHE_SECONDS)

        cached_data = cached_analysis.get("data") if cached_analysis else None
        used_realtime_snapshot = False
        if is_intra and is_realtime_score_record(cached_doc) and not force_analysis_refresh:
            inst_data = cached_analysis.get("inst_data", []) if cached_analysis else []
            f_data = cached_analysis.get("fund", {"Industry": cached_doc.get('產業', '一般產業')})
            data = dict(cached_doc)
            data["Score_Source"] = "名單盤中快照"
        elif is_intra and isinstance(cached_data, dict) and is_realtime_score_record(cached_data):
            inst_data = cached_analysis.get("inst_data", [])
            f_data = cached_analysis.get("fund", {"Industry": cached_doc.get('產業', '一般產業') if cached_doc else "一般產業"})
            data = dict(cached_data)
            data["Score_Source"] = "30秒盤中快照"
        elif cached_analysis:
            inst_data = cached_analysis.get("inst_data", [])
            f_data = cached_analysis.get("fund", {"Industry": cached_doc.get('產業', '一般產業') if cached_doc else "一般產業"})
            data = analyze_today(df_slice, target, inst_data, is_light_mode, f_data, cached_doc=cached_doc, is_intraday=is_intra)
            if is_intra:
                data["Score_Source"] = "盤中重算"
        else:
            inst_data = get_institutional_trading(target)
            f_data = get_fundamental_and_industry_data(target, df_slice['Close'].iloc[-1])
            bp_ratio, mom, yoy = get_finmind_chip_and_revenue(target)
            f_data['BigPlayer'], f_data['MoM'], f_data['YoY'] = bp_ratio, mom, yoy
            data = analyze_today(df_slice, target, inst_data, is_light_mode, f_data, cached_doc=cached_doc, is_intraday=is_intra)
            if is_intra:
                data["Score_Source"] = "盤中重算"
            save_analysis_cache(target, {"data": data, "fund": f_data, "inst_data": inst_data})

        use_cached_list_score = cached_doc and not force_analysis_refresh and not is_intra
        if use_cached_list_score:
            for k in ["Score", "評級", "Reasons", "Feature", "WinRate", "Score_Mode", "Score_Mode_Raw", "Whale_Net", "Confidence"]:
                if k in cached_doc:
                    data[k] = cached_doc[k]
            if "Score_Mode" not in data:
                data["Score_Mode"] = st.session_state.get("score_mode_label", "盤後正式分數")

        sc = data['Score']
        
        display_time = get_stock_live_time(target)
        if sc >= 70:
            strategy_text = "趨勢偏強，拉回不破 20MA 可觀察續強"
        elif sc >= 60:
            strategy_text = "強勢偏多，等待回測均線或量能確認"
        elif sc >= 45:
            strategy_text = "偏多觀察，先等訊號確認不追價"
        else:
            strategy_text = "訊號不足，暫不主動進場"
        render_stock_hero(data, target, c_name, strategy_text)
        score_source_text = f"　｜　來源：<b>{data.get('Score_Source')}</b>" if data.get("Score_Source") else ""
        st.markdown(f"<div style='text-align: center; color: #888; font-size: 0.9rem; margin-bottom: 10px;'>🕒 抓取時間: {display_time}　｜　採用：<b>{data.get('Score_Mode', '盤後正式分數')}</b>{score_source_text}</div>", unsafe_allow_html=True)
        
        _, up_c, _ = st.columns([1, 2, 1])
        force_refresh_analysis = up_c.button("🔄 更新個股即時數值", use_container_width=True)
        if force_refresh_analysis:
            st.session_state[force_key] = True
            st.cache_data.clear()
            st.rerun()
        st.markdown("---")
        
        st.markdown("##### 策略回測實驗室")
        
        # 建立控制欄位讓使用者調整參數
        col_bt1, col_bt2, col_bt3 = st.columns(3)
        with col_bt1:
            atr_target_mult = st.slider("停利 ATR 倍數", min_value=0.5, max_value=5.0, value=1.5, step=0.1, key="bt_target_mult")
        with col_bt2:
            atr_stop_mult = st.slider("停損 ATR 倍數", min_value=0.5, max_value=3.0, value=1.0, step=0.1, key="bt_stop_mult")
        with col_bt3:
            score_thresh = st.slider("開倉分數門檻", min_value=40, max_value=80, value=60, step=5, key="bt_score_thresh")
            
        dynamic_rrr = round(atr_target_mult / atr_stop_mult, 2) if atr_stop_mult > 0 else 0.0

        # 優化選項
        col_opt1, col_opt2, col_opt3 = st.columns(3)
        with col_opt1:
            enable_trailing = st.checkbox("啟用移動止損 (Trailing Stop)", value=False, key="bt_enable_trailing")
        with col_opt2:
            filter_low_conf = st.checkbox("過濾低信心度 (< 60%)", value=False, key="bt_filter_low_conf")
        with col_opt3:
            filter_high_conflict = st.checkbox("過濾高多空衝突", value=False, key="bt_filter_high_conflict")

        backtest_df = df_slice.tail(st.session_state.view_days)
        win_rate, closed_signals, wins, buy_dates, backtest_stats = calculate_historical_winrate_interactive(
            backtest_df, 
            atr_target_mult, 
            atr_stop_mult,
            score_threshold=score_thresh,
            enable_trailing=enable_trailing,
            filter_low_conf=filter_low_conf,
            filter_high_conflict=filter_high_conflict
        )
        
        # 只有當所有回測設定均為系統預設時，才採用快取的歷史勝率以加速讀取
        is_default_backtest = (
            atr_target_mult == 1.5 and 
            atr_stop_mult == 1.0 and 
            score_thresh == 60 and 
            not enable_trailing and 
            not filter_low_conf and 
            not filter_high_conflict
        )
        if use_cached_list_score and is_default_backtest:
            win_rate = safe_num(cached_doc.get("WinRate"), win_rate)
        if is_intra:
            data['WinRate'] = win_rate
            for row in st.session_state.get('nav_pool_data', []) or []:
                if normalize_ticker(row.get('代號', '')) == target:
                    for k in ["Score", "評級", "Reasons", "Feature", "WinRate", "Score_Mode", "Score_Mode_Raw", "Whale_Net", "Confidence", "Score_Source"]:
                        if k in data:
                            row[k] = data[k]
                    break
        
        curr_atr = df_slice['ATR'].iloc[-1] if 'ATR' in df_slice.columns else data['收盤價'] * 0.03
        data['ATR_Target'] = round(data['收盤價'] + (curr_atr * atr_target_mult), 1)
        data['ATR_Stop'] = round(data['收盤價'] - (curr_atr * atr_stop_mult), 1)
        data['RRR'] = dynamic_rrr
        credibility_text, credibility_color = credibility_label(closed_signals)
        render_metric_grid([
            {"label": "AI 分數", "value": f"{sc}", "sub": data.get("評級", "").replace("🟢 ", "").replace("🟡 ", "").replace("⚪ ", ""), "color": "#EF4444" if sc >= 60 else "#FACC15"},
            {"label": "歷史勝率", "value": f"{win_rate:.1f}%", "sub": "保守修正後", "color": "#EF4444" if win_rate >= 60 else "#FACC15"},
            {"label": "回測樣本", "value": f"{closed_signals} 筆", "sub": credibility_text, "color": credibility_color},
            {"label": "風報比", "value": f"1 : {dynamic_rrr}", "sub": f"停損 {atr_stop_mult}x / 停利 {atr_target_mult}x", "color": "#60A5FA"},
        ])
        v_c = "#22c55e" if sc < 45 else ("#facc15" if sc < 60 else "#ef4444")
        v_t = data['評級'].replace('🟢 ', '').replace('🟡 ', '').replace('⚪ ', '')
        confidence = data.get("Confidence", 100)
        st.markdown(f"""
        <div style="border: 2px solid {v_c}; border-radius: 10px; padding: 20px; margin-bottom: 20px; background-color: #0b1120;">
            <h3 style="text-align: center; color: {v_c}; margin-top: 0; font-size: 1.8rem; margin-bottom: 8px;">🤖 100分量化決策大腦：{v_t} ({sc}分)</h3>
            <div style="text-align:center; color:#94a3b8; font-weight:700; margin-bottom:16px;">資料信心：{confidence}%｜口徑：{data.get('Score_Mode', '盤後正式分數')}</div>
            <div style="background-color: rgba(30,41,59,0.5); padding: 15px; border-radius: 8px; border-left: 5px solid {v_c}; margin-bottom:20px;">
                <p style="font-size: 1.05rem; color: #f8fafc; margin: 0; line-height: 1.6;">
                    ✅ <b>自訂策略執行規劃</b><br>合理停利目標：<b style='color:#ef4444;'>{data['ATR_Target']}</b> 元<br>嚴格停損防守：<b style='color:#22c55e;'>{data['ATR_Stop']}</b> 元
                </p>
            </div>
            {generate_comprehensive_analysis(data, inst_data, sc, f_data, is_light_mode)}
        </div>""", unsafe_allow_html=True)

        st.markdown("---")
        st.markdown("### 🧮 資金控管與零股計算器")
        c1_c, c2_c, c3_c = st.columns(3)
        with c1_c: max_loss = st.selectbox("單筆最高可接受虧損 (元)", [5000, 10000, 15000, 20000, 30000])
        with c2_c: stop_loss_price = st.number_input("設定停損價格 (預設套用上方ATR防守線)", value=float(data['ATR_Stop']), step=0.1)
        
        risk_per_share = data['收盤價'] - stop_loss_price
        if risk_per_share > 0:
            suggested_shares = int(max_loss / risk_per_share)
            with c3_c: st.markdown(f"<div style='background:rgba(239,68,68,0.1); padding:10px; border-radius:8px; text-align:center;'><span style='font-size:0.8rem; color:#ef4444;'>嚴守紀律！建議最高買進股數</span><br><span style='font-size:1.8rem; font-weight:bold; color:#ef4444;'>{suggested_shares} 股</span></div>", unsafe_allow_html=True)
        else:
            with c3_c: st.warning("停損價必須低於現價")
        st.markdown("---")
        
        if st.button("🛒 將此自訂策略加入模擬交易", use_container_width=True):
            new_order = {
                "id": str(int(time.time())), "ticker": target, "name": c_name, "buy_price": data['收盤價'],
                "highest_price": data['收盤價'], "target_price": data['ATR_Target'], "stop_price": data['ATR_Stop'],
                "rrr": data['RRR'], "time": datetime.now(timezone(timedelta(hours=8))).strftime('%Y/%m/%d %H:%M:%S')
            }
            st.session_state.simulated_orders.insert(0, new_order)
            save_cloud_data("user_data", "simulated_orders", st.session_state.simulated_orders)
            st.success(f"✅ 已將風報比 1:{data['RRR']} 的策略單寫入資料庫！"); st.balloons()
        
        current_show_buy = chart_flag("show_buy", True)
        current_show_sup = chart_flag("show_sup", True)
        current_show_signals = chart_flag("show_signals", True)
        day_cards = []
        for day in (30, 60, 90):
            active = " active" if st.session_state.view_days == day else ""
            day_cards.append(f"<a class='chart-control-card{active}' href='{chart_control_url(days=day)}'>{day}日</a>")
        st.markdown(f"<div class='chart-control-grid'>{''.join(day_cards)}</div>", unsafe_allow_html=True)
        buy_class = "active" if current_show_buy else "off"
        sup_class = "active" if current_show_sup else "off"
        sig_class = "active" if current_show_signals else "off"
        buy_url = chart_control_url(show_buy=not current_show_buy)
        sup_url = chart_control_url(show_sup=not current_show_sup)
        sig_url = chart_control_url(show_signals=not current_show_signals)
        st.markdown(
            f"<div class='chart-control-grid'>"
            f"<a class='chart-control-card {buy_class}' href='{buy_url}'>買進</a>"
            f"<a class='chart-control-card {sup_class}' href='{sup_url}'>高低點</a>"
            f"<a class='chart-control-card {sig_class}' href='{sig_url}'>符號</a>"
            f"</div>",
            unsafe_allow_html=True,
        )
        fig = draw_professional_chart(df_slice, data['收盤價'], st.session_state.view_days, is_light_mode, current_show_buy, current_show_sup, current_show_signals, buy_dates=buy_dates)
        st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False, 'scrollZoom': True})
        
        with st.expander("📖 點擊展開：圖表符號與線段對照說明", expanded=False):
            st.markdown("""
            **【線段與區域】**
            * 🟨 **黃線 (5T) / 🟩 綠線 (10T) / 🟦 藍線 (20T)**：短中期移動平均線。
            * 🟪 **紫色虛線**：AI 運算的主力成本區 (Volume Profile)，代表該價位累積成交量極大，為關鍵支撐/壓力位。
            
            **【交易訊號圖示】**
            * 🔼 **藍色三角 (帶數字)**：AI 100分量化模型綜合買點，下方數字為當日結算滿分 100 的得分。
            * **撐 / 壓**：帶量突破買點或回踩主力成本支撐成功 / 跌破或遇到主力成本壓力區留上影線。
            * **5↗️ / 5↘️**：單一 5 日短均線扣抵值趨勢。代表均線即直剔除的歷史 K 棒位置，箭頭為預判 5 日均線未來**上彎(↗️)**或**下彎(↘️)**的趨勢。
            * **紅吞 / 黑吞**：K線型態出現紅K吞噬黑K (主力拉抬轉強) 或 黑K吞噬紅K (主力倒貨轉弱)。
            """)

        st.divider()
        st.subheader("⭐ 自選群組管理")
        all_groups = list(st.session_state.fav_groups.keys())
        current_groups = [g for g, s in st.session_state.fav_groups.items() if target in [normalize_ticker(x) for x in s]]
        if current_groups:
            st.caption("目前所在群組：" + "、".join(current_groups))
        else:
            st.caption("目前尚未加入任何自選群組")
        new_group_name = st.text_input("新增群組名稱", placeholder="例如：短線觀察、波段核心", key=f"new_group_{target}")
        selected_groups = st.multiselect("將此標的加入以下群組：", options=all_groups, default=current_groups)
        
        if st.button("💾 儲存自選設定", use_container_width=True, type="primary"):
            new_fav = {k: list(v) for k, v in st.session_state.fav_groups.items()}
            if new_group_name.strip():
                group_name = new_group_name.strip()
                if group_name not in new_fav:
                    new_fav[group_name] = []
                if group_name not in selected_groups:
                    selected_groups.append(group_name)
            for g in all_groups:
                normalized_members = [normalize_ticker(x) for x in new_fav[g]]
                if g in selected_groups and target not in normalized_members: new_fav[g].append(target)
                elif g not in selected_groups and target in normalized_members: new_fav[g] = [x for x in new_fav[g] if normalize_ticker(x) != target]
            for g in selected_groups:
                if g not in new_fav:
                    new_fav[g] = []
                if target not in [normalize_ticker(x) for x in new_fav[g]]:
                    new_fav[g].append(target)
            st.session_state.fav_groups = new_fav
            save_cloud_data("user_settings", "fav_groups", new_fav)
            st.success("✅ 群組設定已成功寫入雲端！")
            time.sleep(0.5) 
            st.rerun()

        st.divider()
        st.markdown(f'''<div style="font-size: 1.4rem; font-weight: bold; color: #facc15; margin-bottom: 16px;">同步監控雷達清單</div>''', unsafe_allow_html=True)

        if not st.session_state.get('nav_pool_data'):
            restore_nav_pool()
        if 'nav_pool_data' in st.session_state and len(st.session_state.nav_pool_data) > 0:
            df_nav = pd.DataFrame(st.session_state.nav_pool_data)
            df_nav = df_nav[df_nav['代號'] != target]
            if not df_nav.empty: 
                st.markdown(generate_cards_html(df_nav, st.session_state.get('is_intraday', True)), unsafe_allow_html=True)
            else: 
                st.info("目前雷達清單中已無其他符合條件的標的。")
        else:
            st.info("💡 尚未快取雷達清單。請先至「首頁」執行雷達掃描，即可在此查看並快速切換同步清單。")
    else: 
        st.error("查無此股票資料。")
