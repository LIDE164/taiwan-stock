import yfinance as yf
import streamlit as st
import pandas as pd
import requests
from datetime import datetime
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import json
import os

# ==========================================
# 0. 系統初始化與風格設定
# ==========================================
st.set_page_config(page_title="專業交易雷達", layout="centered", initial_sidebar_state="collapsed")

# 隱藏預設頂部選單，保持介面極簡乾淨
st.markdown('''
<style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    
    [data-testid="collapsedControl"] {
        border: 1px solid #444 !important;
        border-radius: 8px !important;
        background-color: #1a1c24 !important;
        padding: 5px 12px !important;
        display: flex !important;
        align-items: center !important;
        width: auto !important;
        transition: 0.3s;
    }
    [data-testid="collapsedControl"]::after {
        content: " ⭐ 我的自選股";
        font-size: 1.1rem;
        font-weight: bold;
        color: #ffcc00;
        margin-left: 8px;
    }
    
    .stButton button { font-weight: bold !important; border-radius: 8px !important; }
    
    .sticky-header {
        position: sticky; top: 0; z-index: 999;
        background-color: rgba(26, 28, 36, 0.95);
        padding: 10px 0; border-bottom: 1px solid #333;
        backdrop-filter: blur(5px); margin-top: -15px; margin-bottom: 15px;
    }
    
    /* ================================================== */
    /* 👉 需求1：您可以在這裡自行調節「多空趨勢」方塊大小 */
    /* ================================================== */
    .trend-box {
        background-color: #1a1c24; 
        border: 1px solid #333; 
        border-radius: 8px;
        /* padding 控制方塊的內部空間 (上下 15px, 左右 10px)。若覺得太大，可改為 10px 5px */
        padding: 15px 10px; 
        text-align: center; 
        box-shadow: 0 4px 6px rgba(0,0,0,0.3);
    }
    .trend-title { 
        /* 控制上方標題大小 */
        font-size: 1.1rem; 
        color: #888; 
        font-weight: bold; 
        margin-bottom: 8px; 
        border-bottom: 1px solid #333; 
        padding-bottom: 5px;
    }
    .trend-status { 
        /* 控制下方「強勢格局」文字的大小 */
        font-size: 1.3rem; 
        font-weight: 900; 
    }
    
    /* ================================================== */
    /* 👉 這裡可以調節「技術指標」內部的文字大小 */
    /* ================================================== */
    .tech-title { font-size: 1.1rem; font-weight: bold; color: #fff; margin-bottom: 8px; }
    .tech-text { font-size: 1.0rem; color: #ddd; line-height: 1.6; }
    .tech-val { font-weight: bold; color: #00ffcc; font-family: monospace; font-size: 1.1rem;}

    /* 籌碼表專屬 CSS */
    .chip-table { width: 100%; text-align: center; border-collapse: collapse; font-size: 0.9rem; margin-top: 2px;}
    .chip-table th { color: #888; border-bottom: 1px solid #444; padding: 2px; font-weight: normal;}
    .chip-table td { padding: 4px 2px; border-bottom: 1px solid #2a2d3a; font-family: monospace; font-size: 1rem;}
    .buy-color { color: #ff3333; font-weight: bold; }
    .sell-color { color: #00cc00; font-weight: bold; }
</style>
''', unsafe_allow_html=True)

# 基礎預設名單
STOCK_NAMES = {
    "2330": "台積電", "2317": "鴻海", "2454": "聯發科", "2308": "台達電", "2382": "廣達",
    "3231": "緯創", "2356": "英業達", "3008": "大立光", "2324": "仁寶", "1802": "台玻",
    "2603": "長榮", "2609": "陽明", "2615": "萬海", "2881": "富邦金", "2882": "國泰金"
}

@st.cache_data(ttl=3600)
def get_all_tw_stock_names():
    names = STOCK_NAMES.copy()
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", timeout=5)
        for item in res.json():
            names[item['Code']] = item['Name']
    except:
        pass
    return names

CURRENT_STOCK_NAMES = get_all_tw_stock_names()

FAV_FILE = "favorites.json"
POOL_FILE = "pool.json"

def load_json(file_path, default_data):
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f: return json.load(f)
        except: pass
    return default_data

def save_json(file_path, data):
    with open(file_path, "w", encoding="utf-8") as f: json.dump(data, f)

if 'page' not in st.session_state: st.session_state.page = "home"
if 'current_stock' not in st.session_state: st.session_state.current_stock = "1802"
if 'favorites' not in st.session_state: st.session_state.favorites = load_json(FAV_FILE, ["1802", "2330"])
if 'custom_pool' not in st.session_state: st.session_state.custom_pool = load_json(POOL_FILE, list(STOCK_NAMES.keys()))
if 'nav_pool' not in st.session_state: st.session_state.nav_pool = st.session_state.custom_pool
if 'filter_buy_only' not in st.session_state: st.session_state.filter_buy_only = False

@st.cache_data(ttl=1800)
def fetch_twse_top_50():
    try:
        url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
        res = requests.get(url, timeout=10)
        df = pd.DataFrame(res.json())
        df['TradeVolume'] = pd.to_numeric(df['TradeVolume'], errors='coerce')
        df_stocks = df[df['Code'].str.match(r'^\d{4}$')]
        top_50 = df_stocks.sort_values(by='TradeVolume', ascending=False).head(50)
        return top_50['Code'].tolist()
    except:
        return list(STOCK_NAMES.keys())

# ─── 側邊欄控制 ───
st.sidebar.title("⭐ 我的自選股清單")
if st.session_state.favorites:
    for fav in st.session_state.favorites:
        fav_name = CURRENT_STOCK_NAMES.get(fav, fav)
        if st.sidebar.button(f"📊 {fav} {fav_name}", key=f"side_fav_{fav}", use_container_width=True):
            st.session_state.current_stock = fav
            st.session_state.page = "analysis"
            st.rerun()
else:
    st.sidebar.info("目前無自選股。")

st.sidebar.divider()
st.sidebar.title("⚙️ 雷達池設定")
if st.sidebar.button("🔄 自動抓取當日成交量前 50 名", use_container_width=True):
    st.session_state.custom_pool = fetch_twse_top_50()
    save_json(POOL_FILE, st.session_state.custom_pool)
    st.sidebar.success("池名單已保存！")
    st.rerun()

# ==========================================
# 1. 核心大腦 (技術數據運算與繪圖)
# ==========================================
@st.cache_data(ttl=300) 
def get_stock_data(ticker_number):
    if ticker_number == "^TWII": return yf.Ticker("^TWII").history(period="5d")
    base_ticker = ticker_number.upper().replace(".TW", "").replace(".TWO", "")
    try:
        df = yf.Ticker(f"{base_ticker}.TW").history(period="90d")
        if df.empty or len(df) < 20: df = yf.Ticker(f"{base_ticker}.TWO").history(period="90d")
        if df.empty or len(df) < 20: return None
        
        df['5MA'] = df['Close'].rolling(window=5).mean()
        df['10MA'] = df['Close'].rolling(window=10).mean()
        df['20MA'] = df['Close'].rolling(window=20).mean()
        df['60MA'] = df['Close'].rolling(window=60).mean()
        
        exp1 = df['Close'].ewm(span=12, adjust=False).mean()
        exp2 = df['Close'].ewm(span=26, adjust=False).mean()
        df['MACD'] = exp1 - exp2
        df['Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
        df['MACD_Hist'] = df['MACD'] - df['Signal']
        
        low_9 = df['Low'].rolling(window=9).min()
        high_9 = df['High'].rolling(window=9).max()
        rsv = (df['Close'] - low_9) / (high_9 - low_9) * 100
        df['K'] = rsv.ewm(com=2, adjust=False).mean()
        df['D'] = df['K'].ewm(com=2, adjust=False).mean()
        df['J'] = 3 * df['K'] - 2 * df['D']
        
        delta = df['Close'].diff()
        gain = delta.where(delta > 0, 0).ewm(alpha=1/14, adjust=False).mean()
        loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))
        
        df['STD'] = df['Close'].rolling(window=20).std()
        df['UB'] = df['20MA'] + 2 * df['STD']
        df['LB'] = df['20MA'] - 2 * df['STD']
        
        return df
    except: return None

def analyze_today(df, ticker_number):
    if df is None: return None
    today = df.iloc[-1]
    prev = df.iloc[-2]
    c_name = CURRENT_STOCK_NAMES.get(ticker_number, "")
    
    is_golden_pit = (today['Close'] > today['20MA']) and (today['Close'] < today['5MA']) and (today['J'] < 20)
    change_percent = (today['Close'] - prev['Close']) / prev['Close'] * 100
    
    return {
        "代號": ticker_number, "名稱": c_name, "ticker_raw": ticker_number,
        "收盤價": round(today['Close'], 2), "漲跌": round(today['Close'] - prev['Close'], 2),
        "漲跌幅": round(change_percent, 2), 
        "成交量": int(today['Volume'] / 1000),
        "5日均量": int(df['Volume'].tail(5).mean() / 1000),
        "5MA": round(today['5MA'], 2), "10MA": round(today['10MA'], 2),
        "20MA": round(today['20MA'], 2), "60MA": round(today['60MA'], 2) if not pd.isna(today['60MA']) else 0,
        "MACD": round(today['MACD'], 2), "MACD柱": round(today['MACD_Hist'], 3),
        "K": round(today['K'], 2), "D": round(today['D'], 2), "J值": round(today['J'], 2),
        "RSI": round(today['RSI'], 2) if not pd.isna(today['RSI']) else 50,
        "UB": round(today['UB'], 2) if not pd.isna(today['UB']) else 0,
        "LB": round(today['LB'], 2) if not pd.isna(today['LB']) else 0,
        "訊號": is_golden_pit
    }

def generate_mock_chips_html(df):
    recent_5 = df.tail(5).iloc[::-1]
    html = "<table class='chip-table'><tr><th>日期</th><th>外資</th><th>投信</th></tr>"
    for date, row in recent_5.iterrows():
        d_str = date.strftime("%m/%d")
        change = row['Close'] - row['Open']
        base_vol = row['Volume'] / 1000
        fi_buy = int(change * 200 + (base_vol * 0.08)) 
        it_buy = int(change * 80 + (base
