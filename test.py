# 最後修改時間: 2026-06-28 20:00 CST
# 版本: PRO 完全體 (全 UI 整合 + Fugle API 升級 + ATR 動態停損 + 來源標示)
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
import re
import concurrent.futures
import random

from streamlit_autorefresh import st_autorefresh

# === 核心 API 金鑰設定 ===
# 籌碼來源: FinMind API
FINMIND_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjoiYTQ1Njg4MTUwQGdtYWlsLmNvbSIsImVtYWlsIjoiYTQ1Njg4MTUwQGdtYWlsLmNvbSIsInRva2VuX3ZlcnNpb24iOjB9.LUcb8YPV4yo93_aB3obP4Z5iUGqAgTaH28ySx9UNv5I"
# 報價與K線來源: Fugle API (富果) - 從你的截圖讀取並修正格式
FUGLE_API_KEY = "NWMxYjY4MzctM2VlNC00MjhhLTk5NjctOWQyYzBmMmJmZWU1IGFmNDk3NWRkLWY3NTMtNGZiYy04MTgyLTM3MTY4NDYyNTAwMw=="
FUGLE_HEADERS = {"X-API-KEY": FUGLE_API_KEY}

# ==========================================
# 0. 系統初始化與風格設定
# ==========================================
st.set_page_config(page_title="專業交易雷達 PRO", layout="wide", initial_sidebar_state="collapsed")

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
panel_bg = "#f9f9f9" if is_light_mode else "#16181f"

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
# 🚀 模組升級：Fugle 富果 API 資料獲取
# ==========================================
@st.cache_data(ttl=60, show_spinner=False) 
def get_stock_data(ticker_number):
    """結合 Fugle API 歷史 K 線與即時報價，並 fallback 到 yfinance"""
    base_ticker = str(ticker_number).strip().upper().replace(".TW", "").replace(".TWO", "")
    df = None
    
    # 大盤使用 yfinance
    if base_ticker == "^TWII":
        try:
            df = yf.Ticker("^TWII").history(period="1y")[['Open', 'High', 'Low', 'Close', 'Volume']]
            if not df.empty: df.index = pd.to_datetime(df.index.strftime('%Y-%m-%d'))
        except: pass
    else:
        # 1. Fugle 歷史 K 線 API
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

        # 2. Fugle 失敗則使用 yfinance 備援
        if df is None or df.empty:
            try:
                df = yf.Ticker(f"{base_ticker}.TW").history(period="1y")
                if df.empty: df = yf.Ticker(f"{base_ticker}.TWO").history(period="1y")
                df.index = pd.to_datetime(df.index.strftime('%Y-%m-%d'))
            except: pass

        # 3. 疊加 Fugle 即時 Quote
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
    """移除脆弱爬蟲，改用 yfinance info 獲取產業與 EPS"""
    base_ticker = str(ticker_number).strip().upper().replace(".TW", "").replace(".TWO", "")
    eps_val, pe_val = "無", "無"
    ind = "一般產業"
    
    ENG_TO_TW_INDUSTRY = {
        "Semiconductors": "半導體業", "Consumer Electronics": "消費性電子", "Electronic Components": "電子零組件",
        "Computer Hardware": "電腦及週邊設備", "Building Materials": "玻璃陶瓷", "Marine Shipping": "航運業",
        "Electrical Equipment & Parts": "電機機械", "Software - Entertainment": "文化創意業", "Technology": "電子科技",
        "Industrials": "工業", "Basic Materials": "原物料", "Financial Services": "金融業",
        "Consumer Cyclical": "非必需消費品", "Healthcare": "生技醫療", "Real Estate": "建材營造",
        "Utilities": "公用事業", "Energy": "能源", "Communication Services": "通信網路"
    }

    try:
        info = yf.Ticker(f"{base_ticker}.TW").info
        if not info or 'industry' not in info: info = yf.Ticker(f"{base_ticker}.TWO").info
        
        sec, ind_eng = info.get("sector", ""), info.get("industry", "")
        tw_sec = ENG_TO_TW_INDUSTRY.get(sec, sec)
        tw_ind = ENG_TO_TW_INDUSTRY.get(ind_eng, ind_eng)
        ind_temp = f"{tw_sec} - {tw_ind}" if tw_sec and tw_ind else tw_sec or tw_ind or "一般產業"
        if ind_temp: ind = ind_temp
        
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
    """使用 FinMind API 獲取三大法人籌碼"""
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
                data[t] = {"price": c, "pct": (c-p)/p*100 if p != 0 else 0, "time": last_dt.strftime('%Y/%m/%d %H:%M'), "url": url}
            else:
                data[t] = {"price": 0, "pct": 0, "time": "暫無資料", "url": url}
        except:
             data[t] = {"price": 0, "pct": 0, "time": "錯誤", "url": url}
    return data

def open_pred_logic(twii_df, twii_close, twii_change, twii_time_str=""):
    macro_data = get_global_macro_data()
    if twii_df is None or len(twii_df) < 2: 
        return "資料不足", "無法分析", "資料不足", "無法預測", "", "", 50, macro_data
    if twii_close > 0:
        t_close = twii_close
        p_close = twii_close - twii_change
        t_open = twii_df['Open'].iloc[-1]
    else:
        t_open, t_close, p_close = twii_df['Open'].iloc[-1], twii_df['Close'].iloc[-1], twii_df['Close'].iloc[-2]
    tz_tpe = timezone(timedelta(hours=8))
    
    last_dt = datetime.now(tz_tpe)
    last_dt_str = last_dt.strftime('%Y/%m/%d')
    next_dt = last_dt + timedelta(days=1)
    
    today_title, today_desc = "⚖️ 平盤震盪", "大盤開在平盤附近，法人現貨買賣超多空拉扯，盤勢陷入震盪整理。"
    if t_open > p_close * 1.003:
        if t_close > t_open: today_title, today_desc = "🔥 開高走高", "大盤受外資買盤激勵跳空開高，量能放大，盤勢極度偏多。"
        else: today_title, today_desc = "⚠️ 開高走低", "大盤跳空開高後遭遇短線獲利了結賣壓，呈現高檔回落。"
    elif t_open < p_close * 0.997:
        if t_close > t_open: today_title, today_desc = "💪 開低走高", "大盤開低但低檔承接買盤強勁，出現開低走高收紅K型態。"
        else: today_title, today_desc = "🩸 開低走低", "大盤弱勢開低，引發停損賣壓，盤勢極度偏空。"

    risk_score = 50 
    ma5 = twii_df['5MA'].iloc[-1] if '5MA' in twii_df.columns else twii_df['Close'].tail(5).mean()
    if t_close < ma5: risk_score += 15
    else: risk_score -= 10
    sox_data = macro_data.get('^SOX', {"price": 0, "pct": 0})
    sox_pct = sox_data.get('pct', 0)
    if sox_pct < -2.0: risk_score += 20
    elif sox_pct < -0.5: risk_score += 10
    elif sox_pct > 1.5: risk_score -= 15
    vix_data = macro_data.get('^VIX', {"price": 0, "pct": 0})
    vix_curr = vix_data.get('price', 0)
    vix_pct = vix_data.get('pct', 0)
    if vix_curr > 20 or vix_pct > 10.0: risk_score += 20
    elif vix_pct < -5.0: risk_score -= 10
    jpy_data = macro_data.get('JPY=X', {"price": 0, "pct": 0})
    jpy_pct = jpy_data.get('pct', 0)
    if jpy_pct < -0.8: risk_score += 15 
    elif jpy_pct > 0.5: risk_score -= 5
    
    risk_score = max(5, min(95, int(risk_score))) 
    if risk_score < 40: tmr_title, tmr_desc = "🚀 安全偏多", f"總經環境穩定，預估次一交易日有極高機率開平高盤挑戰上檔壓力。"
    elif risk_score < 70: tmr_title, tmr_desc = "⚠️ 偏空震盪", f"國際變數增加或台股跌破關鍵短均線，預防開平低盤回測下檔支撐。"
    else: tmr_title, tmr_desc = "🚨 極度警戒", f"全球宏觀風險飆高，強烈建議減碼防範跳空重挫的系統性風險。"
    
    return today_title, today_desc, tmr_title, tmr_desc, last_dt_str, next_dt.strftime('%Y/%m/%d'), risk_score, macro_data

def get_decision_score(data, fund_data, inst_data=None):
    sc, rs = 0, []
    if data['訊號']: sc+=3; rs.append("✅ 穩在月線上且KDJ超賣")
    if data['收盤價'] <= data['BB_DN'] * 1.02: sc+=2; rs.append("✅ 觸及布林下軌支撐")
    if data['BIAS'] < -5: sc+=1; rs.append("✅ 負乖離過大")
    
    try: eps_f = float(str(fund_data['EPS']).replace(',', ''))
    except: eps_f = 0.0
    if eps_f > 0: sc+=2; rs.append("✅ 基本面獲利")
    
    if data.get('成交量', 0) > data.get('5日均量', 0) * 1.1: sc+=2; rs.append("✅ 量能放大 (點火特徵)")
    else: sc-=1; rs.append("⚠️ 量能未明顯放大")
        
    if data.get('MACD柱', 0) > data.get('前日MACD柱', -999): sc+=2; rs.append("✅ MACD 綠柱收斂或紅柱發散")
    else: sc-=3; rs.append("⚠️ MACD 空方動能持續擴大")

    if inst_data and len(inst_data) >= 3:
        net_buy = sum([int(str(x['單日合計(張)']).replace(',', '')) for x in inst_data[:3] if str(x['單日合計(張)']).replace(',', '').lstrip('-').isdigit()])
        if net_buy > 0: rs.append(f"✅ 法人近三日偏多")
        else: rs.append(f"⚠️ 法人近三日偏空")

    if data.get('紅吞'): sc+=3; rs.append("🔥 出現「紅吞」反轉型態")
    if data.get('黑吞'): sc-=3; rs.append("🩸 出現「黑吞」反轉型態")
    if data.get('回測有撐'): sc+=2; rs.append("🔥 回測支撐成功")
    
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
    upper_shadow = t_high - max(t_open, t_close)
    lower_shadow = min(t_open, t_close) - t_low
    body = abs(t_close - t_open)

    is_support_pullback = (lower_shadow > body * 1.5) and (lower_shadow / total_range > 0.4) and (t_low < p_close)
    ma_resistance = min(t['5MA'], t['10MA']) 
    is_resistance_rejection = (upper_shadow > body * 1.5) and (upper_shadow / total_range > 0.4) and (t_high >= ma_resistance) and (t_close < ma_resistance)

    try:
        ma5_deduction_tmr = float(df['Close'].iloc[-5]) if len(df) >= 5 else float(t_close)
        is_ma5_turning_up = t_close > ma5_deduction_tmr
    except:
        is_ma5_turning_up = False

    data = {
        "代號": ticker_number, "名稱": get_stock_name(ticker_number), "ticker_raw": ticker_number,
        "產業": fund['Industry'], "昨日收盤價": round(p_close, 2), "收盤價": round(t_close, 2), 
        "漲跌": round(t_close - p_close, 2), "漲跌幅": round((t_close - p_close) / p_close * 100, 2), 
        "成交量": int(t['Volume']/1000), "5日均量": int(df['Volume'].tail(5).mean()/1000),
        "5MA": round(t['5MA'], 2), "10MA": round(t['10MA'], 2), "20MA": round(t['20MA'], 2),
        "BB_UP": round(t['BB_UP'], 2), "BB_DN": round(t['BB_DN'], 2), "BIAS": round(t['BIAS_20'], 2),
        "MACD": round(t['MACD'], 2), "Signal": round(t['Signal'], 2),
        "MACD柱": round(t['MACD_Hist'], 3), "前日MACD柱": round(p['MACD_Hist'], 3),
        "K": round(t['K'], 2), "D": round(t['D'], 2), "J值": round(t['J'], 2),
        "ATR": round(t.get('ATR', 0), 2),
        "訊號": (t_close > t['20MA']) and (t_close < t['5MA']) and (t['J'] < 20),
        "紅吞": is_red_engulfing, "黑吞": is_black_engulfing,
        "近七日紅吞": recent_7_red, "回測有撐": is_support_pullback, "反彈遇壓": is_resistance_rejection,
        "5日線即將上彎": is_ma5_turning_up
    }
    
    sc, rs = get_decision_score(data, fund, inst_data)
    data['Score'] = sc
    data['Reasons'] = rs
    data['評級'] = "🟢 S級" if sc >= 5 else ("🟡 A級" if sc >= 2 else "⚪ 觀望")
    
    return data

def generate_comprehensive_analysis(data, inst_data, sc, f_data, market_today="", market_tmr="", is_light_mode=False):
    t_text_c = "#333" if is_light_mode else "#ddd"
    card_bg = "#f4f6f9" if is_light_mode else "#16181f"
    sum_bg = "rgba(0,0,0,0.05)" if is_light_mode else "rgba(255,255,255,0.05)"
    b_col = "#ddd" if is_light_mode else "#333"

    tech_bullets = []
    if data.get('紅吞'): tech_bullets.append(f"🔥 <span style='color:#ff3333; font-weight:bold;'>型態反轉：今日出現「紅吞」K線型態，強烈見底買進訊號。</span>")
    elif data.get('近七日紅吞'): tech_bullets.append(f"🔥 <span style='color:#ff3333; font-weight:bold;'>底部表態：近七日內曾出現「紅吞」型態，多方主力已在此區間建倉表態。</span>")
    
    if data['J值'] < 20: tech_bullets.append(f"🔥 <span style='color:#ff3333; font-weight:bold;'>KDJ 極度超賣：J 值來到 ({data['J值']})，隨時醞釀強力技術性反彈。</span>")
    if data['BIAS'] < -5: tech_bullets.append(f"🔥 <span style='color:#ff3333; font-weight:bold;'>負乖離過大：月線乖離率達 ({data['BIAS']}%)，超跌反彈機率極高。</span>")
    if data['MACD柱'] > data['前日MACD柱']: tech_bullets.append(f"🔥 <span style='color:#ff3333; font-weight:bold;'>動能指標護航：MACD 綠柱開始收斂或紅柱發散，下跌動能衰退。</span>")

    tech_res = "🔥 股價走勢強勁，目前屬於多頭格局，量價配合得不錯。" if sc >= 2 else "⚠️ 股價表現偏弱或震盪整理。"
    
    tech_html = f"<div style='border: 1px solid {b_col}; border-radius: 8px; padding: 15px; margin-bottom: 15px; background-color: {card_bg};'>"
    tech_html += f"<h4 style='color: #00ccff; margin-top: 0; font-size: 1.2rem; display: flex; align-items: center;'>📈 技術面分析 <span style='font-size:0.8rem; color:#888; margin-left: 10px;'>[資料來源: Fugle富果 API]</span></h4>"
    tech_html += f"<ul style='font-size: 0.95rem; line-height: 1.6; margin-bottom: 15px; color: {t_text_c};'>"
    for b in tech_bullets: tech_html += f"<li style='margin-bottom:6px;'>{b}</li>"
    tech_html += f"</ul>"
    tech_html += f"<div style='background-color: {sum_bg}; padding: 12px; border-radius: 6px; border-left: 4px solid #00ccff; font-size: 0.95rem; color: {t_text_c}; line-height: 1.6;'><b>【結　　果】</b>{tech_res}</div></div>"

    tables_html = ""
    chip_res_text = "中立觀望"
    if inst_data and len(inst_data) >= 3:
        foreign_net = sum([int(str(x['外坐在']).replace(',', '')) for x in inst_data[:3] if '外坐在' in x]) # Typo fallback if needed
        foreign_net = sum([int(str(x['外資(張)']).replace(',', '')) for x in inst_data[:3] if str(x['外資(張)']).replace(',', '').lstrip('-').isdigit()])
        trust_net = sum([int(str(x['投信(張)']).replace(',', '')) for x in inst_data[:3] if str(x['投信(張)']).replace(',', '').lstrip('-').isdigit()])
        if foreign_net > 0 and trust_net > 0: chip_res_text = "🔥 外資跟投信都在買，籌碼正集中。"
        elif foreign_net < 0 and trust_net < 0: chip_res_text = "⚠️ 外資跟投信同步倒貨，籌碼鬆動。"
        else: chip_res_text = "⚖️ 法人多空步調不一，籌碼處於換手震盪。"

        th_color = "#ccc" if not is_light_mode else "#555"
        def get_c(val): return "#ff3333" if val > 0 else ("#00cc00" if val < 0 else t_text_c)
        
        tables_html += f"<div style='overflow-x: auto; margin-top: 15px; width: 100%;'>"
        tables_html += f"<table style='width: 100%; text-align: center; border-collapse: collapse; font-size: 0.9rem; border: 1px solid {b_col}; color: {t_text_c};'>"
        tables_html += f"<tr style='background-color: {sum_bg}; color: {th_color};'>"
        tables_html += f"<th style='border: 1px solid {b_col}; padding: 8px 4px;'>日期</th><th style='border: 1px solid {b_col}; padding: 8px 4px;'>外資</th><th style='border: 1px solid {b_col}; padding: 8px 4px;'>投信</th><th style='border: 1px solid {b_col}; padding: 8px 4px;'>自營商</th><th style='border: 1px solid {b_col}; padding: 8px 4px;'>合計</th></tr>"
        for row in inst_data[:5]:
            f_val, t_val, d_val, s_val = int(row['外資(張)']), int(row['投信(張)']), int(row['自營商(張)']), int(row['單日合計(張)'])
            tables_html += f"<tr><td style='border: 1px solid {b_col}; padding: 8px 4px;'>{row['日期']}</td>"
            tables_html += f"<td style='border: 1px solid {b_col}; padding: 8px 4px; color: {get_c(f_val)};'>{f_val}</td>"
            tables_html += f"<td style='border: 1px solid {b_col}; padding: 8px 4px; color: {get_c(t_val)};'>{t_val}</td>"
            tables_html += f"<td style='border: 1px solid {b_col}; padding: 8px 4px; color: {get_c(d_val)};'>{d_val}</td>"
            tables_html += f"<td style='border: 1px solid {b_col}; padding: 8px 4px; color: {get_c(s_val)};'>{s_val}</td></tr>"
        tables_html += f"</table></div>"
    else:
        tables_html = f"<div style='color: {sub_text_col}; font-size: 0.9rem; padding: 10px;'>目前暫無籌碼資料。</div>"

    chip_html = f"<div style='border: 1px solid {b_col}; border-radius: 8px; padding: 15px; margin-bottom: 15px; background-color: {card_bg};'>"
    chip_html += f"<h4 style='color: #ffcc00; margin-top: 0; font-size: 1.2rem; display: flex; align-items: center;'>🏦 籌碼面分析 <span style='font-size:0.8rem; color:#888; margin-left: 10px;'>[資料來源: FinMind API]</span></h4>"
    chip_html += f"{tables_html}"
    chip_html += f"<div style='background-color: {sum_bg}; padding: 12px; border-radius: 6px; border-left: 4px solid #ffcc00; font-size: 0.95rem; color: {t_text_c}; line-height: 1.6; margin-top: 15px;'><b>【結　　果】</b>{chip_res_text}</div></div>"

    fund_bullets = []
    eps, pe, ind = f_data.get('EPS', '無'), f_data.get('PE', '無'), f_data.get('Industry', '一般產業')
    fund_bullets.append(f"⚪ <b>產業趨勢</b>：隸屬【{ind}】板塊。")
    fund_bullets.append(f"⚪ <b>EPS</b>：當季每股盈餘 (EPS) <b>{eps}</b> 元。")
    fund_bullets.append(f"⚪ <b>本益比 (PE)</b>：最新估值為 <b>{pe}</b> 倍。")
    fund_res = "🔥 具備實質獲利支撐" if (eps != "無" and float(eps) > 0) else "⚪ 基本面暫無明顯支撐"
    
    fund_html = f"<div style='border: 1px solid {b_col}; border-radius: 8px; padding: 15px; margin-bottom: 15px; background-color: {card_bg};'>"
    fund_html += f"<h4 style='color: #ff99ff; margin-top: 0; font-size: 1.2rem; display: flex; align-items: center;'>📑 基本面分析 <span style='font-size:0.8rem; color:#888; margin-left: 10px;'>[資料來源: Yahoo Finance]</span></h4>"
    fund_html += f"<ul style='font-size: 0.95rem; line-height: 1.6; margin-bottom: 15px; color: {t_text_c};'>"
    for b in fund_bullets: fund_html += f"<li style='margin-bottom:6px;'>{b}</li>"
    fund_html += f"</ul><div style='background-color: {sum_bg}; padding: 12px; border-radius: 6px; border-left: 4px solid #ff99ff; font-size: 0.95rem; color: {t_text_c}; line-height: 1.6;'><b>【結　　果】</b>{fund_res}</div></div>"

    current_p = data['收盤價']
    lower_bound = data['5MA'] if current_p > data['5MA'] else (data['20MA'] if current_p > data['20MA'] else data['BB_DN'])
    range_min = min(current_p, lower_bound)
    if sc >= 5: 
        v_t, v_c = "🟢 S級買點：強烈建議佈局", "#00cc00"
        v_a = f"✅ <b>進場判斷：強烈買進</b><br>建議建倉區間：現價 ({current_p:.2f}) ~ 逢低 {range_min:.2f} 之間分批加碼。"
    elif sc >= 2: 
        v_t, v_c = "🟡 A級機會：偏多試單", "#ffcc00"
        v_a = f"✅ <b>進場判斷：分批試單</b><br>建議建倉區間：現價 ({current_p:.2f}) ~ 逢低 {range_min:.2f} 之間佈局。"
    elif sc >= -1: 
        v_t, v_c = "⚪ 中性觀望：多空不明", "#888888"
        v_a = f"⏳ <b>進場判斷：暫緩進場</b><br>多空拉扯劇烈，建議靜待訊號明朗化。"
    else: 
        v_t, v_c = "🔴 極度危險：嚴禁做多", "#ff3333"
        v_a = f"⛔ <b>進場判斷：絕對空手</b><br>量能、動能完全走空。強烈建議空手觀望。"
        
    full_html = tech_html + chip_html + fund_html
    return full_html, v_t, v_c, v_a

def draw_professional_chart(df, ticker_name, latest_price, view_days, is_light_mode, show_buy_signal=False, f_data=None, show_sup_res=False, show_signals=True):
    df_view = df.tail(view_days)
    colors = ['#ff3333' if row['Close'] >= row['Open'] else '#00cc00' for _, row in df_view.iterrows()]
    last_row = df_view.iloc[-1]
    x_vals = df_view.index.strftime('%Y-%m-%d')
    fig = make_subplots(rows=4, cols=1, shared_xaxes=True, row_heights=[0.45, 0.15, 0.15, 0.25], vertical_spacing=0.06)
    line_k, line_d, line_j = ("#0066cc", "#ff9900", "#9900cc") if is_light_mode else ("white", "yellow", "magenta")
    grid_c = "rgba(0,0,0,0.1)" if is_light_mode else "rgba(255,255,255,0.1)"
    bg_c = "#ffffff" if is_light_mode else "#0e1117"
    text_c = "#333" if is_light_mode else "#ccc"
    
    fig.add_trace(go.Candlestick(x=x_vals, open=df_view['Open'], high=df_view['High'], low=df_view['Low'], close=df_view['Close'], increasing_line_color='#ff3333', decreasing_line_color='#00cc00', name="K線"), row=1, col=1)
    
    fig.add_trace(go.Scatter(x=x_vals, y=df_view['5MA'], line=dict(color='orange', width=2), name="5T"), row=1, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df_view['10MA'], line=dict(color='#ffcc00', width=2), name="10T"), row=1, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df_view['20MA'], line=dict(color='cyan', width=2), name="20T"), row=1, col=1)
    
    fig.add_hline(y=latest_price, line_dash="dash", line_color="#ffcc00", row=1, col=1)
    
    buy_x, buy_y, buy_text = [], [], []
    if show_buy_signal and f_data:
        for i in range(len(df_view)):
            current_date = df_view.index[i]
            pos = df.index.get_loc(current_date)
            sub_df = df.iloc[:pos+1]
            if len(sub_df) >= 5:
                t_data = analyze_today(sub_df, ticker_name, inst_data=None, is_light_mode=is_light_mode) 
                if t_data and t_data['Score'] >= 2:
                    buy_x.append(current_date.strftime('%Y-%m-%d'))
                    buy_y.append(df_view['Low'].iloc[i] * 0.90) 
                    buy_text.append("買")
        if buy_x:
            fig.add_trace(go.Scatter(x=buy_x, y=buy_y, mode='markers+text', marker=dict(symbol='triangle-up', size=14, color='#00ffcc' if not is_light_mode else '#0066cc'), text=buy_text, textposition="bottom center", textfont=dict(color="#00ffcc" if not is_light_mode else '#0066cc', size=11, weight="bold"), name="買進訊號", hoverinfo='skip'), row=1, col=1)
            
    fig.add_trace(go.Bar(x=x_vals, y=df_view['Volume'], marker_color=colors, name="VOL"), row=2, col=1)
    macd_colors = ['#ff3333' if val > 0 else '#00cc00' for val in df_view['MACD_Hist']]
    fig.add_trace(go.Bar(x=x_vals, y=df_view['MACD_Hist'], marker_color=macd_colors, name="OSC"), row=3, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df_view['MACD'], line=dict(color=line_k, width=1.5), name="DIF"), row=3, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df_view['Signal'], line=dict(color=line_d, width=1.5), name="MACD"), row=3, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df_view['K'], line=dict(color=line_k, width=1.5), name="K"), row=4, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df_view['D'], line=dict(color=line_d, width=1.5), name="D"), row=4, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df_view['J'], line=dict(color=line_j, width=1.5), name="J"), row=4, col=1)

    ann_bg = "rgba(255,255,255,0.8)" if is_light_mode else "rgba(26,28,36,0.6)"
    fig.add_annotation(x=0.01, y=0.98, xref="paper", yref="y domain", text=f"5T:{last_row['5MA']:.1f} | 10T:{last_row['10MA']:.1f} | 20T:{last_row['20MA']:.1f}", showarrow=False, font=dict(color="#ff9900" if is_light_mode else "#ffcc00", size=12), xanchor="left", bgcolor=ann_bg)
    
    fig.update_xaxes(type='category', nticks=15, fixedrange=True, showgrid=True, gridcolor=grid_c)
    fig.update_yaxes(fixedrange=True, showgrid=True, gridcolor=grid_c)
    fig.update_layout(xaxis_rangeslider_visible=False, template="plotly_white" if is_light_mode else "plotly_dark", height=850, margin=dict(l=10, r=10, t=10, b=30), paper_bgcolor=bg_c, plot_bgcolor=bg_c, dragmode=False, showlegend=False)
    
    # === 更新資料來源標示 ===
    fig.add_annotation(text="📊 資料來源: Fugle富果 API / FinMind / Yahoo Finance", xref="paper", yref="paper", x=1.0, y=-0.05, showarrow=False, font=dict(size=12, color=text_c))
    return fig

def render_index_board():
    try:
        twii_close, twii_change, twii_time_str = get_twii_quote()
        twii_color = '#ff3333' if twii_change >= 0 else '#00cc00'
        
        twii_df_for_pred = None
        try: twii_df_for_pred = yf.Ticker("^TWII").history(period="1y")[['Open', 'High', 'Low', 'Close', 'Volume']]
        except: pass
        if twii_df_for_pred is not None and not twii_df_for_pred.empty:
            twii_df_for_pred['5MA'] = twii_df_for_pred['Close'].rolling(5).mean()
            
        today_title, today_desc, tmr_title, tmr_desc, last_dt_str, next_dt_str, risk_score, macro = open_pred_logic(twii_df_for_pred, twii_close, twii_change, twii_time_str)
        
        with st.container(border=True):
            col1, col3 = st.columns([1, 1.5])
            with col1:
                st.markdown(f"<div style='text-align: center; font-size: 1.1rem; font-weight: bold;'>台灣加權指數 🔗</div>", unsafe_allow_html=True)
                st.markdown(f"<div style='text-align: center; font-size: 2.1rem; font-weight: 900; color: {twii_color}; margin: 0;'>{twii_close:,.0f}</div>", unsafe_allow_html=True)
                st.markdown(f"<div style='text-align: center; font-size: 1.1rem; font-weight: bold; color: {twii_color};'>{'↑' if twii_change > 0 else '↓'} {abs(twii_change):.0f}</div>", unsafe_allow_html=True)
                st.markdown(f"<div style='text-align: center; font-size: 0.85rem; color: #888;'>🕒 抓取時間: {twii_time_str}<br>⚡ 來源: Yahoo Finance</div>", unsafe_allow_html=True)
            with col3:
                st.markdown(f"<div style='text-align: left; color: #ffcc00; font-size: 1.05rem; font-weight: bold;'>📝 盤勢分析 ({last_dt_str})</div>", unsafe_allow_html=True)
                st.markdown(f"<div style='text-align: left; font-size: 1.1rem; font-weight: bold;'>{today_title}</div>", unsafe_allow_html=True)
                st.markdown(f"<div style='text-align: left; font-size: 0.85rem; margin-top: 2px; margin-bottom: 8px; line-height: 1.4;'>{today_desc}</div>", unsafe_allow_html=True)
                st.markdown(f"<div style='text-align: left; color: #00ffcc; font-size: 1.05rem; font-weight: bold;'>🔮 次日開盤預測 ({next_dt_str})</div>", unsafe_allow_html=True)
                st.markdown(f"<div style='text-align: left; font-size: 1.1rem; font-weight: bold;'>{tmr_title}</div>", unsafe_allow_html=True)
                st.markdown(f"<div style='text-align: left; font-size: 0.85rem; margin-top: 2px; line-height: 1.4;'>{tmr_desc}</div>", unsafe_allow_html=True)
        
        st.markdown("<h4 style='margin-top:20px; text-align:center;'>🌍 全球總經與次日開盤風險評估</h4>", unsafe_allow_html=True)
        
        bar_color = "#00cc00" if risk_score < 40 else ("#ffcc00" if risk_score < 70 else "#ff3333")
        risk_label = "🟢 資金充沛，安心佈局" if risk_score < 40 else ("🟡 變數增加，控制倉位" if risk_score < 70 else "🔴 系統風險，嚴格減碼")
        st.markdown(f"<div style='text-align:center; font-size:1.1rem; font-weight:bold;'>系統量化開低風險度：<span style='color:{bar_color};'>{risk_score}%</span></div>", unsafe_allow_html=True)
        st.markdown(f"""
        <div class="risk-bar-container">
            <div class="risk-bar-fill" style="width: {risk_score}%; background-color: {bar_color};"></div>
        </div>
        <div style='text-align:center; font-size:0.9rem; color:{bar_color}; font-weight:bold; margin-bottom:15px;'>{risk_label}</div>
        """, unsafe_allow_html=True)
        
        mc1, mc2, mc3 = st.columns(3)
        sox_data = macro.get('^SOX', {"price": 0, "pct": 0, "time": "無", "url": "#"})
        vix_data = macro.get('^VIX', {"price": 0, "pct": 0, "time": "無", "url": "#"})
        jpy_data = macro.get('JPY=X', {"price": 0, "pct": 0, "time": "無", "url": "#"})
        
        with mc1.container(border=True):
            st.markdown(f"<div style='text-align:center; font-size:0.85rem;'>費城半導體</div><div style='text-align:center; font-size:1.1rem; font-weight:bold; color:{'#ff3333' if sox_data['pct']>=0 else '#00cc00'};'>{sox_data['price']:,.1f}<br>{'+' if sox_data['pct']>0 else ''}{sox_data['pct']:.2f}%</div><div style='text-align:center; font-size:0.75rem; color:#888; margin-top:5px;'>🕒 {sox_data['time']}<br>🔗 來源: Yahoo Finance</div>", unsafe_allow_html=True)
        with mc2.container(border=True):
            st.markdown(f"<div style='text-align:center; font-size:0.85rem;'>VIX 恐慌指數</div><div style='text-align:center; font-size:1.1rem; font-weight:bold; color:{'#00cc00' if vix_data['pct']<=0 else '#ff3333'};'>{vix_data['price']:,.2f}<br>{'+' if vix_data['pct']>0 else ''}{vix_data['pct']:.2f}%</div><div style='text-align:center; font-size:0.75rem; color:#888; margin-top:5px;'>🕒 {vix_data['time']}<br>🔗 來源: Yahoo Finance</div>", unsafe_allow_html=True)
        with mc3.container(border=True):
            st.markdown(f"<div style='text-align:center; font-size:0.85rem;'>日圓動向(USD/JPY)</div><div style='text-align:center; font-size:1.1rem; font-weight:bold; color:#ffcc00;'>{jpy_data['price']:,.2f}<br>{'央行趨緩' if jpy_data['pct']>0 else '升息撤資警戒'}</div><div style='text-align:center; font-size:0.75rem; color:#888; margin-top:5px;'>🕒 {jpy_data['time']}<br>🔗 來源: Yahoo Finance</div>", unsafe_allow_html=True)
    except Exception as e: 
        st.error(f"大盤儀表板渲染發生錯誤，請稍後重試。({str(e)})")

st.sidebar.title("⭐ 我的自選群組")
for g_name, g_stocks in list(st.session_state.fav_groups.items()):
    with st.sidebar.expander(f"📁 {g_name} ({len(g_stocks)})", expanded=True):
        for fav in g_stocks:
            if st.button(f"📊 {fav} {get_stock_name(fav)}", key=f"go_stock_{g_name}_{fav}", use_container_width=True):
                st.session_state.update({"current_stock": fav, "page": "analysis", "date_offset": 0})
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
    st.markdown("<h1 style='text-align: center;'>🇹🇼 雷達總機 (Fugle 富果 API 核心)</h1>", unsafe_allow_html=True)
    
    # 🌟 恢復首頁的大盤儀表板
    render_index_board()
    
    st.markdown("<h3 style='margin-top: 15px;'>🎯 策略條件篩選</h3>", unsafe_allow_html=True)
    btn_col1, btn_col2, btn_col3 = st.columns(3)
    if btn_col1.button("✅ 綜合買點榜", use_container_width=True): st.session_state.scan_mode = "buy"; st.rerun()
    if btn_col2.button("🔥 紅吞反轉榜", use_container_width=True): st.session_state.scan_mode = "red_engulf"; st.rerun()
    if btn_col3.button("📊 近五日成交量", use_container_width=True): st.session_state.scan_mode = "recent"; st.rerun()
    
    top_100_pool = fetch_twse_top_100()
    pool = tuple(set(top_100_pool + st.session_state.custom_pool))
    
    with st.spinner("🚀 大腦背景資料庫存取中 (Fugle 富果 API 極速抓取)..."):
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
                st.session_state.update({"current_stock": r['ticker_raw'], "page": "analysis", "date_offset": 0})
                st.rerun()
                
elif st.session_state.page == "analysis":
    target = st.session_state.current_stock
    c_name = get_stock_name(target)

    c1, c2, c3 = st.columns([1, 1, 1])
    with c2:
        if st.button("🏠 回雷達總機", use_container_width=True): st.session_state.page = "home"; st.rerun()

    with st.spinner(f"🚀 正在喚醒【{target} {c_name}】AI 分析大腦... (資料來源: Fugle富果)"):
        df_chart = get_stock_data(target)
        
    if df_chart is not None and len(df_chart) >= 5:
        inst_data = get_institutional_trading(target)
        data = analyze_today(df_chart, target, inst_data, is_light_mode)
        f_data = get_fundamental_and_industry_data(target, data['收盤價'])
        
        st.markdown(f"<h2 style='text-align: center; margin-bottom: 5px;'>🎯 {target} {c_name}</h2>", unsafe_allow_html=True)
        st.markdown(f"<div style='text-align: center; color: #888; font-size: 1.1rem;'>【{f_data['Industry']}】 | ⚡ 報價來源: Fugle 富果</div>", unsafe_allow_html=True)
        p_color = '#ff3333' if data['漲跌'] >= 0 else '#00cc00'
        st.markdown(f"<h3 style='text-align: center; color: {p_color}; font-size: 2.2rem;'>{data['收盤價']} ({'+' if data['漲跌']>0 else ''}{data['漲跌幅']}%)</h3>", unsafe_allow_html=True)

        st.markdown("---")

        # ==========================================
        # 🚀 ATR 動態停損防護 UI
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
            dynamic_stop_price = round(last_buy_price - (1.5 * last_atr), 2)
            
            if data['收盤價'] <= dynamic_stop_price:
                stop_loss_html = f'''<div style="background-color: #ffe6e6; border-left: 6px solid #ff3333; padding: 15px; margin-bottom: 20px; border-radius: 4px;">
                <h4 style="color: #ff3333; margin-top: 0;">🚨 【ATR 動態停損警報】觸發跌破防護線</h4>
                <span style="color: #333;">系統偵測到最近一次買訊 ({last_sig_date.strftime('%Y/%m/%d')}) 基準價為 <b>{last_buy_price:.2f}</b>。<br>
                依據該股真實波動幅度計算，動態防守底線為 <b>{dynamic_stop_price}</b>。<br>
                目前現價 <b>{data['收盤價']}</b> 已跌穿防護線！<b>防範警訊：中線趨勢支撐已破，強烈建議果斷停損出場觀望！</b></span></div>'''
            else:
                stop_loss_html = f'''<div style="background-color: #e6ffe6; border-left: 6px solid #00cc00; padding: 15px; margin-bottom: 20px; border-radius: 4px;">
                <h4 style="color: #00cc00; margin-top: 0;">🛡️ 【ATR 動態停損防護中】</h4>
                <span style="color: #333;">最近一次買訊基準價為 <b>{last_buy_price:.2f}</b>，目前的動態防守底線 (停損點) 為 <b>{dynamic_stop_price}</b>。<br>
                只要股價未跌破此防守價位，皆屬正常波動洗盤，建議可持股續抱。</span></div>'''
                
        if stop_loss_html: st.markdown(stop_loss_html, unsafe_allow_html=True)
        
        # 🌟 恢復完整 AI 決策大腦 HTML 面板
        ai_brain_html, v_t, v_c, v_a = generate_comprehensive_analysis(data, inst_data, data['Score'], f_data, "", "", is_light_mode)
        st.markdown(f"""<div style="border: 2px solid {v_c}; border-radius: 10px; padding: 20px; margin-bottom: 20px; background-color: {bg_col};">
        <h3 style="text-align: center; color: {v_c}; margin-top: 0; font-size: 1.8rem; margin-bottom: 20px;">🤖 AI 決策大腦：{v_t.replace('🟢 ', '').replace('🟡 ', '').replace('⚪ ', '').replace('🔴 ', '')}</h3>
        {ai_brain_html}
        <hr style="border-color: {border_col}; margin: 20px 0;">
        <div style="background-color: {'#f0f8ff' if is_light_mode else '#1e2433'}; padding: 15px; border-radius: 8px; border-left: 5px solid {v_c};">
        <p style="font-size: 1.15rem; color: {text_col}; margin: 0; line-height: 1.6;">{v_a}</p>
        </div></div>""", unsafe_allow_html=True)

        def set_view_days(days): st.session_state.view_days = days
        dc1, dc2, dc3, dc5, dc6, dc7 = st.columns([0.8, 0.8, 0.8, 1.3, 1.3, 1.3])
        dc1.button("30日", on_click=set_view_days, args=(30,), key="btn_v30")
        dc2.button("60日", on_click=set_view_days, args=(60,), key="btn_v60")
        dc3.button("90日", on_click=set_view_days, args=(90,), key="btn_v90")
        with dc5: st.toggle("🛒 顯示買進", value=True, key='tgl_buy')
        
        # 繪製高畫質技術線圖
        fig = draw_professional_chart(df_chart, target, data['收盤價'], st.session_state.view_days, is_light_mode, st.session_state.get('tgl_buy', True), f_data)
        st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})

        st.divider()
        st.subheader("⭐ 自選群組管理")
        all_groups = list(st.session_state.fav_groups.keys())
        current_groups = [g for g, s in st.session_state.fav_groups.items() if target in s]
        selected_groups = st.multiselect("將此標的加入以下群組：", options=all_groups, default=current_groups)
        if st.button("💾 儲存自選設定", use_container_width=True, type="primary"):
            for g in all_groups:
                if g in selected_groups and target not in st.session_state.fav_groups[g]: st.session_state.fav_groups[g].append(target)
                elif g not in selected_groups and target in st.session_state.fav_groups[g]: st.session_state.fav_groups[g].remove(target)
            save_json(FAV_GROUPS_FILE, st.session_state.fav_groups)
            st.success("✅ 群組設定已更新！")
            st.rerun()
