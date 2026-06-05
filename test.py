import yfinance as yf
import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# ==========================================
# 0. 系統初始化與自訂 CSS 魔法 (打造 APP 質感)
# ==========================================
st.set_page_config(page_title="專業交易雷達", layout="centered", initial_sidebar_state="collapsed")

# 注入 CSS 讓畫面變成深色卡片風格
st.markdown("""
<style>
    /* 隱藏預設的頂部選單與 Footer */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    /* 打造長條狀卡片外觀 */
    div[data-testid="stVerticalBlock"] div[data-testid="stVerticalBlock"] {
        background-color: #1a1c24;
        border-radius: 10px;
        padding: 10px;
        border: 1px solid #2b2e3b;
    }
    /* 讓強調數值有霓虹螢光感 */
    .glow-text { color: #00ffcc; font-weight: bold; font-size: 1.2rem; }
    .glow-red { color: #ff3333; font-weight: bold; font-size: 1.2rem; }
</style>
""", unsafe_allow_html=True)

STOCK_NAMES = {
    "1802": "台玻", "8147": "正凌", "2330": "台積電", "2317": "鴻海",
    "2454": "聯發科", "2308": "台達電", "2881": "富邦金", "2882": "國泰金",
    "2603": "長榮", "2609": "陽明", "2382": "廣達", "3231": "緯創",
    "2356": "英業達", "3008": "大立光", "2324": "仁寶", "3362": "先進光"
}

# 狀態機：控制現在要顯示「首頁(home)」還是「解析頁(analysis)」
if 'page' not in st.session_state:
    st.session_state.page = "home"
if 'current_stock' not in st.session_state:
    st.session_state.current_stock = "1802"
if 'favorites' not in st.session_state:
    st.session_state.favorites = ["1802", "2330"]

# ==========================================
# 1. 核心大腦 (雙頻道自動切換)
# ==========================================
@st.cache_data(ttl=300) 
def get_stock_data(ticker_number):
    if ticker_number == "^TWII":
        return yf.Ticker("^TWII").history(period="5d")
        
    base_ticker = ticker_number.upper().replace(".TW", "").replace(".TWO", "")
    try:
        df = yf.Ticker(f"{base_ticker}.TW").history(period="90d")
        if df.empty or len(df) < 20:
            df = yf.Ticker(f"{base_ticker}.TWO").history(period="90d")
        if df.empty or len(df) < 20: return None
        
        df['5MA'] = df['Close'].rolling(window=5).mean()
        df['20MA'] = df['Close'].rolling(window=20).mean()
        
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
    except:
        return None

def analyze_today(df, ticker_number):
    if df is None: return None
    today = df.iloc[-1]
    prev = df.iloc[-2]
    c_name = STOCK_NAMES.get(ticker_number, "")
    display_name = f"{ticker_number} {c_name}" if c_name else ticker_number
    
    # 判斷是否符合「買黑黃金坑」
    is_golden_pit = (today['Close'] > today['20MA']) and (today['Close'] < today['5MA']) and (today['J'] < 20)
    
    # 格式化技術指標
    ma5_str = f"5T:{today['5MA']:.2f}"
    ma10_str = f"10T:{df['Close'].rolling(window=10).mean().iloc[-1]:.2f}" # 補上 10T 均線
    ma20_str = f"20T:{today['20MA']:.2f}"

    # 確保所有需要的數據都打包回傳
    return {
        "代號": display_name,
        "ticker_raw": ticker_number,
        "收盤價": round(today['Close'], 2),
        "漲跌": round(today['Close'] - prev['Close'], 2),
        "成交量": int(today['Volume'] / 1000),
        "5MA": round(today['5MA'], 2),
        "20MA": round(today['20MA'], 2),  # 👈 關鍵：必須要有這一行
        "J值": round(today['J'], 2),
        "MACD柱": round(today['MACD_Hist'], 3),
        "訊號": is_golden_pit
    }

def draw_professional_chart(df, ticker_name):
    colors = ['#ff3333' if row['Close'] >= row['Open'] else '#00cc00' for _, row in df.iterrows()]
    fig = make_subplots(rows=4, cols=1, shared_xaxes=True, row_heights=[0.5, 0.15, 0.15, 0.2], vertical_spacing=0.03)
    
    fig.add_trace(go.Candlestick(x=df.index, open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'], increasing_line_color='#ff3333', decreasing_line_color='#00cc00', name="K線"), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['5MA'], line=dict(color='orange', width=1.5), name="5MA"), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['20MA'], line=dict(color='cyan', width=1.5), name="20MA"), row=1, col=1)
    fig.add_trace(go.Bar(x=df.index, y=df['Volume'], marker_color=colors, name="成交量"), row=2, col=1)
    
    macd_colors = ['#ff3333' if val > 0 else '#00cc00' for val in df['MACD_Hist']]
    fig.add_trace(go.Bar(x=df.index, y=df['MACD_Hist'], marker_color=macd_colors, name="MACD柱"), row=3, col=1)
    
    fig.add_trace(go.Scatter(x=df.index, y=df['K'], line=dict(color='white', width=1), name="K"), row=4, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['D'], line=dict(color='yellow', width=1), name="D"), row=4, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['J'], line=dict(color='magenta', width=1), name="J"), row=4, col=1)
    fig.add_hline(y=20, line_dash="dash", line_color="#00ffcc", row=4, col=1)
    
    fig.update_layout(title=f"{ticker_name} 技術線圖", xaxis_rangeslider_visible=False, template="plotly_dark", height=800, margin=dict(l=10, r=10, t=40, b=10), paper_bgcolor='#0e1117', plot_bgcolor='#0e1117')
    return fig

# ==========================================
# 2. 畫面路由 (SPA 導航控制)
# ==========================================

# ─── 首頁模式 (Home) ───
if st.session_state.page == "home":
    # 頂部大盤與時間資訊 (APP Header)
    now = datetime.now()
    twii_df = get_stock_data("^TWII")
    twii_close = twii_df['Close'].iloc[-1] if twii_df is not None else 0
    twii_change = (twii_df['Close'].iloc[-1] - twii_df['Close'].iloc[-2]) if twii_df is not None else 0
    
    col_h1, col_h2 = st.columns([2, 1])
    with col_h1:
        st.markdown(f"## 🇹🇼 台股戰術監控總機")
        st.markdown(f"<span style='color:gray;'>資料時間：{now.strftime('%Y/%m/%d %H:%M:%S')}</span>", unsafe_allow_html=True)
    with col_h2:
        st.metric("加權指數", f"{twii_close:,.2f}", f"{twii_change:,.2f}")
        
    st.divider()
    
    # 搜尋與自選股區塊
    col_s1, col_s2 = st.columns([3, 1])
    with col_s1:
        search_val = st.text_input("🔍 輸入代號快速解析 (例如: 2330, 3362)", placeholder="輸入並按 Enter")
        if search_val:
            st.session_state.current_stock = search_val
            st.session_state.page = "analysis"
            st.rerun()
    with col_s2:
        if st.button("⭐ 查看我的自選股"):
            pass # 這裡可以展開自選股，為求簡潔我們先列在下方卡片

    # 自動掃描雷達池
    st.markdown("### 📡 今日黃金潛力榜 ")
    radar_pool = ["1802", "3362", "2330", "2317", "2454", "2308", "2603", "3231", "2356", "3008"]
    
    scan_results = []
    with st.spinner('雷達掃描中...'):
        for stock in radar_pool:
            data = analyze_today(get_stock_data(stock), stock)
            if data: scan_results.append(data)
            
    df_results = pd.DataFrame(scan_results)
    df_filtered = df_results[(df_results['J值'] < 30)] # 放寬一點條件讓卡片有東西顯示
    df_sorted = df_filtered.sort_values(by="J值", ascending=True).head(5)
    
    # 繪製長條狀卡片 (類似 ETF App)
    if not df_sorted.empty:
        for _, row in df_sorted.iterrows():
            with st.container():
                c1, c2, c3, c4, c5 = st.columns([2, 1.5, 1.5, 1.5, 1.5])
                c1.markdown(f"#### {row['代號']}")
                c2.metric("收盤價", f"{row['收盤價']}", f"{row['漲跌']}")
                c3.metric("J值", f"{row['J值']}")
                c4.markdown(f"動能: {row['MACD柱']}")
                
                # 點擊直接切換到解析頁面
                if c5.button("📊 進入解析", key=f"btn_{row['ticker_raw']}"):
                    st.session_state.current_stock = row['ticker_raw']
                    st.session_state.page = "analysis"
                    st.rerun()
                st.markdown("<hr style='margin:0.5em 0; border-color:#2b2e3b;'>", unsafe_allow_html=True)
    else:
        st.info("目前無符合超賣條件之標的。")

# ─── 解析頁模式 (Analysis) ───
elif st.session_state.page == "analysis":
    target = st.session_state.current_stock
    target_name = STOCK_NAMES.get(target, target)
    
    # 導覽列
    col_nav1, col_nav2 = st.columns([1, 4])
    if col_nav1.button("⬅ 返回首頁總覽"):
        st.session_state.page = "home"
        st.rerun()
        
    st.markdown(f"## 🎯 深度解析：{target} {target_name}")
    
    df_chart = get_stock_data(target)
    if df_chart is not None:
        analysis_data = analyze_today(df_chart, target)
        
        # 💥 核心功能：在最上方直接顯示適不適合購買的判定
        if analysis_data['訊號']:
            st.success("✅ **戰術判定：【極佳買點】** 股價穩在月線之上，短線急跌破 5 日線，且 KDJ 極度超賣。符合買黑黃金坑條件，可考慮建倉！")
        else:
            if analysis_data['J值'] >= 80:
                st.error("⚠️ **戰術判定：【高檔過熱】** J值過高，有回檔風險，嚴禁追高！")
            elif analysis_data['收盤價'] < analysis_data['20MA']:
                st.warning("⛔ **戰術判定：【趨勢偏空】** 股價跌破月線支撐，中線趨勢轉弱，請勿隨意接刀。")
            else:
                st.info("⏳ **戰術判定：【觀望中】** 雖然在多頭趨勢，但目前未達極度超賣區，建議耐心等待更安全的買黑時機。")
        
        # 繪製高階圖表
        fig = draw_professional_chart(df_chart, f"{target} {target_name}")
        st.plotly_chart(fig, use_container_width=True)
        
        # 底部自選股控制
        st.divider()
        if target in st.session_state.favorites:
            if st.button("❌ 從我的自選股移除"):
                st.session_state.favorites.remove(target)
                st.rerun()
        else:
            if st.button("⭐ 加入我的自選股"):
                st.session_state.favorites.append(target)
                st.rerun()
    else:
        st.error("無法載入該股票資料，請確認代號是否正確。")
