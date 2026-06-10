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
import streamlit.components.v1 as components

st.set_page_config(page_title="專業交易雷達", layout="centered", initial_sidebar_state="collapsed")

# 1. 強制每次更新滑動至頂端
components.html(
    """
    <script>
        var body = window.parent.document.querySelector('.main');
        if (body) { body.scrollTo({top: 0, behavior: 'smooth'}); }
    </script>
    """,
    height=0, width=0
)

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
    [data-testid="collapsedControl"] {{ border: 1px solid {border_col} !important; border-radius: 8px !important; background-color: {bg_col} !important; padding: 5px 12px !important; display: flex !important; align-items: center !important; width: auto !important; transition: 0.3s; }}
    [data-testid="collapsedControl"]::after {{ content: " ⭐ 我的自選股"; font-size: 1.1rem; font-weight: bold; color: #ffcc00; margin-left: 8px; }}
    .stButton button {{ font-weight: bold !important; border-radius: 8px !important; }}
    button[kind="primary"] {{ font-size: 1.5rem !important; padding: 15px !important; border-radius: 12px !important; background-color: #ffcc00 !important; color: #111 !important; border: none !important; }}
    .sticky-header {{ position: sticky; top: 0; z-index: 999; background-color: {sticky_bg}; padding: 10px 0; border-bottom: 1px solid {border_col}; backdrop-filter: blur(5px); margin-top: -15px; margin-bottom: 15px; }}
    div[data-testid="stVerticalBlockBorderWrapper"] > div {{ background-color: {bg_col} !important; border-color: {border_col} !important; padding: 4px !important; }}
    h1, h2, h3, h4, p, span {{ color: {title_col} !important; }}
</style>
''', unsafe_allow_html=True)

STOCK_NAMES = { "2330": "台積電", "2317": "鴻海", "2454": "聯發科", "2308": "台達電", "2382": "廣達", "2376": "技嘉", "1802": "台玻", "2603": "長榮", "1785": "光洋科", "1519": "華城" }

ENG_TO_TW_INDUSTRY = {
    "Semiconductors": "半導體業", "Consumer Electronics": "消費性電子", "Electronic Components": "電子零組件",
    "Computer Hardware": "電腦及週邊設備", "Building Materials": "玻璃陶瓷", "Marine Shipping": "航運業",
    "Electrical Equipment & Parts": "電機機械", "Software - Entertainment": "文化創意業", "Technology": "電子科技",
    "Industrials": "工業", "Basic Materials": "原物料", "Financial Services": "金融業",
    "Consumer Cyclical": "非必需消費品", "Healthcare": "生技醫療", "Real Estate": "建材營造",
    "Utilities": "公用事業", "Energy": "能源", "Communication Services": "通信網路"
}

@st.cache_data(ttl=86400)
def get_all_tw_stock_names():
    names = STOCK_NAMES.copy()
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", timeout=5)
        for i in res.json(): names[i['Code']] = i['Name']
        res2 = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes", timeout=5)
        for i in res2.json(): names[i['SecuritiesCompanyCode']] = i['CompanyName']
    except: pass
    return names

CURRENT_STOCK_NAMES = get_all_tw_stock_names()
FAV_FILE = "favorites.json"
POOL_FILE = "pool.json"

def load_json(fp, default):
    if os.path.exists(fp):
        try:
            with open(fp, "r", encoding="utf-8") as f: return json.load(f)
        except: pass
    return default

def save_json(fp, data):
    with open(fp, "w", encoding="utf-8") as f: json.dump(data, f)

if 'page' not in st.session_state: st.session_state.page = "home"
if 'current_stock' not in st.session_state: st.session_state.current_stock = "2376"
if 'favorites' not in st.session_state: st.session_state.favorites = load_json(FAV_FILE, ["1802", "2330", "1785"])
if 'custom_pool' not in st.session_state: st.session_state.custom_pool = load_json(POOL_FILE, ["2330", "2317", "2454", "2382", "3231"])
if 'nav_pool' not in st.session_state: st.session_state.nav_pool = st.session_state.custom_pool
if 'scan_mode' not in st.session_state: st.session_state.scan_mode = "hot"
if 'view_days' not in st.session_state: st.session_state.view_days = 60
if 'date_offset' not in st.session_state: st.session_state.date_offset = 0

@st.cache_data(ttl=1800)
def fetch_twse_top_50():
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", timeout=10)
        df = pd.DataFrame(res.json())
        df['TradeVolume'] = pd.to_numeric(df['TradeVolume'], errors='coerce')
        return df[df['Code'].str.match(r'^\d{4}$')].sort_values(by='TradeVolume', ascending=False).head(50)['Code'].tolist()
    except: return ["2330", "2317", "2454", "2382", "3231"]

st.sidebar.divider()
st.sidebar.title("⭐ 我的自選股")
if st.session_state.favorites:
    for fav in st.session_state.favorites:
        st.sidebar.button(f"📊 {fav} {CURRENT_STOCK_NAMES.get(fav, '')}", key=f"sf_{fav}", on_click=lambda f=fav: st.session_state.update({"current_stock": f, "page": "analysis", "date_offset": 0}))

st.sidebar.divider()
st.sidebar.title("⚙️ 雷達池設定")
if st.sidebar.button("🔄 更新熱門股", use_container_width=True):
    st.session_state.custom_pool = fetch_twse_top_50()
    save_json(POOL_FILE, st.session_state.custom_pool)
    st.sidebar.success("✅ 完成！")
    st.rerun()

# --- 新增來源標示與連結 ---
st.sidebar.markdown("<div style='font-size: 0.8rem; color: #888; text-align: center; margin-top: 10px;'>資料來源: <a href='https://openapi.twse.com.tw/' target='_blank' style='color: #00ffcc; text-decoration: none;'>台灣證券交易所 OpenAPI</a></div>", unsafe_allow_html=True)


@st.cache_data(ttl=86400)
def get_fundamental_and_industry_data(ticker_number):
    try:
        base_ticker = str(ticker_number).strip().upper().replace(".TW", "").replace(".TWO", "")
        info = yf.Ticker(f"{base_ticker}.TW").info
        if not info or 'trailingEps' not in info: info = yf.Ticker(f"{base_ticker}.TWO").info
        
        sec, ind = info.get("sector", ""), info.get("industry", "")
        tw_sec, tw_ind = ENG_TO_TW_INDUSTRY.get(sec, sec), ENG_TO_TW_INDUSTRY.get(ind, ind)
        full_ind = f"{tw_sec} - {tw_ind}" if tw_sec and tw_ind else tw_sec or tw_ind or "未提供產業資訊"
        return {"EPS": info.get("trailingEps", "無"), "PE": info.get("trailingPE", "無"), "Industry": full_ind}
    except: return {"EPS": "無", "PE": "無", "Industry": "未提供產業資訊"}

@st.cache_data(ttl=300) 
def get_stock_data(ticker_number):
    try:
        base_ticker = str(ticker_number).strip().upper().replace(".TW", "").replace(".TWO", "")
        if base_ticker == "^TWII": df = yf.Ticker("^TWII").history(period="1y")
        else:
            df = yf.Ticker(f"{base_ticker}.TW").history(period="1y")
            if df.empty or len(df)<20: df = yf.Ticker(f"{base_ticker}.TWO").history(period="1y")
            if df.empty or len(df)<20: df = yf.Ticker(base_ticker).history(period="1y")
        
        if df.empty or len(df)<20: return None
        
        # 🚨【新增防呆機制】：剔除 Yahoo 傳回的 NaN (空值) 垃圾資料，避免導致計算崩潰
        df = df.dropna(subset=['Close']) 
        df['Volume'] = df['Volume'].fillna(0) # 確保成交量沒有空值
        
        if df.empty or len(df)<20: return None # 再次確認過濾後資料是否足夠
        
        df['5MA'] = df['Close'].rolling(5).mean()
        df['10MA'] = df['Close'].rolling(10).mean()
        df['20MA'] = df['Close'].rolling(20).mean()
        df['60MA'] = df['Close'].rolling(60).mean()
        df['STD20'] = df['Close'].rolling(20).std()
        df['BB_UP'] = df['20MA'] + (2 * df['STD20'])
        df['BB_DN'] = df['20MA'] - (2 * df['STD20'])
        df['BIAS_20'] = (df['Close'] - df['20MA']) / df['20MA'] * 100
        
        df['MACD'] = df['Close'].ewm(span=12, adjust=False).mean() - df['Close'].ewm(span=26, adjust=False).mean()
        df['Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
        df['MACD_Hist'] = df['MACD'] - df['Signal']
        
        low_9, high_9 = df['Low'].rolling(9).min(), df['High'].rolling(9).max()
        rsv = (df['Close'] - low_9) / (high_9 - low_9) * 100
        df['K'] = rsv.ewm(com=2, adjust=False).mean()
        df['D'] = df['K'].ewm(com=2, adjust=False).mean()
        df['J'] = 3 * df['K'] - 2 * df['D']
        return df
    except: return None

@st.cache_data(ttl=600)
def get_real_news(ticker, name):
    news_list = []
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36'}
    query = urllib.parse.quote(f"{ticker} {name} 股票")
    url = f"https://news.google.com/rss/search?q={query}+when:7d&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
    
    try:
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200:
            for item in ET.fromstring(res.text).findall('.//item')[:3]:
                news_list.append({"title": item.find('title').text, "link": item.find('link').text})
    except: pass
    
    if not news_list:
        try:
            yf_news = yf.Ticker(f"{ticker}.TW").news
            if not yf_news: yf_news = yf.Ticker(f"{ticker}.TWO").news
            for n in yf_news[:3]:
                news_list.append({"title": n.get('title', '新聞標題'), "link": n.get('link', '#')})
        except: pass

    if not news_list:
        news_list.append({"title": f"👉 點擊查看【{ticker} {name}】最新市場消息", "link": f"https://www.google.com/search?q={query}&tbm=nws"})
        
    return news_list

def analyze_today(df, ticker_number):
    if df is None or len(df) < 5: return None
    t, p, p5 = df.iloc[-1], df.iloc[-2], df.iloc[-5]
    fund = get_fundamental_and_industry_data(ticker_number)
    return {
        "代號": ticker_number, "名稱": CURRENT_STOCK_NAMES.get(ticker_number, ""), "ticker_raw": ticker_number,
        "產業": fund['Industry'], "昨日收盤價": round(p['Close'], 2), "收盤價": round(t['Close'], 2), 
        "漲跌": round(t['Close'] - p['Close'], 2), "漲跌幅": round((t['Close'] - p['Close']) / p['Close'] * 100, 2), 
        "近5日漲幅(%)": f"{round((t['Close'] - p5['Close'])/p5['Close']*100, 2)}%",
        "成交量": int(t['Volume']/1000), "5日均量": int(df['Volume'].tail(5).mean()/1000),
        "5MA": round(t['5MA'], 2), "10MA": round(t['10MA'], 2), "20MA": round(t['20MA'], 2),
        "BB_UP": round(t['BB_UP'], 2), "BB_DN": round(t['BB_DN'], 2), "BIAS": round(t['BIAS_20'], 2),
        "MACD": round(t['MACD'], 2), "MACD柱": round(t['MACD_Hist'], 3),
        "K": round(t['K'], 2), "D": round(t['D'], 2), "J值": round(t['J'], 2),
        "訊號": (t['Close'] > t['20MA']) and (t['Close'] < t['5MA']) and (t['J'] < 20)
    }

def get_decision_score(data, fund_data):
    sc, rs = 0, []
    if data['訊號']: sc+=3; rs.append("✅ 穩在月線上且KDJ超賣")
    if data['收盤價'] <= data['BB_DN'] * 1.02: sc+=2; rs.append("✅ 觸及布林下軌")
    if data['BIAS'] < -5: sc+=1; rs.append("✅ 負乖離擴大")
    if isinstance(fund_data['EPS'], (int, float)) and fund_data['EPS'] > 0: sc+=2; rs.append("✅ 基本面獲利")
    if data['成交量'] / (data['5日均量'] + 0.001) > 1.5 and data['漲跌'] > 0: sc+=2; rs.append("✅ 量價配合")
    
    if data['J值'] >= 80: sc-=3; rs.append("⚠️ KDJ高檔過熱")
    if data['收盤價'] >= data['BB_UP'] * 0.98: sc-=2; rs.append("⚠️ 觸及布林上軌")
    if data['BIAS'] > 7: sc-=2; rs.append("⚠️ 正乖離過大")
    if data['收盤價'] < data['20MA']: sc-=2; rs.append("⚠️ 跌破月線")
    if not (isinstance(fund_data['EPS'], (int, float)) and fund_data['EPS'] > 0) and fund_data['EPS'] != "無": sc-=1; rs.append("⚠️ 基本面虧損")
    return sc, rs

def draw_professional_chart(df, ticker_name, latest_price, view_days, is_light_mode):
    df_view = df.tail(view_days)
    colors = ['#ff3333' if row['Close'] >= row['Open'] else '#00cc00' for _, row in df_view.iterrows()]
    last_row = df_view.iloc[-1]
    
    fig = make_subplots(rows=4, cols=1, shared_xaxes=True, row_heights=[0.45, 0.15, 0.15, 0.25], vertical_spacing=0.06)
    
    line_k, line_d, line_j = ("#0066cc", "#ff9900", "#9900cc") if is_light_mode else ("white", "yellow", "magenta")
    grid_c = "rgba(0,0,0,0.1)" if is_light_mode else "rgba(255,255,255,0.1)"
    bg_c = "#ffffff" if is_light_mode else "#0e1117"
    
    fig.add_trace(go.Candlestick(x=df_view.index, open=df_view['Open'], high=df_view['High'], low=df_view['Low'], close=df_view['Close'], increasing_line_color='#ff3333', decreasing_line_color='#00cc00', name="K線"), row=1, col=1)
    fig.add_trace(go.Scatter(x=df_view.index, y=df_view['5MA'], line=dict(color='orange', width=2), name="5T"), row=1, col=1)
    fig.add_trace(go.Scatter(x=df_view.index, y=df_view['10MA'], line=dict(color='#ffcc00', width=2), name="10T"), row=1, col=1)
    fig.add_trace(go.Scatter(x=df_view.index, y=df_view['20MA'], line=dict(color='cyan', width=2), name="20T"), row=1, col=1)
    fig.add_hline(y=latest_price, line_dash="dash", line_color="#ffcc00", row=1, col=1)
    
    fig.add_trace(go.Bar(x=df_view.index, y=df_view['Volume'], marker_color=colors, name="VOL"), row=2, col=1)
    
    macd_colors = ['#ff3333' if val > 0 else '#00cc00' for val in df_view['MACD_Hist']]
    fig.add_trace(go.Bar(x=df_view.index, y=df_view['MACD_Hist'], marker_color=macd_colors, name="OSC (柱)"), row=3, col=1)
    fig.add_trace(go.Scatter(x=df_view.index, y=df_view['MACD'], line=dict(color=line_k, width=1.5), name="DIF"), row=3, col=1)
    fig.add_trace(go.Scatter(x=df_view.index, y=df_view['Signal'], line=dict(color=line_d, width=1.5), name="MACD"), row=3, col=1)
    
    fig.add_trace(go.Scatter(x=df_view.index, y=df_view['K'], line=dict(color=line_k, width=1.5), name="K"), row=4, col=1)
    fig.add_trace(go.Scatter(x=df_view.index, y=df_view['D'], line=dict(color=line_d, width=1.5), name="D"), row=4, col=1)
    fig.add_trace(go.Scatter(x=df_view.index, y=df_view['J'], line=dict(color=line_j, width=1.5), name="J"), row=4, col=1)

    fig.update_xaxes(fixedrange=True, showgrid=True, gridcolor=grid_c)
    fig.update_yaxes(fixedrange=True, showgrid=True, gridcolor=grid_c)
    fig.update_layout(
        xaxis_rangeslider_visible=False, template="plotly_white" if is_light_mode else "plotly_dark", height=850, 
        margin=dict(l=10, r=10, t=10, b=10), paper_bgcolor=bg_c, plot_bgcolor=bg_c, 
        hovermode='x unified', hoverlabel=dict(font_size=13), dragmode=False, showlegend=False
    )
    return fig

tz_tpe = timezone(timedelta(hours=8))

def predict_tomorrow_open(twii_df, sox_df):
    if twii_df is None or twii_df.empty: return "資料不足", ""
    t_val = twii_df['Close'].iloc[-1]
    ma5 = twii_df['5MA'].iloc[-1]
    
    score = 0
    if t_val > ma5: score += 1
    else: score -= 1
    
    if sox_df is not None and not sox_df.empty:
        s_change = (sox_df['Close'].iloc[-1] - sox_df['Close'].iloc[-2]) / sox_df['Close'].iloc[-2] * 100
        if s_change > 1.5: score += 2
        elif s_change < -1.5: score -= 2
        elif s_change > 0: score += 1
        else: score -= 1
        
    if score >= 2: return "🚀 高機率開高", "美股強勢且台股站穩短均線，預估明日早盤有機會跳空開高。"
    elif score == 1: return "📈 偏多震盪", "國際局勢穩定，台股具備抗跌韌性，預估開平高盤後震盪走高。"
    elif score == 0: return "⚖️ 觀望平盤", "多空力道均衡，預估開平盤附近，需觀察開盤後主力買賣超方向。"
    elif score == -1: return "📉 偏空震盪", "大盤技術面偏弱，預期受國際盤勢拖累，可能開平低盤。"
    else: return "⚠️ 高機率開低", "美股重挫且台股跌破均線，市場恐慌情緒蔓延，預防跳空開低。"

def render_index_board():
    now_time_str = datetime.now(tz_tpe).strftime('%Y/%m/%d %H:%M:%S')
    twii_df = get_stock_data("^TWII")
    twii_close = twii_df['Close'].iloc[-1] if twii_df is not None else 0
    twii_change = (twii_df['Close'].iloc[-1] - twii_df['Close'].iloc[-2]) if twii_df is not None else 0
    twii_color = '#ff3333' if twii_change >= 0 else '#00cc00'
    
    sox_df = None
    try: sox_df = yf.Ticker("^SOX").history(period="5d")
    except: pass
    
    pred_title, pred_desc = predict_tomorrow_open(twii_df, sox_df)
    
    with st.container(border=True):
        col1, col2 = st.columns([1.1, 1.2])
        with col1:
            st.markdown(f"<div style='text-align: center; font-size: 1.1rem; font-weight: bold;'>台灣加權指數</div>", unsafe_allow_html=True)
            st.markdown(f"<div style='text-align: center; font-size: 2.3rem; font-weight: 900; color: {twii_color}; margin: 5px 0;'>{twii_close:,.0f}</div>", unsafe_allow_html=True)
            st.markdown(f"<div style='text-align: center; font-size: 1.2rem; font-weight: bold; color: {twii_color};'>{'↑' if twii_change > 0 else '↓'} {abs(twii_change):.0f}</div>", unsafe_allow_html=True)
        with col2:
            st.markdown(f"<div style='text-align: left; color: #ffcc00; font-size: 1.05rem; font-weight: bold;'>🔮 明日開盤預測模型</div>", unsafe_allow_html=True)
            st.markdown(f"<div style='text-align: left; font-size: 1.1rem; font-weight: bold;'>{pred_title}</div>", unsafe_allow_html=True)
            st.markdown(f"<div style='text-align: left; font-size: 0.85rem; margin-top: 5px; line-height: 1.4;'>{pred_desc}</div>", unsafe_allow_html=True)
            
        st.markdown("<hr style='margin: 10px 0; border-color: #444;'>", unsafe_allow_html=True)
        st.markdown("<span style='font-size:0.95rem; font-weight:bold; color:#ffcc00;'>📰 財經焦點新聞：</span>", unsafe_allow_html=True)
        news_items = []
        try:
            yf_news = yf.Ticker("0050.TW").news
            if not yf_news: yf_news = yf.Ticker("2330.TW").news
            for n in yf_news[:3]: news_items.append({"title": n.get("title", ""), "link": n.get("link", "")})
        except: pass
        if not news_items: news_items = get_real_news("台股", "加權指數")

        if news_items:
            for n in news_items[:3]:
                if n['title'] and n['title'] != "👉 Google 新聞搜尋":
                    st.markdown(f"<a href='{n['link']}' target='_blank' style='color:#00ffcc; font-size:0.85rem; text-decoration: none; display: block; margin-top: 6px;'>➤ {n['title']}</a>", unsafe_allow_html=True)
            
    st.markdown(f"<div style='text-align: right; color: #666; font-size: 0.8rem; margin-top: -10px;'>🔄 系統最後更新: {now_time_str}</div>", unsafe_allow_html=True)

if st.session_state.page == "home":
    st.markdown("<h1 style='text-align: center;'>🇹🇼 雷達總機</h1>", unsafe_allow_html=True)
    render_index_board()
    
    st.markdown("<h3 style='margin-top: 15px;'>🎯 掃描買點</h3>", unsafe_allow_html=True)
    btn_col1, btn_col2, btn_col3 = st.columns(3)
    if btn_col1.button("✅ 尋找買點", use_container_width=True): st.session_state.scan_mode = "buy"; st.rerun()
    if btn_col2.button("📋 熱門名單", use_container_width=True): st.session_state.scan_mode = "hot"; st.rerun()
    if btn_col3.button("🔥 近日熱力", use_container_width=True): st.session_state.scan_mode = "recent"; st.rerun()
        
    search_val = st.text_input("隱藏", placeholder="🔍 搜尋股票 (輸入代號並按 Enter)", label_visibility="collapsed")
    if search_val:
        st.session_state.update({"current_stock": search_val, "date_offset": 0, "page": "analysis"})
        st.rerun()
        
    scan_results = []
    with st.spinner('掃描中...'):
        pool = list(set(st.session_state.custom_pool + ["2330", "2317", "2454", "2308", "2382", "2603", "2881", "2409"]))
        for stock in pool:
            data = analyze_today(get_stock_data(stock), stock)
            if data: scan_results.append(data)
            
    if scan_results:
        df_results = pd.DataFrame(scan_results)
        if st.session_state.scan_mode == "recent":
            st.markdown("##### 🔥 近五日熱門排行榜")
            df_disp = df_results.sort_values(by="成交量", ascending=False).head(20)
            
            st.markdown("---")
            ca, cb, cc, cd, ce, cf = st.columns([1.5, 2.5, 1.5, 1.5, 2, 1.5])
            ca.markdown("**代號**"); cb.markdown("**名稱**"); cc.markdown("**昨收**"); cd.markdown("**今收**"); ce.markdown("**近5日漲幅**"); cf.markdown("**動作**")
            st.markdown("---")
            
            for _, r in df_disp.iterrows():
                ca, cb, cc, cd, ce, cf = st.columns([1.5, 2.5, 1.5, 1.5, 2, 1.5])
                ca.markdown(f"`{r['代號']}`")
                cb.markdown(f"**{r['名稱']}**<br><span style='font-size:0.75rem; color:#888;'>{r['產業']}</span>", unsafe_allow_html=True)
                cc.markdown(f"{r['昨日收盤價']}")
                cd.markdown(f"{r['收盤價']}")
                v5 = float(r['近5日漲幅(%)'].strip('%'))
                c5 = "#ff3333" if v5 > 0 else "#00cc00" if v5 < 0 else text_col
                ce.markdown(f"<span style='color:{c5}; font-weight:bold;'>{r['近5日漲幅(%)']}</span>", unsafe_allow_html=True)
                with cf:
                    if st.button("📊 解析", key=f"br_{r['ticker_raw']}", use_container_width=True):
                        st.session_state.update({"current_stock": r['ticker_raw'], "date_offset": 0, "page": "analysis"}); st.rerun()
                st.markdown("<hr style='margin:0; padding:0;'>", unsafe_allow_html=True)
        else:
            if st.session_state.scan_mode == "buy":
                df_disp = df_results[df_results['訊號'] == True]
                if len(df_disp) < 5:
                    pot = df_results[(df_results['訊號']==False) & (df_results['J值']<30)].sort_values(by='J值')
                    df_disp = pd.concat([df_disp, pot.head(5 - len(df_disp))])
            else: df_disp = df_results.sort_values(by="成交量", ascending=False).head(10)
            
            for _, r in df_disp.iterrows():
                with st.container(border=True):
                    c1, c2 = st.columns([8, 2])
                    with c1: st.markdown(f"### `{r['代號']}` **{r['名稱']}**")
                    with c2:
                        if st.button("⭐ 移除" if r['ticker_raw'] in st.session_state.favorites else "☆ 收藏", key=f"s_{r['ticker_raw']}", use_container_width=True):
                            if r['ticker_raw'] in st.session_state.favorites: st.session_state.favorites.remove(r['ticker_raw'])
                            else: st.session_state.favorites.append(r['ticker_raw'])
                            save_json(FAV_FILE, st.session_state.favorites); st.rerun()
                    
                    bg_c = "#ffffff" if is_light_mode else "#1a1c24"
                    border_c = "#ddd" if is_light_mode else "#333"
                    p_color = "#ff3333" if r['漲跌'] >= 0 else "#00cc00"
                    st.markdown(f'''<div style="background-color: {bg_c}; padding: 12px; border-radius: 8px; border: 1px solid {border_c}; text-align: center; margin: 5px 0 10px 0;"><span style="font-size: 2.6rem; font-weight: 900; color: {p_color};">{r['收盤價']}</span><span style="font-size: 1.3rem; font-weight: bold; color: {p_color}; margin-left: 12px;">{'+' if r['漲跌']>0 else ''}{r['漲跌']} ({r['漲跌幅']}%)</span></div>''', unsafe_allow_html=True)
                    if st.button("📊 解析", key=f"bp_{r['ticker_raw']}", use_container_width=True):
                        st.session_state.update({"current_stock": r['ticker_raw'], "date_offset": 0, "page": "analysis"}); st.rerun()

elif st.session_state.page == "analysis":
    target = st.session_state.current_stock
    df_chart = get_stock_data(target)
    c_name = CURRENT_STOCK_NAMES.get(target, "")
    f_data = get_fundamental_and_industry_data(target)
    yh = f"https://tw.stock.yahoo.com/quote/{target}"
    
    n_pool = st.session_state.get('nav_pool', st.session_state.custom_pool)
    p_stk, n_stk = None, None
    if target in n_pool and len(n_pool) > 1:
        i = n_pool.index(target)
        p_stk = n_pool[i - 1] if i > 0 else None
        n_stk = n_pool[i + 1] if i < len(n_pool) - 1 else None

    c1, c2, c3 = st.columns([1, 1, 1])
    with c1:
        if p_stk and st.button(f"⬅ 上一檔", use_container_width=True): st.session_state.update({"current_stock": p_stk, "date_offset": 0}); st.rerun()
    with c2:
        if st.button("🏠 回首頁", use_container_width=True): st.session_state.page = "home"; st.rerun()
    with c3:
        if n_stk and st.button(f"下一檔 ➡", use_container_width=True): st.session_state.update({"current_stock": n_stk, "date_offset": 0}); st.rerun()
        
    if df_chart is not None:
        df_slice = df_chart.iloc[:len(df_chart) + st.session_state.date_offset] if st.session_state.date_offset < 0 else df_chart
        
        if len(df_slice) < 5:
            st.warning("歷史資料不足"); st.button("返回", on_click=lambda: st.session_state.update({"date_offset": st.session_state.date_offset + 1}))
        else:
            data = analyze_today(df_slice, target)
            v_dt = df_slice.index[-1].strftime('%Y/%m/%d')
            sc, rs = get_decision_score(data, f_data)

            if sc >= 5: v_t, v_a, v_c = "🟢 【S級買點】強烈建議佈局", f"建議於 {data['BB_DN']:.2f} ~ {data['20MA']:.2f} 之間分批建倉。", "#00cc00"
            elif sc >= 2: v_t, v_a, v_c = "🟡 【A級機會】偏多試單", f"可於 {data['收盤價']:.2f} 附近試單，跌破 {data['BB_DN']:.2f} 停損。", "#ffcc00"
            elif sc >= -1: v_t, v_a, v_c = "⚪ 【中性觀望】多空不明", f"建議等待突破 {data['20MA']:.2f} 或回測 {data['BB_DN']:.2f}。", text_col
            elif sc >= -4: v_t, v_a, v_c = "🟠 【風險警示】逢高減碼", f"追高風險大，若持有建議於 {data['收盤價']:.2f} ~ {data['BB_UP']:.2f} 獲利了結。", "#ff9900"
            else: v_t, v_a, v_c = "🔴 【極度危險】嚴禁做多", "強烈建議空手觀望，切勿接刀。", "#ff3333"

            p_color = '#ff3333' if data['漲跌'] >= 0 else '#00cc00'
            st.markdown(f"<h2 style='text-align: center; margin-bottom: 5px;'>🎯 {target} {c_name}</h2>", unsafe_allow_html=True)
            st.markdown(f"<div style='text-align: center; color: #888; font-size: 1.1rem;'>【{f_data['Industry']}】</div>", unsafe_allow_html=True)
            st.markdown(f"<h3 style='text-align: center; color: {p_color}; font-size: 2.2rem; margin-bottom: 0px;'>{data['收盤價']} ({'+' if data['漲跌']>0 else ''}{data['漲跌幅']}%)</h3>", unsafe_allow_html=True)
            st.markdown(f"<div style='text-align: center; color: #888; font-size: 0.95rem; margin-bottom: 15px;'>📊 檢視日期: {v_dt}</div>", unsafe_allow_html=True)
            
            tc1, tc2, tc3, tc4 = st.columns([1, 1, 1, 1])
            with tc1:
                if st.button("⬅️ 前一日", use_container_width=True): st.session_state.date_offset -= 1; st.rerun()
            with tc2: st.markdown(f"<div style='text-align: center; margin-top: 5px; font-weight: bold;'>📅 時光機</div>", unsafe_allow_html=True)
            with tc3:
                if st.session_state.date_offset < 0:
                    if st.button("🎯 回到今日", use_container_width=True): st.session_state.date_offset = 0; st.rerun()
            with tc4:
                if st.button("後一日 ➡️", use_container_width=True, disabled=(st.session_state.date_offset >= 0)): st.session_state.date_offset += 1; st.rerun()

            st.markdown("---")
            st.markdown("##### 💡 近一個月歷史買點回測")
            recent_30 = df_chart.tail(30)
            found_opp = False
            
            btn_cols = st.columns(4)
            col_idx = 0
            
            for idx in range(len(recent_30)):
                temp_df = df_chart.iloc[:len(df_chart) - 30 + idx + 1]
                t_data = analyze_today(temp_df, target)
                t_sc, _ = get_decision_score(t_data, f_data)
                
                if t_sc >= 2: 
                    found_opp = True
                    dt_str = temp_df.index[-1].strftime('%m/%d')
                    badge = "🟢 S級" if t_sc >= 5 else "🟡 A級"
                    
                    with btn_cols[col_idx % 4]:
                        jump_offset = -(len(df_chart) - len(temp_df))
                        if st.button(f"{dt_str} {badge}", key=f"hist_{dt_str}", use_container_width=True):
                            st.session_state.date_offset = jump_offset
                            st.rerun()
                    col_idx += 1
            
            if not found_opp:
                st.info("近一個月內無 A 級以上的極佳買點。")
            st.markdown("---")

            st.markdown(f'''<div style="border: 2px solid {v_c}; border-radius: 10px; padding: 15px; margin-bottom: 20px; background-color: {bg_col};"><h3 style="text-align: center; color: {v_c}; margin-top: 0;">{v_t}</h3><p style="text-align: center; font-size: 1.1rem; font-weight: bold; color: {text_col};">{v_a}</p><hr style="border-color: {border_col};"><p style="font-size: 0.9rem; color: {sub_text_col}; margin-bottom: 5px;"><strong>🧠 決策引擎分析依據：</strong></p><ul style="font-size: 0.9rem; color: {text_col}; line-height: 1.6;">{"".join([f"<li>{r}</li>" for r in rs])}</ul></div>''', unsafe_allow_html=True)
            
            dc1, dc2, dc3, dc4 = st.columns(4)
            if dc1.button("1個月"): st.session_state.view_days = 20
            if dc2.button("3個月"): st.session_state.view_days = 60
            if dc3.button("6個月"): st.session_state.view_days = 120
            if dc4.button("1年"): st.session_state.view_days = 240
            
            fig = draw_professional_chart(df_slice, target, data['收盤價'], st.session_state.view_days, is_light_mode)
            st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})
            
            st.markdown("### 📈 基礎技術指標")
            r1c1, r1c2, r1c3 = st.columns(3)
            with r1c1.container(border=True):
                st.markdown(f"**均線** (昨收: `{data['昨日收盤價']}`) \n\n 5T: `{data['5MA']}` \n\n 10T: `{data['10MA']}` \n\n 20T: `{data['20MA']}` \n\n <a href='{yh}/technical-analysis' target='_blank' style='font-size:0.75rem; text-decoration:none;'>🔗Yahoo</a>", unsafe_allow_html=True)
            with r1c2.container(border=True):
                st.markdown(f"**MACD** \n\n DIF: `{data['MACD']}` \n\n OSC: `{data['MACD柱']}` \n\n <br><br> <a href='{yh}/technical-analysis' target='_blank' style='font-size:0.75rem; text-decoration:none;'>🔗Yahoo</a>", unsafe_allow_html=True)
            with r1c3.container(border=True):
                st.markdown(f"**KDJ** \n\n K: `{data['K']}` \n\n D: `{data['D']}` \n\n J: `{data['J值']}` \n\n <br> <a href='{yh}/technical-analysis' target='_blank' style='font-size:0.75rem; text-decoration:none;'>🔗Yahoo</a>", unsafe_allow_html=True)

            st.markdown("### 🕵️‍♂️ 進階數據面板")
            a1, a2 = st.columns(2)
            with a1.container(border=True):
                st.markdown(f"##### 📊 布林通道 & 乖離率 \n\n **上軌 (壓力):** `{data['BB_UP']}` \n\n **下軌 (支撐):** `{data['BB_DN']}` \n\n **月線乖離率:** `{data['BIAS']}%` \n\n <a href='{yh}/technical-analysis' target='_blank' style='font-size:0.75rem; text-decoration:none;'>🔗Yahoo</a>", unsafe_allow_html=True)

            with a2.container(border=True):
                eps = f_data['EPS']
                m_eps = round(eps/12, 2) if isinstance(eps, (int, float)) else "無"
                st.markdown(f"##### 📑 基本面健檢 \n\n **近四季 EPS:** `{eps}` <a href='{yh}/eps' target='_blank' style='font-size:0.8rem; text-decoration:none;'>🔗來源</a> \n\n **換算單月 EPS:** `{m_eps}` \n\n **本益比 (P/E):** `{f_data['PE']}` <a href='{yh}/profile' target='_blank' style='font-size:0.8rem; text-decoration:none;'>🔗來源</a>", unsafe_allow_html=True)

            st.divider()
            st.subheader("🔗 同產業關聯股動態")
            if f_data['Industry'] != "未提供產業資訊":
                rels = [c for c, n in STOCK_NAMES.items() if get_fundamental_and_industry_data(c)['Industry'] == f_data['Industry'] and c != target][:3]
                if rels:
                    st.markdown(f"以下為同樣屬於 **【{f_data['Industry']}】** 的熱門標的：")
                    cs = st.columns(len(rels))
                    for i, r in enumerate(rels):
                        with cs[i].container(border=True):
                            r_df = get_stock_data(r)
                            if r_df is not None:
                                rc, rp = round(r_df['Close'].iloc[-1], 2), round((r_df['Close'].iloc[-1] - r_df['Close'].iloc[-2])/r_df['Close'].iloc[-2]*100, 2)
                                rcol = "#ff3333" if rp >= 0 else "#00cc00"
                                st.markdown(f"**{r} {CURRENT_STOCK_NAMES.get(r, '')}** <br> <span style='color:{rcol}; font-weight:bold;'>{rc} ({'+' if rp>0 else ''}{rp}%)</span>", unsafe_allow_html=True)
                                if st.button("分析", key=f"b_r_{r}"): st.session_state.update({"current_stock": r, "date_offset": 0, "page": "analysis"}); st.rerun()
                else: st.info("無其他同產業標的。")
            else: st.info("無法識別該股產業。")

            st.divider()
            st.markdown("**🏦 真實籌碼與主力動向查詢**")
            st.markdown(f"[➤ 點擊前往 Yahoo 股市看【外資投信買賣超】](https://tw.stock.yahoo.com/quote/{target}/institutional-trading)")
            st.markdown(f"[➤ 點擊前往 Goodinfo 看【主力進出明細】](https://goodinfo.tw/tw/ShowBuySaleChart.asp?STOCK_ID={target})")
            
            st.divider()
            st.subheader("📰 相關新聞")
            news_items = get_real_news(target, c_name)
            if news_items:
                for n in news_items:
                    st.markdown(f"<a href='{n['link']}' target='_blank' style='color:#00ffcc; font-size:1rem; text-decoration: none; display: block; margin-top: 6px;'>➤ {n['title']}</a>", unsafe_allow_html=True)
            else: st.info("目前暫無相關新聞。")
            
            st.divider()
            if target in st.session_state.favorites:
                if st.button("❌ 從自選股移除此標的", type="primary", use_container_width=True):
                    st.session_state.favorites.remove(target); save_json(FAV_FILE, st.session_state.favorites); st.rerun()
            else:
                if st.button("⭐ 將此標的加入自選股", type="primary", use_container_width=True):
                    st.session_state.favorites.append(target); save_json(FAV_FILE, st.session_state.favorites); st.rerun()
    else: st.error("查無此股票資料。")
