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

from streamlit_autorefresh import st_autorefresh

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
is_light_mode = st.sidebar.toggle("🌞 黑白底色切換", False)

if st.sidebar.button("🗑️ 強制清除快取資料", use_container_width=True):
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

css_style = """
<style>
    .stApp { background-color: """ + app_bg + """; }
    #MainMenu {visibility: hidden;} footer {visibility: hidden;}
    [data-testid="collapsedControl"] { border: 1px solid """ + border_col + """ !important; border-radius: 8px !important; background-color: """ + bg_col + """ !important; padding: 5px 12px !important; display: flex !important; align-items: center !important; width: auto !important; transition: 0.3s; }
    [data-testid="collapsedControl"]::after { content: " ⭐ 我的群組"; font-size: 1.1rem; font-weight: bold; color: #ffcc00; margin-left: 8px; }
    .stButton button { font-weight: bold !important; border-radius: 8px !important; text-align: left !important; }
    button[kind="primary"] { font-size: 1.5rem !important; padding: 15px !important; border-radius: 12px !important; background-color: #ffcc00 !important; color: #111 !important; border: none !important; }
    .sticky-header { position: sticky; top: 0; z-index: 999; background-color: """ + sticky_bg + """; padding: 10px 0; border-bottom: 1px solid """ + border_col + """; backdrop-filter: blur(5px); margin-top: -15px; margin-bottom: 15px; }
    div[data-testid="stVerticalBlockBorderWrapper"] > div { background-color: """ + bg_col + """ !important; border-color: """ + border_col + """ !important; padding: 4px !important; }
    h1, h2, h3, h4, p, span { color: """ + title_col + """ !important; }
    .compact-btn button { padding: 0.25rem 0.5rem !important; font-size: 1rem !important; }
    .risk-bar-container { width: 100%; background-color: #333; border-radius: 8px; margin-top: 5px; margin-bottom: 15px; overflow: hidden; }
    .risk-bar-fill { height: 16px; border-radius: 8px; transition: width 0.5s ease-in-out; }
    .tick-buy { color: #ff3333; font-weight: bold; }
    .tick-sell { color: #00cc00; font-weight: bold; }
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

with st.sidebar.form(key="search_form", clear_on_submit=True):
    search_input = st.text_input("隱藏", placeholder="輸入股票代號或中文名稱...", label_visibility="collapsed")
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
auto_refresh = st.sidebar.toggle("🟢 開啟即時自動更新 (每30秒)", False)

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
if 'nav_pool' not in st.session_state: st.session_state.nav_pool = st.session_state.custom_pool
if 'scan_mode' not in st.session_state: st.session_state.scan_mode = "buy"
if 'view_days' not in st.session_state: st.session_state.view_days = 30
if 'date_offset' not in st.session_state: st.session_state.date_offset = 0

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

if 'recent_searches' not in st.session_state:
    st.session_state.recent_searches = []

@st.cache_data(ttl=15, show_spinner=False)
def fetch_live_tick_data(ticker):
    base_ticker = str(ticker).strip().upper().replace(".TW", "").replace(".TWO", "")
    fallback = {"ticks": [], "ask_ratio": 50.0, "bid_ratio": 50.0, "total_volume": 0}
    
    try:
        url = f"https://www.wantgoo.com/invest/get-realtime-ticks?stockNo={base_ticker}"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': f'https://www.wantgoo.com/stock/{base_ticker}',
            'Accept': 'application/json'
        }
        res = requests.get(url, headers=headers, timeout=3)
        if res.status_code == 200:
            json_data = res.json()
            raw_ticks = json_data.get('ticks', [])
            if raw_ticks:
                raw_ticks = raw_ticks[::-1]
                processed_ticks = []
                ask_vol, bid_vol = 0, 0
                for t in raw_ticks[:30]: 
                    t_time = t.get('time', '')
                    t_price = float(t.get('price', 0))
                    t_vol = int(t.get('volume', 0))
                    t_type = t.get('type', 'mid') 
                    
                    if t_type == 'buy': ask_vol += t_vol
                    elif t_type == 'sell': bid_vol += t_vol
                    
                    processed_ticks.append({
                        "時間": t_time, "價格": t_price, "現量": t_vol, "屬性": "外盤(買進)" if t_type=='buy' else ("內盤(賣出)" if t_type=='sell' else "平盤")
                    })
                    
                total_v = ask_vol + bid_vol
                ask_r = round((ask_vol / total_v) * 100, 1) if total_v > 0 else 50.0
                bid_r = round((bid_vol / total_v) * 100, 1) if total_v > 0 else 50.0
                
                return {
                    "ticks": processed_ticks[:5],
                    "ask_ratio": ask_r, "bid_ratio": bid_r, "total_volume": total_v
                }
    except: pass

    try:
        yf_ticker = f"{base_ticker}.TW"
        df = yf.Ticker(yf_ticker).history(period="1d", interval="1m")
        if df.empty:
            yf_ticker = f"{base_ticker}.TWO"
            df = yf.Ticker(yf_ticker).history(period="1d", interval="1m")
            
        if not df.empty:
            df = df.tail(5).iloc[::-1]
            processed_ticks = []
            ask_vol, bid_vol = 0, 0
            for idx, row in df.iterrows():
                t_time = idx.strftime('%H:%M:%S')
                t_price = float(row['Close'])
                t_vol = max(1, int(row['Volume'] / 1000))
                
                is_buy = row['Close'] >= row['Open']
                t_type = 'buy' if is_buy else 'sell'
                
                if t_type == 'buy': ask_vol += t_vol
                else: bid_vol += t_vol
                
                processed_ticks.append({
                    "時間": t_time, "價格": round(t_price, 2), "現量": t_vol, "屬性": "外盤(買進)" if is_buy else "內盤(賣出)"
                })
            
            total_v = ask_vol + bid_vol
            ask_r = round((ask_vol / total_v) * 100, 1) if total_v > 0 else 50.0
            bid_r = round((bid_vol / total_v) * 100, 1) if total_v > 0 else 50.0
            return {
                "ticks": processed_ticks,
                "ask_ratio": ask_r, "bid_ratio": bid_r, "total_volume": total_v
            }
    except: pass

    return fallback

@st.cache_data(ttl=1800)
def fetch_twse_top_100():
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", timeout=10)
        df = pd.DataFrame(res.json())
        df['TradeVolume'] = pd.to_numeric(df['TradeVolume'], errors='coerce')
        return df[df['Code'].str.match(r'^\d{4}$')].sort_values(by='TradeVolume', ascending=False).head(100)['Code'].tolist()
    except: return ["2330", "2317", "2454", "2382", "3231"]

@st.cache_data(ttl=15, show_spinner=False) 
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
        info = yf.Ticker(f"{base_ticker}.TW").info
        if not info or 'sector' not in info:
            info = yf.Ticker(f"{base_ticker}.TWO").info

        sec, ind_eng = info.get("sector", ""), info.get("industry", "")
        tw_sec = ENG_TO_TW_INDUSTRY.get(sec, sec)
        tw_ind = ENG_TO_TW_INDUSTRY.get(ind_eng, ind_eng)
        ind_temp = f"{tw_sec} - {tw_ind}" if tw_sec and tw_ind else tw_sec or tw_ind or "一般產業"
        if not re.search(r'[a-zA-Z]', ind_temp): ind = ind_temp

        if 'trailingEps' in info and info['trailingEps'] is not None:
            eps_val = str(round(info['trailingEps'], 2))
        if 'trailingPE' in info and info['trailingPE'] is not None:
            pe_val = str(round(info['trailingPE'], 2))
    except: pass

    if eps_val == "無":
        try:
            res_api = requests.get(f"https://ws.cnyes.com/twstock/api/v1/company/profile/{base_ticker}", timeout=3)
            if res_api.status_code == 200:
                data = res_api.json()
                if 'data' in data and 'eps' in data['data']:
                    eps_val = f"{float(data['data']['eps']):.2f}"
        except: pass

    if pe_val == "無" and eps_val != "無":
        try:
            eps_f = float(eps_val)
            if eps_f > 0 and current_price > 0:
                pe_val = str(round(float(current_price) / eps_f, 2))
            elif eps_f <= 0:
                pe_val = "無 (EPS ≦ 0)"
        except: pass

    return {"EPS": eps_val, "PE": pe_val, "Industry": ind}

@st.cache_data(ttl=15, show_spinner=False) 
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

# 🎯 雙層極速備援：台指期近月 (包含日夜盤支援)
@st.cache_data(ttl=15, show_spinner=False)
def get_futures_quote():
    tz_tpe = timezone(timedelta(hours=8))
    fallback_curr, fallback_change, update_time_str = 0, 0, "暫無資料"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'X-Requested-With': 'XMLHttpRequest'
    }

    # 來源 1: 玩股網 API (使用 %26 代表 URL 編碼的 &，取得台指期連續盤)
    try:
        res = requests.get("https://www.wantgoo.com/invest/get-quote?StockNo=WTX%26", headers=headers, timeout=3)
        if res.status_code == 200:
            data = res.json()
            curr = float(data.get('price', 0))
            change = float(data.get('change', 0))
            dt_str = data.get('time', '')
            if curr > 0:
                now_date = datetime.now(tz_tpe).strftime('%Y/%m/%d')
                return curr, change, f"即時 ({now_date} {dt_str})"
    except: pass

    # 來源 2: 玩股網 API (備用，日盤 WTX)
    try:
        res = requests.get("https://www.wantgoo.com/invest/get-quote?StockNo=WTX", headers=headers, timeout=3)
        if res.status_code == 200:
            data = res.json()
            curr = float(data.get('price', 0))
            change = float(data.get('change', 0))
            dt_str = data.get('time', '')
            if curr > 0:
                now_date = datetime.now(tz_tpe).strftime('%Y/%m/%d')
                return curr, change, f"即時 ({now_date} {dt_str})"
    except: pass

    # 來源 3: Yahoo Finance API (TWF=F 備用)
    try:
        tk = yf.Ticker("TWF=F")
        hist = tk.history(period="1d", interval="1m")
        if not hist.empty:
            curr = float(hist['Close'].iloc[-1])
            try:
                prev = float(tk.fast_info.previous_close)
            except:
                prev = float(hist['Open'].iloc[0])
            ts = hist.index[-1].timestamp()
            update_time_str = datetime.fromtimestamp(ts, tz_tpe).strftime('%Y/%m/%d %H:%M')
            return curr, curr - prev, update_time_str
    except: pass

    return fallback_curr, fallback_change, update_time_str

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
        url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInstitutionalInvestorsBuySell&data_id={ticker}&start_date={start_date}"
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

@st.cache_data(ttl=60, show_spinner=False)
def get_global_macro_data():
    tz_tpe = timezone(timedelta(hours=8))
    fetch_time = datetime.now(tz_tpe).strftime('%Y/%m/%d %H:%M:%S')
    data = {"global_time": fetch_time}
    tickers = {
        "^SOX": "https://finance.yahoo.com/quote/^SOX",
        "^VIX": "https://finance.yahoo.com/quote/^VIX",
        "JPY=X": "https://finance.yahoo.com/quote/JPY=X"
    }
    
    latest_ts = 0
    latest_time_str = "無資料"
    success_count = 0
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    for t, url in tickers.items():
        success = False
        try:
            api_url = f"https://query2.finance.yahoo.com/v8/finance/chart/{t}?interval=1d&range=1d"
            res = requests.get(api_url, headers=headers, timeout=3)
            if res.status_code == 200:
                meta = res.json()['chart']['result'][0]['meta']
                c = float(meta['regularMarketPrice'])
                p = float(meta.get('previousClose', meta.get('chartPreviousClose', c)))
                ts = int(meta['regularMarketTime'])
                
                time_str = datetime.fromtimestamp(ts, tz_tpe).strftime('%Y/%m/%d %H:%M')
                data[t] = {"price": c, "pct": (c-p)/p*100 if p != 0 else 0, "time": time_str, "url": url}
                success_count += 1
                success = True
                
                if ts > latest_ts:
                    latest_ts = ts
                    latest_time_str = time_str
        except: pass

        if not success:
            try:
                df = yf.Ticker(t).history(period="5d")
                if df is not None and not df.empty:
                    df = df.dropna(subset=['Close'])
                    if len(df) >= 2:
                        c = float(df['Close'].iloc[-1])
                        p = float(df['Close'].iloc[-2])
                        last_dt = df.index[-1]
                        time_str = last_dt.strftime('%Y/%m/%d')
                        
                        data[t] = {"price": c, "pct": (c-p)/p*100 if p != 0 else 0, "time": time_str, "url": url}
                        success_count += 1
                        
                        if last_dt.timestamp() > latest_ts:
                            latest_ts = last_dt.timestamp()
                            latest_time_str = time_str
            except: pass
            
        if t not in data:
            data[t] = {"price": 0, "pct": 0, "time": "暫無資料", "url": url}
            
    data['global_time'] = latest_time_str if latest_time_str != "無資料" else fetch_time

    if success_count > 0:
        try:
            with open(MACRO_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except: pass
        return data
    else:
        try:
            if os.path.exists(MACRO_CACHE_FILE):
                with open(MACRO_CACHE_FILE, "r", encoding="utf-8") as f:
                    cache_data = json.load(f)
                    cache_data['global_time'] = cache_data.get('global_time', '') + " (快取)"
                    return cache_data
        except: pass

    return data


# =========================================================================================
# 🌟 核心引擎：三大主力特徵 + Veto System 🌟
# =========================================================================================
def get_decision_score(data, fund_data, inst_data=None, tick_data=None):
    sc, rs = 0, []
    
    if data['訊號']: sc+=3; rs.append("✅ 穩在月線上且KDJ超賣")
    if data['收盤價'] <= data['BB_DN'] * 1.02: sc+=2; rs.append("✅ 觸及布林下軌支撐")
    if data['BIAS'] < -5: sc+=1; rs.append("✅ 負乖離過大")
    
    try: eps_f = float(str(fund_data['EPS']).replace(',', ''))
    except: eps_f = 0.0
    if eps_f > 0: sc+=2; rs.append("✅ 基本面獲利")
    
    vol = data.get('成交量', 0)
    vol_5ma = data.get('5日均量', 1)
    is_red_k = data['收盤價'] > data.get('Open', data['收盤價'])
    
    # 🎯 偵測 1：量能點火 (主力初動特徵之一)
    if vol > vol_5ma * 2 and is_red_k and data['BIAS'] < 5:
        sc += 2
        rs.append(f"🔥 量能點火 (成交量達均量 {round(vol/max(vol_5ma, 1), 1)} 倍，主力資金進駐)")
        data['量能點火'] = True
    elif vol > vol_5ma * 1.2:
        sc += 1
        rs.append(f"✅ 量能溫和放大 (達均量 {round(vol/max(vol_5ma, 1), 1)} 倍)")
    elif vol < vol_5ma:
        sc -= 1
        rs.append("⚠️ 量能萎縮 (缺乏追價動能)")
        
    if data.get('MACD柱', 0) > data.get('前日MACD柱', -999): sc+=2; rs.append("✅ MACD 綠柱收斂或紅柱放大 (動能防禦過關)")
    else: sc-=3; rs.append("⚠️ MACD 空方動能持續擴大 (型態脆弱嚴防接刀)")

    # 🎯 偵測 2：均線糾結突破 (主力初動特徵之二)
    ma5, ma10, ma20 = data['5MA'], data['10MA'], data['20MA']
    ma_max = max(ma5, ma10, ma20)
    ma_min = min(ma5, ma10, ma20)
    ma_spread = (ma_max - ma_min) / ma_min * 100 if ma_min > 0 else 0
    if ma_spread < 2.5 and data['收盤價'] > ma_max and data.get('昨日收盤價', data['收盤價']) < ma_max:
        sc += 3
        rs.append("🔥 均線糾結突破 (5T/10T/20T收斂後表態上攻，強烈起漲訊號)")
        data['均線突破'] = True

    # 🎯 偵測 3：法人潛伏 (土洋合買高佔比 或 連續默默吃貨)
    if inst_data and len(inst_data) >= 1:
        t0 = inst_data[0]
        f_t0 = int(str(t0['外資(張)']).replace(',', ''))
        t_t0 = int(str(t0['投信(張)']).replace(',', ''))
        if f_t0 > 0 and t_t0 > 0:
            total_inst = f_t0 + t_t0
            ratio = (total_inst / vol) * 100 if vol > 0 else 0
            if ratio >= 10.0:
                sc += 3
                rs.append(f"🤝 土洋合買高度集中 (兩大法人齊買，佔總成交量 {ratio:.1f}%)")
                data['法人潛伏'] = True
            else:
                sc += 1
                rs.append("✅ 土洋合買 (外資與投信同步站在買方)")
        elif f_t0 < 0 and t_t0 < 0:
            sc -= 2
            rs.append("🩸 土洋雙殺 (外資與投信同步賣超，留意籌碼鬆動)")
            
    if inst_data and len(inst_data) >= 2:
        t0, t1 = inst_data[0], inst_data[1]
        f_t0 = int(str(t0['外資(張)']).replace(',', ''))
        f_t1 = int(str(t1['外資(張)']).replace(',', ''))
        t_t0 = int(str(t0['投信(張)']).replace(',', ''))
        t_t1 = int(str(t1['投信(張)']).replace(',', ''))
        
        if t_t0 > 0 and t_t1 <= 0:
            sc += 2; rs.append("🔥 投信由賣轉買 (主力發動第一天，極具爆發潛力)")
        elif t_t0 > 0:
            sc += 1; rs.append(f"✅ 投信連續偏多 (今日買超 {t_t0} 張)")
            
        if f_t0 > 0 and f_t1 <= 0:
            sc += 1; rs.append("🔥 外資由賣轉買 (大資金回補)")
        elif f_t0 > 0:
            sc += 1; rs.append(f"✅ 外資連續偏多 (今日買超 {f_t0} 張)")
            
        if abs(data.get('漲跌幅', 0)) < 2.5 and vol <= vol_5ma * 1.2:
            if (t_t0 > 0 and t_t1 > 0) or (f_t0 > 0 and f_t1 > 0):
                sc += 2
                rs.append("🤝 法人價平量縮潛伏 (底部偷偷吃貨特徵)")
                data['法人潛伏'] = True

    if data.get('紅吞'): sc+=3; rs.append("🔥 出現「紅吞」反轉型態 (強烈多頭買進訊號)")
    if data.get('回測有撐'): sc+=2; rs.append("🔥 帶量長下影線 (主力回測支撐成功)")
    
    if data['收盤價'] >= data['5MA'] and data.get('5日線即將上彎'): 
        sc+=2; rs.append("🔥 5日線扣低值 (短均線準備上彎發散)")
        
    if data['5MA'] >= data['10MA']: sc += 2

    if tick_data and tick_data.get('total_volume', 0) > 0:
        ask_r = tick_data.get('ask_ratio', 50)
        if ask_r > 55.0: sc+=3; rs.append(f"🔥 盤中買氣極強 (外盤比高達 {ask_r}%)")

    fatal_risk = False

    max_support = max(data['5MA'], data['10MA'], data['20MA'])
    if data['收盤價'] > max_support * 1.03:
        sc -= 5
        rs.append("🚨 【Veto 價格防護】現價已遠超合理建倉成本區(均線上3%)，追高勝率低，強制取消買點！")
        fatal_risk = True
    
    if data['J值'] >= 85 or data['BIAS'] > 8 or data['收盤價'] >= data['BB_UP'] * 0.98:
        sc -= 10
        rs.append("🚨 【Veto 一票否決】高檔過熱防護：J值過高或正乖離過大，追高極易套牢，強制降級！")
        fatal_risk = True

    if data.get('黑吞') or data.get('反彈遇壓'):
        sc -= 10
        rs.append("🚨 【Veto 一票否決】空方型態防護：出現黑吞或帶量長上影線，主力出貨風險極高，強制降級！")
        fatal_risk = True

    if data['收盤價'] < data['20MA'] and not data.get('紅吞') and data.get('成交量', 0) < data.get('5日均量', 0):
        sc -= 8
        rs.append("🚨 【Veto 一票否決】破線接刀防護：股價跌破月線且呈無量下跌，嚴防續跌，禁止做多！")
        fatal_risk = True
        
    if data['5MA'] < data['10MA']: sc -= 2 
    
    if eps_f < 0: sc -= 1; rs.append("⚠️ 基本面虧損")
    if tick_data and tick_data.get('bid_ratio', 50) > 55.0: sc-=3; rs.append(f"🩸 盤中賣壓沉重 (內盤比高達 {tick_data.get('bid_ratio')}%)")

    if fatal_risk and sc >= 3:
        sc = 2 

    return sc, rs

def analyze_today(df, ticker_number, inst_data=None, tick_data=None):
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
        "Open": t_open, 
        "5MA": round(t['5MA'], 2), "10MA": round(t['10MA'], 2), "20MA": round(t['20MA'], 2),
        "60MA": round(t['60MA'], 2),
        "BB_UP": round(t['BB_UP'], 2), "BB_DN": round(t['BB_DN'], 2), "BIAS": round(t['BIAS_20'], 2),
        "MACD": round(t['MACD'], 2), "MACD柱": round(t['MACD_Hist'], 3), "前日MACD柱": round(p['MACD_Hist'], 3),
        "K": round(t['K'], 2), "D": round(t['D'], 2), "J值": round(t['J'], 2),
        "訊號": (t_close > t['20MA']) and (t_close < t['5MA']) and (t['J'] < 20),
        "紅吞": is_red_engulfing, "黑吞": is_black_engulfing,
        "近七日紅吞": recent_7_red,
        "回測有撐": is_support_pullback,
        "反彈遇壓": is_resistance_rejection,
        "5日線即將上彎": is_ma5_turning_up,
        "季線即將上彎": is_ma60_turning_up
    }
    
    sc, rs = get_decision_score(data, fund, inst_data, tick_data)
    data['Score'] = sc
    data['Reasons'] = rs
    
    data['評級'] = "🟢 S級" if sc >= 7 else ("🟡 A級" if sc >= 3 else "⚪ 觀望")
    data['Vol_Ratio'] = round(data['成交量'] / max(1, data['5日均量']), 2)
    
    if "主力初動" not in data:
        data['主力初動'] = (data['Vol_Ratio'] >= 1.5) and (t_close > t_open) and (data['BIAS'] < 6.0)
    
    return data

# =========================================================================================
# 🌟 統一按鈕文字格式化引擎 (支援 榜單、近期搜尋、同產業) 🌟
# =========================================================================================
def format_stock_label_from_data(r, is_current=False):
    r_dict = dict(r) if hasattr(r, 'to_dict') else r
    p_val = r_dict.get('漲跌', 0)
    sign = "+" if p_val > 0 else ""
    trend_icon = "🔺" if p_val > 0 else ("🔻" if p_val < 0 else "➖")
    s_score = r_dict.get('Score', 0)
    score_icon = "🟢 S級" if s_score >= 7 else ("🟡 A級" if s_score >= 3 else "⚪ 觀望")
    
    tags = []
    if r_dict.get('紅吞') or r_dict.get('近七日紅吞'): 
        tags.append("紅吞")
    elif r_dict.get('黑吞'): 
        tags.append("黑吞")
    
    if r_dict.get('回測有撐'): 
        tags.append("📌撐")
    elif r_dict.get('反彈遇壓'): 
        tags.append("⚠️壓")
    
    tag_display = " | ".join(tags)
    if tag_display: tag_display = f" | {tag_display}"
    
    btn_prefix = "⭐ " if is_current else "▪️ "
    return f"{btn_prefix}{r_dict.get('代號', '')} {r_dict.get('名稱', '')} {trend_icon}{r_dict.get('收盤價', '')}({sign}{r_dict.get('漲跌幅', '')}%) | {score_icon}{tag_display}"

def get_dynamic_stock_label(ticker, nav_data_list, is_current=False):
    for item in nav_data_list:
        if item.get("ticker_raw") == ticker:
            return format_stock_label_from_data(item, is_current)
    
    c_name = get_stock_name(ticker)
    df_chart = get_stock_data(ticker)
    if df_chart is not None and len(df_chart) >= 5:
        t_data = analyze_today(df_chart, ticker, inst_data=None, tick_data=None)
        if t_data:
            return format_stock_label_from_data(t_data, is_current)
            
    btn_prefix = "⭐ " if is_current else "▪️ "
    return f"{btn_prefix}{ticker} {c_name}"

# 🎯 徹底更名快取避免舊資料打架 (升級 v4)
@st.cache_data(ttl=180, show_spinner=False)
def get_global_scan_results_v4(pool_tuple):
    scan_results = []
    def process_scan(stock):
        df = get_stock_data(stock)
        if df is not None: return analyze_today(df, stock, inst_data=None, tick_data=None)
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
    st.markdown("<h1 style='text-align: center;'>🇹🇼 雷達總機</h1>", unsafe_allow_html=True)
    render_index_board()
    
    st.markdown("<h3 style='margin-top: 15px;'>🎯 策略條件篩選</h3>", unsafe_allow_html=True)
    btn_col1, btn_col2, btn_col3 = st.columns(3)
    if btn_col1.button("✅ 綜合買點榜", use_container_width=True): st.session_state.scan_mode = "buy"; st.rerun()
    if btn_col2.button("🔥 紅吞反轉榜", use_container_width=True): st.session_state.scan_mode = "red_engulf"; st.rerun()
    if btn_col3.button("🕵️ 主力初動榜", use_container_width=True): st.session_state.scan_mode = "main_force"; st.rerun()
    
    top_100_pool = fetch_twse_top_100()
    pool = tuple(set(top_100_pool + st.session_state.custom_pool + list(STOCK_NAMES.keys())))
    
    with st.spinner("🚀 大腦背景資料庫存取中..."):
        scan_results = get_global_scan_results_v4(pool)
            
    if scan_results:
        pd.set_option('future.no_silent_downcasting', True)
        df_results = pd.DataFrame(scan_results)
        
        # 🎯 安全防護：保證 DataFrame 中絕對有 Vol_Ratio 欄位
        if 'Vol_Ratio' not in df_results.columns:
            if '成交量' in df_results.columns and '5日均量' in df_results.columns:
                df_results['Vol_Ratio'] = df_results['成交量'] / df_results['5日均量'].apply(lambda x: x if x > 0 else 1)
            else:
                df_results['Vol_Ratio'] = 0.0
        df_results['Vol_Ratio'] = df_results['Vol_Ratio'].fillna(0.0)
        
        if 'Score' not in df_results.columns: df_results['Score'] = 0
        if '主力初動' not in df_results.columns: df_results['主力初動'] = False
        if '近七日紅吞' not in df_results.columns: df_results['近七日紅吞'] = False
        
        df_results['Bullish_Count'] = df_results.apply(
            lambda r: (1 if r.get('紅吞') or r.get('近七日紅吞') else 0) + 
                      (1 if r.get('回測有撐') else 0) + 
                      (1 if r.get('5日線即將上彎') else 0), axis=1)

        if st.session_state.scan_mode == "main_force":
            st.markdown("##### 🕵️ 主力初動榜 (不限評級，優先以點火量能排序)")
            df_disp = df_results[df_results['主力初動'] == True].sort_values(
                by=['Vol_Ratio', '漲跌幅'], ascending=[False, False]
            ).head(20)
            if df_disp.empty: st.info("💡 目前雷達池內暫無符合「底部爆量收紅」的主力初動標的。")
        elif st.session_state.scan_mode == "red_engulf":
            st.markdown("##### 🔥 近七日觸發「紅吞」反轉型態標的 (S、A級)")
            df_disp = df_results[(df_results['近七日紅吞'] == True) & (df_results['Score'] >= 3)].sort_values(
                by=['Vol_Ratio', 'Score'], ascending=[False, False]
            ).head(20)
            if df_disp.empty: st.info("💡 目前雷達池內近七日內暫無符合「紅吞反轉型態」的強勢個股。")
        elif st.session_state.scan_mode == "buy":
            st.markdown("##### 🎯 綜合買點榜單 (優先以點火量能排序)")
            df_disp = df_results[df_results['Score'] >= 3].sort_values(
                by=['Vol_Ratio', 'Score'], ascending=[False, False]
            ).head(20)
            if df_disp.empty: st.info("目前雷達池內沒有符合條件的標的。")
            
        st.session_state.nav_pool = df_disp['ticker_raw'].tolist()
        st.session_state.nav_pool_data = df_disp.to_dict('records') 
            
        for _, r in df_disp.iterrows():
            button_label = format_stock_label_from_data(r, is_current=False)
            if st.button(button_label, key=f"btn_{r['ticker_raw']}_{st.session_state.scan_mode}", use_container_width=True):
                st.session_state.update({"current_stock": r['ticker_raw'], "page": "analysis", "date_offset": 0})
                st.rerun()
                
elif st.session_state.page == "analysis":
    target = st.session_state.current_stock
    c_name = get_stock_name(target)
    
    if target in st.session_state.recent_searches:
        st.session_state.recent_searches.remove(target)
    st.session_state.recent_searches.insert(0, target)
    st.session_state.recent_searches = st.session_state.recent_searches[:5]
    
    n_pool = st.session_state.get('nav_pool', [])
    p_stk, n_stk = None, None
    if target in n_pool and len(n_pool) > 1:
        i = n_pool.index(target)
        p_stk = n_pool[i - 1] if i > 0 else None
        n_stk = n_pool[i + 1] if i < len(n_pool) - 1 else None

    c1, c2, c3 = st.columns([1, 1, 1])
    with c1:
        if p_stk and st.button(f"⬅ 上一檔", use_container_width=True): st.session_state.update({"current_stock": p_stk}); st.rerun()
    with c2:
        if st.button("🏠 回雷達總機", use_container_width=True): st.session_state.page = "home"; st.rerun()
    with c3:
        if n_stk and st.button(f"下一檔 ➡", use_container_width=True): st.session_state.update({"current_stock": n_stk}); st.rerun()

    def set_view_days(days):
        st.session_state.view_days = days

    load_ph = st.empty()
    pre_rendered_fig = None  
    tick_info = {"ticks": [], "ask_ratio": 50.0, "bid_ratio": 50.0}

    with load_ph.container():
        st.markdown(f"<h4 style='text-align:center;'>🚀 正在喚醒【{target} {c_name}】AI 分析大腦...</h4>", unsafe_allow_html=True)
        p_bar = st.progress(0)
        status = st.empty()
        status.markdown("<div style='text-align:center; color:#888;'>⏳ 讀取 K 線與價量歷史...</div>", unsafe_allow_html=True)
        df_chart = get_stock_data(target)
        p_bar.progress(20)

        if df_chart is not None:
            df_slice = df_chart.iloc[:len(df_chart) + st.session_state.date_offset] if st.session_state.date_offset < 0 else df_chart
            if len(df_slice) < 5:
                load_ph.empty()
                st.warning("歷史資料不足")
            else:
                status.markdown("<div style='text-align:center; color:#888;'>⏱️ 正在載入盤中 Tick 微觀逐筆流向...</div>", unsafe_allow_html=True)
                tick_info = fetch_live_tick_data(target)
                p_bar.progress(35)
                
                status.markdown("<div style='text-align:center; color:#888;'>🏦 追蹤三大法人籌碼流向...</div>", unsafe_allow_html=True)
                inst_data = get_institutional_trading(target)
                p_bar.progress(50)
                status.markdown("<div style='text-align:center; color:#888;'>🧠 啟動動能與型態精算模組...</div>", unsafe_allow_html=True)
                data = analyze_today(df_slice, target, inst_data, tick_data=tick_info)
                sc = data['Score']
                p_bar.progress(65)
                status.markdown("<div style='text-align:center; color:#888;'>📑 獲取基本面與產業定位...</div>", unsafe_allow_html=True)
                f_data = get_fundamental_and_industry_data(target, data['收盤價'])
                p_bar.progress(80)
                status.markdown("<div style='text-align:center; color:#888;'>🌍 交叉比對總體經濟與大盤風險...</div>", unsafe_allow_html=True)
                twii_close, twii_change, twii_time_str = get_twii_quote()
                twii_df_for_pred = get_stock_data("^TWII")
                t_title, t_desc, tmr_title, tmr_desc, l_dt, n_dt, risk_score, macro = open_pred_logic(twii_df_for_pred, twii_close, twii_change, twii_time_str)
                p_bar.progress(90)
                
                status.markdown("<div style='text-align:center; color:#888;'>🎨 繪製高畫質技術線圖中...</div>", unsafe_allow_html=True)
                current_show_buy = st.session_state.get('toggle_buy_sig', True)
                current_show_sup = st.session_state.get('toggle_sup_res', True)
                current_show_signals = st.session_state.get('toggle_signals', True)
                pre_rendered_fig = draw_professional_chart(df_slice, target, data['收盤價'], st.session_state.view_days, is_light_mode, current_show_buy, f_data, current_show_sup, current_show_signals)
                p_bar.progress(98)

                status.markdown("<div style='text-align:center; color:#00cc00; font-weight:bold;'>✅ 解析完成！即將顯示...</div>", unsafe_allow_html=True)
                p_bar.progress(100)
                time.sleep(0.1) 
        else:
            load_ph.empty()
            st.error("查無此股票資料。")

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
            
            with st.expander("⏱️ 盤中即時微觀 Tick 與多空主動追價力道 (點擊展開)", expanded=False):
                tick_col1, tick_col2 = st.columns([2.2, 2.8])
                
                with tick_col1.container(border=True):
                    st.markdown("<p style='font-size:0.95rem; font-weight:bold; margin-bottom:8px;'>🔥 最新/收盤 5筆逐筆撮合 (Tick)</p>", unsafe_allow_html=True)
                    if tick_info["ticks"]:
                        tick_df = pd.DataFrame(tick_info["ticks"])
                        def color_tick_row(row):
                            if "外盤" in row['屬性']: return ['color: #ff3333; font-weight: bold']*4
                            elif "內盤" in row['屬性']: return ['color: #00cc00; font-weight: bold']*4
                            return ['']*4
                        st.dataframe(tick_df.style.apply(color_tick_row, axis=1), use_container_width=True, hide_index=True)
                    else:
                        st.info("🕒 非盤中交易時間，或暫無即時 Tick 撮合數據流。")
                        
                with tick_col2.container(border=True):
                    st.markdown("<p style='font-size:0.95rem; font-weight:bold; margin-bottom:2px;'>📊 盤中動態內外盤佔比 (主動多空攻擊力道)</p>", unsafe_allow_html=True)
                    ask_r = tick_info["ask_ratio"]
                    bid_r = tick_info["bid_ratio"]
                    st.markdown(f"""
                    <div style='display: flex; font-size: 0.85rem; justify-content: space-between; margin-bottom: 4px; font-weight: bold;'>
                        <span style='color: #ff3333;'>外盤 (主動買進): {ask_r}%</span>
                        <span style='color: #00cc00;'>內盤 (主動賣出): {bid_r}%</span>
                    </div>
                    <div style='width: 100%; background-color: #00cc00; height: 18px; border-radius: 4px; overflow: hidden; display: flex;'>
                        <div style='width: {ask_r}%; background-color: #ff3333; height: 100%; transition: width 0.3s;'></div>
                    </div>
                    """, unsafe_allow_html=True)
                    if ask_r > 55.0: st.markdown("<p style='font-size:0.85rem; color:#ff3333; font-weight:bold; margin-top:8px;'>🔥 多方點火：盤中由買方主動積極「往上追價」敲進，短線點火動能強勁！(系統已加分)</p>", unsafe_allow_html=True)
                    elif bid_r > 55.0: st.markdown("<p style='font-size:0.85rem; color:#00cc00; font-weight:bold; margin-top:8px;'>⚠️ 空方倒貨：盤中賣方主動積極「往下倒貨」求售，防範浮額倒貨風險。(系統已扣分)</p>", unsafe_allow_html=True)
                    else: st.markdown("<p style='font-size:0.85rem; color:#888; margin-top:8px;'>⚖️ 盤中多空勢均力敵，呈現平盤搓合狹幅震盪。</p>", unsafe_allow_html=True)
            
            st.markdown("---")
            
            stop_loss_html = ""
            recent_20 = df_slice.tail(20)
            recent_signals = []
            for idx in range(len(recent_20)):
                temp_df = df_slice.iloc[:len(df_slice) - 20 + idx + 1]
                if len(temp_df) >= 5:
                    t_data = analyze_today(temp_df, target, inst_data=None, tick_data=None)
                    if t_data and t_data['Score'] >= 3: recent_signals.append((temp_df.index[-1], t_data['收盤價']))
            
            if recent_signals:
                last_sig_date, last_sig_price = recent_signals[-1]
                if data['收盤價'] <= last_sig_price * 0.95:
                    loss_pct = (data['收盤價'] - last_sig_price) / last_sig_price * 100
                    stop_loss_html = f'''<div style="background-color: #ffe6e6; border-left: 6px solid #ff3333; padding: 15px; margin-bottom: 20px; border-radius: 4px;"><h4 style="color: #ff3333; margin-top: 0; font-size: 1.3rem;">🚨 【嚴格停損警報】觸發 5% 停損防護線</h4><span style="color: #333; font-size: 1.05rem; line-height: 1.6;">系統偵測到最近一次策略買訊 ({last_sig_date.strftime('%Y/%m/%d')}) 基準成本為 <b>{last_sig_price:.2f}</b>。<br>目前現價 <b>{data['收盤價']}</b> 已跌穿 5% 鐵律防護線 (預估帳面分歧為 <span style="color:#ff3333; font-weight:bold;">{loss_pct:.2f}%</span>)。<br><b>防範警訊：中線趨勢支撐已破，強烈建議嚴守交易紀律，果斷停損出場觀望，切勿盲目攤平接刀！</b></span></div>'''
            if stop_loss_html: st.markdown(stop_loss_html, unsafe_allow_html=True)

            st.markdown("##### 💡 近一個月歷史買點回測與趨勢分析")
            recent_30 = df_slice.tail(30)
            s_count, a_count = 0, 0
            buy_points_prices = []
            price_30_days_ago = recent_30['Close'].iloc[0]
            current_price = recent_30['Close'].iloc[-1]
            month_trend_pct = (current_price - price_30_days_ago) / price_30_days_ago * 100
            trend_color = "#ff3333" if month_trend_pct >= 0 else "#00cc00"
            trend_text = "上漲" if month_trend_pct >= 0 else "下跌"
            sign_t = "+" if month_trend_pct > 0 else ""
            
            for idx in range(len(recent_30)):
                temp_df = df_slice.iloc[:len(df_slice) - 30 + idx + 1]
                if len(temp_df) >= 5:
                    t_data = analyze_today(temp_df, target, inst_data=None, tick_data=None)
                    if t_data:
                        if t_data['Score'] >= 7: s_count += 1; buy_points_prices.append(t_data['收盤價'])
                        elif t_data['Score'] >= 3: a_count += 1; buy_points_prices.append(t_data['收盤價'])
            
            with st.container(border=True):
                col_sum1, col_sum2, col_sum3 = st.columns(3)
                with col_sum1: st.markdown(f"<div style='text-align:center;'>近一月趨勢<br><span style='color:{trend_color}; font-size:1.6rem; font-weight:900;'>{trend_text} {sign_t}{month_trend_pct:.2f}%</span></div>", unsafe_allow_html=True)
                with col_sum2: st.markdown(f"<div style='text-align:center;'>🟢 S級 強烈買進<br><span style='font-size:1.6rem; font-weight:900; color:#00cc00;'>{s_count} 次</span></div>", unsafe_allow_html=True)
                with col_sum3: st.markdown(f"<div style='text-align:center;'>🟡 A級 偏多試單<br><span style='font-size:1.6rem; font-weight:900; color:#ffcc00;'>{a_count} 次</span></div>", unsafe_allow_html=True)
                if not buy_points_prices:
                    summary_text = "近一個月股價呈現高檔推升或低迷打底，未曾觸發 any A/S 級超賣點條件，建議控制風險。"
                else:
                    avg_buy_price = sum(buy_points_prices) / len(buy_points_prices)
                    profit_pct = (current_price - avg_buy_price) / avg_buy_price * 100
                    prof_color = "#ff3333" if profit_pct >= 0 else "#00cc00"
                    prof_text = "獲利" if profit_pct >= 0 else "虧損"
                    summary_text = f"本月共發出 **{s_count + a_count}** 次策略買點。等額分批建倉平均成本約為 **{avg_buy_price:.2f}**。對比今日收盤，策略回測呈 <span style='color:{prof_color}; font-weight:bold;'>{prof_text} {'+' if profit_pct>0 else ''}{profit_pct:.2f}%</span>。"
                st.markdown(f"<div style='margin-top:12px; padding:12px; background-color:{'#f0f8ff' if is_light_mode else '#1e2433'}; border-radius:8px; line-height: 1.6;'>📝 <b>大腦回測總結：</b>{summary_text}</div>", unsafe_allow_html=True)

            bullets, v_t, v_c, v_a = generate_comprehensive_analysis(data, inst_data, sc, t_title, tmr_title)
            bullets_html = "".join([f"<li style='margin-bottom: 8px;'>{b}</li>" for b in bullets])
            st.markdown(f'''<div style="border: 2px solid {v_c}; border-radius: 10px; padding: 20px; margin-bottom: 20px; background-color: {bg_col};"><h3 style="text-align: center; color: {v_c}; margin-top: 0; font-size: 1.8rem;">🤖 AI 決策大腦：{v_t.replace('🟢 ', '').replace('🟡 ', '').replace('⚪ ', '').replace('🟠 ', '').replace('🔴 ', '')}</h3><hr style="border-color: {border_col}; margin: 15px 0;"><div style="margin-bottom: 15px;"><h4 style="color: {text_col}; margin-bottom: 10px;">🔍 綜合技術、型態與籌碼防護診斷：</h4><ul style="font-size: 1rem; color: {text_col}; line-height: 1.6;">{bullets_html}</ul></div><div style="background-color: {'#f0f8ff' if is_light_mode else '#1e2433'}; padding: 15px; border-radius: 8px; border-left: 5px solid {v_c};"><p style="font-size: 1.15rem; color: {text_col}; margin: 0; line-height: 1.6;">{v_a}</p></div></div>''', unsafe_allow_html=True)
            
            dc1, dc2, dc3, dc5, dc6, dc7 = st.columns([0.8, 0.8, 0.8, 1.3, 1.3, 1.3])
            dc1.button("30日", on_click=set_view_days, args=(30,))
            dc2.button("60日", on_click=set_view_days, args=(60,))
            dc3.button("90日", on_click=set_view_days, args=(90,))
            with dc5: st.toggle("🛒 顯示買進", value=True, key='toggle_buy_sig')
            with dc6: st.toggle("📏 歷史高低點", value=True, key='toggle_sup_res')
            with dc7: st.toggle("🏷️ 顯示符號", value=True, key='toggle_signals')
                
            if pre_rendered_fig is not None:
                st.plotly_chart(pre_rendered_fig, use_container_width=True, config={'displayModeBar': False, 'scrollZoom': False})
            
            st.markdown("### 🕵️‍♂️ 進階數據面板")
            a1, a2 = st.columns(2)
            with a1.container(border=True): st.markdown(f"##### 📊 技術與動能指標<br><br>**月線乖離率:** `{data['BIAS']}%`<br><br>**MACD 柱狀體:** `{data['MACD柱']}`<br><br>**5日均量:** `{data['5日均量']} 張`", unsafe_allow_html=True)
            with a2.container(border=True):
                eps = f_data['EPS']
                try: m_eps = round(float(eps)/12, 2) if eps != "無" else "無"
                except: m_eps = "無"
                st.markdown(f"##### 📑 基本面價值評估<br><br>**近四季累計 EPS (TTM):** `{eps}`<br><br>**換算單月 EPS:** `{m_eps}`<br><br>**最新即時本益比 (P/E):** `{f_data['PE']}`", unsafe_allow_html=True)
            
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

            st.divider()
            nav_data = st.session_state.get('nav_pool_data', [])
            
            with st.expander("🕒 最近搜尋紀錄", expanded=False):
                if st.session_state.recent_searches:
                    for r_tick in st.session_state.recent_searches:
                        btn_label = get_dynamic_stock_label(r_tick, nav_data, is_current=(r_tick==target))
                        if st.button(btn_label, key=f"recent_btn_{r_tick}", use_container_width=True):
                            st.session_state.current_stock = r_tick
                            st.rerun()
                else:
                    st.info("尚無搜尋紀錄")

            with st.expander(f"🔗 同產業相關股票 ({f_data['Industry']})", expanded=False):
                related_found = []
                
                for item in nav_data:
                    if item.get('產業') == f_data['Industry'] and item.get('ticker_raw') != target:
                        related_found.append(item)
                
                if len(related_found) < 5:
                    for t in st.session_state.custom_pool:
                        if t == target or any(r.get('ticker_raw') == t for r in related_found): continue
                        t_fund = get_fundamental_and_industry_data(t)
                        if t_fund['Industry'] == f_data['Industry']:
                            t_df = get_stock_data(t)
                            if t_df is not None and len(t_df) >= 5:
                                t_full_data = analyze_today(t_df, t, inst_data=None, tick_data=None)
                                if t_full_data:
                                    related_found.append(t_full_data)
                        if len(related_found) >= 5: break
                
                if related_found:
                    for rel_item in related_found[:5]:
                        btn_label = format_stock_label_from_data(rel_item, is_current=False)
                        if st.button(btn_label, key=f"rel_btn_{rel_item['ticker_raw']}", use_container_width=True):
                            st.session_state.current_stock = rel_item['ticker_raw']
                            st.rerun()
                else:
                    st.info(f"雷達池中暫無其他【{f_data['Industry']}】的關聯標的。")

        with col_right_menu:
            mode_titles = {
                "buy": "✅ 綜合買點榜", 
                "red_engulf": "🔥 紅吞反轉榜", 
                "recent": "📊 近五日成交量",
                "main_force": "🕵️ 主力初動榜"
            }
            active_title = mode_titles.get(st.session_state.scan_mode, "📋 當前雷達清單")
            
            with st.expander(active_title, expanded=False):
                if n_pool:
                    for stock_id in n_pool:
                        is_current = (stock_id == target)
                        btn_label = get_dynamic_stock_label(stock_id, nav_data, is_current)
                        
                        if st.button(btn_label, key=f"right_nav_{stock_id}_{st.session_state.scan_mode}", use_container_width=True):
                            st.session_state.current_stock = stock_id
                            st.rerun()
                else:
                    st.info("暫無榜單暫存。請先返回首頁執行篩選掃描。")