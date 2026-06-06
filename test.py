code = """import yfinance as yf
import streamlit as st
import pandas as pd
import requests
from datetime import datetime
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import json
import os

st.set_page_config(page_title="專業交易雷達", layout="centered", initial_sidebar_state="collapsed")

# 隱藏選單
st.markdown('''
<style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
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
if 'view_days' not in st.session_state: st.session_state.view_days = 60
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
st.sidebar.title("⭐ 我的自選股")
if st.session_state.favorites:
    for fav in st.session_state.favorites:
        if st.sidebar.button(f"📊 {fav}", key=f"side_fav_{fav}", use_container_width=True):
            st.session_state.current_stock = fav
            st.session_state.page = "analysis"
            st.rerun()

st.sidebar.divider()
st.sidebar.title("⚙️ 設定")
if st.sidebar.button("🔄 更新熱門股", use_container_width=True):
    st.session_state.custom_pool = fetch_twse_top_50()
    save_json(POOL_FILE, st.session_state.custom_pool)
    st.sidebar.success("✅ 完成！")
    st.rerun()

# ==========================================
# 1. 核心大腦
# ==========================================
@st.cache_data(ttl=300) 
def get_stock_data(ticker_number):
    if ticker_number == "^TWII": return yf.Ticker("^TWII").history(period="1y")
    base_ticker = ticker_number.upper().replace(".TW", "").replace(".TWO", "")
    try:
        df = yf.Ticker(f"{base_ticker}.TW").history(period="1y")
        if df.empty or len(df) < 20: df = yf.Ticker(f"{base_ticker}.TWO").history(period="1y")
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
    
    is_golden_pit = (today['Close'] > today['20MA']) and (today['Close'] < today['5MA']) and (today['J'] < 20)
    change_percent = (today['Close'] - prev['Close']) / prev['Close'] * 100
    
    return {
        "代號": ticker_number, "ticker_raw": ticker_number,
        "收盤價": round(today['Close'], 2), "漲跌": round(today['Close'] - prev['Close'], 2),
        "漲跌幅": round(change_percent, 2), 
        "成交量": int(today['Volume'] / 1000),
        "5日均量": int(df['Volume'].tail(5).mean() / 1000),
        "5MA": round(today['5MA'], 2), "10MA": round(today['10MA'], 2), "20MA": round(today['20MA'], 2),
        "MACD": round(today['MACD'], 2), "MACD柱": round(today['MACD_Hist'], 3),
        "K": round(today['K'], 2), "D": round(today['D'], 2), "J值": round(today['J'], 2),
        "訊號": is_golden_pit
    }

def generate_mock_chips_html(df):
    recent_5 = df.tail(5).iloc[::-1]
    html = "<table style='width:100%; text-align:center;'><tr><th>日期</th><th>外資</th><th>投信</th></tr>"
    for date, row in recent_5.iterrows():
        d_str = date.strftime("%m/%d")
        change = row['Close'] - row['Open']
        base_vol = row['Volume'] / 1000
        fi_buy = int(change * 200 + (base_vol * 0.08)) 
        it_buy = int(change * 80 + (base_vol * 0.03))
        
        fi_color = "#ff3333" if fi_buy > 0 else "#00cc00"
        it_color = "#ff3333" if it_buy > 0 else "#00cc00"
        
        fi_str = f"+{fi_buy:,}" if fi_buy > 0 else f"{fi_buy:,}"
        it_str = f"+{it_buy:,}" if it_buy > 0 else f"{it_buy:,}"
        
        html += f"<tr><td>{d_str}</td><td style='color:{fi_color}'>{fi_str}</td><td style='color:{it_color}'>{it_str}</td></tr>"
    html += "</table>"
    return html

def draw_professional_chart(df, latest_price, view_days):
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
    trend_desc = ""
    
    if twii_df is not None and not twii_df.empty:
        twii_close = twii_df['Close'].iloc[-1]
        twii_change = twii_df['Close'].iloc[-1] - twii_df['Close'].iloc[-2]
        ma5 = twii_df['5MA'].iloc[-1]
        ma20 = twii_df['20MA'].iloc[-1]
        
        if twii_close > ma5 and twii_close > ma20:
            trend_status = "🔥 強勢偏多"
            trend_desc = "大盤站上5日與月線"
        elif twii_close < ma5 and twii_close < ma20:
            trend_status = "🧊 弱勢偏空"
            trend_desc = "跌破5日與月線支撐"
        elif twii_close > ma20:
            trend_status = "⚠️ 震盪整理"
            trend_desc = "守月線但破5日線"
        else:
            trend_status = "📈 跌深反彈"
            trend_desc = "站回5日但低於月線"
            
    twii_color = '#ff3333' if twii_change >= 0 else '#00cc00'
    
    with st.container(border=True):
        col1, col2 = st.columns([1.2, 1])
        with col1:
            st.markdown(f"<div style='text-align: center; color: #aaa; font-size: 1.1rem; font-weight: bold;'>加權指數 ({now.strftime('%m/%d %H:%M')})</div>", unsafe_allow_html=True)
            st.markdown(f"<div style='text-align: center; font-size: 2.2rem; font-weight: 900; color: {twii_color}; margin: 5px 0;'>{twii_close:,.2f}</div>", unsafe_allow_html=True)
            st.markdown(f"<div style='text-align: center; font-size: 1.2rem; font-weight: bold; color: {twii_color};'>漲跌: {'+' if twii_change > 0 else ''}{twii_change:,.2f}</div>", unsafe_allow_html=True)
        with col2:
            st.markdown(f"<div style='text-align: center; color: #ffcc00; font-size: 1.1rem; font-weight: bold; margin-top: 10px;'>大盤局勢</div>", unsafe_allow_html=True)
            st.markdown(f"<div style='text-align: center; font-size: 1.2rem; font-weight: 900; color: #fff;'>{trend_status}</div>", unsafe_allow_html=True)
            st.markdown(f"<div style='text-align: center; font-size: 0.85rem; color: #aaa; margin-top: 5px;'>{trend_desc}</div>", unsafe_allow_html=True)

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
        
    search_val = st.text_input("輸入代號看解析 (如: 2330)", label_visibility="collapsed")
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
                    st.markdown(f"### `{row['代號']}`")
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
    
    if st.button("⬅ 返回首頁", use_container_width=True):
        st.session_state.page = "home"
        st.rerun()
        
    if df_chart is not None:
        data = analyze_today(df_chart, target)
        p_color = '#ff3333' if data['漲跌'] >= 0 else '#00cc00'
        sign = "+" if data['漲跌'] > 0 else ""
        st.markdown(f"<h2 style='text-align: center;'>🎯 {target}</h2>", unsafe_allow_html=True)
        st.markdown(f"<h3 style='text-align: center; color: {p_color}; font-size: 2rem;'>{data['收盤價']} ({sign}{data['漲跌幅']}%)</h3>", unsafe_allow_html=True)
        
        if data['訊號']:
            buy_zone_low = data['20MA']
            buy_zone_high = round(data['20MA'] * 1.02, 2)
            st.success(f"✅ **極佳買點** \n\n建議區間： `{buy_zone_low} ~ {buy_zone_high}`")
        else:
            if data['J值'] >= 80:
                st.error(f"⚠️ **高檔過熱** \n\n建議拉回至 `{data['10MA']}`。")
            elif data['收盤價'] < data['20MA']:
                st.warning(f"⛔ **趨勢偏空** \n\n建議突破 `{data['20MA']}` 再進場。")
            else:
                st.info(f"⏳ **觀望中** \n\n可於 `{data['10MA']}` 至 `{data['20MA']}` 佈局。")
        
        d_col1, d_col2, d_col3, d_col4 = st.columns(4)
        if d_col1.button("1個月"): st.session_state.view_days = 20
        if d_col2.button("3個月"): st.session_state.view_days = 60
        if d_col3.button("6個月"): st.session_state.view_days = 120
        if d_col4.button("1年"): st.session_state.view_days = 240
        
        fig = draw_professional_chart(df_chart, target, data['收盤價'], st.session_state.view_days)
        st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})
        
        row1_c1, row1_c2, row1_c3 = st.columns(3)
        with row1_c1.container(border=True):
            st.write(f"**均線**\\n5T: {data['5MA']}\\n10T: {data['10MA']}\\n20T: {data['20MA']}")
        with row1_c2.container(border=True):
            st.write(f"**MACD**\\nDIF: {data['MACD']}\\nOSC: {data['MACD柱']}")
        with row1_c3.container(border=True):
            st.write(f"**KDJ**\\nK: {data['K']}\\nD: {data['D']}\\nJ: {data['J值']}")

        row2_c1, row2_c2 = st.columns(2)
        with row2_c1.container(border=True):
            st.write(f"**量能**\\n今日: {data['成交量']}張\\n5均: {data['5日均量']}張")
        with row2_c2.container(border=True):
            st.markdown("**籌碼(模擬)**")
            st.markdown(generate_mock_chips_html(df_chart), unsafe_allow_html=True)
"""
with open("test.py", "w", encoding="utf-8") as f:
    f.write(code)
print("test.py stripped and saved successfully.")
