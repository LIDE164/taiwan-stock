# 最後修改時間: 2026-07-02 13:20 CST
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
FINMIND_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjoiYTQ1Njg4MTUwQGdtYWlsLmNvbSIsImVtYWlsIjoiYTQ1Njg4MTUwQGdtYWlsLmNvbSIsInRva2VuX3ZlcnNpb24iOjB9.LUcb8YPV4yo93_aB3obP4Z5iUGqAgTaH28ySx9UNv5I"

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

# === 動態定義 CSS 變數 ===
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
sticky_bg = "rgba(255,255,255,0.95)" if is_light_mode else "rgba(11,17,32,0.95)"

css_style = f"""
<style>
    .stApp {{ background-color: {app_bg}; -webkit-tap-highlight-color: transparent; overflow-x: hidden; }}
    #MainMenu {{visibility: hidden;}} footer {{visibility: hidden;}}
    [data-testid="collapsedControl"] {{ border: 1px solid {border_col} !important; border-radius: 8px !important; background-color: {bg_col} !important; padding: 5px 12px !important; display: flex !important; align-items: center !important; width: auto !important; transition: 0.3s; }}
    [data-testid="collapsedControl"]::after {{ content: " ⭐ 我的群組"; font-size: 1.1rem; font-weight: bold; color: #ffcc00; margin-left: 8px; }}
    .stButton button {{ font-weight: bold !important; border-radius: 8px !important; text-align: left !important; }}
    button[kind="primary"] {{ font-size: 1.5rem !important; padding: 15px !important; border-radius: 12px !important; background-color: #ffcc00 !important; color: #111 !important; border: none !important; }}
    .sticky-header {{ position: sticky; top: 0; z-index: 999; background-color: {sticky_bg}; padding: 10px 0; border-bottom: 1px solid {border_col}; backdrop-filter: blur(5px); margin-top: -15px; margin-bottom: 15px; }}
    h1, h2, h3, h4, p, span {{ color: {title_col} !important; }}
    .risk-bar-container {{ width: 100%; background-color: #333; border-radius: 8px; margin-top: 5px; margin-bottom: 15px; overflow: hidden; }}
    .risk-bar-fill {{ height: 16px; border-radius: 8px; transition: width 0.5s ease-in-out; }}
    
    /* 🎯 完美復刻：膠囊過濾器 CSS */
    div[role="radiogroup"] {{ display: flex; flex-direction: row; gap: 8px; flex-wrap: wrap; margin-bottom: 16px; }}
    div[role="radiogroup"] > label {{ margin: 0 !important; padding: 0 !important; background: transparent !important; }}
    div[role="radiogroup"] label > div:first-child {{ display: none !important; }}
    div[role="radiogroup"] div[data-testid="stMarkdownContainer"] p {{
        background-color: {pill_bg}; border: 1px solid {pill_border}; color: {pill_text} !important;
        padding: 6px 14px; border-radius: 25px; font-size: 0.85rem; font-weight: 600; margin: 0;
        cursor: pointer; transition: all 0.2s ease-in-out; white-space: nowrap; flex-shrink: 0;
    }}
    div[role="radiogroup"] label:hover div[data-testid="stMarkdownContainer"] p {{ background-color: {pill_hover}; }}
    div[role="radiogroup"] label:has(input:checked) div[data-testid="stMarkdownContainer"] p {{
        background-color: #4f46e5 !important; border-color: #4f46e5 !important; color: white !important;
        box-shadow: 0 4px 10px rgba(79, 70, 229, 0.4);
    }}
    a.stock-card-link {{ text-decoration: none; color: inherit; display: block; }}
    a.stock-card-link:hover .stock-name-hover {{ color: #818cf8 !important; }}
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
        else:
            st.session_state.search_error = f"⚠️ 找不到與「{s_val}」相關的標的。"

if "search_error" in st.session_state and st.session_state.search_error:
    st.sidebar.warning(st.session_state.search_error)
    st.session_state.search_error = ""

st.sidebar.divider()
st.sidebar.title("⏱️ 盤中即時跳動雷達")
auto_refresh = st.sidebar.toggle("🟢 開啟即時自動更新 (每30秒)", False, key="auto_refresh_toggle")
if auto_refresh: st_autorefresh(interval=30000, limit=None, key="market_auto_refresh")

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
if 'current_stock' not in st.session_state: st.session_state.current_stock = "2330"
if 'custom_pool' not in st.session_state: st.session_state.custom_pool = load_json(POOL_FILE, ["2330", "2317", "2454", "2382", "3231"])
if 'nav_pool' not in st.session_state: st.session_state.nav_pool = st.session_state.custom_pool
if 'view_days' not in st.session_state: st.session_state.view_days = 30
if 'date_offset' not in st.session_state: st.session_state.date_offset = 0

# 點擊名稱跳轉邏輯
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

    # 🚀 加入 TR 與 ATR 真實波動幅度運算 (用於動態停利損)
    try:
        tr1 = df['High'] - df['Low']
        tr2 = (df['High'] - df['Close'].shift(1)).abs()
        tr3 = (df['Low'] - df['Close'].shift(1)).abs()
        df['TR'] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        df['ATR'] = df['TR'].rolling(14).mean().bfill()
    except:
        df['ATR'] = df['Close'] * 0.03 # 備用預設波動
        
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

@st.cache_data(ttl=5, show_spinner=False)
def get_stock_live_time(ticker):
    tz_tpe = timezone(timedelta(hours=8))
    return datetime.now(tz_tpe).strftime('%Y/%m/%d %H:%M:%S')

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
# 🚀 升級版 AI 核心計算邏輯 (共振與雙引擎)
# ==========================================
def get_decision_score(data, fund_data, inst_data=None):
    sc, rs = 0, []
    
    # 🎯 濾網 1：大趨勢防護 (長線保護短線)
    if data['收盤價'] >= data['60MA']:
        sc += 2; rs.append("✅ 站穩季線 (長線多頭保護，勝率基準提升)")
    else:
        sc -= 4; rs.append("🚨 跌破季線 (長線空頭，反彈易遇壓，嚴格扣分)")

    # 👑 共振 1：強勢股的黃金坑 (長線多頭 + 短線極度超賣)
    if data['收盤價'] >= data['60MA'] and (data['J值'] < 20 or data['BIAS'] < -5):
        sc += 3; rs.append("🔥 【動能共振】多頭趨勢下的極度超賣 (勝率極高的黃金坑)")
        
    # 👑 共振 2：關鍵支撐位的型態表態 (在月線或季線附近出現紅吞/長下影線)
    near_support = abs(data['收盤價'] - data['20MA']) / data['20MA'] < 0.03 or abs(data['收盤價'] - data['60MA']) / data['60MA'] < 0.03
    if near_support and (data.get('紅吞') or data.get('回測有撐')):
        sc += 4; rs.append("🔥 【位階共振】關鍵均線支撐處出現紅吞/下影線 (主力強力防守)")
    elif data.get('紅吞'):
        sc += 1; rs.append("✅ 出現紅吞型態 (但未處於完美支撐位)")

    # 👑 共振 3：籌碼純度 (土洋齊買 > 單純加總買超)
    if inst_data and len(inst_data) >= 3:
        f_net = sum([int(str(x['外資(張)']).replace(',', '')) for x in inst_data[:3] if str(x['外資(張)']).replace(',', '').lstrip('-').isdigit()])
        t_net = sum([int(str(x['投信(張)']).replace(',', '')) for x in inst_data[:3] if str(x['投信(張)']).replace(',', '').lstrip('-').isdigit()])
        net_buy = f_net + t_net + sum([int(str(x['自營商(張)']).replace(',', '')) for x in inst_data[:3] if str(x['自營商(張)']).replace(',', '').lstrip('-').isdigit()])
        
        if f_net > 0 and t_net > 0:
            sc += 4; rs.append(f"🔥 【籌碼共振】土洋同步買超 (外資 {f_net}張, 投信 {t_net}張，籌碼極度安定)")
        elif net_buy > 500:
            sc += 2; rs.append(f"✅ 法人近三日偏多 (累計買超 {net_buy} 張)")
        elif net_buy < -500:
            sc -= 3; rs.append(f"🚨 法人近三日偏空倒貨 (累計賣超 {abs(net_buy)} 張，嚴防破底)")

    # 🎯 量價與傳統指標
    if data.get('成交量', 0) > data.get('5日均量', 0) * 1.3: sc += 2; rs.append("🔥 量能顯著放大 (主力明確點火)")
    elif data.get('成交量', 0) < data.get('5日均量', 0) * 0.8: sc -= 1; rs.append("⚠️ 量能萎縮 (攻擊動能不足)")
        
    if data.get('MACD柱', 0) > data.get('前日MACD柱', -999): sc += 1; rs.append("✅ MACD 動能轉強")
    if data.get('反彈遇壓'): sc -= 2; rs.append("🩸 反彈遇壓留長上影線")
    if data.get('黑吞'): sc -= 3; rs.append("🩸 出現「黑吞」反轉型態 (強烈空訊)")
    
    if data['收盤價'] >= data['5MA'] and data.get('5日線即將上彎'): sc += 2; rs.append("🔥 5日線扣低值將上彎")

    if data['J值'] >= 85: sc -= 3; rs.append("⚠️ KDJ高檔極度過熱 (追高風險)")
    if data['收盤價'] >= data['BB_UP'] * 0.98: sc -= 2; rs.append("⚠️ 觸及布林上軌壓力")

    return sc, rs

def get_dynamic_theme(ticker, industry):
    THEME_MAP = {
        "2382": ("AI伺服器", "💡"), "2356": ("AI伺服器", "💡"), "3231": ("AI伺服器", "💡"),
        "2330": ("先進製程", "⚙️"), "2454": ("先進製程", "⚙️"), "6147": ("先進製程", "⚙️"),
        "1503": ("重電", "⚡"), "1519": ("重電", "⚡"), "2308": ("重電", "⚡"),
        "2359": ("機器人", "🤖"), "2354": ("機器人", "🤖"), "2603": ("航運", "🚢")
    }
    if ticker in THEME_MAP: return THEME_MAP[ticker]
    ind = str(industry).strip() if pd.notna(industry) and industry else ""
    if not ind or ind == "一般產業": return ("一般題材", "📌")
    icon_map = { "半導體": "⚙️", "電腦": "💻", "電子": "⚡", "電機": "🔌", "綠能": "🌱", "光電": "☀️", "通信": "📡", "網通": "📶", "生技": "🧬", "航運": "🚢", "鋼鐵": "🏗️", "金融": "💰", "營造": "🏗️", "觀光": "✈️" }
    icon = "🏷️"
    for kw, ic in icon_map.items():
        if kw in ind: icon = ic; break
    return (ind, icon)

def analyze_today(df, ticker_number, inst_data=None, is_light_mode=False):
    if df is None or len(df) < 5: return None
    t, p, p5 = df.iloc[-1], df.iloc[-2], df.iloc[-5]
    fund = get_fundamental_and_industry_data(ticker_number, round(t['Close'], 2))
    
    t_open, t_close, t_high, t_low = float(t['Open']), float(t['Close']), float(t['High']), float(t['Low'])
    p_open, p_close = float(p['Open']), float(p['Close'])
    
    # K線判斷
    red_mask = (df['Open'].shift(1) > df['Close'].shift(1)) & (df['Close'] > df['Open']) & (df['Close'] > df['Open'].shift(1)) & (df['Open'] < df['Close'].shift(1))
    black_mask = (df['Close'].shift(1) > df['Open'].shift(1)) & (df['Open'] > df['Close']) & (df['Open'] > df['Close'].shift(1)) & (df['Close'] < df['Open'].shift(1))
    total_range = t_high - t_low if t_high - t_low != 0 else 0.001
    lower_shadow = min(t_open, t_close) - t_low
    body = abs(t_close - t_open)
    ma_resistance = min(t['5MA'], t['10MA']) 
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
    
    # 盤中即時資料精算 (VWAP近似值)
    vwap_approx = (t_open + t_high + t_low + t_close) / 4
    vwap_dev = (t_close - vwap_approx) / vwap_approx * 100
    est_vol_ratio = t['Volume'] / df['Volume'].tail(5).mean() if df['Volume'].tail(5).mean() > 0 else 1
    intraday_signal = "穩守均價線" if t_close > vwap_approx else "跌破均價線"
    if t_close > vwap_approx and est_vol_ratio > 1.3: intraday_signal = "強勢越過均價線"

    # 盤後 ATR 精算
    atr_val = t.get('ATR', t_close * 0.03)
    target_p = t_close + (atr_val * 1.5)
    stop_p = t_close - (atr_val * 1.0)
    target_pct = (target_p - t_close) / t_close * 100
    stop_pct = (stop_p - t_close) / t_close * 100
    rrr = round(abs(target_pct / stop_pct), 1) if stop_pct != 0 else 0

    data = {
        "代號": ticker_number, "名稱": get_stock_name(ticker_number), "ticker_raw": ticker_number,
        "產業": fund['Industry'], "昨日收盤價": round(p_close, 2), "收盤價": round(t_close, 2), 
        "漲跌": round(t_close - p_close, 2), "漲跌幅": round((t_close - p_close) / p_close * 100, 2), 
        "成交量": int(t['Volume']), "5日均量": int(df['Volume'].tail(5).mean()),
        "5MA": round(t['5MA'], 2), "20MA": round(t['20MA'], 2), "60MA": round(t['60MA'], 2),
        "BB_UP": round(t['BB_UP'], 2), "BB_DN": round(t['BB_DN'], 2), "BIAS": round(t['BIAS_20'], 2),
        "MACD": round(t['MACD'], 2), "MACD柱": round(t['MACD_Hist'], 3), "前日MACD柱": round(p['MACD_Hist'], 3),
        "K": round(t['K'], 2), "D": round(t['D'], 2), "J值": round(t['J'], 2),
        "紅吞": bool(red_mask.iloc[-1]), "黑吞": bool(black_mask.iloc[-1]),
        "近七日紅吞": bool(red_mask.tail(7).any()),
        "回測有撐": (lower_shadow > body * 1.5) and (lower_shadow / total_range > 0.4) and (t_low < p_close) and (t_close >= min(p_open, p_close)),
        "反彈遇壓": (upper_shadow > body * 1.5) and (upper_shadow / total_range > 0.4) and (t_high >= ma_resistance) and (t_close < ma_resistance),
        "5日線即將上彎": t_close > (float(df['Close'].iloc[-5]) if len(df) >= 5 else float(t_close)),
        "Whale_Action": whale_tag, "Whale_Net": whale_net_buy,
        "Theme_Name": theme_name, "Theme_Icon": theme_icon,
        "VWAP": round(vwap_approx, 1), "VWAP_Dev": vwap_dev, "Est_Vol_Ratio": est_vol_ratio, "Intraday_Signal": intraday_signal,
        "ATR_Target": round(target_p, 1), "ATR_Stop": round(stop_p, 1), "ATR_Target_Pct": target_pct, "ATR_Stop_Pct": stop_pct, "RRR": rrr
    }
    
    sc, rs = get_decision_score(data, fund, inst_data)
    data['Score'] = sc
    data['Reasons'] = rs
    data['評級'] = "🟢 S級" if sc >= 10 else ("🟡 A級" if sc >= 5 else "⚪ 觀望")
    
    # 萃取最關鍵的共振說明供卡片顯示
    confluence_str = "無明顯共振"
    for r in rs:
        if "共振" in r: 
            confluence_str = r.split("】")[1].split("(")[0].strip() if "】" in r else r
            break
    data['Confluence'] = confluence_str
    return data

@st.cache_data(ttl=180, show_spinner=False)
def get_global_scan_results(pool_tuple):
    scan_results = []
    def process_scan(stock):
        df = get_stock_data(stock)
        if df is not None: 
            inst_data = get_institutional_trading(stock)
            return analyze_today(df, stock, inst_data=inst_data, is_light_mode=False)
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
    # 頂部導航
    st.markdown("<h2 style='text-align: center; color: #818cf8; margin-bottom: 20px;'>極致精準：雙引擎量化雷達</h2>", unsafe_allow_html=True)
    
    top_100_pool = fetch_twse_top_100()
    pool = tuple(set(top_100_pool + st.session_state.custom_pool + list(STOCK_NAMES.keys())))
    
    with st.spinner("🚀 盤中/盤後雙引擎資料精密解析中..."):
        scan_results = get_global_scan_results(pool)
            
    if scan_results:
        df_results = pd.DataFrame(scan_results)
        
        # 雙引擎模式切換
        col_m1, col_m2 = st.columns([1, 1])
        with col_m1:
            radar_mode = st.radio("引擎模式：", ["盤後波段精算 (15:00後)", "盤中動能快篩 (09:00-13:30)"], horizontal=True, label_visibility="collapsed")
        is_intraday = "盤中" in radar_mode
        
        # 題材膠囊過濾
        available_themes = ["全部題材"] + sorted(list(set(df_results['Theme_Name'].unique()) - {"一般題材"}))
        selected_theme = st.radio("題材：", available_themes, horizontal=True, label_visibility="collapsed")
        
        if selected_theme != "全部題材":
            df_results = df_results[df_results['Theme_Name'] == selected_theme]
        
        # 強制依評分排序，嚴格篩選 A/S 級 (大於等於 5 分)
        df_disp = df_results[df_results['Score'] >= 5].sort_values(by=['Score', '漲跌幅'], ascending=[False, False]).head(30)
        
        st.session_state.nav_pool = df_disp['ticker_raw'].tolist()
        st.session_state.nav_pool_data = df_disp.to_dict('records') 
            
        # 統計列
        st.markdown(f"<div style='display: flex; justify-content: space-between; font-size: 0.8rem; color: #94a3b8; border-bottom: 1px solid #1e293b; padding-bottom: 8px; margin-bottom: 16px;'><span><i class='fa-solid fa-bolt'></i> {'盤中高勝率預估' if is_intraday else '近60日波段勝率與風報比'}</span><span>共 {len(df_disp)} 檔</span></div>", unsafe_allow_html=True)
        
        # === 全新：手機版高質感獨立卡片排版 (無左右滑動，評分在最前) ===
        if df_disp.empty:
            st.markdown("<div style='text-align: center; padding: 40px; color: #64748b; font-size: 0.9rem;'>此條件下暫無符合高階共振條件的標的。</div>", unsafe_allow_html=True)
        else:
            cards_html = ""
            for _, r in df_disp.iterrows():
                p_val = r['漲跌']
                p_col = "#ef4444" if p_val >= 0 else "#22c55e"
                p_bg = "rgba(239,68,68,0.1)" if p_val >= 0 else "rgba(34,197,94,0.1)"
                change_sign = "+" if p_val > 0 else ""
                
                score = r.get('Score', 0)
                s_col = "#ef4444" if score >= 10 else ("#facc15" if score >= 5 else "#22c55e")
                rating = r.get('評級', '觀望').replace('🟢 ', '').replace('🟡 ', '').replace('⚪ ', '')
                r_col = "#4ade80" if rating == "S級" else ("#facc15" if rating == "A級" else "#94a3b8")
                
                # 連結包住名稱與代碼 (需求：解析的部分放在股票名稱上)
                stock_link = f'href="/?stock={r["代號"]}" target="_self"'
                
                cards_html += f"""
                <div style="background-color: #0f172a; border: 1px solid #1e293b; border-radius: 12px; padding: 14px; margin-bottom: 12px; position: relative; overflow: hidden;">
                    <!-- 頂部資訊列： 評分 | 股名(含連結) | 價格 -->
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; position: relative; z-index: 10;">
                        <div style="display: flex; align-items: center; gap: 12px;">
                            <!-- ★ 評分強制放在第一格 ★ -->
                            <div style="width: 50px; height: 50px; border-radius: 50%; background: radial-gradient(circle, #1e293b 0%, #0b1120 100%); border: 1px solid #334155; display: flex; flex-direction: column; align-items: center; justify-content: center; flex-shrink: 0; box-shadow: inset 0 2px 4px rgba(255,255,255,0.05), 0 4px 8px rgba(0,0,0,0.4);">
                                <span style="color: {s_col}; font-weight: 800; font-size: 1.2rem; line-height: 1;">{score}</span>
                                <span style="color: {r_col}; font-size: 0.65rem; font-weight: 800; margin-top: 2px;">{rating}</span>
                            </div>
                            
                            <!-- 股名與連結 -->
                            <a {stock_link} class="stock-card-link">
                                <div style="display: flex; align-items: center; gap: 6px;">
                                    <span class="stock-name-hover" style="color: #f8fafc; font-weight: bold; font-size: 1.15rem; transition: color 0.2s;">{r['名稱']}</span>
                                    <span style="font-size: 0.7rem; background-color: rgba(79,70,229,0.15); color: #818cf8; border: 1px solid rgba(79,70,229,0.3); padding: 2px 6px; border-radius: 4px; white-space: nowrap; font-weight: 600;">{r['Theme_Icon']} {r['Theme_Name']}</span>
                                </div>
                                <div style="font-size: 0.8rem; color: #64748b; margin-top: 4px; font-family: monospace;">{r['代號']} <span style="color:#475569; font-size:0.7rem; margin-left:4px;">(點擊解析)</span></div>
                            </a>
                        </div>
                        
                        <!-- 價格區塊 -->
                        <div style="text-align: right; flex-shrink: 0;">
                            <div style="color: {p_col}; font-weight: 800; font-size: 1.2rem; font-family: monospace;">{r['收盤價']:.1f}</div>
                            <div style="background-color: {p_bg}; color: {p_col}; font-size: 0.75rem; padding: 2px 6px; border-radius: 4px; display: inline-block; font-weight: 800; font-family: monospace; margin-top: 4px;">{change_sign}{r['漲跌幅']}%</div>
                        </div>
                    </div>
                """
                
                # 根據盤中/盤後模式顯示不同的數據方塊 (Grid，絕不橫移)
                if is_intraday:
                    v_dev = r['VWAP_Dev']
                    v_col = "#ef4444" if v_dev > 0 else "#22c55e"
                    est_vol = r['Est_Vol_Ratio']
                    ev_col = "#facc15" if est_vol > 1.3 else "#e2e8f0"
                    
                    cards_html += f"""
                    <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; background-color: rgba(30,41,59,0.4); border: 1px solid rgba(51,65,85,0.5); padding: 10px; border-radius: 8px; font-size: 0.75rem; margin-bottom: 10px; position: relative; z-index: 10;">
                        <div style="display: flex; flex-direction: column;">
                            <span style="color: #64748b; margin-bottom: 4px;">VWAP乖離</span>
                            <span style="color: {v_col}; font-weight: bold; font-family: monospace;">{'+' if v_dev>0 else ''}{v_dev:.1f}%</span>
                        </div>
                        <div style="display: flex; flex-direction: column;">
                            <span style="color: #64748b; margin-bottom: 4px;">預估量能</span>
                            <span style="color: {ev_col}; font-weight: bold; font-family: monospace;">{est_vol:.1f}x</span>
                        </div>
                        <div style="display: flex; flex-direction: column;">
                            <span style="color: #64748b; margin-bottom: 4px;">盤中動能</span>
                            <span style="color: #f8fafc; font-weight: bold;">{r['Intraday_Signal']}</span>
                        </div>
                    </div>
                    <div style="font-size: 0.75rem; color: #fbbf24; display: flex; align-items: flex-start; gap: 6px; position: relative; z-index: 10;">
                        <span style="margin-top: 1px;">⚡</span>
                        <span style="line-height: 1.4; font-weight: 500;">雷達評分：{score} (盤中無法人籌碼，以動能為主)</span>
                    </div>
                    """
                else:
                    atr_t = f"+{r['ATR_Target_Pct']:.1f}%"
                    rrr_val = r['RRR']
                    w_net = r.get('Whale_Net', 0)
                    w_col = "#ef4444" if w_net > 0 else ("#22c55e" if w_net < 0 else "#94a3b8")
                    whale_str = f"+{w_net:,}" if w_net > 0 else f"{w_net:,}"
                    confluence = r.get('Confluence', '無明顯共振')
                    
                    cards_html += f"""
                    <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; background-color: rgba(30,41,59,0.4); border: 1px solid rgba(51,65,85,0.5); padding: 10px; border-radius: 8px; font-size: 0.75rem; margin-bottom: 10px; position: relative; z-index: 10;">
                        <div style="display: flex; flex-direction: column;">
                            <span style="color: #64748b; margin-bottom: 4px;">ATR動態目標</span>
                            <span style="color: #ef4444; font-weight: bold; font-family: monospace;">🎯 {atr_t}</span>
                        </div>
                        <div style="display: flex; flex-direction: column;">
                            <span style="color: #64748b; margin-bottom: 4px;">風報比 RRR</span>
                            <span style="color: #e2e8f0; font-weight: bold; font-family: monospace;">1 : {rrr_val}</span>
                        </div>
                        <div style="display: flex; flex-direction: column;">
                            <span style="color: #64748b; margin-bottom: 4px;">法人淨買</span>
                            <span style="color: {w_col}; font-weight: bold; font-family: monospace;">{whale_str}</span>
                        </div>
                    </div>
                    <div style="font-size: 0.75rem; color: #fbbf24; display: flex; align-items: flex-start; gap: 6px; position: relative; z-index: 10;">
                        <span style="margin-top: 1px;">⚡</span>
                        <span style="line-height: 1.4; font-weight: 500;">{confluence}</span>
                    </div>
                    """
                cards_html += "</div>"
            st.markdown(cards_html, unsafe_allow_html=True)

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
                p_bar.progress(60)
                data = analyze_today(df_slice, target, inst_data, is_light_mode)
                sc = data['Score']
                f_data = get_fundamental_and_industry_data(target, data['收盤價'])
                p_bar.progress(90)
                
                # ... 省略畫圖邏輯 (與舊版相同) ...
                p_bar.progress(100)
                time.sleep(0.1) 
        else:
            load_ph.empty()
            st.error("查無此股票資料。")

    if df_chart is not None and len(df_slice) >= 14:
        load_ph.empty()
        
        col_main_view, col_right_menu = st.columns([3.9, 1.1])
        
        with col_main_view:
            p_color = '#ef4444' if data['漲跌'] >= 0 else '#22c55e'
            st.markdown(f"<h2 style='text-align: center; margin-bottom: 5px;'>🎯 {target} {c_name}</h2>", unsafe_allow_html=True)
            st.markdown(f"<div style='text-align: center; color: #888; font-size: 1.1rem;'>【{f_data['Industry']}】</div>", unsafe_allow_html=True)
            st.markdown(f"<h3 style='text-align: center; color: {p_color}; font-size: 2.2rem; margin-bottom: 0px;'>{data['收盤價']} ({'+' if data['漲跌']>0 else ''}{data['漲跌幅']}%)</h3>", unsafe_allow_html=True)
            st.markdown("---")
            
            # ==========================================
            # 🚀 全新升級：ATR 動態停利損勝率回測引擎
            # ==========================================
            st.markdown("##### 📊 ATR 動態勝率歷史回測 (近 60 日)")
            recent_60 = df_slice.tail(60)
            s_count, a_count = 0, 0
            wins = 0
            closed_signals = 0
            buy_points_prices = []
            
            for idx in range(len(recent_60)):
                current_date = recent_60.index[idx]
                actual_idx = df_slice.index.get_loc(current_date)
                temp_df = df_slice.iloc[:actual_idx + 1]
                
                if len(temp_df) >= 14:
                    t_data = analyze_today(temp_df, target, inst_data=None, is_light_mode=is_light_mode)
                    if t_data and t_data['Score'] >= 5: # A級或S級
                        if t_data['Score'] >= 10: s_count += 1
                        else: a_count += 1
                        
                        buy_price = t_data['收盤價']
                        atr_val = t_data.get('ATR_Target', buy_price * 1.03) - buy_price
                        target_p = buy_price + (atr_val)
                        stop_p = buy_price - (atr_val / t_data.get('RRR', 1.5))
                        
                        buy_points_prices.append(buy_price)
                        
                        future_df = df_slice.iloc[actual_idx + 1 : actual_idx + 6]
                        if len(future_df) > 0:
                            closed_signals += 1
                            hit_target = future_df['High'].max() >= target_p
                            hit_stop = future_df['Low'].min() <= stop_p
                            
                            # 勝率判斷：5日內達標且未碰停損，或5日後收盤獲利
                            if hit_target and not hit_stop:
                                wins += 1
                            elif future_df['Close'].iloc[-1] > buy_price and not hit_stop:
                                wins += 1
            
            win_rate = (wins / closed_signals * 100) if closed_signals > 0 else 0
            wr_color = "#ef4444" if win_rate >= 75 else ("#facc15" if win_rate >= 50 else "#22c55e")
            
            with st.container(border=True):
                col_sum1, col_sum2, col_sum3 = st.columns(3)
                with col_sum1: st.markdown(f"<div style='text-align:center; color:#888; font-size:0.9rem;'>動態波段勝率<br><span style='color:{wr_color}; font-size:1.8rem; font-weight:900;'>{win_rate:.1f}%</span></div>", unsafe_allow_html=True)
                with col_sum2: st.markdown(f"<div style='text-align:center; color:#888; font-size:0.9rem;'>🟢 S級 強烈買點<br><span style='font-size:1.8rem; font-weight:900; color:#ef4444;'>{s_count} 次</span></div>", unsafe_allow_html=True)
                with col_sum3: st.markdown(f"<div style='text-align:center; color:#888; font-size:0.9rem;'>🟡 A級 偏多試單<br><span style='font-size:1.8rem; font-weight:900; color:#facc15;'>{a_count} 次</span></div>", unsafe_allow_html=True)
                
                if closed_signals == 0:
                    summary_text = "過去 60 日內，系統經過嚴格的「季線防護」與「高階共振」篩選，尚未產生足夠的歷史買進訊號。這代表此標的近期無安全買點，或處於空頭。"
                else:
                    summary_text = f"過去 60 日共觸發 **{closed_signals}** 次有效買點。導入 ATR 動態停利模型後，短線波段勝率達 <span style='color:{wr_color}; font-weight:bold;'>{win_rate:.1f}%</span>。當前建議之風報比 (RRR) 為 1 : {data['RRR']}，代表每次獲利期望值為虧損的 {data['RRR']} 倍。"
                
                st.markdown(f"<div style='margin-top:12px; padding:12px; background-color:rgba(30,41,59,0.5); border-radius:8px; line-height: 1.6; font-size:0.95rem; color:#cbd5e1;'>📝 <b>回測總結：</b>{summary_text}</div>", unsafe_allow_html=True)

            # AI 決策文字產生器
            t_text_c = "#ddd"
            b_col = "#333"
            tech_bullets = []
            if data['收盤價'] >= data['60MA']: tech_bullets.append("🔥 <span style='color:#ef4444; font-weight:bold;'>大趨勢防護：股價站穩季線 (60MA)，長線保護短線，操作勝率高。</span>")
            else: tech_bullets.append("⚠️ <span style='color:#22c55e;'>趨勢走空：股價跌破季線，長線空頭壓制，搶反彈難度大。</span>")
            
            if data['收盤價'] > data['VWAP']: tech_bullets.append(f"🔥 <span style='color:#ef4444; font-weight:bold;'>均價線支撐：當日股價企穩於 VWAP ({data['VWAP']}) 之上，大戶建倉成本具保護力。</span>")
            
            if "共振" in data['Confluence']: tech_bullets.append(f"👑 <span style='color:#facc15; font-weight:bold;'>AI 特殊共振點：{data['Confluence']}</span>")
            
            if data['Est_Vol_Ratio'] > 1.3: tech_bullets.append(f"🔥 <span style='color:#ef4444; font-weight:bold;'>預估量能放大：今日預估量達 5 日均量的 {data['Est_Vol_Ratio']:.1f} 倍，攻擊意願強。</span>")

            ai_brain_html = f"<div style='border: 1px solid {b_col}; border-radius: 8px; padding: 15px; background-color: #0f172a;'><ul style='font-size: 0.95rem; line-height: 1.6; margin-bottom: 0; color: {t_text_c}; list-style:none; padding-left:0;'>"
            for b in tech_bullets: ai_brain_html += f"<li style='margin-bottom:8px;'>{b}</li>"
            ai_brain_html += "</ul></div>"

            v_c = "#22c55e" if sc < 5 else ("#facc15" if sc < 10 else "#ef4444")
            v_t = "🔴 空手觀望" if sc < 5 else ("🟡 A級試單" if sc < 10 else "🟢 S級強烈買進")
            
            st.markdown(f"""
            <div style="border: 2px solid {v_c}; border-radius: 10px; padding: 20px; margin-bottom: 20px; background-color: #0b1120;">
                <h3 style="text-align: center; color: {v_c}; margin-top: 0; font-size: 1.8rem; margin-bottom: 20px;">🤖 雙引擎決策大腦：{v_t.replace('🟢 ', '').replace('🟡 ', '').replace('🔴 ', '')}</h3>
                {ai_brain_html}
                <div style="background-color: rgba(30,41,59,0.5); padding: 15px; border-radius: 8px; border-left: 5px solid {v_c}; margin-top:15px;">
                    <p style="font-size: 1.15rem; color: #f8fafc; margin: 0; line-height: 1.6;">
                        ✅ <b>進階 ATR 目標精算</b><br>
                        依據真實波動率計算，合理停利目標為 <b style='color:#ef4444;'>{data['ATR_Target']}</b> ({data['ATR_Target_Pct']:.1f}%)，嚴格停損設於 <b style='color:#22c55e;'>{data['ATR_Stop']}</b> ({data['ATR_Stop_Pct']:.1f}%)。<br>
                        風報比 (Risk-Reward) 為 <b>1 : {data['RRR']}</b>。
                    </p>
                </div>
            </div>""", unsafe_allow_html=True)
            
            # ... 下方保留您原本的圖表、群組邏輯 (皆不變) ...
