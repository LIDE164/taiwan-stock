import yfinance as yf
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

INDUSTRY_MAP = {
    "2330": "半導體業", "2317": "其他電子業", "2454": "半導體業", "2308": "電子零組件業", "2382": "電腦及週邊設備業",
    "2376": "電腦及週邊設備業", "1802": "玻璃陶瓷", "2603": "航運業", "1785": "光電業", "1519": "電機機械",
    "3293": "文化創意業", "3037": "電子零組件業", "8046": "電子零組件業"
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

@st.cache_data(ttl=86400)
def get_fundamental_and_industry_data(ticker_number):
    try:
        base_ticker = str(ticker_number).strip().upper().replace(".TW", "").replace(".TWO", "")
        info = yf.Ticker(f"{base_ticker}.TW").info
        if not info or 'trailingEps' not in info:
            info = yf.Ticker(f"{base_ticker}.TWO").info
            
        eps = info.get("trailingEps", "無")
        pe = info.get("trailingPE", "無")
        sector = info.get("sector", "")
        industry = info.get("industry", "")
        full_industry = f"{sector} - {industry}" if sector and industry else INDUSTRY_MAP.get(base_ticker, "未提供產業資訊")
        
        return {"EPS": eps, "PE": pe, "Industry": full_industry}
    except:
        return {"EPS": "無", "PE": "無", "Industry": INDUSTRY_MAP.get(str(ticker_number).strip().upper(), "未提供產業資訊")}

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
        
        df['STD20'] = df['Close'].rolling(window=20).std()
        df['BB_UP'] = df['20MA'] + (2 * df['STD20'])
        df['BB_DN'] = df['20MA'] - (2 * df['STD20'])
        
        df['BIAS_20'] = (df['Close'] - df['20MA']) / df['20MA'] * 100
        
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
    
    close_5d = df['Close'].iloc[-5] if len(df) >= 5 else df['Close'].iloc[0]
    pct_5d = (today['Close'] - close_5d) / close_5d * 100
    
    fund_data = get_fundamental_and_industry_data(ticker_number)
    
    return {
        "代號": ticker_number, "名稱": CURRENT_STOCK_NAMES.get(ticker_number, ""), "ticker_raw": ticker_number,
        "產業": fund_data['Industry'],
        "昨日收盤價": round(prev['Close'], 2),
        "收盤價": round(today['Close'], 2), "漲跌": round(today['Close'] - prev['Close'], 2),
        "漲跌幅": round((today['Close'] - prev['Close']) / prev['Close'] * 100, 2), 
        "近5日漲幅(%)": f"{round(pct_5d, 2)}%",
        "成交量": int(today['Volume'] / 1000), "5日均量": int(df['Volume'].tail(5).mean() / 1000),
        "5MA": round(today['5MA'], 2), "10MA": round(today['10MA'], 2), "20MA": round(today['20MA'], 2),
        "BB_UP": round(today['BB_UP'], 2), "BB_DN": round(today['BB_DN'], 2), "BIAS": round(today['BIAS_20'], 2),
        "MACD": round(today['MACD'], 2), "MACD柱": round(today['MACD_Hist'], 3),
        "K": round(today['K'], 2), "D": round(today['D'], 2), "J值": round(today['J'], 2),
        "訊號": (today['Close'] > today['20MA']) and (today['Close'] < today['5MA']) and (today['J'] < 20)
    }

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
            
            st.markdown("---")
            col_a, col_b, col_c, col_d, col_e, col_f = st.columns([1.5, 2.5, 1.5, 1.5, 2, 1.5])
            col_a.markdown("**代號**"); col_b.markdown("**名稱**"); col_c.markdown("**昨收**"); col_d.markdown("**今收**"); col_e.markdown("**近5日漲幅**"); col_f.markdown("**動作**")
            st.markdown("---")
            
            for _, row in df_display.iterrows():
                ca, cb, cc, cd, ce, cf = st.columns([1.5, 2.5, 1.5, 1.5, 2, 1.5])
                ca.markdown(f"`{row['代號']}`")
                
                # 產業名稱如果是未提供，則縮小顯示
                ind_display = row['產業'] if row['產業'] else "未知"
                cb.markdown(f"**{row['名稱']}** <br><span style='font-size:0.75rem; color:#888;'>{ind_display}</span>", unsafe_allow_html=True)
                
                cc.markdown(f"{row['昨日收盤價']}")
                cd.markdown(f"{row['收盤價']}")
                
                val_5d = float(row['近5日漲幅(%)'].strip('%'))
                color_5d = "#ff3333" if val_5d > 0 else "#00cc00" if val_5d < 0 else text_col
                ce.markdown(f"<span style='color:{color_5d}; font-weight:bold;'>{row['近5日漲幅(%)']}</span>", unsafe_allow_html=True)
                
                with cf:
                    if st.button("📊 解析", key=f"btn_recent_{row['ticker_raw']}", use_container_width=True):
                        st.session_state.current_stock = row['ticker_raw']
                        st.session_state.page = "analysis"
                        st.rerun()
                st.markdown("<hr style='margin: 0; padding: 0;'>", unsafe_allow_html=True)
            
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
    fund_data = get_fundamental_and_industry_data(target)
    
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
        
        # --- 🏆 AI 終極操盤決策引擎計算 ---
        score = 0
        reasons = []
        
        signal_kdj = data['訊號']
        j_val = data['J值']
        close_val = data['收盤價']
        ma20_val = data['20MA']
        bb_up = data['BB_UP']
        bb_dn = data['BB_DN']
        bias_val = data['BIAS']
        vol_ratio = data['成交量'] / (data['5日均量'] + 0.001)
        eps_val = fund_data['EPS']
        is_profitable = isinstance(eps_val, (int, float)) and eps_val > 0

        if signal_kdj:
            score += 3
            reasons.append("✅ 穩在月線上且KDJ超賣 (極佳買點核心條件)")
        if close_val <= bb_dn * 1.02:
            score += 2
            reasons.append("✅ 觸及布林下軌 (具備超跌反彈動能)")
        if bias_val < -5:
            score += 1
            reasons.append("✅ 負乖離擴大 (短線殺盤力道竭盡)")
        if is_profitable:
            score += 2
            reasons.append("✅ 基本面獲利 (具備長線保護短線優勢)")
        if vol_ratio > 1.5 and data['漲跌'] > 0:
            score += 2
            reasons.append("✅ 量價配合 (突破5日均量，主力疑似進場)")

        if j_val >= 80:
            score -= 3
            reasons.append("⚠️ KDJ高檔過熱 (隨時面臨獲利了結賣壓)")
        if close_val >= bb_up * 0.98:
            score -= 2
            reasons.append("⚠️ 觸及布林上軌 (上檔壓力沉重，追高風險大)")
        if bias_val > 7:
            score -= 2
            reasons.append("⚠️ 正乖離過大 (技術面隨時有修正需求)")
        if close_val < ma20_val:
            score -= 2
            reasons.append("⚠️ 跌破月線 (中線趨勢已轉弱)")
        if not is_profitable and eps_val != "無":
            score -= 1
            reasons.append("⚠️ 基本面虧損 (缺乏底部實質支撐)")

        if score >= 5:
            verdict_title = "🟢 【S級買點】強烈建議佈局"
            verdict_action = f"勝率極高！建議於 {bb_dn:.2f} ~ {ma20_val:.2f} 之間分批建倉。"
            v_color = "#00cc00"
        elif score >= 2:
            verdict_title = "🟡 【A級機會】偏多試單"
            verdict_action = f"具備反彈契機，可於 {close_val:.2f} 附近小注試單，若跌破 {bb_dn:.2f} 需嚴格停損。"
            v_color = "#ffcc00"
        elif score >= -1:
            verdict_title = "⚪ 【中性觀望】多空不明"
            verdict_action = f"目前多空拉鋸，無必勝把握，建議等待突破 {ma20_val:.2f} 或回測 {bb_dn:.2f} 再行動作。"
            v_color = text_col
        elif score >= -4:
            verdict_title = "🟠 【風險警示】逢高減碼"
            verdict_action = f"追高風險大，若持有建議於 {close_val:.2f} ~ {bb_up:.2f} 之間分批獲利了結。"
            v_color = "#ff9900"
        else:
            verdict_title = "🔴 【極度危險】嚴禁做多"
            verdict_action = "技術面與籌碼面皆弱，或者極度超買，強烈建議空手觀望，切勿接刀。"
            v_color = "#ff3333"

        # --- 渲染介面 ---
        p_color = '#ff3333' if data['漲跌'] >= 0 else '#00cc00'
        sign = "+" if data['漲跌'] > 0 else ""
        
        display_title = f"🎯 {target} {data.get('名稱', '')}" if data.get('名稱') else f"🎯 {target}"
        st.markdown(f"<h2 style='text-align: center; margin-bottom: 5px;'>{display_title}</h2>", unsafe_allow_html=True)
        st.markdown(f"<div style='text-align: center; color: #888; font-size: 1.1rem; margin-top: 0px;'>【{fund_data['Industry']}】</div>", unsafe_allow_html=True)
        st.markdown(f"<h3 style='text-align: center; color: {p_color}; font-size: 2.2rem;'>{data['收盤價']} ({sign}{data['漲跌幅']}%)</h3>", unsafe_allow_html=True)
        
        now_time_str = datetime.now(tz_tpe).strftime('%Y/%m/%d %H:%M:%S')
        st.markdown(f"<div style='text-align: center; color: #666; font-size: 0.8rem; margin-top: -10px; margin-bottom: 15px;'>🔄 資料更新時間: {now_time_str}</div>", unsafe_allow_html=True)
        
        # 顯示終極決策面板
        st.markdown(f'''
        <div style="border: 2px solid {v_color}; border-radius: 10px; padding: 15px; margin-bottom: 20px; background-color: {bg_col};">
            <h3 style="text-align: center; color: {v_color}; margin-top: 0;">{verdict_title}</h3>
            <p style="text-align: center; font-size: 1.1rem; font-weight: bold; color: {text_col};">{verdict_action}</p>
            <hr style="border-color: {border_col};">
            <p style="font-size: 0.9rem; color: {sub_text_col}; margin-bottom: 5px;"><strong>🧠 決策引擎分析依據：</strong></p>
            <ul style="font-size: 0.9rem; color: {text_col}; line-height: 1.6;">
                {"".join([f"<li>{r}</li>" for r in reasons])}
            </ul>
        </div>
        ''', unsafe_allow_html=True)
        
        d_col1, d_col2, d_col3, d_col4 = st.columns(4)
        if d_col1.button("1個月"): st.session_state.view_days = 20
        if d_col2.button("3個月"): st.session_state.view_days = 60
        if d_col3.button("6個月"): st.session_state.view_days = 120
        if d_col4.button("1年"): st.session_state.view_days = 240
        
        fig = draw_professional_chart(df_chart, target, data['收盤價'], st.session_state.view_days, is_light_mode)
        st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})
        
        st.markdown("### 📈 基礎技術指標")
        row1_c1, row1_c2, row1_c3 = st.columns(3)
        with row1_c1.container(border=True):
            st.markdown("**均線**")
            st.markdown(f"5T: `{data['5MA']}`")
            st.markdown(f"10T: `{data['10MA']}`")
            st.markdown(f"20T: `{data['20MA']}`")
        with row1_c2.container(border=True):
            st.markdown("**MACD**")
            st.markdown(f"DIF: `{data['MACD']}`")
            st.markdown(f"OSC: `{data['MACD柱']}`")
        with row1_c3.container(border=True):
            st.markdown("**KDJ**")
            st.markdown(f"K: `{data['K']}`")
            st.markdown(f"D: `{data['D']}`")
            st.markdown(f"J: `{data['J值']}`")

        st.markdown("### 🕵️‍♂️ 進階數據面板")
        adv1, adv2 = st.columns(2)
        with adv1.container(border=True):
            st.markdown("##### 📊 布林通道 & 乖離率")
            st.markdown(f"**布林上軌 (壓力):** `{data['BB_UP']}`")
            st.markdown(f"**布林下軌 (支撐):** `{data['BB_DN']}`")
            st.markdown(f"**月線乖離率:** `{data['BIAS']}%`")

        with adv2.container(border=True):
            st.markdown("##### 📑 基本面健檢")
            st.markdown(f"**近四季 EPS:** `{eps_val}`")
            st.markdown(f"**目前本益比 (P/E):** `{fund_data['PE']}`")
            st.markdown(f"**今日成交量:** `{data['成交量']}張`")

        st.divider()
        st.markdown("**🏦 真實籌碼與主力動向查詢**")
        st.markdown(f"[➤ 點擊前往 Yahoo 股市看【外資投信買賣超】](https://tw.stock.yahoo.com/quote/{target}/institutional-trading)")
        st.markdown(f"[➤ 點擊前往 Goodinfo 看【主力進出明細】](https://goodinfo.tw/tw/ShowBuySaleChart.asp?STOCK_ID={target})")
        
        st.subheader("📰 相關新聞")
        news_items = get_real_news(target, clean_name)
        if news_items:
            for n in news_items: st.markdown(f"- [{n['title']}]({n['link']})")
        else: st.info("目前暫無相關新聞。")
            
    else:
        st.error("查無此股票資料，請確認輸入代號是否正確。")
