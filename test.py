import yfinance as yf
import streamlit as st
import pandas as pd
import requests
from datetime import datetime
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import json
import os
import re

# ==========================================
# 0. 系統初始化與風格設定
# ==========================================
st.set_page_config(page_title="專業交易雷達", layout="centered", initial_sidebar_state="collapsed")

# 隱藏預設頂部選單，保持介面極簡乾淨
st.markdown('''
<style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    
    /* 左上角側欄箭頭旁加上文字 */
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
    
    /* 解析頁面股價凍結置頂 */
    .sticky-header {
        position: sticky;
        top: 0;
        z-index: 999;
        background-color: rgba(26, 28, 36, 0.95);
        padding: 10px 0;
        border-bottom: 1px solid #333;
        backdrop-filter: blur(5px);
        margin-top: -15px;
        margin-bottom: 15px;
    }
    
    /* 多空趨勢的單行三格方塊設計 */
    .trend-box {
        background-color: #1a1c24; border: 1px solid #333; border-radius: 8px;
        padding: 15px 10px; text-align: center; box-shadow: 0 4px 6px rgba(0,0,0,0.3);
    }
    .trend-title { font-size: 1.1rem; color: #888; font-weight: bold; margin-bottom: 8px; border-bottom: 1px solid #333; padding-bottom: 5px;}
    .trend-status { font-size: 1.3rem; font-weight: 900; }
    
    /* 星星按鈕微調，讓它能跟名稱放在一起 */
    .star-btn {
        background: transparent; border: none; color: #ffcc00; font-size: 1.5rem; 
        cursor: pointer; padding: 0; margin-left: 8px;
    }
</style>
''', unsafe_allow_html=True)

# 內建基礎股票池
STOCK_NAMES = {
    "2330": "台積電", "2317": "鴻海", "2454": "聯發科", "2308": "台達電", "2382": "廣達",
    "3231": "緯創", "2356": "英業達", "3008": "大立光", "2324": "仁寶", "1802": "台玻",
    "3362": "先進光", "2603": "長榮", "2609": "陽明", "2615": "萬海", "2881": "富邦金",
    "2882": "國泰金", "2891": "中信金", "2886": "兆豐金", "2303": "聯電", "2409": "友達",
    "3481": "群創", "2344": "華邦電", "2408": "南亞科", "2379": "瑞昱", "3034": "聯詠",
    "2301": "光寶科", "2395": "研華", "2357": "華碩", "2353": "宏碁", "2371": "大同",
    "1504": "東元", "1519": "華城", "1513": "中興電", "1605": "華新", "2002": "中鋼",
    "2618": "長榮航", "2610": "華航", "3037": "欣興", "3189": "景碩", "8046": "南電",
    "2368": "金像電", "6269": "台郡", "2313": "華通", "2449": "京元電子", "3293": "鈊象",
    "3042": "晶技", "8147": "正凌", "2360": "致茂", "6505": "台塑化", "1301": "台塑"
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

# 狀態初始化
if 'page' not in st.session_state: st.session_state.page = "home"
if 'current_stock' not in st.session_state: st.session_state.current_stock = "1802"
if 'favorites' not in st.session_state: st.session_state.favorites = load_json(FAV_FILE, ["1802", "2330"])
if 'custom_pool' not in st.session_state: st.session_state.custom_pool = load_json(POOL_FILE, list(STOCK_NAMES.keys()))
if 'dynamic_names' not in st.session_state: st.session_state.dynamic_names = {}

# 合併內建名單與動態抓取的名單
CURRENT_STOCK_NAMES = {**STOCK_NAMES, **st.session_state.dynamic_names}

# ─── 自動抓取 TWSE 證交所 API ───
@st.cache_data(ttl=1800) # 快取 30 分鐘，避免過度頻繁請求被阻擋
def fetch_twse_top_50():
    try:
        url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
        res = requests.get(url, timeout=10)
        data = res.json()
        df = pd.DataFrame(data)
        
        # 將成交量轉為數值格式
        df['TradeVolume'] = pd.to_numeric(df['TradeVolume'], errors='coerce')
        
        # 僅保留一般個股 (代號為4位純數字，排除權證、ETF等)
        df_stocks = df[df['Code'].str.match(r'^\d{4}$')]
        
        # 依成交量排序，取前 50 名
        top_50 = df_stocks.sort_values(by='TradeVolume', ascending=False).head(50)
        return top_50[['Code', 'Name']].to_dict('records')
    except Exception as e:
        return []

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

# 全新升級的自動抓取功能
if st.sidebar.button("🔄 自動抓取當日成交量前 50 名", use_container_width=True):
    with st.spinner("連線證交所抓取最新數據中..."):
        top_stocks = fetch_twse_top_50()
        if top_stocks:
            new_pool = []
            for item in top_stocks:
                st.session_state.dynamic_names[item['Code']] = item['Name']
                new_pool.append(item['Code'])
            
            st.session_state.custom_pool = new_pool
            save_json(POOL_FILE, st.session_state.custom_pool)
            st.sidebar.success("✅ 已更新為當日成交量前 50 名！")
            st.rerun()
        else:
            st.sidebar.error("❌ 抓取失敗，請稍後再試。")

pool_input = st.sidebar.text_area("自訂股票池代號 (逗號分隔)", value=",".join(st.session_state.custom_pool), height=150)
if st.sidebar.button("💾 儲存更新池", use_container_width=True):
    new_pool = [x.strip() for x in pool_input.split(",") if x.strip()]
    st.session_state.custom_pool = new_pool
    save_json(POOL_FILE, new_pool)
    st.sidebar.success("池名單已保存！")

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
    
    last_row = df_30.iloc[-1]
    latest_vol = last_row['Volume']
    latest_macd = last_row['MACD']
    latest_j = last_row['J']
    
    fig = make_subplots(rows=4, cols=1, shared_xaxes=True, row_heights=[0.45, 0.15, 0.15, 0.25], vertical_spacing=0.06)
    
    fig.add_trace(go.Candlestick(x=df_30.index, open=df_30['Open'], high=df_30['High'], low=df_30['Low'], close=df_30['Close'], increasing_line_color='#ff3333', decreasing_line_color='#00cc00', name="K線"), row=1, col=1)
    fig.add_trace(go.Scatter(x=df_30.index, y=df_30['5MA'], line=dict(color='orange', width=2), name="5T"), row=1, col=1)
    fig.add_trace(go.Scatter(x=df_30.index, y=df_30['10MA'], line=dict(color='yellow', width=2), name="10T"), row=1, col=1)
    fig.add_trace(go.Scatter(x=df_30.index, y=df_30['20MA'], line=dict(color='cyan', width=2), name="20T"), row=1, col=1)
    fig.add_hline(y=latest_price, line_dash="dash", line_color="#ffcc00", row=1, col=1, annotation_text=f"現價: {latest_price:.2f}", annotation_position="top right", annotation_font=dict(size=14, color="#ffcc00"))
    
    fig.add_trace(go.Bar(x=df_30.index, y=df_30['Volume'], marker_color=colors, name="VOL"), row=2, col=1)
    fig.add_hline(y=latest_vol, line_dash="dash", line_color="#888888", row=2, col=1, annotation_text=f"VOL: {latest_vol:,.0f}", annotation_position="top right", annotation_font=dict(size=14, color="#cccccc"))
    
    macd_colors = ['#ff3333' if val > 0 else '#00cc00' for val in df_30['MACD_Hist']]
    fig.add_trace(go.Bar(x=df_30.index, y=df_30['MACD_Hist'], marker_color=macd_colors, name="OSC (柱)"), row=3, col=1)
    fig.add_trace(go.Scatter(x=df_30.index, y=df_30['MACD'], line=dict(color='white', width=1.5), name="DIF"), row=3, col=1)
    fig.add_trace(go.Scatter(x=df_30.index, y=df_30['Signal'], line=dict(color='yellow', width=1.5), name="MACD"), row=3, col=1)
    fig.add_hline(y=latest_macd, line_dash="dash", line_color="white", row=3, col=1, annotation_text=f"DIF: {latest_macd:.2f}", annotation_position="top right", annotation_font=dict(size=14, color="white"))
    
    fig.add_trace(go.Scatter(x=df_30.index, y=df_30['K'], line=dict(color='white', width=1.5), name="K"), row=4, col=1)
    fig.add_trace(go.Scatter(x=df_30.index, y=df_30['D'], line=dict(color='yellow', width=1.5), name="D"), row=4, col=1)
    fig.add_trace(go.Scatter(x=df_30.index, y=df_30['J'], line=dict(color='magenta', width=1.5), name="J"), row=4, col=1)
    fig.add_hline(y=latest_j, line_dash="dash", line_color="magenta", row=4, col=1, annotation_text=f"J: {latest_j:.2f}", annotation_position="top right", annotation_font=dict(size=14, color="magenta"))
    
    fig.update_xaxes(title_text="CANDLESTICK / MA", row=1, col=1, title_font=dict(size=14, color="#888888", weight="bold"))
    fig.update_xaxes(title_text="VOLUME", row=2, col=1, title_font=dict(size=14, color="#888888", weight="bold"))
    fig.update_xaxes(title_text="MACD / OSC", row=3, col=1, title_font=dict(size=14, color="#888888", weight="bold"))
    fig.update_xaxes(title_text="KDJ", row=4, col=1, title_font=dict(size=14, color="#888888", weight="bold"))
    
    fig.update_layout(
        xaxis_rangeslider_visible=False, template="plotly_dark", height=850, 
        margin=dict(l=10, r=10, t=20, b=40), paper_bgcolor='#0e1117', plot_bgcolor='#0e1117', 
        hovermode='x unified', hoverlabel=dict(font_size=18)
    )
    return fig

def render_index_board():
    now = datetime.now()
    twii_df = get_stock_data("^TWII")
    twii_close = twii_df['Close'].iloc[-1] if twii_df is not None else 0
    twii_change = (twii_df['Close'].iloc[-1] - twii_df['Close'].iloc[-2]) if twii_df is not None else 0
    twii_color = '#ff3333' if twii_change >= 0 else '#00cc00'
    
    with st.container(border=True):
        st.markdown(f"<div style='text-align: center; color: #aaa; font-size: 1.2rem; font-weight: bold;'>加權指數 ({now.strftime('%m/%d %H:%M')})</div>", unsafe_allow_html=True)
        st.markdown(f"<div style='text-align: center; font-size: 2.5rem; font-weight: 900; color: {twii_color}; margin: 5px 0;'>{twii_close:,.2f}</div>", unsafe_allow_html=True)
        st.markdown(f"<div style='text-align: center; font-size: 1.3rem; font-weight: bold; color: {twii_color};'>漲跌: {'+' if twii_change > 0 else ''}{twii_change:,.2f}</div>", unsafe_allow_html=True)

# ==========================================
# 2. 畫面路由 (SPA 導航控制)
# ==========================================

# ─── 首頁模式 (Home) ───
if st.session_state.page == "home":
    st.markdown(f"<h1 style='text-align: center;'>🇹🇼 台股戰術監控總機</h1>", unsafe_allow_html=True)
    
    render_index_board()
        
    st.markdown("<h3 style='margin-top: 15px;'>🔍 快速搜尋個股</h3>", unsafe_allow_html=True)
    search_val = st.text_input("隱藏標籤2", placeholder="輸入代號並按 Enter (例如: 2330)", label_visibility="collapsed")
    if search_val:
        st.session_state.current_stock = search_val
        st.session_state.page = "analysis"
        st.rerun()

    st.markdown("<h3 style='margin-top: 20px;'>📡 今日黃金坑榜單 (超賣前 10 名)</h3>", unsafe_allow_html=True)
    scan_results = []
    with st.spinner('智慧雷達掃描中...'):
        for stock in st.session_state.custom_pool:
            data = analyze_today(get_stock_data(stock), stock)
            if data: scan_results.append(data)
            
    if scan_results:
        df_results = pd.DataFrame(scan_results)
        df_top50_vol = df_results.sort_values(by="成交量", ascending=False).head(50)
        df_top10_j = df_top50_vol.sort_values(by="J值", ascending=True).head(10)
        
        for _, row in df_top10_j.iterrows():
            with st.container(border=True):
                is_fav = row['ticker_raw'] in st.session_state.favorites
                star_icon = "⭐" if is_fav else "☆"
                sign = "+" if row['漲跌'] > 0 else ""
                p_color = "#ff3333" if row['漲跌'] >= 0 else "#00cc00"
                
                c_title, c_star = st.columns([8, 2])
                with c_title:
                    st.markdown(f"### `{row['代號']}` **{row['名稱']}**")
                with c_star:
                    if st.button(star_icon, key=f"star_{row['ticker_raw']}", use_container_width=True):
                        if is_fav: st.session_state.favorites.remove(row['ticker_raw'])
                        else: st.session_state.favorites.append(row['ticker_raw'])
                        save_json(FAV_FILE, st.session_state.favorites)
                        st.rerun()
                
                st.markdown(f'''
                <div style="background-color: #1a1c24; padding: 12px; border-radius: 8px; border: 1px solid #333; text-align: center; margin: 5px 0 10px 0;">
                    <span style="font-size: 2.6rem; font-weight: 900; color: {p_color};">{row['收盤價']}</span>
                    <span style="font-size: 1.3rem; font-weight: bold; color: {p_color}; margin-left: 12px;">{sign}{row['漲跌']} ({sign}{row['漲跌幅']}%)</span>
                </div>
                ''', unsafe_allow_html=True)
                
                st.markdown(f"📊 當前動態 ➜ **J值:** `{row['J值']}`")
                
                if st.button("📊 深度個股解析", key=f"btn_{row['ticker_raw']}", use_container_width=True):
                    st.session_state.current_stock = row['ticker_raw']
                    st.session_state.page = "analysis"
                    st.rerun()
    else: st.info("目前雷達池無資料，請至左側設定選單新增。")

# ─── 解析頁模式 (Analysis) ───
elif st.session_state.page == "analysis":
    target = st.session_state.current_stock
    df_chart = get_stock_data(target)
    clean_name = CURRENT_STOCK_NAMES.get(target, "")
    
    if df_chart is not None:
        data = analyze_today(df_chart, target)
        p_color = '#ff3333' if data['漲跌'] >= 0 else '#00cc00'
        sign = "+" if data['漲跌'] > 0 else ""
        
        # 凍結在頁面頂部的標題與股價
        st.markdown(f'''
        <div class="sticky-header">
            <h2 style='text-align: center; margin: 0; padding-bottom: 5px;'>🎯 {target} {clean_name}</h2>
            <h3 style='text-align: center; color: {p_color}; font-size: 2.2rem; font-weight: 900; margin: 0;'>{data['收盤價']} ({sign}{data['漲跌幅']}%)</h3>
        </div>
        ''', unsafe_allow_html=True)
        
        # 導航列
        nav_pool = st.session_state.custom_pool
        if target in nav_pool and len(nav_pool) > 1:
            idx = nav_pool.index(target)
            prev_stock = nav_pool[(idx - 1) % len(nav_pool)]
            next_stock = nav_pool[(idx + 1) % len(nav_pool)]
        else:
            prev_stock = nav_pool[-1] if nav_pool else ""
            next_stock = nav_pool[0] if nav_pool else ""

        c_nav1, c_nav2, c_nav3 = st.columns([1, 1, 1])
        with c_nav1:
            if prev_stock and st.button(f"⬅ {prev_stock}", use_container_width=True):
                st.session_state.current_stock = prev_stock
                st.rerun()
        with c_nav2:
            if st.button("🏠 首頁", use_container_width=True):
                st.session_state.page = "home"
                st.rerun()
        with c_nav3:
            if next_stock and st.button(f"{next_stock} ➡", use_container_width=True):
                st.session_state.current_stock = next_stock
                st.rerun()
            
        st.markdown("<br>", unsafe_allow_html=True)
        
        # 戰術判定框
        if data['訊號']:
            st.success("✅ **戰術判定：【極佳買點】** 股價穩在月線之上，短線急跌破 5 日線，且 KDJ 極度超賣。符合買黑黃金坑條件！")
        else:
            if data['J值'] >= 80:
                st.error("⚠️ **戰術判定：【高檔過熱】** J值過高，有回檔風險，嚴禁追高！")
            elif data['收盤價'] < data['20MA']:
                st.warning("⛔ **戰術判定：【趨勢偏空】** 股價跌破月線支撐，中線趨勢轉弱。")
            else:
                st.info("⏳ **戰術判定：【觀望中】** 雖然在多頭趨勢，但目前未達極度超賣區，建議耐心等待。")
        
        st.subheader("📊 技術指標參數")
        
        row1_col1, row1_col2 = st.columns(2)
        with row1_col1.container(border=True):
            st.markdown("#### 🔹 均線 (MA)")
            st.markdown(f"* 5T ➜ **`{data['5MA']}`**")
            st.markdown(f"* 10T ➜ **`{data['10MA']}`**")
            st.markdown(f"* 20T ➜ **`{data['20MA']}`**")
            
        with row1_col2.container(border=True):
            st.markdown("#### 🔹 動能 (MACD)")
            st.markdown(f"* DIF ➜ **`{data['MACD']}`**")
            st.markdown(f"* OSC ➜ **`{data['MACD柱']}`**")
            st.markdown("<br>", unsafe_allow_html=True) 
            
        row2_col1, row2_col2 = st.columns(2)
        with row2_col1.container(border=True):
            st.markdown("#### 🔹 隨機指標 (KDJ)")
            st.markdown(f"* K ➜ **`{data['K']}`**")
            st.markdown(f"* D ➜ **`{data['D']}`**")
            st.markdown(f"* J ➜ **`{data['J值']}`**")
            
        with row2_col2.container(border=True):
            st.markdown("#### 🔹 市場熱度")
            st.markdown(f"* 成交量 ➜")
            st.markdown(f"**`{data['成交量']} 張`**")
            st.markdown("<br>", unsafe_allow_html=True) 

        fig = draw_professional_chart(df_chart, target, data['收盤價'])
        st.plotly_chart(fig, use_container_width=True)
        st.markdown(f"<div style='text-align: center; font-size: 1.1rem; color: #888; margin-top: -10px;'>▲ {target} {clean_name} 技術指標綜合面板</div>", unsafe_allow_html=True)
        
        st.divider()

        st.subheader("📈 三級多空趨勢判定")
        
        t_short_text = "強勢格局" if data['收盤價'] > data['5MA'] else "短線弱勢"
        t_short_color = "#ff3333" if data['收盤價'] > data['5MA'] else "#00cc00"
        
        t_mid_text = "中期偏多" if data['收盤價'] > data['20MA'] else "跌破月線"
        t_mid_color = "#ff3333" if data['收盤價'] > data['20MA'] else "#00cc00"
        
        t_long_text = "長線保護" if data['收盤價'] > data['60MA'] else "趨勢轉空"
        t_long_color = "#ff3333" if data['收盤價'] > data['60MA'] else "#00cc00"
        
        t1, t2, t3 = st.columns(3)
        with t1:
            st.markdown(f"""
            <div class="trend-box">
                <div class="trend-title">短線 (日線)</div>
                <div class="trend-status" style="color: {t_short_color};">{t_short_text}</div>
            </div>
            """, unsafe_allow_html=True)
            
        with t2:
            st.markdown(f"""
            <div class="trend-box">
                <div class="trend-title">中線 (周線)</div>
                <div class="trend-status" style="color: {t_mid_color};">{t_mid_text}</div>
            </div>
            """, unsafe_allow_html=True)
            
        with t3:
            st.markdown(f"""
            <div class="trend-box">
                <div class="trend-title">長線 (月線)</div>
                <div class="trend-status" style="color: {t_long_color};">{t_long_text}</div>
            </div>
            """, unsafe_allow_html=True)
        
        st.markdown("<br>", unsafe_allow_html=True)
        
        if target in st.session_state.favorites:
            if st.button("❌ 從自選股移除", use_container_width=True):
                st.session_state.favorites.remove(target)
                save_json(FAV_FILE, st.session_state.favorites) 
                st.rerun()
        else:
            if st.button("⭐ 將此標的加入自選", use_container_width=True):
                st.session_state.favorites.append(target)
                save_json(FAV_FILE, st.session_state.favorites) 
                st.rerun()
    else: st.error("無法載入該股票資料，請確認代號是否正確。")
