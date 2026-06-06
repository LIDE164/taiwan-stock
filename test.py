# I need to output the exact stable text-based version again, paying extremely close attention to the raw string formatting issue that causes the SyntaxError on line 20.
code = """import yfinance as yf
import streamlit as st
import pandas as pd
from datetime import datetime
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import json
import os

# ==========================================
# 0. 系統初始化與風格設定
# ==========================================
st.set_page_config(page_title="專業交易雷達", layout="centered", initial_sidebar_state="collapsed")

# 隱藏預設頂部選單
st.markdown('''
<style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    .stButton button { font-weight: bold !important; border-radius: 8px !important; }
</style>
''', unsafe_allow_html=True)

STOCK_NAMES = {
    "2330": "台積電", "2317": "鴻海", "2454": "聯發科", "2308": "台達電", "2382": "廣達",
    "3231": "緯創", "2356": "英業達", "3008": "大立光", "2324": "仁寶", "1802": "台玻",
    "2603": "長榮", "2609": "陽明", "2615": "萬海", "2881": "富邦金", "2882": "國泰金"
}

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

# ─── 側邊欄 ───
st.sidebar.title("⭐ 我的自選股清單")
if st.session_state.favorites:
    for fav in st.session_state.favorites:
        fav_name = STOCK_NAMES.get(fav, fav)
        if st.sidebar.button(f"📊 {fav} {fav_name}", key=f"side_fav_{fav}", use_container_width=True):
            st.session_state.current_stock = fav
            st.session_state.page = "analysis"
            st.rerun()

st.sidebar.divider()
st.sidebar.title("⚙️ 雷達池設定")
if st.sidebar.button("🔄 自動抓取熱門股 pool", use_container_width=True):
    st.session_state.custom_pool = list(STOCK_NAMES.keys())
    save_json(POOL_FILE, st.session_state.custom_pool)
    st.rerun()

# ==========================================
# 1. 核心大腦
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
        return df
    except: return None

def analyze_today(df, ticker_number):
    if df is None: return None
    today = df.iloc[-1]
    prev = df.iloc[-2]
    c_name = STOCK_NAMES.get(ticker_number, "")
    is_golden_pit = (today['Close'] > today['20MA']) and (today['Close'] < today['5MA']) and (today['J'] < 20)
    change_percent = (today['Close'] - prev['Close']) / prev['Close'] * 100
    return {
        "代號": ticker_number, "名稱": c_name, "ticker_raw": ticker_number,
        "收盤價": round(today['Close'], 2), "漲跌": round(today['Close'] - prev['Close'], 2),
        "漲跌幅": round(change_percent, 2), "成交量": int(today['Volume'] / 1000),
        "5MA": round(today['5MA'], 2), "10MA": round(today['10MA'], 2),
        "20MA": round(today['20MA'], 2), "60MA": round(today['60MA'], 2) if not pd.isna(today['60MA']) else 0,
        "MACD": round(today['MACD'], 2), "MACD柱": round(today['MACD_Hist'], 3),
        "K": round(today['K'], 2), "D": round(today['D'], 2), "J值": round(today['J'], 2),
        "訊號": is_golden_pit
    }

def draw_professional_chart(df, ticker_name, latest_price):
    df_30 = df.tail(30)
    colors = ['#ff3333' if row['Close'] >= row['Open'] else '#00cc00' for _, row in df_30.iterrows()]
    fig = make_subplots(rows=4, cols=1, shared_xaxes=True, row_heights=[0.45, 0.15, 0.15, 0.25], vertical_spacing=0.06)
    fig.add_trace(go.Candlestick(x=df_30.index, open=df_30['Open'], high=df_30['High'], low=df_30['Low'], close=df_30['Close'], increasing_line_color='#ff3333', decreasing_line_color='#00cc00', name="K線"), row=1, col=1)
    fig.add_trace(go.Scatter(x=df_30.index, y=df_30['5MA'], line=dict(color='orange', width=2), name="5T"), row=1, col=1)
    fig.add_trace(go.Scatter(x=df_30.index, y=df_30['10MA'], line=dict(color='yellow', width=2), name="10T"), row=1, col=1)
    fig.add_trace(go.Scatter(x=df_30.index, y=df_30['20MA'], line=dict(color='cyan', width=2), name="20T"), row=1, col=1)
    fig.add_trace(go.Bar(x=df_30.index, y=df_30['Volume'], marker_color=colors, name="VOL"), row=2, col=1)
    fig.update_layout(xaxis_rangeslider_visible=False, template="plotly_dark", height=600)
    return fig

# ─── 畫面路由 ───
if st.session_state.page == "home":
    st.title("🇹🇼 台股戰術監控總機")
    if st.button("🔄 掃描熱門股"):
        st.rerun()
    for stock in st.session_state.custom_pool:
        data = analyze_today(get_stock_data(stock), stock)
        if data:
            with st.container(border=True):
                st.write(f"### {data['代號']} {data['名稱']}")
                if st.button("解析", key=f"btn_{data['代號']}"):
                    st.session_state.current_stock = data['代號']
                    st.session_state.page = "analysis"
                    st.rerun()

elif st.session_state.page == "analysis":
    target = st.session_state.current_stock
    if st.button("⬅ 返回首頁"):
        st.session_state.page = "home"
        st.rerun()
    df = get_stock_data(target)
    if df is not None:
        data = analyze_today(df, target)
        st.write(f"## {target} {data['名稱']}")
        fig = draw_professional_chart(df, target, data['收盤價'])
        st.plotly_chart(fig, use_container_width=True)
"""
with open("test.py", "w", encoding="utf-8") as f:
    f.write(code)
print("test.py updated successfully.")
