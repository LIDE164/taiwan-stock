import yfinance as yf
import streamlit as st
import pandas as pd
import requests
from datetime import datetime
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import json
import os

st.set_page_config(page_title="專業交易雷達", layout="centered", initial_sidebar_state="collapsed")

st.markdown("""
<style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    [data-testid="collapsedControl"] {
        border: 1px solid #444 !important; border-radius: 8px !important; background-color: #1a1c24 !important;
        padding: 5px 12px !important; display: flex !important; align-items: center !important; width: auto !important; transition: 0.3s;
    }
    [data-testid="collapsedControl"]::after { content: " ⭐ 我的自選股"; font-size: 1.1rem; font-weight: bold; color: #ffcc00; margin-left: 8px; }
    .stButton button { font-weight: bold !important; border-radius: 8px !important; }
    .sticky-header {
        position: sticky; top: 0; z-index: 999; background-color: rgba(26, 28, 36, 0.95);
        padding: 10px 0; border-bottom: 1px solid #333; backdrop-filter: blur(5px); margin-top: -15px; margin-bottom: 15px;
    }
    .trend-box { background-color: #1a1c24; border: 1px solid #333; border-radius: 8px; padding: 8px 5px; text-align: center; box-shadow: 0 4px 6px rgba(0,0,0,0.3); }
    .trend-title { font-size: 0.95rem; color: #888; font-weight: bold; margin-bottom: 4px; border-bottom: 1px solid #333; padding-bottom: 2px; white-space: nowrap; }
    .trend-status { font-size: 1.05rem; font-weight: 900; white-space: nowrap; }
    div[data-testid="stVerticalBlockBorderWrapper"] { padding: 4px !important; }
    .tech-title { font-size: 0.95rem; font-weight: bold; color: #fff; margin-bottom: 4px; text-align: center; border-bottom: 1px solid #333; padding-bottom: 2px; white-space: nowrap;}
    .tech-text { font-size: 0.85rem; color: #ddd; line-height: 1.3; display: flex; justify-content: space-between; padding: 0 2px;}
    .tech-val { font-weight: bold; color: #00ffcc; font-family: monospace; font-size: 0.95rem;}
    .chip-table { width: 100%; text-align: center; border-collapse: collapse; font-size: 0.8rem; margin-top: 2px;}
    .chip-table th { color: #888; border-bottom: 1px solid #444; padding: 2px; font-weight: normal; white-space: nowrap;}
    .chip-table td { padding: 2px 1px; border-bottom: 1px solid #2a2d3a; font-family: monospace; font-size: 0.85rem;}
    .buy-color { color: #ff3333; font-weight: bold; }
    .sell-color { color: #00cc00; font-weight: bold; }
    @media (max-width: 768px) {
        .trend-box { padding: 4px 1px; }
        .trend-title { font-size: 0.8rem; }
        .trend-status { font-size: 0.9rem; }
        .tech-title { font-size: 0.85rem; }
        .tech-text { font-size: 0.75rem; flex-direction: column; text-align: center;}
        .tech-val { font-size: 0.85rem; }
    }
</style>
""", unsafe_allow_html=True)

# 加強版字典，涵蓋常見的上市與上櫃股票
STOCK_NAMES = {
    "2330": "台積電", "2317": "鴻海", "2454": "聯發科", "2308": "台達電", "2382": "廣達",
    "3231": "緯創", "2356": "英業達", "3008": "大立光", "2324": "仁寶", "1802": "台玻",
    "2603": "長榮", "2609": "陽明", "2615": "萬海", "2881": "富邦金", "2882": "國泰金",
    "2376": "技嘉", "1785": "光洋科", "3293": "鈊象", "1519": "華城", "1513": "中興電"
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
if 'current_stock' not in st.session_state: st.session_state.current_stock = "2376"
if 'favorites' not in st.session_state: st.session_state.favorites = load_json(FAV_FILE, ["1802", "2330", "1785"])
if 'custom_pool' not in st.session_state: st.session_state.custom_pool = load_json(POOL_FILE, ["2330", "2317", "2454", "2382", "3231"])
if 'nav_pool' not in st.session_state: st.session_state.nav_pool = st.session_state.custom_pool
if 'filter_buy_only' not in st.session_state: st.session_state.filter_buy_only = False
if 'view_days' not in st.session_state: st.session_state.view_days = 60

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
        return ["2330", "2317", "2454", "2382", "3231"]

st.sidebar.title("⭐ 我的自選股")
if st.session_state.favorites:
    for fav in st.session_state.favorites:
        fav_name = STOCK_NAMES.get(fav, "")
        if st.sidebar.button(f"📊 {fav} {fav_name}", key=f"side_fav_{fav}", use_container_width=True):
            st.session_state.current_stock = fav
            st.session_state.page = "analysis"
            st.rerun()

st.sidebar.divider()
st.sidebar.title("⚙️ 雷達池設定")
if st.sidebar.button("🔄 更新熱門股", use_container_width=True):
    st.session_state.custom_pool = fetch_twse_top_50()
    save_json(POOL_FILE, st.session_state.custom_pool)
    st.sidebar.success("✅ 完成！")
    st.rerun()

@st.cache_data(ttl=300) 
def get_stock_data(ticker_number):
    try:
        # 強制去除使用者輸入的空白
        base_ticker = str(ticker_number).strip().upper().replace(".TW", "").replace(".TWO", "")
        
        if base_ticker == "^TWII":
            df = yf.Ticker("^TWII").history(period="1y")
        else:
            # 防呆：先找上市 (.TW)，找不到再找上櫃 (.TWO)，最後找美股原代號
            df = yf.Ticker(f"{base_ticker}.TW").history(period="1y")
            if df.empty or len(df) < 20: 
                df = yf.Ticker(f"{base_ticker}.TWO").history(period="1y")
            if df.empty or len(df) < 20:
                df = yf.Ticker(base_ticker).history(period="1y")
                
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

def generate_mock_chips_html(df):
    recent_5 = df.tail(5).iloc[::-1]
    html = "<table class='chip-table'><tr><th>日期</th><th>外資</th><th>投信</th></tr>"
    for date, row in recent_5.iterrows():
        d_str = date.strftime("%m/%d")
        change = row['Close'] - row['Open']
        base_vol = row['Volume'] / 1000
        
        fi_buy = int(base_vol * 0.15) + 120 
        it_buy = int(change * 80 + (base_vol * 0.03))
        if it_buy == 0: it_buy = -int(base_vol * 0.005) - 5
        
        fi_class = "buy-color" 
        it_class = "buy-color" if it_buy > 0 else "sell-color"
        
        fi_str = f"+{fi_buy:,}"
        it_str = f"+{it_buy:,}" if it_buy > 0 else f"{it_buy:,}"
        
        html += f"<tr><td>{d_str}</td><td class='{fi_class}'>{fi_str}</td><td class='{it_class}'>{it_str}</td></tr>"
    html += "</table>"
    return html

def analyze_today(df, ticker_number):
    if df is None: return None
    today = df.iloc[-1]
    prev = df.iloc[-2]
    
    # 支援沒在字典裡的股票也能分析
    c_name = STOCK_NAMES.get(ticker_number, "")
    
    is_golden_pit = (today['Close'] > today['20MA']) and (today['Close'] < today['5MA']) and (today['J'] < 20)
    change_percent = (today['Close'] - prev['Close']) / prev['Close'] * 100
    
    return {
        "代號": ticker_number, "名稱": c_name, "ticker_raw": ticker_number,
        "收盤價": round(today['Close'], 2), "漲跌": round(today['Close'] - prev['Close'], 2),
        "漲跌幅": round(change_percent, 2), 
        "成交量": int(today['Volume'] / 1000),
        "5日均量": int(df['Volume'].tail(5).mean() / 1000),
        "5MA": round(today['5MA'], 2), "10MA": round(today['10MA'], 2), "20MA": round(today['20MA'], 2),
        "MACD": round(today['MACD'], 2), "MACD柱": round(today['MACD_Hist'], 3),
        "K": round(today['K'], 2), "D": round(today['D'], 2), "J值": round(today['J'], 2),
        "訊號": is_golden_pit
    }

def draw_professional_chart(df, ticker_name, latest_price, view_days):
    df_view = df.tail(view_days)
    colors = ['#ff3333' if row['Close'] >= row['Open'] else '#00cc00' for _, row in df_view.iterrows()]
    last_row = df_view.iloc[-1]
    
    fig = make_subplots(rows=4, cols=1, shared_xaxes=True, row_heights=[0.45, 0.15, 0.15, 0.25], vertical_spacing=0.06)
    
    fig.add_trace(go.Candlestick(x=df_view.index, open=df_view['Open'], high=df_view['High'], low=df_view['Low'], close=df_view['Close'], increasing_line_color='#ff3333', decreasing_line_color='#00cc00', name="K線"), row=1, col=1)
    fig.add_trace(go.Scatter(x=df_view.index, y=df_view['5MA'], line=dict(color='orange', width=2), name="5T"), row=1, col=1)
    fig.add_trace(go.Scatter(x=df_view.index, y=df_view['10MA'], line=dict(color='yellow', width=2), name="10T"), row=1, col=1)
    fig.add_trace(go.Scatter(x=df_view.index, y=df_view['20MA'], line=dict(color='cyan', width=2), name="20T"), row=1, col=1)
    
    fig.add_hline(y=latest_price, line_dash="dash", line_color="#ffcc00", row=1, col=1, annotation_text=f"今日收盤: {latest_price:.2f}", annotation_position="top right", annotation_font=dict(size=15, color="#ffcc00", weight="bold"))
    
    fig.add_trace(go.Bar(x=df_view.index, y=df_view['Volume'], marker_color=colors, name="VOL"), row=2, col=1)
    
    macd_colors = ['#ff3333' if val > 0 else '#00cc00' for val in df_view['MACD_Hist']]
    fig.add_trace(go.Bar(x=df_view.index, y=df_view['MACD_Hist'], marker_color=macd_colors, name="OSC"), row=3, col=1)
    fig.add_trace(go.Scatter(x=df_view.index, y=df_view['MACD'], line=dict(color='white', width=1.5), name="DIF"), row=3, col=1)
    fig.add_trace(go.Scatter(x=df_view.index, y=df_view['Signal'], line=dict(color='yellow', width=1.5), name="MACD"), row=3, col=1)
    
    fig.add_trace(go.Scatter(x=df_view.index, y=df_view['K'], line=dict(color='white', width=1.5), name="K"), row=4, col=1)
    fig.add_trace(go.Scatter(x=df_view.index, y=df_view['D'], line=dict(color='yellow', width=1.5), name="D"), row=4, col=1)
    fig.add_trace(go.Scatter(x=df_view.index, y=df_view['J'], line=dict(color='magenta', width=1.5), name="J"), row=4, col=1)

    fig.add_annotation(x=0.01, y=0.98, xref="paper", yref="y domain", text=f"現價:{latest_price:.1f} | 5T:{last_row['5MA']:.1f} | 10T:{last_row['10MA']:.1f} | 20T:{last_row['20MA']:.1f}", showarrow=False, font=dict(color="#ffcc00", size=12), xanchor="left", bgcolor="rgba(26,28,36,0.6)")
    fig.add_annotation(x=0.01, y=0.95, xref="paper", yref="y2 domain", text=f"VOL: {last_row['Volume']:,.0f}", showarrow=False, font=dict(color="#ccc", size=12), xanchor="left", bgcolor="rgba(26,28,36,0.6)")
    fig.add_annotation(x=0.01, y=0.95, xref="paper", yref="y3 domain", text=f"MACD:{last_row['MACD']:.2f} | DIF:{last_row['Signal']:.2f} | OSC:{last_row['MACD_Hist']:.2f}", showarrow=False, font=dict(color="#ccc", size=12), xanchor="left", bgcolor="rgba(26,28,36,0.6)")
    fig.add_annotation(x=0.01, y=0.95, xref="paper", yref="y4 domain", text=f"K:{last_row['K']:.2f} | D:{last_row['D']:.2f} | J:{last_row['J']:.2f}", showarrow=False, font=dict(color="#ccc", size=12), xanchor="left", bgcolor="rgba(26,28,36,0.6)")

    fig.update_xaxes(fixedrange=True, showgrid=True, gridcolor='rgba(255,255,255,0.1)')
    fig.update_yaxes(fixedrange=True, showgrid=True, gridcolor='rgba(255,255,255,0.1)')
    fig.update_xaxes(title_text="", row=1, col=1)
    fig.update_xaxes(title_text="", row=2, col=1)
    fig.update_xaxes(title_text="", row=3, col=1)
    fig.update_xaxes(title_text="", row=4, col=1)
    
    fig.update_layout(
        xaxis_rangeslider_visible=False, template="plotly_dark", height=850, 
        margin=dict(l=10, r=10, t=10, b=10), paper_bgcolor='#0e1117', plot_bgcolor='#0e1117', 
        hovermode='x unified', hoverlabel=dict(font_size=13, bgcolor="rgba(26,28,36,0.9)"),
        dragmode=False, 
        legend=dict(orientation="h", yanchor="top", y=-0.05, xanchor="center", x=0.5)
    )
    return fig

def render_index_board():
    now = datetime.now()
    twii_df = get_stock_data("^TWII")
    twii_close = 0
    twii_change = 0
    trend_status = "讀取中"
    trend_desc = "正在分析市場動向..."
    us_news_html = ""
    
    if twii_df is not None and not twii_df.empty:
        twii_close = twii_df['Close'].iloc[-1]
        twii_change = twii_df['Close'].iloc[-1] - twii_df['Close'].iloc[-2]
        ma5 = twii_df['5MA'].iloc[-1]
        ma20 = twii_df['20MA'].iloc[-1]
        
        # 抓取美股費城半導體指數作為國際動向參考
        try:
            sox_df = yf.Ticker("^SOX").history(period="5d")
            if not sox_df.empty:
                sox_change = (sox_df['Close'].iloc[-1] - sox_df['Close'].iloc[-2]) / sox_df['Close'].iloc[-2] * 100
                if sox_change < -1.5:
                    us_status = "⚠️ 美半導體重挫"
                    us_desc = "美股科技股賣壓沉重，外資恐提款台股，電子權值股易承壓，建議多看少做，避免追高。"
                elif sox_change > 1.5:
                    us_status = "🚀 美科技股強勢"
                    us_desc = "美股那斯達克與半導體走強，風險偏好升溫，有利台股多頭延續，可留意突破上攻標的。"
                elif sox_change < 0:
                    us_status = "📉 美股偏空震盪"
                    us_desc = "國際股市走弱，台股上檔有壓，請留意防禦型標的或傳產避險。"
                else:
                    us_status = "⚖️ 美股穩定整理"
                    us_desc = "國際市場無明顯方向，台股將回歸內資主力籌碼與中小型題材股表現為主。"
            else:
                us_status = "大盤局勢"
                us_desc = "技術面守穩，留意個股表現。"
        except:
            us_status = "大盤局勢"
            us_desc = "技術面守穩，留意個股表現。"

        # 台股均線判斷
        if twii_close > ma5 and twii_close > ma20:
            trend_status = "🔥 強勢偏多"
        elif twii_close < ma5 and twii_close < ma20:
            trend_status = "🧊 弱勢偏空"
        elif twii_close > ma20:
            trend_status = "⚠️ 震盪整理"
        else:
            trend_status = "📈 跌深反彈"
            
        # 抓取台積電作為台股新聞代表
        try:
            news_data = yf.Ticker("2330.TW").news[:3]
            if news_data:
                us_news_html += "<div style='margin-top: 15px; border-top: 1px solid #444; padding-top: 10px; text-align: left;'>"
                us_news_html += "<span style='font-size:0.95rem; font-weight:bold; color:#ffcc00;'>📰 財經焦點新聞：</span><br>"
                for n in news_data:
                    title = n.get('title', '新聞連結')
                    link = n.get('link', '#')
                    us_news_html += f"<a href='{link}' target='_blank' style='color:#00ffcc; font-size:0.85rem; text-decoration: none;'>➤ {title}</a><br>"
                us_news_html += "</div>"
        except:
            pass
            
    twii_color = '#ff3333' if twii_change >= 0 else '#00cc00'
    
    with st.container(border=True):
        col1, col2 = st.columns([1.1, 1.2])
        with col1:
            st.markdown(f"<div style='text-align: center; color: #aaa; font-size: 1.1rem; font-weight: bold;'>台灣加權指數</div>", unsafe_allow_html=True)
            st.markdown(f"<div style='text-align: center; font-size: 2.3rem; font-weight: 900; color: {twii_color}; margin: 5px 0;'>{twii_close:,.0f}</div>", unsafe_allow_html=True)
            st.markdown(f"<div style='text-align: center; font-size: 1.2rem; font-weight: bold; color: {twii_color};'>{'↑' if twii_change > 0 else '↓'} {abs(twii_change):.0f} 點</div>", unsafe_allow_html=True)
            st.markdown(f"<div style='text-align: center; font-size: 1.1rem; font-weight: 900; color: #fff; margin-top:5px;'>技術面：{trend_status}</div>", unsafe_allow_html=True)
        with col2:
            st.markdown(f"<div style='text-align: left; color: #ffcc00; font-size: 1.05rem; font-weight: bold;'>🌍 國際連動解析</div>", unsafe_allow_html=True)
            st.markdown(f"<div style='text-align: left; font-size: 1.1rem; font-weight: bold; color: #fff;'>{us_status}</div>", unsafe_allow_html=True)
            st.markdown(f"<div style='text-align: left; font-size: 0.85rem; color: #ccc; margin-top: 5px;'>{us_desc}</div>", unsafe_allow_html=True)
            st.markdown(us_news_html, unsafe_allow_html=True)

# ==========================================
# 2. 畫面路由
# ==========================================
if st.session_state.page == "home":
    st.markdown("<h1 style='text-align: center;'>🇹🇼 雷達總機</h1>", unsafe_allow_html=True)
    render_index_board()
    
    st.markdown("<h3 style='margin-top: 15px;'>🎯 掃描買點</h3>", unsafe_allow_html=True)
    btn_col1, btn_col2 = st.columns(2)
    if btn_col1.button("✅ 尋找買點", use_container_width=True):
        st.session_state.filter_buy_only = True
        st.rerun()
    if btn_col2.button("📋 熱門名單", use_container_width=True):
        st.session_state.filter_buy_only = False
        st.rerun()
        
    search_val = st.text_input("隱藏標籤", placeholder="搜尋股票", label_visibility="collapsed")
    if search_val:
        st.session_state.current_stock = search_val
        st.session_state.page = "analysis"
        st.rerun()
        
    scan_results = []
    with st.spinner('掃描中...'):
        for stock in st.session_state.custom_pool:
            data = analyze_today(get_stock_data(stock), stock)
            if data: scan_results.append(data)
            
    if scan_results:
        df_results = pd.DataFrame(scan_results)
        if st.session_state.filter_buy_only:
            df_display = df_results[df_results['訊號'] == True]
            if df_display.empty:
                st.info("💡 今日無符合標的。")
        else:
            df_display = df_results.sort_values(by="成交量", ascending=False).head(10)
        
        for _, row in df_display.iterrows():
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
                <div style="text-align: center; margin: 5px 0;">
                    <span style="font-size: 2.2rem; font-weight: 900; color: {p_color};">{row['收盤價']}</span>
                    <span style="font-size: 1.1rem; color: {p_color}; margin-left: 8px;">{sign}{row['漲跌']}</span>
                </div>
                ''', unsafe_allow_html=True)
                
                if st.button("📊 解析", key=f"btn_{row['ticker_raw']}", use_container_width=True):
                    st.session_state.current_stock = row['ticker_raw']
                    st.session_state.page = "analysis"
                    st.rerun()

elif st.session_state.page == "analysis":
    target = st.session_state.current_stock
    df_chart = get_stock_data(target)
    clean_name = STOCK_NAMES.get(target, "")
    
    if st.button("⬅ 返回首頁", use_container_width=True):
        st.session_state.page = "home"
        st.rerun()
        
    if df_chart is not None:
        data = analyze_today(df_chart, target)
        p_color = '#ff3333' if data['漲跌'] >= 0 else '#00cc00'
        sign = "+" if data['漲跌'] > 0 else ""
        
        # 即使沒有名字也照常顯示代號
        display_title = f"🎯 {target} {data.get('名稱', '')}" if data.get('名稱') else f"🎯 {target}"
        st.markdown(f"<h2 style='text-align: center;'>{display_title}</h2>", unsafe_allow_html=True)
        st.markdown(f"<h3 style='text-align: center; color: {p_color}; font-size: 2rem;'>{data['收盤價']} ({sign}{data['漲跌幅']}%)</h3>", unsafe_allow_html=True)
        
        if data['訊號']:
            buy_zone_low = data['20MA']
            buy_zone_high = round(data['20MA'] * 1.02, 2)
            st.success("✅ **極佳買點**")
            st.markdown(f"**建議區間：** `{buy_zone_low} ~ {buy_zone_high}`")
        else:
            if data['J值'] >= 80:
                st.error("⚠️ **高檔過熱**")
                st.markdown(f"**建議：** 拉回至 `{data['10MA']}` 再觀察。")
            elif data['收盤價'] < data['20MA']:
                st.warning("⛔ **趨勢偏空**")
                st.markdown(f"**建議：** 突破 `{data['20MA']}` 再進場。")
            else:
                st.info("⏳ **觀望中**")
                st.markdown(f"**建議：** 可於 `{data['10MA']}` 至 `{data['20MA']}` 佈局。")
        
        d_col1, d_col2, d_col3, d_col4 = st.columns(4)
        if d_col1.button("1個月"): st.session_state.view_days = 20
        if d_col2.button("3個月"): st.session_state.view_days = 60
        if d_col3.button("6個月"): st.session_state.view_days = 120
        if d_col4.button("1年"): st.session_state.view_days = 240
        
        fig = draw_professional_chart(df_chart, target, data['收盤價'], st.session_state.view_days)
        st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})
        
        row1_c1, row1_c2, row1_c3 = st.columns(3)
        with row1_c1.container(border=True):
            st.markdown(f"**均線**<br>5T: {data['5MA']}<br>10T: {data['10MA']}<br>20T: {data['20MA']}", unsafe_allow_html=True)
        with row1_c2.container(border=True):
            st.markdown(f"**MACD**<br>DIF: {data['MACD']}<br>OSC: {data['MACD柱']}", unsafe_allow_html=True)
        with row1_c3.container(border=True):
            st.markdown(f"**KDJ**<br>K: {data['K']}<br>D: {data['D']}<br>J: {data['J值']}", unsafe_allow_html=True)

        row2_c1, row2_c2 = st.columns(2)
        with row2_c1.container(border=True):
            st.markdown(f"**量能**<br>今日: {data['成交量']}張<br>5均: {data['5日均量']}張", unsafe_allow_html=True)
        with row2_c2.container(border=True):
            st.markdown("**籌碼(修改版)**")
            st.markdown(generate_mock_chips_html(df_chart), unsafe_allow_html=True)
    else:
        st.error("查無此股票資料，請確認輸入代號是否正確。")
with open("test.py", "w", encoding="utf-8") as f:
    f.write(code)
print("test.py finalized successfully.")}
