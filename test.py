# 最後修改時間: 2026-07-05 (終極完美版：雲端秒開引擎 + 華麗專業量化 UI 滿血回歸)
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

from streamlit_autorefresh import st_autorefresh

# 設定日誌系統
logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(levelname)s - %(message)s')

# === 雙引擎 API 憑證 (安全雲端讀取) ===
FINMIND_TOKEN = st.secrets["FINMIND_TOKEN"]
FUGLE_API_KEY = st.secrets["FUGLE_API_KEY"]

# ==========================================
# 0. 系統初始化與風格設定
# ==========================================
st.set_page_config(page_title="專業交易雷達", layout="wide", initial_sidebar_state="collapsed")

st.markdown('''
<head>
    <link rel="manifest" href="/manifest.json">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <meta name="apple-mobile-web-app-title" content="交易雷達">
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
app_bg = "#f4f6f9" if is_light_mode else "#0b1120"
pill_bg = "#ffffff" if is_light_mode else "#1e293b"
pill_border = "#d1d5db" if is_light_mode else "#334155"
pill_text = "#374151" if is_light_mode else "#94a3b8"

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

st.sidebar.title("🔍 快速搜尋")
with st.sidebar.form(key="search_form"):
    search_input = st.text_input("隱藏", placeholder="輸入股票代號或中文名稱...", label_visibility="collapsed")
    submit_search = st.form_submit_button("送出搜尋", use_container_width=True)
    
if submit_search and search_input:
    s_val = search_input.strip().replace(" ", "")
    target_ticker = None
    if re.match(r'^[A-Za-z0-9]+$', s_val):
        target_ticker = s_val.upper()
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
if st.sidebar.button("📋 我的模擬下單紀錄", use_container_width=True):
    st.session_state.page = "simulated_orders"; st.rerun()

def get_stock_name(ticker):
    ticker_str = str(ticker).strip().upper().replace(".TW", "").replace(".TWO", "")
    return CURRENT_STOCK_NAMES.get(ticker_str, ticker_str)

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

if 'page' not in st.session_state: st.session_state.page = "home"
if 'current_stock' not in st.session_state: st.session_state.current_stock = "2330"
if 'view_days' not in st.session_state: st.session_state.view_days = 30
if 'date_offset' not in st.session_state: st.session_state.date_offset = 0

if 'simulated_orders' not in st.session_state:
    st.session_state.simulated_orders = load_cloud_data("user_data", "simulated_orders", [])
if 'fav_groups' not in st.session_state:
    st.session_state.fav_groups = load_cloud_data("user_settings", "fav_groups", {"預設群組": ["1802", "2330", "1785"]})

if 'stock' in st.query_params:
    q_stock = st.query_params['stock']
    if st.session_state.get('last_q_stock') != q_stock:
        st.session_state.current_stock = q_stock
        st.session_state.page = "analysis"
        st.session_state.date_offset = 0
        st.session_state.last_q_stock = q_stock

# ==========================================
# 🚀 核心計算與抓取模組
# ==========================================
ENG_TO_TW_INDUSTRY = {
    "Semiconductors": "半導體業", "Consumer Electronics": "消費性電子", "Electronic Components": "電子零組件",
    "Computer Hardware": "電腦及週邊設備", "Marine Shipping": "航運業", "Financial Services": "金融業",
}

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_twse_index_history():
    try:
        df = yf.Ticker("^TWII").history(period="1y")
        if not df.empty:
            df.index = pd.to_datetime(df.index.strftime('%Y-%m-%d'))
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
                return d
        except: return None

    df = fetch_twse_index_history() if base_ticker == "^TWII" else fetch_clean(f"{base_ticker}.TW")
    if df is None and base_ticker != "^TWII": df = fetch_clean(f"{base_ticker}.TWO")
    if df is None: return None
    
    try:
        if base_ticker != "^TWII":
            url = f"https://api.fugle.tw/marketdata/v1.0/stock/intraday/quote/{base_ticker}"
            res = requests.get(url, headers={'X-API-KEY': FUGLE_API_KEY}, timeout=3)
            if res.status_code == 200:
                q = res.json()
                c_price = float(q.get('closePrice', q.get('lastPrice', df['Close'].iloc[-1])))
                
                # 🚀 修復週末 K 線重複 Bug：判斷是否為交易日
                now_tpe = datetime.now(timezone(timedelta(hours=8)))
                if now_tpe.weekday() < 5: # 0-4 代表週一到週五
                    dt_live = pd.to_datetime(now_tpe.strftime('%Y-%m-%d'))
                    if dt_live not in df.index:
                        new_row = pd.DataFrame({'Open': [float(q.get('openPrice', c_price))], 'High': [float(q.get('highPrice', c_price))], 'Low': [float(q.get('lowPrice', c_price))], 'Close': [c_price], 'Volume': [float(q.get('total', {}).get('tradeVolume', 0))]}, index=[dt_live])
                        df = pd.concat([df, new_row])
                    else:
                        df.at[dt_live, 'Close'] = c_price
                        df.at[dt_live, 'High'] = max(df.at[dt_live, 'High'], float(q.get('highPrice', c_price)))
                        df.at[dt_live, 'Low'] = min(df.at[dt_live, 'Low'], float(q.get('lowPrice', c_price)))
    except: pass

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
    except:
        df['ATR'] = df['Close'] * 0.03
        df['ADX'] = 20
    return df

@st.cache_data(ttl=86400, show_spinner=False)
def get_fundamental_and_industry_data(ticker_number, current_price=0):
    base_ticker = str(ticker_number).strip().upper().replace(".TW", "").replace(".TWO", "")
    eps_val, pe_val, ind = "無", "無", "一般產業"
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
    data = {"global_time": datetime.now(timezone(timedelta(hours=8))).strftime('%Y/%m/%d %H:%M:%S')}
    for t, url in {"^SOX": "https://finance.yahoo.com/quote/^SOX", "^VIX": "https://finance.yahoo.com/quote/^VIX", "TWD=X": "https://finance.yahoo.com/quote/TWD=X"}.items():
        try:
            df = yf.Ticker(t).history(period="5d").dropna(subset=['Close'])
            if len(df) >= 2:
                c, p = float(df['Close'].iloc[-1]), float(df['Close'].iloc[-2])
                data[t] = {"price": c, "pct": (c-p)/p*100 if p != 0 else 0, "time": df.index[-1].strftime('%Y/%m/%d'), "url": url}
        except: data[t] = {"price": 0, "pct": 0, "time": "暫無資料", "url": url}
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
    if t_open > p_close * 1.003:
        if t_close > t_open: today_title, today_desc = "🔥 開高走高", "大盤受外資買盤與美股溢價激勵跳空開高，配合融資餘額增加與量能放大，盤勢極度偏多。"
        else: today_title, today_desc = "⚠️ 開高走低", "大盤跳空開高後遭遇短線獲利了結賣壓，動能指標有進入超買區疑慮，呈現高檔回落。"
    elif t_open < p_close * 0.997:
        if t_close > t_open: today_title, today_desc = "💪 開低走高", "大盤受國際盤回檔影響開低，但低檔投信承接買盤強勁，出現開低走高收紅K型態。"
        else: today_title, today_desc = "🩸 開低走低", "大盤弱勢開低，恐慌指數上升引發散戶多殺多停損賣壓，盤勢極度偏空。"

    risk_score = 50 
    if t_close < (twii_df['5MA'].iloc[-1] if '5MA' in twii_df.columns else t_close): risk_score += 15
    else: risk_score -= 10
    sox_pct = macro_data.get('^SOX', {}).get('pct', 0)
    if sox_pct < -2.0: risk_score += 20
    elif sox_pct > 1.5: risk_score -= 15
    vix = macro_data.get('^VIX', {})
    if vix.get('price', 0) > 20 or vix.get('pct', 0) > 10.0: risk_score += 20
    elif vix.get('pct', 0) < -5.0: risk_score -= 10
    
    risk_score = max(5, min(95, int(risk_score))) 
    if risk_score < 40: tmr_title, tmr_desc = "🚀 安全偏多", f"總經環境穩定，預估次一交易日 ({next_dt_str}) 有極高機率開平高盤挑戰上檔壓力。"
    elif risk_score < 70: tmr_title, tmr_desc = "⚠️ 偏空震盪", f"國際變數增加或台股跌破關鍵短均線，預防 ({next_dt_str}) 開平低盤回測下檔支撐。"
    else: tmr_title, tmr_desc = "🚨 極度警戒", f"全球宏觀風險飆高，強烈建議減碼防範 ({next_dt_str}) 跳空重挫的系統性風險。"
    
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
                st.markdown(f"<div style='text-align: center; font-size: 0.85rem; color: #888;'>🕒 抓取時間: {twii_time_str}</div>", unsafe_allow_html=True)
            with col3:
                st.markdown(f"<div style='text-align: left; color: #facc15; font-size: 1.05rem; font-weight: bold;'>📝 盤勢分析 ({last_dt_str})</div>", unsafe_allow_html=True)
                st.markdown(f"<div style='text-align: left; font-size: 1.1rem; font-weight: bold;'>{today_title}</div>", unsafe_allow_html=True)
                st.markdown(f"<div style='text-align: left; font-size: 0.85rem; margin-top: 2px; margin-bottom: 8px; line-height: 1.4;'>{today_desc}</div>", unsafe_allow_html=True)
                st.markdown(f"<div style='text-align: left; color: #60a5fa; font-size: 1.05rem; font-weight: bold;'>🔮 次日開盤預測 ({next_dt_str})</div>", unsafe_allow_html=True)
                st.markdown(f"<div style='text-align: left; font-size: 1.1rem; font-weight: bold;'>{tmr_title}</div>", unsafe_allow_html=True)
                st.markdown(f"<div style='text-align: left; font-size: 0.85rem; margin-top: 2px; line-height: 1.4;'>{tmr_desc}</div>", unsafe_allow_html=True)
            
            if st.button("🔄 手動更新即時大盤報價", use_container_width=True): st.cache_data.clear(); st.rerun()
        
        st.markdown("<h4 style='margin-top:20px; text-align:center;'>🌍 全球總經與次日開盤風險評估</h4>", unsafe_allow_html=True)
        st.markdown(f"<div style='text-align:center; font-size:0.85rem; color:#888; margin-top:-10px; margin-bottom:10px;'>🕒 總經最後收盤時間: {macro.get('global_time', '無')}</div>", unsafe_allow_html=True)

        bar_color = "#22c55e" if risk_score < 40 else ("#facc15" if risk_score < 70 else "#ef4444")
        risk_label = "🟢 資金充沛，安心佈局" if risk_score < 40 else ("🟡 變數增加，控制倉位" if risk_score < 70 else "🔴 系統風險，嚴格減碼")
        st.markdown(f"<div style='text-align:center; font-size:1.1rem; font-weight:bold;'>系統量化開低風險度：<span style='color:{bar_color};'>{risk_score}%</span></div>", unsafe_allow_html=True)
        st.markdown(f"""
        <div style="width:100%; height:12px; background-color:#1e293b; border-radius:6px; overflow:hidden; margin: 10px 0;">
            <div style="width: {risk_score}%; height:100%; background-color: {bar_color}; transition: width 0.5s;"></div>
        </div>
        <div style='text-align:center; font-size:0.9rem; color:{bar_color}; font-weight:bold; margin-bottom:15px;'>{risk_label}</div>
        """, unsafe_allow_html=True)
        
        mc1, mc2, mc3 = st.columns(3)
        sox = macro.get('^SOX', {"price": 0, "pct": 0, "time": "無", "url": "#"})
        vix = macro.get('^VIX', {"price": 0, "pct": 0, "time": "無", "url": "#"})
        twd = macro.get('TWD=X', {"price": 0, "pct": 0, "time": "無", "url": "#"})
        
        with mc1.container(border=True):
            sox_c = "#ef4444" if sox.get('pct',0) >= 0 else "#22c55e"
            st.markdown(f"<div style='text-align:center; font-size:0.85rem;'>費城半導體</div><div style='text-align:center; font-size:1.1rem; font-weight:bold; color:{sox_c};'>{sox.get('price',0):,.1f}<br>{'+' if sox.get('pct',0)>0 else ''}{sox.get('pct',0):.2f}%</div>", unsafe_allow_html=True)
        with mc2.container(border=True):
            vix_c = "#22c55e" if vix.get('pct',0) <= 0 else "#ef4444"
            st.markdown(f"<div style='text-align:center; font-size:0.85rem;'>VIX 恐慌指數</div><div style='text-align:center; font-size:1.1rem; font-weight:bold; color:{vix_c};'>{vix.get('price',0):,.2f}<br>{'+' if vix.get('pct',0)>0 else ''}{vix.get('pct',0):.2f}%</div>", unsafe_allow_html=True)
        with mc3.container(border=True):
            twd_status = "台幣貶值 (警戒)" if twd.get('pct',0) > 0 else "台幣升值 (熱錢流入)"
            st.markdown(f"<div style='text-align:center; font-size:0.85rem;'>美元/台幣</div><div style='text-align:center; font-size:1.1rem; font-weight:bold; color:#facc15;'>{twd.get('price',0):,.3f}<br>{twd_status}</div>", unsafe_allow_html=True)
            
    except Exception as e: 
        st.error(f"大盤儀表板加載中...")

def get_decision_score(data, fund_data, inst_data=None):
    sc, rs = 0, []
    adx = data.get('ADX', 0)
    roc_20 = data.get('ROC_20', 0)
    is_trending = adx >= 25 
    
    if data.get('訊號', False): 
        if is_trending: sc+=3; rs.append(f"✅ 穩在月線上且KDJ超賣 (ADX:{adx} 趨勢明確)")
        else: sc+=1; rs.append(f"⚠️ 穩在月線上 (但 ADX:{adx} 盤整區間，動能稍弱)")
            
    if data.get('收盤價', 0) <= data.get('BB_DN', 0) * 1.02: sc+=2; rs.append("✅ 觸及布林下軌支撐")
    if data.get('BIAS', 0) < -5: sc+=1; rs.append("✅ 負乖離過大")
    
    if roc_20 > 10: sc+=2; rs.append(f"🔥 近月漲幅 {roc_20}% 表現亮眼，具備市場主流強勢股特徵")
    elif roc_20 < -5: sc-=2; rs.append(f"🩸 近月跌幅 {roc_20}% 表現弱勢，請避開弱勢接刀陷阱")
    
    if data.get('MoM', 0) > 0 and data.get('YoY', 0) > 0: sc+=3; rs.append(f"🔥 月營收雙增 (MoM: {data['MoM']}%, YoY: {data['YoY']}%)，具備長線黑馬特質")
    elif data.get('YoY', 0) > 15: sc+=2; rs.append(f"✅ 月營收年增達 {data['YoY']}%，營運動能強勁")
        
    try: eps_f = float(str(fund_data['EPS']).replace(',', ''))
    except: eps_f = 0.0
    if eps_f > 0: sc+=2; rs.append("✅ 歷史 EPS 獲利體質")
    
    if data.get('成交量', 0) > data.get('5日均量', 0) * 1.1: sc+=2; rs.append("✅ 量能放大 (具備主力進場點火特徵)")
    else: sc-=1; rs.append("⚠️ 量能未明顯放大 (打底或缺乏點火動能)")
        
    if data.get('MACD柱', 0) > data.get('前日MACD柱', -999): sc+=2; rs.append("✅ MACD 綠柱收斂或紅柱放大 (動能防禦過關)")
    else: sc-=3; rs.append("⚠️ MACD 空方動能持續擴大 (型態脆弱嚴防接刀)")

    if data.get('紅吞', False): 
        if is_trending: sc+=4; rs.append("🔥 出現「紅吞」反轉型態 (趨勢確認，強烈買訊)")
        else: sc+=1; rs.append("⚠️ 出現「紅吞」(但 ADX 偏低處於盤整，提防假突破)")
    if data.get('黑吞', False): sc-=3; rs.append("🩸 出現「黑吞」反轉型態 (強烈空頭逃命訊號)")

    if data.get('回測有撐', False): sc+=2; rs.append("🔥 帶量長下影線 (主力回測支撐成功)")
    if data.get('反彈遇壓', False): sc-=2; rs.append("🩸 反彈遇均線壓力留長上影線 (空方壓制)")
    
    if data.get('收盤價', 0) >= data.get('5MA', 0) and data.get('5日線即將上彎', False): rs.append("🔥 5日線扣低值 (短均線準備上彎發散，短線動能轉強)")
    if data.get('收盤價', 0) < data.get('5MA', 0) and not data.get('5日線即將上彎', True): rs.append("⚠️ 5日線扣高值 (短均線即將下彎產生蓋頭壓力)")

    if data.get('J值', 0) >= 80: sc-=3; rs.append("⚠️ KDJ高檔過熱")
    if data.get('收盤價', 0) >= data.get('BB_UP', 0) * 0.98: sc-=2; rs.append("⚠️ 觸及布林上軌壓力")
    if data.get('BIAS', 0) > 7: sc-=2; rs.append("⚠️ 正乖離過大")
    if data.get('收盤價', 0) < data.get('20MA', 0): sc-=2; rs.append("⚠️ 跌破月線支撐")
    if eps_f < 0: sc-=1; rs.append("⚠️ 基本面虧損")

    return sc, rs

def get_dynamic_theme(ticker, industry):
    THEME_MAP = {
        "2382": ("AI伺服器", "💡"), "2356": ("AI伺服器", "💡"), "3231": ("AI伺服器", "💡"),
        "2330": ("先進製程", "⚙️"), "2454": ("先進製程", "⚙️"), "6147": ("先進製程", "⚙️"),
        "1503": ("重電綠能", "⚡"), "1519": ("重電綠能", "⚡"), "2308": ("重電綠能", "⚡"),
        "2359": ("機器人", "🤖"), "2354": ("機器人", "🤖"), "2603": ("航運", "🚢"),
        "2881": ("金融業", "💰"), "2891": ("金融業", "💰")
    }
    if ticker in THEME_MAP: return THEME_MAP[ticker]
    ind = str(industry).strip() if pd.notna(industry) and industry else ""
    if not ind or ind == "一般產業": return ("一般題材", "📌")
    icon_map = { "半導體": "⚙️", "電腦": "💻", "電子": "⚡", "電機": "🔌", "綠能": "🌱", "光電": "☀️", "通信": "📡", "網通": "📶", "生技": "🧬", "航運": "🚢", "鋼鐵": "🏗️", "金融": "💰", "營造": "🏗️", "觀光": "✈️" }
    for kw, ic in icon_map.items():
        if kw in ind: return (ind, ic)
    return (ind, "🏷️")

@st.cache_data(ttl=5, show_spinner=False) 
def analyze_today(df, ticker_number, inst_data=None, is_light_mode=False, pre_fund=None):
    if df is None or len(df) < 5: return None
    t, p = df.iloc[-1], df.iloc[-2]
    fund = pre_fund if pre_fund else get_fundamental_and_industry_data(ticker_number, round(t['Close'], 2))
    
    t_open, t_close, t_high, t_low = float(t['Open']), float(t['Close']), float(t['High']), float(t['Low'])
    p_open, p_close = float(p['Open']), float(p['Close'])
    
    bp_ratio, mom, yoy = get_finmind_chip_and_revenue(ticker_number)
    
    f_net_10d, t_net_10d, d_net_10d = 0, 0, 0
    if inst_data:
        f_net_10d = sum([int(str(x['外資(張)']).replace(',', '')) for x in inst_data if str(x['外資(張)']).replace(',', '').lstrip('-').isdigit()])
        t_net_10d = sum([int(str(x['投信(張)']).replace(',', '')) for x in inst_data if str(x['投信(張)']).replace(',', '').lstrip('-').isdigit()])
        d_net_10d = sum([int(str(x['自營商(張)']).replace(',', '')) for x in inst_data if str(x['自營商(張)']).replace(',', '').lstrip('-').isdigit()])
    
    red_mask = (df['Open'].shift(1) > df['Close'].shift(1)) & (df['Close'] > df['Open']) & (df['Close'] > df['Open'].shift(1)) & (df['Open'] < df['Close'].shift(1))
    black_mask = (df['Close'].shift(1) > df['Open'].shift(1)) & (df['Open'] > df['Close']) & (df['Open'] > df['Close'].shift(1)) & (df['Close'] < df['Open'].shift(1))
    total_range = t_high - t_low if t_high - t_low != 0 else 0.001
    lower_shadow = min(t_open, t_close) - t_low
    body = abs(t_close - t_open)
    ma_resistance = min(t.get('5MA', t_close), t.get('10MA', t_close))
    upper_shadow = t_high - max(t_open, t_close)

    whale_tag, whale_net_buy = "主力觀望", 0
    if inst_data and len(inst_data) >= 3:
        f_net = sum([int(str(x['外資(張)']).replace(',', '')) for x in inst_data[:3] if str(x['外資(張)']).replace(',', '').lstrip('-').isdigit()])
        t_net = sum([int(str(x['投信(張)']).replace(',', '')) for x in inst_data[:3] if str(x['投信(張)']).replace(',', '').lstrip('-').isdigit()])
        d_net = sum([int(str(x['自營商(張)']).replace(',', '')) for x in inst_data[:3] if str(x['自營商(張)']).replace(',', '').lstrip('-').isdigit()])
        whale_net_buy = f_net + t_net + d_net
        if f_net > 0 and t_net > 0: whale_tag = "土洋齊買"
        elif f_net > 500: whale_tag = "外資連買"
        elif t_net > 200: whale_tag = "投信認養"
        elif whale_net_buy > 0: whale_tag = "法人偏多"
        elif whale_net_buy < 0: whale_tag = "法人倒貨"

    theme_name, theme_icon = get_dynamic_theme(ticker_number, fund['Industry'])
    vwap_approx = (t_open + t_high + t_low + t_close) / 4
    vwap_dev = (t_close - vwap_approx) / vwap_approx * 100
    est_vol_ratio = t['Volume'] / df['Volume'].tail(5).mean() if df['Volume'].tail(5).mean() > 0 else 1
    
    intraday_signal = "穩守均價線" if t_close > vwap_approx else "跌破均價線"
    if t_close > vwap_approx and est_vol_ratio > 1.3: intraday_signal = "強勢越過均價線"
    
    intraday_score = 40
    if vwap_dev > 0: intraday_score += min(30, vwap_dev * 10)
    else: intraday_score += max(-30, vwap_dev * 10)
    if est_vol_ratio > 1.5: intraday_score += 20
    elif est_vol_ratio > 1.0: intraday_score += 10
    elif est_vol_ratio < 0.8: intraday_score -= 10
    if t_close > p_close: intraday_score += 10
    if t.get('J', 50) < 30: intraday_score += 5
    intraday_score = max(10, min(99, int(intraday_score)))

    flow = "內外盤拉扯"
    if est_vol_ratio > 1.5 and t_close > vwap_approx: flow = "大單敲進"
    elif t_close > vwap_approx: flow = "主動買盤"
    elif est_vol_ratio > 1.5 and t_close < vwap_approx: flow = "大單倒出"

    atr_val = t.get('ATR', t_close * 0.03)
    target_p = t_close + (atr_val * 1.5)
    stop_p = t_close - (atr_val * 1.0)
    target_pct = (target_p - t_close) / t_close * 100
    stop_pct = (stop_p - t_close) / t_close * 100
    rrr = round(abs(target_pct / stop_pct), 1) if stop_pct != 0 else 0
    roc_20 = (t_close - float(df['Close'].iloc[-20])) / float(df['Close'].iloc[-20]) * 100 if len(df) >= 20 else 0

    data = {
        "代號": ticker_number, "名稱": get_stock_name(ticker_number), "ticker_raw": ticker_number,
        "產業": fund['Industry'], "昨日收盤價": round(p_close, 2), "收盤價": round(t_close, 2), 
        "漲跌": round(t_close - p_close, 2), "漲跌幅": round((t_close - p_close) / p_close * 100, 2), 
        "成交量": int(t['Volume']), "5日均量": int(df['Volume'].tail(5).mean()),
        "5MA": round(t.get('5MA', t_close), 2), "10MA": round(t.get('10MA', t_close), 2), 
        "20MA": round(t.get('20MA', t_close), 2), "60MA": round(t.get('60MA', t_close), 2),
        "BB_UP": round(t.get('BB_UP', t_close), 2), "BB_DN": round(t.get('BB_DN', t_close), 2), 
        "BIAS": round(t.get('BIAS_20', 0), 2),
        "MACD": round(t.get('MACD', 0), 2), "MACD柱": round(t.get('MACD_Hist', 0), 3), "前日MACD柱": round(p.get('MACD_Hist', 0), 3),
        "K": round(t.get('K', 50), 2), "D": round(t.get('D', 50), 2), "J值": round(t.get('J', 50), 2),
        "ADX": round(t.get('ADX', 0), 1), "ROC_20": round(roc_20, 2), "MoM": mom, "YoY": yoy, 
        "ForeignNet10d": f_net_10d, "TrustNet10d": t_net_10d, "DealerNet10d": d_net_10d, 
        "訊號": (t_close > t.get('20MA', 0)) and (t_close < t.get('5MA', 9999)) and (t.get('J', 50) < 20),
        "紅吞": bool(red_mask.iloc[-1]), "黑吞": bool(black_mask.iloc[-1]), "近七日紅吞": bool(red_mask.tail(7).any()),
        "回測有撐": (lower_shadow > body * 1.5) and (lower_shadow / total_range > 0.4) and (t_low < p_close) and (t_close >= min(p_open, p_close)),
        "反彈遇壓": (upper_shadow > body * 1.5) and (upper_shadow / total_range > 0.4) and (t_high >= ma_resistance) and (t_close < ma_resistance),
        "5日線即將上彎": t_close > (float(df['Close'].iloc[-5]) if len(df) >= 5 else float(t_close)),
        "Whale_Action": whale_tag, "Whale_Net": whale_net_buy, "Theme_Name": theme_name, "Theme_Icon": theme_icon,
        "VWAP": round(vwap_approx, 1), "VWAP_Dev": vwap_dev, "Est_Vol_Ratio": est_vol_ratio, "Intraday_Signal": intraday_signal, "Intraday_Score": intraday_score, "Flow": flow,
        "ATR_Target": round(target_p, 1), "ATR_Stop": round(stop_p, 1), "ATR_Target_Pct": target_pct, "ATR_Stop_Pct": stop_pct, "RRR": rrr
    }
    
    sc, rs = get_decision_score(data, fund, inst_data)
    data['Score'] = sc
    data['Reasons'] = rs
    data['評級'] = "🟢 S級" if sc >= 5 else ("🟡 A級" if sc >= 2 else "⚪ 觀望")
    
    feature = "一般狀態"
    if sc >= 2:
        if whale_net_buy > 500: feature = "法人連買"
        elif bool(red_mask.iloc[-1]): feature = "紅吞表態"
        elif (lower_shadow > body * 1.5): feature = "支撐防守"
        elif data.get('Est_Vol_Ratio', 1) > 1.3: feature = "出量攻擊"
    data['Feature'] = feature
    data['WinRate'] = 0.0 
    
    return data

@st.cache_data(ttl=3600, show_spinner=False)
def calculate_historical_winrate(ticker_number):
    df_slice = get_stock_data(ticker_number)
    if df_slice is None or len(df_slice) < 14: return 0.0, 0, 0, 0, []
        
    fund = get_fundamental_and_industry_data(ticker_number, round(df_slice['Close'].iloc[-1], 2))
    recent_90 = df_slice.tail(90)
    s_count, a_count, wins, closed_signals = 0, 0, 0, 0
    buy_dates = []
    last_buy_idx = -999
    start_idx = len(df_slice) - len(recent_90)
    
    for idx in range(len(recent_90)):
        actual_idx = start_idx + idx
        if actual_idx - last_buy_idx < 5: continue
            
        temp_df = df_slice.iloc[:actual_idx + 1]
        if len(temp_df) >= 14:
            t_data = analyze_today(temp_df, ticker_number, inst_data=None, is_light_mode=False, pre_fund=fund)
            if t_data and t_data['Score'] >= 2:
                if t_data['Score'] >= 5: s_count += 1
                else: a_count += 1
                
                last_buy_idx = actual_idx
                buy_dates.append(recent_90.index[idx])
                
                buy_price = t_data['收盤價']
                atr_val = t_data.get('ATR_Target', buy_price * 1.03) - buy_price
                target_p = buy_price + atr_val
                rrr = t_data.get('RRR', 1.5)
                if rrr <= 0: rrr = 1.5
                stop_p = buy_price - (atr_val / rrr)
                
                future_df = df_slice.iloc[actual_idx + 1 : actual_idx + 6]
                if len(future_df) > 0:
                    closed_signals += 1
                    hit_target = future_df['High'].max() >= target_p
                    hit_stop = future_df['Low'].min() <= stop_p
                    if hit_target and not hit_stop: wins += 1
                    elif future_df['Close'].iloc[-1] > buy_price and not hit_stop: wins += 1
                    
    win_rate = (wins / closed_signals * 100) if closed_signals > 0 else 0
    return win_rate, closed_signals, s_count, a_count, buy_dates

def generate_comprehensive_analysis(data, inst_data, sc, f_data, is_light_mode=False):
    t_text_c = "#333" if is_light_mode else "#e2e8f0"
    card_bg = "#f4f6f9" if is_light_mode else "#0f172a"
    sum_bg = "rgba(0,0,0,0.05)" if is_light_mode else "rgba(30,41,59,0.5)"
    b_col = "#ddd" if is_light_mode else "#1e293b"

    tech_bullets = []
    adx = data.get('ADX', 0)
    roc = data.get('ROC_20', 0)
    if adx >= 25: tech_bullets.append(f"🔥 <span style='color:#ef4444; font-weight:bold;'>ADX 趨勢指標 ({adx})：大於 25，多空方向明確，突破延續性極高。</span>")
    else: tech_bullets.append(f"⚠️ <span style='color:#facc15; font-weight:bold;'>ADX 趨勢指標 ({adx})：低於 25，正處於橫盤震盪，較容易假突破被雙巴。</span>")
    if roc > 10: tech_bullets.append(f"🔥 <span style='color:#ef4444; font-weight:bold;'>強勢股濾網 (近月漲幅 {roc}%)：大幅打敗大盤，屬於市場主流資金偏好的強勢標的。</span>")

    for reason in data.get('Reasons', []):
        if "✅" in reason or "🔥" in reason: tech_bullets.append(f"<span style='color:#ef4444; font-weight:bold;'>{reason}</span>")
        elif "⚠️" in reason or "🚨" in reason or "🩸" in reason: tech_bullets.append(f"<span style='color:#22c55e;'><b>{reason}</b></span>")

    tech_res = "🔥 股價走勢強勁，目前屬於多頭格局，量價配合。" if sc >= 2 else "⚖️ 股價處於震盪或空方弱勢整理。"
    
    tech_html = f"<div style='border: 1px solid {b_col}; border-radius: 8px; padding: 15px; margin-bottom: 15px; background-color: {card_bg};'>"
    tech_html += f"<h4 style='color: #60a5fa; margin-top: 0; font-size: 1.2rem;'>📈 技術面分析</h4><ul style='font-size: 0.95rem; line-height: 1.6; color: {t_text_c};'>"
    for b in tech_bullets: tech_html += f"<li style='margin-bottom:6px;'>{b}</li>"
    tech_html += f"</ul><div style='background-color: {sum_bg}; padding: 12px; border-radius: 6px; border-left: 4px solid #60a5fa; font-size: 0.95rem; color: {t_text_c};'><b>【結  果】</b>{tech_res}</div></div>"

    chip_res_text = "中立觀望"
    tables_html = ""
    th_color = "#ccc" if not is_light_mode else "#555"
    def get_c(val): return "#ef4444" if val > 0 else ("#22c55e" if val < 0 else t_text_c)

    f_net = data.get('ForeignNet10d', 0)
    t_net = data.get('TrustNet10d', 0)
    d_net = data.get('DealerNet10d', 0)
    
    if inst_data and len(inst_data) >= 3:
        f_net_today = sum([int(str(x['外資(張)']).replace(',', '')) for x in inst_data[:3] if str(x['外資(張)']).replace(',', '').lstrip('-').isdigit()])
        t_net_today = sum([int(str(x['投信(張)']).replace(',', '')) for x in inst_data[:3] if str(x['投信(張)']).replace(',', '').lstrip('-').isdigit()])
        
        if f_net_today > 0 and t_net_today > 0: chip_res_text = "🔥 外資跟投信都在買，籌碼正集中到大戶法人手上，走勢穩定。"
        elif f_net_today < 0 and t_net_today < 0: chip_res_text = "⚠️ 外資跟投信同步倒貨，籌碼有鬆動流向散戶的疑慮。"
        else: chip_res_text = "⚖️ 法人多空步調不一，一方買一方賣，籌碼處於換手震盪階段。"

        tables_html += f"<div style='display: flex; gap: 15px; flex-wrap: wrap; margin-top: 15px; width: 100%;'>"
        tables_html += f"<div style='flex: 1; min-width: 260px; border: 1px solid {b_col}; border-radius: 6px; padding: 15px; background-color: {sum_bg};'>"
        tables_html += f"<div style='font-weight: bold; color: {t_text_c}; font-size: 1rem; margin-bottom: 15px;'>🎯 進階籌碼監控 (真實數據)</div>"
        tables_html += f"<div style='font-size: 0.9rem; font-weight: bold; margin-bottom: 10px; color: {t_text_c};'>⚖️ 三大法人 10 日累積買賣超</div>"
        tables_html += f"<div style='display: flex; justify-content: space-between; font-size: 0.85rem; margin-bottom: 8px;'><span>外資及陸資</span><span style='color: {get_c(f_net)}; font-weight: bold;'>{'+' if f_net>0 else ''}{f_net:,} 張</span></div>"
        tables_html += f"<div style='display: flex; justify-content: space-between; font-size: 0.85rem; margin-bottom: 8px;'><span>投信</span><span style='color: {get_c(t_net)}; font-weight: bold;'>{'+' if t_net>0 else ''}{t_net:,} 張</span></div>"
        tables_html += f"<div style='display: flex; justify-content: space-between; font-size: 0.85rem;'><span>自營商</span><span style='color: {get_c(d_net)}; font-weight: bold;'>{'+' if d_net>0 else ''}{d_net:,} 張</span></div></div>"
        
        tables_html += f"<div style='flex: 1.5; min-width: 320px;'><div style='font-weight: bold; color: {t_text_c}; font-size: 0.95rem; margin-bottom: 10px;'>⏳ 近五日三大法人逐日買賣超明細 (張)</div>"
        tables_html += f"<table style='width: 100%; text-align: center; border-collapse: collapse; font-size: 0.9rem; border: 1px solid {b_col}; color: {t_text_c};'>"
        tables_html += f"<tr style='background-color: {sum_bg}; color: {th_color};'><th style='border: 1px solid {b_col}; padding: 8px 4px;'>日期</th><th style='border: 1px solid {b_col}; padding: 8px 4px;'>外資</th><th style='border: 1px solid {b_col}; padding: 8px 4px;'>投信</th><th style='border: 1px solid {b_col}; padding: 8px 4px;'>自營商</th><th style='border: 1px solid {b_col}; padding: 8px 4px;'>合計</th></tr>"
        
        for row in inst_data[:5]:
            date_str = row['日期']
            f_val = int(str(row['外資(張)']).replace(',', ''))
            t_val = int(str(row['投信(張)']).replace(',', ''))
            d_val = int(str(row['自營商(張)']).replace(',', ''))
            s_val = int(str(row['單日合計(張)']).replace(',', ''))
            tables_html += f"<tr><td style='border: 1px solid {b_col}; padding: 8px 4px;'>{date_str}</td><td style='border: 1px solid {b_col}; padding: 8px 4px; color: {get_c(f_val)}; font-weight: 500;'>{f_val}</td><td style='border: 1px solid {b_col}; padding: 8px 4px; color: {get_c(t_val)}; font-weight: 500;'>{t_val}</td><td style='border: 1px solid {b_col}; padding: 8px 4px; color: {get_c(d_val)}; font-weight: 500;'>{d_val}</td><td style='border: 1px solid {b_col}; padding: 8px 4px; color: {get_c(s_val)}; font-weight: 500;'>{s_val}</td></tr>"
        tables_html += f"</table><div style='text-align: right; font-size: 0.75rem; color: #888; margin-top: 10px;'>來源: FinMind API</div></div></div>"
    else:
        tables_html = f"<div style='color: {sub_text_col}; font-size: 0.9rem; padding: 10px; border: 1px dashed {border_col}; border-radius: 6px;'>目前暫無籌碼資料可供分析。</div>"

    chip_html = f"<div style='border: 1px solid {b_col}; border-radius: 8px; padding: 15px; margin-bottom: 15px; background-color: {card_bg};'>"
    chip_html += f"<h4 style='color: #facc15; margin-top: 0; font-size: 1.2rem;'>🏦 籌碼面分析</h4>{tables_html}"
    chip_html += f"<div style='background-color: {sum_bg}; padding: 12px; border-radius: 6px; border-left: 4px solid #facc15; font-size: 0.95rem; color: {t_text_c}; margin-top: 15px;'><b>【結  果】</b>{chip_res_text}</div></div>"

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
        eps_f, pe_f = float(eps), float(pe) if pe != "無" else 999
        if eps_f > 0 and pe_f < 20: fund_res = "🔥 具備實質獲利支撐，且本益比合理，具投資價值。"
        elif eps_f > 0 and pe_f >= 20: fund_res = "⚠️ 公司雖有獲利，但目前的本益比估值偏高，需留意追高風險。"
        else: fund_res = "🩸 暫無明顯獲利支撐，或呈現虧損，需嚴防營運風險。"
    except: fund_res = "⚪ 基礎財報數據不足，暫以技術與籌碼面為主。"

    fund_html = f"<div style='border: 1px solid {b_col}; border-radius: 8px; padding: 15px; margin-bottom: 15px; background-color: {card_bg};'>"
    fund_html += f"<h4 style='color: #c084fc; margin-top: 0; font-size: 1.2rem;'>📑 基本面分析</h4><ul style='font-size: 0.95rem; line-height: 1.6; color: {t_text_c};'>"
    for b in fund_bullets: fund_html += f"<li style='margin-bottom:6px;'>{b}</li>"
    fund_html += f"</ul><div style='background-color: {sum_bg}; padding: 12px; border-radius: 6px; border-left: 4px solid #c084fc; font-size: 0.95rem; color: {t_text_c};'><b>【結  果】</b>{fund_res}</div></div>"

    return tech_html + chip_html + fund_html

def draw_professional_chart(df, ticker_name, latest_price, view_days, is_light_mode, show_buy_signal=False, f_data=None, show_sup_res=False, show_signals=True, buy_dates=[]):
    df_view = df.tail(view_days)
    colors = ['#ef4444' if row['Close'] >= row['Open'] else '#22c55e' for _, row in df_view.iterrows()]
    last_row = df_view.iloc[-1]
    x_vals = df_view.index.strftime('%Y-%m-%d')
    fig = make_subplots(rows=4, cols=1, shared_xaxes=True, row_heights=[0.45, 0.15, 0.15, 0.25], vertical_spacing=0.06)
    
    line_k, line_d, line_j = ("#3b82f6", "#f59e0b", "#a855f7") if is_light_mode else ("#60a5fa", "#fbbf24", "#c084fc")
    grid_c = "rgba(0,0,0,0.1)" if is_light_mode else "rgba(255,255,255,0.05)"
    bg_c = "#ffffff" if is_light_mode else "#0b1120"
    text_c = "#333" if is_light_mode else "#94a3b8"
    ann_bg = "rgba(255,255,255,0.8)" if is_light_mode else "rgba(15,23,42,0.8)"
    
    fig.add_trace(go.Candlestick(x=x_vals, open=df_view['Open'], high=df_view['High'], low=df_view['Low'], close=df_view['Close'], increasing_line_color='#ef4444', decreasing_line_color='#22c55e', name="K線"), row=1, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df_view['5MA'], line=dict(color='orange', width=2), name="5T"), row=1, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df_view['10MA'], line=dict(color='#facc15', width=2), name="10T"), row=1, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df_view['20MA'], line=dict(color='cyan', width=2), name="20T"), row=1, col=1)
    fig.add_hline(y=latest_price, line_dash="dash", line_color="#facc15", row=1, col=1)
    
    if show_sup_res:
        fig.add_hline(y=df_view['High'].max(), line_dash="dash", line_color="#ef4444", row=1, col=1, annotation_text=f"壓力 {df_view['High'].max():.2f}", annotation_position="top right", annotation_font=dict(size=12, color="#ef4444"))
        fig.add_hline(y=df_view['Low'].min(), line_dash="dash", line_color="#22c55e", row=1, col=1, annotation_text=f"支撐 {df_view['Low'].min():.2f}", annotation_position="bottom right", annotation_font=dict(size=12, color="#22c55e"))
    
    re_x, re_y, re_text, be_x, be_y, be_text = [], [], [], [], [], []
    sup_x, sup_y, sup_text, res_x, res_y, res_text = [], [], [], [], [], []
    start_pos = len(df) - len(df_view)
    
    for i, date in enumerate(df_view.index):
        pos = start_pos + i
        if pos >= 1:
            t, p = df.iloc[pos], df.iloc[pos-1]
            t_open, t_close, t_high, t_low = t['Open'], t['Close'], t['High'], t['Low']
            p_open, p_close = p['Open'], p['Close']
            
            if (p_open > p_close) and (t_close > t_open) and (t_close > p_open) and (t_open < p_close):
                re_x.append(date.strftime('%Y-%m-%d')); re_y.append(t_low * 0.98); re_text.append("<b>紅吞</b>")
            if (p_close > p_open) and (t_open > t_close) and (t_open > p_close) and (t_close < p_open):
                be_x.append(date.strftime('%Y-%m-%d')); be_y.append(t_high * 1.02); be_text.append("<b>黑吞</b>")
            
            total_range = t_high - t_low if t_high - t_low != 0 else 0.001
            if (min(t_open, t_close) - t_low > abs(t_close - t_open) * 1.5) and ((min(t_open, t_close) - t_low) / total_range > 0.4) and (t_low < p_close):
                sup_x.append(date.strftime('%Y-%m-%d')); sup_y.append(t_low * 0.95); sup_text.append("<b>撐</b>")

    if show_signals:
        if re_x: fig.add_trace(go.Scatter(x=re_x, y=re_y, mode='text', text=re_text, textposition="bottom center", textfont=dict(color="#ef4444", size=13), hoverinfo='skip'), row=1, col=1)
        if be_x: fig.add_trace(go.Scatter(x=be_x, y=be_y, mode='text', text=be_text, textposition="top center", textfont=dict(color="#22c55e", size=13), hoverinfo='skip'), row=1, col=1)
        if sup_x: fig.add_trace(go.Scatter(x=sup_x, y=sup_y, mode='text', text=sup_text, textposition="bottom center", textfont=dict(color="#facc15", size=13), hoverinfo='skip'), row=1, col=1)

    if show_buy_signal and buy_dates:
        buy_x, buy_y = [], []
        for d in buy_dates:
            if d in df_view.index:
                idx = df_view.index.get_loc(d)
                buy_x.append(d.strftime('%Y-%m-%d')); buy_y.append(df_view['Low'].iloc[idx] * 0.90)
        if buy_x:
            fig.add_trace(go.Scatter(x=buy_x, y=buy_y, mode='markers+text', marker=dict(symbol='triangle-up', size=14, color='#34d399'), text=["買"]*len(buy_x), textposition="bottom center", textfont=dict(color="#34d399", size=11, weight="bold"), hoverinfo='skip'), row=1, col=1)
            
    fig.add_trace(go.Bar(x=x_vals, y=df_view['Volume'], marker_color=colors, name="VOL"), row=2, col=1)
    macd_colors = ['#ef4444' if val > 0 else '#22c55e' for val in df_view.get('MACD_Hist', [0]*len(df_view))]
    fig.add_trace(go.Bar(x=x_vals, y=df_view.get('MACD_Hist', 0), marker_color=macd_colors, name="OSC"), row=3, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df_view.get('MACD', 0), line=dict(color=line_k, width=1.5), name="DIF"), row=3, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df_view.get('Signal', 0), line=dict(color=line_d, width=1.5), name="MACD"), row=3, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df_view.get('K', 50), line=dict(color=line_k, width=1.5), name="K"), row=4, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df_view.get('D', 50), line=dict(color=line_d, width=1.5), name="D"), row=4, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df_view.get('J', 50), line=dict(color=line_j, width=1.5), name="J"), row=4, col=1)

    fig.update_xaxes(type='category', nticks=15, fixedrange=True, showgrid=True, gridcolor=grid_c)
    fig.update_yaxes(fixedrange=True, showgrid=True, gridcolor=grid_c)
    fig.update_layout(xaxis_rangeslider_visible=False, template="plotly_white" if is_light_mode else "plotly_dark", height=850, margin=dict(l=10, r=10, t=10, b=30), paper_bgcolor=bg_c, plot_bgcolor=bg_c, hovermode='x unified', hoverlabel=dict(bgcolor=bg_c, font_size=13, font_color=text_c), dragmode=False, showlegend=False)
    fig.add_annotation(text="📊 資料來源: yfinance / TWSE / WantGoo", xref="paper", yref="paper", x=1.0, y=-0.05, showarrow=False, font=dict(size=12, color=text_c))
    return fig

def generate_cards_html(df_disp, is_intraday):
    cards_html = ""
    for _, r in df_disp.iterrows():
        p_val = r.get('漲跌', 0)
        p_col = "#ef4444" if p_val >= 0 else "#22c55e"
        p_bg = "rgba(239,68,68,0.1)" if p_val >= 0 else "rgba(34,197,94,0.1)"
        change_sign = "+" if p_val > 0 else ""
        
        score = r.get('Intraday_Score', 50) if is_intraday else r.get('Score', 0)
        if is_intraday:
            s_col = "#ef4444" if score >= 80 else ("#facc15" if score >= 60 else "#22c55e")
            rating = "動能"
        else:
            s_col = "#ef4444" if score >= 5 else ("#facc15" if score >= 2 else "#22c55e")
            rating = r.get('評級', '觀望').replace('🟢 ', '').replace('🟡 ', '').replace('⚪ ', '')
            
        r_col = "#4ade80" if rating == "S級" else ("#facc15" if rating == "A級" else "#94a3b8")
        stock_link = f'href="/?stock={r.get("代號", "")}" target="_self"'
        
        cards_html += f"<div style='background-color: #0f172a; border: 1px solid #1e293b; border-radius: 12px; padding: 14px; margin-bottom: 12px; position: relative; overflow: hidden;'>"
        cards_html += f"<div style='display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; position: relative; z-index: 10;'>"
        cards_html += f"<div style='display: flex; align-items: center; gap: 12px;'>"
        cards_html += f"<div style='width: 50px; height: 50px; border-radius: 50%; background: radial-gradient(circle, #1e293b 0%, #0b1120 100%); border: 1px solid #334155; display: flex; flex-direction: column; align-items: center; justify-content: center; flex-shrink: 0; box-shadow: inset 0 2px 4px rgba(255,255,255,0.05), 0 4px 8px rgba(0,0,0,0.4);'>"
        cards_html += f"<span style='color: {s_col}; font-weight: 800; font-size: 1.2rem; line-height: 1;'>{score}</span>"
        cards_html += f"<span style='color: {r_col}; font-size: 0.65rem; font-weight: 800; margin-top: 2px;'>{rating}</span></div>"
        
        cards_html += f"<a {stock_link} class='stock-card-link'><div style='display: flex; align-items: center; gap: 6px;'>"
        cards_html += f"<span class='stock-name-hover' style='color: #f8fafc; font-weight: bold; font-size: 1.15rem; transition: color 0.2s;'>{r.get('名稱', '')}</span>"
        if r.get("Theme_Name", "一般題材") != "一般題材":
            cards_html += f"<span style='font-size: 0.7rem; background-color: rgba(79,70,229,0.15); color: #818cf8; border: 1px solid rgba(79,70,229,0.3); padding: 2px 6px; border-radius: 4px; white-space: nowrap; font-weight: 600;'>{r.get('Theme_Icon', '🏷️')} {r.get('Theme_Name', '')}</span>"
        cards_html += f"</div><div style='font-size: 0.8rem; color: #64748b; margin-top: 4px; font-family: monospace;'>{r.get('代號', '')} <span style='color:#475569; font-size:0.7rem; margin-left:4px;'>(點擊解析)</span></div></a></div>"
        
        cards_html += f"<div style='text-align: right; flex-shrink: 0;'><div style='color: {p_col}; font-weight: 800; font-size: 1.2rem; font-family: monospace;'>{r.get('收盤價', 0):.1f}</div>"
        cards_html += f"<div style='background-color: {p_bg}; color: {p_col}; font-size: 0.75rem; padding: 2px 6px; border-radius: 4px; display: inline-block; font-weight: 800; font-family: monospace; margin-top: 4px;'>{change_sign}{r.get('漲跌幅', 0)}%</div></div></div>"
        
        if is_intraday:
            v_dev = r.get('VWAP_Dev', 0)
            v_col = "#ef4444" if v_dev > 0 else "#22c55e"
            est_vol = r.get('Est_Vol_Ratio', 1)
            ev_col = "#facc15" if est_vol > 1.3 else "#e2e8f0"
            flow_val = r.get('Flow', '內外盤拉扯')
            flow_col = "#ef4444" if "大單" in flow_val and "敲進" in flow_val else "#e2e8f0"
            wr_val = r.get('WinRate', 0.0)
            wr_col = "#ef4444" if wr_val >= 75 else ("#facc15" if wr_val >= 40 else "#22c55e")
            
            cards_html += f"<div style='display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; background-color: rgba(30,41,59,0.4); border: 1px solid rgba(51,65,85,0.5); padding: 10px; border-radius: 8px; font-size: 0.75rem; margin-bottom: 10px; position: relative; z-index: 10;'>"
            cards_html += f"<div style='display: flex; flex-direction: column;'><span style='color: #64748b; margin-bottom: 4px;'>VWAP乖離</span><span style='color: {v_col}; font-weight: bold; font-family: monospace;'>{'+' if v_dev>0 else ''}{v_dev:.1f}%</span></div>"
            cards_html += f"<div style='display: flex; flex-direction: column;'><span style='color: #64748b; margin-bottom: 4px;'>大單淨量</span><span style='color: {flow_col}; font-weight: bold;'>{flow_val}</span></div>"
            cards_html += f"<div style='display: flex; flex-direction: column;'><span style='color: #64748b; margin-bottom: 4px;'>預估量能</span><span style='color: {ev_col}; font-weight: bold; font-family: monospace;'>{est_vol:.1f}x</span></div>"
            cards_html += f"<div style='display: flex; flex-direction: column;'><span style='color: #64748b; margin-bottom: 4px;'>歷史勝率</span><span style='color: {wr_col}; font-weight: bold; font-family: monospace;'>{wr_val}%</span></div></div>"
            cards_html += f"<div style='font-size: 0.75rem; color: #fbbf24; display: flex; align-items: flex-start; gap: 6px; position: relative; z-index: 10;'><span style='margin-top: 1px;'>⚡</span><span style='line-height: 1.4; font-weight: 500;'>盤中訊號：{r.get('Intraday_Signal', '穩守均價線')}</span></div>"
        else:
            wr_val = r.get('WinRate', 0.0)
            wr_col = "#ef4444" if wr_val >= 75 else ("#facc15" if wr_val >= 40 else "#22c55e")
            rrr_val = r.get('RRR', 1.5)
            w_net = r.get('Whale_Net', 0)
            w_col = "#ef4444" if w_net > 0 else ("#22c55e" if w_net < 0 else "#94a3b8")
            whale_str = f"+{w_net:,}" if w_net > 0 else f"{w_net:,}"
            
            cards_html += f"<div style='display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; background-color: rgba(30,41,59,0.4); border: 1px solid rgba(51,65,85,0.5); padding: 10px; border-radius: 8px; font-size: 0.75rem; margin-bottom: 10px; position: relative; z-index: 10;'>"
            cards_html += f"<div style='display: flex; flex-direction: column;'><span style='color: #64748b; margin-bottom: 4px;'>歷史勝率</span><span style='color: {wr_col}; font-weight: bold; font-family: monospace;'>{wr_val}%</span></div>"
            cards_html += f"<div style='display: flex; flex-direction: column;'><span style='color: #64748b; margin-bottom: 4px;'>風報比 RRR</span><span style='color: #e2e8f0; font-weight: bold; font-family: monospace;'>1 : {rrr_val}</span></div>"
            cards_html += f"<div style='display: flex; flex-direction: column;'><span style='color: #64748b; margin-bottom: 4px;'>法人淨買</span><span style='color: {w_col}; font-weight: bold; font-family: monospace;'>{whale_str}</span></div></div>"
            cards_html += f"<div style='font-size: 0.75rem; color: #fbbf24; display: flex; align-items: flex-start; gap: 6px; position: relative; z-index: 10;'><span style='margin-top: 1px;'>⚡</span><span style='line-height: 1.4; font-weight: 500;'>主力特徵：{r.get('Feature', '一般')}</span></div>"
        
        cards_html += f"</div>"
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
        
        col_m1, col_m2 = st.columns([1, 1])
        with col_m1:
            radar_mode = st.radio("引擎模式：", ["盤後波段精算 (雲端快篩)", "盤中動能快篩 (雲端快篩)"], horizontal=True, label_visibility="collapsed")
        is_intraday = "盤中" in radar_mode
        st.session_state.is_intraday = is_intraday
        
        available_themes = ["全部題材"] + sorted(list(set(df_results['Theme_Name'].unique()) - {"一般題材"}))
        selected_theme = st.radio("題材過濾：", available_themes, horizontal=True, label_visibility="collapsed")
        if selected_theme != "全部題材": df_results = df_results[df_results['Theme_Name'] == selected_theme]
            
        available_features = ["全部特徵"] + sorted(list(set(df_results['Feature'].unique())))
        selected_feature = st.radio("特徵過濾：", available_features, horizontal=True, label_visibility="collapsed")
        if selected_feature != "全部特徵": df_results = df_results[df_results['Feature'] == selected_feature]
        
        if not df_results.empty:
            df_disp = df_results.sort_values(by=['Intraday_Score' if is_intraday else 'Score', '漲跌幅'], ascending=[False, False]).head(30)
        else: df_disp = df_results

        st.session_state.nav_pool = df_disp['ticker_raw'].tolist()
        st.session_state.nav_pool_data = df_disp.to_dict('records') 
            
        st.markdown(f"<div style='font-size:0.8rem; color:#94a3b8; border-bottom:1px solid #1e293b; padding-bottom:8px; margin-bottom:16px;'>⚡ 雲端秒級同步完成 | 當前符合條件標的共 {len(df_disp)} 檔</div>", unsafe_allow_html=True)
        
        if df_disp.empty: st.markdown("<div style='text-align: center; padding: 40px; color: #64748b; font-size: 0.9rem;'>此條件下暫無符合條件的標的。</div>", unsafe_allow_html=True)
        else: st.markdown(generate_cards_html(df_disp, is_intraday), unsafe_allow_html=True)
    else:
        st.info("💡 雲端資料庫目前無暫存數據，請確保您的 GitHub Actions 排程已至少順利執行過一次。")

# ==========================================
# 🚀 模擬交易紀錄獨立頁面
# ==========================================
elif st.session_state.page == "simulated_orders":
    st.markdown("<h2 style='text-align: center; color: #818cf8; margin-bottom: 20px;'>🛒 我的模擬下單紀錄</h2>", unsafe_allow_html=True)
    
    col_home, col_clear = st.columns([1, 1])
    with col_home:
        if st.button("🏠 回雷達總機", use_container_width=True): st.session_state.page = "home"; st.rerun()
    with col_clear:
        if st.button("🗑️ 清空所有紀錄", use_container_width=True):
            st.session_state.simulated_orders = []
            save_cloud_data("user_data", "simulated_orders", [])
            st.success("已清除所有紀錄！"); st.rerun()
            
    orders = st.session_state.get('simulated_orders', [])
    if not orders: st.info("目前沒有模擬下單紀錄。")
    else:
        if "delete_order_id" in st.session_state:
            st.session_state.simulated_orders = [o for o in orders if o.get('id') != st.session_state.delete_order_id]
            save_cloud_data("user_data", "simulated_orders", st.session_state.simulated_orders)
            del st.session_state["delete_order_id"]; st.rerun()
            
        for idx, order in enumerate(orders):
            df_temp = get_stock_data(order['ticker'])
            curr_price = float(df_temp['Close'].iloc[-1]) if df_temp is not None else order['buy_price']
            ma10 = float(df_temp['10MA'].iloc[-1]) if df_temp is not None else order['buy_price']
            atr = float(df_temp.get('ATR', pd.Series([order['buy_price'] * 0.03])).iloc[-1]) if df_temp is not None else order['buy_price'] * 0.03
            
            if 'highest_price' not in order: order['highest_price'] = order['buy_price']
            if curr_price > order['highest_price']: 
                order['highest_price'] = curr_price
                save_cloud_data("user_data", "simulated_orders", st.session_state.simulated_orders)
                
            dynamic_stop = order['highest_price'] - (2 * atr)
            pl_val = curr_price - order['buy_price']
            pl_pct = (pl_val / order['buy_price']) * 100 if order['buy_price'] > 0 else 0
            
            if curr_price < ma10: status_html = f"<div style='background-color: rgba(239,68,68,0.2); color: #ef4444; border: 1px solid #ef4444; padding: 2px 8px; border-radius: 4px; font-size: 0.75rem; font-weight: bold; margin-left: 8px;'>📉 跌破 10MA ({ma10:.1f})，停利出場</div>"
            elif curr_price < dynamic_stop: status_html = f"<div style='background-color: rgba(34,197,94,0.2); color: #22c55e; border: 1px solid #22c55e; padding: 2px 8px; border-radius: 4px; font-size: 0.75rem; font-weight: bold; margin-left: 8px;'>🛑 回檔2倍ATR，停損出場</div>"
            else: status_html = f"<div style='background-color: rgba(96,165,250,0.1); color: #60a5fa; border: 1px solid #60a5fa; padding: 2px 8px; border-radius: 4px; font-size: 0.75rem; font-weight: bold; margin-left: 8px;'>🚀 獲利奔跑中</div>"
            
            pl_col = "#ef4444" if pl_pct >= 0 else "#22c55e"
            pl_bg = "rgba(239,68,68,0.1)" if pl_pct >= 0 else "rgba(34,197,94,0.1)"
            
            with st.container(border=False):
                html = f"<div style='background-color: #0f172a; border: 1px solid #1e293b; border-radius: 12px; padding: 16px; margin-bottom: 14px;'><div style='display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 12px;'>"
                html += f"<a href='/?stock={order['ticker']}' target='_self' style='text-decoration:none;'><div style='display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; margin-bottom: 4px;'><span style='color: #f8fafc; font-weight: bold; font-size: 1.25rem;'>{order['name']}</span><span style='color: #64748b; font-family: monospace; font-size: 0.9rem;'>{order['ticker']}</span>{status_html}</div><div style='font-size: 0.75rem; color: #64748b;'>下單時間: {order['time']}</div></a>"
                html += f"<div style='text-align: right;'><div style='font-size: 0.8rem; color: #94a3b8; margin-bottom: 2px;'>最新現價 / 報酬率</div><div style='font-size: 1.3rem; font-weight: bold; font-family: monospace; color: {pl_col}; line-height: 1.1;'>{curr_price:.1f}</div><div style='font-size: 0.85rem; font-weight: bold; font-family: monospace; color: {pl_col}; background-color: {pl_bg}; padding: 2px 6px; border-radius: 4px; display: inline-block; margin-top: 4px;'>{'+' if pl_pct>0 else ''}{pl_pct:.2f}%</div></div></div>"
                
                html += f"<div style='display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; background-color: rgba(0,0,0,0.2); border: 1px solid rgba(255,255,255,0.05); padding: 10px; border-radius: 8px;'>"
                html += f"<div style='display: flex; flex-direction: column; align-items: center;'><span style='font-size: 0.7rem; color: #64748b; margin-bottom: 4px;'>買進成本</span><span style='font-size: 1rem; font-weight: bold; color: #e2e8f0; font-family: monospace;'>{order['buy_price']:.1f}</span></div>"
                html += f"<div style='display: flex; flex-direction: column; align-items: center;'><span style='font-size: 0.7rem; color: #64748b; margin-bottom: 4px;'>創高紀錄</span><span style='font-size: 1rem; font-weight: bold; color: #facc15; font-family: monospace;'>{order['highest_price']:.1f}</span></div>"
                html += f"<div style='display: flex; flex-direction: column; align-items: center;'><span style='font-size: 0.7rem; color: #64748b; margin-bottom: 4px;'>動態防護線</span><span style='font-size: 1rem; font-weight: bold; color: #34d399; font-family: monospace;'>{max(ma10, dynamic_stop):.1f}</span></div>"
                html += f"<div style='display: flex; flex-direction: column; align-items: center;'><span style='font-size: 0.7rem; color: #64748b; margin-bottom: 4px;'>風報比 (RRR)</span><span style='font-size: 1rem; font-weight: bold; color: #facc15; font-family: monospace;'>1 : {order.get('rrr', 1.5)}</span></div></div>"
                st.markdown(html, unsafe_allow_html=True)
                
                if st.button(f"❌ 刪除此單 ({order['name']})", key=f"btn_del_{order['id']}_{idx}"):
                    st.session_state.delete_order_id = order['id']; st.rerun()

# ==========================================
# 🚀 進入單一個股解析頁面
# ==========================================
elif st.session_state.page == "analysis":
    target = st.session_state.current_stock
    c_name = get_stock_name(target)
    
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
        inst_data = get_institutional_trading(target)
        f_data = get_fundamental_and_industry_data(target, df_slice['Close'].iloc[-1])
        data = analyze_today(df_slice, target, inst_data, is_light_mode, pre_fund=f_data)
        sc = data['Score']
        
        win_rate, closed_signals, s_count, a_count, buy_dates = calculate_historical_winrate(target)
        
        display_time = get_stock_live_time(target)
        p_color = '#ef4444' if data['漲跌'] >= 0 else '#22c55e'
        
        st.markdown(f"<h2 style='text-align: center; margin-bottom: 5px;'>🎯 {target} {c_name}</h2>", unsafe_allow_html=True)
        st.markdown(f"<div style='text-align: center; color: #888; font-size: 1.1rem;'>【{f_data['Industry']}】</div>", unsafe_allow_html=True)
        st.markdown(f"<h3 style='text-align: center; color: {p_color}; font-size: 2.2rem; margin-bottom: 0px;'>{data['收盤價']} ({'+' if data['漲跌']>0 else ''}{data['漲跌幅']}%)</h3>", unsafe_allow_html=True)
        st.markdown(f"<div style='text-align: center; color: #888; font-size: 0.9rem; margin-bottom: 10px;'>🕒 抓取時間: {display_time}</div>", unsafe_allow_html=True)
        
        _, up_c, _ = st.columns([1, 2, 1])
        if up_c.button("🔄 更新個股即時數值", use_container_width=True): st.cache_data.clear(); st.rerun()
        st.markdown("---")
        
        st.markdown("##### 📊 ATR 動態勝率歷史回測 (近 1 季 / 90 日)")
        wr_color = "#ef4444" if win_rate >= 75 else ("#facc15" if win_rate >= 40 else "#22c55e")
        with st.container(border=True):
            col_sum1, col_sum2, col_sum3 = st.columns(3)
            with col_sum1: st.markdown(f"<div style='text-align:center; color:#888; font-size:0.9rem;'>動態波段勝率<br><span style='color:{wr_color}; font-size:1.8rem; font-weight:900;'>{win_rate:.1f}%</span></div>", unsafe_allow_html=True)
            with col_sum2: st.markdown(f"<div style='text-align:center; color:#888; font-size:0.9rem;'>🟢 S級 強烈買點<br><span style='font-size:1.8rem; font-weight:900; color:#ef4444;'>{s_count} 次</span></div>", unsafe_allow_html=True)
            with col_sum3: st.markdown(f"<div style='text-align:center; color:#888; font-size:0.9rem;'>🟡 A級 偏多試單<br><span style='font-size:1.8rem; font-weight:900; color:#facc15;'>{a_count} 次</span></div>", unsafe_allow_html=True)
            summary_text = f"過去 90 日共觸發 **{closed_signals}** 次有效買點。短線波段勝率達 <span style='color:{wr_color}; font-weight:bold;'>{win_rate:.1f}%</span>。當前建議之風報比 (RRR) 為 1 : {data['RRR']}。" if closed_signals > 0 else "過去 90 日內尚未產生足夠的歷史買進訊號。"
            st.markdown(f"<div style='margin-top:12px; padding:12px; background-color:rgba(30,41,59,0.5); border-radius:8px; line-height: 1.6; font-size:0.95rem; color:#cbd5e1;'>📝 <b>回測總結：</b>{summary_text}</div>", unsafe_allow_html=True)

        v_c = "#22c55e" if sc < 2 else ("#facc15" if sc < 5 else "#ef4444")
        v_t = "🔴 空手觀望" if sc < 2 else ("🟡 A級試單" if sc < 5 else "🟢 S級強烈買進")
        st.markdown(f"""
        <div style="border: 2px solid {v_c}; border-radius: 10px; padding: 20px; margin-bottom: 20px; background-color: #0b1120;">
            <h3 style="text-align: center; color: {v_c}; margin-top: 0; font-size: 1.8rem; margin-bottom: 20px;">🤖 雙引擎決策大腦：{v_t.replace('🟢 ', '').replace('🟡 ', '').replace('🔴 ', '')}</h3>
            <div style="background-color: rgba(30,41,59,0.5); padding: 15px; border-radius: 8px; border-left: 5px solid {v_c}; margin-bottom:20px;">
                <p style="font-size: 1.15rem; color: #f8fafc; margin: 0; line-height: 1.6;">
                    ✅ <b>進階 ATR 目標精算</b><br>合理停利目標為 <b style='color:#ef4444;'>{data['ATR_Target']}</b> ({data['ATR_Target_Pct']:.1f}%)，嚴格停損設於 <b style='color:#22c55e;'>{data['ATR_Stop']}</b> ({data['ATR_Stop_Pct']:.1f}%)。<br>
                    風報比 (Risk-Reward) 為 <b>1 : {data['RRR']}</b>。
                </p>
            </div>
            {generate_comprehensive_analysis(data, inst_data, sc, f_data, is_light_mode)}
        </div>""", unsafe_allow_html=True)
        
        if st.button("🛒 執行模擬下單 (套用最新移動停利引擎)", use_container_width=True):
            new_order = {
                "id": str(int(time.time())), "ticker": target, "name": c_name, "buy_price": data['收盤價'],
                "highest_price": data['收盤價'], "target_price": data['ATR_Target'], "stop_price": data['ATR_Stop'],
                "rrr": data['RRR'], "time": datetime.now(timezone(timedelta(hours=8))).strftime('%Y/%m/%d %H:%M:%S')
            }
            st.session_state.simulated_orders.insert(0, new_order)
            save_cloud_data("user_data", "simulated_orders", st.session_state.simulated_orders)
            st.success("✅ 模擬交易設定成功！已同步至 Firebase 雲端保險箱。"); st.balloons()
        
        dc1, dc2, dc3, dc5, dc6, dc7 = st.columns([0.8, 0.8, 0.8, 1.3, 1.3, 1.3])
        dc1.button("30日", on_click=set_view_days, args=(30,))
        dc2.button("60日", on_click=set_view_days, args=(60,))
        dc3.button("90日", on_click=set_view_days, args=(90,))
        with dc5: current_show_buy = st.toggle("🛒 顯示買進", value=True)
        with dc6: current_show_sup = st.toggle("📏 歷史高低點", value=True)
        with dc7: current_show_signals = st.toggle("🏷️ 顯示符號", value=True)
            
        fig = draw_professional_chart(df_slice, target, data['收盤價'], st.session_state.view_days, is_light_mode, current_show_buy, f_data, current_show_sup, current_show_signals, buy_dates=buy_dates)
        st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False, 'scrollZoom': False})
        
        with st.expander("📖 K 線圖符號代表名稱說明 (點擊展開)", expanded=False):
            st.markdown("""<ul style="line-height: 1.8; font-size: 1rem;">
                <li><b><span style='color: #ef4444;'>紅吞</span></b>：強烈的短線反轉向上買進訊號。</li><li><b><span style='color: #22c55e;'>黑吞</span></b>：強烈的短線高檔反轉向下警訊。</li>
                <li><b><span style='color: #facc15;'>撐 (橘黃字)</span></b>：回測有撐，當日價格下殺後爆出買盤收長下影線，主力防守支撐。</li>
                <li><b><span style='color: #34d399;'>買 (青色指標)</span></b>：系統 AI 綜合動能計算出之買點。</li></ul>""", unsafe_allow_html=True)
        
        st.divider()
        st.subheader("⭐ 自選群組管理")
        all_groups = list(st.session_state.fav_groups.keys())
        current_groups = [g for g, s in st.session_state.fav_groups.items() if target in s]
        selected_groups = st.multiselect("將此標的加入以下群組：", options=all_groups, default=current_groups)
        if st.button("💾 儲存自選設定", use_container_width=True, type="primary"):
            for g in all_groups:
                if g in selected_groups and target not in st.session_state.fav_groups[g]: st.session_state.fav_groups[g].append(target)
                elif g not in selected_groups and target in st.session_state.fav_groups[g]: st.session_state.fav_groups[g].remove(target)
            save_cloud_data("user_settings", "fav_groups", st.session_state.fav_groups)
            st.success("✅ 群組設定已更新！"); st.rerun()

        st.divider()
        st.markdown(f'''<div style="font-size: 1.4rem; font-weight: bold; color: #facc15; margin-bottom: 16px;">同步監控雷達清單 (首頁快篩結果)</div>''', unsafe_allow_html=True)
        if n_pool and 'nav_pool_data' in st.session_state:
            df_nav = pd.DataFrame(st.session_state.nav_pool_data)
            df_nav = df_nav[df_nav['ticker_raw'] != target]
            if not df_nav.empty: st.markdown(generate_cards_html(df_nav, st.session_state.get('is_intraday', True)), unsafe_allow_html=True)
            else: st.info("目前清單中已無其他符合條件的標的。")
    else: st.error("查無此股票資料。")
