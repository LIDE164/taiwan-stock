# 最後修改時間: 2026-06-25 12:45 CST
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
import random

from streamlit_autorefresh import st_autorefresh

# === 使用者專屬 FinMind API Token ===
FINMIND_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjoiYTQ1Njg4MTUwQGdtYWlsLmNvbSIsImVtYWlsIjoiYTQ1Njg4MTUwQGdtYWlsLmNvbSIsInRva2VuX3ZlcnNpb24iOjB9.LUcb8YPV4yo93_aB3obP4Z5iUGqAgTaH28ySx[...]

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

components.html(
    """
    <script>
        var body = window.parent.document.querySelector('.main');
        if (body) { body.scrollTo({top: 0, behavior: 'smooth'}); }
    </script>
    """,
    height=0, width=0
)

st.sidebar.title("⚙️ 介面設定")
is_light_mode = st.sidebar.toggle("🌞 黑白底色切換", False, key="toggle_theme_mode")

if st.sidebar.button("🗑️ 強制清除快取資料", use_container_width=True, key="btn_clear_cache"):
    st.cache_data.clear()
    st.sidebar.success("已清除暫存，請重整網頁！")

bg_col = "#ffffff" if is_light_mode else "#1a1c24"
border_col = "#ddd" if is_light_mode else "#333"
text_col = "#333" if is_light_mode else "#ddd"
title_col = "#111" if is_light_mode else "#fff"
sub_text_col = "#666" if is_light_mode else "#888"
val_col = "#0066cc" if is_light_mode else "#00ffcc"
sticky_bg = "rgba(255,255,255,0.95)" if is_light_mode else "rgba(26,28,36,0.95)"
app_bg = "#f4f6f9" if is_light_mode else "#0e1117"
panel_bg = "#f9f9f9" if is_light_mode else "#16181f"

css_style = """
<style>
    .stApp { background-color: """ + app_bg + """; }
    #MainMenu {visibility: hidden;} footer {visibility: hidden;}
    [data-testid="collapsedControl"] { border: 1px solid """ + border_col + """ !important; border-radius: 8px !important; background-color: """ + bg_col + """ !important; padding: 5px 12px !important; }
    [data-testid="collapsedControl"]::after { content: " ⭐ 我的群組"; font-size: 1.1rem; font-weight: bold; color: #ffcc00; margin-left: 8px; }
    .stButton button { font-weight: bold !important; border-radius: 8px !important; text-align: left !important; }
    button[kind="primary"] { font-size: 1.5rem !important; padding: 15px !important; border-radius: 12px !important; background-color: #ffcc00 !important; color: #111 !important; border: none !important; }
    .sticky-header { position: sticky; top: 0; z-index: 999; background-color: """ + sticky_bg + """; padding: 10px 0; border-bottom: 1px solid """ + border_col + """; backdrop-filter: blur(5px); margin-bottom: 10px; }
    div[data-testid="stVerticalBlockBorderWrapper"] > div { background-color: """ + bg_col + """ !important; border-color: """ + border_col + """ !important; padding: 4px !important; }
    h1, h2, h3, h4, p, span { color: """ + title_col + """ !important; }
    .compact-btn button { padding: 0.25rem 0.5rem !important; font-size: 1rem !important; }
    .risk-bar-container { width: 100%; background-color: #333; border-radius: 8px; margin-top: 5px; margin-bottom: 15px; overflow: hidden; }
    .risk-bar-fill { height: 16px; border-radius: 8px; transition: width 0.5s ease-in-out; }
    [data-testid="stExpander"] { border-color: """ + border_col + """ !important; background-color: """ + bg_col + """ !important; border-radius: 8px !important; margin-bottom: 15px; }
</style>
"""
st.markdown(css_style, unsafe_allow_html=True)

STOCK_NAMES = { "2330": "台積電", "2317": "鴻海", "2454": "聯發科", "2308": "台達電", "2382": "廣達", "2376": "技嘉", "1802": "台玻", "2603": "長榮", "1785": "光洋科", "1519": "華磊"}

@st.cache_data(ttl=86400)
def get_all_tw_stock_names():
    names = STOCK_NAMES.copy()
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", timeout=5)
        for i in res.json(): names[i['Code']] = i['Name']
        res2 = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes", timeout=5)
        for i in res2.json(): names[i['SecuritiesCompanyCode']] = i['CompanyName']
    except: pass
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
            st.session_state.search_error = f"⚠️ 找不到與「{s_val}」相關的標的。"

if "search_error" in st.session_state and st.session_state.search_error:
    st.sidebar.warning(st.session_state.search_error)
    st.session_state.search_error = ""

st.sidebar.divider()
st.sidebar.title("⏱️ 盤中即時跳動雷達")
auto_refresh = st.sidebar.toggle("🟢 開啟即時自動更新 (每30秒)", False, key="auto_refresh_toggle")

if auto_refresh:
    st_autorefresh(interval=30000, limit=None, key="market_auto_refresh")
    st.sidebar.success("⚡ 盤中高頻探測已啟動！")

st.sidebar.divider()

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
        soup = BeautifulSoup(res.text, 'html.parser')
        h2 = soup.find('h2')
        if h2:
            name = h2.text.strip()
            if name and not name.isdigit(): return name
    except: pass
    try:
        res = requests.get(f"https://histock.tw/stock/{ticker}", headers={'User-Agent': 'Mozilla/5.0'}, timeout=3)
        soup = BeautifulSoup(res.text, 'html.parser')
        title = soup.find('title')
        if title:
            name = title.text.split('(')[0].strip()
            if name and ticker not in name and "嗨投資" not in name and not name.isdigit(): return name
    except: pass
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

FAV_FILE = "favorites.json" 
FAV_GROUPS_FILE = "fav_groups.json" 
POOL_FILE = "pool.json"
FILTER_PRESETS_FILE = "filter_presets.json"

def load_json(fp, default):
    if os.path.exists(fp):
        try:
            with open(fp, "r", encoding="utf-8") as f: return json.load(f)
        except: pass
    return default

def save_json(fp, data):
    with open(fp, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False)

if 'page' not in st.session_state: st.session_state.page = "home"
if 'current_stock' not in st.session_state: st.session_state.current_stock = "2376"
if 'custom_pool' not in st.session_state: st.session_state.custom_pool = load_json(POOL_FILE, ["2330", "2317", "2454", "2382", "3231"])
if 'nav_pool' not in st.session_state: st.session_state.nav_pool = st.session_state.custom_pool
if 'scan_mode' not in st.session_state: st.session_state.scan_mode = "buy"
if 'view_days' not in st.session_state: st.session_state.view_days = 30
if 'date_offset' not in st.session_state: st.session_state.date_offset = 0
if 'filter_presets' not in st.session_state: st.session_state.filter_presets = load_json(FILTER_PRESETS_FILE, {})

if 'url_parsed' not in st.session_state:
    st.session_state.url_parsed = True
    params = st.query_params
    if 'stock' in params:
        st.session_state.current_stock = params['stock']
        st.session_state.page = "analysis"
        st.session_state.date_offset = 0

if 'fav_groups' not in st.session_state:
    default_groups = {"預設群組": ["1802", "2330", "1785"]}
    if os.path.exists(FAV_FILE) and not os.path.exists(FAV_GROUPS_FILE):
        old_favs = load_json(FAV_FILE, ["1802", "2330", "1785"])
        default_groups["預設群組"] = old_favs
    st.session_state.fav_groups = load_json(FAV_GROUPS_FILE, default_groups)

@st.cache_data(ttl=1800)
def fetch_twse_top_100():
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", timeout=10)
        df = pd.DataFrame(res.json())
        df['TradeVolume'] = pd.to_numeric(df['TradeVolume'], errors='coerce')
        return df[df['Code'].str.match(r'^\d{4}$')].sort_values(by='TradeVolume', ascending=False).head(100)['Code'].tolist()
    except: return ["2330", "2317", "2454", "2382", "3231"]

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
        except: pass
        return None

    df = fetch_twse_index_history() if base_ticker == "^TWII" else fetch_clean(f"{base_ticker}.TW")
    if df is None and base_ticker != "^TWII": df = fetch_clean(f"{base_ticker}.TWO")
    if df is None and base_ticker != "^TWII": df = fetch_clean(base_ticker)
    
    if df is None: return None
    
    try:
        url = "https://ws.cnyes.com/charting/api/v1/TWS:TSE01:INDEX/quote" if base_ticker == "^TWII" else f"https://ws.cnyes.com/charting/api/v1/TWS:{base_ticker}:STOCK/quote"
        res = requests.get(url, timeout=3)
        if res.status_code == 200:
            q = res.json()['data']['quote']
            c_price, o_price = float(q['23']), float(q['22'])
            h_price, l_price = float(q['25']), float(q['26'])
            v_vol = float(q.get('14', 0)) * 1000 if base_ticker != "^TWII" else 0
            ts = int(q['20'])
            dt_live = pd.to_datetime(datetime.fromtimestamp(ts, timezone(timedelta(hours=8))).strftime('%Y-%m-%d'))
            
            if dt_live not in df.index:
                new_row = pd.DataFrame({'Open': [o_price], 'High': [h_price], 'Low': [l_price], 'Close': [c_price], 'Volume': [v_vol]}, index=[dt_live])
                df = pd.concat([df, new_row])
            else:
                df.at[dt_live, 'Close'] = c_price
                df.at[dt_live, 'High'] = max(df.at[dt_live, 'High'], h_price)
                df.at[dt_live, 'Low'] = min(df.at[dt_live, 'Low'], l_price)
                if base_ticker != "^TWII": df.at[dt_live, 'Volume'] = max(df.at[dt_live, 'Volume'], v_vol)
    except: pass

    df['5MA'] = df['Close'].rolling(5).mean()
    df['10MA'] = df['Close'].rolling(10).mean()
    df['20MA'] = df['Close'].rolling(20).mean()
    df['60MA'] = df['Close'].rolling(60).mean()
    df['STD20'] = df['Close'].rolling(20).std()
    df['BB_UP'] = df['20MA'] + (2 * df['STD20'])
    df['BB_DN'] = df['20MA'] - (2 * df['STD20'])
    df['BIAS_20'] = (df['Close'] - df['20MA']) / df['20MA'] * 100
    df['MACD'] = df['Close'].ewm(span=12, adjust=False).mean() - df['Close'].ewm(span=26, adjust=False).mean()
    df['Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    df['MACD_Hist'] = df['MACD'] - df['Signal']
    low_9 = df['Low'].rolling(9).min()
    high_9 = df['High'].rolling(9).max()
    rsv = (df['Close'] - low_9) / (high_9 - low_9) * 100
    df['K'] = rsv.ewm(com=2, adjust=False).mean()
    df['D'] = df['K'].ewm(com=2, adjust=False).mean()
    df['J'] = 3 * df['K'] - 2 * df['D']
    return df

@st.cache_data(ttl=86400, show_spinner=False)
def get_fundamental_and_industry_data(ticker_number, current_price=0):
    base_ticker = str(ticker_number).strip().upper().replace(".TW", "").replace(".TWO", "")
    eps_val, pe_val = "無", "無"
    ind = "一般產業"
    try:
        url = f"https://invest.cnyes.com/twstock/TWS/{base_ticker}/overview"
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, 'html.parser')
            text = soup.get_text(separator='|')
            match = re.search(r'當季EPS\|+([\-\d\.]+)', text)
            if match: eps_val = match.group(1)
            else:
                res_api = requests.get(f"https://ws.cnyes.com/twstock/api/v1/company/profile/{base_ticker}", timeout=3)
                if res_api.status_code == 200:
                    data = res_api.json()
                    if 'data' in data and 'eps' in data['data']: eps_val = f"{float(data['data']['eps']):.2f}"
    except: pass
    try:
        info = yf.Ticker(f"{base_ticker}.TW").info
        if not info or 'industry' not in info: info = yf.Ticker(f"{base_ticker}.TWO").info
        sec, ind_eng = info.get("sector", ""), info.get("industry", "")
        tw_sec = ENG_TO_TW_INDUSTRY.get(sec, sec)
        tw_ind = ENG_TO_TW_INDUSTRY.get(ind_eng, ind_eng)
        ind_temp = f"{tw_sec} - {tw_ind}" if tw_sec and tw_ind else tw_sec or tw_ind or "一般產業"
        if not re.search(r'[a-zA-Z]', ind_temp): ind = ind_temp
        if eps_val == "無" and 'trailingEps' in info and info['trailingEps'] is not None:
            eps_val = str(round(info['trailingEps'], 2))
    except: pass
    try:
        if eps_val != "無":
            eps_f = float(eps_val)
            if eps_f > 0 and current_price > 0: pe_val = str(round(float(current_price) / eps_f, 2))
            elif eps_f <= 0: pe_val = "無 (EPS ≦ 0)"
    except: pass
    return {"EPS": eps_val, "PE": pe_val, "Industry": ind}

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
        res = session.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=3)
        if res.status_code == 200:
            data = res.json()
            if 'msgArray' in data and len(data['msgArray']) > 0:
                info = data['msgArray'][0]
                z, y, d, t = info.get('z'), info.get('y'), info.get('d'), info.get('t')
                curr = float(z.replace(',','')) if z and z != '-' else (float(y.replace(',','')) if y and y != '-' else 0)
                prev = float(y.replace(',','')) if y and y != '-' else curr
                if curr > 10000:
                    if d and t: update_time_str = f"{d[:4]}/{d[4:6]}/{d[6:]} {t}"
                    return curr, curr - prev, update_time_str
    except: pass
    if fallback_curr > 10000: return fallback_curr, fallback_change, update_time_str
    return 0, 0, "無資料"

@st.cache_data(ttl=5, show_spinner=False)
def get_stock_live_time(ticker):
    base_ticker = str(ticker).strip().upper().replace(".TW", "").replace(".TWO", "")
    tz_tpe = timezone(timedelta(hours=8))
    try:
        url = f"https://ws.cnyes.com/charting/api/v1/TWS:{base_ticker}:STOCK/quote"
        res = requests.get(url, timeout=3)
        if res.status_code == 200:
            ts = int(res.json()['data']['quote']['20'])
            return datetime.fromtimestamp(ts, tz_tpe).strftime('%Y/%m/%d %H:%M:%S')
    except: pass
    return datetime.now(tz_tpe).strftime('%Y/%m/%d %H:%M:%S')

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_twse_index_history():
    try:
        df = yf.Ticker("^TWII").history(period="1y")
        if not df.empty:
            df.index = pd.to_datetime(df.index.strftime('%Y-%m-%d'))
            return df[['Open', 'High', 'Low', 'Close', 'Volume']]
    except: pass
    return None

@st.cache_data(ttl=3600, show_spinner=False)
def get_institutional_trading(ticker):
    try:
        start_date = (datetime.now() - timedelta(days=20)).strftime('%Y-%m-%d')
        url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInstitutionalInvestorsBuySell&data_id={ticker}&start_date={start_date}&token={FINMIND_TOKEN}"
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            data = res.json()
            if data.get('msg') == 'success' and len(data.get('data', [])) > 0:
                df = pd.DataFrame(data['data'])
                df['net'] = (df['buy'] - df['sell']) / 1000  
                df['type'] = '其他'
                df.loc[df['name'].str.contains('Foreign|外資', case=False, na=False), 'type'] = '外資'
                df.loc[df['name'].str.contains('Trust|投信', case=False, na=False), 'type'] = '投信'
                df.loc[df['name'].str.contains('Dealer|自營', case=False, na=False), 'type'] = '自營商'
                pivot = df.groupby(['date', 'type'])['net'].sum().unstack(fill_value=0).reset_index()
                for col in ['外資', '投信', '自營商']:
                    if col not in pivot.columns: pivot[col] = 0
                pivot['單日合計'] = pivot['外資'] + pivot['投信'] + pivot['自營商']
                pivot = pivot.sort_values('date', ascending=False).head(10)
                res_list = []
                for _, row in pivot.iterrows():
                    res_list.append({
                        "日期": row['date'][-5:].replace("-", "/"),
                        "外資(張)": int(row['外資']), "投信(張)": int(row['投信']),
                        "自營商(張)": int(row['自營商']), "單日合計(張)": int(row['單日合計'])
                    })
                if res_list: return res_list
    except: pass
    return []

# ==========================================
# 🚀 新增：進階篩選功能模組 (針對短線操作優化)
# ==========================================

def apply_advanced_filters(scan_results_df, filters):
    """根據篩選條件過濾結果，支援短線交易策略"""
    df = scan_results_df.copy()
    
    # 技術面篩選 - 短線熱點
    if filters.get('kdj_oversold'):
        df = df[df['J值'] < 20]
    
    if filters.get('kdj_overbought'):
        df = df[df['J值'] > 80]
    
    if filters.get('bias_negative'):
        df = df[df['BIAS'] < -5]
    
    if filters.get('bias_positive'):
        df = df[df['BIAS'] > 7]
    
    if filters.get('bb_lower'):
        df = df[df['收盤價'] <= df['BB_DN'] * 1.02]
    
    if filters.get('bb_upper'):
        df = df[df['收盤價'] >= df['BB_UP'] * 0.98]
    
    if filters.get('ma_support'):
        df = df[df['收盤價'] > df['20MA']]
    
    if filters.get('ma_below'):
        df = df[df['收盤價'] < df['5MA']]
    
    if filters.get('red_candle'):
        df = df[df['紅吞'] == True]
    
    if filters.get('volume_expand'):
        df = df[df['成交量'] > df['5日均量'] * 1.1]
    
    if filters.get('volume_shrink'):
        df = df[df['成交量'] < df['5日均量'] * 0.8]
    
    if filters.get('macd_positive'):
        df = df[df['MACD柱'] > df['前日MACD柱']]
    
    if filters.get('macd_negative'):
        df = df[df['MACD柱'] < df['前日MACD柱']]
    
    if filters.get('support_test'):
        df = df[df['回測有撐'] == True]
    
    if filters.get('resistance_test'):
        df = df[df['反彈遇壓'] == True]
    
    # 基本面篩選
    pe_range = filters.get('pe_range', [0, 999])
    try:
        df['PE_val'] = pd.to_numeric(df['PE'].astype(str).str.replace('無.*', '999'), errors='coerce')
        df = df[(df['PE_val'] >= pe_range[0]) & (df['PE_val'] <= pe_range[1])]
        df = df.drop('PE_val', axis=1)
    except: pass
    
    eps_range = filters.get('eps_range', [0, 999])
    try:
        df['EPS_val'] = pd.to_numeric(df['EPS'].astype(str).str.replace('無.*', '0'), errors='coerce')
        df = df[(df['EPS_val'] >= eps_range[0]) & (df['EPS_val'] <= eps_range[1])]
        df = df.drop('EPS_val', axis=1)
    except: pass
    
    # 漲跌幅篩選
    change_range = filters.get('change_range', [-100, 100])
    df = df[(df['漲跌幅'] >= change_range[0]) & (df['漲跌幅'] <= change_range[1])]
    
    # 成交量篩選
    volume_range = filters.get('volume_range', [0, 999999])
    df = df[(df['成交量'] >= volume_range[0]) & (df['成交量'] <= volume_range[1])]
    
    # 評級篩選
    ratings = filters.get('ratings', [])
    if ratings:
        df = df[df['評級'].isin(ratings)]
    
    return df

@st.cache_data(ttl=180, show_spinner=False)
def get_global_scan_results(pool_tuple):
    scan_results = []
    def process_scan(stock):
        df = get_stock_data(stock)
        if df is not None: return analyze_today(df, stock, inst_data=None, is_light_mode=False)
        return None
        
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(process_scan, stock): stock for stock in pool_tuple}
        for future in concurrent.futures.as_completed(futures):
            try:
                res = future.result()
                if res: scan_results.append(res)
            except: pass
    return scan_results

def get_decision_score(data, fund_data, inst_data=None):
    sc, rs = 0, []
    if data['訊號']: sc+=3; rs.append("✅ 穩在月線上且KDJ超賣")
    if data['收盤價'] <= data['BB_DN'] * 1.02: sc+=2; rs.append("✅ 觸及布林下軌支撐")
    if data['BIAS'] < -5: sc+=1; rs.append("✅ 負乖離過大")
    
    try: eps_f = float(str(fund_data['EPS']).replace(',', ''))
    except: eps_f = 0.0
    if eps_f > 0: sc+=2; rs.append("✅ 基本面獲利")
    
    if data.get('成交量', 0) > data.get('5日均量', 0) * 1.1: sc+=2; rs.append("✅ 量能放大 (具備主力進場點火特徵)")
    else: sc-=1; rs.append("⚠️ 量能未明顯放大 (打底或缺乏點火動能)")
        
    if data.get('MACD柱', 0) > data.get('前日MACD柱', -999): sc+=2; rs.append("✅ MACD 綠柱收斂或紅柱放大 (動能防禦過關)")
    else: sc-=3; rs.append("⚠️ MACD 空方動能持續擴大 (型態脆弱嚴防接刀)")

    if inst_data and len(inst_data) >= 3:
        net_buy = sum([int(str(x['單日合計(張)']).replace(',', '')) for x in inst_data[:3] if str(x['單日合計(張)']).replace(',', '').lstrip('-').isdigit()])
        if net_buy > 0: rs.append(f"✅ 法人近三日偏多 (累計買超 {net_buy} 張)")
        else: rs.append(f"⚠️ 法人近三日偏空 (累計賣超 {abs(net_buy)} 張)")

    if data.get('紅吞'): sc+=3; rs.append("🔥 出現「紅吞」反轉型態 (強烈多頭買進訊號)")
    if data.get('黑吞'): sc-=3; rs.append("🩸 出現「黑吞」反轉型態 (強烈空頭逃命訊號)")

    if data.get('回測有撐'): sc+=2; rs.append("🔥 帶量長下影線 (主力回測支撐成功)")
    if data.get('反彈遇壓'): sc-=2; rs.append("🩸 反彈遇均線壓力留長上影線 (空方壓制)")
    
    if data['收盤價'] >= data['5MA'] and data.get('5日線即將上彎'): 
        rs.append("🔥 5日線扣低值 (短均線準備上彎發散，短線動能轉強)")
    if data['收盤價'] < data['5MA'] and not data.get('5日線即將上彎'): 
        rs.append("⚠️ 5日線扣高值 (短均線即將下彎產生蓋頭壓力)")

    if data['J值'] >= 80: sc-=3; rs.append("⚠️ KDJ高檔過熱")
    if data['收盤價'] >= data['BB_UP'] * 0.98: sc-=2; rs.append("⚠️ 觸及布林上軌壓力")
    if data['BIAS'] > 7: sc-=2; rs.append("⚠️ 正乖離過大")
    if data['收盤價'] < data['20MA']: sc-=2; rs.append("⚠️ 跌破月線支撐")
    if eps_f < 0: sc-=1; rs.append("⚠️ 基本面虧損")

    return sc, rs

def analyze_today(df, ticker_number, inst_data=None, is_light_mode=False):
    if df is None or len(df) < 5: return None
    t, p, p5 = df.iloc[-1], df.iloc[-2], df.iloc[-5]
    fund = get_fundamental_and_industry_data(ticker_number, round(t['Close'], 2))
    
    try:
        t_open, t_close, t_high, t_low = float(t['Open']), float(t['Close']), float(t['High']), float(t['Low'])
        p_open, p_close = float(p['Open']), float(p['Close'])
        
        red_mask = (df['Open'].shift(1) > df['Close'].shift(1)) & (df['Close'] > df['Open']) & (df['Close'] > df['Open'].shift(1)) & (df['Open'] < df['Close'].shift(1))
        black_mask = (df['Close'].shift(1) > df['Open'].shift(1)) & (df['Open'] > df['Close']) & (df['Open'] > df['Close'].shift(1)) & (df['Close'] < df['Open'].shift(1))
        
        is_red_engulfing = bool(red_mask.iloc[-1])
        is_black_engulfing = bool(black_mask.iloc[-1])
        recent_7_red = bool(red_mask.tail(7).any())
        
        total_range = t_high - t_low
        if total_range == 0: total_range = 0.001
        upper_shadow = t_high - max(t_open, t_close)
        lower_shadow = min(t_open, t_close) - t_low
        body = abs(t_close - t_open)

        is_support_pullback = (lower_shadow > body * 1.5) and (lower_shadow / total_range > 0.4) and (t_low < p_close) and (t_close >= min(p_open, p_close))
        ma_resistance = min(t['5MA'], t['10MA']) 
        is_resistance_rejection = (upper_shadow > body * 1.5) and (upper_shadow / total_range > 0.4) and (t_high >= ma_resistance) and (t_close < ma_resistance)
    except:
        is_red_engulfing, is_black_engulfing, recent_7_red = False, False, False
        is_support_pullback, is_resistance_rejection = False, False
        
    try:
        ma5_deduction_tmr = float(df['Close'].iloc[-5]) if len(df) >= 5 else float(t_close)
        ma60_deduction_tmr = float(df['Close'].iloc[-60]) if len(df) >= 60 else float(t_close)
        is_ma5_turning_up = t_close > ma5_deduction_tmr
        is_ma60_turning_up = t_close > ma60_deduction_tmr
    except:
        is_ma5_turning_up, is_ma60_turning_up = False, False

    data = {
        "代號": ticker_number, "名稱": get_stock_name(ticker_number), "ticker_raw": ticker_number,
        "產業": fund['Industry'], "昨日收盤價": round(p_close, 2), "收盤價": round(t_close, 2), 
        "漲跌": round(t_close - p_close, 2), "漲跌幅": round((t_close - p_close) / p_close * 100, 2), 
        "近5日漲幅(%)": f"{round((t_close - p5['Close'])/p5['Close']*100, 2)}%",
        "成交量": int(t['Volume']/1000), "5日均量": int(df['Volume'].tail(5).mean()/1000),
        "5MA": round(t['5MA'], 2), "10MA": round(t['10MA'], 2), "20MA": round(t['20MA'], 2),
        "60MA": round(t['60MA'], 2),
        "BB_UP": round(t['BB_UP'], 2), "BB_DN": round(t['BB_DN'], 2), "BIAS": round(t['BIAS_20'], 2),
        "MACD": round(t['MACD'], 2), "MACD柱": round(t['MACD_Hist'], 3), "前日MACD柱": round(p['MACD_Hist'], 3),
        "K": round(t['K'], 2), "D": round(t['D'], 2), "J值": round(t['J'], 2),
        "EPS": fund['EPS'], "PE": fund['PE'],
        "訊號": (t_close > t['20MA']) and (t_close < t['5MA']) and (t['J'] < 20),
        "紅吞": is_red_engulfing, "黑吞": is_black_engulfing,
        "近七日紅吞": recent_7_red,
        "回測有撐": is_support_pullback,
        "反彈遇壓": is_resistance_rejection,
        "5日線即將上彎": is_ma5_turning_up,
        "季線即將上彎": is_ma60_turning_up
    }
    
    sc, rs = get_decision_score(data, fund, inst_data)
    data['Score'] = sc
    data['Reasons'] = rs
    data['評級'] = "🟢 S級" if sc >= 5 else ("🟡 A級" if sc >= 2 else "⚪ 觀望")
    
    return data

@st.cache_data(ttl=180, show_spinner=False)
def get_stock_rating_fast(ticker):
    try:
        df = get_stock_data(ticker)
        if df is not None and len(df) >= 5:
            data = analyze_today(df, ticker, inst_data=None)
            if data: return data.get('評級', "⚪ 觀望")
    except: pass
    return "⚪ 觀望"

st.sidebar.title("⭐ 我的自選群組")

MAX_GROUPS = 5
current_group_count = len(st.session_state.fav_groups)

if current_group_count < MAX_GROUPS:
    with st.sidebar.expander("➕ 新增個人化群組", expanded=False):
        new_g_name = st.text_input("群組名稱", placeholder="輸入群組名稱...", label_visibility="collapsed", key="add_new_fav_group_input_999")
        if st.button("建立", use_container_width=True, key="btn_create_new_group_999") and new_g_name:
            if new_g_name not in st.session_state.fav_groups:
                st.session_state.fav_groups[new_g_name] = []
                save_json(FAV_GROUPS_FILE, st.session_state.fav_groups)
                st.rerun()
else:
    st.sidebar.info(f"已達群組數量上限 ({MAX_GROUPS} 個)。")

for g_name, g_stocks in list(st.session_state.fav_groups.items()):
    with st.sidebar.expander(f"📁 {g_name} ({len(g_stocks)})", expanded=True):
        col_rn, col_sv, col_del = st.columns([5, 2, 2])
        new_g_name_input = col_rn.text_input("重命名", value=g_name, key=f"rn_group_{g_name}", label_visibility="collapsed")
        
        if col_sv.button("💾", key=f"sv_group_{g_name}", help="儲存新群組名稱"):
            if new_g_name_input and new_g_name_input != g_name and new_g_name_input not in st.session_state.fav_groups:
                new_dict = {}
                for k, v in st.session_state.fav_groups.items():
                    if k == g_name: new_dict[new_g_name_input] = v
                    else: new_dict[k] = v
                st.session_state.fav_groups = new_dict
                save_json(FAV_GROUPS_FILE, st.session_state.fav_groups)
                st.rerun()
                
        if col_del.button("🗑️", key=f"del_group_{g_name}", help="刪除此群組"):
            if len(st.session_state.fav_groups) > 1:
                del st.session_state.fav_groups[g_name]
                save_json(FAV_GROUPS_FILE, st.session_state.fav_groups)
                st.rerun()
            else:
                st.error("至少需保留一個群組！")
                
        for fav in g_stocks:
            fav_rating = get_stock_rating_fast(fav)
            if st.button(f"📊 {fav} {get_stock_name(fav)} | {fav_rating}", key=f"go_stock_{g_name}_{fav}", use_container_width=True):
                st.session_state.update({"current_stock": fav, "page": "analysis", "date_offset": 0})
                st.rerun()

st.sidebar.divider()
st.sidebar.title("⚙️ 雷達池設定")
if st.sidebar.button("🔄 更新熱門雷達池 (Top 100)", use_container_width=True, key="btn_update_top_100_radar"):
    st.session_state.custom_pool = fetch_twse_top_100()
    save_json(POOL_FILE, st.session_state.custom_pool)
    st.sidebar.success("✅ 雷達池已擴大更新為全台前 100 檔！")
    st.rerun()

# ==========================================
# 🚀 頁面路由控制中心
# ==========================================
if st.session_state.page == "home":
    st.markdown("<h1 style='text-align: center;'>🇹🇼 短線交易雷達總機</h1>", unsafe_allow_html=True)
    
    st.markdown("<h3 style='margin-top: 15px;'>🎯 快速篩選模式</h3>", unsafe_allow_html=True)
    btn_col1, btn_col2, btn_col3, btn_col4 = st.columns(4)
    if btn_col1.button("✅ 綜合買點榜", use_container_width=True, key="btn_scan_buy"): 
        st.session_state.scan_mode = "buy"
        st.rerun()
    if btn_col2.button("🔥 紅吞反轉榜", use_container_width=True, key="btn_scan_red"): 
        st.session_state.scan_mode = "red_engulf"
        st.rerun()
    if btn_col3.button("📊 成交量排行", use_container_width=True, key="btn_scan_vol"): 
        st.session_state.scan_mode = "recent"
        st.rerun()
    if btn_col4.button("🔧 進階篩選", use_container_width=True, key="btn_scan_advanced"): 
        st.session_state.scan_mode = "advanced"
        st.rerun()
    
    st.markdown("---")
    
    # 進階篩選界面
    if st.session_state.scan_mode == "advanced":
        st.markdown("<h4>⚙️ 短線交易進階篩選 - 自訂條件組合</h4>", unsafe_allow_html=True)
        
        with st.expander("📈 技術面條件 - 短線熱點", expanded=True):
            col_tech1, col_tech2, col_tech3, col_tech4 = st.columns(4)
            with col_tech1:
                kdj_oversold = st.checkbox("🟢 KDJ超賣 (J<20)", value=False, key="filter_kdj_oversold")
            with col_tech2:
                kdj_overbought = st.checkbox("🔴 KDJ過熱 (J>80)", value=False, key="filter_kdj_overbought")
            with col_tech3:
                bias_negative = st.checkbox("📉 負乖離 (BIAS<-5)", value=False, key="filter_bias_negative")
            with col_tech4:
                bias_positive = st.checkbox("📈 正乖離 (BIAS>7)", value=False, key="filter_bias_positive")
            
            col_tech5, col_tech6, col_tech7, col_tech8 = st.columns(4)
            with col_tech5:
                bb_lower = st.checkbox("⬇️ 布林下軌", value=False, key="filter_bb_lower")
            with col_tech6:
                bb_upper = st.checkbox("⬆️ 布林上軌", value=False, key="filter_bb_upper")
            with col_tech7:
                ma_support = st.checkbox("📌 在月線上方", value=False, key="filter_ma_support")
            with col_tech8:
                ma_below = st.checkbox("📍 在5日線下方", value=False, key="filter_ma_below")
            
            col_tech9, col_tech10, col_tech11, col_tech12 = st.columns(4)
            with col_tech9:
                red_candle = st.checkbox("🔺 紅吞反轉", value=False, key="filter_red_candle")
            with col_tech10:
                support_test = st.checkbox("🛡️ 回測有撐", value=False, key="filter_support_test")
            with col_tech11:
                resistance_test = st.checkbox("⚠️ 反彈遇壓", value=False, key="filter_resistance_test")
            with col_tech12:
                macd_positive = st.checkbox("📈 MACD轉正", value=False, key="filter_macd_positive")
            
            col_tech13, col_tech14 = st.columns(2)
            with col_tech13:
                volume_expand = st.checkbox("📊 量能放大 (>5日均*1.1)", value=False, key="filter_volume_expand")
            with col_tech14:
                volume_shrink = st.checkbox("📉 量能萎縮 (<5日均*0.8)", value=False, key="filter_volume_shrink")
        
        with st.expander("💰 基本面條件", expanded=False):
            col_fund1, col_fund2 = st.columns(2)
            with col_fund1:
                pe_min, pe_max = st.slider("本益比 (PE) 範圍", 0.0, 50.0, (0.0, 30.0), key="filter_pe_range")
            with col_fund2:
                eps_min, eps_max = st.slider("EPS 範圍", 0.0, 20.0, (0.0, 10.0), key="filter_eps_range")
        
        with st.expander("📊 成交量與漲跌", expanded=False):
            col_vol1, col_vol2 = st.columns(2)
            with col_vol1:
                change_min, change_max = st.slider("漲跌幅 (%)", -20.0, 20.0, (-20.0, 20.0), key="filter_change_range")
            with col_vol2:
                vol_min, vol_max = st.slider("成交量 (千股)", 0, 100000, (0, 100000), key="filter_volume_range")
        
        with st.expander("🏷️ 其他條件", expanded=False):
            ratings = st.multiselect("篩選評級", options=["🟢 S級", "🟡 A級", "⚪ 觀望"], key="filter_ratings")
        
        # 組裝篩選條件
        advanced_filters = {
            'kdj_oversold': kdj_oversold,
            'kdj_overbought': kdj_overbought,
            'bias_negative': bias_negative,
            'bias_positive': bias_positive,
            'bb_lower': bb_lower,
            'bb_upper': bb_upper,
            'ma_support': ma_support,
            'ma_below': ma_below,
            'red_candle': red_candle,
            'volume_expand': volume_expand,
            'volume_shrink': volume_shrink,
            'macd_positive': macd_positive,
            'support_test': support_test,
            'resistance_test': resistance_test,
            'pe_range': [pe_min, pe_max],
            'eps_range': [eps_min, eps_max],
            'change_range': [change_min, change_max],
            'volume_range': [int(vol_min), int(vol_max)],
            'ratings': ratings,
        }
        
        col_save, col_clear = st.columns(2)
        if col_save.button("💾 儲存為預設", use_container_width=True, key="btn_save_filter_preset"):
            preset_name = st.text_input("預設名稱 (如: 超賣反彈)", key="filter_preset_name")
            if preset_name:
                st.session_state.filter_presets[preset_name] = advanced_filters
                save_json(FILTER_PRESETS_FILE, st.session_state.filter_presets)
                st.success(f"✅ 預設 '{preset_name}' 已儲存！下次可快速套用")
        
        if col_clear.button("🔄 重置條件", use_container_width=True, key="btn_clear_filters"):
            st.rerun()
    
    top_100_pool = fetch_twse_top_100()
    pool = tuple(set(top_100_pool + st.session_state.custom_pool + list(STOCK_NAMES.keys())))
    
    with st.spinner("🚀 掃描全市場中... (快取命中時<1秒)"):
        scan_results = get_global_scan_results(pool)
            
    if scan_results:
        df_results = pd.DataFrame(scan_results)
        
        df_results['Bullish_Count'] = df_results.apply(
            lambda r: (1 if r.get('紅吞') or r.get('近七日紅吞') else 0) + 
                      (1 if r.get('回測有撐') else 0) + 
                      (1 if r.get('5日線即將上彎') else 0), axis=1)

        # 應用進階篩選
        if st.session_state.scan_mode == "advanced":
            df_disp = apply_advanced_filters(df_results, advanced_filters)
            st.markdown(f"##### 🔍 進階篩選結果 (共 {len(df_disp)} 檔符合)")
            if len(df_disp) == 0:
                st.info("💡 沒有符合條件的標的，請調整篩選條件")
        elif st.session_state.scan_mode == "recent":
            st.markdown("##### 📊 近五日成交量排行榜 (Top 20)")
            df_disp = df_results.sort_values(by="成交量", ascending=False).head(20)
        elif st.session_state.scan_mode == "red_engulf":
            st.markdown("##### 🔥 近七日紅吞反轉標的 (S/A級)")
            df_disp = df_results[(df_results['近七日紅吞'] == True) & (df_results['Score'] >= 2)].sort_values(
                by=['Score', 'Bullish_Count', '漲跌幅'], ascending=[False, False, False]
            ).head(20)
            if len(df_disp) == 0:
                st.info("💡 暫無符合「紅吞反轉型態」的強勢個股")
        else:  # buy mode
            st.markdown("##### 🎯 綜合買點榜單 (S/A級)")
            df_disp = df_results[df_results['Score'] >= 2].sort_values(
                by=['Score', 'Bullish_Count', '漲跌幅'], ascending=[False, False, False]
            ).head(20)
            if len(df_disp) == 0:
                st.info("💡 暫無符合條件的標的，市場空方主導")
            
        st.session_state.nav_pool = df_disp['ticker_raw'].tolist() if len(df_disp) > 0 else []
        st.session_state.nav_pool_data = df_disp.to_dict('records') if len(df_disp) > 0 else []
        
        if len(df_disp) > 0:
            # 顯示篩選結果表格
            display_df = df_disp[['代號', '名稱', '收盤價', '漲跌幅', '成交量', '5日均量', 'J值', 'BIAS', '評級']].copy()
            display_df['漲跌幅'] = display_df['漲跌幅'].apply(lambda x: f"{x:+.2f}%")
            st.dataframe(display_df, use_container_width=True, hide_index=True)
            
            st.markdown("---")
            for _, r in df_disp.iterrows():
                p_val = r['漲跌']
                sign = "+" if p_val > 0 else ""
                trend_icon = "🔺" if p_val > 0 else ("🔻" if p_val < 0 else "➖")
                s_score = r['Score']
                score_icon = "🟢 S級" if s_score >= 5 else ("🟡 A級" if s_score >= 2 else "⚪ 觀望")
                
                tags = []
                if r.get('紅吞'): tags.append("🔺紅吞")
                elif r.get('黑吞'): tags.append("🔻黑吞")
                if r.get('回測有撐'): tags.append("🛡️撐")
                elif r.get('反彈遇壓'): tags.append("⚠️壓")
                if '5日線即將上彎' in r:
                    tags.append("↗️上" if r.get('5日線即將上彎') else "↘️下")
                    
                tag_display = " | ".join(tags) if tags else "無特殊信號"
                
                button_label = f"[{score_icon}] {r['代號']} {r['名稱']} {trend_icon}{r['收盤價']}({sign}{r['漲跌幅']}%) | {tag_display}"
                if st.button(button_label, key=f"btn_scan_list_{r['ticker_raw']}_{st.session_state.scan_mode}", use_container_width=True):
                    st.session_state.update({"current_stock": r['ticker_raw'], "page": "analysis", "date_offset": 0})
                    st.rerun()

elif st.session_state.page == "analysis":
    target = st.session_state.current_stock
    c_name = get_stock_name(target)
    
    n_pool = st.session_state.get('nav_pool', [])
    p_stk, n_stk = None, None
    if target in n_pool and len(n_pool) > 1:
        i = n_pool.index(target)
        p_stk = n_pool[i - 1] if i > 0 else None
        n_stk = n_pool[i + 1] if i < len(n_pool) - 1 else None

    c1, c2, c3 = st.columns([1, 1, 1])
    with c1:
        if p_stk and st.button(f"⬅ 上一檔", use_container_width=True, key="btn_prev_stock_nav"): 
            st.session_state.update({"current_stock": p_stk})
            st.rerun()
    with c2:
        if st.button("🏠 回雷達總機", use_container_width=True, key="btn_go_home_nav"): 
            st.session_state.page = "home"
            st.rerun()
    with c3:
        if n_stk and st.button(f"下一檔 ➡", use_container_width=True, key="btn_next_stock_nav"): 
            st.session_state.update({"current_stock": n_stk})
            st.rerun()

    load_ph = st.empty()
    
    with load_ph.container():
        st.markdown(f"<h4 style='text-align:center;'>🚀 分析 {target} {c_name} 中...</h4>", unsafe_allow_html=True)
        p_bar = st.progress(0)
        status = st.empty()
        
        status.markdown("<div style='text-align:center; color:#888;'>⏳ 讀取股價資料...</div>", unsafe_allow_html=True)
        df_chart = get_stock_data(target)
        p_bar.progress(50)

        if df_chart is not None and len(df_chart) >= 5:
            data = analyze_today(df_chart, target, inst_data=None, is_light_mode=is_light_mode)
            status.markdown("<div style='text-align:center; color:#00cc00; font-weight:bold;'>✅ 完成</div>", unsafe_allow_html=True)
            p_bar.progress(100)
            time.sleep(0.3)
        else:
            load_ph.empty()
            st.error("❌ 查無資料或資料不足")
            st.stop()

    load_ph.empty()
    
    p_color = '#ff3333' if data['漲跌'] >= 0 else '#00cc00'
    analysis_date = df_chart.index[-1].strftime('%Y/%m/%d')
    
    st.markdown(f"<h2 style='text-align: center;'>🎯 {target} {c_name}</h2>", unsafe_allow_html=True)
    st.markdown(f"<div style='text-align: center; color: {p_color}; font-size: 2.5rem; font-weight: 900;'>{data['收盤價']}</div>", unsafe_allow_html=True)
    st.markdown(f"<div style='text-align: center; font-size: 1.2rem;'>{'+' if data['漲跌']>0 else ''}{data['漲跌']} ({'+' if data['漲跌幅']>0 else ''}{data['漲跌幅']}%)</div>", unsafe_allow_html=True)
    
    col_stat1, col_stat2, col_stat3, col_stat4, col_stat5 = st.columns(5)
    with col_stat1:
        st.metric("評級", data['評級'].replace('🟢 ', '').replace('🟡 ', '').replace('⚪ ', ''))
    with col_stat2:
        st.metric("PE", data['PE'], "本益比")
    with col_stat3:
        st.metric("EPS", data['EPS'], "每股盈餘")
    with col_stat4:
        st.metric("J值", f"{data['J值']:.0f}", "KDJ")
    with col_stat5:
        st.metric("BIAS", f"{data['BIAS']:.1f}%", "乖離率")
    
    st.markdown("---")
    st.success(f"✅ 分析完成 | 日期: {analysis_date} | 產業: {data['產業']}")
