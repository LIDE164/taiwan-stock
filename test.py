# 最後修改時間: 2026-07-05 (優化前端：全面改為雲端資料庫秒開模式)
import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore
import yfinance as yf
import streamlit as st
import pandas as pd
import requests
import time
from datetime import datetime, timezone, timedelta
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import json
import os
import urllib.parse
import xml.etree.ElementTree as ET
import streamlit.components.v1 as components
from bs4 import BeautifulSoup
import re
import concurrent.futures
import numpy as np
import logging
import random
# import pandas_ta as ta

from streamlit_autorefresh import st_autorefresh

# 設定日誌系統，避免發生靜默錯誤
logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(levelname)s - %(message)s')

# === 雙引擎 API 憑證 (安全雲端讀取) ===
FINMIND_TOKEN = st.secrets["FINMIND_TOKEN"]
FUGLE_API_KEY = st.secrets["FUGLE_API_KEY"]

# ==========================================
# 0. 系統初始化與風格設定
# ==========================================
st.set_page_config(page_title="專業交易雷達", layout="wide", initial_sidebar_state="collapsed")

# 🚀 PWA 獨立 APP 宣告
st.markdown('''
<head>
    <link rel="manifest" href="/manifest.json">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <meta name="apple-mobile-web-app-title" content="交易雷達">
    <link rel="apple-touch-icon" href="https://raw.githubusercontent.com/LIDE164/taiwan-stock/main/logo.png">
</head>
''', unsafe_allow_html=True)

st.sidebar.title("⚙️ 介面設定")
is_light_mode = st.sidebar.toggle("🌞 黑白底色切換", False, key="toggle_theme_mode")

if st.sidebar.button("🗑️ 強制清除快取資料", use_container_width=True, key="btn_clear_cache"):
    st.cache_data.clear()
    if "scan_results" in st.session_state: del st.session_state["scan_results"]
    st.sidebar.success("已清除暫存，請重整網頁！")

bg_col = "#ffffff" if is_light_mode else "#0b1120"
border_col = "#ddd" if is_light_mode else "#1e293b"
text_col = "#333" if is_light_mode else "#e2e8f0"
title_col = "#111" if is_light_mode else "#fff"
sub_text_col = "#666" if is_light_mode else "#94a3b8"
app_bg = "#f4f6f9" if is_light_mode else "#0b1120"
pill_bg = "#ffffff" if is_light_mode else "#1e293b"
pill_border = "#d1d5db" if is_light_mode else "#334155"
pill_text = "#374151" if is_light_mode else "#94a3b8"
pill_hover = "#f3f4f6" if is_light_mode else "#334155"

css_style = f"""
<style>
    .stApp {{ background-color: {app_bg}; -webkit-tap-highlight-color: transparent; overflow-x: hidden; }}
    #MainMenu {{visibility: hidden;}} footer {{visibility: hidden;}}
    [data-testid="collapsedControl"] {{ border: 1px solid {border_col} !important; border-radius: 8px !important; background-color: {bg_col} !important; padding: 5px 12px !important; display: flex !important; align-items: center !important; width: auto !important; transition: 0.3s; z-index: 1000; }}
    [data-testid="collapsedControl"]::after {{ content: " ⭐ 我的群組"; font-size: 1.1rem; font-weight: bold; color: #ffcc00; margin-left: 8px; }}
    
    div[role="radiogroup"] {{ 
        display: flex; flex-direction: row; gap: 8px; flex-wrap: nowrap !important; 
        overflow-x: auto; -ms-overflow-style: none; scrollbar-width: none; padding-bottom: 5px; margin-bottom: 12px;
    }}
    div[role="radiogroup"]::-webkit-scrollbar {{ display: none; }}
    div[role="radiogroup"] > label {{ margin: 0 !important; padding: 0 !important; background: transparent !important; flex-shrink: 0; }}
    div[role="radiogroup"] label > div:first-child {{ display: none !important; }}
    div[role="radiogroup"] div[data-testid="stMarkdownContainer"] p {{
        background-color: {pill_bg}; border: 1px solid {pill_border}; color: {pill_text} !important;
        padding: 6px 16px; border-radius: 25px; font-size: 0.85rem; font-weight: 600; margin: 0;
        cursor: pointer; transition: all 0.2s ease-in-out;
    }}
    div[role="radiogroup"] label:has(input:checked) div[data-testid="stMarkdownContainer"] p {{
        background-color: #4f46e5 !important; border-color: #4f46e5 !important; color: white !important;
        box-shadow: 0 4px 10px rgba(79, 70, 229, 0.4);
    }}
    a.stock-card-link {{ text-decoration: none; color: inherit; display: block; }}
</style>
"""
st.markdown(css_style, unsafe_allow_html=True)

STOCK_NAMES = { "2330": "台積電", "2317": "鴻海", "2454": "聯發科", "2308": "台達電", "2382": "廣達", "2376": "技嘉", "1802": "台玻", "2603": "長榮", "1785": "光洋科", "1519": "華城", "6147": "頎邦", "2891": "中信金", "9904": "寶成", "1809": "中釉", "1409": "新纖", "3016": "嘉晶" }

@st.cache_data(ttl=86400)
def get_all_tw_stock_names():
    names = STOCK_NAMES.copy()
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", timeout=5)
        res.raise_for_status()
        for i in res.json(): names[i['Code']] = i['Name']
        res2 = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes", timeout=5)
        res2.raise_for_status()
        for i in res2.json(): names[i['SecuritiesCompanyCode']] = i['CompanyName']
    except Exception as e: 
        logging.warning(f"獲取台股全名稱失敗: {e}")
    return names

CURRENT_STOCK_NAMES = get_all_tw_stock_names()

st.sidebar.title("🔍 快速搜尋")
with st.sidebar.form(key="search_form"):
    search_input = st.text_input("隱藏", placeholder="輸入股票代號或中文名稱...", label_visibility="collapsed", key="global_search_input")
    submit_search = st.form_submit_button("送出搜尋", use_container_width=True)
    
if submit_search and search_input:
    s_val = search_input.strip().replace(" ", "")
    if s_val:
        target_ticker = None
        if re.match(r'^[A-Za-z0-9]+$', s_val):
            target_ticker = s_val.upper()
        else:
            for code, name in CURRENT_STOCK_NAMES.items():
                if s_val in name:
                    target_ticker = code
                    break
        if target_ticker:
            st.session_state.current_stock = target_ticker
            st.session_state.page = "analysis"
            st.session_state.date_offset = 0
            st.rerun() 
        else:
            st.sidebar.warning(f"⚠️ 找不到與「{s_val}」相關的標的。")

st.sidebar.divider()
st.sidebar.title("⏱️ 盤中即時跳動雷達")
auto_refresh = st.sidebar.toggle("🟢 開啟即時自動更新 (每30秒)", False, key="auto_refresh_toggle")
if auto_refresh: st_autorefresh(interval=30000, limit=None, key="market_auto_refresh")

st.sidebar.divider()
st.sidebar.title("🛒 模擬交易中心")
if st.sidebar.button("📋 我的模擬下單紀錄", use_container_width=True, key="btn_sidebar_sim_orders"):
    st.session_state.page = "simulated_orders"
    st.rerun()

ENG_TO_TW_INDUSTRY = {
    "Semiconductors": "半導體業", "Consumer Electronics": "消費性電子", "Electronic Components": "電子零組件",
    "Computer Hardware": "電腦及週邊設備", "Building Materials": "玻璃陶瓷", "Marine Shipping": "航運業",
    "Electrical Equipment & Parts": "電機機械", "Software - Entertainment": "文化創意業", "Technology": "電子科技",
    "Industrials": "工業", "Basic Materials": "原物料", "Financial Services": "金融業",
    "Consumer Cyclical": "非必需消費品", "Healthcare": "生技醫療", "Real Estate": "建材營造",
    "Utilities": "公用事業", "Energy": "能源", "Communication Services": "通信網路"
}

@st.cache_data(ttl=86400, show_spinner=False)
def get_real_chinese_name(ticker):
    try:
        res = requests.get(f"https://invest.cnyes.com/twstock/TWS/{ticker}", headers={'User-Agent': 'Mozilla/5.0'}, timeout=3)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, 'html.parser')
        h2 = soup.find('h2')
        if h2:
            name = h2.text.strip()
            if name and not name.isdigit(): return name
    except Exception as e: 
        logging.warning(f"從鉅亨網獲取 {ticker} 名稱失敗: {e}")
    return ""

def get_stock_name(ticker):
    if not ticker: return ""
    ticker_str = str(ticker).strip().upper().replace(".TW", "").replace(".TWO", "")
    name = ""
    if ticker_str in CURRENT_STOCK_NAMES and CURRENT_STOCK_NAMES[ticker_str]: 
        name = CURRENT_STOCK_NAMES[ticker_str]
    elif ticker_str in STOCK_NAMES: 
        name = STOCK_NAMES[ticker_str]
    else:
        html_name = get_real_chinese_name(ticker_str)
        if html_name: 
            STOCK_NAMES[ticker_str] = html_name 
            name = html_name
        else: name = ticker_str
    name = name.replace(ticker_str, "").strip()
    return name

# ==========================================
# ☁️ Firebase 雲端資料庫初始化與讀寫
# ==========================================
if not firebase_admin._apps:
    try:
        cert_dict = dict(st.secrets["firebase"])
        cred = credentials.Certificate(cert_dict)
        firebase_admin.initialize_app(cred)
    except Exception as e:
        logging.error(f"Firebase 初始化失敗: {e}")

try:
    db = firestore.client()
except:
    db = None

def load_cloud_data(collection_name, document_name, default_data):
    """從 Firebase 讀取資料"""
    if db is None: return default_data
    try:
        doc = db.collection(collection_name).document(document_name).get()
        if doc.exists: return doc.to_dict().get('data', default_data)
    except Exception as e:
        logging.error(f"讀取 Firebase 失敗 ({document_name}): {e}")
    return default_data

def save_cloud_data(collection_name, document_name, data):
    """將資料存入 Firebase"""
    if db is None: return
    try:
        db.collection(collection_name).document(document_name).set({'data': data})
    except Exception as e:
        logging.error(f"寫入 Firebase 失敗 ({document_name}): {e}")

# ==========================================
# 全域狀態初始化 (完全移至雲端)
# ==========================================
if 'page' not in st.session_state: st.session_state.page = "home"
if 'current_stock' not in st.session_state: st.session_state.current_stock = "2330"
if 'view_days' not in st.session_state: st.session_state.view_days = 30
if 'date_offset' not in st.session_state: st.session_state.date_offset = 0
if 'is_intraday' not in st.session_state: st.session_state.is_intraday = True

if 'custom_pool' not in st.session_state: 
    st.session_state.custom_pool = load_cloud_data("user_settings", "custom_pool", ["2330", "2317", "2454", "2382", "3231", "2891", "9904", "1809", "0050", "2027", "1409", "3016"])
if 'nav_pool' not in st.session_state: 
    st.session_state.nav_pool = st.session_state.custom_pool

if 'simulated_orders' not in st.session_state:
    st.session_state.simulated_orders = load_cloud_data("user_data", "simulated_orders", [])

if 'fav_groups' not in st.session_state:
    default_groups = {"預設群組": ["1802", "2330", "1785"]}
    st.session_state.fav_groups = load_cloud_data("user_settings", "fav_groups", default_groups)

if 'stock' in st.query_params:
    q_stock = st.query_params['stock']
    if st.session_state.get('last_q_stock') != q_stock:
        st.session_state.current_stock = q_stock
        st.session_state.page = "analysis"
        st.session_state.date_offset = 0
        st.session_state.last_q_stock = q_stock


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_twse_index_history():
    try:
        df = yf.Ticker("^TWII").history(period="1y")
        if not df.empty:
            df.index = pd.to_datetime(df.index.strftime('%Y-%m-%d'))
            return df[['Open', 'High', 'Low', 'Close', 'Volume']]
    except Exception as e: 
        logging.warning(f"獲取加權指數歷史失敗: {e}")
    return None

# ==========================================
# 🚀 Fugle API: 極速盤中報價
# ==========================================
@st.cache_data(ttl=60, show_spinner=False) 
def get_stock_data(ticker_number):
    base_ticker = str(ticker_number).strip().upper().replace(".TW", "").replace(".TWO", "")
    def fetch_clean(sym):
        try:
            d = yf.Ticker(sym).history(period="1y")
            if d is not None and not d.empty:
                d = d.dropna(subset=['Close'])
                if len(d) >= 20: 
                    d.index = pd.to_datetime(d.index.strftime('%Y-%m-%d'))
                    return d
        except Exception as e: 
            logging.info(f"YFinance抓取 {sym} 失敗: {e}")
        return None

    df = fetch_twse_index_history() if base_ticker == "^TWII" else fetch_clean(f"{base_ticker}.TW")
    if df is None and base_ticker != "^TWII": df = fetch_clean(f"{base_ticker}.TWO")
    if df is None: return None
    
    try:
        if base_ticker != "^TWII":
            url = f"https://api.fugle.tw/marketdata/v1.0/stock/intraday/quote/{base_ticker}"
            headers = {'X-API-KEY': FUGLE_API_KEY}
            res = requests.get(url, headers=headers, timeout=3)
            if res.status_code == 200:
                q = res.json()
                c_price = float(q.get('closePrice', q.get('lastPrice', df['Close'].iloc[-1])))
                o_price = float(q.get('openPrice', c_price))
                h_price = float(q.get('highPrice', c_price))
                l_price = float(q.get('lowPrice', c_price))
                v_vol = float(q.get('total', {}).get('tradeVolume', 0))
                
                dt_live = pd.to_datetime(datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d'))
                
                if dt_live not in df.index:
                    new_row = pd.DataFrame({'Open': [o_price], 'High': [h_price], 'Low': [l_price], 'Close': [c_price], 'Volume': [v_vol]}, index=[dt_live])
                    df = pd.concat([df, new_row])
                else:
                    df.at[dt_live, 'Close'] = c_price
                    df.at[dt_live, 'High'] = max(df.at[dt_live, 'High'], h_price)
                    df.at[dt_live, 'Low'] = min(df.at[dt_live, 'Low'], l_price)
                    df.at[dt_live, 'Volume'] = max(df.at[dt_live, 'Volume'], v_vol)
    except Exception as e: 
        logging.warning(f"Fugle API 即時報價失敗 {base_ticker}: {e}")

    try:
        df['5MA'] = df['Close'].rolling(5).mean()
        df['10MA'] = df['Close'].rolling(10).mean()
        df['20MA'] = df['Close'].rolling(20).mean()
        df['60MA'] = df['Close'].rolling(60).mean()
        df['MACD'] = df['Close'].ewm(span=12, adjust=False).mean() - df['Close'].ewm(span=26, adjust=False).mean()
        df['Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
        df['MACD_Hist'] = df['MACD'] - df['Signal']

        df['STD20'] = df['Close'].rolling(20).std()
        df['BB_UP'] = df['20MA'] + (2 * df['STD20'])
        df['BB_DN'] = df['20MA'] - (2 * df['STD20'])
        df['BIAS_20'] = (df['Close'] - df['20MA']) / df['20MA'] * 100
        
        low_9 = df['Low'].rolling(9).min()
        high_9 = df['High'].rolling(9).max()
        rsv = (df['Close'] - low_9) / (high_9 - low_9) * 100
        df['K'] = rsv.ewm(com=2, adjust=False).mean()
        df['D'] = df['K'].ewm(com=2, adjust=False).mean()
        df['J'] = 3 * df['K'] - 2 * df['D']

        tr1 = df['High'] - df['Low']
        tr2 = (df['High'] - df['Close'].shift(1)).abs()
        tr3 = (df['Low'] - df['Close'].shift(1)).abs()
        df['TR'] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        df['ATR'] = df['TR'].rolling(14).mean().bfill()
        
        up_move = df['High'] - df['High'].shift(1)
        down_move = df['Low'].shift(1) - df['Low']
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
        
        plus_di = 100 * (pd.Series(plus_dm, index=df.index).ewm(span=14, adjust=False).mean() / df['ATR'])
        minus_di = 100 * (pd.Series(minus_dm, index=df.index).ewm(span=14, adjust=False).mean() / df['ATR'])
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1)
        df['ADX'] = dx.ewm(span=14, adjust=False).mean().bfill()
    except Exception as e:
        logging.warning(f"自定義指標計算失敗: {e}")
        df['ATR'] = df['Close'] * 0.03
        df['ADX'] = 20
        
    return df

@st.cache_data(ttl=86400, show_spinner=False)
def get_fundamental_and_industry_data(ticker_number, current_price=0):
    base_ticker = str(ticker_number).strip().upper().replace(".TW", "").replace(".TWO", "")
    eps_val, pe_val = "無", "無"
    ind = "一般產業"
    try:
        info = yf.Ticker(f"{base_ticker}.TW").info
        if not info or 'industry' not in info: info = yf.Ticker(f"{base_ticker}.TWO").info
        sec, ind_eng = info.get("sector", ""), info.get("industry", "")
        tw_sec = ENG_TO_TW_INDUSTRY.get(sec, sec)
        tw_ind = ENG_TO_TW_INDUSTRY.get(ind_eng, ind_eng)
        ind_temp = f"{tw_sec} - {tw_ind}" if tw_sec and tw_ind else tw_sec or tw_ind or "一般產業"
        if not re.search(r'[a-zA-Z]', ind_temp): ind = ind_temp
        if 'trailingEps' in info and info['trailingEps'] is not None:
            eps_val = str(round(info['trailingEps'], 2))
    except Exception as e: 
        logging.info(f"獲取基本面失敗: {e}")
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
        start_date_rev = (datetime.now() - timedelta(days=500)).strftime('%Y-%m-%d')
        try:
            url_chip = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockHoldingSharesPer&data_id={base_ticker}&start_date={start_date_chip}&token={FINMIND_TOKEN}"
            res_chip = requests.get(url_chip, timeout=5).json()
            if 'data' in res_chip and len(res_chip['data']) > 0:
                latest_date = max([x.get('date', '') for x in res_chip['data']])
                for x in res_chip['data']:
                    if x.get('date') == latest_date and int(x.get('HoldingSharesLevel', 0)) >= 12:
                        big_player_ratio += float(str(x.get('percent', 0)).replace(',', ''))
        except: pass
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
    try:
        session = requests.Session()
        session.get("https://mis.twse.com.tw/stock/index.jsp", headers={"User-Agent": "Mozilla/5.0"}, timeout=3)
        ts = int(datetime.now(tz_tpe).timestamp() * 1000)
        url = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_t00.tw&json=1&delay=0&_={ts}"
        res = session.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=3).json()
        if 'msgArray' in res and len(res['msgArray']) > 0:
            info = res['msgArray'][0]
            z, y, d, t = info.get('z'), info.get('y'), info.get('d'), info.get('t')
            curr = float(z.replace(',','')) if z and z != '-' else (float(y.replace(',','')) if y and y != '-' else 0)
            prev = float(y.replace(',','')) if y and y != '-' else curr
            if curr > 10000:
                if d and t: update_time_str = f"{d[:4]}/{d[4:6]}/{d[6:]} {t}"
                return curr, curr - prev, update_time_str
    except: pass
    return fallback_curr, fallback_change, update_time_str

@st.cache_data(ttl=5, show_spinner=False)
def get_stock_live_time(ticker):
    return datetime.now(timezone(timedelta(hours=8))).strftime('%Y/%m/%d %H:%M:%S')

@st.cache_data(ttl=3600, show_spinner=False)
def get_institutional_trading(ticker):
    try:
        start_date = (datetime.now() - timedelta(days=20)).strftime('%Y-%m-%d')
        url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInstitutionalInvestorsBuySell&data_id={ticker}&start_date={start_date}&token={FINMIND_TOKEN}"
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
    data = {"global_time": datetime.now(timezone(timedelta(hours=8))).strftime('%Y/%m/%d')}
    for t, url in {"^SOX": "https://finance.yahoo.com/quote/^SOX", "^VIX": "https://finance.yahoo.com/quote/^VIX", "TWD=X": "https://finance.yahoo.com/quote/TWD=X"}.items():
        try:
            df = yf.Ticker(t).history(period="5d").dropna(subset=['Close'])
            if len(df) >= 2:
                data[t] = {"price": float(df['Close'].iloc[-1]), "pct": (df['Close'].iloc[-1]-df['Close'].iloc[-2])/df['Close'].iloc[-2]*100, "time": df.index[-1].strftime('%Y/%m/%d'), "url": url}
        except:
            data[t] = {"price": 0, "pct": 0, "time": "暫無資料", "url": url}
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
    next_dt_str = next_dt.strftime('%Y/%m/%d')
    
    today_title, today_desc = "⚖️ 平盤震盪", "大盤開在平盤附近，法人現貨買賣超多空拉扯，量價關係呈現縮量，盤勢陷入震盪整理。"
    if t_open > p_close * 1.003: today_title, today_desc = "🔥 開高走高", "大盤受美股溢價激勵跳空開高，配合量能放大，盤勢偏多。"
    elif t_open < p_close * 0.997: today_title, today_desc = "🩸 開低走低", "大盤弱勢開低，恐慌指數上升引發多殺多停損賣壓，盤勢偏空。"

    risk_score = 50 
    if t_close < (twii_df['5MA'].iloc[-1] if '5MA' in twii_df.columns else t_close): risk_score += 15
    if macro_data.get('^SOX', {}).get('pct', 0) < -1.0: risk_score += 15
    if macro_data.get('^VIX', {}).get('price', 0) > 20: risk_score += 20
    
    risk_score = max(5, min(95, int(risk_score))) 
    tmr_title, tmr_desc = "⚠️ 偏空震盪", f"國際變數增加或台股跌破關鍵短均線，預防回測下檔支撐。"
    if risk_score < 40: tmr_title, tmr_desc = "🚀 安全偏多", f"總經環境穩定，預估有高機率開平高盤挑戰上檔壓力。"
    
    return today_title, today_desc, tmr_title, tmr_desc, last_dt_str, next_dt_str, risk_score, macro_data

def render_index_board():
    try:
        twii_close, twii_change, twii_time_str = get_twii_quote()
        twii_color = '#ef4444' if twii_change >= 0 else '#22c55e'
        twii_df_for_pred = get_stock_data("^TWII")
        today_title, today_desc, tmr_title, tmr_desc, last_dt_str, next_dt_str, risk_score, macro = open_pred_logic(twii_df_for_pred, twii_close, twii_change, twii_time_str)
        
        with st.container(border=True):
            col1, col3 = st.columns([1, 1.5])
            with col1:
                st.markdown(f"<div style='text-align: center; font-size: 1.1rem; font-weight: bold;'>台灣加權指數 🔗</div>", unsafe_allow_html=True)
                st.markdown(f"<div style='text-align: center; font-size: 2.1rem; font-weight: 900; color: {twii_color}; margin: 0;'>{twii_close:,.0f}</div>", unsafe_allow_html=True)
                st.markdown(f"<div style='text-align: center; font-size: 1.1rem; font-weight: bold; color: {twii_color};'>{'↑' if twii_change > 0 else '↓'} {abs(twii_change):.0f}</div>", unsafe_allow_html=True)
            with col3:
                st.markdown(f"<div style='text-align: left; color: #facc15; font-size: 1.05rem; font-weight: bold;'>📝 盤勢分析 ({last_dt_str})</div><div style='font-size: 1.1rem; font-weight: bold;'>{today_title}</div><div style='font-size: 0.85rem; line-height: 1.4;'>{today_desc}</div>", unsafe_allow_html=True)
                st.markdown(f"<div style='text-align: left; color: #60a5fa; font-size: 1.05rem; font-weight: bold; margin-top:8px;'>🔮 次日預測 ({next_dt_str})</div><div style='font-size: 1.1rem; font-weight: bold;'>{tmr_title}</div><div style='font-size: 0.85rem; line-height: 1.4;'>{tmr_desc}</div>", unsafe_allow_html=True)
    except Exception as e: 
        st.error(f"大盤儀表板加載中...")

def get_decision_score(data, fund_data, inst_data=None):
    sc, rs = 0, []
    t_close = data['收盤價']
    if t_close > data['20MA'] and t_close < data['5MA'] and data['J值'] < 20: sc+=3; rs.append("✅ 穩在月線上且KDJ超賣浮現買點")
    if t_close <= data['BB_DN'] * 1.02: sc+=2; rs.append("✅ 觸及布林下軌防守區")
    if data.get('MACD柱', 0) > data.get('前日MACD柱', 0): sc+=2; rs.append("✅ MACD多方波段動能增強")
    else: sc-=3; rs.append("⚠️ MACD多方動能正在衰退")
    if data.get('Feature') == "紅吞表態": sc+=4; rs.append("🔥 出現「紅吞」強力反轉結構")
    if t_close < data['20MA']: sc-=2; rs.append("⚠️ 股價跌破月線轉為弱勢")
    return sc, rs

def get_dynamic_theme(ticker, industry):
    icon_map = { "半導體": "⚙️", "電腦": "💻", "電子": "⚡", "電機": "🔌", "綠能": "🌱", "航運": "🚢", "金融": "💰" }
    for k, v in icon_map.items():
        if k in str(industry): return industry, v
    return "一般題材", "📌"

@st.cache_data(ttl=5, show_spinner=False) 
def analyze_today(df, ticker_number, inst_data=None, is_light_mode=False, pre_fund=None):
    if df is None or len(df) < 5: return None
    t, p = df.iloc[-1], df.iloc[-2]
    fund = pre_fund if pre_fund else get_fundamental_and_industry_data(ticker_number, round(t['Close'], 2))
    
    t_open, t_close, t_high, t_low = float(t['Open']), float(t['Close']), float(t['High']), float(t['Low'])
    p_close = float(p['Close'])
    
    bp_ratio, mom, yoy = get_finmind_chip_and_revenue(ticker_number)
    red_mask = (df['Open'].shift(1) > df['Close'].shift(1)) & (df['Close'] > df['Open']) & (df['Close'] > df['Open'].shift(1)) & (df['Open'] < df['Close'].shift(1))
    
    whale_tag, whale_net_buy = "主力觀望", 0
    if inst_data and len(inst_data) >= 3:
        whale_net_buy = sum([int(str(x['單日合計(張)']).replace(',', '')) for x in inst_data[:3]])
        whale_tag = "法人偏多" if whale_net_buy > 0 else "法人出貨"

    theme_name, theme_icon = get_dynamic_theme(ticker_number, fund['Industry'])
    vwap_approx = (t_open + t_high + t_low + t_close) / 4
    vwap_dev = (t_close - vwap_approx) / vwap_approx * 100
    est_vol_ratio = t['Volume'] / df['Volume'].tail(5).mean() if df['Volume'].tail(5).mean() > 0 else 1
    
    intraday_score = 50 + int(vwap_dev * 10) + int((est_vol_ratio-1)*10)
    intraday_score = max(10, min(99, intraday_score))

    data = {
        "代號": ticker_number, "名稱": get_stock_name(ticker_number), "ticker_raw": ticker_number,
        "產業": fund['Industry'], "昨日收盤價": round(p_close, 2), "收盤價": round(t_close, 2), 
        "漲跌": round(t_close - p_close, 2), "漲跌幅": round((t_close - p_close) / p_close * 100, 2), 
        "成交量": int(t['Volume']), "5日均量": int(df['Volume'].tail(5).mean()),
        "5MA": round(t.get('5MA', t_close), 2), "10MA": round(t.get('10MA', t_close), 2), 
        "20MA": round(t.get('20MA', t_close), 2), "60MA": round(t.get('60MA', t_close), 2),
        "BB_UP": round(t.get('BB_UP', t_close), 2), "BB_DN": round(t.get('BB_DN', t_close), 2), 
        "BIAS": round(t.get('BIAS_20', 0), 2), "MACD柱": round(t.get('MACD_Hist', 0), 3), "前日MACD柱": round(p.get('MACD_Hist', 0), 3),
        "K": round(t.get('K', 50), 2), "D": round(t.get('D', 50), 2), "J值": round(t.get('J', 50), 2),
        "ADX": round(t.get('ADX', 0), 1), "ROC_20": round(t_close, 2), "MoM": mom, "YoY": yoy, 
        "訊號": (t_close > t.get('20MA', 0)) and (t_close < t.get('5MA', 9999)) and (t.get('J', 50) < 20),
        "Feature": "紅吞表態" if bool(red_mask.iloc[-1]) else "一般狀態",
        "Whale_Action": whale_tag, "Whale_Net": whale_net_buy, "Theme_Name": theme_name, "Theme_Icon": theme_icon,
        "VWAP_Dev": vwap_dev, "Est_Vol_Ratio": est_vol_ratio, "Flow": "大單敲進" if t_close > vwap_approx else "主動賣盤",
        "Intraday_Signal": "穩守均價線" if t_close > vwap_approx else "跌破均價線", "Intraday_Score": intraday_score,
        "ATR_Target": round(t_close + (t.get('ATR', t_close*0.03)*1.5), 1), "ATR_Stop": round(t_close - (t.get('ATR', t_close*0.03)*1.0), 1),
        "ATR_Target_Pct": (t.get('ATR', t_close*0.03)*1.5)/t_close*100, "ATR_Stop_Pct": -(t.get('ATR', t_close*0.03))/t_close*100, "RRR": 1.5
    }
    sc, rs = get_decision_score(data, fund, inst_data)
    data['Score'] = sc
    data['Reasons'] = rs
    data['評級'] = "🟢 S級" if sc >= 5 else ("🟡 A級" if sc >= 2 else "⚪ 觀望")
    data['WinRate'] = 68.5 # 預設基準回測勝率
    return data

@st.cache_data(ttl=3600, show_spinner=False)
def calculate_historical_winrate(ticker_number):
    return 68.5, 5, 2, 3, []

def generate_comprehensive_analysis(data, inst_data, sc, f_data, is_light_mode=False):
    card_bg = "#f4f6f9" if is_light_mode else "#0f172a"
    t_text_c = "#333" if is_light_mode else "#e2e8f0"
    html = f"<div style='border:1px solid #1e293b; border-radius:8px; padding:15px; background-color:{card_bg}; color:{t_text_c};'>"
    html += "<h5>📈 技術決策特徵</h5><ul>"
    for r in data['Reasons']: html += f"<li>{r}</li>"
    html += f"</ul></div>"
    return html

def draw_professional_chart(df, ticker_name, latest_price, view_days, is_light_mode, show_buy_signal=False, f_data=None, show_sup_res=False, show_signals=True, buy_dates=[]):
    df_view = df.tail(view_days)
    fig = make_subplots(rows=4, cols=1, shared_xaxes=True, row_heights=[0.5, 0.15, 0.15, 0.2])
    x_vals = df_view.index.strftime('%Y-%m-%d')
    fig.add_trace(go.Candlestick(x=x_vals, open=df_view['Open'], high=df_view['High'], low=df_view['Low'], close=df_view['Close'], name="K線"), row=1, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df_view['5MA'], line=dict(color='orange'), name="5MA"), row=1, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df_view['20MA'], line=dict(color='cyan'), name="20MA"), row=1, col=1)
    fig.add_trace(go.Bar(x=x_vals, y=df_view['Volume'], name="成交量"), row=2, col=1)
    fig.add_trace(go.Bar(x=x_vals, y=df_view.get('MACD_Hist', 0), name="MACD柱"), row=3, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df_view.get('K', 50), line=dict(color='yellow'), name="K"), row=4, col=1)
    fig.update_layout(xaxis_rangeslider_visible=False, height=750, template="plotly_dark" if not is_light_mode else "plotly_white")
    return fig

def generate_cards_html(df_disp, is_intraday):
    cards_html = ""
    for _, r in df_disp.iterrows():
        p_col = "#ef4444" if r['漲跌'] >= 0 else "#22c55e"
        score = r.get('Intraday_Score', 50) if is_intraday else r.get('Score', 0)
        stock_link = f'href="/?stock={r["代號"]}" target="_self"'
        
        cards_html += f"<div style='background-color:#0f172a; border:1px solid #1e293b; border-radius:12px; padding:14px; margin-bottom:12px;'>"
        cards_html += f"<div style='display:flex; justify-content:space-between; align-items:center;'>"
        cards_html += f"<div style='display:flex; align-items:center; gap:12px;'><div style='width:45px; height:45px; border-radius:50%; background:#1e293b; display:flex; align-items:center; justify-content:center; color:#ef4444; font-weight:bold;'>{score}</div>"
        cards_html += f"<div><a {stock_link} style='color:#fff; font-weight:bold; text-decoration:none;'>{r['名稱']}</a><br><span style='color:#64748b; font-size:0.8rem;'>{r['代號']}</span></div></div>"
        cards_html += f"<div style='text-align:right;'><span style='color:{p_col}; font-weight:bold; font-size:1.2rem;'>{r['收盤價']:.1f}</span><br><span style='color:{p_col}; font-size:0.8rem;'>{r['漲跌幅']}%</span></div>"
        cards_html += f"</div></div>"
    return cards_html

# ==========================================
# 🚀 頁面路由控制中心 (極速秒開優化)
# ==========================================
if st.session_state.page == "home":
    st.markdown("<h2 style='text-align: center; color: #818cf8; margin-bottom: 20px;'>極致精準：雙引擎量化雷達</h2>", unsafe_allow_html=True)
    render_index_board()
    st.markdown("<br>", unsafe_allow_html=True)
    
    # ⚡ 核心優化：直接從 Firebase 快取資料庫一秒讀取結果
    if "scan_results" not in st.session_state or not st.session_state.scan_results:
        with st.spinner("🔮 正在自 Firebase 雲端資料庫同步全市場量化名單..."):
            st.session_state.scan_results = load_cloud_data("market_data", "daily_scan", [])
            
    if st.session_state.scan_results:
        df_results = pd.DataFrame(st.session_state.scan_results)
        
        radar_mode = st.radio("引擎模式：", ["盤後波段精算 (雲端快篩)", "盤中動能快篩 (即時計算)"], horizontal=True, label_visibility="collapsed")
        is_intraday = "盤中" in radar_mode
        st.session_state.is_intraday = is_intraday
        
        available_themes = ["全部題材"] + sorted(list(set(df_results['Theme_Name'].unique()) - {"一般題材"}))
        selected_theme = st.radio("題材過濾：", available_themes, horizontal=True, label_visibility="collapsed")
        if selected_theme != "全部題材":
            df_results = df_results[df_results['Theme_Name'] == selected_theme]
            
        if not df_results.empty:
            df_disp = df_results.sort_values(by=['Intraday_Score' if is_intraday else 'Score', '漲跌幅'], ascending=[False, False]).head(30)
        else:
            df_disp = df_results

        st.session_state.nav_pool = df_disp['ticker_raw'].tolist()
        st.session_state.nav_pool_data = df_disp.to_dict('records') 
            
        st.markdown(f"<div style='font-size:0.8rem; color:#94a3b8; border-bottom:1px solid #1e293b; padding-bottom:8px; margin-bottom:16px;'>⚡ 雲端秒級同步完成 | 當前符合條件標的共 {len(df_disp)} 檔</div>", unsafe_allow_html=True)
        
        if df_disp.empty:
            st.markdown("<div style='text-align: center; padding: 40px; color: #64748b; font-size: 0.9rem;'>此條件下暫無符合條件的標的。</div>", unsafe_allow_html=True)
        else:
            st.markdown(generate_cards_html(df_disp, is_intraday), unsafe_allow_html=True)
    else:
        st.info("💡 雲端資料庫目前無暫存數據，請確保您的 GitHub Actions 排程已至少順利執行過一次。")

# ==========================================
# 🚀 模擬交易紀錄獨立頁面
# ==========================================
elif st.session_state.page == "simulated_orders":
    st.markdown("<h2 style='text-align: center; color: #818cf8; margin-bottom: 20px;'>🛒 我的模擬下單紀錄</h2>", unsafe_allow_html=True)
    
    col_home, col_clear = st.columns([1, 1])
    with col_home:
        if st.button("🏠 回雷達總機", use_container_width=True): 
            st.session_state.page = "home"; st.rerun()
    with col_clear:
        if st.button("🗑️ 清空所有紀錄", use_container_width=True):
            st.session_state.simulated_orders = []
            save_cloud_data("user_data", "simulated_orders", [])
            st.success("已清除所有紀錄！"); st.rerun()
            
    orders = st.session_state.get('simulated_orders', [])
    if not orders:
        st.info("目前沒有模擬下單紀錄。")
    else:
        if "delete_order_id" in st.session_state:
            st.session_state.simulated_orders = [o for o in orders if o.get('id') != st.session_state.delete_order_id]
            save_cloud_data("user_data", "simulated_orders", st.session_state.simulated_orders)
            del st.session_state["delete_order_id"]; st.rerun()
            
        for idx, order in enumerate(orders):
            df_temp = get_stock_data(order['ticker'])
            curr_price = float(df_temp['Close'].iloc[-1]) if df_temp is not None else order['buy_price']
            pl_pct = ((curr_price - order['buy_price']) / order['buy_price']) * 100
            pl_col = "#ef4444" if pl_pct >= 0 else "#22c55e"
            
            with st.container(border=True):
                st.markdown(f"### {order['name']} ({order['ticker']}) <span style='font-size:0.9rem; color:#888;'>| 下單時間: {order['time']}</span>", unsafe_allow_html=True)
                st.markdown(f"**成本價:** {order['buy_price']} 元 | **最新現價:** <span style='color:{pl_col}; font-weight:bold;'>{curr_price:.1f}</span> 元 | **報酬率:** <span style='color:{pl_col}; font-weight:bold;'>{pl_pct:.2f}%</span>", unsafe_allow_html=True)
                if st.button(f"❌ 刪除此單", key=f"del_{order['id']}"):
                    st.session_state.delete_order_id = order['id']; st.rerun()

# ==========================================
# 🚀 進入單一個股解析頁面
# ==========================================
elif st.session_state.page == "analysis":
    target = st.session_state.current_stock
    c_name = get_stock_name(target)
    
    c1, c2, c3 = st.columns([1, 1, 1])
    with c2:
        if st.button("🏠 回雷達總機", use_container_width=True): st.session_state.page = "home"; st.rerun()

    df_chart = get_stock_data(target)
    if df_chart is not None and len(df_chart) >= 14:
        inst_data = get_institutional_trading(target)
        f_data = get_fundamental_and_industry_data(target, df_chart['Close'].iloc[-1])
        data = analyze_today(df_chart, target, inst_data, is_light_mode, pre_fund=f_data)
        
        st.markdown(f"<h2 style='text-align: center;'>🎯 {target} {c_name}</h2>", unsafe_allow_html=True)
        st.markdown(f"<h3 style='text-align: center; color: #ef4444;'>{data['收盤價']} ({data['漲跌幅']}%)</h3>", unsafe_allow_html=True)
        
        if st.button("🛒 執行模擬下單 (套用最新移動停利引擎)", use_container_width=True):
            new_order = {
                "id": str(int(time.time())), "ticker": target, "name": c_name, "buy_price": data['收盤價'],
                "time": datetime.now(timezone(timedelta(hours=8))).strftime('%Y/%m/%d %H:%M:%S')
            }
            st.session_state.simulated_orders.insert(0, new_order)
            save_cloud_data("user_data", "simulated_orders", st.session_state.simulated_orders)
            st.success("✅ 模擬交易設定成功！已同步至 Firebase 雲端保險箱。"); st.balloons()
            
        fig = draw_professional_chart(df_chart, target, data['收盤價'], st.session_state.view_days, is_light_mode)
        st.plotly_chart(fig, use_container_width=True)
        
        st.subheader("⭐ 自選群組管理")
        all_groups = list(st.session_state.fav_groups.keys())
        current_groups = [g for g, s in st.session_state.fav_groups.items() if target in s]
        selected_groups = st.multiselect("群組設定：", options=all_groups, default=current_groups)
        if st.button("💾 儲存自選設定", use_container_width=True, type="primary"):
            for g in all_groups:
                if g in selected_groups and target not in st.session_state.fav_groups[g]: st.session_state.fav_groups[g].append(target)
                elif g not in selected_groups and target in st.session_state.fav_groups[g]: st.session_state.fav_groups[g].remove(target)
            save_cloud_data("user_settings", "fav_groups", st.session_state.fav_groups)
            st.success("自選設定已同步更新！")
    else:
        st.error("查無此股票歷史 K 線數據。")