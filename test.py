import yfinance as yf
import streamlit as st
import pandas as pd
from datetime import datetime
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import json
import os

# ==========================================
# 0. 系統初始化 (完全移除危險的排版 CSS)
# ==========================================
st.set_page_config(page_title="專業交易雷達", layout="centered", initial_sidebar_state="collapsed")

# 只保留最基礎的隱藏選單與按鈕美化，絕不干擾預設排版
st.markdown("""
<style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    .stButton button { font-weight: bold !important; border-radius: 8px !important; }
</style>
""", unsafe_allow_html=True)

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

if 'page' not in st.session_state: st.session_state.page = "home"
if 'current_stock' not in st.session_state: st.session_state.current_stock = "1802"
if 'favorites' not in st.session_state: st.session_state.favorites = load_json(FAV_FILE, ["1802", "2330"])
if 'custom_pool' not in st.session_state: st.session_state.custom_pool = load_json(POOL_FILE, list(STOCK_NAMES.keys()))

# ─── 側邊欄 ───
st.sidebar.title("⭐ 我的自選股")
if st.session_state.favorites:
    for fav in st.session_state.favorites:
        fav_name = STOCK_NAMES.get(fav, fav)
        if st.sidebar.button(f"📊 {fav} {fav_name}", key=f"side_fav_{fav}", use_container_width=True):
            st.session_state.current_stock = fav
            st.session_state.page = "analysis"
            st.rerun()
else:
    st.sidebar.info("目前無自選股。")

st.sidebar.divider()
st.sidebar.title("⚙️ 雷達掃描池設定")
if st.sidebar.button("🔄 自動抓取市場熱門股", use_container_width=True):
    st.session_state.custom_pool = list(STOCK_NAMES.keys())
    save_json(POOL_FILE, st.session_state.custom_pool)
    st.rerun()

pool_input = st.sidebar.text_area("手動微調代號 (逗號分隔)", value=",".join(st.session_state.custom_pool), height=150)
if st.sidebar.button("💾 儲存手動更新", use_container_width=True):
    new_pool = [x.strip() for x in pool_input.split(",") if x.strip()]
    st.session_state.custom_pool = new_pool
    save_json(POOL_FILE, new_pool)
    st.sidebar.success("✅ 掃描池已更新！")

# ==========================================
# 1. 核心大腦 (技術指標與運算)
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

# ==========================================
# 2. 畫面路由 (SPA 導航控制)
# ==========================================

# ─── 首頁模式 (Home) ───
if st.session_state.page == "home":
    st.title("🇹🇼 台股戰術監控總機")
    
    now = datetime.now()
    twii_df = get_stock_data("^TWII")
    twii_close = twii_df['Close'].iloc[-1] if twii_df is not None else 0
    twii_change = (twii_df['Close'].iloc[-1] - twii_df['Close'].iloc[-2]) if twii_df is not None else 0
    
    # 乾淨的大盤顯示
    with st.container(border=True):
        st.metric(label=f"加權指數 ({now.strftime('%Y/%m/%d %H:%M')})", value=f"{twii_close:,.2f}", delta=f"{twii_change:,.2f}")
        
    search_val = st.text_input("🔍 快速搜尋代號", placeholder="輸入代號並按 Enter (例如: 2330)")
    if search_val:
        st.session_state.current_stock = search_val
        st.session_state.page = "analysis"
        st.rerun()

    st.subheader("📡 量大精選：超賣前 10 名榜單")
    scan_results = []
    with st.spinner('雷達掃描中...'):
        for stock in st.session_state.custom_pool:
            data = analyze_today(get_stock_data(stock), stock)
            if data: scan_results.append(data)
            
    if scan_results:
        df_results = pd.DataFrame(scan_results)
        df_top50_vol = df_results.sort_values(by="成交量", ascending=False).head(50)
        df_top10_j = df_top50_vol.sort_values(by="J值", ascending=True).head(10)
        
        # 使用 Streamlit 原生 container 製作不跑版的卡片
        for _, row in df_top10_j.iterrows():
            with st.container(border=True):
                is_fav = row['ticker_raw'] in st.session_state.favorites
                sign = "+" if row['漲跌'] > 0 else ""
                
                # 上半部：資訊文字
                st.markdown(f"### `{row['代號']}` {row['名稱']}")
                st.markdown(f"**收盤:** {row['收盤價']} | **漲跌:** {sign}{row['漲跌']} ({sign}{row['漲跌幅']}%) | **J值:** {row['J值']}")
                
                # 下半部：操作按鈕 (使用原生 columns，手機上自然排列絕不重疊)
                col1, col2 = st.columns(2)
                star_btn_text = "⭐ 移除自選" if is_fav else "☆ 加入自選"
                
                if col1.button(star_btn_text, key=f"star_{row['ticker_raw']}", use_container_width=True):
                    if is_fav: st.session_state.favorites.remove(row['ticker_raw'])
                    else: st.session_state.favorites.append(row['ticker_raw'])
                    save_json(FAV_FILE, st.session_state.favorites)
                    st.rerun()
                
                if col2.button("📊 進入解析", key=f"btn_{row['ticker_raw']}", use_container_width=True):
                    st.session_state.current_stock = row['ticker_raw']
                    st.session_state.page = "analysis"
                    st.rerun()
    else: st.info("目前雷達池無資料，請至左側設定選單新增。")

# ─── 解析頁模式 (Analysis) ───
elif st.session_state.page == "analysis":
    target = st.session_state.current_stock
    df_chart = get_stock_data(target)
    clean_name = STOCK_NAMES.get(target, "")
    
    # 頂部返回按鈕
    if st.button("⬅ 返回首頁", key="back_btn", use_container_width=True):
        st.session_state.page = "home"
        st.rerun()
    
    # 大盤看板
    now = datetime.now()
    twii_df = get_stock_data("^TWII")
    twii_close = twii_df['Close'].iloc[-1] if twii_df is not None else 0
    twii_change = (twii_df['Close'].iloc[-1] - twii_df['Close'].iloc[-2]) if twii_df is not None else 0
    with st.container(border=True):
        st.metric(label=f"加權指數 ({now.strftime('%Y/%m/%d %H:%M')})", value=f"{twii_close:,.2f}", delta=f"{twii_change:,.2f}")
    
    if df_chart is not None:
        data = analyze_today(df_chart, target)
        
        # 標題
        sign = "+" if data['漲跌'] > 0 else ""
        st.markdown(f"<h2 style='text-align: center;'>🎯 {target} {clean_name}</h2>", unsafe_allow_html=True)
        st.markdown(f"<h3 style='text-align: center;'>{data['收盤價']} ({sign}{data['漲跌幅']}%)</h3>", unsafe_allow_html=True)
        
        # 戰術判定 (使用原生防呆訊息框)
        if data['訊號']:
            st.success("✅ **戰術判定：【極佳買點】** 股價穩在月線之上，短線急跌破 5 日線，且 KDJ 極度超賣。符合買黑黃金坑條件！")
        else:
            if data['J值'] >= 80:
                st.error("⚠️ **戰術判定：【高檔過熱】** J值過高，有回檔風險，嚴禁追高！")
            elif data['收盤價'] < data['20MA']:
                st.warning("⛔ **戰術判定：【趨勢偏空】** 股價跌破月線支撐，中線趨勢轉弱。")
            else:
                st.info("⏳ **戰術判定：【觀望中】** 雖然在多頭趨勢，但目前未達極度超賣區，建議耐心等待。")
        
        # 技術指標 (使用原生卡片，手機上會自動排版不變形)
        st.subheader("📊 技術指標參數")
        
        c1, c2 = st.columns(2)
        with c1.container(border=True):
            st.markdown(f"**均線 (MA)**\n\n5T: `{data['5MA']}`\n\n10T: `{data['10MA']}`\n\n20T: `{data['20MA']}`")
        with c2.container(border=True):
            st.markdown(f"**動能 (MACD)**\n\nDIF: `{data['MACD']}`\n\nOSC: `{data['MACD柱']}`")
            
        c3, c4 = st.columns(2)
        with c3.container(border=True):
            st.markdown(f"**隨機指標 (KDJ)**\n\nK: `{data['K']}`\n\nD: `{data['D']}`\n\nJ: `{data['J值']}`")
        with c4.container(border=True):
            st.markdown(f"**市場熱度**\n\n成交量:\n\n`{data['成交量']} 張`")

        # 圖表
        fig = draw_professional_chart(df_chart, target, data['收盤價'])
        st.plotly_chart(fig, use_container_width=True)
        
        st.divider()

        # 多空趨勢
        st.subheader("📈 多空趨勢判定")
        t_short = "🔼 多頭 (站上5T)" if data['收盤價'] > data['5MA'] else "🔽 跌破5T"
        t_mid = "🔼 多頭 (站上20T)" if data['收盤價'] > data['20MA'] else "🔽 跌破20T"
        t_long = "🔼 多頭 (站上季線)" if data['收盤價'] > data['60MA'] else "🔽 跌破季線"
        
        t1, t2, t3 = st.columns(3)
        with t1.container(border=True): st.markdown(f"**日線**\n\n{t_short}")
        with t2.container(border=True): st.markdown(f"**周線**\n\n{t_mid}")
        with t3.container(border=True): st.markdown(f"**月線**\n\n{t_long}")
        
        st.markdown("<br>", unsafe_allow_html=True)
        
        # 底部自選
        if target in st.session_state.favorites:
            if st.button("❌ 移除自選", use_container_width=True):
                st.session_state.favorites.remove(target)
                save_json(FAV_FILE, st.session_state.favorites) 
                st.rerun()
        else:
            if st.button("⭐ 加入自選", use_container_width=True):
                st.session_state.favorites.append(target)
                save_json(FAV_FILE, st.session_state.favorites) 
                st.rerun()
    else: st.error("無法載入該股票資料，請確認代號是否正確。")
