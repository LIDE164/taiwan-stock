import yfinance as yf
import streamlit as st
import pandas as pd
from datetime import datetime
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import json
import os

# ==========================================
# 0. 系統初始化與自訂 CSS 視覺強化
# ==========================================
st.set_page_config(page_title="專業交易雷達", layout="centered", initial_sidebar_state="collapsed")

st.markdown("""
<style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    
    div[data-testid="stVerticalBlock"] div[data-testid="stVerticalBlock"] {
        background-color: #1a1c24;
        border-radius: 10px;
        padding: 15px;
        border: 1px solid #2b2e3b;
    }
    
    [data-testid="stMetricLabel"] p { font-size: 1.4rem !important; color: #cccccc !important; }
    [data-testid="stMetricValue"] { font-size: 2.2rem !important; font-weight: 900 !important; }
    [data-testid="stMetricDelta"] { font-size: 1.4rem !important; }

    .stButton button p { font-size: 1.5rem !important; font-weight: bold !important; }
    .stButton button { padding: 12px 0px !important; border-radius: 8px !important; }
    
    /* 解析頁面：一般方塊 */
    .metric-box {
        background-color: #1a1c24;
        border: 1px solid #333;
        border-radius: 8px;
        padding: 12px;
        font-size: 1.4rem;
        line-height: 1.6;
        color: #e0e0e0;
        box-shadow: 0 4px 6px rgba(0,0,0,0.3);
    }
    .metric-title { font-size: 1.2rem; color: #888; margin-bottom: 5px; font-weight: bold; text-align: center; }
    
    /* 需求修正：技術指標專屬微縮、換行對齊方塊 */
    .tech-box {
        background-color: #1a1c24;
        border: 1px solid #333;
        border-radius: 8px;
        padding: 12px 15px;
        font-size: 1.2rem; /* 字體大小適中 */
        line-height: 1.8; /* 加大行距讓數值不擁擠 */
        color: #cccccc;
        box-shadow: 0 4px 6px rgba(0,0,0,0.3);
        text-align: left; /* 靠左對齊讓數值排排站 */
    }
    .tech-title { 
        font-size: 1.1rem; 
        color: #888; 
        margin-bottom: 8px; 
        font-weight: bold; 
        text-align: center; /* 標題置中 */
        border-bottom: 1px solid #333; /* 加上分隔線更專業 */
        padding-bottom: 5px;
    }
    .val-highlight { color: #00ffcc; font-weight: bold; margin-left: 5px; } 
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
        if st.sidebar.button(f"📊 {fav} {fav_name}", key=f"side_fav_{fav}"):
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

st.sidebar.markdown("<div style='font-size:1rem; color:gray;'>手動微調代號 (請用逗號分隔)：</div>", unsafe_allow_html=True)
pool_input = st.sidebar.text_area("隱藏標籤", value=",".join(st.session_state.custom_pool), height=150, label_visibility="collapsed")
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
    
    display_name = f"{ticker_number}<br><span style='font-size: 1.3rem; color: gray;'>{c_name}</span>" if c_name else f"{ticker_number}"
    is_golden_pit = (today['Close'] > today['20MA']) and (today['Close'] < today['5MA']) and (today['J'] < 20)
    change_percent = (today['Close'] - prev['Close']) / prev['Close'] * 100
    
    return {
        "代號": display_name, "ticker_raw": ticker_number,
        "收盤價": round(today['Close'], 2), "漲跌": round(today['Close'] - prev['Close'], 2),
        "漲跌幅": round(change_percent, 2), "成交量": int(today['Volume'] / 1000),
        "5MA": round(today['5MA'], 2), "10MA": round(today['10MA'], 2),
        "20MA": round(today['20MA'], 2), "60MA": round(today['60MA'], 2) if not pd.isna(today['60MA']) else 0,
        "MACD": round(today['MACD'], 2), "MACD柱": round(today['MACD_Hist'], 3),
        "K": round(today['K'], 2), "D": round(today['D'], 2), "J值": round(today['J'], 2),
        "訊號": is_golden_pit
    }

def draw_professional_chart(df, ticker_name):
    df_30 = df.tail(30)
    colors = ['#ff3333' if row['Close'] >= row['Open'] else '#00cc00' for _, row in df_30.iterrows()]
    
    last_row = df_30.iloc[-1]
    latest_price = last_row['Close']
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
    now = datetime.now()
    twii_df = get_stock_data("^TWII")
    twii_close = twii_df['Close'].iloc[-1] if twii_df is not None else 0
    twii_change = (twii_df['Close'].iloc[-1] - twii_df['Close'].iloc[-2]) if twii_df is not None else 0
    
    col_h1, col_h2 = st.columns([2, 1])
    with col_h1:
        st.markdown(f"<h1 style='font-size: 2.5rem;'>🇹🇼 台股戰術監控總機</h1>", unsafe_allow_html=True)
        st.markdown(f"<span style='color:gray; font-size: 1.4rem;'>資料時間：{now.strftime('%Y/%m/%d %H:%M:%S')}</span>", unsafe_allow_html=True)
    with col_h2: st.metric("加權指數", f"{twii_close:,.2f}", f"{twii_change:,.2f}")
        
    st.divider()
    st.markdown("<h3 style='font-size: 1.8rem;'>🔍 快速搜尋</h3>", unsafe_allow_html=True)
    search_val = st.text_input("隱藏標籤2", placeholder="輸入代號並按 Enter (例如: 2330)", label_visibility="collapsed")
    if search_val:
        st.session_state.current_stock = search_val
        st.session_state.page = "analysis"
        st.rerun()

    st.markdown("<h3 style='font-size: 1.8rem;'>📡 量大精選：超賣前 10 名榜單</h3>", unsafe_allow_html=True)
    scan_results = []
    with st.spinner('掃描雷達池標的中...'):
        for stock in st.session_state.custom_pool:
            data = analyze_today(get_stock_data(stock), stock)
            if data: scan_results.append(data)
            
    if scan_results:
        df_results = pd.DataFrame(scan_results)
        df_top50_vol = df_results.sort_values(by="成交量", ascending=False).head(50)
        df_top10_j = df_top50_vol.sort_values(by="J值", ascending=True).head(10)
        
        for _, row in df_top10_j.iterrows():
            with st.container():
                c_star, c_name, c_price, c_btn = st.columns([1.5, 2.5, 4.5, 2.5])
                is_fav = row['ticker_raw'] in st.session_state.favorites
                star_icon = "⭐" if is_fav else "➕"
                
                st.markdown("<div style='padding-top:10px;'>", unsafe_allow_html=True)
                if c_star.button(star_icon, key=f"star_{row['ticker_raw']}", use_container_width=True):
                    if is_fav: st.session_state.favorites.remove(row['ticker_raw'])
                    else: st.session_state.favorites.append(row['ticker_raw'])
                    save_json(FAV_FILE, st.session_state.favorites)
                    st.rerun()
                
                c_name.markdown(f"<div style='font-size: 2.0rem; font-weight: 900; line-height: 1.2;'>{row['代號']}</div>", unsafe_allow_html=True)
                c_price.metric("收盤價", f"{row['收盤價']}", f"{row['漲跌']} ({row['漲跌幅']}%)")
                
                st.markdown("<br>", unsafe_allow_html=True) 
                if c_btn.button("📊 解析", key=f"btn_{row['ticker_raw']}", use_container_width=True):
                    st.session_state.current_stock = row['ticker_raw']
                    st.session_state.page = "analysis"
                    st.rerun()
                st.markdown("<hr style='margin:0.5em 0; border-color:#2b2e3b;'>", unsafe_allow_html=True)
    else: st.info("目前雷達池無資料，請至左側設定選單新增。")

# ─── 解析頁模式 (Analysis) ───
elif st.session_state.page == "analysis":
    target = st.session_state.current_stock
    df_chart = get_stock_data(target)
    clean_name = STOCK_NAMES.get(target, "")
    
    if st.button("⬅ 返回首頁", key="back_btn"):
        st.session_state.page = "home"
        st.rerun()
    
    now = datetime.now()
    twii_df = get_stock_data("^TWII")
    twii_close = twii_df['Close'].iloc[-1] if twii_df is not None else 0
    twii_change = (twii_df['Close'].iloc[-1] - twii_df['Close'].iloc[-2]) if twii_df is not None else 0
    twii_color = '#ff3333' if twii_change >= 0 else '#00cc00'
    
    st.markdown(
        f"<div style='text-align: center; background: #1a1c24; padding: 15px; border-radius: 8px; margin-bottom: 20px; border: 1px solid #333;'>"
        f"<span style='color: #a0a0a0; font-size: 1.4rem;'>加權指數 </span>"
        f"<strong style='color: {twii_color}; font-size: 2.2rem;'>{twii_close:,.2f} ({twii_change:,.2f})</strong><br>"
        f"<span style='color: #666; font-size: 1.2rem;'>{now.strftime('%Y/%m/%d %H:%M:%S')}</span>"
        f"</div>", 
        unsafe_allow_html=True
    )
    
    if df_chart is not None:
        data = analyze_today(df_chart, target)
        
        p_color = '#ff3333' if data['漲跌'] >= 0 else '#00cc00'
        sign = "+" if data['漲跌'] > 0 else ""
        st.markdown(
            f"<h2 style='text-align: center; font-size: 2.5rem;'>🎯 {target} {clean_name} &nbsp;"
            f"<span style='color:{p_color}; font-weight:900;'>{data['收盤價']} ({sign}{data['漲跌幅']}%)</span></h2>", 
            unsafe_allow_html=True
        )
        st.markdown("<br>", unsafe_allow_html=True)
        
        if data['訊號']:
            st.markdown("<div style='background-color:rgba(0, 204, 0, 0.2); padding:15px; border-radius:8px; font-size:1.5rem; font-weight:bold; color:#00ffcc;'>✅ 戰術判定：【極佳買點】 股價穩在月線之上，短線急跌破 5 日線，且 KDJ 極度超賣。符合買黑黃金坑條件！</div>", unsafe_allow_html=True)
        else:
            if data['J值'] >= 80:
                st.markdown("<div style='background-color:rgba(255, 51, 51, 0.2); padding:15px; border-radius:8px; font-size:1.5rem; font-weight:bold; color:#ff3333;'>⚠️ 戰術判定：【高檔過熱】 J值過高，有回檔風險，嚴禁追高！</div>", unsafe_allow_html=True)
            elif data['收盤價'] < data['20MA']:
                st.markdown("<div style='background-color:rgba(255, 165, 0, 0.2); padding:15px; border-radius:8px; font-size:1.5rem; font-weight:bold; color:orange;'>⛔ 戰術判定：【趨勢偏空】 股價跌破月線支撐，中線趨勢轉弱。</div>", unsafe_allow_html=True)
            else:
                st.markdown("<div style='background-color:rgba(100, 149, 237, 0.2); padding:15px; border-radius:8px; font-size:1.5rem; font-weight:bold; color:#6495ED;'>⏳ 戰術判定：【觀望中】 雖然在多頭趨勢，但目前未達極度超賣區，建議耐心等待。</div>", unsafe_allow_html=True)
        
        st.markdown("<br>", unsafe_allow_html=True)

        st.markdown("<h3 style='font-size: 1.8rem;'>📊 技術指標參數</h3>", unsafe_allow_html=True)
        
        # 需求修正：加入 <br> 換行，並套用新的 .tech-box CSS
        m1, m2, m3, m4 = st.columns(4)
        m1.markdown(f"<div class='tech-box'><div class='tech-title'>均線 (MA)</div>5T: <span class='val-highlight'>{data['5MA']}</span><br>10T: <span class='val-highlight'>{data['10MA']}</span><br>20T: <span class='val-highlight'>{data['20MA']}</span></div>", unsafe_allow_html=True)
        m2.markdown(f"<div class='tech-box'><div class='tech-title'>動能 (MACD)</div>DIF: <span class='val-highlight'>{data['MACD']}</span><br>OSC: <span class='val-highlight'>{data['MACD柱']}</span><br>&nbsp;</div>", unsafe_allow_html=True)
        m3.markdown(f"<div class='tech-box'><div class='tech-title'>隨機指標 (KDJ)</div>K: <span class='val-highlight'>{data['K']}</span><br>D: <span class='val-highlight'>{data['D']}</span><br>J: <span class='val-highlight'>{data['J值']}</span></div>", unsafe_allow_html=True)
        m4.markdown(f"<div class='tech-box' style='text-align:center;'><div class='tech-title'>市場熱度</div>成交量<br><span class='val-highlight' style='font-size:1.4rem;'>{data['成交量']}</span>張<br>&nbsp;</div>", unsafe_allow_html=True)
        
        st.markdown("<br>", unsafe_allow_html=True)

        fig = draw_professional_chart(df_chart, target)
        st.plotly_chart(fig, use_container_width=True)
        st.markdown(f"<div style='text-align: center; font-size: 1.4rem; color: #888; margin-top: -10px;'>▲ {target} {clean_name} 技術指標綜合面板</div>", unsafe_allow_html=True)
        
        st.divider()

        st.markdown("<h3 style='font-size: 1.8rem;'>📈 多空趨勢判定</h3>", unsafe_allow_html=True)
        t_short = f"<span style='color:#ff3333;'>🔼 多頭 (站上5T)</span>" if data['收盤價'] > data['5MA'] else f"<span style='color:#00cc00;'>🔽 跌破5T</span>"
        t_mid = f"<span style='color:#ff3333;'>🔼 多頭 (站上20T)</span>" if data['收盤價'] > data['20MA'] else f"<span style='color:#00cc00;'>🔽 跌破20T</span>"
        t_long = f"<span style='color:#ff3333;'>🔼 多頭 (站上季線)</span>" if data['收盤價'] > data['60MA'] else f"<span style='color:#00cc00;'>🔽 跌破季線</span>"
        
        c_t1, c_t2, c_t3 = st.columns(3)
        c_t1.markdown(f"<div class='metric-box' style='text-align:center;'><div class='metric-title'>日線 (短)</div>{t_short}</div>", unsafe_allow_html=True)
        c_t2.markdown(f"<div class='metric-box' style='text-align:center;'><div class='metric-title'>周線 (中)</div>{t_mid}</div>", unsafe_allow_html=True)
        c_t3.markdown(f"<div class='metric-box' style='text-align:center;'><div class='metric-title'>月線 (長)</div>{t_long}</div>", unsafe_allow_html=True)
        
        st.markdown("<br>", unsafe_allow_html=True)
        
        if target in st.session_state.favorites:
            if st.button("❌ 從我的自選股移除", use_container_width=True):
                st.session_state.favorites.remove(target)
                save_json(FAV_FILE, st.session_state.favorites) 
                st.rerun()
        else:
            if st.button("⭐ 加入我的自選股", use_container_width=True):
                st.session_state.favorites.append(target)
                save_json(FAV_FILE, st.session_state.favorites) 
                st.rerun()
    else: st.error("無法載入該股票資料，請確認代號是否正確。")
