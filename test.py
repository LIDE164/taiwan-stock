```python
# 最後修改時間: 2026-06-29 16:30 CST
# 版本: 完美實戰旗艦版 (無斷層K線 + 左側全局過濾 + 產業熱力圖 + 大戶/回測圖表 + ATR合併)
import yfinance as yf
import streamlit as st
import pandas as pd
import requests
import time
from datetime import datetime, timezone, timedelta
import plotly.graph_objects as go
import plotly.express as px
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

# === 核心 API 金鑰設定 ===
FINMIND_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjoiYTQ1Njg4MTUwQGdtYWlsLmNvbSIsImVtYWlsIjoiYTQ1Njg4MTUwQGdtYWlsLmNvbSIsInRva2VuX3ZlcnNpb24iOjB9.LUcb8YPV4yo93_aB3obP4Z5iUGqAgTaH28ySx9UNv5I"
FUGLE_API_KEY = "NWMxYjY4MzctM2VlNC00MjhhLTk5NjctOWQyYzBmMmJmZWU1IGFmNDk3NWRkLWY3NTMtNGZiYy04MTgyLTM3MTY4NDYyNTAwMw=="
FUGLE_HEADERS = {"X-API-KEY": FUGLE_API_KEY}

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

components.html("""<script>var body = window.parent.document.querySelector('.main'); if (body) { body.scrollTo({top: 0, behavior: 'smooth'}); }</script>""", height=0, width=0)

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
sum_bg = "rgba(0,0,0,0.05)" if is_light_mode else "rgba(255,255,255,0.05)"

css_style = f"""
<style>
    .stApp {{ background-color: {app_bg}; }}
    #MainMenu {{visibility: hidden;}} footer {{visibility: hidden;}}
    [data-testid="collapsedControl"] {{ border: 1px solid {border_col} !important; border-radius: 8px !important; background-color: {bg_col} !important; padding: 5px 12px !important; display: flex !important; align-items: center !important; width: auto !important; transition: 0.3s; }}
    [data-testid="collapsedControl"]::after {{ content: " ⭐ 我的群組"; font-size: 1.1rem; font-weight: bold; color: #ffcc00; margin-left: 8px; }}
    .stButton button {{ font-weight: bold !important; border-radius: 8px !important; text-align: left !important; }}
    button[kind="primary"] {{ font-size: 1.5rem !important; padding: 15px !important; border-radius: 12px !important; background-color: #ffcc00 !important; color: #111 !important; border: none !important; }}
    .sticky-header {{ position: sticky; top: 0; z-index: 999; background-color: {sticky_bg}; padding: 10px 0; border-bottom: 1px solid {border_col}; backdrop-filter: blur(5px); margin-top: -15px; margin-bottom: 15px; }}
    div[data-testid="stVerticalBlockBorderWrapper"] > div {{ background-color: {bg_col} !important; border-color: {border_col} !important; padding: 4px !important; }}
    h1, h2, h3, h4, p, span {{ color: {title_col} !important; }}
    .compact-btn button {{ padding: 0.25rem 0.5rem !important; font-size: 1rem !important; }}
    .risk-bar-container {{ width: 100%; background-color: #333; border-radius: 8px; margin-top: 5px; margin-bottom: 15px; overflow: hidden; }}
    .risk-bar-fill {{ height: 16px; border-radius: 8px; transition: width 0.5s ease-in-out; }}
    [data-testid="stExpander"] {{ border-color: {border_col} !important; background-color: {bg_col} !important; border-radius: 8px !important; margin-bottom: 15px; }}
</style>
"""
st.markdown(css_style, unsafe_allow_html=True)

STOCK_NAMES = { "2330": "台積電", "2317": "鴻海", "2454": "聯發科", "2308": "台達電", "2382": "廣達", "2376": "技嘉", "1802": "台玻", "2603": "長榮", "1785": "光洋科", "1519": "華城", "6147": "頎邦", "6191": "精成科"}

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
    search_input = st.text_input("隱藏", placeholder="輸入股票代號或名稱...", label_visibility="collapsed", key="global_search_input")
    submit_search = st.form_submit_button("送出搜尋", use_container_width=True)
    
if submit_search and search_input:
    s_val = search_input.strip().replace(" ", "")
    target_ticker = s_val.upper() if re.match(r'^[A-Za-z0-9]+$', s_val) else next((c for c, n in CURRENT_STOCK_NAMES.items() if s_val in n), None)
    if target_ticker:
        st.session_state.update({"current_stock": target_ticker, "page": "analysis", "date_offset": 0})
        st.rerun() 
    else:
        st.sidebar.warning(f"⚠️ 找不到「{s_val}」相關標的。")

st.sidebar.divider()
st.sidebar.title("⏱️ 即時跳動雷達")
auto_refresh = st.sidebar.toggle("🟢 開啟局部即時更新", False, key="auto_refresh_toggle")
if auto_refresh: st.sidebar.success("⚡ 局部高頻探測啟動！")
st.sidebar.divider()

def get_stock_name(ticker):
    ticker_str = str(ticker).strip().upper().replace(".TW", "").replace(".TWO", "")
    return CURRENT_STOCK_NAMES.get(ticker_str, STOCK_NAMES.get(ticker_str, ticker_str))

FAV_FILE, FAV_GROUPS_FILE, POOL_FILE = "favorites.json", "fav_groups.json", "pool.json"
def load_json(fp, default): return json.load(open(fp, "r", encoding="utf-8")) if os.path.exists(fp) else default
def save_json(fp, data): json.dump(data, open(fp, "w", encoding="utf-8"))

if 'page' not in st.session_state: st.session_state.page = "home"
if 'current_stock' not in st.session_state: st.session_state.current_stock = "2376"
if 'custom_pool' not in st.session_state: st.session_state.custom_pool = load_json(POOL_FILE, ["2330", "2317", "2454", "2382", "3231", "6191"])
if 'nav_pool' not in st.session_state: st.session_state.nav_pool = st.session_state.custom_pool
if 'scan_mode' not in st.session_state: st.session_state.scan_mode = "buy"
if 'view_days' not in st.session_state: st.session_state.view_days = 60
if 'date_offset' not in st.session_state: st.session_state.date_offset = 0

if 'fav_groups' not in st.session_state:
    st.session_state.fav_groups = load_json(FAV_GROUPS_FILE, {"預設群組": ["1802", "2330", "1785", "6191"]})

@st.cache_data(ttl=1800)
def fetch_twse_top_100():
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", timeout=10)
        df = pd.DataFrame(res.json())
        df['TradeVolume'] = pd.to_numeric(df['TradeVolume'], errors='coerce')
        return df[df['Code'].str.match(r'^\d{4}$')].sort_values(by='TradeVolume', ascending=False).head(100)['Code'].tolist()
    except: return ["2330", "2317", "2454", "2382", "3231", "6191"]

# ==========================================
# 🟢 API 核心: Fugle 歷史與即時 K 線 (修復空值與斷層)
# ==========================================
@st.cache_data(ttl=60, show_spinner=False) 
def get_stock_data(ticker_number):
    base_ticker = str(ticker_number).strip().upper().replace(".TW", "").replace(".TWO", "")
    df = None
    
    if base_ticker == "^TWII":
        try:
            df = yf.Ticker("^TWII").history(period="1y")[['Open', 'High', 'Low', 'Close', 'Volume']]
            if not df.empty: df.index = pd.to_datetime(df.index.strftime('%Y-%m-%d'))
        except: pass
    else:
        try:
            url_hist = f"https://api.fugle.tw/marketdata/v1.0/stock/historical/candles/{base_ticker}?timeframe=D"
            res = requests.get(url_hist, headers=FUGLE_HEADERS, timeout=5)
            if res.status_code == 200:
                data = res.json().get('data', [])
                if data:
                    df = pd.DataFrame(data)
                    df['date'] = pd.to_datetime(df['date']).dt.tz_localize(None)
                    df.set_index('date', inplace=True)
                    df = df[['open', 'high', 'low', 'close', 'volume']].rename(columns={'open':'Open', 'high':'High', 'low':'Low', 'close':'Close', 'volume':'Volume'})
                    df = df.sort_index()
        except: pass

        if df is None or df.empty:
            try:
                df = yf.Ticker(f"{base_ticker}.TW").history(period="1y")
                if df.empty: df = yf.Ticker(f"{base_ticker}.TWO").history(period="1y")
                if not df.empty: df.index = pd.to_datetime(df.index.strftime('%Y-%m-%d'))
            except: pass

        if df is not None and not df.empty:
            try:
                url_quote = f"https://api.fugle.tw/marketdata/v1.0/stock/intraday/quote/{base_ticker}"
                res_quote = requests.get(url_quote, headers=FUGLE_HEADERS, timeout=3)
                if res_quote.status_code == 200:
                    q_data = res_quote.json()
                    quote = q_data.get('data', {}).get('quote', {})
                    dt_live = pd.to_datetime(datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d'))
                    
                    c_price = quote.get('closePrice') or quote.get('lastPrice') or quote.get('previousClose') or df['Close'].iloc[-1]
                    o_price = quote.get('openPrice') or c_price
                    h_price = quote.get('highPrice') or c_price
                    l_price = quote.get('lowPrice') or c_price
                    v_vol = quote.get('totalVolume', 0)
                    
                    if dt_live not in df.index:
                        df = pd.concat([df, pd.DataFrame({'Open': [o_price], 'High': [h_price], 'Low': [l_price], 'Close': [c_price], 'Volume': [v_vol]}, index=[dt_live])])
                    else:
                        df.at[dt_live, 'Close'] = c_price
                        df.at[dt_live, 'High'] = max(df.at[dt_live, 'High'], h_price)
                        df.at[dt_live, 'Low'] = min(df.at[dt_live, 'Low'], l_price)
                        df.at[dt_live, 'Volume'] = max(df.at[dt_live, 'Volume'], v_vol)
            except: pass
            
    if df is None or df.empty: return None

    # 💡 強制清理斷層與亂碼數據
    df = df[~df.index.duplicated(keep='last')].dropna(subset=['Close'])
    df = df[df['Close'] > 0]

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

    df['TR'] = pd.concat([
        df['High'] - df['Low'],
        (df['High'] - df['Close'].shift(1)).abs(),
        (df['Low'] - df['Close'].shift(1)).abs()
    ], axis=1).max(axis=1)
    df['ATR'] = df['TR'].rolling(14).mean()

    return df

# 🟢 API 核心: 鉅亨網精準產業別
@st.cache_data(ttl=86400, show_spinner=False)
def get_fundamental_and_industry_data(ticker_number, current_price=0):
    base_ticker = str(ticker_number).strip().upper().replace(".TW", "").replace(".TWO", "")
    eps_val, pe_val, ind = "無", "無", "一般產業"

    try:
        url_profile = f"https://ws.cnyes.com/twstock/api/v1/company/profile/{base_ticker}"
        res_profile = requests.get(url_profile, headers={'User-Agent': 'Mozilla/5.0'}, timeout=3)
        if res_profile.status_code == 200:
            data = res_profile.json()
            if 'data' in data and data['data'].get('industry'):
                ind = data['data']['industry'] 
    except: pass
    
    try:
        start_date = (datetime.now() - timedelta(days=14)).strftime('%Y-%m-%d')
        fm_url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockPER&data_id={base_ticker}&start_date={start_date}&token={FINMIND_TOKEN}"
        fm_res = requests.get(fm_url, timeout=3)
        if fm_res.status_code == 200:
            fm_data = fm_res.json()
            if fm_data.get('msg') == 'success' and len(fm_data.get('data', [])) > 0:
                latest_pe = fm_data['data'][-1].get('PER')
                if latest_pe and float(latest_pe) > 0: pe_val = f"{float(latest_pe):.2f}"
    except: pass
    
    try:
        info = yf.Ticker(f"{base_ticker}.TW").info
        if not info or ('trailingEps' not in info): info = yf.Ticker(f"{base_ticker}.TWO").info

        if 'trailingEps' in info and info['trailingEps'] is not None:
            eps_val = str(round(info['trailingEps'], 2))

        if pe_val == "無" and eps_val != "無":
            eps_f = float(eps_val)
            if eps_f > 0 and current_price > 0: pe_val = str(round(float(current_price) / eps_f, 2))
        elif eps_val == "無" and pe_val != "無":
            pe_f = float(pe_val)
            if pe_f > 0 and current_price > 0: eps_val = str(round(float(current_price) / pe_f, 2))
    except: pass

    return {"EPS": eps_val, "PE": pe_val, "Industry": ind}

@st.cache_data(ttl=15, show_spinner=False) 
def get_twii_quote():
    tz_tpe = timezone(timedelta(hours=8))
    update_time_str = datetime.now(tz_tpe).strftime('%Y/%m/%d %H:%M:%S')
    try:
        url = "https://api.fugle.tw/marketdata/v1.0/stock/intraday/quote/IX0001"
        res = requests.get(url, headers=FUGLE_HEADERS, timeout=3)
        if res.status_code == 200:
            q = res.json().get('data', {}).get('quote', {})
            c = q.get('closePrice') or q.get('lastPrice') or q.get('previousClose', 0)
            p = q.get('previousClose', c)
            if c > 10000: return float(c), float(c - p), update_time_str
    except: pass
    try:
        df = yf.Ticker("^TWII").history(period="5d").dropna(subset=['Close'])
        if not df.empty and len(df) >= 2: return float(df['Close'].iloc[-1]), float(df['Close'].iloc[-1] - df['Close'].iloc[-2]), update_time_str
    except: pass
    return 0, 0, update_time_str

@st.cache_data(ttl=3600, show_spinner=False)
def get_institutional_trading(ticker):
    try:
        start_date = (datetime.now() - timedelta(days=20)).strftime('%Y-%m-%d')
        url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInstitutionalInvestorsBuySell&data_id={ticker}&start_date={start_date}&token={FINMIND_TOKEN}"
        res = requests.get(url, timeout=5)
        if res.status_code == 200 and res.json().get('msg') == 'success' and len(res.json().get('data', [])) > 0:
            df = pd.DataFrame(res.json()['data'])
            df['net'] = (df['buy'] - df['sell']) / 1000  
            df['type'] = '其他'
            df.loc[df['name'].str.contains('Foreign|外資', case=False, na=False), 'type'] = '外資'
            df.loc[df['name'].str.contains('Trust|投信', case=False, na=False), 'type'] = '投信'
            df.loc[df['name'].str.contains('Dealer|自營', case=False, na=False), 'type'] = '自營商'
            pivot = df.groupby(['date', 'type'])['net'].sum().unstack(fill_value=0).reset_index().fillna(0)
            for col in ['外資', '投信', '自營商']:
                if col not in pivot.columns: pivot[col] = 0
            pivot['單日合計'] = pivot['外資'] + pivot['投信'] + pivot['自營商']
            pivot = pivot.sort_values('date', ascending=False).head(10)
            return [{"日期": r['date'][-5:].replace("-", "/"), "外資(張)": int(r['外資']), "投信(張)": int(r['投信']), "自營商(張)": int(r['自營商']), "單日合計(張)": int(r['單日合計'])} for _, r in pivot.iterrows()]
    except: pass
    return []

# 🌟 新增：千張大戶持股
@st.cache_data(ttl=86400, show_spinner=False)
def get_big_player_holding(ticker):
    try:
        start_date = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')
        res = requests.get(f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockHoldingSharesPer&data_id={ticker}&start_date={start_date}&token={FINMIND_TOKEN}", timeout=5)
        if res.status_code == 200 and res.json().get('data'):
            df = pd.DataFrame(res.json()['data'])
            df['HoldingSharesLevel'] = pd.to_numeric(df['HoldingSharesLevel'], errors='coerce')
            trend = df[df['HoldingSharesLevel'] >= 11].groupby('date')['percent'].sum().reset_index()
            if not trend.empty: return trend['date'].tolist(), trend['percent'].tolist()
    except: pass
    return [], []

@st.cache_data(ttl=300, show_spinner=False)
def get_global_macro_data():
    tz_tpe = timezone(timedelta(hours=8))
    fetch_time = datetime.now(tz_tpe).strftime('%Y/%m/%d %H:%M:%S')
    data = {"global_time": fetch_time}
    tickers = {"^SOX": "https://finance.yahoo.com/quote/^SOX", "^VIX": "https://finance.yahoo.com/quote/^VIX", "JPY=X": "https://finance.yahoo.com/quote/JPY=X"}
    for t, url in tickers.items():
        try:
            df = yf.Ticker(t).history(period="5d")
            if df is not None and not df.empty and len(df) >= 2:
                c, p = float(df['Close'].iloc[-1]), float(df['Close'].iloc[-2])
                data[t] = {"price": c, "pct": (c-p)/p*100 if p != 0 else 0, "time": df.index[-1].strftime('%Y/%m/%d %H:%M'), "url": url}
            else: data[t] = {"price": 0, "pct": 0, "time": "暫無資料", "url": url}
        except: data[t] = {"price": 0, "pct": 0, "time": "錯誤", "url": url}
    return data

def open_pred_logic(twii_df, twii_close, twii_change, twii_time_str=""):
    macro_data = get_global_macro_data()
    if twii_df is None or len(twii_df) < 2: return "資料不足", "無法分析", "資料不足", "無法預測", "", "", 50, macro_data
    
    t_open, t_close, p_close = twii_df['Open'].iloc[-1], twii_df['Close'].iloc[-1], twii_df['Close'].iloc[-2]
    if twii_close > 0: t_close, p_close = twii_close, twii_close - twii_change
    
    tz_tpe = timezone(timedelta(hours=8))
    last_dt_str = twii_time_str.split(" ")[0] if twii_time_str and "/" in twii_time_str else datetime.now(tz_tpe).strftime('%Y/%m/%d')
    next_dt = datetime.strptime(last_dt_str, '%Y/%m/%d') + timedelta(days=1)
    while next_dt.weekday() >= 5: next_dt += timedelta(days=1)
    
    today_title, today_desc = "⚖️ 平盤震盪", "大盤開在平盤附近，法人現貨買賣超多空拉扯，量價關係呈現縮量，盤勢陷入震盪整理。"
    if t_open > p_close * 1.003:
        if t_close > t_open: today_title, today_desc = "🔥 開高走高", "大盤受外資買盤與美股溢價激勵跳空開高，配合融資餘額增加與量能放大，盤勢極度偏多。"
        else: today_title, today_desc = "⚠️ 開高走低", "大盤跳空開高後遭遇短線獲利了結賣壓，呈現高檔回落。"
    elif t_open < p_close * 0.997:
        if t_close > t_open: today_title, today_desc = "💪 開低走高", "大盤受國際盤回檔影響開低，但低檔投信承接買盤強勁，出現開低走高收紅K型態。"
        else: today_title, today_desc = "🩸 開低走低", "大盤弱勢開低，恐慌指數上升引發散戶多殺多停損賣壓，盤勢極度偏空。"

    risk_score = 50 
    if t_close < (twii_df['5MA'].iloc[-1] if '5MA' in twii_df.columns else twii_df['Close'].tail(5).mean()): risk_score += 15
    else: risk_score -= 10
    
    sox_pct = macro_data.get('^SOX', {}).get('pct', 0)
    if sox_pct < -2.0: risk_score += 20
    elif sox_pct < -0.5: risk_score += 10
    elif sox_pct > 1.5: risk_score -= 15
    
    vix_pct = macro_data.get('^VIX', {}).get('pct', 0)
    if vix_pct > 10.0: risk_score += 20
    elif vix_pct < -5.0: risk_score -= 10
    
    jpy_pct = macro_data.get('JPY=X', {}).get('pct', 0)
    if jpy_pct < -0.8: risk_score += 15 
    elif jpy_pct > 0.5: risk_score -= 5
    
    risk_score = max(5, min(95, int(risk_score))) 
    if risk_score < 40: tmr_title, tmr_desc = "🚀 安全偏多", f"總經環境穩定，預估次一交易日有極高機率開平高盤挑戰上檔壓力。"
    elif risk_score < 70: tmr_title, tmr_desc = "⚠️ 偏空震盪", f"國際變數增加或台股跌破關鍵短均線，預防開平低盤回測下檔支撐。"
    else: tmr_title, tmr_desc = "🚨 極度警戒", f"全球宏觀風險飆高，強烈建議減碼防範跳空重挫的系統性風險。"
    
    return today_title, today_desc, tmr_title, tmr_desc, last_dt_str, next_dt.strftime('%Y/%m/%d'), risk_score, macro_data

def render_index_board():
    twii_close, twii_change, twii_time_str = get_twii_quote()
    twii_color = '#ff3333' if twii_change >= 0 else '#00cc00'
    twii_df_for_pred = get_stock_data("^TWII")
    today_title, today_desc, tmr_title, tmr_desc, last_dt_str, next_dt_str, risk_score, macro = open_pred_logic(twii_df_for_pred, twii_close, twii_change, twii_time_str)
    
    with st.container(border=True):
        col1, col3 = st.columns([1, 1.5])
        with col1:
            st.markdown(f"<div style='text-align: center; font-size: 1.1rem; font-weight: bold;'>台灣加權指數 🔗</div>", unsafe_allow_html=True)
            st.markdown(f"<div style='text-align: center; font-size: 2.1rem; font-weight: 900; color: {twii_color}; margin: 0;'>{twii_close:,.0f}</div>", unsafe_allow_html=True)
            st.markdown(f"<div style='text-align: center; font-size: 1.1rem; font-weight: bold; color: {twii_color};'>{'↑' if twii_change > 0 else '↓'} {abs(twii_change):.0f}</div>", unsafe_allow_html=True)
            st.markdown(f"<div style='text-align: center; font-size: 0.85rem; color: #888;'>🕒 抓取時間: {twii_time_str}<br>⚡ 資料來源: Fugle富果/Yahoo</div>", unsafe_allow_html=True)
        with col3:
            st.markdown(f"<div style='text-align: left; color: #ffcc00; font-size: 1.05rem; font-weight: bold;'>📝 盤勢分析 ({last_dt_str})</div>", unsafe_allow_html=True)
            st.markdown(f"<div style='text-align: left; font-size: 1.1rem; font-weight: bold;'>{today_title}</div>", unsafe_allow_html=True)
            st.markdown(f"<div style='text-align: left; font-size: 0.85rem; margin-top: 2px; margin-bottom: 8px; line-height: 1.4;'>{today_desc}</div>", unsafe_allow_html=True)
            st.markdown(f"<div style='text-align: left; color: #00ffcc; font-size: 1.05rem; font-weight: bold;'>🔮 次日開盤預測 ({next_dt_str})</div>", unsafe_allow_html=True)
            st.markdown(f"<div style='text-align: left; font-size: 1.1rem; font-weight: bold;'>{tmr_title}</div>", unsafe_allow_html=True)
            st.markdown(f"<div style='text-align: left; font-size: 0.85rem; margin-top: 2px; line-height: 1.4;'>{tmr_desc}</div>", unsafe_allow_html=True)
        
        if st.button("🔄 手動更新即時大盤報價", use_container_width=True): st.cache_data.clear(); st.rerun()
    
    st.markdown("<h4 style='margin-top:20px; text-align:center;'>🌍 全球總經與次日開盤風險評估</h4>", unsafe_allow_html=True)
    st.markdown(f"<div style='text-align:center; font-size:0.85rem; color:#888; margin-top:-10px; margin-bottom:10px;'>🕒 總經最後收盤時間: {macro.get('global_time', '無')}</div>", unsafe_allow_html=True)

    bar_color = "#00cc00" if risk_score < 40 else ("#ffcc00" if risk_score < 70 else "#ff3333")
    risk_label = "🟢 資金充沛，安心佈局" if risk_score < 40 else ("🟡 變數增加，控制倉位" if risk_score < 70 else "🔴 系統風險，嚴格減碼")
    st.markdown(f"<div style='text-align:center; font-size:1.1rem; font-weight:bold;'>系統量化開低風險度：<span style='color:{bar_color};'>{risk_score}%</span></div>", unsafe_allow_html=True)
    st.markdown(f"""<div class="risk-bar-container"><div class="risk-bar-fill" style="width: {risk_score}%; background-color: {bar_color};"></div></div>
    <div style='text-align:center; font-size:0.9rem; color:{bar_color}; font-weight:bold; margin-bottom:15px;'>{risk_label}</div>""", unsafe_allow_html=True)
    
    mc1, mc2, mc3 = st.columns(3)
    sox_data = macro.get('^SOX', {"price": 0, "pct": 0, "time": "無", "url": "https://finance.yahoo.com/quote/^SOX"})
    sox_c = "#ff3333" if sox_data['pct'] >= 0 else "#00cc00"
    with mc1.container(border=True): st.markdown(f"<div style='text-align:center; font-size:0.85rem;'>費城半導體</div><div style='text-align:center; font-size:1.1rem; font-weight:bold; color:{sox_c};'>{sox_data['price']:,.1f}<br>{'+' if sox_data['pct']>0 else ''}{sox_data['pct']:.2f}%</div><div style='text-align:center; font-size:0.75rem; color:#888; margin-top:5px;'>🕒 {sox_data['time']}<br>🔗 來源: Yahoo Finance</div>", unsafe_allow_html=True)
    
    vix_data = macro.get('^VIX', {"price": 0, "pct": 0, "time": "無", "url": "https://finance.yahoo.com/quote/^VIX"})
    vix_c = "#00cc00" if vix_data['pct'] <= 0 else "#ff3333"
    with mc2.container(border=True): st.markdown(f"<div style='text-align:center; font-size:0.85rem;'>VIX 恐慌指數</div><div style='text-align:center; font-size:1.1rem; font-weight:bold; color:{vix_c};'>{vix_data['price']:,.2f}<br>{'+' if vix_data['pct']>0 else ''}{vix_data['pct']:.2f}%</div><div style='text-align:center; font-size:0.75rem; color:#888; margin-top:5px;'>🕒 {vix_data['time']}<br>🔗 來源: Yahoo Finance</div>", unsafe_allow_html=True)
    
    jpy_data = macro.get('JPY=X', {"price": 0, "pct": 0, "time": "無", "url": "https://finance.yahoo.com/quote/JPY=X"})
    jpy_status = "央行趨緩" if jpy_data['pct'] > 0 else "升息撤資警戒"
    with mc3.container(border=True): st.markdown(f"<div style='text-align:center; font-size:0.85rem;'>日圓動向(USD/JPY)</div><div style='text-align:center; font-size:1.1rem; font-weight:bold; color:#ffcc00;'>{jpy_data['price']:,.2f}<br>{jpy_status}</div><div style='text-align:center; font-size:0.75rem; color:#888; margin-top:5px;'>🕒 {jpy_data['time']}<br>🔗 來源: Yahoo Finance</div>", unsafe_allow_html=True)

def analyze_today(df, ticker_number, inst_data=None, is_light_mode=False):
    if df is None or len(df) < 5: return None
    t, p, p5 = df.iloc[-1], df.iloc[-2], df.iloc[-5]
    fund = get_fundamental_and_industry_data(ticker_number, round(t['Close'], 2))
    
    t_open, t_close, t_high, t_low = float(t['Open']), float(t['Close']), float(t['High']), float(t['Low'])
    p_open, p_close = float(p['Open']), float(p['Close'])
    
    red_mask = (df['Open'].shift(1) > df['Close'].shift(1)) & (df['Close'] > df['Open']) & (df['Close'] > df['Open'].shift(1)) & (df['Open'] < df['Close'].shift(1))
    black_mask = (df['Close'].shift(1) > df['Open'].shift(1)) & (df['Open'] > df['Close']) & (df['Open'] > df['Close'].shift(1)) & (df['Close'] < df['Open'].shift(1))
    
    total_range = t_high - t_low if (t_high - t_low) != 0 else 0.001
    upper_shadow = t_high - max(t_open, t_close)
    lower_shadow = min(t_open, t_close) - t_low
    body = abs(t_close - t_open)

    is_support_pullback = (lower_shadow > body * 1.5) and (lower_shadow / total_range > 0.4) and (t_low < p_close) and (t_close >= min(p_open, p_close))
    ma_resistance = min(t['5MA'], t['10MA']) 
    is_resistance_rejection = (upper_shadow > body * 1.5) and (upper_shadow / total_range > 0.4) and (t_high >= ma_resistance) and (t_close < ma_resistance)

    data = {
        "代號": ticker_number, "名稱": get_stock_name(ticker_number), "ticker_raw": ticker_number, "產業": fund['Industry'], "PE": fund['PE'], "EPS": fund['EPS'],
        "昨日收盤價": round(p_close, 2), "收盤價": round(t_close, 2), "漲跌": round(t_close - p_close, 2), "漲跌幅": round((t_close - p_close) / p_close * 100, 2), 
        "成交量": int(t['Volume']/1000), "5日均量": int(df['Volume'].tail(5).mean()/1000),
        "5MA": round(t['5MA'], 2), "10MA": round(t['10MA'], 2), "20MA": round(t['20MA'], 2), "60MA": round(t['60MA'], 2),
        "BB_UP": round(t['BB_UP'], 2), "BB_DN": round(t['BB_DN'], 2), "BIAS": round(t['BIAS_20'], 2),
        "MACD柱": round(t['MACD_Hist'], 3), "前日MACD柱": round(p['MACD_Hist'], 3), "J值": round(t['J'], 2), "ATR": round(t.get('ATR', 0), 2),
        "訊號": (t_close > t['20MA']) and (t_close < t['5MA']) and (t['J'] < 20),
        "紅吞": bool(red_mask.iloc[-1]), "黑吞": bool(black_mask.iloc[-1]), "近七日紅吞": bool(red_mask.tail(7).any()),
        "回測有撐": is_support_pullback, "反彈遇壓": is_resistance_rejection,
        "5日線即將上彎": t_close > (float(df['Close'].iloc[-5]) if len(df)>=5 else t_close)
    }
    
    sc = 0
    if data['訊號']: sc+=3
    if data['收盤價'] <= data['BB_DN'] * 1.02: sc+=2
    if data['BIAS'] < -5: sc+=1
    if str(fund['EPS']).replace(',','').replace('.','').replace('-','').isdigit() and float(str(fund['EPS']).replace(',','')) > 0: sc+=2
    if data['成交量'] > data['5日均量'] * 1.1: sc+=2
    else: sc-=1
    if data['MACD柱'] > data['前日MACD柱']: sc+=2
    else: sc-=3
    if inst_data and len(inst_data) >= 3 and sum([int(str(x['單日合計(張)']).replace(',', '')) for x in inst_data[:3]]) > 0: sc+=1
    if data['紅吞']: sc+=3
    if data['黑吞']: sc-=3
    if data['回測有撐']: sc+=2
    if data['反彈遇壓']: sc-=2
    if data['J值'] >= 80: sc-=3
    if data['收盤價'] >= data['BB_UP'] * 0.98: sc-=2
    if data['收盤價'] < data['20MA']: sc-=2
    if data['收盤價'] < data['5MA'] and not data.get('5日線即將上彎'): sc-=1
    
    data['Score'], data['評級'] = sc, "🟢 S級" if sc >= 5 else ("🟡 A級" if sc >= 2 else "⚪ 觀望")
    return data

@st.cache_data(ttl=180, show_spinner=False)
def get_global_scan_results(pool_tuple):
    scan_results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        for f in concurrent.futures.as_completed({executor.submit(lambda s: analyze_today(get_stock_data(s), s), stock): stock for stock in pool_tuple}):
            if f.result(): scan_results.append(f.result())
    return scan_results

# 🟢 無斷層繪圖核心
def draw_professional_chart(df, ticker_name, latest_price, view_days, is_light_mode, show_buy_signal=False, f_data=None, show_sup_res=False, show_signals=True):
    df_view = df.tail(view_days)
    
    # 強制X軸為連續整數，消滅假日空白
    x_indices = list(range(len(df_view)))
    x_labels = df_view.index.strftime('%Y/%m/%d').tolist()

    colors = ['#ff3333' if row['Close'] >= row['Open'] else '#00cc00' for _, row in df_view.iterrows()]
    last_row = df_view.iloc[-1]
    
    fig = make_subplots(rows=4, cols=1, shared_xaxes=True, row_heights=[0.45, 0.15, 0.15, 0.25], vertical_spacing=0.06)
    line_k, line_d, line_j = ("#0066cc", "#ff9900", "#9900cc") if is_light_mode else ("white", "yellow", "magenta")
    grid_c = "rgba(0,0,0,0.1)" if is_light_mode else "rgba(255,255,255,0.1)"
    bg_c = "#ffffff" if is_light_mode else "#0e1117"
    text_c = "#333" if is_light_mode else "#ccc"
    
    fig.add_trace(go.Candlestick(x=x_indices, open=df_view['Open'], high=df_view['High'], low=df_view['Low'], close=df_view['Close'], increasing_line_color='#ff3333', decreasing_line_color='#00cc00', name="K線"), row=1, col=1)
    
    fig.add_trace(go.Scatter(x=x_indices, y=df_view['5MA'], line=dict(color='orange', width=2), name="5T"), row=1, col=1)
    fig.add_trace(go.Scatter(x=x_indices, y=df_view['10MA'], line=dict(color='#ffcc00', width=2), name="10T"), row=1, col=1)
    fig.add_trace(go.Scatter(x=x_indices, y=df_view['20MA'], line=dict(color='cyan', width=2), name="20T"), row=1, col=1)
    
    fig.add_hline(y=latest_price, line_dash="dash", line_color="#ffcc00", row=1, col=1)
    fig.add_annotation(x=0.01, y=0.92, xref="paper", yref="y domain", text=f"現價: {latest_price:.2f}", showarrow=False, font=dict(color="#ffcc00", size=14, weight="bold"), xanchor="left", bgcolor="rgba(0,0,0,0.5)")
    
    if show_sup_res:
        highest_price = df_view['High'].max()
        lowest_price = df_view['Low'].min()
        fig.add_hline(y=highest_price, line_dash="dash", line_color="#ff3333", row=1, col=1, annotation_text=f"壓力 {highest_price:.2f}", annotation_position="top right", annotation_font=dict(size=12, color="#ff3333"))
        fig.add_hline(y=lowest_price, line_dash="dash", line_color="#00cc00", row=1, col=1, annotation_text=f"支撐 {lowest_price:.2f}", annotation_position="bottom right", annotation_font=dict(size=12, color="#00cc00"))
    
    re_x, re_y, re_text = [], [], []
    be_x, be_y, be_text = [], [], []
    sup_x, sup_y, sup_text = [], [], []
    res_x, res_y, res_text = [], [], []
    deduct_up_x, deduct_up_y, deduct_up_text = [], [], []
    deduct_down_x, deduct_down_y, deduct_down_text = [], [], []
    
    start_pos = len(df) - len(df_view)
    
    for i in range(1, len(df_view)):
        pos = start_pos + i
        t, p = df.iloc[pos], df.iloc[pos-1]
        
        is_red = (p['Open'] > p['Close']) and (t['Close'] > t['Open']) and (t['Close'] > p['Open']) and (t['Open'] < p['Close'])
        is_black = (p['Close'] > p['Open']) and (t['Open'] > t['Close']) and (t['Open'] > p['Close']) and (t['Close'] < p['Open'])
        
        if is_red: re_x.append(i); re_y.append(t['Low'] * 0.98); re_text.append("<b>紅吞</b>")
        if is_black: be_x.append(i); be_y.append(t['High'] * 1.02); be_text.append("<b>黑吞</b>")
        
        body = abs(t['Close'] - t['Open'])
        total_range = max(0.001, t['High'] - t['Low'])
        if ((min(t['Open'], t['Close']) - t['Low']) > body * 1.5) and ((min(t['Open'], t['Close']) - t['Low']) / total_range > 0.4) and (t['Low'] < p['Close']):
            sup_x.append(i); sup_y.append(t['Low'] * 0.95); sup_text.append("<b>撐</b>")
        if ((t['High'] - max(t['Open'], t['Close'])) > body * 1.5) and ((t['High'] - max(t['Open'], t['Close'])) / total_range > 0.4) and (t['High'] >= min(t['5MA'], t['10MA'])) and (t['Close'] < min(t['5MA'], t['10MA'])):
            res_x.append(i); res_y.append(t['High'] * 1.05); res_text.append("<b>壓</b>")

        if pos >= 5:
            if (t['Close'] >= t['5MA']) and (t['Close'] > df.iloc[pos - 5]['Close']) and not ((p['Close'] >= p['5MA']) and (p['Close'] > df.iloc[pos - 6]['Close'])):
                deduct_up_x.append(i); deduct_up_y.append(t['Low'] * 0.85); deduct_up_text.append("<b>↗️</b>")
            if (t['Close'] < t['5MA']) and (t['Close'] < df.iloc[pos - 5]['Close']) and not ((p['Close'] < p['5MA']) and (p['Close'] < df.iloc[pos - 6]['Close'])):
                deduct_down_x.append(i); deduct_down_y.append(t['High'] * 1.15); deduct_down_text.append("<b>↘️</b>")

    if show_signals:
        if re_x: fig.add_trace(go.Scatter(x=re_x, y=re_y, mode='text', text=re_text, textposition="bottom center", textfont=dict(color="#ff3333", size=13), hoverinfo='skip'), row=1, col=1)
        if be_x: fig.add_trace(go.Scatter(x=be_x, y=be_y, mode='text', text=be_text, textposition="top center", textfont=dict(color="#00cc00", size=13), hoverinfo='skip'), row=1, col=1)
        if sup_x: fig.add_trace(go.Scatter(x=sup_x, y=sup_y, mode='text', text=sup_text, textposition="bottom center", textfont=dict(color="#ff9900" if is_light_mode else "#ffcc00", size=13), hoverinfo='skip'), row=1, col=1)
        if res_x: fig.add_trace(go.Scatter(x=res_x, y=res_y, mode='text', text=res_text, textposition="top center", textfont=dict(color="#0066cc" if is_light_mode else "#00ccff", size=13), hoverinfo='skip'), row=1, col=1)
        if deduct_up_x: fig.add_trace(go.Scatter(x=deduct_up_x, y=deduct_up_y, mode='text', text=deduct_up_text, textposition="bottom center", textfont=dict(color="#ff3333", size=13), hoverinfo='skip'), row=1, col=1)
        if deduct_down_x: fig.add_trace(go.Scatter(x=deduct_down_x, y=deduct_down_y, mode='text', text=deduct_down_text, textposition="top center", textfont=dict(color="#00cc00", size=13), hoverinfo='skip'), row=1, col=1)

    # 💡 買點圖示優化：單純的青色向上箭頭
    if show_buy_signal and f_data:
        buy_x, buy_y = [], []
        for i in range(len(df_view)):
            sub_df = df.iloc[:df.index.get_loc(df_view.index[i])+1]
            if len(sub_df) >= 5:
                t_data = analyze_today(sub_df, ticker_name) 
                if t_data and t_data['Score'] >= 2:
                    buy_x.append(i); buy_y.append(df_view['Low'].iloc[i] * 0.90) 
        if buy_x:
            fig.add_trace(go.Scatter(x=buy_x, y=buy_y, mode='markers', marker=dict(symbol='triangle-up', size=16, color='#00ffcc' if not is_light_mode else '#0066cc'), name="買進訊號", hoverinfo='skip'), row=1, col=1)
            
    fig.add_trace(go.Bar(x=x_indices, y=df_view['Volume'], marker_color=colors, name="VOL"), row=2, col=1)
    macd_colors = ['#ff3333' if val > 0 else '#00cc00' for val in df_view['MACD_Hist']]
    fig.add_trace(go.Bar(x=x_indices, y=df_view['MACD_Hist'], marker_color=macd_colors, name="OSC"), row=3, col=1)
    fig.add_trace(go.Scatter(x=x_indices, y=df_view['MACD'], line=dict(color=line_k, width=1.5), name="DIF"), row=3, col=1)
    fig.add_trace(go.Scatter(x=x_indices, y=df_view['Signal'], line=dict(color=line_d, width=1.5), name="MACD"), row=3, col=1)
    fig.add_trace(go.Scatter(x=x_indices, y=df_view['K'], line=dict(color=line_k, width=1.5), name="K"), row=4, col=1)
    fig.add_trace(go.Scatter(x=x_indices, y=df_view['D'], line=dict(color=line_d, width=1.5), name="D"), row=4, col=1)
    fig.add_trace(go.Scatter(x=x_indices, y=df_view['J'], line=dict(color=line_j, width=1.5), name="J"), row=4, col=1)

    ann_bg = "rgba(255,255,255,0.8)" if is_light_mode else "rgba(26,28,36,0.6)"
    fig.add_annotation(x=0.01, y=0.98, xref="paper", yref="y domain", text=f"5T:{last_row['5MA']:.1f} | 10T:{last_row['10MA']:.1f} | 20T:{last_row['20MA']:.1f}", showarrow=False, font=dict(color="#ff9900" if is_light_mode else "#ffcc00", size=12), xanchor="left", bgcolor=ann_bg)
    fig.add_annotation(x=0.01, y=0.95, xref="paper", yref="y2 domain", text=f"VOL: {last_row['Volume']:,.0f}", showarrow=False, font=dict(color=text_c, size=12), xanchor="left", bgcolor=ann_bg)
    fig.add_annotation(x=0.01, y=0.95, xref="paper", yref="y3 domain", text=f"MACD:{last_row['MACD']:.2f} | DIF:{last_row['Signal']:.2f} | OSC:{last_row['MACD_Hist']:.2f}", showarrow=False, font=dict(color=text_c, size=12), xanchor="left", bgcolor=ann_bg)
    fig.add_annotation(x=0.01, y=0.95, xref="paper", yref="y4 domain", text=f"K:{last_row['K']:.2f} | D:{last_row['D']:.2f} | J:{last_row['J']:.2f}", showarrow=False, font=dict(color=text_c, size=12), xanchor="left", bgcolor=ann_bg)

    tick_interval = max(1, len(x_indices) // 10)
    fig.update_xaxes(
        tickmode='array', tickvals=x_indices[::tick_interval], ticktext=x_labels[::tick_interval],
        showgrid=True, gridcolor=grid_c
    )
    
    fig.update_yaxes(fixedrange=True, showgrid=True, gridcolor=grid_c)
    fig.update_layout(xaxis_rangeslider_visible=False, template="plotly_white" if is_light_mode else "plotly_dark", height=850, margin=dict(l=10, r=10, t=10, b=30), paper_bgcolor=bg_c, plot_bgcolor=bg_c, hovermode='x unified', hoverlabel=dict(bgcolor=bg_c, font_size=13, font_color=text_c), dragmode=False, showlegend=False)
    
    fig.add_annotation(text="📊 資料來源: Fugle富果 API / FinMind / 鉅亨網", xref="paper", yref="paper", x=1.0, y=-0.05, showarrow=False, font=dict(size=12, color=text_c))
    return fig

# ==========================================
# 🚀 頁面路由與主邏輯
# ==========================================
if st.session_state.page == "home":
    st.markdown("<h1 style='text-align: center;'>🇹🇼 雷達總機 PRO</h1>", unsafe_allow_html=True)
    
    if auto_refresh:
        @st.fragment(run_every="60s")
        def render_live_home_board(): render_index_board()
        render_live_home_board()
    else:
        @st.fragment
        def render_static_home_board(): render_index_board()
        render_static_home_board()

    # 🌟 左側欄加入全局過濾器
    st.sidebar.markdown("---")
    st.sidebar.title("🛠️ 榜單全局過濾")
    min_score = st.sidebar.slider("🤖 最低 AI 評分", -5, 10, 2)
    max_pe = st.sidebar.slider("📈 本益比上限", 5, 200, 200)
    # 動態產生產業選單的佔位符，稍後填入
    ind_filter_placeholder = st.sidebar.empty()

    st.markdown("<h3 style='margin-top: 15px;'>🎯 策略條件篩選</h3>", unsafe_allow_html=True)
    btn_col1, btn_col2, btn_col3 = st.columns(3)
    if btn_col1.button("✅ 綜合買點榜", use_container_width=True, key="btn_scan_buy"): st.session_state.scan_mode = "buy"; st.rerun()
    if btn_col2.button("🔥 紅吞反轉榜", use_container_width=True, key="btn_scan_red"): st.session_state.scan_mode = "red_engulf"; st.rerun()
    if btn_col3.button("📊 近五日成交量", use_container_width=True, key="btn_scan_vol"): st.session_state.scan_mode = "recent"; st.rerun()
    
    top_100_pool = fetch_twse_top_100()
    pool = tuple(set(top_100_pool + st.session_state.custom_pool + list(STOCK_NAMES.keys())))
    
    with st.spinner("🚀 大腦背景資料庫存取中 (若無快取約需 5 秒，快取命中則 0.1 秒極速秒發)..."):
        scan_results = get_global_scan_results(pool)
            
    if scan_results:
        df_results = pd.DataFrame(scan_results).fillna(0)
        df_results['Bullish_Count'] = df_results.apply(
            lambda r: (1 if r.get('紅吞') or r.get('近七日紅吞') else 0) + 
                      (1 if r.get('回測有撐') else 0) + 
                      (1 if r.get('5日線即將上彎') else 0), axis=1)

        # 填入側邊欄產業選單
        all_inds = [i for i in df_results['產業'].unique() if i != "一般產業"] + ["一般產業"]
        selected_ind = ind_filter_placeholder.multiselect("🏭 鎖定產業", options=all_inds)

        # 🚀 套用全局過濾
        df_results['PE_num'] = pd.to_numeric(df_results['PE'], errors='coerce').fillna(999)
        df_filtered = df_results[(df_results['Score'] >= min_score) & (df_results['PE_num'] <= max_pe)]
        if selected_ind: df_filtered = df_filtered[df_filtered['產業'].isin(selected_ind)]

        # 🌟 獨家：產業資金熱力圖 (連動左側過濾)
        st.markdown("<h4 style='margin-top: 10px; margin-bottom: 5px;'>🗺️ 今日台股產業資金熱力圖 <span style='font-size:0.8rem; color:#888;'>[面積=成交量 | 顏色=漲跌幅]</span></h4>", unsafe_allow_html=True)
        df_plot = df_filtered[df_filtered['成交量'] > 0].copy()
        if not df_plot.empty:
            df_plot['漲跌_數值'] = pd.to_numeric(df_plot['漲跌幅'], errors='coerce').fillna(0)
            fig_tree = px.treemap(df_plot, path=[px.Constant("台股熱門雷達池"), '產業', '名稱'], values='成交量', color='漲跌_數值', color_continuous_scale=['#00cc00', '#222222', '#ff3333'], color_continuous_midpoint=0)
            fig_tree.update_traces(textinfo="label+value", textfont=dict(size=14, color="white", weight="bold"))
            fig_tree.update_layout(margin=dict(t=10, l=10, r=10, b=10), paper_bgcolor=app_bg, plot_bgcolor=app_bg, height=400)
            st.plotly_chart(fig_tree, use_container_width=True)
        else:
            st.warning("過濾條件太嚴格，目前無股票可繪製熱力圖。")
        st.divider()

        # 榜單呈現 (連動左側過濾)
        if st.session_state.scan_mode == "recent":
            st.markdown("##### 📊 條件過濾後：近五日成交量排行榜")
            df_disp = df_filtered.sort_values(by="成交量", ascending=False).head(20)
        elif st.session_state.scan_mode == "red_engulf":
            st.markdown("##### 🔥 條件過濾後：近七日觸發「紅吞」反轉型態標的")
            df_disp = df_filtered[df_filtered['近七日紅吞'] == True].sort_values(by=['Score', 'Bullish_Count', '漲跌幅'], ascending=[False, False, False]).head(20)
        elif st.session_state.scan_mode == "buy":
            st.markdown("##### 🎯 條件過濾後：尋找買點榜單 (高靈敏度動能捕捉)")
            df_disp = df_filtered.sort_values(by=['Score', 'Bullish_Count', '漲跌幅'], ascending=[False, False, False]).head(20)
            
        st.session_state.nav_pool = df_disp['ticker_raw'].tolist()
        st.session_state.nav_pool_data = df_disp.to_dict('records') 
            
        for _, r in df_disp.iterrows():
            p_val = r['漲跌']
            sign = "+" if p_val > 0 else ""
            trend_icon = "🔺" if p_val > 0 else ("🔻" if p_val < 0 else "➖")
            s_score = r['Score']
            score_icon = "🟢 S級" if s_score >= 5 else ("🟡 A級" if s_score >= 2 else "⚪ 觀望")
            
            tags = []
            if r.get('紅吞'): tags.append("🔺紅吞")
            if r.get('黑吞'): tags.append("🔻黑吞")
            if r.get('回測有撐'): tags.append("📌撐")
            if r.get('反彈遇壓'): tags.append("⚠️壓")
            if r.get('5日線即將上彎'): tags.append("↗️")
            tag_display = " | ".join(tags)
            if tag_display: tag_display = f" | {tag_display}"
            
            button_label = f"▪️ {r['代號']} {r['名稱']} {trend_icon}{r['收盤價']}({sign}{r['漲跌幅']}%) | 產業:{r['產業']} | {score_icon}{tag_display}"
            if st.button(button_label, key=f"btn_scan_list_{r['ticker_raw']}_{st.session_state.scan_mode}", use_container_width=True):
                st.session_state.update({"current_stock": r['ticker_raw'], "page": "analysis", "date_offset": 0})
                st.rerun()

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

    def render_analysis_content():
        load_ph = st.empty()
        with load_ph.container():
            st.markdown(f"<h4 style='text-align:center;'>🚀 正在喚醒【{target} {c_name}】AI 分析大腦...</h4>", unsafe_allow_html=True)
            p_bar = st.progress(0)
            df_chart = get_stock_data(target)
            p_bar.progress(30)
            
            if df_chart is not None and len(df_chart) >= 5:
                df_slice = df_chart.iloc[:len(df_chart) + st.session_state.date_offset] if st.session_state.date_offset < 0 else df_chart
                inst_data = get_institutional_trading(target)
                p_bar.progress(50)
                data = analyze_today(df_slice, target, inst_data, is_light_mode)
                f_data = get_fundamental_and_industry_data(target, data['收盤價'])
                p_bar.progress(80)
                twii_close, twii_change, twii_time_str = get_twii_quote()
                twii_df = get_stock_data("^TWII")
                t_title, t_desc, tmr_title, tmr_desc, l_dt, n_dt, risk_score, macro = open_pred_logic(twii_df, twii_close, twii_change, twii_time_str)
                p_bar.progress(100)
                time.sleep(0.1) 
            else:
                load_ph.empty()
                st.error("查無此股票資料。")
                return

        if df_chart is not None and len(df_slice) >= 5:
            load_ph.empty()
            display_time = get_stock_live_time(target)
            p_color = '#ff3333' if data['漲跌'] >= 0 else '#00cc00'
            analysis_date = df_slice.index[-1].strftime('%Y/%m/%d')
            
            col_main_view, col_right_menu = st.columns([3.9, 1.1])
            with col_main_view:
                st.markdown(f"<h2 style='text-align: center; margin-bottom: 5px;'>🎯 {target} {c_name}</h2>", unsafe_allow_html=True)
                st.markdown(f"<div style='text-align: center; color: #888; font-size: 1.1rem;'>【{f_data['Industry']}】</div>", unsafe_allow_html=True)
                st.markdown(f"<h3 style='text-align: center; color: {p_color}; font-size: 2.2rem; margin-bottom: 0px;'>{data['收盤價']} ({'+' if data['漲跌']>0 else ''}{data['漲跌幅']}%)</h3>", unsafe_allow_html=True)
                st.markdown(f"<div style='text-align: center; color: #888; font-size: 1rem; margin-top: 5px;'>昨日收盤: {data['昨日收盤價']} | 最新報價: {data['收盤價']}</div>", unsafe_allow_html=True)
                st.markdown(f"<div style='text-align: center; color: #888; font-size: 0.9rem; margin-bottom: 10px;'>🕒 盤勢分析日期: {analysis_date} | 抓取時間: {display_time}</div>", unsafe_allow_html=True)
                
                _, up_c, _ = st.columns([1, 2, 1])
                if up_c.button("🔄 更新個股即時數值", use_container_width=True): st.cache_data.clear(); st.rerun()
                st.markdown("---")

                # 🌟 獨家戰情儀表板：歷史回測資金曲線
                st.markdown("##### 💡 近兩個月 AI 歷史回測與資金曲線")
                recent_60 = df_slice.tail(60)
                trades, capital = [], 100000
                eq_x, eq_y = [recent_60.index[0]], [capital]
                cur_hold, b_price, en_date = False, 0, None
                recent_signals = [] # 收集信號給 ATR 判斷使用
                
                for idx in range(len(recent_60)):
                    c_date = recent_60.index[idx]
                    s_df = df_slice.iloc[:df_slice.index.get_loc(c_date)+1]
                    if len(s_df) >= 5:
                        t_data = analyze_today(s_df, target)
                        if t_data['Score'] >= 2: recent_signals.append((c_date, t_data['收盤價'], t_data.get('ATR', 0)))
                        c_p, atr = t_data['收盤價'], t_data.get('ATR', 0)
                        
                        if cur_hold:
                            if c_p <= (b_price - 1.5 * atr) or (c_date - en_date).days > 15:
                                prof = (c_p - b_price) / b_price * capital
                                capital += prof; trades.append(1 if prof > 0 else 0)
                                cur_hold = False; eq_x.append(c_date); eq_y.append(capital)
                        elif t_data['Score'] >= 2:
                            cur_hold = True; b_price, en_date = c_p, c_date
                
                if cur_hold:
                    prof = (data['收盤價'] - b_price) / b_price * capital
                    eq_x.append(df_slice.index[-1]); eq_y.append(capital + prof)
                    
                win_rate = (sum(trades) / len(trades) * 100) if trades else 0
                
                ec1, ec2 = st.columns([1, 2.5])
                with ec1.container(border=True):
                    fig_g = go.Figure(go.Indicator(mode="gauge+number", value=win_rate, title={'text': "策略回測勝率"}, gauge={'axis': {'range': [0, 100]}, 'bar': {'color': "#00ffcc"}}))
                    fig_g.update_layout(height=200, margin=dict(l=10, r=10, t=30, b=10), paper_bgcolor=app_bg)
                    st.plotly_chart(fig_g, use_container_width=True)
                with ec2.container(border=True):
                    fig_eq = go.Figure(go.Scatter(x=eq_x, y=eq_y, mode='lines+markers', line=dict(color='#ffcc00', width=3), name="資金變化"))
                    fig_eq.update_layout(title="累積獲利曲線 (初始本金 10 萬)", height=200, margin=dict(l=10, r=10, t=30, b=10), paper_bgcolor=app_bg, plot_bgcolor=app_bg)
                    st.plotly_chart(fig_eq, use_container_width=True)

                # 🌟 獨家戰情儀表板：千張大戶持股趨勢
                st.markdown("##### 🏦 進階籌碼：千張大戶持股趨勢追蹤")
                h_dates, h_percents = get_big_player_holding(target)
                if h_dates and h_percents:
                    fig_hold = go.Figure(go.Scatter(x=h_dates, y=h_percents, mode='lines+markers', fill='tozeroy', line=dict(color='#ff3333', width=2), fillcolor='rgba(255,51,51,0.2)'))
                    fig_hold.update_layout(height=180, margin=dict(l=10, r=10, t=10, b=10), paper_bgcolor=app_bg, plot_bgcolor=app_bg, yaxis_title="大戶持股 %")
                    st.plotly_chart(fig_hold, use_container_width=True)
                    st.markdown("<div style='text-align:right; font-size:0.8rem; color:#888;'>來源: FinMind (400張以上大戶)</div>", unsafe_allow_html=True)
                else: st.info("此標的暫無大戶持股資料。")

                # ==========================================
                # 完美保留：原版的文字技術/籌碼/基本分析
                # ==========================================
                t_text_c = "#333" if is_light_mode else "#ddd"
                card_bg = "#f4f6f9" if is_light_mode else "#16181f"
                sum_bg = "rgba(0,0,0,0.05)" if is_light_mode else "rgba(255,255,255,0.05)"
                b_col = "#ddd" if is_light_mode else "#333"

                t_bull = []
                if data.get('紅吞'): t_bull.append(f"🔥 <span style='color:#ff3333; font-weight:bold;'>今日出現「紅吞」K線型態，強烈見底買進訊號。</span>")
                elif data.get('近七日紅吞'): t_bull.append(f"🔥 <span style='color:#ff3333; font-weight:bold;'>底部表態：近七日內曾出現「紅吞」型態。</span>")
                if data['J值'] < 20: t_bull.append(f"🔥 <span style='color:#ff3333; font-weight:bold;'>KDJ 極度超賣：J 值來到 ({data['J值']})，醞釀技術性反彈。</span>")
                if data['BIAS'] < -5: t_bull.append(f"🔥 <span style='color:#ff3333; font-weight:bold;'>負乖離過大：月線乖離率達 ({data['BIAS']}%)，超跌反彈機率極高。</span>")
                if data['成交量'] > data['5日均量'] * 1.1 and data.get('漲跌',0) > 0: t_bull.append(f"🔥 <span style='color:#ff3333; font-weight:bold;'>量價確認：今日量能放大大於5日均量，主力進場點火信號明確。</span>")
                
                t_html = f"<div style='border: 1px solid {b_col}; border-radius: 8px; padding: 15px; margin-bottom: 15px; background-color: {card_bg};'><h4 style='color: #00ccff; margin-top: 0;'>📈 技術面分析 <span style='font-size:0.8rem; color:#888;'>[來源: Fugle富果]</span></h4><ul style='color:{t_text_c}'>"
                for b in t_bull: t_html += f"<li>{b}</li>"
                t_html += f"</ul></div>"

                c_html = f"<div style='border: 1px solid {b_col}; border-radius: 8px; padding: 15px; margin-bottom: 15px; background-color: {card_bg};'><h4 style='color: #ffcc00; margin-top: 0;'>🏦 法人籌碼 <span style='font-size:0.8rem; color:#888;'>[來源: FinMind]</span></h4>"
                if inst_data:
                    c_html += f"<table style='width: 100%; text-align: center; border-collapse: collapse; font-size: 0.9rem; border: 1px solid {b_col}; color: {t_text_c};'><tr style='background-color: {sum_bg};'><th>日期</th><th>外資</th><th>投信</th><th>自營商</th><th>合計</th></tr>"
                    for r in inst_data[:5]:
                        def gc(v): return "#ff3333" if v>0 else ("#00cc00" if v<0 else t_text_c)
                        c_html += f"<tr><td style='border: 1px solid {b_col};'>{r['日期']}</td><td style='border: 1px solid {b_col}; color: {gc(r['外資(張)'])};'>{r['外資(張)']}</td><td style='border: 1px solid {b_col}; color: {gc(r['投信(張)'])};'>{r['投信(張)']}</td><td style='border: 1px solid {b_col}; color: {gc(r['自營商(張)'])};'>{r['自營商(張)']}</td><td style='border: 1px solid {b_col}; color: {gc(r['單日合計(張)'])};'>{r['單日合計(張)']}</td></tr>"
                    c_html += "</table></div>"
                else: c_html += f"<div style='color:{t_text_c};'>暫無資料</div></div>"

                f_html = f"<div style='border: 1px solid {b_col}; border-radius: 8px; padding: 15px; margin-bottom: 15px; background-color: {card_bg};'><h4 style='color: #ff99ff; margin-top: 0;'>📑 基本面分析 <span style='font-size:0.8rem; color:#888;'>[來源: 鉅亨網/Yahoo]</span></h4><ul style='color:{t_text_c}'>"
                f_html += f"<li>⚪ <b>EPS</b>: 當季每股盈餘 <b>{f_data['EPS']}</b> 元。</li><li>⚪ <b>本益比 (PE)</b>: 最新即時估值為 <b>{f_data['PE']}</b> 倍。</li></ul></div>"
                
                # 🌟 ATR 功能與建倉建議合併
                cur_p = data['收盤價']
                lw_b = data['5MA'] if cur_p > data['5MA'] else data['20MA']
                r_min = min(cur_p, lw_b) * 0.98 if abs(cur_p - lw_b)<0.05 else min(cur_p, lw_b)
                
                atr_text = ""
                if recent_signals:
                    l_date, l_price, l_atr = recent_signals[-1]
                    dyn_stop = round(l_price - (1.5 * l_atr), 2)
                    if cur_p <= dyn_stop: atr_text = f"<br><br>🚨 <b style='color:#ff3333;'>【ATR 停損警報】已跌穿動態防守線 {dyn_stop}！</b> 強烈建議嚴守紀律，果斷停損出場！"
                    else: atr_text = f"<br><br>🛡️ <b style='color:#00cc00;'>【ATR 動態防護中】</b> 目前動態防守底線為 <b>{dyn_stop}</b>，未跌破可持股續抱。"
                
                sc = data['Score']
                if sc >= 5: v_t, v_c, v_a = "🟢 S級買點：強烈佈局", "#00cc00", f"✅ <b>進場判斷：強烈買進</b><br>📌 建議建倉區間：現價 ({cur_p:.2f}) ~ 逢低 {r_min:.2f} 之間加碼。{atr_text}"
                elif sc >= 2: v_t, v_c, v_a = "🟡 A級機會：偏多試單", "#ffcc00", f"✅ <b>進場判斷：分批試單</b><br>📌 建議建倉區間：現價 ({cur_p:.2f}) ~ 逢低 {r_min:.2f} 之間佈局。{atr_text}"
                else: v_t, v_c, v_a = "⚪ 中性觀望：多空不明", "#888888", f"⏳ <b>進場判斷：暫緩進場</b><br>多空拉扯劇烈，建議靜待訊號明朗化。📌 支撐參考 {r_min:.2f}。{atr_text}"

                st.markdown(f"""<div style="border: 2px solid {v_c}; border-radius: 10px; padding: 20px; margin-bottom: 20px; background-color: {bg_col};">
                <h3 style="text-align: center; color: {v_c}; margin-top: 0;">🤖 AI 決策：{v_t}</h3>
                {t_html}{c_html}{f_html}
                <div style="background-color: {sum_bg}; padding: 15px; border-radius: 8px; border-left: 5px solid {v_c};">
                <p style="font-size: 1.1rem; color: {text_col}; margin: 0; line-height: 1.6;">{v_a}</p></div></div>""", unsafe_allow_html=True)

                dc1, dc2, dc3, dc5, dc6, dc7 = st.columns([0.8, 0.8, 0.8, 1.3, 1.3, 1.3])
                dc1.button("30日", on_click=set_view_days, args=(30,))
                dc2.button("60日", on_click=set_view_days, args=(60,))
                dc3.button("90日", on_click=set_view_days, args=(90,))
                with dc5: st.toggle("🛒 顯示買進", value=True, key='tgl_buy')
                with dc6: st.toggle("📏 歷史高低點", value=True, key='tgl_sup')
                with dc7: st.toggle("🏷️ 顯示符號", value=True, key='tgl_sig')
                
                fig = draw_professional_chart(df_slice, target, data['收盤價'], st.session_state.view_days, is_light_mode, st.session_state.get('tgl_buy', True), f_data, st.session_state.get('tgl_sup', True), st.session_state.get('tgl_sig', True))
                st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})

            with col_right_menu:
                st.markdown(f'''<div style="text-align: center; font-size: 1.15rem; font-weight: bold; background-color: {bg_col}; border: 1px solid {border_col}; padding: 8px; border-radius: 6px; color: #ffcc00 !important; margin-bottom: 12px;">📋 當前雷達清單</div>''', unsafe_allow_html=True)
                if n_pool:
                    nav_data = st.session_state.get('nav_pool_data', [])
                    for sid in n_pool:
                        info = next((i for i in nav_data if i["ticker_raw"] == sid), None)
                        if info:
                            tg = []
                            if info.get('紅吞'): tg.append("🔺紅吞")
                            if info.get('回測有撐'): tg.append("📌撐")
                            td = f" | {' '.join(tg)}" if tg else ""
                            btn_l = f"{'⭐' if sid==target else '▪️'} {info['代號']} {info['名稱']} | {'🟢S' if info['Score']>=5 else '🟡A'}{td}"
                        else: btn_l = f"{'⭐' if sid==target else '▪️'} {sid} {get_stock_name(sid)}"
                        if st.button(btn_l, key=f"r_nav_{sid}", use_container_width=True):
                            st.session_state.update({"current_stock": sid}); st.rerun()

    if auto_refresh:
        @st.fragment(run_every="60s")
        def render_live_analysis(): render_analysis_content()
        render_live_analysis()
    else:
        @st.fragment
        def render_static_analysis(): render_analysis_content()
        render_static_analysis()


```
