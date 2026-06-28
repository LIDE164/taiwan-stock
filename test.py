# 最後修改時間: 2026-06-28 19:30 CST
# 版本: v2.0 (Fugle API 升級版 + ATR 動態停損)
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
import re
import concurrent.futures
import random
import xml.etree.ElementTree as ET
import streamlit.components.v1 as components
from streamlit_autorefresh import st_autorefresh

# === 核心 API 金鑰設定 ===
# 使用者專屬 FinMind API Token (用於三大法人籌碼)
FINMIND_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjoiYTQ1Njg4MTUwQGdtYWlsLmNvbSIsImVtYWlsIjoiYTQ1Njg4MTUwQGdtYWlsLmNvbSIsInRva2VuX3ZlcnNpb24iOjB9.LUcb8YPV4yo93_aB3obP4Z5iUGqAgTaH28ySx9UNv5I"
# 使用者專屬 Fugle 富果 API Token (用於即時報價與精準歷史K線)
FUGLE_API_KEY = "NWMxYjY4MzctM2VlNC00MjhhLTk5NjctOWQyYzBmMmJmZWU1IGFmNDk3NWRkLWY3NTMtNGZiYy04MTgyLTM3MTY4NDYyNTAwMw=="
FUGLE_HEADERS = {"X-API-KEY": FUGLE_API_KEY}

# ==========================================
# 0. 系統初始化與風格設定
# ==========================================
st.set_page_config(page_title="專業交易雷達 v2.0", layout="wide", initial_sidebar_state="collapsed")

# 🚀 PWA 獨立 APP 宣告
st.markdown('''
<head>
    <link rel="manifest" href="/manifest.json">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <meta name="apple-mobile-web-app-title" content="交易雷達">
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
sticky_bg = "rgba(255,255,255,0.95)" if is_light_mode else "rgba(26,28,36,0.95)"
app_bg = "#f4f6f9" if is_light_mode else "#0e1117"
sum_bg = "rgba(0,0,0,0.05)" if is_light_mode else "rgba(255,255,255,0.05)"

css_style = """
<style>
    .stApp { background-color: """ + app_bg + """; }
    #MainMenu {visibility: hidden;} footer {visibility: hidden;}
    [data-testid="collapsedControl"] { border: 1px solid """ + border_col + """ !important; border-radius: 8px !important; background-color: """ + bg_col + """ !important; padding: 5px 12px !important; display: flex !important; align-items: center !important; width: auto !important; transition: 0.3s; }
    [data-testid="collapsedControl"]::after { content: " ⭐ 我的群組"; font-size: 1.1rem; font-weight: bold; color: #ffcc00; margin-left: 8px; }
    .stButton button { font-weight: bold !important; border-radius: 8px !important; text-align: left !important; }
    button[kind="primary"] { font-size: 1.5rem !important; padding: 15px !important; border-radius: 12px !important; background-color: #ffcc00 !important; color: #111 !important; border: none !important; }
    h1, h2, h3, h4, p, span { color: """ + title_col + """ !important; }
    .risk-bar-container { width: 100%; background-color: #333; border-radius: 8px; margin-top: 5px; margin-bottom: 15px; overflow: hidden; }
    .risk-bar-fill { height: 16px; border-radius: 8px; transition: width 0.5s ease-in-out; }
    [data-testid="stExpander"] { border-color: """ + border_col + """ !important; background-color: """ + bg_col + """ !important; border-radius: 8px !important; margin-bottom: 15px; }
</style>
"""
st.markdown(css_style, unsafe_allow_html=True)

STOCK_NAMES = { "2330": "台積電", "2317": "鴻海", "2454": "聯發科", "2308": "台達電", "2382": "廣達", "2376": "技嘉", "1802": "台玻", "2603": "長榮", "1785": "光洋科", "1519": "華城", "6147": "頎邦" }

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

st.sidebar.divider()
st.sidebar.title("⏱️ 盤中即時跳動雷達")
auto_refresh = st.sidebar.toggle("🟢 開啟即時自動更新 (每60秒)", False, key="auto_refresh_toggle")

if auto_refresh:
    # 稍微放寬更新頻率以保護 API 額度
    st_autorefresh(interval=60000, limit=None, key="market_auto_refresh")
    st.sidebar.success("⚡ 盤中高頻探測已啟動！")

def get_stock_name(ticker):
    if not ticker: return ""
    ticker_str = str(ticker).strip().upper().replace(".TW", "").replace(".TWO", "")
    return CURRENT_STOCK_NAMES.get(ticker_str, STOCK_NAMES.get(ticker_str, ticker_str))

FAV_FILE = "favorites.json" 
FAV_GROUPS_FILE = "fav_groups.json" 
POOL_FILE = "pool.json"

def load_json(fp, default):
    if os.path.exists(fp):
        try:
            with open(fp, "r", encoding="utf-8") as f: return json.load(f)
        except: pass
    return default

def save_json(fp, data):
    with open(fp, "w", encoding="utf-8") as f: json.dump(data, f)

if 'page' not in st.session_state: st.session_state.page = "home"
if 'current_stock' not in st.session_state: st.session_state.current_stock = "2376"
if 'custom_pool' not in st.session_state: st.session_state.custom_pool = load_json(POOL_FILE, ["2330", "2317", "2454", "2382", "3231"])
if 'scan_mode' not in st.session_state: st.session_state.scan_mode = "buy"
if 'view_days' not in st.session_state: st.session_state.view_days = 60
if 'date_offset' not in st.session_state: st.session_state.date_offset = 0

if 'fav_groups' not in st.session_state:
    st.session_state.fav_groups = load_json(FAV_GROUPS_FILE, {"預設群組": ["1802", "2330", "1785"]})

@st.cache_data(ttl=3600)
def fetch_twse_top_100():
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", timeout=5)
        df = pd.DataFrame(res.json())
        df['TradeVolume'] = pd.to_numeric(df['TradeVolume'], errors='coerce')
        return df[df['Code'].str.match(r'^\d{4}$')].sort_values(by='TradeVolume', ascending=False).head(100)['Code'].tolist()
    except: return ["2330", "2317", "2454", "2382", "3231"]

# ==========================================
# 🚀 升級模組 1: Fugle API 取代網頁爬蟲
# ==========================================
@st.cache_data(ttl=60, show_spinner=False) 
def get_stock_data(ticker_number):
    """結合 Fugle API 歷史 K 線與即時報價，並 fallback 到 yfinance"""
    base_ticker = str(ticker_number).strip().upper().replace(".TW", "").replace(".TWO", "")
    df = None
    
    # 大盤走 yfinance (富果指數代碼轉換較複雜，大盤用 yf 足矣)
    if base_ticker == "^TWII":
        try:
            df = yf.Ticker("^TWII").history(period="1y")[['Open', 'High', 'Low', 'Close', 'Volume']]
            if not df.empty: df.index = pd.to_datetime(df.index.strftime('%Y-%m-%d'))
        except: pass
    else:
        # 1. 嘗試使用 Fugle 歷史 K 線 API (精準無比)
        try:
            url_hist = f"https://api.fugle.tw/marketdata/v1.0/stock/historical/candles/{base_ticker}?timeframe=D"
            res = requests.get(url_hist, headers=FUGLE_HEADERS, timeout=5)
            if res.status_code == 200:
                data = res.json().get('data', [])
                if data:
                    df = pd.DataFrame(data)
                    df['date'] = pd.to_datetime(df['date'])
                    df.set_index('date', inplace=True)
                    df = df[['open', 'high', 'low', 'close', 'volume']].rename(columns={'open':'Open', 'high':'High', 'low':'Low', 'close':'Close', 'volume':'Volume'})
                    df = df.sort_index()
        except: pass

        # 2. 如果 Fugle 歷史抓失敗，使用 yfinance 備援
        if df is None or df.empty:
            try:
                df = yf.Ticker(f"{base_ticker}.TW").history(period="1y")
                if df.empty: df = yf.Ticker(f"{base_ticker}.TWO").history(period="1y")
                df.index = pd.to_datetime(df.index.strftime('%Y-%m-%d'))
            except: pass

        # 3. 疊加 Fugle 即時 Quote (解決 yfinance 盤中延遲或缺少當日 K 棒問題)
        if df is not None and not df.empty:
            try:
                url_quote = f"https://api.fugle.tw/marketdata/v1.0/stock/intraday/quote/{base_ticker}"
                res_quote = requests.get(url_quote, headers=FUGLE_HEADERS, timeout=3)
                if res_quote.status_code == 200:
                    q_data = res_quote.json()
                    dt_live = pd.to_datetime(datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d'))
                    
                    if 'closePrice' in q_data:
                        c_price = q_data.get('closePrice', df['Close'].iloc[-1])
                        o_price = q_data.get('openPrice', df['Open'].iloc[-1])
                        h_price = q_data.get('highPrice', df['High'].iloc[-1])
                        l_price = q_data.get('lowPrice', df['Low'].iloc[-1])
                        v_vol = q_data.get('totalVolume', 0)
                        
                        if dt_live not in df.index:
                            new_row = pd.DataFrame({'Open': [o_price], 'High': [h_price], 'Low': [l_price], 'Close': [c_price], 'Volume': [v_vol]}, index=[dt_live])
                            df = pd.concat([df, new_row])
                        else:
                            df.at[dt_live, 'Close'] = c_price
                            df.at[dt_live, 'High'] = max(df.at[dt_live, 'High'], h_price)
                            df.at[dt_live, 'Low'] = min(df.at[dt_live, 'Low'], l_price)
                            df.at[dt_live, 'Volume'] = max(df.at[dt_live, 'Volume'], v_vol)
            except: pass
            
    if df is None or df.empty: return None

    # === 技術指標運算 ===
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

    # === ATR 動態停損指標運算 ===
    df['TR'] = pd.concat([
        df['High'] - df['Low'],
        (df['High'] - df['Close'].shift(1)).abs(),
        (df['Low'] - df['Close'].shift(1)).abs()
    ], axis=1).max(axis=1)
    df['ATR'] = df['TR'].rolling(14).mean()

    return df

@st.cache_data(ttl=86400, show_spinner=False)
def get_fundamental_and_industry_data(ticker_number, current_price=0):
    """利用 yfinance 配合少量爬蟲，減少崩潰機率"""
    base_ticker = str(ticker_number).strip().upper().replace(".TW", "").replace(".TWO", "")
    eps_val, pe_val = "無", "無"
    ind = "一般產業"
    
    try:
        # yfinance 的 info API 獲取產業與 EPS (相對穩定)
        info = yf.Ticker(f"{base_ticker}.TW").info
        if not info or 'industry' not in info: info = yf.Ticker(f"{base_ticker}.TWO").info
        
        tw_ind = info.get("industry", "")
        if tw_ind: ind = tw_ind
        
        if 'trailingEps' in info and info['trailingEps'] is not None:
            eps_val = str(round(info['trailingEps'], 2))
    except: pass
    
    try:
        if eps_val != "無":
            eps_f = float(eps_val)
            if eps_f > 0 and current_price > 0: pe_val = str(round(float(current_price) / eps_f, 2))
            elif eps_f <= 0: pe_val = "無 (EPS ≦ 0)"
    except: pass
    
    return {"EPS": eps_val, "PE": pe_val, "Industry": ind}

@st.cache_data(ttl=15, show_spinner=False) 
def get_twii_quote():
    tz_tpe = timezone(timedelta(hours=8))
    update_time_str = datetime.now(tz_tpe).strftime('%Y/%m/%d %H:%M:%S')
    fallback_curr, fallback_change = 0, 0
    try:
        df = yf.Ticker("^TWII").history(period="5d").dropna(subset=['Close'])
        if not df.empty and len(df) >= 2:
            fallback_curr = float(df['Close'].iloc[-1])
            fallback_change = float(df['Close'].iloc[-1] - df['Close'].iloc[-2])
    except: pass
    return fallback_curr, fallback_change, update_time_str

@st.cache_data(ttl=3600, show_spinner=False)
def get_institutional_trading(ticker):
    """使用 FinMind 獲取法人籌碼"""
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

MACRO_CACHE_FILE = "macro_cache.json"

@st.cache_data(ttl=300, show_spinner=False)
def get_global_macro_data():
    tz_tpe = timezone(timedelta(hours=8))
    fetch_time = datetime.now(tz_tpe).strftime('%Y/%m/%d %H:%M:%S')
    data = {"global_time": fetch_time}
    tickers = {
        "^SOX": "https://finance.yahoo.com/quote/^SOX",
        "^VIX": "https://finance.yahoo.com/quote/^VIX",
        "JPY=X": "https://finance.yahoo.com/quote/JPY=X"
    }
    
    headers = {'User-Agent': 'Mozilla/5.0'}
    for t, url in tickers.items():
        try:
            df = yf.Ticker(t).history(period="5d")
            if df is not None and not df.empty and len(df) >= 2:
                c = float(df['Close'].iloc[-1])
                p = float(df['Close'].iloc[-2])
                last_dt = df.index[-1]
                data[t] = {"price": c, "pct": (c-p)/p*100 if p != 0 else 0, "time": last_dt.strftime('%Y/%m/%d'), "url": url}
            else:
                data[t] = {"price": 0, "pct": 0, "time": "暫無資料", "url": url}
        except:
             data[t] = {"price": 0, "pct": 0, "time": "錯誤", "url": url}
    return data

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
    
    if data['J值'] >= 80: sc-=3; rs.append("⚠️ KDJ高檔過熱")
    if data['收盤價'] >= data['BB_UP'] * 0.98: sc-=2; rs.append("⚠️ 觸及布林上軌壓力")
    if data['收盤價'] < data['20MA']: sc-=2; rs.append("⚠️ 跌破月線支撐")
    if eps_f < 0: sc-=1; rs.append("⚠️ 基本面虧損")

    return sc, rs

def analyze_today(df, ticker_number, inst_data=None, is_light_mode=False):
    if df is None or len(df) < 5: return None
    t, p, p5 = df.iloc[-1], df.iloc[-2], df.iloc[-5]
    fund = get_fundamental_and_industry_data(ticker_number, round(t['Close'], 2))
    
    t_open, t_close, t_high, t_low = float(t['Open']), float(t['Close']), float(t['High']), float(t['Low'])
    p_open, p_close = float(p['Open']), float(p['Close'])
    
    red_mask = (df['Open'].shift(1) > df['Close'].shift(1)) & (df['Close'] > df['Open']) & (df['Close'] > df['Open'].shift(1)) & (df['Open'] < df['Close'].shift(1))
    black_mask = (df['Close'].shift(1) > df['Open'].shift(1)) & (df['Open'] > df['Close']) & (df['Open'] > df['Close'].shift(1)) & (df['Close'] < df['Open'].shift(1))
    
    is_red_engulfing = bool(red_mask.iloc[-1])
    is_black_engulfing = bool(black_mask.iloc[-1])
    recent_7_red = bool(red_mask.tail(7).any())
    
    total_range = t_high - t_low if (t_high - t_low) != 0 else 0.001
    lower_shadow = min(t_open, t_close) - t_low
    body = abs(t_close - t_open)
    is_support_pullback = (lower_shadow > body * 1.5) and (lower_shadow / total_range > 0.4) and (t_low < p_close)
    
    data = {
        "代號": ticker_number, "名稱": get_stock_name(ticker_number), "ticker_raw": ticker_number,
        "產業": fund['Industry'], "昨日收盤價": round(p_close, 2), "收盤價": round(t_close, 2), 
        "漲跌": round(t_close - p_close, 2), "漲跌幅": round((t_close - p_close) / p_close * 100, 2), 
        "成交量": int(t['Volume']/1000), "5日均量": int(df['Volume'].tail(5).mean()/1000),
        "5MA": round(t['5MA'], 2), "10MA": round(t['10MA'], 2), "20MA": round(t['20MA'], 2),
        "BB_UP": round(t['BB_UP'], 2), "BB_DN": round(t['BB_DN'], 2), "BIAS": round(t['BIAS_20'], 2),
        "MACD柱": round(t['MACD_Hist'], 3), "前日MACD柱": round(p['MACD_Hist'], 3),
        "K": round(t['K'], 2), "D": round(t['D'], 2), "J值": round(t['J'], 2),
        "ATR": round(t.get('ATR', 0), 2),
        "訊號": (t_close > t['20MA']) and (t_close < t['5MA']) and (t['J'] < 20),
        "紅吞": is_red_engulfing, "黑吞": is_black_engulfing,
        "近七日紅吞": recent_7_red, "回測有撐": is_support_pullback
    }
    
    sc, rs = get_decision_score(data, fund, inst_data)
    data['Score'] = sc
    data['Reasons'] = rs
    data['評級'] = "🟢 S級" if sc >= 5 else ("🟡 A級" if sc >= 2 else "⚪ 觀望")
    
    return data

st.sidebar.title("⭐ 我的自選群組")
for g_name, g_stocks in list(st.session_state.fav_groups.items()):
    with st.sidebar.expander(f"📁 {g_name} ({len(g_stocks)})", expanded=True):
        for fav in g_stocks:
            if st.button(f"📊 {fav} {get_stock_name(fav)}", key=f"go_stock_{g_name}_{fav}", use_container_width=True):
                st.session_state.update({"current_stock": fav, "page": "analysis"})
                st.rerun()

@st.cache_data(ttl=180, show_spinner=False)
def get_global_scan_results(pool_tuple):
    scan_results = []
    def process_scan(stock):
        df = get_stock_data(stock)
        if df is not None: return analyze_today(df, stock)
        return None
        
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(process_scan, stock): stock for stock in pool_tuple}
        for future in concurrent.futures.as_completed(futures):
            try:
                res = future.result()
                if res: scan_results.append(res)
            except: pass
    return scan_results

# ==========================================
# 🚀 頁面路由控制中心
# ==========================================
if st.session_state.page == "home":
    st.markdown("<h1 style='text-align: center;'>🇹🇼 雷達總機 (富果核心)</h1>", unsafe_allow_html=True)
    
    st.markdown("<h3 style='margin-top: 15px;'>🎯 策略條件篩選</h3>", unsafe_allow_html=True)
    btn_col1, btn_col2, btn_col3 = st.columns(3)
    if btn_col1.button("✅ 綜合買點榜", use_container_width=True): st.session_state.scan_mode = "buy"; st.rerun()
    if btn_col2.button("🔥 紅吞反轉榜", use_container_width=True): st.session_state.scan_mode = "red_engulf"; st.rerun()
    if btn_col3.button("📊 近五日成交量", use_container_width=True): st.session_state.scan_mode = "recent"; st.rerun()
    
    top_100_pool = fetch_twse_top_100()
    pool = tuple(set(top_100_pool + st.session_state.custom_pool))
    
    with st.spinner("🚀 大腦背景資料庫存取中 (富果 API 極速抓取)..."):
        scan_results = get_global_scan_results(pool)
            
    if scan_results:
        df_results = pd.DataFrame(scan_results)
        
        if st.session_state.scan_mode == "recent":
            st.markdown("##### 📊 近五日成交量排行榜")
            df_disp = df_results.sort_values(by="成交量", ascending=False).head(20)
        elif st.session_state.scan_mode == "red_engulf":
            st.markdown("##### 🔥 近七日觸發「紅吞」反轉型態標的 (S、A級)")
            df_disp = df_results[(df_results['近七日紅吞'] == True) & (df_results['Score'] >= 2)].sort_values(by='Score', ascending=False).head(20)
        elif st.session_state.scan_mode == "buy":
            st.markdown("##### 🎯 尋找買點榜單 (高靈敏度動能捕捉榜)")
            df_disp = df_results[df_results['Score'] >= 2].sort_values(by='Score', ascending=False).head(20)
            
        for _, r in df_disp.iterrows():
            button_label = f"▪️ {r['代號']} {r['名稱']} | 現價:{r['收盤價']} | {r['評級']}"
            if st.button(button_label, key=f"btn_scan_{r['ticker_raw']}", use_container_width=True):
                st.session_state.update({"current_stock": r['ticker_raw'], "page": "analysis"})
                st.rerun()
                
elif st.session_state.page == "analysis":
    target = st.session_state.current_stock
    c_name = get_stock_name(target)

    if st.button("🏠 回雷達總機", use_container_width=True): st.session_state.page = "home"; st.rerun()

    df_chart = get_stock_data(target)
    
    if df_chart is not None and len(df_chart) >= 5:
        inst_data = get_institutional_trading(target)
        data = analyze_today(df_chart, target, inst_data, is_light_mode)
        f_data = get_fundamental_and_industry_data(target, data['收盤價'])
        
        st.markdown(f"<h2 style='text-align: center;'>🎯 {target} {c_name}</h2>", unsafe_allow_html=True)
        p_color = '#ff3333' if data['漲跌'] >= 0 else '#00cc00'
        st.markdown(f"<h3 style='text-align: center; color: {p_color};'>{data['收盤價']} ({'+' if data['漲跌']>0 else ''}{data['漲跌幅']}%)</h3>", unsafe_allow_html=True)
        st.markdown(f"<div style='text-align: center; color: #888;'>ATR 真實波動幅度: {data['ATR']}</div>", unsafe_allow_html=True)

        # ==========================================
        # 🚀 升級模組 2: ATR 動態停損防護
        # ==========================================
        stop_loss_html = ""
        recent_20 = df_chart.tail(20)
        recent_signals = []
        for idx in range(len(recent_20)):
            temp_df = df_chart.iloc[:len(df_chart) - 20 + idx + 1]
            if len(temp_df) >= 5:
                t_data = analyze_today(temp_df, target)
                if t_data and t_data['Score'] >= 2: 
                    recent_signals.append((temp_df.index[-1], t_data['收盤價'], t_data.get('ATR', 0)))
        
        if recent_signals:
            last_sig_date, last_buy_price, last_atr = recent_signals[-1]
            # 動態計算停損點 (買進價 - 1.5 * ATR)
            dynamic_stop_price = round(last_buy_price - (1.5 * last_atr), 2)
            
            if data['收盤價'] <= dynamic_stop_price:
                stop_loss_html = f'''<div style="background-color: #ffe6e6; border-left: 6px solid #ff3333; padding: 15px; margin-bottom: 20px; border-radius: 4px;">
                <h4 style="color: #ff3333; margin-top: 0;">🚨 【ATR 動態停損警報】觸發</h4>
                <span style="color: #333;">最近一次買訊 ({last_sig_date.strftime('%Y/%m/%d')}) 基準價為 <b>{last_buy_price:.2f}</b>。<br>
                依據該股真實波動幅度計算，動態防守底線為 <b>{dynamic_stop_price}</b>。<br>
                目前現價 <b>{data['收盤價']}</b> 已跌穿防護線！<b>強烈建議果斷停損出場觀望！</b></span></div>'''
            else:
                stop_loss_html = f'''<div style="background-color: #e6ffe6; border-left: 6px solid #00cc00; padding: 15px; margin-bottom: 20px; border-radius: 4px;">
                <h4 style="color: #00cc00; margin-top: 0;">🛡️ 【ATR 動態防護線】</h4>
                <span style="color: #333;">最近買訊基準價為 <b>{last_buy_price:.2f}</b>，目前的動態防守底線 (停損點) 為 <b>{dynamic_stop_price}</b>。<br>
                股價若未跌破此價位，皆屬正常波動洗盤，可持股續抱。</span></div>'''
                
        if stop_loss_html: st.markdown(stop_loss_html, unsafe_allow_html=True)
        
        # 繪製 K 線圖 (使用 Plotly 保留原樣，資料源已切換為 Fugle)
        df_view = df_chart.tail(st.session_state.view_days)
        fig = go.Figure(data=[go.Candlestick(x=df_view.index, open=df_view['Open'], high=df_view['High'], low=df_view['Low'], close=df_view['Close'], increasing_line_color='#ff3333', decreasing_line_color='#00cc00', name="K線")])
        fig.add_trace(go.Scatter(x=df_view.index, y=df_view['5MA'], line=dict(color='orange', width=1.5), name="5T"))
        fig.add_trace(go.Scatter(x=df_view.index, y=df_view['20MA'], line=dict(color='cyan', width=1.5), name="20T"))
        fig.update_layout(xaxis_rangeslider_visible=False, template="plotly_white" if is_light_mode else "plotly_dark", height=500, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(fig, use_container_width=True)

        st.subheader("⭐ 加入群組")
        all_groups = list(st.session_state.fav_groups.keys())
        current_groups = [g for g, s in st.session_state.fav_groups.items() if target in s]
        selected_groups = st.multiselect("群組清單：", options=all_groups, default=current_groups)
        if st.button("💾 儲存自選設定", use_container_width=True):
            for g in all_groups:
                if g in selected_groups and target not in st.session_state.fav_groups[g]: st.session_state.fav_groups[g].append(target)
                elif g not in selected_groups and target in st.session_state.fav_groups[g]: st.session_state.fav_groups[g].remove(target)
            save_json(FAV_GROUPS_FILE, st.session_state.fav_groups)
            st.success("✅ 更新成功！")
            st.rerun()


