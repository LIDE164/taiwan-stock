# 最後修改時間: 2026-07-07 (修復群組顯示、分數同步、雷達清單與營收籌碼回補防呆版)
import firebase_admin
from firebase_admin import credentials, firestore
import yfinance as yf
import streamlit as st
import pandas as pd
import requests
import time
from datetime import datetime, timezone, timedelta
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import re
import concurrent.futures
import numpy as np
import logging
from streamlit_autorefresh import st_autorefresh

# 引入自訂繪圖函式與共用大腦核心演算法
from analysis_core import BACKTEST_LOOKBACK_DAYS, apply_technical_indicators, calculate_historical_performance, calculate_historical_winrate
from charts import draw_professional_chart
from scoring import get_decision_score

logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(levelname)s - %(message)s')

FINMIND_TOKEN = st.secrets["FINMIND_TOKEN"]
FUGLE_API_KEY = st.secrets["FUGLE_API_KEY"]
LIVE_SCORE_CACHE_SECONDS = 30
POST_ANALYSIS_CACHE_SECONDS = 21600

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

if st.sidebar.button("🗑️ 強制清除快取資料", use_container_width=True):
    st.cache_data.clear()
    if "scan_results" in st.session_state: del st.session_state["scan_results"]
    st.sidebar.success("已清除暫存，請重整網頁！")

bg_col = "#ffffff" if is_light_mode else "#0b1120"
border_col = "#ddd" if is_light_mode else "#1e293b"
text_col = "#333" if is_light_mode else "#e2e8f0"
app_bg = "#f4f6f9" if is_light_mode else "#0b1120"
panel_bg = "#0f172a"
panel_border = "#1e293b"
muted_text = "#64748b"
soft_text = "#94a3b8"

css_style = f"""
<style>
    .stApp {{ background-color: {app_bg}; overflow-x: hidden; }}
    #MainMenu {{visibility: hidden;}} footer {{visibility: hidden;}}
    [data-testid="collapsedControl"] {{ border: 1px solid {border_col} !important; border-radius: 8px !important; background-color: {bg_col} !important; padding: 5px 12px !important; display: flex !important; align-items: center !important; width: auto !important; transition: 0.3s; z-index: 1000; }}
    [data-testid="collapsedControl"]::after {{ content: " ⭐ 我的群組"; font-size: 1.1rem; font-weight: bold; color: #ffcc00; margin-left: 8px; }}
    a.stock-card-link {{ text-decoration: none; color: inherit; display: block; }}
    .radar-panel {{ background-color: {panel_bg}; border: 1px solid {panel_border}; border-radius: 12px; padding: 14px; margin-bottom: 12px; color: #e2e8f0; }}
    .radar-metric {{ background-color: {panel_bg}; border: 1px solid {panel_border}; border-radius: 12px; padding: 16px; text-align: center; color: #e2e8f0; min-height: 92px; }}
    .radar-label {{ color: {soft_text}; font-size: 0.85rem; font-weight: 700; }}
    .radar-subtle {{ color: {muted_text}; }}
</style>
"""
st.markdown(css_style, unsafe_allow_html=True)

STOCK_NAMES = { "2330": "台積電", "2317": "鴻海", "2454": "聯發科", "2308": "台達電", "2382": "廣達", "3231": "緯創", "2891": "中信金"}

@st.cache_data(ttl=86400)
def get_all_tw_stock_names():
    names = STOCK_NAMES.copy()
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", timeout=5)
        if res.status_code == 200:
            for i in res.json(): names[i['Code']] = i['Name']
        res2 = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes", timeout=5)
        if res2.status_code == 200:
            for i in res2.json(): names[i['SecuritiesCompanyCode']] = i['CompanyName']
    except: pass
    return names

CURRENT_STOCK_NAMES = get_all_tw_stock_names()

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
    with container.container():
        st.title("⭐ 我的自選群組")
        fav_groups = st.session_state.get('fav_groups', {})
        if fav_groups:
            for g_name, g_stocks in fav_groups.items():
                stocks = [normalize_ticker(s) for s in g_stocks]
                with st.expander(f"📁 {g_name} ({len(stocks)} 檔)"):
                    for s in stocks:
                        s_name = get_stock_name(s)
                        st.markdown(f"- <a href='/?stock={s}' target='_self' style='text-decoration:none; color:{text_col}; font-weight:bold;'>{s} {s_name}</a>", unsafe_allow_html=True)
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
auto_refresh = st.sidebar.toggle("🟢 開啟自動更新 (每30秒)", False)
if auto_refresh: st_autorefresh(interval=30000, limit=None)

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
    except Exception as e: logging.error(f"Firebase 初始化失敗: {e}")

try: db = firestore.client()
except: db = None

def load_cloud_data(collection_name, document_name, default_data):
    if db is None: return default_data
    try:
        doc = db.collection(collection_name).document(document_name).get()
        if doc.exists: return doc.to_dict().get('data', default_data)
    except: pass
    return default_data

def save_cloud_data(collection_name, document_name, data):
    if db is None: return
    try: db.collection(collection_name).document(document_name).set({'data': data})
    except: pass

def load_analysis_cache(ticker, max_age_seconds=900):
    cached = load_cloud_data("analysis_cache", normalize_ticker(ticker), None)
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
    if db is None or not isinstance(payload, dict):
        return
    compact = dict(payload)
    compact["saved_at"] = datetime.now(timezone(timedelta(hours=8))).isoformat()
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

if 'page' not in st.session_state: st.session_state.page = "home"
if 'current_stock' not in st.session_state: st.session_state.current_stock = "2330"
if 'view_days' not in st.session_state: st.session_state.view_days = BACKTEST_LOOKBACK_DAYS
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
    q_stock = st.query_params['stock']
    q_mode = str(st.query_params.get('mode', '')).lower()
    if q_mode in ("intraday", "realtime"):
        _, q_score_mode_label, q_is_intraday = resolve_score_mode(True)
        st.session_state.is_intraday = q_is_intraday
        st.session_state.score_mode_label = q_score_mode_label
    if st.session_state.get('last_q_stock') != q_stock:
        st.session_state.current_stock = q_stock
        st.session_state.page = "analysis"
        st.session_state.date_offset = 0
        st.session_state.last_q_stock = q_stock

ENG_TO_TW_INDUSTRY = {
    "Semiconductors": "半導體", "Consumer Electronics": "消費性電子", "Electronic Components": "電子零組件",
    "Computer Hardware": "電腦及週邊設備", "Marine Shipping": "航運業", "Financial Services": "金融業",
    "Building Materials": "玻璃陶瓷", "Electrical Equipment & Parts": "電機機械", "Software - Entertainment": "文化創意", 
    "Technology": "電子科技", "Industrials": "工業", "Basic Materials": "原物料", "Consumer Cyclical": "非必需消費品", 
    "Healthcare": "生技醫療", "Real Estate": "建材營造", "Utilities": "公用事業", "Energy": "能源", 
    "Communication Services": "通信網路", "Auto Parts": "汽車工業", "Chemicals": "化學工業", 
    "Textile Manufacturing": "紡織纖維", "Food": "食品工業", "Steel": "鋼鐵工業", "Rubber": "橡膠工業", 
    "Plastics": "塑膠工業", "Biotechnology": "生技醫療", "Specialty Retail": "貿易百貨", "Consumer Defensive": "核心消費品"
}

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_twse_index_history():
    try:
        df = yf.Ticker("^TWII").history(period="1y")
        if not df.empty:
            df.index = pd.to_datetime(df.index.strftime('%Y-%m-%d'))
            df = df[~df.index.duplicated(keep='last')]
            return df[['Open', 'High', 'Low', 'Close', 'Volume']]
    except: return None

@st.cache_data(ttl=60, show_spinner=False) 
def get_stock_data(ticker_number):
    base_ticker = str(ticker_number).strip().upper().replace(".TW", "").replace(".TWO", "")
    def fetch_clean(sym):
        try:
            d = yf.Ticker(sym).history(period="1y").dropna(subset=['Close'])
            if len(d) >= 20: 
                d.index = pd.to_datetime(d.index.strftime('%Y-%m-%d'))
                d = d[~d.index.duplicated(keep='last')]
                return d
        except: return None

    df = fetch_twse_index_history() if base_ticker == "^TWII" else fetch_clean(f"{base_ticker}.TW")
    if df is None and base_ticker != "^TWII": df = fetch_clean(f"{base_ticker}.TWO")
    if df is None: return None
    
    try:
        market_state = get_market_state()
        if base_ticker != "^TWII" and market_state == "open":
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
        
        if ind == "一般產業":
            res_cnyes = requests.get(f"https://ws.cnyes.com/twstock/api/v1/company/profile/{base_ticker}", timeout=3).json()
            if 'data' in res_cnyes and 'categoryName' in res_cnyes['data']: ind = res_cnyes['data']['categoryName']
            
        if 'trailingEps' in info and info['trailingEps'] is not None: eps_val = str(round(info['trailingEps'], 2))
    except: pass
    
    if eps_val == "無":
        try:
            res_api = requests.get(f"https://ws.cnyes.com/twstock/api/v1/company/profile/{base_ticker}", timeout=3).json()
            if 'data' in res_api and 'eps' in res_api['data']: eps_val = f"{float(res_api['data']['eps']):.2f}"
        except: pass

    if eps_val != "無" and current_price > 0:
        try: pe_val = str(round(float(current_price) / float(eps_val), 2)) if float(eps_val)>0 else "虧損"
        except: pass
    return {"EPS": eps_val, "PE": pe_val, "Industry": ind}

@st.cache_data(ttl=86400, show_spinner=False)
def get_finmind_chip_and_revenue(ticker):
    big_player_ratio, mom, yoy = 0.0, 0.0, 0.0
    base_ticker = str(ticker).strip().upper().replace(".TW", "").replace(".TWO", "")
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

@st.cache_data(ttl=60, show_spinner=False)
def get_txf_quote():
    for symbol in ["TXF.TW", "FITX.TW", "TX=F"]:
        try:
            df = yf.Ticker(symbol).history(period="5d").dropna(subset=['Close'])
            if len(df) >= 2:
                curr = float(df['Close'].iloc[-1])
                prev = float(df['Close'].iloc[-2])
                if 10000 < curr < 100000 and 10000 < prev < 100000:
                    return curr, curr - prev, symbol, df.index[-1].strftime('%Y/%m/%d')
        except Exception:
            pass
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
                    return curr, curr - prev, "FinMind TX", str(df["date"].iloc[-1])
    except Exception:
        pass
    return None, None, "資料源受限", "暫無資料"

@st.cache_data(ttl=5, show_spinner=False)
def get_stock_live_time(ticker): return datetime.now(timezone(timedelta(hours=8))).strftime('%Y/%m/%d %H:%M:%S')

@st.cache_data(ttl=3600, show_spinner=False)
def get_institutional_trading(ticker):
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
    for t, url in {"^SOX": "https://finance.yahoo.com/quote/^SOX", "^VIX": "https://finance.yahoo.com/quote/^VIX", "TWD=X": "https://finance.yahoo.com/quote/TWD=X", "TX=F": "https://finance.yahoo.com/quote/TX=F"}.items():
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
        txf_close, txf_change, txf_symbol, txf_time = get_txf_quote()
        twii_color = '#ef4444' if twii_change >= 0 else '#22c55e'
        txf_available = txf_close is not None and txf_change is not None
        txf_color = '#ef4444' if (txf_change or 0) >= 0 else '#22c55e'
        txf_price_text = f"{txf_close:,.0f}" if txf_available else "資料源受限"
        txf_change_text = f"{'↑' if txf_change > 0 else '↓'} {abs(txf_change):.0f}" if txf_available else "請改用 FinMind/券商源"
        twii_df_for_pred = get_stock_data("^TWII")
        today_title, today_desc, tmr_title, tmr_desc, last_dt_str, next_dt_str, risk_score, macro = open_pred_logic(twii_df_for_pred, twii_close, twii_change, twii_time_str)
        
        with st.container(border=False):
            st.markdown("<div style='background-color:#0f172a; border:1px solid #1e293b; border-radius:12px; padding:16px; margin-bottom:14px;'>", unsafe_allow_html=True)
            col1, col2, col3 = st.columns([1, 1, 1.7])
            with col1:
                st.markdown(f"<div style='text-align: center; font-size: 0.85rem; color:#94a3b8; font-weight: bold;'>台灣加權指數</div><div style='text-align: center; font-size: 2rem; font-weight: 900; color: {twii_color}; margin: 0;'>{twii_close:,.0f}</div><div style='text-align: center; font-size: 0.95rem; font-weight: bold; color: {twii_color};'>{'↑' if twii_change > 0 else '↓'} {abs(twii_change):.0f}</div>", unsafe_allow_html=True)
            with col2:
                st.markdown(f"<div style='text-align: center; font-size: 0.85rem; color:#94a3b8; font-weight: bold;'>台指期 ({txf_symbol})</div><div style='text-align: center; font-size: 1.7rem; font-weight: 900; color: {txf_color}; margin: 0;'>{txf_price_text}</div><div style='text-align: center; font-size: 0.8rem; font-weight: bold; color: {txf_color};'>{txf_change_text}</div>", unsafe_allow_html=True)
            with col3:
                st.markdown(f"<div style='text-align: left; color: #facc15; font-size: 1.05rem; font-weight: bold;'>📝 盤勢分析 ({last_dt_str})</div><div style='font-size: 1.1rem; font-weight: bold;'>{today_title}</div><div style='font-size: 0.85rem; line-height: 1.4;'>{today_desc}</div>", unsafe_allow_html=True)
                st.markdown(f"<div style='text-align: left; color: #60a5fa; font-size: 1.05rem; font-weight: bold; margin-top:8px;'>🔮 次日開盤預測 ({next_dt_str})</div><div style='font-size: 1.1rem; font-weight: bold;'>{tmr_title}</div><div style='font-size: 0.85rem; line-height: 1.4;'>{tmr_desc}</div>", unsafe_allow_html=True)
            st.markdown("</div>", unsafe_allow_html=True)
            if st.button("🔄 手動更新即時大盤報價", use_container_width=True): st.cache_data.clear(); st.rerun()
        
        bar_color = "#22c55e" if risk_score < 40 else ("#facc15" if risk_score < 70 else "#ef4444")
        st.markdown(f"<h4 style='margin-top:20px; text-align:center;'>🌍 全球總經與次日風險：<span style='color:{bar_color};'>{risk_score}%</span></h4>", unsafe_allow_html=True)
        st.markdown(f"<div style='width:100%; height:12px; background-color:#1e293b; border-radius:6px; overflow:hidden; margin: 10px 0;'><div style='width: {risk_score}%; height:100%; background-color: {bar_color}; transition: width 0.5s;'></div></div>", unsafe_allow_html=True)
        
        mc1, mc2, mc3, mc4 = st.columns(4)
        sox = macro.get('^SOX', {"price": None, "pct": None})
        vix = macro.get('^VIX', {"price": None, "pct": None})
        twd = macro.get('TWD=X', {"price": None, "pct": None})
        def fmt_macro(item, digits=2):
            price, pct = item.get("price"), item.get("pct")
            if price is None or pct is None:
                return "資料缺失", "資料缺失", "#94a3b8"
            return f"{price:,.{digits}f}", f"{'+' if pct > 0 else ''}{pct:.2f}%", "#ef4444" if pct >= 0 else "#22c55e"
        
        with mc1.container(border=True):
            sox_price, sox_pct_text, sox_col = fmt_macro(sox, 1)
            st.markdown(f"<div style='text-align:center; font-size:0.85rem;'>費城半導體</div><div style='text-align:center; font-size:1.1rem; font-weight:bold; color:{sox_col};'>{sox_price}<br>{sox_pct_text}</div>", unsafe_allow_html=True)
        with mc2.container(border=True):
            vix_price, vix_pct_text, vix_col_raw = fmt_macro(vix, 2)
            vix_col = "#22c55e" if vix.get("pct") is not None and vix.get("pct") <= 0 else vix_col_raw
            st.markdown(f"<div style='text-align:center; font-size:0.85rem;'>VIX 恐慌指數</div><div style='text-align:center; font-size:1.1rem; font-weight:bold; color:{vix_col};'>{vix_price}<br>{vix_pct_text}</div>", unsafe_allow_html=True)
        with mc3.container(border=True):
            twd_price, _, _ = fmt_macro(twd, 3)
            twd_text = "資料缺失" if twd.get("pct") is None else ("台幣貶值" if twd.get("pct") > 0 else "台幣升值")
            st.markdown(f"<div style='text-align:center; font-size:0.85rem;'>美元/台幣</div><div style='text-align:center; font-size:1.1rem; font-weight:bold; color:#facc15;'>{twd_price}<br>{twd_text}</div>", unsafe_allow_html=True)
        with mc4.container(border=True):
            st.markdown(f"<div style='text-align:center; font-size:0.85rem;'>台指期</div><div style='text-align:center; font-size:1.1rem; font-weight:bold; color:{txf_color};'>{txf_price_text}<br>{('+' if txf_available and txf_change>0 else '') + (f'{txf_change:.0f}' if txf_available else '受限')}</div>", unsafe_allow_html=True)
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

def calculate_historical_winrate_interactive(df_slice, target_mult, stop_mult):
    result = calculate_historical_performance(df_slice, target_mult, stop_mult)
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
    cards_html = ""
    favorite_set = get_favorite_stock_set()
    simulated_set = get_simulated_order_stock_set()
    for _, r in df_disp.iterrows():
        p_val = r.get('漲跌', 0)
        p_col = "#ef4444" if p_val >= 0 else "#22c55e"
        p_bg = "rgba(239,68,68,0.1)" if p_val >= 0 else "rgba(34,197,94,0.1)"
        change_sign = "+" if p_val > 0 else ""
        
        score = r.get('Score', 0)
        s_col = "#ef4444" if score >= 60 else ("#facc15" if score >= 45 else "#22c55e")
        rating = r.get('評級', '⚪ 忽略').replace('🟢 ', '').replace('🟡 ', '').replace('⚪ ', '')
        score_mode = r.get('Score_Mode', st.session_state.get('score_mode_label', '盤後正式分數'))
        score_source = r.get('Score_Source', '')
            
        r_col = "#4ade80" if "強勢" in rating else ("#facc15" if "偏多" in rating else "#94a3b8")
        ticker_code = normalize_ticker(r.get("代號", ""))
        mode_param = "&mode=intraday" if is_intraday or is_realtime_score_record(r.to_dict() if hasattr(r, "to_dict") else r) else ""
        stock_link = f'href="/?stock={ticker_code}{mode_param}" target="_self"'
        
        disp_name = r.get('名稱', '')
        if not disp_name or disp_name == "": disp_name = get_stock_name(r.get("代號", ""))
        fav_mark = " ⭐" if ticker_code in favorite_set else ""
        sim_mark = " 🛒" if ticker_code in simulated_set else ""
        
        cards_html += f"<div style='background-color: #0f172a; border: 1px solid #1e293b; border-radius: 12px; padding: 14px; margin-bottom: 12px; position: relative; overflow: hidden;'>"
        cards_html += f"<div style='display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; position: relative; z-index: 10;'>"
        cards_html += f"<div style='display: flex; align-items: center; gap: 12px;'>"
        cards_html += f"<div style='width: 50px; height: 50px; border-radius: 50%; background: radial-gradient(circle, #1e293b 0%, #0b1120 100%); border: 1px solid #334155; display: flex; flex-direction: column; align-items: center; justify-content: center; flex-shrink: 0; box-shadow: inset 0 2px 4px rgba(255,255,255,0.05), 0 4px 8px rgba(0,0,0,0.4);'>"
        cards_html += f"<span style='color: {s_col}; font-weight: 800; font-size: 1.2rem; line-height: 1;'>{score}</span>"
        cards_html += f"<span style='color: {r_col}; font-size: 0.65rem; font-weight: 800; margin-top: 2px;'>{rating}</span></div>"
        
        cards_html += f"<a {stock_link} class='stock-card-link'><div style='display: flex; align-items: center; gap: 6px;'>"
        cards_html += f"<span class='stock-name-hover' style='color: #f8fafc; font-weight: bold; font-size: 1.15rem; transition: color 0.2s;'>{disp_name}{fav_mark}{sim_mark}</span>"
        
        industry_name = r.get("產業", "一般產業")
        cards_html += f"<span style='font-size: 0.7rem; background-color: rgba(79,70,229,0.15); color: #818cf8; border: 1px solid rgba(79,70,229,0.3); padding: 2px 6px; border-radius: 4px; white-space: nowrap; font-weight: 600;'>🏷️ {industry_name}</span>"
        
        cards_html += f"</div><div style='font-size: 0.8rem; color: #64748b; margin-top: 4px; font-family: monospace;'>{r.get('代號', '')} <span style='color:#475569; font-size:0.7rem; margin-left:4px;'>(點擊解析)</span></div></a></div>"
        
        cards_html += f"<div style='text-align: right; flex-shrink: 0;'><div style='color: {p_col}; font-weight: 800; font-size: 1.2rem; font-family: monospace;'>{r.get('收盤價', 0):.1f}</div>"
        cards_html += f"<div style='background-color: {p_bg}; color: {p_col}; font-size: 0.75rem; padding: 2px 6px; border-radius: 4px; display: inline-block; font-weight: 800; font-family: monospace; margin-top: 4px;'>{change_sign}{r.get('漲跌幅', 0)}%</div></div></div>"
        
        wr_val = r.get('WinRate', 0.0)
        wr_col = "#ef4444" if wr_val >= 60 else ("#facc15" if wr_val >= 40 else "#22c55e")
        confidence_val = safe_num(r.get('Confidence'), 100)
        conf_col = "#4ade80" if confidence_val >= 80 else ("#facc15" if confidence_val >= 60 else "#94a3b8")
        w_net = r.get('Whale_Net', 0)
        w_col = "#ef4444" if w_net > 0 else ("#22c55e" if w_net < 0 else "#94a3b8")
        whale_str = f"+{w_net:,}" if w_net > 0 else f"{w_net:,}"
        
        cards_html += f"<div style='display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; background-color: rgba(30,41,59,0.4); border: 1px solid rgba(51,65,85,0.5); padding: 10px; border-radius: 8px; font-size: 0.75rem; margin-bottom: 10px; position: relative; z-index: 10;'>"
        cards_html += f"<div style='display: flex; flex-direction: column;'><span style='color: #64748b; margin-bottom: 4px;'>歷史勝率</span><span style='color: {wr_col}; font-weight: bold; font-family: monospace;'>{wr_val}%</span></div>"
        cards_html += f"<div style='display: flex; flex-direction: column;'><span style='color: #64748b; margin-bottom: 4px;'>資料信心</span><span style='color: {conf_col}; font-weight: bold; font-family: monospace;'>{confidence_val:.0f}%</span></div>"
        cards_html += f"<div style='display: flex; flex-direction: column;'><span style='color: #64748b; margin-bottom: 4px;'>法人淨買</span><span style='color: {w_col}; font-weight: bold; font-family: monospace;'>{whale_str}</span></div></div>"
        cards_html += f"<div style='font-size: 0.75rem; color: #fbbf24; display: flex; align-items: flex-start; gap: 6px; position: relative; z-index: 10;'><span style='margin-top: 1px;'>⚡</span><span style='line-height: 1.4; font-weight: 500;'>進場特徵：{r.get('Feature', '一般')}</span></div>"
        source_text = f"{score_mode}｜{score_source}" if score_source else score_mode
        cards_html += f"<div style='font-size:0.72rem; color:#64748b; margin-top:6px;'>分數來源：{source_text}</div>"
        
        cards_html += f"</div>"
    return cards_html

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
            
    if st.session_state.scan_results:
        fetch_time = datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
        st.markdown(f"<div style='font-size:0.85rem; color:#64748b; margin-bottom:15px; font-weight:bold;'>☁️ 最新雲端資料庫讀取時間：{fetch_time}</div>", unsafe_allow_html=True)

        col_m1, col_m2 = st.columns([1, 1])
        with col_m1: radar_mode = st.radio("引擎模式：", ["盤後波段精算", "盤中動能快篩"], horizontal=True, label_visibility="collapsed")
        with col_m2: only_favorites = st.toggle("⭐ 只看自選群組", value=False)
        requested_intraday = "盤中" in radar_mode
        score_mode, score_mode_label, is_intraday = resolve_score_mode(requested_intraday)
        st.session_state.is_intraday = is_intraday
        st.session_state.score_mode_label = score_mode_label
        if requested_intraday and not is_intraday:
            st.caption("目前非台股交易時段，系統已自動改採盤後正式分數。")
        
        cached_list = list(st.session_state.get('scan_results', []))
        cloud_count = len(cached_list)
        
        if is_intraday:
            with st.spinner("⚡ 混合動力引擎啟動：即時運算 100 分模型 (約需 3-5 秒)..."):
                fb_df = pd.DataFrame(cached_list)
                targets = list(set([str(t) for t in fb_df['代號'].tolist()[:200]] + st.session_state.custom_pool))
                live_data = []
                
                def process_live(ticker):
                    df = get_stock_data(ticker)
                    if df is not None:
                        base = next((x for x in cached_list if str(x['代號']) == str(ticker)), None)
                        analysis_cache = load_analysis_cache(ticker, LIVE_SCORE_CACHE_SECONDS)
                        cached_data = analysis_cache.get("data") if analysis_cache else None
                        if isinstance(cached_data, dict) and cached_data.get("Score_Mode_Raw") == "realtime":
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
                        res = analyze_today(df, ticker, inst_data, False, fund, cached_doc=base, is_intraday=True)
                        if res:
                            res["Score_Source"] = "盤中重算"
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
        selected_theme = st.radio("產業過濾：", available_themes, horizontal=True, label_visibility="collapsed")
        if selected_theme != "全部產業": df_results = df_results[df_results['產業'] == selected_theme]
        industry_count = len(df_results)
        sort_mode = st.radio("排序：", ["AI分數", "歷史勝率", "資料信心"], horizontal=True, label_visibility="collapsed")
            
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
                st.markdown(generate_cards_html(df_disp, is_intraday), unsafe_allow_html=True)
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
        if st.button("🏠 回雷達總機", use_container_width=True): st.session_state.page = "home"; st.rerun()
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
        if st.button("🏠 回雷達總機", use_container_width=True): st.session_state.page = "home"; st.rerun()
    with c3:
        if n_stk and st.button(f"下一檔 ➡", use_container_width=True): st.session_state.update({"current_stock": n_stk}); st.rerun()

    def set_view_days(days): st.session_state.view_days = days
    
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
        p_color = '#ef4444' if data['漲跌幅'] >= 0 else '#22c55e'
        
        st.markdown(f"<h2 style='text-align: center; margin-bottom: 5px;'>🎯 {target} {c_name}</h2>", unsafe_allow_html=True)
        st.markdown(f"<div style='text-align: center; color: #888; font-size: 1.1rem;'>【{data['產業']}】</div>", unsafe_allow_html=True)
        st.markdown(f"<h3 style='text-align: center; color: {p_color}; font-size: 2.2rem; margin-bottom: 0px;'>{data['收盤價']} ({'+' if data['漲跌幅']>0 else ''}{data['漲跌幅']}%)</h3>", unsafe_allow_html=True)
        score_source_text = f"　｜　來源：<b>{data.get('Score_Source')}</b>" if data.get("Score_Source") else ""
        st.markdown(f"<div style='text-align: center; color: #888; font-size: 0.9rem; margin-bottom: 10px;'>🕒 抓取時間: {display_time}　｜　採用：<b>{data.get('Score_Mode', '盤後正式分數')}</b>{score_source_text}</div>", unsafe_allow_html=True)
        
        _, up_c, _ = st.columns([1, 2, 1])
        force_refresh_analysis = up_c.button("🔄 更新個股即時數值", use_container_width=True)
        if force_refresh_analysis:
            st.session_state[force_key] = True
            st.cache_data.clear()
            st.rerun()
        st.markdown("---")
        
        st.markdown("##### 🧪 策略回測實驗室 (自由調配風報比)")
        with st.expander("⚙️ 調整停利/停損參數 (預設風報比 1:1.5)", expanded=True):
            s_col1, s_col2 = st.columns(2)
            with s_col1: atr_stop_mult = st.slider("🛑 停損區間 (ATR倍數)", 0.5, 3.0, 1.0, 0.1)
            with s_col2: atr_target_mult = st.slider("💰 停利目標 (ATR倍數)", 0.5, 5.0, 1.5, 0.1)
            dynamic_rrr = round(atr_target_mult / atr_stop_mult, 1) if atr_stop_mult > 0 else 0
            st.markdown(f"<div style='text-align:right; color:#34d399; font-weight:bold; font-size:0.9rem;'>當前配置風報比 (RRR) = 1 : {dynamic_rrr}</div>", unsafe_allow_html=True)

        backtest_df = df_slice.tail(st.session_state.view_days)
        win_rate, closed_signals, wins, buy_dates, backtest_stats = calculate_historical_winrate_interactive(backtest_df, atr_target_mult, atr_stop_mult)
        if use_cached_list_score and atr_target_mult == 1.5 and atr_stop_mult == 1.0:
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

        wr_color = "#ef4444" if win_rate >= 60 else ("#facc15" if win_rate >= 40 else "#22c55e")
        with st.container(border=True):
            col_sum1, col_sum2, col_sum3 = st.columns(3)
            with col_sum1: st.markdown(f"<div style='text-align:center; color:#888; font-size:0.9rem;'>歷史勝率<br><span style='color:{wr_color}; font-size:1.8rem; font-weight:900;'>{win_rate:.1f}%</span></div>", unsafe_allow_html=True)
            with col_sum2: st.markdown(f"<div style='text-align:center; color:#888; font-size:0.9rem;'>歷史觸發買點<br><span style='font-size:1.8rem; font-weight:900; color:#e2e8f0;'>{closed_signals} 次</span></div>", unsafe_allow_html=True)
            with col_sum3: st.markdown(f"<div style='text-align:center; color:#888; font-size:0.9rem;'>成功達標獲利<br><span style='font-size:1.8rem; font-weight:900; color:#ef4444;'>{wins} 次</span></div>", unsafe_allow_html=True)
            avg_ret = backtest_stats.get('avg_return', 0.0)
            max_dd = backtest_stats.get('max_drawdown', 0.0)
            max_loss_streak = backtest_stats.get('max_consecutive_losses', 0)
            avg_col = "#ef4444" if avg_ret > 0 else ("#22c55e" if avg_ret < 0 else "#94a3b8")
            st.markdown(
                f"<div style='display:grid; grid-template-columns: repeat(3, 1fr); gap:8px; background-color:rgba(30,41,59,0.4); border:1px solid rgba(51,65,85,0.5); padding:10px; border-radius:8px; margin-top:12px;'>"
                f"<div style='display:flex; flex-direction:column; align-items:center;'><span style='color:#64748b; font-size:0.8rem;'>平均報酬</span><span style='color:{avg_col}; font-weight:800; font-family:monospace;'>{avg_ret:+.2f}%</span></div>"
                f"<div style='display:flex; flex-direction:column; align-items:center;'><span style='color:#64748b; font-size:0.8rem;'>最大回撤</span><span style='color:#22c55e; font-weight:800; font-family:monospace;'>-{max_dd:.2f}%</span></div>"
                f"<div style='display:flex; flex-direction:column; align-items:center;'><span style='color:#64748b; font-size:0.8rem;'>連續虧損</span><span style='color:#facc15; font-weight:800; font-family:monospace;'>{max_loss_streak} 次</span></div>"
                f"</div>",
                unsafe_allow_html=True
            )
            
            summary_text = f"在自訂風報比 `1 : {dynamic_rrr}` 之下，目前圖表區間 {len(backtest_df)} 日共觸發 {closed_signals} 次買點，歷史勝率為 <span style='color:{wr_color}; font-weight:bold;'>{win_rate:.1f}%</span>，平均單筆報酬 <span style='color:{avg_col}; font-weight:bold;'>{avg_ret:+.2f}%</span>。此勝率已依樣本數做保守修正。" if closed_signals > 0 else f"目前圖表區間 {len(backtest_df)} 日內此策略尚未產生足夠的歷史買進訊號。"
            st.markdown(f"<div style='margin-top:12px; padding:12px; background-color:rgba(30,41,59,0.5); border-radius:8px; line-height: 1.6; font-size:0.95rem; color:#cbd5e1;'>📝 <b>回測總結：</b>{summary_text}</div>", unsafe_allow_html=True)

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
        
        dc1, dc2, dc3, dc5, dc6, dc7 = st.columns([0.8, 0.8, 0.8, 1.3, 1.3, 1.3])
        dc1.button("30日", on_click=set_view_days, args=(30,))
        dc2.button("60日", on_click=set_view_days, args=(60,))
        dc3.button("90日", on_click=set_view_days, args=(90,))
        with dc5: current_show_buy = st.toggle("🛒 顯示買進", value=True)
        with dc6: current_show_sup = st.toggle("📏 歷史高低點", value=True)
        with dc7: current_show_signals = st.toggle("🏷️ 顯示符號", value=True)
        
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
