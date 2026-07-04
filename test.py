# 最後修改時間: 2026-07-04 16:50 CST (穩定流暢版)
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
import streamlit.components.v1 as components
from bs4 import BeautifulSoup
import re
import concurrent.futures
import numpy as np

from streamlit_autorefresh import st_autorefresh

# === 雙引擎 API 憑證 ===
FINMIND_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjoiYTQ1Njg4MTUwQGdtYWlsLmNvbSIsImVtYWlsIjoiYTQ1Njg4MTUwQGdtYWlsLmNvbSIsInRva2VuX3ZlcnNpb24iOjB9.LUcb8YPV4yo93_aB3obP4Z5iUGqAgTaH28ySx9UNv5I"
FUGLE_API_KEY = "YzIzNTU5MTItYWNjMi00OGQ0LWFkNmEtYjU2MDA1N2FlZjJlIDE2ZGQzM2MzLTA5MDEtNGU2NS04MWMwLTIyMzIyMzdjODIzOA=="

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
    "Consumer Cyclical": "非必需消費品", "Healthcare": "生技醫療", "Real Estate": "建材營造"
}

@st.cache_data(ttl=86400, show_spinner=False)
def get_real_chinese_name(ticker):
    try:
        res = requests.get(f"https://invest.cnyes.com/twstock/TWS/{ticker}", headers={'User-Agent': 'Mozilla/5.0'}, timeout=4)
        soup = BeautifulSoup(res.text, 'html.parser')
        h2 = soup.find('h2')
        if h2:
            name = h2.text.strip()
            if name and not name.isdigit(): return name
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
SIM_FILE = "simulated_orders.json"

def load_json(fp, default):
    if os.path.exists(fp):
        try:
            with open(fp, "r", encoding="utf-8") as f: return json.load(f)
        except: pass
    return default
def save_json(fp, data):
    with open(fp, "w", encoding="utf-8") as f: json.dump(data, f)

if 'page' not in st.session_state: st.session_state.page = "home"
if 'current_stock' not in st.session_state: st.session_state.current_stock = "2330"
if 'custom_pool' not in st.session_state: st.session_state.custom_pool = load_json(POOL_FILE, ["2330", "2317", "2454", "2382", "3231", "2891", "9904", "1809", "0050", "2027", "1409", "3016"])
if 'nav_pool' not in st.session_state: st.session_state.nav_pool = st.session_state.custom_pool
if 'view_days' not in st.session_state: st.session_state.view_days = 60
if 'date_offset' not in st.session_state: st.session_state.date_offset = 0
if 'is_intraday' not in st.session_state: st.session_state.is_intraday = True

if 'simulated_orders' not in st.session_state:
    st.session_state.simulated_orders = load_json(SIM_FILE, [])

if 'stock' in st.query_params:
    q_stock = st.query_params['stock']
    if st.session_state.get('last_q_stock') != q_stock:
        st.session_state.current_stock = q_stock
        st.session_state.page = "analysis"
        st.session_state.date_offset = 0
        st.session_state.last_q_stock = q_stock

if 'fav_groups' not in st.session_state:
    default_groups = {"預設群組": ["1802", "2330", "1785"]}
    if os.path.exists(FAV_FILE) and not os.path.exists(FAV_GROUPS_FILE):
        old_favs = load_json(FAV_FILE, ["1802", "2330", "1785"])
        default_groups["預設群組"] = old_favs
    st.session_state.fav_groups = load_json(FAV_GROUPS_FILE, default_groups)

@st.cache_data(ttl=1800)
def fetch_twse_top_100():
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", timeout=5)
        df = pd.DataFrame(res.json())
        df['TradeVolume'] = pd.to_numeric(df['TradeVolume'], errors='coerce')
        return df[df['Code'].str.match(r'^\d{4}$')].sort_values(by='TradeVolume', ascending=False).head(100)['Code'].tolist()
    except: return ["2330", "2317", "2454", "2382", "3231"]

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_twse_index_history():
    try:
        df = yf.Ticker("^TWII").history(period="1y")
        if not df.empty:
            df.index = pd.to_datetime(df.index.strftime('%Y-%m-%d'))
            return df[['Open', 'High', 'Low', 'Close', 'Volume']]
    except: pass
    return None

# ==========================================
# 🚀 Fugle API & 量化指標引擎
# ==========================================
@st.cache_data(ttl=60, show_spinner=False) 
def get_stock_data(ticker_number):
    base_ticker = str(ticker_number).strip().upper().replace(".TW", "").replace(".TWO", "")
    def fetch_clean(sym):
        try:
            d = yf.Ticker(sym).history(period="2y") 
            if d is not None and not d.empty:
                d = d.dropna(subset=['Close'])
                if len(d) >= 60: 
                    d.index = pd.to_datetime(d.index.strftime('%Y-%m-%d'))
                    return d
        except: pass
        return None

    df = fetch_twse_index_history() if base_ticker == "^TWII" else fetch_clean(f"{base_ticker}.TW")
    if df is None and base_ticker != "^TWII": df = fetch_clean(f"{base_ticker}.TWO")
    if df is None and base_ticker != "^TWII": df = fetch_clean(base_ticker)
    
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
    except: pass

    try:
        tr1 = df['High'] - df['Low']
        tr2 = (df['High'] - df['Close'].shift(1)).abs()
        tr3 = (df['Low'] - df['Close'].shift(1)).abs()
        df['TR'] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        df['ATR'] = df['TR'].rolling(14).mean()
        
        up_move = df['High'] - df['High'].shift(1)
        down_move = df['Low'].shift(1) - df['Low']
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
        
        plus_di = 100 * (pd.Series(plus_dm, index=df.index).ewm(span=14, adjust=False).mean() / df['ATR'])
        minus_di = 100 * (pd.Series(minus_dm, index=df.index).ewm(span=14, adjust=False).mean() / df['ATR'])
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1)
        df['ADX'] = dx.ewm(span=14, adjust=False).mean()
    except:
        df['ATR'] = np.nan 
        df['ADX'] = np.nan
        
    df['5MA'] = df['Close'].rolling(5).mean()
    df['10MA'] = df['Close'].rolling(10).mean()
    df['20MA'] = df['Close'].rolling(20).mean()
    df['60MA'] = df['Close'].rolling(60).mean()
    df['200MA'] = df['Close'].rolling(200).mean()
    df['60MA_UP'] = df['60MA'] >= df['60MA'].shift(1) 
    
    df['ROC_20'] = df['Close'].pct_change(periods=20) * 100 
    
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
    big_player_ratio = 0.0 
    mom = 0.0
    yoy = 0.0
    base_ticker = str(ticker).strip().upper().replace(".TW", "").replace(".TWO", "")
    try:
        start_date_chip = (datetime.now() - timedelta(days=60)).strftime('%Y-%m-%d')
        start_date_rev = (datetime.now() - timedelta(days=500)).strftime('%Y-%m-%d')
        
        try:
            url_chip = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockHoldingSharesPer&data_id={base_ticker}&start_date={start_date_chip}&token={FINMIND_TOKEN}"
            res_chip = requests.get(url_chip, timeout=4).json()
            if 'data' in res_chip and len(res_chip['data']) > 0:
                d_list = res_chip['data']
                latest_date = max([x.get('date', '') for x in d_list])
                for x in d_list:
                    if x.get('date') == latest_date:
                        try:
                            lvl = int(x.get('HoldingSharesLevel', 0))
                            if lvl >= 12:
                                big_player_ratio += float(str(x.get('percent', 0)).replace(',', ''))
                        except: pass
        except: pass

        try:
            url_rev = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockMonthRevenue&data_id={base_ticker}&start_date={start_date_rev}&token={FINMIND_TOKEN}"
            res_rev = requests.get(url_rev, timeout=4).json()
            if 'data' in res_rev and len(res_rev['data']) > 0:
                d_list = res_rev['data']
                d_list.sort(key=lambda x: x.get('date', ''))
                for x in d_list:
                    try: x['rev_float'] = float(str(x.get('revenue', 0)).replace(',', ''))
                    except: x['rev_float'] = 0.0
                
                if len(d_list) >= 2:
                    curr_rev = d_list[-1]['rev_float']
                    last_m_rev = d_list[-2]['rev_float']
                    if last_m_rev > 0: mom = (curr_rev - last_m_rev) / last_m_rev * 100
                    
                if len(d_list) >= 13:
                    curr_rev = d_list[-1]['rev_float']
                    last_y_rev = d_list[-13]['rev_float']
                    if last_y_rev > 0: yoy = (curr_rev - last_y_rev) / last_y_rev * 100
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
        ts = int(datetime.now(tz_tpe).timestamp() * 1000)
        url = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_t00.tw&json=1&delay=0&_={ts}"
        res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=4)
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
    return fallback_curr, fallback_change, update_time_str

@st.cache_data(ttl=5, show_spinner=False)
def get_stock_live_time(ticker):
    tz_tpe = timezone(timedelta(hours=8))
    return datetime.now(tz_tpe).strftime('%Y/%m/%d %H:%M:%S')

@st.cache_data(ttl=3600, show_spinner=False)
def get_institutional_trading(ticker):
    try:
        start_date = (datetime.now() - timedelta(days=20)).strftime('%Y-%m-%d')
        url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInstitutionalInvestorsBuySell&data_id={ticker}&start_date={start_date}&token={FINMIND_TOKEN}"
        res = requests.get(url, timeout=4)
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
        "TWD=X": "https://finance.yahoo.com/quote/TWD=X"
    }
    latest_time_str = "無資料"
    for t, url in tickers.items():
        try:
            df = yf.Ticker(t).history(period="5d")
            if df is not None and not df.empty:
                df = df.dropna(subset=['Close'])
                if len(df) >= 2:
                    c = float(df['Close'].iloc[-1])
                    p = float(df['Close'].iloc[-2])
                    time_str = df.index[-1].strftime('%Y/%m/%d')
                    data[t] = {"price": c, "pct": (c-p)/p*100 if p != 0 else 0, "time": time_str, "url": url}
                    latest_time_str = time_str
        except: 
            data[t] = {"price": 0, "pct": 0, "time": "暫無資料", "url": url}
    data['global_time'] = latest_time_str
    return data

def open_pred_logic(twii_df, twii_close, twii_change, twii_time_str=""):
    macro_data = get_global_macro_data()
    if twii_df is None or len(twii_df) < 2: 
        return "資料不足", "無法分析", "資料不足", "無法預測", "", "", 50, macro_data
    if twii_close > 0:
        t_close, p_close, t_open = twii_close, twii_close - twii_change, twii_df['Open'].iloc[-1]
    else:
        t_open, t_close, p_close = twii_df['Open'].iloc[-1], twii_df['Close'].iloc[-1], twii_df['Close'].iloc[-2]
    
    tz_tpe = timezone(timedelta(hours=8))
    last_dt_str = datetime.now(tz_tpe).strftime('%Y/%m/%d')
    next_dt = datetime.now(tz_tpe) + timedelta(days=1)
    if next_dt.weekday() >= 5: next_dt += timedelta(days=(7-next_dt.weekday()))
    next_dt_str = next_dt.strftime('%Y/%m/%d')
    
    today_title, today_desc = "⚖️ 平盤震盪", "大盤開在平盤附近，法人現貨多空拉扯，量價關係縮量，盤勢震盪整理。"
    if t_open > p_close * 1.003:
        if t_close > t_open: today_title, today_desc = "🔥 開高走高", "受外資與美股溢價激勵跳空開高，量能放大，極度偏多。"
        else: today_title, today_desc = "⚠️ 開高走低", "跳空開高後遇短線獲利了結賣壓，高檔回落需留意。"
    elif t_open < p_close * 0.997:
        if t_close > t_open: today_title, today_desc = "💪 開低走高", "受國際盤影響開低，但低檔承接買盤強勁，收紅K型態。"
        else: today_title, today_desc = "🩸 開低走低", "弱勢開低，恐慌指數上升引發停損賣壓，盤勢偏空。"

    risk_score = 50 
    sox_pct = macro_data.get('^SOX', {}).get('pct', 0)
    vix_pct = macro_data.get('^VIX', {}).get('pct', 0)
    twd_pct = macro_data.get('TWD=X', {}).get('pct', 0)
    
    if sox_pct < -2.0: risk_score += 20
    elif sox_pct > 1.5: risk_score -= 15
    if vix_pct > 10.0: risk_score += 20
    elif vix_pct < -5.0: risk_score -= 10
    if twd_pct > 0.3: risk_score += 15 
    
    risk_score = max(5, min(95, int(risk_score))) 
    if risk_score < 40: tmr_title, tmr_desc = "🚀 安全偏多", f"總經環境穩定 (台股折價收斂)，預估次一交易日有極高機率開平高盤挑戰壓力。"
    elif risk_score < 70: tmr_title, tmr_desc = "⚠️ 偏空震盪", f"國際變數增加或台幣弱勢，預防次一交易日開平低盤回測下檔支撐。"
    else: tmr_title, tmr_desc = "🚨 極度警戒", f"總經風險飆高，VIX大漲，強烈建議啟動時間停損與嚴格資金控管。"
    
    st.session_state.macro_risk = risk_score
    
    return today_title, today_desc, tmr_title, tmr_desc, last_dt_str, next_dt_str, risk_score, macro_data

def render_index_board():
    try:
        twii_close, twii_change, twii_time_str = get_twii_quote()
        twii_color = '#ef4444' if twii_change >= 0 else '#22c55e'
        twii_df = get_stock_data("^TWII")
        today_title, today_desc, tmr_title, tmr_desc, last_dt_str, next_dt_str, risk_score, macro = open_pred_logic(twii_df, twii_close, twii_change, twii_time_str)
        
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
            
        st.markdown("<h4 style='margin-top:20px; text-align:center;'>🌍 全球總經與次日開盤風險評估</h4>", unsafe_allow_html=True)
        bar_color = "#22c55e" if risk_score < 40 else ("#facc15" if risk_score < 70 else "#ef4444")
        risk_label = "🟢 資金充沛，安心佈局" if risk_score < 40 else ("🟡 變數增加，控制倉位" if risk_score < 70 else "🔴 系統風險，嚴格減碼")
        st.markdown(f"<div style='text-align:center; font-size:1.1rem; font-weight:bold;'>系統量化開低風險度：<span style='color:{bar_color};'>{risk_score}%</span></div>", unsafe_allow_html=True)
        st.markdown(f"""<div class="risk-bar-container"><div class="risk-bar-fill" style="width: {risk_score}%; background-color: {bar_color}; height: 8px; border-radius: 4px;"></div></div><div style='text-align:center; font-size:0.9rem; color:{bar_color}; font-weight:bold; margin-bottom:15px;'>{risk_label}</div>""", unsafe_allow_html=True)
        
        mc1, mc2, mc3 = st.columns(3)
        sox_p = macro.get('^SOX', {}).get('pct', 0)
        with mc1.container(border=True): st.markdown(f"<div style='text-align:center; font-size:0.85rem;'>費城半導體</div><div style='text-align:center; font-size:1.1rem; font-weight:bold; color:{'#ef4444' if sox_p>=0 else '#22c55e'};'>{macro.get('^SOX', {}).get('price', 0):,.1f}<br>{'+' if sox_p>0 else ''}{sox_p:.2f}%</div>", unsafe_allow_html=True)
        vix_p = macro.get('^VIX', {}).get('pct', 0)
        with mc2.container(border=True): st.markdown(f"<div style='text-align:center; font-size:0.85rem;'>VIX 恐慌指數</div><div style='text-align:center; font-size:1.1rem; font-weight:bold; color:{'#22c55e' if vix_p<=0 else '#ef4444'};'>{macro.get('^VIX', {}).get('price', 0):,.2f}<br>{'+' if vix_p>0 else ''}{vix_p:.2f}%</div>", unsafe_allow_html=True)
        twd_p = macro.get('TWD=X', {}).get('pct', 0)
        with mc3.container(border=True): st.markdown(f"<div style='text-align:center; font-size:0.85rem;'>美元/台幣</div><div style='text-align:center; font-size:1.1rem; font-weight:bold; color:#facc15;'>{macro.get('TWD=X', {}).get('price', 0):,.3f}<br>{'台幣貶值' if twd_p>0 else '台幣升值'}</div>", unsafe_allow_html=True)
            
    except Exception as e: st.error(f"大盤儀表板渲染發生錯誤。({str(e)})")

# ==========================================
# 🌟 核心引擎重構：多因子決策樹與動態風報比
# ==========================================
def get_decision_score(data, fund_data):
    rs = []
    
    if pd.isna(data.get('ADX', np.nan)) or pd.isna(data.get('60MA', np.nan)):
        rs.append("⚪ 資料不足或無效，無法進行系統量化分析")
        return 0, rs, 1.5, 1.5, 1.0
    
    close = data.get('收盤價', 0)
    ma20 = data.get('20MA', close)
    ma60 = data.get('60MA', close)
    ma200 = data.get('200MA', close)
    
    trend_score = 0
    if close > ma20 > ma60 > ma200:
        trend_score = 100
        rs.append("✅ 長期趨勢完美多頭 (均線多頭排列)")
    elif close > ma60 > ma200:
        trend_score = 70
        rs.append("✅ 長期趨勢向上 (站穩季線與年線)")
    elif close < ma60 < ma200:
        trend_score = 0
        rs.append("🩸 空頭排列，趨勢向下")
    else:
        trend_score = 40
        rs.append("⚖️ 趨勢震盪整理中")
        
    tech_score = 0
    if data.get('紅吞', False): 
        tech_score += 30
        rs.append("🔥 出現「紅吞」強烈多頭反轉訊號")
    if data.get('回測有撐', False):
        tech_score += 20
        rs.append("✅ 帶量長下影線，主力防守支撐")
    if data.get('ADX', 0) >= 30:
        tech_score += 30
        rs.append(f"🔥 ADX 動能極強 ({data['ADX']:.1f})")
    elif data.get('ADX', 0) >= 20:
        tech_score += 15
        rs.append(f"✅ ADX 動能增溫 ({data['ADX']:.1f})")
    if data.get('J值', 50) < 30:
        tech_score += 20
        rs.append("✅ KDJ 超賣，反彈契機")
    if data.get('黑吞', False) or data.get('反彈遇壓', False):
        tech_score -= 30
        rs.append("⚠️ 出現空方吞噬或長上影線壓力")
    tech_score = max(0, min(100, tech_score))
    
    chip_score = 0
    f_net = data.get('ForeignNet10d', 0)
    t_net = data.get('TrustNet10d', 0)
    if f_net > 500:
        chip_score += 40
        rs.append(f"🔥 外資近10日大買 {f_net:,} 張")
    elif f_net > 0: chip_score += 15
    if t_net > 200:
        chip_score += 40
        rs.append(f"🔥 投信近10日積極認養 {t_net:,} 張")
    elif t_net > 0: chip_score += 15
    if data.get('BigPlayerRatio', 0) > 60:
        chip_score += 20
        rs.append(f"✅ 大戶持股高度集中 ({data['BigPlayerRatio']}%)")
    chip_score = max(0, min(100, chip_score))
    
    fund_score = 0
    yoy = data.get('YoY', 0)
    mom = data.get('MoM', 0)
    if yoy > 10 and mom > 0:
        fund_score += 60
        rs.append(f"🔥 營收雙增 (YoY: {yoy:.1f}%, MoM: {mom:.1f}%)")
    elif yoy > 10:
        fund_score += 40
        rs.append(f"✅ 年營收成長強勁 (YoY: {yoy:.1f}%)")
    try:
        eps_f = float(str(fund_data['EPS']).replace(',', ''))
        if eps_f > 1.0:
            fund_score += 40
            rs.append("✅ 歷史 EPS 獲利體質優良")
        elif eps_f > 0: fund_score += 20
    except: pass
    fund_score = max(0, min(100, fund_score))
    
    vol_score = 0
    vol_ratio = data.get('Est_Vol_Ratio', 1)
    if vol_ratio > 1.5:
        vol_score = 100
        rs.append("🔥 量能顯著放大，攻擊訊號")
    elif vol_ratio > 1.1:
        vol_score = 60
        rs.append("✅ 量能溫和放大")
    else:
        vol_score = 20

    final_score = (trend_score * 0.25) + (tech_score * 0.35) + (chip_score * 0.20) + (fund_score * 0.15) + (vol_score * 0.05)
    
    macro_risk = st.session_state.get('macro_risk', 50)
    if macro_risk >= 75 and final_score >= 60:
        final_score = 59 
        rs.append("🚨 【系統保護觸發】總經大盤風險過高，已強制取消買進訊號，轉為觀望。")
    
    roc_20 = data.get('ROC_20', 0)
    if roc_20 > 20: 
        atr_target_mult, atr_stop_mult = 1.2, 1.0 
    elif roc_20 > 10: 
        atr_target_mult, atr_stop_mult = 1.5, 1.0
    else: 
        atr_target_mult, atr_stop_mult = 2.0, 1.2 
        
    rrr = round(atr_target_mult / atr_stop_mult, 2)
    
    return int(final_score), rs, atr_target_mult, atr_stop_mult, rrr

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
    icon = "🏷️"
    for kw, ic in icon_map.items():
        if kw in ind: icon = ic; break
    return (ind, icon)

@st.cache_data(ttl=5, show_spinner=False) 
def analyze_today(df, ticker_number, inst_data=None, is_light_mode=False, pre_fund=None):
    if df is None or len(df) < 5: return None
    t, p = df.iloc[-1], df.iloc[-2]
    
    if pre_fund is not None: fund = pre_fund
    else: fund = get_fundamental_and_industry_data(ticker_number, round(t['Close'], 2))
        
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
    ma_resistance = min(t.get('5MA', t_high), t.get('10MA', t_high)) 
    upper_shadow = t_high - max(t_open, t_close)

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

    data = {
        "代號": ticker_number, "名稱": get_stock_name(ticker_number), "ticker_raw": ticker_number,
        "產業": fund['Industry'], "昨日收盤價": round(p_close, 2), "收盤價": round(t_close, 2), 
        "漲跌": round(t_close - p_close, 2), "漲跌幅": round((t_close - p_close) / p_close * 100, 2), 
        "成交量": int(t['Volume']), "5日均量": int(df['Volume'].tail(5).mean()),
        "5MA": round(t.get('5MA', t_close), 2), "10MA": round(t.get('10MA', t_close), 2), "20MA": round(t.get('20MA', t_close), 2), "60MA": round(t.get('60MA', t_close), 2), "200MA": round(t.get('200MA', t_close), 2),
        "60MA_UP": t.get('60MA_UP', False),
        "BB_UP": round(t.get('BB_UP', t_close), 2), "BB_DN": round(t.get('BB_DN', t_close), 2), "BIAS": round(t.get('BIAS_20', 0), 2),
        "ADX": round(t.get('ADX', np.nan), 1), "ROC_20": round(t.get('ROC_20', 0), 2),
        "BigPlayerRatio": bp_ratio, "MoM": mom, "YoY": yoy, 
        "ForeignNet10d": f_net_10d, "TrustNet10d": t_net_10d, "DealerNet10d": d_net_10d, 
        "紅吞": bool(red_mask.iloc[-1]), "黑吞": bool(black_mask.iloc[-1]),
        "回測有撐": (lower_shadow > body * 1.5) and (lower_shadow / total_range > 0.4) and (t_low < p_close) and (t_close >= min(p_open, p_close)),
        "反彈遇壓": (upper_shadow > body * 1.5) and (upper_shadow / total_range > 0.4) and (t_high >= ma_resistance) and (t_close < ma_resistance),
        "5日線即將上彎": t_close > (float(df['Close'].iloc[-5]) if len(df) >= 5 else float(t_close)),
        "Theme_Name": theme_name, "Theme_Icon": theme_icon,
        "VWAP": round(vwap_approx, 1), "VWAP_Dev": vwap_dev, "Est_Vol_Ratio": est_vol_ratio, "Intraday_Signal": intraday_signal, "Intraday_Score": intraday_score, "Flow": flow,
    }
    
    sc, rs, atr_target_mult, atr_stop_mult, rrr = get_decision_score(data, fund)
    data['Score'] = sc
    data['Reasons'] = rs
    
    # 🌟 卡片圈圈圖示僅顯示S, A, B 級
    data['評級'] = "S級" if sc >= 75 else ("A級" if sc >= 60 else ("B級" if sc >= 40 else "觀望"))
    
    feature = "一般狀態"
    if data['紅吞']: feature = "紅吞表態"
    elif data['回測有撐']: feature = "支撐防守"
    elif f_net_10d > 1000 or t_net_10d > 300: feature = "法人重倉"
    elif est_vol_ratio > 1.5: feature = "出量攻擊"
    elif not pd.isna(data['ADX']) and data['ADX'] >= 30: feature = "強勢動能"
    elif data['BIAS'] < -10: feature = "跌深反彈"
    data['Feature'] = feature
    
    atr_val = t.get('ATR', np.nan)
    if pd.isna(atr_val): atr_val = t_close * 0.03
    
    target_p = t_close + (atr_val * atr_target_mult)
    stop_p = t_close - (atr_val * atr_stop_mult) 
    data['ATR_Target'] = round(target_p, 1)
    data['ATR_Stop'] = round(stop_p, 1)
    data['ATR_Target_Pct'] = (target_p - t_close) / t_close * 100
    data['ATR_Stop_Pct'] = (stop_p - t_close) / t_close * 100
    data['RRR'] = rrr
    
    data['WinRate'] = 0.0 
    data['WinRate240'] = 0.0
    return data

@st.cache_data(ttl=3600, show_spinner=False)
def calculate_historical_winrate(ticker_number, df_cached=None, fund_cached=None):
    df_slice = df_cached if df_cached is not None else get_stock_data(ticker_number)
    if df_slice is None or len(df_slice) < 60:
        return 0.0, 0.0, 0, 0, []
        
    fund = fund_cached if fund_cached is not None else get_fundamental_and_industry_data(ticker_number, round(df_slice['Close'].iloc[-1], 2))
    
    def run_backtest_for_period(days):
        recent_df = df_slice.tail(days)
        wins = 0
        closed_signals = 0
        s_count, a_count = 0, 0
        b_dates = []
        start_idx = len(df_slice) - len(recent_df)
        last_buy_idx = -999
        
        for idx in range(len(recent_df)):
            actual_idx = start_idx + idx
            if actual_idx - last_buy_idx < 3: 
                continue
                
            temp_df = df_slice.iloc[:actual_idx + 1]
            if len(temp_df) >= 60: 
                t_data = analyze_today(temp_df, ticker_number, inst_data=None, is_light_mode=False, pre_fund=fund)
                
                # 🌟 降低回測觸發門檻 (40分)，增加樣本數以提供具參考價值的歷史勝率
                if t_data and t_data['Score'] >= 40: 
                    if t_data['Score'] >= 75: s_count += 1
                    else: a_count += 1
                    
                    last_buy_idx = actual_idx
                    b_dates.append(recent_df.index[idx])
                    
                    future_df = df_slice.iloc[actual_idx + 1 : actual_idx + 6] 
                    if len(future_df) > 0:
                        buy_price = future_df['Open'].iloc[0] 
                        
                        atr_val = temp_df['ATR'].iloc[-1] if 'ATR' in temp_df.columns and not pd.isna(temp_df['ATR'].iloc[-1]) else buy_price * 0.03
                        target_mult = t_data.get('RRR', 1.5)
                        target_p = buy_price + (atr_val * target_mult)
                        stop_p = buy_price - (atr_val * 1.2) 
                        
                        closed_signals += 1
                        hit_stop = future_df['Low'].min() <= stop_p
                        hit_target = future_df['High'].max() >= target_p
                        
                        # 🩸 導入真實交易成本 (手續費 0.285% + 稅 0.3% = 0.585%)
                        buy_cost = buy_price * 1.001425
                        if hit_stop:
                            pass 
                        elif hit_target:
                            sell_net = target_p * 0.995575
                            if (sell_net - buy_cost) / buy_cost > 0: wins += 1
                        else:
                            sell_net = future_df['Close'].iloc[-1] * 0.995575
                            if (sell_net - buy_cost) / buy_cost > 0: wins += 1 
                            
        win_rate = (wins / closed_signals * 100) if closed_signals > 0 else 0
        return win_rate, closed_signals, s_count, a_count, b_dates

    wr_90, closed_90, sc_90, ac_90, dates_90 = run_backtest_for_period(90)
    wr_240, _, _, _, _ = run_backtest_for_period(240) 
    
    return wr_90, wr_240, closed_90, sc_90, dates_90

def generate_comprehensive_analysis(data, inst_data, sc, f_data, is_light_mode=False):
    t_text_c = "#333" if is_light_mode else "#e2e8f0"
    card_bg = "#f4f6f9" if is_light_mode else "#0f172a"
    sum_bg = "rgba(0,0,0,0.05)" if is_light_mode else "rgba(30,41,59,0.5)"
    b_col = "#ddd" if is_light_mode else "#1e293b"

    ma_status = "均線多頭排列" if data['5MA'] > data['10MA'] > data['20MA'] else ("均線空頭排列" if data['5MA'] < data['10MA'] < data['20MA'] else "均線糾結整理")
    macd_val = data.get('MACD柱', 0)
    macd_prev = data.get('前日MACD柱', 0)
    if macd_val > 0 and macd_val > macd_prev: macd_status = "MACD 紅柱持續放大，多方動能強勢"
    elif macd_val > 0 and macd_val <= macd_prev: macd_status = "MACD 紅柱收斂，多方動能暫歇"
    elif macd_val <= 0 and macd_val > macd_prev: macd_status = "MACD 綠柱收斂，空方力道逐步減弱"
    else: macd_status = "MACD 綠柱放大，空方賣壓沉重"
    
    vol_ratio = data.get('Est_Vol_Ratio', 1)
    if vol_ratio > 1.3: vol_status = "伴隨買盤積極點火，量價結構屬多方控盤"
    elif vol_ratio < 0.7: vol_status = "市場追價意願收斂，籌碼進入量縮沉澱期"
    else: vol_status = "量能維持常態水位，買賣雙方力道均衡"

    adx_val = data.get('ADX', 0)
    if pd.isna(adx_val): adx_status = "目前趨勢指標資料尚在運算中。"
    elif adx_val >= 30: adx_status = f"ADX 高達 {adx_val:.1f}，顯示目前已進入強烈的主升/主跌段，順勢操作勝率極高。"
    elif adx_val >= 20: adx_status = f"ADX 來到 {adx_val:.1f}，暗示股價正在脫離橫盤，醞釀新一波趨勢。"
    else: adx_status = f"ADX 僅 {adx_val:.1f}，方向並不明確，容易頻繁假突破，建議區間操作或耐心觀望。"

    rich_intro = f"<p style='font-size: 0.95rem; line-height: 1.6; color: {t_text_c}; margin-bottom: 15px;'>整體型態目前呈現<b>【{ma_status}】</b>。由技術指標觀察，{macd_status}，且{vol_status}。此外，{adx_status}</p>"

    tech_bullets = []
    for reason in data['Reasons']:
        if "✅" in reason or "🔥" in reason or "🎯" in reason:
            tech_bullets.append(f"<span style='color:#ef4444; font-weight:bold;'>{reason}</span>")
        elif "⚠️" in reason or "⚖️" in reason or "🚨" in reason:
            tech_bullets.append(f"<span style='color:#facc15;'><b>{reason}</b></span>")
        else:
            tech_bullets.append(f"<span style='color:#94a3b8;'><b>{reason}</b></span>")

    tech_res = "🔥 決策樹判定：符合波段發動特徵，建議嚴格設定資金部位試單。" if sc >= 60 else "⚖️ 決策樹判定：缺乏長多趨勢或強動能保護，建議縮小部位或觀望。"
    
    tech_html = f"<div style='border: 1px solid {b_col}; border-radius: 8px; padding: 15px; margin-bottom: 15px; background-color: {card_bg};'>"
    tech_html += f"<h4 style='color: #60a5fa; margin-top: 0; font-size: 1.2rem; display: flex; align-items: center;'>📈 多因子技術決策分析</h4>"
    tech_html += rich_intro 
    tech_html += f"<ul style='font-size: 0.95rem; line-height: 1.6; margin-bottom: 15px; color: {t_text_c};'>"
    for b in tech_bullets:
        tech_html += f"<li style='margin-bottom:6px;'>{b}</li>"
    tech_html += f"</ul>"
    tech_html += f"<div style='background-color: {sum_bg}; padding: 12px; border-radius: 6px; border-left: 4px solid #60a5fa; font-size: 0.95rem; color: {t_text_c}; line-height: 1.6;'>"
    tech_html += f"<b>【綜合診斷】</b>{tech_res}"
    tech_html += f"</div></div>"

    chip_res_text = "中立觀望"
    tables_html = ""
    th_color = "#ccc" if not is_light_mode else "#555"
    def get_c(val): return "#ef4444" if val > 0 else ("#22c55e" if val < 0 else t_text_c)

    big_player_ratio = data.get('BigPlayerRatio', 0.0)
    f_net = data.get('ForeignNet10d', 0)
    t_net = data.get('TrustNet10d', 0)
    d_net = data.get('DealerNet10d', 0)
    
    if inst_data and len(inst_data) >= 3:
        f_net_today = sum([int(str(x['外資(張)']).replace(',', '')) for x in inst_data[:3] if str(x['外資(張)']).replace(',', '').lstrip('-').isdigit()])
        t_net_today = sum([int(str(x['投信(張)']).replace(',', '')) for x in inst_data[:3] if str(x['投信(張)']).replace(',', '').lstrip('-').isdigit()])
        
        if f_net_today > 0 and t_net_today > 0: chip_res_text = "🔥 外資跟投信同步佈局，籌碼正流入大戶法人手中。"
        elif f_net_today < 0 and t_net_today < 0: chip_res_text = "⚠️ 內外資同步倒貨，籌碼鬆動流向散戶，需提防高檔出貨。"
        else: chip_res_text = "⚖️ 法人多空步調不一，籌碼處於換手震盪階段。"

        tables_html += f"<div style='display: flex; gap: 15px; flex-wrap: wrap; margin-top: 15px; width: 100%;'>"
        
        tables_html += f"<div style='flex: 1; min-width: 260px; border: 1px solid {b_col}; border-radius: 6px; padding: 15px; background-color: {sum_bg}; position: relative;'>"
        tables_html += f"<div style='font-weight: bold; color: {t_text_c}; font-size: 1rem; margin-bottom: 15px; display: flex; align-items: center; gap: 5px;'>🎯 進階籌碼監控 (真實數據)</div>"
        
        bp_c = '#ef4444' if big_player_ratio > 60 else '#facc15'
        tables_html += f"<div style='margin-bottom: 15px; border-bottom: 1px dashed {b_col}; padding-bottom: 10px;'>"
        tables_html += f"<div style='display: flex; justify-content: space-between; font-size: 0.85rem; color: {t_text_c}; margin-bottom: 4px;'><span>大戶持股集中度 (400張以上)</span><span style='color: {bp_c}; font-weight: bold;'>{big_player_ratio:.2f}%</span></div>"
        tables_html += f"<div style='width: 100%; height: 8px; background-color: rgba(128,128,128,0.2); border-radius: 4px;'><div style='width: {big_player_ratio}%; height: 100%; background-color: {bp_c}; border-radius: 4px;'></div></div>"
        tables_html += f"</div>"
        
        tables_html += f"<div style='font-size: 0.9rem; font-weight: bold; margin-bottom: 10px; color: {t_text_c};'>⚖️ 三大法人 10 日累積買賣超</div>"
        tables_html += f"<div style='display: flex; justify-content: space-between; font-size: 0.85rem; margin-bottom: 8px;'><span>外資及陸資</span><span style='color: {get_c(f_net)}; font-weight: bold;'>{'+' if f_net>0 else ''}{f_net:,} 張</span></div>"
        tables_html += f"<div style='display: flex; justify-content: space-between; font-size: 0.85rem; margin-bottom: 8px;'><span>投信</span><span style='color: {get_c(t_net)}; font-weight: bold;'>{'+' if t_net>0 else ''}{t_net:,} 張</span></div>"
        tables_html += f"<div style='display: flex; justify-content: space-between; font-size: 0.85rem;'><span>自營商</span><span style='color: {get_c(d_net)}; font-weight: bold;'>{'+' if d_net>0 else ''}{d_net:,} 張</span></div>"
        tables_html += f"</div>"
        
        tables_html += f"<div style='flex: 1.5; min-width: 320px;'>"
        tables_html += f"<div style='font-weight: bold; color: {t_text_c}; font-size: 0.95rem; margin-bottom: 10px;'>⏳ 近五日三大法人逐日買賣超明細 (張)</div>"
        tables_html += f"<table style='width: 100%; text-align: center; border-collapse: collapse; font-size: 0.9rem; border: 1px solid {b_col}; color: {t_text_c};'>"
        tables_html += f"<tr style='background-color: {sum_bg}; color: {th_color};'>"
        tables_html += f"<th style='border: 1px solid {b_col}; padding: 8px 4px;'>日期</th>"
        tables_html += f"<th style='border: 1px solid {b_col}; padding: 8px 4px;'>外資</th>"
        tables_html += f"<th style='border: 1px solid {b_col}; padding: 8px 4px;'>投信</th>"
        tables_html += f"<th style='border: 1px solid {b_col}; padding: 8px 4px;'>自營商</th>"
        tables_html += f"<th style='border: 1px solid {b_col}; padding: 8px 4px;'>合計</th></tr>"
        
        for row in inst_data[:5]:
            date_str = row['日期']
            f_val = int(str(row['外資(張)']).replace(',', ''))
            t_val = int(str(row['投信(張)']).replace(',', ''))
            d_val = int(str(row['自營商(張)']).replace(',', ''))
            s_val = int(str(row['單日合計(張)']).replace(',', ''))
            tables_html += f"<tr>"
            tables_html += f"<td style='border: 1px solid {b_col}; padding: 8px 4px;'>{date_str}</td>"
            tables_html += f"<td style='border: 1px solid {b_col}; padding: 8px 4px; color: {get_c(f_val)}; font-weight: 500;'>{f_val}</td>"
            tables_html += f"<td style='border: 1px solid {b_col}; padding: 8px 4px; color: {get_c(t_val)}; font-weight: 500;'>{t_val}</td>"
            tables_html += f"<td style='border: 1px solid {b_col}; padding: 8px 4px; color: {get_c(d_val)}; font-weight: 500;'>{d_val}</td>"
            tables_html += f"<td style='border: 1px solid {b_col}; padding: 8px 4px; color: {get_c(s_val)}; font-weight: 500;'>{s_val}</td>"
            tables_html += f"</tr>"
        tables_html += f"</table>"
        tables_html += f"<div style='text-align: right; font-size: 0.75rem; color: #888; margin-top: 10px;'>來源: FinMind API</div>"
        tables_html += f"</div></div>"
    else:
        tables_html = f"<div style='color: {sub_text_col}; font-size: 0.9rem; padding: 10px; border: 1px dashed {border_col}; border-radius: 6px;'>目前暫無籌碼資料可供分析。</div>"

    chip_html = f"<div style='border: 1px solid {b_col}; border-radius: 8px; padding: 15px; margin-bottom: 15px; background-color: {card_bg};'>"
    chip_html += f"<h4 style='color: #facc15; margin-top: 0; font-size: 1.2rem; display: flex; align-items: center;'>🏦 籌碼面分析</h4>"
    chip_html += f"{tables_html}"
    chip_html += f"<div style='background-color: {sum_bg}; padding: 12px; border-radius: 6px; border-left: 4px solid #facc15; font-size: 0.95rem; color: {t_text_c}; line-height: 1.6; margin-top: 15px;'>"
    chip_html += f"<b>【結　　果】</b>{chip_res_text}"
    chip_html += f"</div></div>"

    fund_bullets = []
    eps = f_data.get('EPS', '無')
    pe = f_data.get('PE', '無')
    ind = f_data.get('Industry', '一般產業')
    yahoo_news_url = f"https://tw.stock.yahoo.com/quote/{data['代號']}/news"
    
    fund_bullets.append(f"⚪ <b>產業趨勢/題材</b>：隸屬【{ind}】板塊，受惠於市場趨勢發展。 <a href='{yahoo_news_url}' target='_blank' style='color:#60a5fa; text-decoration:none;'>[🔗Yahoo新聞解析]</a>")
    
    mom_c = "#ef4444" if data.get('MoM', 0) > 0 else ("#22c55e" if data.get('MoM', 0) < 0 else t_text_c)
    yoy_c = "#ef4444" if data.get('YoY', 0) > 0 else ("#22c55e" if data.get('YoY', 0) < 0 else t_text_c)
    fund_bullets.append(f"⚪ <b>最新月營收動能</b>：月增 (MoM) <span style='color:{mom_c}; font-weight:bold;'>{data.get('MoM', 0):.2f}%</span>，年增 (YoY) <span style='color:{yoy_c}; font-weight:bold;'>{data.get('YoY', 0):.2f}%</span>。 <span style='color:#888; font-size:0.8rem;'>(註: 月營收為落後指標，僅作體質濾網參考)</span>")
    
    fund_bullets.append(f"⚪ <b>當季EPS</b>：<b>{eps}</b> 元。 <span style='font-size:0.8rem; color:#888;'>[資料來源: 證交所]</span>")
    fund_bullets.append(f"⚪ <b>本益比 (PE)</b>：最新即時估值為 <b>{pe}</b> 倍。")
    
    try: 
        eps_f = float(eps)
        pe_f = float(pe) if pe != "無" else 999
        if eps_f > 0 and pe_f < 20: fund_res = "🔥 具備實質獲利支撐，且本益比合理，具投資價值。"
        elif eps_f > 0 and pe_f >= 20: fund_res = "⚠️ 公司雖有獲利，但目前的本益比估值偏高，需留意追高風險。"
        else: fund_res = "🩸 暫無明顯獲利支撐，或呈現虧損，需嚴防營運風險。"
    except: 
        fund_res = "⚪ 基礎財報數據不足，暫以技術與籌碼面為主。"

    fund_html = f"<div style='border: 1px solid {b_col}; border-radius: 8px; padding: 15px; margin-bottom: 15px; background-color: {card_bg};'>"
    fund_html += f"<h4 style='color: #c084fc; margin-top: 0; font-size: 1.2rem; display: flex; align-items: center;'>📑 基本面分析</h4>"
    fund_html += f"<ul style='font-size: 0.95rem; line-height: 1.6; margin-bottom: 15px; color: {t_text_c};'>"
    for b in fund_bullets: fund_html += f"<li style='margin-bottom:6px;'>{b}</li>"
    fund_html += f"</ul>"
    fund_html += f"<div style='background-color: {sum_bg}; padding: 12px; border-radius: 6px; border-left: 4px solid #c084fc; font-size: 0.95rem; color: {t_text_c}; line-height: 1.6;'>"
    fund_html += f"<b>【結　　果】</b>{fund_res}"
    fund_html += f"</div></div>"
    
    risk_html = f"<div style='border: 1px solid #34d399; border-radius: 8px; padding: 15px; margin-bottom: 15px; background-color: rgba(16, 185, 129, 0.05);'>"
    risk_html += f"<h4 style='color: #34d399; margin-top: 0; font-size: 1.2rem; display: flex; align-items: center;'>🛡️ 風險與資金控管 (Position Sizing)</h4>"
    
    risk_html += f"<div style='background-color: rgba(245, 158, 11, 0.1); border-left: 4px solid #f59e0b; padding: 10px; margin-bottom: 15px; font-size: 0.9rem; color: {t_text_c};'>"
    risk_html += f"⚠️ <b>注意：</b> 盤中指標（如紅吞、MACD）隨報價跳動會產生重繪效應。真正的波段買點，建議於 <b>13:20 尾盤</b> 確認 K 線型態後再行決策。"
    risk_html += f"</div>"
    
    buy_p = data['收盤價']
    stop_p = data['ATR_Stop']
    risk_per_share = buy_p - stop_p
    
    if risk_per_share > 0:
        suggested_shares_5k = int(5000 / risk_per_share)
        suggested_shares_10k = int(10000 / risk_per_share)
        risk_desc = f"每股承受風險距為 <b>{risk_per_share:.1f}</b> 元。<br>若單筆交易願承受最大虧損 <b>$5,000</b>，建議買進 <b>{suggested_shares_5k:,}</b> 股 ({suggested_shares_5k/1000:.1f} 張)。<br>若可承受虧損 <b>$10,000</b>，建議買進 <b>{suggested_shares_10k:,}</b> 股 ({suggested_shares_10k/1000:.1f} 張)。"
    else:
        risk_desc = "無法計算風險距，建議觀望。"

    risk_html += f"<p style='font-size: 1rem; color: {t_text_c}; line-height: 1.6;'>{risk_desc}</p>"
    risk_html += f"<p style='font-size: 0.85rem; color: #888;'>*量化交易核心在於資金分配。歷史勝率回測已扣除 0.585% 交易稅費摩擦成本，請嚴格執行停損，切勿單筆重押。</p>"
    risk_html += f"</div>"

    return tech_html + chip_html + fund_html + risk_html

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
            s_col = "#ef4444" if score >= 75 else ("#facc15" if score >= 60 else "#22c55e")
            rating = "S級" if score >= 75 else ("A級" if score >= 60 else ("B級" if score >= 40 else "觀望"))
            
        r_col = "#4ade80" if "S級" in rating else ("#facc15" if "A級" in rating else "#94a3b8")
        
        stock_link = f'href="/?stock={r.get("代號", "")}" target="_self"'
        
        cards_html += f"<div style='background-color: #0f172a; border: 1px solid #1e293b; border-radius: 12px; padding: 14px; margin-bottom: 12px; position: relative; overflow: hidden;'>"
        cards_html += f"<div style='display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; position: relative; z-index: 10;'>"
        cards_html += f"<div style='display: flex; align-items: center; gap: 12px;'>"
        
        cards_html += f"<div style='width: 50px; height: 50px; border-radius: 50%; background: radial-gradient(circle, #1e293b 0%, #0b1120 100%); border: 1px solid #334155; display: flex; flex-direction: column; align-items: center; justify-content: center; flex-shrink: 0; box-shadow: inset 0 2px 4px rgba(255,255,255,0.05), 0 4px 8px rgba(0,0,0,0.4);'>"
        cards_html += f"<span style='color: {s_col}; font-weight: 800; font-size: 1.2rem; line-height: 1;'>{score}</span>"
        cards_html += f"<span style='color: {r_col}; font-size: 0.65rem; font-weight: 800; margin-top: 2px;'>{rating}</span>"
        cards_html += f"</div>"
        
        cards_html += f"<a {stock_link} class='stock-card-link'>"
        cards_html += f"<div style='display: flex; align-items: center; gap: 6px;'>"
        cards_html += f"<span class='stock-name-hover' style='color: #f8fafc; font-weight: bold; font-size: 1.15rem; transition: color 0.2s;'>{r.get('名稱', '')}</span>"
        if r.get("Theme_Name", "一般") != "一般題材":
            cards_html += f"<span style='font-size: 0.7rem; background-color: rgba(79,70,229,0.15); color: #818cf8; border: 1px solid rgba(79,70,229,0.3); padding: 2px 6px; border-radius: 4px; white-space: nowrap; font-weight: 600;'>{r.get('Theme_Icon', '')} {r.get('Theme_Name', '')}</span>"
        cards_html += f"</div>"
        cards_html += f"<div style='font-size: 0.8rem; color: #64748b; margin-top: 4px; font-family: monospace;'>{r.get('代號', '')} <span style='color:#475569; font-size:0.7rem; margin-left:4px;'>(點擊解析)</span></div>"
        cards_html += f"</a></div>"
        
        cards_html += f"<div style='text-align: right; flex-shrink: 0;'>"
        cards_html += f"<div style='color: {p_col}; font-weight: 800; font-size: 1.2rem; font-family: monospace;'>{r.get('收盤價', 0):.1f}</div>"
        cards_html += f"<div style='background-color: {p_bg}; color: {p_col}; font-size: 0.75rem; padding: 2px 6px; border-radius: 4px; display: inline-block; font-weight: 800; font-family: monospace; margin-top: 4px;'>{change_sign}{r.get('漲跌幅', 0)}%</div>"
        cards_html += f"</div></div>"
        
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
            cards_html += f"<div style='display: flex; flex-direction: column;'><span style='color: #64748b; margin-bottom: 4px;'>一季勝率</span><span style='color: {wr_col}; font-weight: bold; font-family: monospace;'>{wr_val}%</span></div>"
            cards_html += f"</div>"
            cards_html += f"<div style='font-size: 0.75rem; color: #fbbf24; display: flex; align-items: flex-start; gap: 6px; position: relative; z-index: 10;'><span style='margin-top: 1px;'>⚡</span><span style='line-height: 1.4; font-weight: 500;'>盤中訊號：{r.get('Intraday_Signal', '穩守均價線')}</span></div>"
        else:
            wr_val = r.get('WinRate', 0.0)
            wr_240 = r.get('WinRate240', 0.0)
            wr_col = "#ef4444" if wr_val >= 75 else ("#facc15" if wr_val >= 40 else "#22c55e")
            rrr_val = r.get('RRR', 1.5)
            w_net = r.get('Whale_Net', 0)
            w_col = "#ef4444" if w_net > 0 else ("#22c55e" if w_net < 0 else "#94a3b8")
            whale_str = f"+{w_net:,}" if w_net > 0 else f"{w_net:,}"
            
            cards_html += f"<div style='display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; background-color: rgba(30,41,59,0.4); border: 1px solid rgba(51,65,85,0.5); padding: 10px; border-radius: 8px; font-size: 0.75rem; margin-bottom: 10px; position: relative; z-index: 10;'>"
            cards_html += f"<div style='display: flex; flex-direction: column;'><span style='color: #64748b; margin-bottom: 4px;'>一季勝率</span><span style='color: {wr_col}; font-weight: bold; font-family: monospace;'>{wr_val}%</span></div>"
            cards_html += f"<div style='display: flex; flex-direction: column;'><span style='color: #64748b; margin-bottom: 4px;'>一年勝率</span><span style='color: {wr_col}; font-weight: bold; font-family: monospace;'>{wr_240}%</span></div>"
            cards_html += f"<div style='display: flex; flex-direction: column;'><span style='color: #64748b; margin-bottom: 4px;'>風報比</span><span style='color: #e2e8f0; font-weight: bold; font-family: monospace;'>1:{rrr_val}</span></div>"
            cards_html += f"<div style='display: flex; flex-direction: column;'><span style='color: #64748b; margin-bottom: 4px;'>法人淨買</span><span style='color: {w_col}; font-weight: bold; font-family: monospace;'>{whale_str}</span></div>"
            cards_html += f"</div>"
            cards_html += f"<div style='font-size: 0.75rem; color: #fbbf24; display: flex; align-items: flex-start; gap: 6px; position: relative; z-index: 10;'><span style='margin-top: 1px;'>⚡</span><span style='line-height: 1.4; font-weight: 500;'>主力特徵：{r.get('Feature', '一般')}</span></div>"
        
        cards_html += f"</div>"
    return cards_html

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
    
    fig.add_trace(go.Scatter(x=x_vals, y=df_view['5MA'], line=dict(color='#f59e0b', width=1.5), name="5T"), row=1, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df_view['10MA'], line=dict(color='#10b981', width=1.5), name="10T"), row=1, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df_view['20MA'], line=dict(color='#8b5cf6', width=1.5), name="20T"), row=1, col=1)
    
    fig.add_hline(y=latest_price, line_dash="dash", line_color="#facc15", row=1, col=1)
    
    if show_sup_res:
        highest_price = df_view['High'].max()
        lowest_price = df_view['Low'].min()
        fig.add_hline(y=highest_price, line_dash="dash", line_color="#ef4444", row=1, col=1, annotation_text=f"壓力 {highest_price:.2f}", annotation_position="top right", annotation_font=dict(size=12, color="#ef4444"))
        fig.add_hline(y=lowest_price, line_dash="dash", line_color="#22c55e", row=1, col=1, annotation_text=f"支撐 {lowest_price:.2f}", annotation_position="bottom right", annotation_font=dict(size=12, color="#22c55e"))
    
    re_x, re_y, re_text = [], [], []
    be_x, be_y, be_text = [], [], []
    sup_x, sup_y, sup_text = [], [], []
    
    start_pos = len(df) - len(df_view)
    for i, date in enumerate(df_view.index):
        pos = start_pos + i
        if pos >= 1:
            t = df.iloc[pos]
            p = df.iloc[pos-1]
            
            t_open, t_close, t_high, t_low = t['Open'], t['Close'], t['High'], t['Low']
            p_open, p_close = p['Open'], p['Close']
            
            is_red = (p_open > p_close) and (t_close > t_open) and (t_close > p_open) and (t_open < p_close)
            is_black = (p_close > p_open) and (t_open > t_close) and (t_open > p_close) and (t_close < p_open)
            
            if is_red:
                re_x.append(date.strftime('%Y-%m-%d'))
                re_y.append(t_low * 0.98) 
                re_text.append("<b>紅吞</b>")
            if is_black:
                be_x.append(date.strftime('%Y-%m-%d'))
                be_y.append(t_high * 1.02) 
                be_text.append("<b>黑吞</b>")
            
            total_range = t_high - t_low if t_high - t_low != 0 else 0.001
            lower_shadow = min(t_open, t_close) - t_low
            body = abs(t_close - t_open)

            is_support_pullback = (lower_shadow > body * 1.5) and (lower_shadow / total_range > 0.4) and (t_low < p_close) and (t_close >= min(p_open, p_close))
            if is_support_pullback:
                sup_x.append(date.strftime('%Y-%m-%d'))
                sup_y.append(t_low * 0.95) 
                sup_text.append("<b>撐</b>")

    if show_signals:
        if re_x: fig.add_trace(go.Scatter(x=re_x, y=re_y, mode='text', text=re_text, textposition="bottom center", textfont=dict(color="#ef4444", size=13), name="紅吞", hoverinfo='skip'), row=1, col=1)
        if be_x: fig.add_trace(go.Scatter(x=be_x, y=be_y, mode='text', text=be_text, textposition="top center", textfont=dict(color="#22c55e", size=13), name="黑吞", hoverinfo='skip'), row=1, col=1)
        if sup_x: fig.add_trace(go.Scatter(x=sup_x, y=sup_y, mode='text', text=sup_text, textposition="bottom center", textfont=dict(color="#facc15", size=13), name="回測有撐", hoverinfo='skip'), row=1, col=1)

    if show_buy_signal and buy_dates:
        buy_x, buy_y, buy_text = [], [], []
        for d in buy_dates:
            if d in df_view.index:
                idx = df_view.index.get_loc(d)
                buy_x.append(d.strftime('%Y-%m-%d'))
                buy_y.append(df_view['Low'].iloc[idx] * 0.90) 
                buy_text.append("買")
        if buy_x:
            fig.add_trace(go.Scatter(x=buy_x, y=buy_y, mode='markers+text', marker=dict(symbol='triangle-up', size=14, color='#34d399'), text=buy_text, textposition="bottom center", textfont=dict(color="#34d399", size=11, weight="bold"), name="買進訊號", hoverinfo='skip'), row=1, col=1)
            
    fig.add_trace(go.Bar(x=x_vals, y=df_view['Volume'], marker_color=colors, name="VOL"), row=2, col=1)
    macd_colors = ['#ef4444' if val > 0 else '#22c55e' for val in df_view['MACD_Hist']]
    fig.add_trace(go.Bar(x=x_vals, y=df_view['MACD_Hist'], marker_color=macd_colors, name="OSC"), row=3, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df_view['MACD'], line=dict(color=line_k, width=1.5), name="DIF"), row=3, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df_view['Signal'], line=dict(color=line_d, width=1.5), name="MACD"), row=3, col=1)
    
    adx_color = "#ef4444"
    fig.add_trace(go.Scatter(x=x_vals, y=df_view['ADX'], line=dict(color=adx_color, width=2.5), name="ADX趨勢動能"), row=4, col=1)
    fig.add_hline(y=30, line_dash="dash", line_color="#facc15", row=4, col=1, annotation_text="強勢線 (30)", annotation_font=dict(size=10, color="#facc15"))

    fig.add_annotation(x=0.01, y=0.98, xref="paper", yref="y domain", text=f"5T:{last_row['5MA']:.1f} | 10T:{last_row['10MA']:.1f} | 20T:{last_row['20MA']:.1f}", showarrow=False, font=dict(color="#facc15", size=12), xanchor="left", bgcolor=ann_bg)
    fig.add_annotation(x=0.01, y=0.95, xref="paper", yref="y2 domain", text=f"VOL: {last_row['Volume']:,.0f}", showarrow=False, font=dict(color=text_c, size=12), xanchor="left", bgcolor=ann_bg)
    fig.add_annotation(x=0.01, y=0.95, xref="paper", yref="y3 domain", text=f"MACD:{last_row['MACD']:.2f} | DIF:{last_row['Signal']:.2f}", showarrow=False, font=dict(color=text_c, size=12), xanchor="left", bgcolor=ann_bg)
    fig.add_annotation(x=0.01, y=0.95, xref="paper", yref="y4 domain", text=f"ADX:{last_row['ADX']:.2f}", showarrow=False, font=dict(color=text_c, size=12), xanchor="left", bgcolor=ann_bg)

    fig.update_xaxes(type='category', nticks=15, fixedrange=True, showgrid=True, gridcolor=grid_c)
    fig.update_yaxes(fixedrange=True, showgrid=True, gridcolor=grid_c)
    
    fig.update_layout(xaxis_rangeslider_visible=False, template="plotly_white" if is_light_mode else "plotly_dark", height=850, margin=dict(l=10, r=10, t=10, b=30), paper_bgcolor=bg_c, plot_bgcolor=bg_c, hovermode='x unified', hoverlabel=dict(bgcolor=bg_c, font_size=13, font_color=text_c), dragmode=False, showlegend=False)
    fig.add_annotation(text="📊 資料來源: yfinance / TWSE / WantGoo", xref="paper", yref="paper", x=1.0, y=-0.05, showarrow=False, font=dict(size=12, color=text_c))
    return fig

# ==========================================
# 🚀 頁面路由控制中心
# ==========================================
if st.session_state.page == "home":
    st.markdown("<h2 style='text-align: center; color: #818cf8; margin-bottom: 20px;'>極致精準：雙引擎量化雷達</h2>", unsafe_allow_html=True)
    render_index_board()
    st.markdown("<br>", unsafe_allow_html=True)
    
    top_100_pool = fetch_twse_top_100()
    pool = tuple(set(top_100_pool + st.session_state.custom_pool + list(STOCK_NAMES.keys())))
    
    if "scan_results" not in st.session_state:
        st.session_state.scan_results = []
        progress_text = st.empty()
        p_bar = st.progress(0)
        
        pool_list = list(pool)
        total = len(pool_list)
        completed = 0
        
        def process_scan(stock):
            try:
                # 回歸穩定不卡的掃描方式：單純依序取資料，若有錯誤即跳過
                df = get_stock_data(stock)
                if df is None or len(df) < 60: return None
                
                inst_data = get_institutional_trading(stock)
                fund = get_fundamental_and_industry_data(stock, round(df['Close'].iloc[-1], 2))
                data = analyze_today(df, stock, inst_data=inst_data, is_light_mode=is_light_mode, pre_fund=fund)
                
                if data:
                    if data.get('Score', 0) >= 40 or data.get('Intraday_Score', 0) >= 60: 
                        wr_90, wr_240, _, _, _ = calculate_historical_winrate(stock, df_cached=df, fund_cached=fund)
                        data['WinRate'] = round(wr_90, 1)
                        data['WinRate240'] = round(wr_240, 1)
                    return data
            except:
                pass
            return None
            
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor: 
            futures = {executor.submit(process_scan, stock): stock for stock in pool_list}
            for future in concurrent.futures.as_completed(futures):
                completed += 1
                try:
                    res = future.result()
                    if res: st.session_state.scan_results.append(res)
                except: pass
                
                p_bar.progress(min(completed / total, 1.0))
                progress_text.markdown(f"<div style='text-align: center; color: #818cf8; font-weight: bold;'>🚀 雙週期回測極速解析中... ({completed} / {total})</div>", unsafe_allow_html=True)
                
        progress_text.empty()
        p_bar.empty()
            
    if st.session_state.scan_results:
        df_results = pd.DataFrame(st.session_state.scan_results)
        
        col_m1, col_m2 = st.columns([1, 1])
        with col_m1:
            radar_mode = st.radio("引擎模式：", ["盤後波段精算 (15:00後)", "盤中動能快篩 (09:00-13:30)"], horizontal=True, label_visibility="collapsed")
        is_intraday = "盤中" in radar_mode
        st.session_state.is_intraday = is_intraday
        
        available_themes = ["全部題材"] + sorted(list(set(df_results['Theme_Name'].unique()) - {"一般題材"}))
        selected_theme = st.radio("題材過濾：", available_themes, horizontal=True, label_visibility="collapsed")
        
        if selected_theme != "全部題材":
            df_results = df_results[df_results['Theme_Name'] == selected_theme]
            
        available_features = ["全部特徵"] + sorted(list(set(df_results['Feature'].unique())))
        selected_feature = st.radio("特徵過濾：", available_features, horizontal=True, label_visibility="collapsed")
        
        if selected_feature != "全部特徵":
            df_results = df_results[df_results['Feature'] == selected_feature]
        
        if not df_results.empty:
            if is_intraday:
                df_disp = df_results[df_results['Intraday_Score'] >= 60].sort_values(by=['Intraday_Score', '漲跌幅'], ascending=[False, False]).head(30)
            else:
                df_disp = df_results[df_results['Score'] >= 40].sort_values(by=['Score', '漲跌幅'], ascending=[False, False]).head(30)
        else:
            df_disp = df_results
        
        st.session_state.nav_pool = df_disp['ticker_raw'].tolist()
        st.session_state.nav_pool_data = df_disp.to_dict('records') 
            
        st.markdown(f"<div style='display: flex; justify-content: space-between; font-size: 0.8rem; color: #94a3b8; border-bottom: 1px solid #1e293b; padding-bottom: 8px; margin-bottom: 16px;'><span><i class='fa-solid fa-bolt'></i> {'09:00-13:30 盤中動能排序' if is_intraday else 'V2.1 評分系統波段精選'}</span><span>共 {len(df_disp)} 檔</span></div>", unsafe_allow_html=True)
        
        if df_disp.empty:
            st.markdown("<div style='text-align: center; padding: 40px; color: #64748b; font-size: 0.9rem;'>此條件下目前無推薦的標的。</div>", unsafe_allow_html=True)
        else:
            cards_html = generate_cards_html(df_disp, is_intraday)
            st.markdown(cards_html, unsafe_allow_html=True)

# ==========================================
# 🚀 模擬交易紀錄獨立頁面
# ==========================================
elif st.session_state.page == "simulated_orders":
    st.markdown("<h2 style='text-align: center; color: #818cf8; margin-bottom: 20px;'>🛒 我的模擬下單紀錄</h2>", unsafe_allow_html=True)
    
    col_home, col_clear = st.columns([1, 1])
    with col_home:
        if st.button("🏠 回雷達總機", use_container_width=True, key="btn_sim_home"): 
            st.session_state.page = "home"
            st.rerun()
    with col_clear:
        if st.button("🗑️ 清空所有紀錄", use_container_width=True, key="btn_sim_clear"):
            st.session_state.simulated_orders = []
            save_json(SIM_FILE, [])
            st.success("已清除所有模擬下單紀錄！")
            st.rerun()
            
    orders = st.session_state.get('simulated_orders', [])
    
    if not orders:
        st.info("目前沒有模擬下單紀錄。請先進入「個股解析頁面」，點擊「🛒 執行模擬下單」來建立您的紙上測試部位！")
    else:
        sector_counts = {}
        for order in orders:
            sec = order.get('industry', '未知')
            sector_counts[sec] = sector_counts.get(sec, 0) + 1
            
        heavy_sectors = [s for s, c in sector_counts.items() if c >= 3 and s != '未知']
        if heavy_sectors:
            st.markdown(f"<div style='background-color: rgba(239,68,68,0.1); border-left: 4px solid #ef4444; padding: 12px; margin-bottom: 15px; border-radius: 4px; color: #ef4444; font-size: 0.95rem;'>⚠️ <b>風險警告</b>：您的投資組合中，在【{', '.join(heavy_sectors)}】板塊持倉超過 3 檔，板塊關聯性風險極高，若遇單一產業回調將面臨巨大虧損。</div>", unsafe_allow_html=True)
            
        st.markdown(f"<div style='text-align: right; font-size: 0.85rem; color: #888; margin-bottom: 15px;'>共 {len(orders)} 筆紀錄，價格為即時抓取更新</div>", unsafe_allow_html=True)
        
        if "delete_order_id" in st.session_state:
            del_id = st.session_state.delete_order_id
            st.session_state.simulated_orders = [o for o in orders if o.get('id') != del_id]
            save_json(SIM_FILE, st.session_state.simulated_orders)
            del st.session_state["delete_order_id"]
            st.rerun()
            
        card_bg_global = "#f4f6f9" if is_light_mode else "#0f172a"
        title_c_global = "#111" if is_light_mode else "#f8fafc"
        
        for idx, order in enumerate(orders):
            df_temp = get_stock_data(order['ticker']) 
            curr_price = float(df_temp['Close'].iloc[-1]) if df_temp is not None else order['buy_price']
            ma10 = float(df_temp['10MA'].iloc[-1]) if df_temp is not None else order['buy_price']
            atr = float(df_temp['ATR'].iloc[-1]) if df_temp is not None and not pd.isna(df_temp['ATR'].iloc[-1]) else order['buy_price'] * 0.03
            
            if 'highest_price' not in order: order['highest_price'] = order['buy_price']
            if curr_price > order['highest_price']: 
                order['highest_price'] = curr_price
                save_json(SIM_FILE, st.session_state.simulated_orders)
                
            dynamic_stop = order['highest_price'] - (1.2 * atr) 
            pl_val = curr_price - order['buy_price']
            pl_pct = (pl_val / order['buy_price']) * 100 if order['buy_price'] > 0 else 0
            
            if curr_price < ma10:
                status_html = f"<div style='background-color: rgba(239,68,68,0.2); color: #ef4444; border: 1px solid #ef4444; padding: 2px 8px; border-radius: 4px; font-size: 0.75rem; font-weight: bold; margin-left: 8px;'>📉 跌破 10MA ({ma10:.1f})，停利出場</div>"
            elif curr_price < dynamic_stop:
                status_html = f"<div style='background-color: rgba(34,197,94,0.2); color: #22c55e; border: 1px solid #22c55e; padding: 2px 8px; border-radius: 4px; font-size: 0.75rem; font-weight: bold; margin-left: 8px;'>🛑 回檔1.2倍ATR，停損出場</div>"
            else:
                status_html = f"<div style='background-color: rgba(96,165,250,0.1); color: #60a5fa; border: 1px solid #60a5fa; padding: 2px 8px; border-radius: 4px; font-size: 0.75rem; font-weight: bold; margin-left: 8px;'>🚀 獲利奔跑中</div>"
            
            pl_col = "#ef4444" if pl_pct >= 0 else "#22c55e"
            pl_bg = "rgba(239,68,68,0.1)" if pl_pct >= 0 else "rgba(34,197,94,0.1)"
            sign = "+" if pl_val > 0 else ""
            
            stock_link = f'href="/?stock={order["ticker"]}" target="_self"'
            
            with st.container(border=False):
                cards_html = f"<div style='background-color: {card_bg_global}; border: 1px solid {border_col}; border-radius: 12px; padding: 16px; margin-bottom: 14px; position: relative; overflow: hidden; box-shadow: 0 4px 6px rgba(0,0,0,0.1);'>"
                cards_html += f"<div style='display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 12px;'>"
                cards_html += f"<a {stock_link} class='stock-card-link' style='flex: 1;'>"
                cards_html += f"<div style='display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; margin-bottom: 4px;'>"
                cards_html += f"<span style='color: {title_c_global}; font-weight: bold; font-size: 1.25rem;'>{order['name']}</span>"
                cards_html += f"<span style='color: #64748b; font-family: monospace; font-size: 0.9rem;'>{order['ticker']}</span>"
                cards_html += status_html
                cards_html += f"</div>"
                cards_html += f"<div style='font-size: 0.75rem; color: #64748b;'>板塊: {order.get('industry', '未知')} | 下單時間: {order['time']}</div>"
                cards_html += f"</a>"
                cards_html += f"<div style='text-align: right; flex-shrink: 0;'>"
                cards_html += f"<div style='font-size: 0.8rem; color: #94a3b8; margin-bottom: 2px;'>最新現價 / 報酬率</div>"
                cards_html += f"<div style='font-size: 1.3rem; font-weight: bold; font-family: monospace; color: {pl_col}; line-height: 1.1;'>{curr_price:.1f}</div>"
                cards_html += f"<div style='font-size: 0.85rem; font-weight: bold; font-family: monospace; color: {pl_col}; background-color: {pl_bg}; padding: 2px 6px; border-radius: 4px; display: inline-block; margin-top: 4px;'>{sign}{pl_pct:.2f}%</div>"
                cards_html += f"</div></div>"
                
                cards_html += f"<div style='display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; background-color: rgba(0,0,0,0.2); border: 1px solid rgba(255,255,255,0.05); padding: 10px; border-radius: 8px;'>"
                cards_html += f"<div style='display: flex; flex-direction: column; align-items: center;'>"
                cards_html += f"<span style='font-size: 0.7rem; color: #64748b; margin-bottom: 4px;'>買進成本</span>"
                cards_html += f"<span style='font-size: 1rem; font-weight: bold; color: {text_col}; font-family: monospace;'>{order['buy_price']:.1f}</span>"
                cards_html += f"</div>"
                cards_html += f"<div style='display: flex; flex-direction: column; align-items: center;'>"
                cards_html += f"<span style='font-size: 0.7rem; color: #64748b; margin-bottom: 4px;'>創高紀錄</span>"
                cards_html += f"<span style='font-size: 1rem; font-weight: bold; color: #facc15; font-family: monospace;'>{order['highest_price']:.1f}</span>"
                cards_html += f"</div>"
                cards_html += f"<div style='display: flex; flex-direction: column; align-items: center;'>"
                cards_html += f"<span style='font-size: 0.7rem; color: #64748b; margin-bottom: 4px;'>動態防護線</span>"
                cards_html += f"<span style='font-size: 1rem; font-weight: bold; color: #34d399; font-family: monospace;'>{max(ma10, dynamic_stop):.1f}</span>"
                cards_html += f"</div>"
                cards_html += f"<div style='display: flex; flex-direction: column; align-items: center;'>"
                cards_html += f"<span style='font-size: 0.7rem; color: #64748b; margin-bottom: 4px;'>風報比 (RRR)</span>"
                cards_html += f"<span style='font-size: 1rem; font-weight: bold; color: #facc15; font-family: monospace;'>1 : {order.get('rrr', 1.5)}</span>"
                cards_html += f"</div>"
                cards_html += f"</div>"
                st.markdown(cards_html, unsafe_allow_html=True)
                
                if st.button(f"❌ 刪除此單 ({order['name']})", key=f"btn_del_{order['id']}_{idx}", help="刪除這筆模擬交易紀錄"):
                    st.session_state.delete_order_id = order['id']
                    st.rerun()
                st.markdown("</div>", unsafe_allow_html=True)

# ==========================================
# 🚀 進入單一個股解析頁面
# ==========================================
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
        if p_stk and st.button(f"⬅ 上一檔", use_container_width=True, key="btn_prev_stock_nav"): st.session_state.update({"current_stock": p_stk}); st.rerun()
    with c2:
        if st.button("🏠 回雷達總機", use_container_width=True, key="btn_go_home_nav"): st.session_state.page = "home"; st.rerun()
    with c3:
        if n_stk and st.button(f"下一檔 ➡", use_container_width=True, key="btn_next_stock_nav"): st.session_state.update({"current_stock": n_stk}); st.rerun()

    def set_view_days(days): st.session_state.view_days = days

    load_ph = st.empty()
    pre_rendered_fig = None  

    with load_ph.container():
        st.markdown(f"<h4 style='text-align:center;'>🚀 正在喚醒【{target} {c_name}】AI 雙引擎分析大腦...</h4>", unsafe_allow_html=True)
        p_bar = st.progress(0)
        
        df_chart = get_stock_data(target) 
        p_bar.progress(30)

        if df_chart is not None:
            df_slice = df_chart.iloc[:len(df_chart) + st.session_state.date_offset] if st.session_state.date_offset < 0 else df_chart
            if len(df_slice) >= 14:
                inst_data = get_institutional_trading(target)
                p_bar.progress(50)
                data = analyze_today(df_slice, target, inst_data, is_light_mode)
                sc = data['Score']
                f_data = get_fundamental_and_industry_data(target, data['收盤價'])
                p_bar.progress(70)
                
                win_rate, win_rate_240, closed_signals, s_count, buy_dates = calculate_historical_winrate(target, df_cached=df_slice, fund_cached=f_data)
                
                current_show_buy = st.session_state.get('toggle_buy_sig_ch', True)
                current_show_sup = st.session_state.get('toggle_sup_res_ch', True)
                current_show_signals = st.session_state.get('toggle_signals_ch', True)
                pre_rendered_fig = draw_professional_chart(df_slice, target, data['收盤價'], st.session_state.view_days, is_light_mode, current_show_buy, f_data, current_show_sup, current_show_signals, buy_dates=buy_dates)
                p_bar.progress(100)
                time.sleep(0.1) 
        else:
            load_ph.empty()
            st.error("查無此股票資料。")

    if df_chart is not None and len(df_slice) >= 14:
        load_ph.empty()
        
        display_time = get_stock_live_time(target)
        p_color = '#ef4444' if data['漲跌'] >= 0 else '#22c55e'
        analysis_date = df_slice.index[-1].strftime('%Y/%m/%d')
        
        st.markdown(f"<h2 style='text-align: center; margin-bottom: 5px;'>🎯 {target} {c_name}</h2>", unsafe_allow_html=True)
        st.markdown(f"<div style='text-align: center; color: #888; font-size: 1.1rem;'>【{f_data['Industry']}】</div>", unsafe_allow_html=True)
        st.markdown(f"<h3 style='text-align: center; color: {p_color}; font-size: 2.2rem; margin-bottom: 0px;'>{data['收盤價']} ({'+' if data['漲跌']>0 else ''}{data['漲跌幅']}%)</h3>", unsafe_allow_html=True)
        st.markdown(f"<div style='text-align: center; color: #888; font-size: 1rem; margin-top: 5px;'>昨日收盤: {data['昨日收盤價']} | 最新報價: {data['收盤價']}</div>", unsafe_allow_html=True)
        st.markdown(f"<div style='text-align: center; color: #888; font-size: 0.9rem; margin-bottom: 10px;'>🕒 盤勢分析日期: {analysis_date} | 抓取時間: {display_time}</div>", unsafe_allow_html=True)
        
        _, up_c, _ = st.columns([1, 2, 1])
        if up_c.button("🔄 更新個股即時數值", use_container_width=True, key="btn_refresh_stock_data"): st.cache_data.clear(); st.rerun()
        st.markdown("---")
        
        st.markdown("##### 📊 ATR 動態勝率雙週期回測 (T+1日模擬，已扣交易成本)")
        
        wr_color_90 = "#ef4444" if win_rate >= 75 else ("#facc15" if win_rate >= 40 else "#22c55e")
        wr_color_240 = "#ef4444" if win_rate_240 >= 75 else ("#facc15" if win_rate_240 >= 40 else "#22c55e")
        
        with st.container(border=True):
            col_sum1, col_sum2, col_sum3 = st.columns(3)
            with col_sum1: st.markdown(f"<div style='text-align:center; color:#888; font-size:0.9rem;'>近一季勝率 (短線股性)<br><span style='color:{wr_color_90}; font-size:1.8rem; font-weight:900;'>{win_rate:.1f}%</span></div>", unsafe_allow_html=True)
            with col_sum2: st.markdown(f"<div style='text-align:center; color:#888; font-size:0.9rem;'>近一年勝率 (多空耐震)<br><span style='color:{wr_color_240}; font-size:1.8rem; font-weight:900;'>{win_rate_240:.1f}%</span></div>", unsafe_allow_html=True)
            with col_sum3: st.markdown(f"<div style='text-align:center; color:#888; font-size:0.9rem;'>歷史觸發總次數<br><span style='font-size:1.8rem; font-weight:900; color:#60a5fa;'>{closed_signals} 次</span></div>", unsafe_allow_html=True)
            
            if closed_signals == 0:
                summary_text = "過去 90 日內尚未產生足夠的歷史買進訊號。"
            else:
                summary_text = f"過去 90 日內共觸發 **{closed_signals}** 次有效買點。模擬以訊號隔日開盤價買入，並**強制扣除 0.585% 的手續費與交易稅摩擦成本**。扣除成本後之淨波段勝率達 <span style='color:{wr_color_90}; font-weight:bold;'>{win_rate:.1f}%</span>。"
            
            st.markdown(f"<div style='margin-top:12px; padding:12px; background-color:rgba(30,41,59,0.5); border-radius:8px; line-height: 1.6; font-size:0.95rem; color:#cbd5e1;'>📝 <b>回測總結：</b>{summary_text}</div>", unsafe_allow_html=True)

        ai_html = generate_comprehensive_analysis(data, inst_data, sc, f_data, is_light_mode=is_light_mode)
        
        v_c = "#22c55e" if sc < 40 else ("#facc15" if sc < 75 else "#ef4444")
        v_t = "🔴 空手觀望" if sc < 40 else ("🟡 A級試單" if sc < 75 else "🟢 S級強烈買進")
        
        macro_risk = st.session_state.get('macro_risk', 50)
        macro_warning = ""
        if macro_risk >= 75:
            macro_warning = f"<div style='background-color: rgba(239,68,68,0.2); color: #ef4444; padding: 10px; margin-bottom: 10px; border-left: 4px solid #ef4444;'>🚨 <b>系統保護觸發</b>：目前大盤系統量化風險度高達 {macro_risk}%，屬於極度警戒區。為避免覆巢之下無完卵，系統已強制取消所有 S/A 級買進建議，轉為觀望。</div>"
            
        st.markdown(f"""
        {macro_warning}
        <div style="border: 2px solid {v_c}; border-radius: 10px; padding: 20px; margin-bottom: 20px; background-color: #0b1120;">
            <h3 style="text-align: center; color: {v_c}; margin-top: 0; font-size: 1.8rem; margin-bottom: 20px;">🤖 雙引擎決策大腦 (綜合得分: {sc})：{v_t.replace('🟢 ', '').replace('🟡 ', '').replace('🔴 ', '')}</h3>
            <div style="background-color: rgba(30,41,59,0.5); padding: 15px; border-radius: 8px; border-left: 5px solid {v_c}; margin-bottom:20px;">
                <p style="font-size: 1.15rem; color: #f8fafc; margin: 0; line-height: 1.6;">
                    ✅ <b>進階 ATR 目標精算</b><br>
                    依據真實波動率計算，合理停利目標為 <b style='color:#ef4444;'>{data['ATR_Target']}</b> ({data['ATR_Target_Pct']:.1f}%)，嚴格停損設於 <b style='color:#22c55e;'>{data['ATR_Stop']}</b> ({data['ATR_Stop_Pct']:.1f}%)。<br>
                    風報比 (Risk-Reward) 為 <b>1 : {data['RRR']}</b>。
                </p>
            </div>
            {ai_html}
        </div>""", unsafe_allow_html=True)
        
        if st.button("🛒 執行模擬下單 (套用最新移動停利引擎)", use_container_width=True):
            new_order = {
                "id": str(int(time.time())),
                "ticker": target,
                "name": c_name,
                "industry": f_data.get('Industry', '未知'),
                "buy_price": data['收盤價'],
                "highest_price": data['收盤價'],
                "target_price": data['ATR_Target'],
                "stop_price": data['ATR_Stop'],
                "rrr": data['RRR'],
                "time": datetime.now(timezone(timedelta(hours=8))).strftime('%Y/%m/%d %H:%M:%S')
            }
            st.session_state.simulated_orders.insert(0, new_order)
            save_json(SIM_FILE, st.session_state.simulated_orders)
            
            st.success(f"✅ **模擬下單設定成功！** 已在 **{data['收盤價']}** 元虛擬買進 {target} {c_name}。\n\n"
                       f"👉 **已啟動「讓獲利奔跑」移動防護引擎！** 請從左側選單進入「模擬交易中心」隨時追蹤即時報酬率！")
            st.balloons()
        
        dc1, dc2, dc3, dc5, dc6, dc7 = st.columns([0.8, 0.8, 0.8, 1.3, 1.3, 1.3])
        dc1.button("30日", on_click=set_view_days, args=(30,), key="btn_view_30d")
        dc2.button("60日", on_click=set_view_days, args=(60,), key="btn_view_60d")
        dc3.button("90日", on_click=set_view_days, args=(90,), key="btn_view_90d")
        with dc5: st.toggle("🛒 顯示買進", value=True, key='toggle_buy_sig_ch')
        with dc6: st.toggle("📏 歷史高低點", value=True, key='toggle_sup_res_ch')
        with dc7: st.toggle("🏷️ 顯示符號", value=True, key='toggle_signals_ch')
            
        if pre_rendered_fig is not None:
            st.plotly_chart(pre_rendered_fig, use_container_width=True, config={'displayModeBar': False, 'scrollZoom': False})
        
        with st.expander("📖 K 線圖符號代表名稱說明 (點擊展開)", expanded=False):
            st.markdown(f"""
            <ul style="line-height: 1.8; color: {text_col}; font-size: 1rem;">
                <li><b><span style='color: #ef4444;'>紅吞</span></b>：代表強烈的短線反轉向上買進訊號。</li>
                <li><b><span style='color: #22c55e;'>黑吞</span></b>：代表強烈的短線高檔反轉向下警訊。</li>
                <li><b><span style='color: #facc15;'>撐 (橘黃字)</span></b>：回測有撐，當日價格下殺後爆出買盤收長下影線，主力防守支撐。</li>
                <li><b><span style='color: #60a5fa;'>壓 (藍字)</span></b>：反彈遇壓，當日反彈遭遇均線壓力被打回，收出長上影線。</li>
                <li><b><span style='color: #34d399;'>買 (青色指標)</span></b>：由系統 V2.0 AI 綜合動能、型態與籌碼精算之「波段試單買點」。</li>
            </ul>
            """, unsafe_allow_html=True)
        
        st.divider()
        st.subheader("⭐ 自選群組管理")
        all_groups = list(st.session_state.fav_groups.keys())
        current_groups = [g for g, s in st.session_state.fav_groups.items() if target in s]
        selected_groups = st.multiselect("將此標的加入以下群組：", options=all_groups, default=current_groups, key="multiselect_fav_groups")
        if st.button("💾 儲存自選設定", use_container_width=True, type="primary", key="btn_save_fav_groups"):
            for g in all_groups:
                if g in selected_groups and target not in st.session_state.fav_groups[g]: st.session_state.fav_groups[g].append(target)
                elif g not in selected_groups and target in st.session_state.fav_groups[g]: st.session_state.fav_groups[g].remove(target)
            save_json(FAV_GROUPS_FILE, st.session_state.fav_groups)
            st.success("✅ 群組設定已更新！")
            st.rerun()

        st.divider()
        st.markdown(f'''<div style="font-size: 1.4rem; font-weight: bold; color: #facc15; margin-bottom: 16px;"><i class='fa-solid fa-list'></i> 同步監控雷達清單 (首頁快篩結果)</div>''', unsafe_allow_html=True)
        
        if n_pool and 'nav_pool_data' in st.session_state:
            nav_data = st.session_state.nav_pool_data
            if nav_data: 
                df_nav = pd.DataFrame(nav_data)
                if 'ticker_raw' in df_nav.columns:
                    df_nav = df_nav[df_nav['ticker_raw'] != target]
                    if not df_nav.empty:
                        is_intra = st.session_state.get('is_intraday', True)
                        bottom_cards_html = generate_cards_html(df_nav, is_intra)
                        st.markdown(bottom_cards_html, unsafe_allow_html=True)
                    else:
                        st.info("目前清單中已無其他符合條件的標的。")
                else:
                    st.info("目前清單中無效的標的資料。")
            else:
                st.info("目前清單中已無其他符合條件的標的。")
        else:
            st.info("目前無掃描名單暫存。請先返回首頁執行雷達快篩。")
