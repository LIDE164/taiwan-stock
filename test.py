code = """import yfinance as yf
import streamlit as st
import pandas as pd
import requests
from datetime import datetime, timezone, timedelta
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import json
import os
import urllib.parse
import xml.etree.ElementTree as ET

st.set_page_config(page_title="專業交易雷達", layout="centered", initial_sidebar_state="collapsed")

# 1. 黑白模式切換與動態 CSS 設定
st.sidebar.title("⚙️ 介面設定")
is_light_mode = st.sidebar.toggle("🌞 黑白底色切換", False)

bg_col = "#ffffff" if is_light_mode else "#1a1c24"
border_col = "#ddd" if is_light_mode else "#333"
text_col = "#333" if is_light_mode else "#ddd"
title_col = "#111" if is_light_mode else "#fff"
sub_text_col = "#666" if is_light_mode else "#888"
val_col = "#0066cc" if is_light_mode else "#00ffcc"
sticky_bg = "rgba(255,255,255,0.95)" if is_light_mode else "rgba(26,28,36,0.95)"
app_bg = "#f4f6f9" if is_light_mode else "#0e1117"

st.markdown(f'''
<style>
    .stApp {{ background-color: {app_bg}; }}
    #MainMenu {{visibility: hidden;}} footer {{visibility: hidden;}}
    [data-testid="collapsedControl"] {{
        border: 1px solid {border_col} !important; border-radius: 8px !important; background-color: {bg_col} !important;
        padding: 5px 12px !important; display: flex !important; align-items: center !important; width: auto !important; transition: 0.3s;
    }}
    [data-testid="collapsedControl"]::after {{ content: " ⭐ 我的自選股"; font-size: 1.1rem; font-weight: bold; color: #ffcc00; margin-left: 8px; }}
    .stButton button {{ font-weight: bold !important; border-radius: 8px !important; }}
    .sticky-header {{
        position: sticky; top: 0; z-index: 999; background-color: {sticky_bg};
        padding: 10px 0; border-bottom: 1px solid {border_col}; backdrop-filter: blur(5px); margin-top: -15px; margin-bottom: 15px;
    }}
    .trend-box {{ background-color: {bg_col}; border: 1px solid {border_col}; border-radius: 8px; padding: 8px 5px; text-align: center; box-shadow: 0 4px 6px rgba(0,0,0,0.05); }}
    .trend-title {{ font-size: 0.95rem; color: {sub_text_col}; font-weight: bold; margin-bottom: 4px; border-bottom: 1px solid {border_col}; padding-bottom: 2px; white-space: nowrap; }}
    .trend-status {{ font-size: 1.05rem; font-weight: 900; white-space: nowrap; color: {title_col}; }}
    div[data-testid="stVerticalBlockBorderWrapper"] > div {{ background-color: {bg_col} !important; border-color: {border_col} !important; padding: 4px !important; }}
    .tech-title {{ font-size: 0.95rem; font-weight: bold; color: {title_col}; margin-bottom: 4px; text-align: center; border-bottom: 1px solid {border_col}; padding-bottom: 2px; white-space: nowrap;}}
    .tech-text {{ font-size: 0.85rem; color: {text_col}; line-height: 1.3; display: flex; justify-content: space-between; padding: 0 2px;}}
    .tech-val {{ font-weight: bold; color: {val_col}; font-family: monospace; font-size: 0.95rem;}}
    h1, h2, h3, h4, p, span {{ color: {title_col} !important; }}
</style>
''', unsafe_allow_html=True)

STOCK_NAMES = {
    "2330": "台積電", "2317": "鴻海", "2454": "聯發科", "2308": "台達電", "2382": "廣達",
    "2376": "技嘉", "1802": "台玻", "2603": "長榮", "1785": "光洋科", "1519": "華城"
}

@st.cache_data(ttl=86400)
def get_all_tw_stock_names():
    names = STOCK_NAMES.copy()
    try:
        res_twse = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", timeout=5)
        for item in res_twse.json(): names[item['Code']] = item['Name']
        res_tpex = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes", timeout=5)
        for item in res_tpex.json(): names[item['SecuritiesCompanyCode']] = item['CompanyName']
    except: pass
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
if 'current_stock' not in st.session_state: st.session_state.current_stock = "2376"
if 'favorites' not in st.session_state: st.session_state.favorites = load_json(FAV_FILE, ["1802", "2330", "1785"])
if 'custom_pool' not in st.session_state: st.session_state.custom_pool = load_json(POOL_FILE, ["2330", "2317", "2454", "2382", "3231"])
if 'nav_pool' not in st.session_state: st.session_state.nav_pool = st.session_state.custom_pool
if 'scan_mode' not in st.session_state: st.session_state.scan_mode = "hot"
if 'view_days' not in st.session_state: st.session_state.view_days = 60

@st.cache_data(ttl=1800)
def fetch_twse_top_50():
    try:
        url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
        res = requests.get(url, timeout=10)
        df = pd.DataFrame(res.json())
        df['TradeVolume'] = pd.to_numeric(df['TradeVolume'], errors='coerce')
        df_stocks = df[df['Code'].str.match(r'^\d{4}$')]
        return df_stocks.sort_values(by='TradeVolume', ascending=False).head(50)['Code'].tolist()
    except:
        return ["2330", "2317", "2454", "2382", "3231"]

st.sidebar.divider()
st.sidebar.title("⭐ 我的自選股")
if st.session_state.favorites:
    for fav in st.session_state.favorites:
        fav_name = CURRENT_STOCK_NAMES.get(fav, "")
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
        base_ticker = str(ticker_number).strip().upper().replace(".TW", "").replace(".TWO", "")
        if base_ticker == "^TWII":
            df = yf.Ticker("^TWII").history(period="1y")
        else:
            df = yf.Ticker(f"{base_ticker}.TW").history(period="1y")
            if df.empty or len(df) < 20: df = yf.Ticker(f"{base_ticker}.TWO").history(period="1y")
            if df.empty or len(df) < 20: df = yf.Ticker(base_ticker).history(period="1y")
                
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

@st.cache_data(ttl=600)
def get_real_news(ticker, name):
    query = urllib.parse.quote(f"{ticker} {name} 股票")
    url = f"https://news.google.com/rss/search?q={query}+when:7d&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
    news_list = []
    try:
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            root = ET.fromstring(res.text)
            for item in root.findall('.//item')[:3]:
                news_list.append({"title": item.find('title').text, "link": item.find('link').text})
    except: pass
    if not news_list:
        news_list.append({"title": f"👉 點擊前往 Google 新聞查看 {ticker} {name} 最新消息", "link": f"https://www.google.com/search?q={query}&tbm=nws"})
    return news_list

def analyze_today(df, ticker_number):
    if df is None: return None
    today = df.iloc[-1]
    prev = df.iloc[-2]
    
    # 5日漲跌幅計算
    close_5d = df['Close'].iloc[-5] if len(df) >= 5 else df['Close'].iloc[0]
    pct_5d = (today['Close'] - close_5d) / close_5d * 100
    
    return {
        "代號": ticker_number, "名稱": CURRENT_STOCK_NAMES.get(ticker_number, ""), "ticker_raw": ticker_number,
        "昨日收盤價": round(prev['Close'], 2),
        "收盤價": round(today['Close'], 2), "漲跌": round(today['Close'] - prev['Close'], 2),
        "漲跌幅": round((today['Close'] - prev['Close']) / prev['Close'] * 100, 2), 
        "近5日漲幅(%)": f"{round(pct_5d, 2)}%",
        "成交量": int(today['Volume'] / 1000), "5日均量": int(df['Volume'].tail(5).mean() / 1000),
        "5MA": round(today['5MA'], 2), "10MA": round(today['10MA'], 2), "20MA": round(today['20MA'], 2),
        "MACD": round(today['MACD'], 2), "MACD柱": round(today['MACD_Hist'], 3),
        "K": round(today['K'], 2), "D": round(today['D'], 2), "J值": round(today['J'], 2),
        "訊號": (today['Close'] > today['20MA']) and (today['Close'] < today['5MA']) and (today['J'] < 20)
    }

# 修正：參數對齊 5 個 (df, ticker_name, latest_price, view_days, is_light_mode)
def draw_professional_chart(df, ticker_name, latest_price, view_days, is_light_mode):
    df_view = df.tail(view_days)
    colors = ['#ff3333' if row['Close'] >= row['Open'] else '#00cc00' for _, row in df_view.iterrows()]
    last_row = df_view.iloc[-1]
    
    fig = make_subplots(rows=4, cols=1, shared_xaxes=True, row_heights=[0.45, 0.15, 0.15, 0.25], vertical_spacing=0.06)
    
    line_k = "#0066cc" if is_light_mode else "white"
    line_d = "#ff9900" if is_light_mode else "yellow"
    line_j = "#9900cc" if is_light_mode else "magenta"
    grid_c = "rgba(0,0,0,0.1)" if is_light_mode else "rgba(255,255,255,0.1)"
    bg_c = "#ffffff" if is_light_mode else "#0e1117"
    text_c = "#333" if is_light_mode else "#ccc"
    
    fig.add_trace(go.Candlestick(x=df_view.index, open=df_view['Open'], high=df_view['High'], low=df_view['Low'], close=df_view['Close'], increasing_line_color='#ff3333', decreasing_line_color='#00cc00', name="K線"), row=1, col=1)
    fig.add_trace(go.Scatter(x=df_view.index, y=df_view['5MA'], line=dict(color='orange', width=2), name="5T"), row=1, col=1)
    fig.add_trace(go.Scatter(x=df_view.index, y=df_view['10MA'], line=dict(color='#ffcc00', width=2), name="10T"), row=1, col=1)
    fig.add_trace(go.Scatter(x=df_view.index, y=df_view['20MA'], line=dict(color='cyan', width=2), name="20T"), row=1, col=1)
    
    fig.add_hline(y=latest_price, line_dash="dash", line_color="#ffcc00", row=1, col=1, annotation_text=f"今日收盤: {latest_price:.2f}", annotation_position="top right", annotation_font=dict(size=15, color="#ffcc00", weight="bold"))
    
    fig.add_trace(go.Bar(x=df_view.index, y=df_view['Volume'], marker_color=colors, name="VOL"), row=2, col=1)
    
    macd_colors = ['#ff3333' if val > 0 else '#00cc00' for val in df_view['MACD_Hist']]
    fig.add_trace(go.Bar(x=df_view.index, y=df_view['MACD_Hist'], marker_color=macd_colors, name="OSC"), row=3, col=1)
    fig.add_trace(go.Scatter(x=df_view.index, y=df_view['MACD'], line=dict(color=line_k, width=1.5), name="DIF"), row=3, col=1)
    fig.add_trace(go.Scatter(x=df_view.index, y=df_view['Signal'], line=dict(color=line_d, width=1.5), name="MACD"), row=3, col=1)
    
    fig.add_trace(go.Scatter(x=df_view.index, y=df_view['K'], line=dict(color=line_k, width=1.5), name="K"), row=4, col=1)
    fig.add_trace(go.Scatter(x=df_view.index, y=df_view['D'], line=dict(color=line_d, width=1.5), name="D"), row=4, col=1)
    fig.add_trace(go.Scatter(x=df_view.index, y=df_view['J'], line=dict(color=line_j, width=1.5), name="J"), row=4, col=1)

    ann_bg = "rgba(255,255,255,0.8)" if is_light_mode else "rgba(26,28,36,0.6)"
    fig.add_annotation(x=0.01, y=0.98, xref="paper", yref="y domain", text=f"現價:{latest_price:.1f} | 5T:{last_row['5MA']:.1f} | 10T:{last_row['10MA']:.1f} | 20T:{last_row['20MA']:.1f}", showarrow=False, font=dict(color="#ff9900" if is_light_mode else "#ffcc00", size=12), xanchor="left", bgcolor=ann_bg)
    fig.add_annotation(x=0.01, y=0.95, xref="paper", yref="y2 domain", text=f"VOL: {last_row['Volume']:,.0f}", showarrow=False, font=dict(color=text_c, size=12), xanchor="left", bgcolor=ann_bg)
    fig.add_annotation(x=0.01, y=0.95, xref="paper", yref="y3 domain", text=f"MACD:{last_row['MACD']:.2f} | DIF:{last_row['Signal']:.2f} | OSC:{last_row['MACD_Hist']:.2f}", showarrow=False, font=dict(color=text_c, size=12), xanchor="left", bgcolor=ann_bg)
    fig.add_annotation(x=0.01, y=0.95, xref="paper", yref="y4 domain", text=f"K:{last_row['K']:.2f} | D:{last_row['D']:.2f} | J:{last_row['J']:.2f}", showarrow=False, font=dict(color=text_c, size=12), xanchor="left", bgcolor=ann_bg)

    fig.update_xaxes(fixedrange=True, showgrid=True, gridcolor=grid_c)
    fig.update_yaxes(fixedrange=True, showgrid=True, gridcolor=grid_c)
    fig.update_xaxes(title_text="", row=1, col=1); fig.update_xaxes(title_text="", row=2, col=1); fig.update_xaxes(title_text="", row=3, col=1); fig.update_xaxes(title_text="", row=4, col=1)
    
    fig.update_layout(
        xaxis_rangeslider_visible=False, template="plotly_white" if is_light_mode else "plotly_dark", height=850, 
        margin=dict(l=10, r=10, t=10, b=10), paper_bgcolor=bg_c, plot_bgcolor=bg_c, 
        hovermode='x unified', hoverlabel=dict(font_size=13), dragmode=False, 
        legend=dict(orientation="h", yanchor="top", y=-0.05, xanchor="center", x=0.5, font=dict(color=text_c))
    )
    return fig

tz_tpe = timezone(timedelta(hours=8))

def render_index_board():
    now_time_str = datetime.now(tz_tpe).strftime('%Y/%m/%d %H:%M:%S')
    twii_df = get_stock_data("^TWII")
    twii_close, twii_change = 0, 0
    trend_status, trend_desc = "讀取中", "正在分析市場動向..."
    us_news_html, us_status, us_desc = "", "", ""
    
    if twii_df is not None and not twii_df.empty:
        twii_close = twii_df['Close'].iloc[-1]
        twii_change = twii_df['Close'].iloc[-1] - twii_df['Close'].iloc[-2]
        ma5 = twii_df['5MA'].iloc[-1]
        ma20 = twii_df['20MA'].iloc[-1]
        
        try:
            sox_df = yf.Ticker("^SOX").history(period="5d")
            if not sox_df.empty:
                sox_change = (sox_df['Close'].iloc[-1] - sox_df['Close'].iloc[-2]) / sox_df['Close'].iloc[-2] * 100
                if sox_change < -1.5:
                    us_status, us_desc = "⚠️ 美半導體重挫", "外資恐提款台股，電子權值股易承壓，建議多看少做。"
                elif sox_change > 1.5:
                    us_status, us_desc = "🚀 美科技股強勢", "風險偏好升溫，有利台股多頭延續，留意突破上攻標的。"
                elif sox_change < 0:
                    us_status, us_desc = "📉 美股偏空震盪", "台股上檔有壓，請留意防禦型標的或傳產避險。"
                else:
                    us_status, us_desc = "⚖️ 美股穩定整理", "國際無明顯方向，台股回歸內資主力籌碼與題材股表現。"
        except: pass

        if twii_close > ma5 and twii_close > ma20: trend_status = "🔥 強勢偏多"
        elif twii_close < ma5 and twii_close < ma20: trend_status = "🧊 弱勢偏空"
        elif twii_close > ma20: trend_status = "⚠️ 震盪整理"
        else: trend_status = "📈 跌深反彈"
            
        news_items = get_real_news("台股", "大盤")
        if news_items:
            us_news_html += "<div style='margin-top: 15px; border-top: 1px solid #444; padding-top: 10px; text-align: left;'>"
            us_news_html += "<span style='font-size:0.95rem; font-weight:bold; color:#ffcc00;'>📰 財經焦點新聞：</span><br>"
            for n in news_items: us_news_html += f"<a href='{n['link']}' target='_blank' style='color:#00ffcc; font-size:0.85rem; text-decoration: none; display: block; margin-bottom: 4px;'>➤ {n['title']}</a>"
            us_news_html += "</div>"
            
    twii_color = '#ff3333' if twii_change >= 0 else '#00cc00'
    
    with st.container(border=True):
        col1, col2 = st.columns([1.1, 1.2])
        with col1:
            st.markdown(f"<div style='text-align: center; font-size: 1.1rem; font-weight: bold;'>台灣加權指數</div>", unsafe_allow_html=True)
            st.markdown(f"<div style='text-align: center; font-size: 2.3rem; font-weight: 900; color: {twii_color}; margin: 5px 0;'>{twii_close:,.0f}</div>", unsafe_allow_html=True)
            st.markdown(f"<div style='text-align: center; font-size: 1.2rem; font-weight: bold; color: {twii_color};'>{'↑' if twii_change > 0 else '↓'} {abs(twii_change):.0f} 點</div>", unsafe_allow_html=True)
            st.markdown(f"<div style='text-align: center; font-size: 1.1rem; font-weight: 900; margin-top:5px;'>技術面：{trend_status}</div>", unsafe_allow_html=True)
        with col2:
            st.markdown(f"<div style='text-align: left; color: #ffcc00; font-size: 1.05rem; font-weight: bold;'>🌍 國際連動解析</div>", unsafe_allow_html=True)
            st.markdown(f"<div style='text-align: left; font-size: 1.1rem; font-weight: bold;'>{us_status}</div>", unsafe_allow_html=True)
            st.markdown(f"<div style='text-align: left; font-size: 0.85rem; margin-top: 5px; line-height: 1.4;'>{us_desc}</div>", unsafe_allow_html=True)
            st.markdown(us_news_html, unsafe_allow_html=True)
            
    st.markdown(f"<div style='text-align: right; color: #666; font-size: 0.8rem; margin-top: -10px;'>🔄 系統最後更新時間: {now_time_str}</div>", unsafe_allow_html=True)

if st.session_state.page == "home":
    st.markdown("<h1 style='text-align: center;'>🇹🇼 雷達總機</h1>", unsafe_allow_html=True)
    render_index_board()
    
    st.markdown("<h3 style='margin-top: 15px;'>🎯 掃描買點</h3>", unsafe_allow_html=True)
    
    btn_col1, btn_col2, btn_col3 = st.columns(3)
    if btn_col1.button("✅ 尋找買點", use_container_width=True): st.session_state.scan_mode = "buy"; st.rerun()
    if btn_col2.button("📋 熱門名單", use_container_width=True): st.session_state.scan_mode = "hot"; st.rerun()
    if btn_col3.button("🔥 近日熱力", use_container_width=True): st.session_state.scan_mode = "recent"; st.rerun()
        
    search_val = st.text_input("隱藏標籤", placeholder="🔍 搜尋股票 (輸入代號並按 Enter)", label_visibility="collapsed")
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
        
        if st.session_state.scan_mode == "recent":
            st.markdown("##### 🔥 近五日熱門排行榜")
            df_display = df_results.sort_values(by="成交量", ascending=False).head(20)
            # 新增昨日收盤價欄位
            table_df = df_display[['代號', '名稱', '昨日收盤價', '收盤價', '漲跌幅', '近5日漲幅(%)', '成交量', '5日均量']]
            table_df.set_index('代號', inplace=True)
            st.dataframe(table_df, use_container_width=True)
            
        else:
            if st.session_state.scan_mode == "buy":
                df_display = df_results[df_results['訊號'] == True]
                if df_display.empty: st.info("💡 今日無符合標的。")
            else:
                df_display = df_results.sort_values(by="成交量", ascending=False).head(10)
            
            for _, row in df_display.iterrows():
                with st.container(border=True):
                    is_fav = row['ticker_raw'] in st.session_state.favorites
                    star_icon = "⭐ 移除自選" if is_fav else "☆ 加入自選"
                    sign = "+" if row['漲跌'] > 0 else ""
                    p_color = "#ff3333" if row['漲跌'] >= 0 else "#00cc00"
                    
                    c_title, c_star = st.columns([8, 2])
                    with c_title: st.markdown(f"### `{row['代號']}` **{row['名稱']}**")
                    with c_star:
                        if st.button(star_icon, key=f"star_{row['ticker_raw']}", use_container_width=True):
                            if is_fav: st.session_state.favorites.remove(row['ticker_raw'])
                            else: st.session_state.favorites.append(row['ticker_raw'])
                            save_json(FAV_FILE, st.session_state.favorites)
                            st.rerun()
                    
                    bg_c = "#ffffff" if is_light_mode else "#1a1c24"
                    border_c = "#ddd" if is_light_mode else "#333"
                    st.markdown(f'''
                    <div style="background-color: {bg_c}; padding: 12px; border-radius: 8px; border: 1px solid {border_c}; text-align: center; margin: 5px 0 10px 0;">
                        <span style="font-size: 2.6rem; font-weight: 900; color: {p_color};">{row['收盤價']}</span>
                        <span style="font-size: 1.3rem; font-weight: bold; color: {p_color}; margin-left: 12px;">{sign}{row['漲跌']} ({sign}{row['漲跌幅']}%)</span>
                    </div>
                    ''', unsafe_allow_html=True)
                    
                    if st.button("📊 解析", key=f"btn_{row['ticker_raw']}", use_container_width=True):
                        st.session_state.current_stock = row['ticker_raw']
                        st.session_state.page = "analysis"
                        st.rerun()

elif st.session_state.page == "analysis":
    target = st.session_state.current_stock
    df_chart = get_stock_data(target)
    clean_name = CURRENT_STOCK_NAMES.get(target, "")
    
    c_nav1, c_nav2, c_nav3 = st.columns([1, 1, 1])
    nav_pool = st.session_state.get('nav_pool', st.session_state.custom_pool)
    prev_stock, next_stock = None, None
    if target in nav_pool and len(nav_pool) > 1:
        idx = nav_pool.index(target)
        prev_stock = nav_pool[idx - 1] if idx > 0 else None
        next_stock = nav_pool[idx + 1] if idx < len(nav_pool) - 1 else None

    with c_nav1:
        if prev_stock and st.button(f"⬅ 上一檔", use_container_width=True):
            st.session_state.current_stock = prev_stock; st.rerun()
    with c_nav2:
        if st.button("🏠 回首頁", use_container_width=True):
            st.session_state.page = "home"; st.rerun()
    with c_nav3:
        if next_stock and st.button(f"下一檔 ➡", use_container_width=True):
            st.session_state.current_stock = next_stock; st.rerun()
        
    if df_chart is not None:
        data = analyze_today(df_chart, target)
        p_color = '#ff3333' if data['漲跌'] >= 0 else '#00cc00'
        sign = "+" if data['漲跌'] > 0 else ""
        
        display_title = f"🎯 {target} {data.get('名稱', '')}" if data.get('名稱') else f"🎯 {target}"
        st.markdown(f"<h2 style='text-align: center;'>{display_title}</h2>", unsafe_allow_html=True)
        st.markdown(f"<h3 style='text-align: center; color: {p_color}; font-size: 2rem;'>{data['收盤價']} ({sign}{data['漲跌幅']}%)</h3>", unsafe_allow_html=True)
        
        now_time_str = datetime.now(tz_tpe).strftime('%Y/%m/%d %H:%M:%S')
        st.markdown(f"<div style='text-align: center; color: #666; font-size: 0.8rem; margin-top: -10px; margin-bottom: 15px;'>🔄 資料更新時間: {now_time_str}</div>", unsafe_allow_html=True)
        
        if data['訊號']:
            buy_zone_low = data['20MA']
            buy_zone_high = round(data['20MA'] * 1.02, 2)
            st.success("✅ **極佳買點：** 股價穩在月線之上，短線急跌且 KDJ 極度超賣。")
            st.markdown(f"> **🎯 建議操作：** 接近月線支撐約 `{buy_zone_low} ~ {buy_zone_high}` 附近佈局。")
        else:
            if data['J值'] >= 80:
                st.error("⚠️ **高檔過熱：** J值過高，有回檔風險。")
                st.markdown(f"> **🎯 建議操作：** 目前溢價風險高，建議等拉回至 10日線 `{data['10MA']}` 附近再觀察。")
            elif data['收盤價'] < data['20MA']:
                st.warning("⛔ **趨勢偏空：** 股價跌破月線支撐，中線趨勢轉弱。")
                st.markdown(f"> **🎯 建議操作：** 空頭走勢中，建議空手觀望，或等突破月線 `{data['20MA']}` 再進場。")
            else:
                st.info("⏳ **觀望中：** 雖然在多頭趨勢，但目前未達極度超賣區。")
                st.markdown(f"> **🎯 建議操作：** 可於 `{data['10MA']}` 至 `{data['20MA']}` 區間分批逢低佈局。")
        
        d_col1, d_col2, d_col3, d_col4 = st.columns(4)
        if d_col1.button("1個月"): st.session_state.view_days = 20
        if d_col2.button("3個月"): st.session_state.view_days = 60
        if d_col3.button("6個月"): st.session_state.view_days = 120
        if d_col4.button("1年"): st.session_state.view_days = 240
        
        # 修正參數呼叫
        fig = draw_professional_chart(df_chart, target, data['收盤價'], st.session_state.view_days, is_light_mode)
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
            st.markdown("**🏦 真實籌碼與主力動向查詢**")
            st.markdown(f"由於國際 API 無法取得台灣券商每日分點進出資料，為求精準判斷，強烈建議您直接點擊下方專業平台，查看最新主力籌碼動向：", unsafe_allow_html=True)
            st.markdown(f"[➤ 點擊前往 Yahoo 股市看【外資投信買賣超】](https://tw.stock.yahoo.com/quote/{target}/institutional-trading)", unsafe_allow_html=True)
            st.markdown(f"[➤ 點擊前往 Goodinfo 看【主力進出明細】](https://goodinfo.tw/tw/ShowBuySaleChart.asp?STOCK_ID={target})", unsafe_allow_html=True)

        st.divider()
        st.subheader("📰 相關新聞")
        news_items = get_real_news(target, clean_name)
        if news_items:
            for n in news_items: st.markdown(f"- [{n['title']}]({n['link']})")
        else: st.info("目前暫無相關新聞。")
            
    else:
        st.error("查無此股票資料，請確認輸入代號是否正確。")
"""
with open("test.py", "w", encoding="utf-8") as f:
    f.write(code)
print("test.py updated completely.")
