import yfinance as yf
import streamlit as st
import pandas as pd
import requests
import time
from datetime import datetime, timezone, timedelta
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import json
import os
import urllib.parse
import xml.etree.ElementTree as ET
import streamlit.components.v1 as components
from bs4 import BeautifulSoup
import re
import concurrent.futures

from streamlit_autorefresh import st_autorefresh

# ==========================================
# 0. 系統初始化與風格設定
# ==========================================
st.set_page_config(page_title="專業交易雷達", layout="wide", initial_sidebar_state="collapsed")

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

if st.sidebar.button("🗑️ 強制清除快取資料", use_container_width=True):
    st.cache_data.clear()
    st.sidebar.success("已清除暫存，請重整網頁！")

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
    [data-testid="collapsedControl"]::after {{ content: " ⭐ 我的群組"; font-size: 1.1rem; font-weight: bold; color: #ffcc00; margin-left: 8px; }}
    .stButton button {{ font-weight: bold !important; border-radius: 8px !important; text-align: left !important; }}
    button[kind="primary"] {{ font-size: 1.5rem !important; padding: 15px !important; border-radius: 12px !important; background-color: #ffcc00 !important; color: #111 !important; border: none !important; }}
    .sticky-header {{ position: sticky; top: 0; z-index: 999; background-color: {sticky_bg}; padding: 10px 0; border-bottom: 1px solid {border_col}; backdrop-filter: blur(5px); margin-top: -15px; margin-bottom: 15px; }}
    div[data-testid="stVerticalBlockBorderWrapper"] > div {{ background-color: {bg_col} !important; border-color: {border_col} !important; padding: 4px !important; }}
    h1, h2, h3, h4, p, span {{ color: {title_col} !important; }}
    .compact-btn button {{ padding: 0.25rem 0.5rem !important; font-size: 1rem !important; }}
    
    .risk-bar-container {{ width: 100%; background-color: #333; border-radius: 8px; margin-top: 5px; margin-bottom: 15px; overflow: hidden; }}
    .risk-bar-fill {{ height: 16px; border-radius: 8px; transition: width 0.5s ease-in-out; }}
</style>
''', unsafe_allow_html=True)

STOCK_NAMES = { "2330": "台積電", "2317": "鴻海", "2454": "聯發科", "2308": "台達電", "2382": "廣達", "2376": "技嘉", "1802": "台玻", "2603": "長榮", "1785": "光洋科", "1519": "華城", "6147": "頎邦" }

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

st.sidebar.title("🔍 快速搜尋")

with st.sidebar.form(key="search_form"):
    search_input = st.text_input("隱藏", placeholder="輸入股票代號或中文名稱...", label_visibility="collapsed")
    submit_search = st.form_submit_button("送出搜尋", use_container_width=True)
    
if submit_search and search_input:
    s_val = search_input.strip().replace(" ", "")
    if s_val:
        target_ticker = None
        if re.match(r'^[A-Za-z0-9]+$', s_val):
            target_ticker = s_val.upper()
        else:
            for code, name in CURRENT_STOCK_NAMES.items():
                if s_val in name:
                    target_ticker = code
                    break
        if target_ticker:
            st.session_state.current_stock = target_ticker
            st.session_state.page = "analysis"
            st.session_state.date_offset = 0
            st.rerun() 
        else:
            st.session_state.search_error = f"⚠️ 找不到與「{s_val}」相關的標的。"

if "search_error" in st.session_state and st.session_state.search_error:
    st.sidebar.warning(st.session_state.search_error)
    st.session_state.search_error = ""

st.sidebar.divider()

st.sidebar.title("⏱️ 盤中即時跳動雷達")
auto_refresh = st.sidebar.toggle("🟢 開啟即時自動更新 (每30秒)", False)

if auto_refresh:
    st_autorefresh(interval=30000, limit=None, key="market_auto_refresh")
    st.sidebar.success("⚡ 盤中高頻探測已啟動！")

st.sidebar.divider()

ENG_TO_TW_INDUSTRY = {
    "Semiconductors": "半導體業", "Consumer Electronics": "消費性電子", "Electronic Components": "電子零組件",
    "Computer Hardware": "電腦及週邊設備", "Building Materials": "玻璃陶瓷", "Marine Shipping": "航運業",
    "Electrical Equipment & Parts": "電機機械", "Software - Entertainment": "文化創意業", "Technology": "電子科技",
    "Industrials": "工業", "Basic Materials": "原物料", "Financial Services": "金融業",
    "Consumer Cyclical": "非必需消費品", "Healthcare": "生技醫療", "Real Estate": "建材營造",
    "Utilities": "公用事業", "Energy": "能源", "Communication Services": "通信網路"
}

@st.cache_data(ttl=86400, show_spinner=False)
def get_real_chinese_name(ticker):
    try:
        res = requests.get(f"https://invest.cnyes.com/twstock/TWS/{ticker}", headers={'User-Agent': 'Mozilla/5.0'}, timeout=3)
        soup = BeautifulSoup(res.text, 'html.parser')
        h2 = soup.find('h2')
        if h2:
            name = h2.text.strip()
            if name and not name.isdigit(): return name
    except: pass
    try:
        res = requests.get(f"https://histock.tw/stock/{ticker}", headers={'User-Agent': 'Mozilla/5.0'}, timeout=3)
        soup = BeautifulSoup(res.text, 'html.parser')
        title = soup.find('title')
        if title:
            name = title.text.split('(')[0].strip()
            if name and ticker not in name and "嗨投資" not in name and not name.isdigit(): return name
    except: pass
    return ""

def get_stock_name(ticker):
    if not ticker: return ""
    ticker_str = str(ticker).strip().upper().replace(".TW", "").replace(".TWO", "")
    name = ""
    if ticker_str in CURRENT_STOCK_NAMES and CURRENT_STOCK_NAMES[ticker_str]: 
        name = CURRENT_STOCK_NAMES[ticker_str]
    elif ticker_str in STOCK_NAMES: 
        name = STOCK_NAMES[ticker_str]
    else:
        html_name = get_real_chinese_name(ticker_str)
        if html_name: 
            STOCK_NAMES[ticker_str] = html_name 
            name = html_name
        else: name = ticker_str
    name = name.replace(ticker_str, "").strip()
    return name

FAV_FILE = "favorites.json" 
FAV_GROUPS_FILE = "fav_groups.json" 
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
if 'custom_pool' not in st.session_state: st.session_state.custom_pool = load_json(POOL_FILE, ["2330", "2317", "2454", "2382", "3231"])
if 'nav_pool' not in st.session_state: st.session_state.nav_pool = st.session_state.custom_pool
if 'scan_mode' not in st.session_state: st.session_state.scan_mode = "buy"
if 'view_days' not in st.session_state: st.session_state.view_days = 20
if 'date_offset' not in st.session_state: st.session_state.date_offset = 0

if 'url_parsed' not in st.session_state:
    st.session_state.url_parsed = True
    params = st.query_params
    if 'stock' in params:
        st.session_state.current_stock = params['stock']
        st.session_state.page = "analysis"
        st.session_state.date_offset = 0

if 'fav_groups' not in st.session_state:
    default_groups = {"預設群組": ["1802", "2330", "1785"]}
    if os.path.exists(FAV_FILE) and not os.path.exists(FAV_GROUPS_FILE):
        old_favs = load_json(FAV_FILE, ["1802", "2330", "1785"])
        default_groups["預設群組"] = old_favs
    st.session_state.fav_groups = load_json(FAV_GROUPS_FILE, default_groups)

@st.cache_data(ttl=1800)
def fetch_twse_top_100():
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", timeout=10)
        df = pd.DataFrame(res.json())
        df['TradeVolume'] = pd.to_numeric(df['TradeVolume'], errors='coerce')
        return df[df['Code'].str.match(r'^\d{4}$')].sort_values(by='TradeVolume', ascending=False).head(100)['Code'].tolist()
    except: return ["2330", "2317", "2454", "2382", "3231"]

@st.cache_data(ttl=60, show_spinner=False) 
def get_stock_data(ticker_number):
    base_ticker = str(ticker_number).strip().upper().replace(".TW", "").replace(".TWO", "")
    def fetch_clean(sym):
        try:
            d = yf.Ticker(sym).history(period="1y")
            if d is not None and not d.empty:
                d = d.dropna(subset=['Close'])
                if len(d) >= 20: 
                    d.index = pd.to_datetime(d.index.strftime('%Y-%m-%d'))
                    return d
        except: pass
        return None

    df = fetch_twse_index_history() if base_ticker == "^TWII" else fetch_clean(f"{base_ticker}.TW")
    if df is None and base_ticker != "^TWII": df = fetch_clean(f"{base_ticker}.TWO")
    if df is None and base_ticker != "^TWII": df = fetch_clean(base_ticker)
    
    if df is None: return None
    
    try:
        url = "https://ws.cnyes.com/charting/api/v1/TWS:TSE01:INDEX/quote" if base_ticker == "^TWII" else f"https://ws.cnyes.com/charting/api/v1/TWS:{base_ticker}:STOCK/quote"
        res = requests.get(url, timeout=3)
        if res.status_code == 200:
            q = res.json()['data']['quote']
            c_price, o_price = float(q['23']), float(q['22'])
            h_price, l_price = float(q['25']), float(q['26'])
            v_vol = float(q.get('14', 0)) * 1000 if base_ticker != "^TWII" else 0
            ts = int(q['20'])
            dt_live = pd.to_datetime(datetime.fromtimestamp(ts, timezone(timedelta(hours=8))).strftime('%Y-%m-%d'))
            
            if dt_live not in df.index:
                new_row = pd.DataFrame({'Open': [o_price], 'High': [h_price], 'Low': [l_price], 'Close': [c_price], 'Volume': [v_vol]}, index=[dt_live])
                df = pd.concat([df, new_row])
            else:
                df.at[dt_live, 'Close'] = c_price
                df.at[dt_live, 'High'] = max(df.at[dt_live, 'High'], h_price)
                df.at[dt_live, 'Low'] = min(df.at[dt_live, 'Low'], l_price)
                if base_ticker != "^TWII": df.at[dt_live, 'Volume'] = max(df.at[dt_live, 'Volume'], v_vol)
    except: pass

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
    low_9 = df['Low'].rolling(9).min()
    high_9 = df['High'].rolling(9).max()
    rsv = (df['Close'] - low_9) / (high_9 - low_9) * 100
    df['K'] = rsv.ewm(com=2, adjust=False).mean()
    df['D'] = df['K'].ewm(com=2, adjust=False).mean()
    df['J'] = 3 * df['K'] - 2 * df['D']
    return df

@st.cache_data(ttl=86400, show_spinner=False)
def get_fundamental_and_industry_data(ticker_number, current_price=0):
    base_ticker = str(ticker_number).strip().upper().replace(".TW", "").replace(".TWO", "")
    eps_val, pe_val = "無", "無"
    ind = "一般產業"
    try:
        url = f"https://invest.cnyes.com/twstock/TWS/{base_ticker}/overview"
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, 'html.parser')
            text = soup.get_text(separator='|')
            match = re.search(r'當季EPS\|+([\-\d\.]+)', text)
            if match: eps_val = match.group(1)
            else:
                res_api = requests.get(f"https://ws.cnyes.com/twstock/api/v1/company/profile/{base_ticker}", timeout=3)
                if res_api.status_code == 200:
                    data = res_api.json()
                    if 'data' in data and 'eps' in data['data']: eps_val = f"{float(data['data']['eps']):.2f}"
    except: pass
    try:
        info = yf.Ticker(f"{base_ticker}.TW").info
        if not info or 'industry' not in info: info = yf.Ticker(f"{base_ticker}.TWO").info
        sec, ind_eng = info.get("sector", ""), info.get("industry", "")
        tw_sec = ENG_TO_TW_INDUSTRY.get(sec, sec)
        tw_ind = ENG_TO_TW_INDUSTRY.get(ind_eng, ind_eng)
        ind_temp = f"{tw_sec} - {tw_ind}" if tw_sec and tw_ind else tw_sec or tw_ind or "一般產業"
        if not re.search(r'[a-zA-Z]', ind_temp): ind = ind_temp
        if eps_val == "無" and 'trailingEps' in info and info['trailingEps'] is not None:
            eps_val = str(round(info['trailingEps'], 2))
    except: pass
    try:
        if eps_val != "無":
            eps_f = float(eps_val)
            if eps_f > 0 and current_price > 0: pe_val = str(round(float(current_price) / eps_f, 2))
            elif eps_f <= 0: pe_val = "無 (EPS ≦ 0)"
    except: pass
    return {"EPS": eps_val, "PE": pe_val, "Industry": ind}

def get_decision_score(data, fund_data, inst_data=None):
    sc, rs = 0, []
    if data['訊號']: sc+=3; rs.append("✅ 穩在月線上且KDJ超賣")
    if data['收盤價'] <= data['BB_DN'] * 1.02: sc+=2; rs.append("✅ 觸及布林下軌支撐")
    if data['BIAS'] < -5: sc+=1; rs.append("✅ 負乖離過大")
    
    try: eps_f = float(str(fund_data['EPS']).replace(',', ''))
    except: eps_f = 0.0
    if eps_f > 0: sc+=2; rs.append("✅ 基本面獲利")
    
    if data.get('成交量', 0) > data.get('5日均量', 0) * 1.1: sc+=2; rs.append("✅ 量能放大 (具備主力進場點火特徵)")
    else: sc-=1; rs.append("⚠️ 量能未明顯放大 (打底或缺乏點火動能)")
        
    if data.get('MACD柱', 0) > data.get('前日MACD柱', -999): sc+=2; rs.append("✅ MACD 綠柱收斂或紅柱放大 (動能防禦過關)")
    else: sc-=3; rs.append("⚠️ MACD 空方動能持續擴大 (型態脆弱嚴防接刀)")

    if inst_data and len(inst_data) >= 3:
        net_buy = sum([int(str(x['單日合計(張)']).replace(',', '')) for x in inst_data[:3] if str(x['單日合計(張)']).replace(',', '').lstrip('-').isdigit()])
        if net_buy > 0: rs.append(f"✅ 法人近三日偏多 (累計買超 {net_buy} 張)")
        else: rs.append(f"⚠️ 法人近三日偏空 (累計賣超 {abs(net_buy)} 張)")

    if data.get('紅吞'): sc+=3; rs.append("🔥 出現「紅吞」反轉型態 (強烈多頭買進訊號)")
    if data.get('黑吞'): sc-=3; rs.append("🩸 出現「黑吞」反轉型態 (強烈空頭逃命訊號)")

    if data.get('回測有撐'): sc+=2; rs.append("🔥 帶量長下影線 (主力回測支撐成功)")
    if data.get('反彈遇壓'): sc-=2; rs.append("🩸 反彈遇均線壓力留長上影線 (空方壓制)")
    
    # 🎯 回歸原始：只看 5MA 上彎 (不加減分，僅顯示文字診斷)
    if data['收盤價'] >= data['5MA'] and data.get('5日線即將上彎'): 
        rs.append("🔥 5日線扣低值 (短均線準備上彎發散，短線動能轉強)")
    if data['收盤價'] < data['5MA'] and not data.get('5日線即將上彎'): 
        rs.append("⚠️ 5日線扣高值 (短均線即將下彎產生蓋頭壓力)")

    if data['J值'] >= 80: sc-=3; rs.append("⚠️ KDJ高檔過熱")
    if data['收盤價'] >= data['BB_UP'] * 0.98: sc-=2; rs.append("⚠️ 觸及布林上軌壓力")
    if data['BIAS'] > 7: sc-=2; rs.append("⚠️ 正乖離過大")
    if data['收盤價'] < data['20MA']: sc-=2; rs.append("⚠️ 跌破月線支撐")
    if eps_f < 0: sc-=1; rs.append("⚠️ 基本面虧損")

    return sc, rs

def analyze_today(df, ticker_number, inst_data=None):
    if df is None or len(df) < 5: return None
    t, p, p5 = df.iloc[-1], df.iloc[-2], df.iloc[-5]
    fund = get_fundamental_and_industry_data(ticker_number, round(t['Close'], 2))
    
    try:
        t_open, t_close, t_high, t_low = float(t['Open']), float(t['Close']), float(t['High']), float(t['Low'])
        p_open, p_close = float(p['Open']), float(p['Close'])
        
        red_mask = (df['Open'].shift(1) > df['Close'].shift(1)) & (df['Close'] > df['Open']) & (df['Close'] > df['Open'].shift(1)) & (df['Open'] < df['Close'].shift(1))
        black_mask = (df['Close'].shift(1) > df['Open'].shift(1)) & (df['Open'] > df['Close']) & (df['Open'] > df['Close'].shift(1)) & (df['Close'] < df['Open'].shift(1))
        
        is_red_engulfing = bool(red_mask.iloc[-1])
        is_black_engulfing = bool(black_mask.iloc[-1])
        recent_7_red = bool(red_mask.tail(7).any())
        
        total_range = t_high - t_low
        if total_range == 0: total_range = 0.001
        upper_shadow = t_high - max(t_open, t_close)
        lower_shadow = min(t_open, t_close) - t_low
        body = abs(t_close - t_open)

        is_support_pullback = (lower_shadow > body * 1.5) and (lower_shadow / total_range > 0.4) and (t_low < p_close) and (t_close >= min(p_open, p_close))
        ma_resistance = min(t['5MA'], t['10MA']) 
        is_resistance_rejection = (upper_shadow > body * 1.5) and (upper_shadow / total_range > 0.4) and (t_high >= ma_resistance) and (t_close < ma_resistance)
    except:
        is_red_engulfing, is_black_engulfing, recent_7_red = False, False, False
        is_support_pullback, is_resistance_rejection = False, False
        
    try:
        ma5_deduction_tmr = float(df['Close'].iloc[-5]) if len(df) >= 5 else float(t_close)
        ma60_deduction_tmr = float(df['Close'].iloc[-60]) if len(df) >= 60 else float(t_close)
        is_ma5_turning_up = t_close > ma5_deduction_tmr
        is_ma60_turning_up = t_close > ma60_deduction_tmr
    except:
        is_ma5_turning_up, is_ma60_turning_up = False, False

    data = {
        "代號": ticker_number, "名稱": get_stock_name(ticker_number), "ticker_raw": ticker_number,
        "產業": fund['Industry'], "昨日收盤價": round(p_close, 2), "收盤價": round(t_close, 2), 
        "漲跌": round(t_close - p_close, 2), "漲跌幅": round((t_close - p_close) / p_close * 100, 2), 
        "近5日漲幅(%)": f"{round((t_close - p5['Close'])/p5['Close']*100, 2)}%",
        "成交量": int(t['Volume']/1000), "5日均量": int(df['Volume'].tail(5).mean()/1000),
        "5MA": round(t['5MA'], 2), "10MA": round(t['10MA'], 2), "20MA": round(t['20MA'], 2),
        "60MA": round(t['60MA'], 2),
        "BB_UP": round(t['BB_UP'], 2), "BB_DN": round(t['BB_DN'], 2), "BIAS": round(t['BIAS_20'], 2),
        "MACD": round(t['MACD'], 2), "MACD柱": round(t['MACD_Hist'], 3), "前日MACD柱": round(p['MACD_Hist'], 3),
        "K": round(t['K'], 2), "D": round(t['D'], 2), "J值": round(t['J'], 2),
        "訊號": (t_close > t['20MA']) and (t_close < t['5MA']) and (t['J'] < 20),
        "紅吞": is_red_engulfing, "黑吞": is_black_engulfing,
        "近七日紅吞": recent_7_red,
        "回測有撐": is_support_pullback,
        "反彈遇壓": is_resistance_rejection,
        "5日線即將上彎": is_ma5_turning_up,
        "季線即將上彎": is_ma60_turning_up
    }
    
    sc, rs = get_decision_score(data, fund, inst_data)
    data['Score'] = sc
    data['Reasons'] = rs
    data['評級'] = "🟢 S級" if sc >= 5 else ("🟡 A級" if sc >= 2 else "⚪ 觀望")
    
    return data

# 🚀 快速抓取個股單獨評級 (用於側邊欄)
@st.cache_data(ttl=180, show_spinner=False)
def get_stock_rating_fast(ticker):
    try:
        df = get_stock_data(ticker)
        if df is not None and len(df) >= 5:
            data = analyze_today(df, ticker, inst_data=None)
            if data: return data.get('評級', "⚪ 觀望")
    except: pass
    return "⚪ 觀望"

st.sidebar.title("⭐ 我的自選群組")

MAX_GROUPS = 5
current_group_count = len(st.session_state.fav_groups)

if current_group_count < MAX_GROUPS:
    with st.sidebar.expander("➕ 新增個人化群組", expanded=False):
        new_g_name = st.text_input("群組名稱", placeholder="輸入群組名稱...", label_visibility="collapsed")
        if st.button("建立", use_container_width=True) and new_g_name:
            if new_g_name not in st.session_state.fav_groups:
                st.session_state.fav_groups[new_g_name] = []
                save_json(FAV_GROUPS_FILE, st.session_state.fav_groups)
                st.rerun()
else:
    st.sidebar.info(f"已達群組數量上限 ({MAX_GROUPS} 個)。")

for g_name, g_stocks in list(st.session_state.fav_groups.items()):
    with st.sidebar.expander(f"📁 {g_name} ({len(g_stocks)})", expanded=True):
        col_rn, col_sv, col_del = st.columns([5, 2, 2])
        new_g_name_input = col_rn.text_input("重命名", value=g_name, key=f"rn_{g_name}", label_visibility="collapsed")
        
        if col_sv.button("💾", key=f"sv_{g_name}", help="儲存新群組名稱"):
            if new_g_name_input and new_g_name_input != g_name and new_g_name_input not in st.session_state.fav_groups:
                new_dict = {}
                for k, v in st.session_state.fav_groups.items():
                    if k == g_name: new_dict[new_g_name_input] = v
                    else: new_dict[k] = v
                st.session_state.fav_groups = new_dict
                save_json(FAV_GROUPS_FILE, st.session_state.fav_groups)
                st.rerun()
                
        if col_del.button("🗑️", key=f"del_{g_name}", help="刪除此群組"):
            if len(st.session_state.fav_groups) > 1:
                del st.session_state.fav_groups[g_name]
                save_json(FAV_GROUPS_FILE, st.session_state.fav_groups)
                st.rerun()
            else:
                st.error("至少需保留一個群組！")
                
        for fav in g_stocks:
            # 🚀 側邊欄：單獨顯示評級字眼
            fav_rating = get_stock_rating_fast(fav)
            if st.button(f"📊 {fav} {get_stock_name(fav)} | {fav_rating}", key=f"go_{g_name}_{fav}", use_container_width=True):
                st.session_state.update({"current_stock": fav, "page": "analysis", "date_offset": 0})
                st.rerun()

st.sidebar.divider()
st.sidebar.title("⚙️ 雷達池設定")
if st.sidebar.button("🔄 更新熱門雷達池 (Top 100)", use_container_width=True):
    st.session_state.custom_pool = fetch_twse_top_100()
    save_json(POOL_FILE, st.session_state.custom_pool)
    st.sidebar.success("✅ 雷達池已擴大更新為全台前 100 檔！")
    st.rerun()

@st.cache_data(ttl=5, show_spinner=False) 
def get_twii_quote():
    tz_tpe = timezone(timedelta(hours=8))
    update_time_str = datetime.now(tz_tpe).strftime('%Y/%m/%d %H:%M:%S')
    fallback_curr, fallback_change = 0, 0
    try:
        df = yf.Ticker("^TWII").history(period="1mo").dropna(subset=['Close'])
        if not df.empty and len(df) >= 2:
            fallback_curr = float(df['Close'].iloc[-1])
            fallback_change = float(df['Close'].iloc[-1] - df['Close'].iloc[-2])
    except: pass
    try:
        session = requests.Session()
        session.get("https://mis.twse.com.tw/stock/index.jsp", headers={"User-Agent": "Mozilla/5.0"}, timeout=3)
        ts = int(datetime.now(tz_tpe).timestamp() * 1000)
        url = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_t00.tw&json=1&delay=0&_={ts}"
        res = session.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=3)
        if res.status_code == 200:
            data = res.json()
            if 'msgArray' in data and len(data['msgArray']) > 0:
                info = data['msgArray'][0]
                z, y, d, t = info.get('z'), info.get('y'), info.get('d'), info.get('t')
                curr = float(z.replace(',','')) if z and z != '-' else (float(y.replace(',','')) if y and y != '-' else 0)
                prev = float(y.replace(',','')) if y and y != '-' else curr
                if curr > 10000:
                    if d and t: update_time_str = f"{d[:4]}/{d[4:6]}/{d[6:]} {t}"
                    return curr, curr - prev, update_time_str
    except: pass
    if fallback_curr > 10000: return fallback_curr, fallback_change, update_time_str
    return 0, 0, "無資料"

FUTURES_CACHE_FILE = "wtx_cache.json"

@st.cache_data(ttl=5, show_spinner=False)
def get_futures_quote():
    tz_tpe = timezone(timedelta(hours=8))
    dt_str = datetime.now(tz_tpe).strftime('%Y/%m/%d %H:%M:%S')
    success = False
    curr, prev = 0.0, 0.0
    
    if not success:
        try:
            res = requests.get("https://histock.tw/index/FITX", headers={'User-Agent': 'Mozilla/5.0'}, timeout=3)
            soup = BeautifulSoup(res.text, 'html.parser')
            curr_tag = soup.find(id="Price1_lbTPrice")
            prev_tag = soup.find(id="Price1_lbYPrice")
            if curr_tag and prev_tag:
                curr = float(curr_tag.text.replace(',', ''))
                prev = float(prev_tag.text.replace(',', ''))
                if curr > 10000:
                    success = True
        except: pass

    if not success:
        try:
            headers = {'User-Agent': 'Mozilla/5.0'}
            res = requests.get("https://tw.stock.yahoo.com/quote/FITXAMP", headers=headers, timeout=3)
            curr_m = re.search(r'"regularMarketPrice"\s*:\s*(?:\{[^}]*"raw"\s*:\s*)?([\d\.]+)', res.text)
            prev_m = re.search(r'"regularMarketPreviousClose"\s*:\s*(?:\{[^}]*"raw"\s*:\s*)?([\d\.]+)', res.text)
            if curr_m and prev_m:
                curr = float(curr_m.group(1))
                prev = float(prev_m.group(1))
                if curr > 10000:
                    success = True
        except: pass

    if not success:
        try:
            headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.wantgoo.com/', 'X-Requested-With': 'XMLHttpRequest'}
            res = requests.get("https://www.wantgoo.com/global/api/quotes/wtmf", headers=headers, timeout=3)
            if res.status_code == 200:
                data = res.json()
                curr = float(data.get('lastPrice', 0))
                prev = float(data.get('previousClose', 0))
                if prev == 0 and data.get('change'): 
                    prev = curr - float(data['change'])
                if curr > 10000:
                    success = True
        except: pass

    if not success:
        try:
            df = yf.Ticker("WTX=F").history(period="1mo").dropna(subset=['Close'])
            if not df.empty and len(df) >= 2:
                df = df.sort_index(ascending=True)
                curr = float(df['Close'].iloc[-1])
                prev = float(df['Close'].iloc[-2])
                if curr > 10000:
                    success = True
        except: pass

    if success:
        try:
            with open(FUTURES_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump({"curr": curr, "change": curr - prev, "prev": prev, "time": dt_str}, f)
        except: pass
        return curr, curr - prev, prev, dt_str
    else:
        try:
            if os.path.exists(FUTURES_CACHE_FILE):
                with open(FUTURES_CACHE_FILE, "r", encoding="utf-8") as f:
                    cache_data = json.load(f)
                    return cache_data["curr"], cache_data["change"], cache_data["prev"], cache_data["time"] + " (快取)"
        except: pass

    return 0, 0, 0, "暫無報價"

@st.cache_data(ttl=5, show_spinner=False)
def get_stock_live_time(ticker):
    base_ticker = str(ticker).strip().upper().replace(".TW", "").replace(".TWO", "")
    tz_tpe = timezone(timedelta(hours=8))
    try:
        url = f"https://ws.cnyes.com/charting/api/v1/TWS:{base_ticker}:STOCK/quote"
        res = requests.get(url, timeout=3)
        if res.status_code == 200:
            ts = int(res.json()['data']['quote']['20'])
            return datetime.fromtimestamp(ts, tz_tpe).strftime('%Y/%m/%d %H:%M:%S')
    except: pass
    return datetime.now(tz_tpe).strftime('%Y/%m/%d %H:%M:%S')

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_twse_index_history():
    try:
        df = yf.Ticker("^TWII").history(period="1y")
        if not df.empty:
            df.index = pd.to_datetime(df.index.strftime('%Y-%m-%d'))
            return df[['Open', 'High', 'Low', 'Close', 'Volume']]
    except: pass
    return None

@st.cache_data(ttl=3600, show_spinner=False)
def get_institutional_trading(ticker):
    try:
        start_date = (datetime.now() - timedelta(days=20)).strftime('%Y-%m-%d')
        url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInstitutionalInvestorsBuySell&data_id={ticker}&start_date={start_date}"
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            data = res.json()
            if data.get('msg') == 'success' and len(data.get('data', [])) > 0:
                df = pd.DataFrame(data['data'])
                df['net'] = (df['buy'] - df['sell']) / 1000  
                df['type'] = '其他'
                df.loc[df['name'].str.contains('Foreign|外資', case=False, na=False), 'type'] = '外資'
                df.loc[df['name'].str.contains('Trust|投信', case=False, na=False), 'type'] = '投信'
                df.loc[df['name'].str.contains('Dealer|自營', case=False, na=False), 'type'] = '自營商'
                pivot = df.groupby(['date', 'type'])['net'].sum().unstack(fill_value=0).reset_index()
                for col in ['外資', '投信', '自營商']:
                    if col not in pivot.columns: pivot[col] = 0
                pivot['單日合計'] = pivot['外資'] + pivot['投信'] + pivot['自營商']
                pivot = pivot.sort_values('date', ascending=False).head(10)
                res_list = []
                for _, row in pivot.iterrows():
                    res_list.append({
                        "日期": row['date'][-5:].replace("-", "/"),
                        "外資(張)": int(row['外資']), "投信(張)": int(row['投信']),
                        "自營商(張)": int(row['自營商']), "單日合計(張)": int(row['單日合計'])
                    })
                if res_list: return res_list
    except: pass
    return []

MACRO_CACHE_FILE = "macro_cache.json"

@st.cache_data(ttl=600, show_spinner=False)
def get_global_macro_data():
    tz_tpe = timezone(timedelta(hours=8))
    fetch_time = datetime.now(tz_tpe).strftime('%Y/%m/%d %H:%M:%S')
    data = {"global_time": fetch_time}
    tickers = {
        "^SOX": "https://finance.yahoo.com/quote/^SOX",
        "^VIX": "https://finance.yahoo.com/quote/^VIX",
        "JPY=X": "https://finance.yahoo.com/quote/JPY=X"
    }
    
    latest_ts = 0
    latest_time_str = "無資料"
    success_count = 0
    
    for t, url in tickers.items():
        try:
            df = yf.Ticker(t).history(period="1mo")
            if df is not None and not df.empty:
                df = df.dropna(subset=['Close'])
                if len(df) >= 2:
                    c = float(df['Close'].iloc[-1])
                    p = float(df['Close'].iloc[-2])
                    last_dt = df.index[-1]
                    time_str = last_dt.strftime('%Y/%m/%d')
                    
                    data[t] = {"price": c, "pct": (c-p)/p*100 if p != 0 else 0, "time": time_str, "url": url}
                    success_count += 1
                    
                    if last_dt.timestamp() > latest_ts:
                        latest_ts = last_dt.timestamp()
                        latest_time_str = time_str
                    continue
        except: pass
        data[t] = {"price": 0, "pct": 0, "time": "暫無資料", "url": url}
            
    data['global_time'] = latest_time_str

    if success_count > 0:
        try:
            with open(MACRO_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except: pass
        return data
    else:
        try:
            if os.path.exists(MACRO_CACHE_FILE):
                with open(MACRO_CACHE_FILE, "r", encoding="utf-8") as f:
                    cache_data = json.load(f)
                    cache_data['global_time'] = cache_data.get('global_time', '') + " (快取)"
                    return cache_data
        except: pass

    return data

def generate_comprehensive_analysis(data, inst_data, sc, market_today="", market_tmr=""):
    analysis_bullets = []
    if market_today and market_tmr:
        market_today_clean = market_today.replace("🔥 ", "").replace("⚠️ ", "").replace("💪 ", "").replace("🩸 ", "").replace("📈 ", "").replace("📉 ", "").replace("⚖️ ", "")
        market_tmr_clean = market_tmr.replace("🚀 ", "").replace("⚠️ ", "").replace("📈 ", "").replace("📉 ", "").replace("⚖️ ", "")
        if "多" in market_tmr_clean or "高" in market_tmr_clean: analysis_bullets.append(f"🔥 <span style='color:#ff3333; font-weight:bold;'>大盤盤勢導航：今日【{market_today_clean}】，預測次一交易日【{market_tmr_clean}】，大環境偏多有利個股發揮。</span>")
        elif "空" in market_tmr_clean or "低" in market_tmr_clean: analysis_bullets.append(f"⚠️ <span style='color:#00cc00;'>大盤盤勢導航：今日【{market_today_clean}】，預測次一交易日【{market_tmr_clean}】，大環境不佳需防範系統性風險。</span>")
        else: analysis_bullets.append(f"⚪ <b>大盤盤勢導航</b>：今日【{market_today_clean}】，預測次一交易日【{market_tmr_clean}】，大環境震盪，個股表現分歧。")

    t_short = data['收盤價'] > data['5MA']
    t_mid = data['收盤價'] > data['20MA']
    t_long = data['收盤價'] > data['60MA']
    if t_short and t_mid and t_long: analysis_bullets.append("🔥 <span style='color:#ff3333; font-weight:bold;'>三級多空趨勢：短、中、長線（5T, 20T, 60T）呈現完全多頭排列。</span>")
    elif not t_short and not t_mid and not t_long: analysis_bullets.append("⚠️ <span style='color:#00cc00;'>三級多空趨勢：短、中、長線皆呈現空頭排列，防範中線續跌。</span>")

    if data.get('紅吞'): analysis_bullets.append(f"🔥 <span style='color:#ff3333; font-weight:bold;'>型態反轉：今日出現「紅吞（多頭吞噬）」K線型態，強烈見底買進訊號。</span>")
    elif data.get('近七日紅吞'): analysis_bullets.append(f"🔥 <span style='color:#ff3333; font-weight:bold;'>底部表態：近七日內曾出現「紅吞」型態，多方主力已在此區間建倉表態。</span>")
    elif data.get('黑吞'): analysis_bullets.append(f"⚠️ <span style='color:#00cc00;'><b>型態反轉：出現「黑吞（空頭吞噬）」K線型態，強烈高檔反轉警訊。</b></span>")
    
    if data.get('回測有撐'): analysis_bullets.append(f"🔥 <span style='color:#ff3333; font-weight:bold;'>支撐確認：今日價格下殺後爆出買盤，收出長下影線，主力防守支撐強勁。</span>")
    if data.get('反彈遇壓'): analysis_bullets.append(f"⚠️ <span style='color:#00cc00;'><b>均線壓力：今日反彈遭遇均線壓力被打回，收長上影線，空方壓制強烈。</b></span>")
    
    if data['J值'] < 20: analysis_bullets.append(f"🔥 <span style='color:#ff3333; font-weight:bold;'>KDJ 極度超賣：J 值來到 ({data['J值']})，隨時醞釀強力技術性反彈。</span>")
    elif data['J值'] > 80: analysis_bullets.append(f"⚠️ <span style='color:#00cc00;'><b>KDJ 高檔過熱</b>：J 值高達 {data['J值']}，短線過熱步入超買區。</span>")
    if data['K'] > data['D']: analysis_bullets.append(f"🔥 <span style='color:#ff3333; font-weight:bold;'>KDJ 黃金交叉：K值 大於 D值，指標呈現多頭向上發散。</span>")
    else: analysis_bullets.append(f"⚠️ <span style='color:#00cc00;'><b>KDJ 死亡交叉</b>：K值 小於 D值，短線動能偏弱。</span>")

    if data['收盤價'] <= data['BB_DN'] * 1.02: analysis_bullets.append(f"🔥 <span style='color:#ff3333; font-weight:bold;'>觸及布林下軌：股價貼近布林下軌 ({data['BB_DN']})，具備極強的技術性支撐。</span>")
    elif data['收盤價'] >= data['BB_UP'] * 0.98: analysis_bullets.append(f"⚠️ <span style='color:#00cc00;'><b>觸及布林上軌</b>：股價貼近布林上軌 ({data['BB_UP']})，易遇壓力回檔。</span>")

    if data['BIAS'] < -5: analysis_bullets.append(f"🔥 <span style='color:#ff3333; font-weight:bold;'>負乖離過大：月線乖離率達 ({data['BIAS']}%)，超跌反彈機率極高。</span>")
    elif data['BIAS'] > 7: analysis_bullets.append(f"⚠️ <span style='color:#00cc00;'><b>正乖離過大</b>：月線乖離率達 ({data['BIAS']}%)，追高風險劇增。</span>")

    if data['收盤價'] >= data['5MA'] and data.get('5日線即將上彎'):
        analysis_bullets.append(f"🔥 <span style='color:#ff3333; font-weight:bold;'>扣低值支撐：未來5日線即將扣低值，均線將持續翻揚向上，提供短線強大保護力。</span>")
    elif data['收盤價'] < data['5MA'] and not data.get('5日線即將上彎'):
        analysis_bullets.append(f"⚠️ <span style='color:#00cc00;'><b>扣高值壓力：未來5日線即將扣高值，均線容易下彎形成蓋頭壓力，反彈應防範回檔。</b></span>")

    if data['成交量'] > data['5日均量'] * 1.1 and data.get('漲跌',0) > 0: analysis_bullets.append(f"🔥 <span style='color:#ff3333; font-weight:bold;'>量價確認：今日量能放大大於5日均量，主力進場點火信號明確。</span>")
    elif data['成交量'] < data['5日均量']: analysis_bullets.append(f"⚠️ <span style='color:#00cc00;'>量能警訊：反彈或震盪中「量能萎縮」，需防範缺乏買盤支撐的虛假反彈。</span>")
        
    if data['MACD柱'] > data['前日MACD柱']: analysis_bullets.append(f"🔥 <span style='color:#ff3333; font-weight:bold;'>動能指標護航：MACD 綠柱開始收斂或紅柱發散，下跌動能衰退，反彈格局成形。</span>")
    else: analysis_bullets.append(f"⚠️ <span style='color:#00cc00;'>波段動能不佳：MACD 空方動能尚未停歇，此時反彈極易遇蓋頭賣壓。</span>")

    if inst_data and len(inst_data) >= 3:
        foreign_net = sum([int(str(x['外資(張)']).replace(',', '')) for x in inst_data[:3] if str(x['外資(張)']).replace(',', '').lstrip('-').isdigit()])
        trust_net = sum([int(str(x['投信(張)']).replace(',', '')) for x in inst_data[:3] if str(x['投信(張)']).replace(',', '').lstrip('-').isdigit()])
        chip_status = "⚪ <b>法人籌碼動向 (近3日)</b>："
        if foreign_net > 0: chip_status += f"🔥 <span style='color:#ff3333; font-weight:bold;'>外資偏多 (買超 {foreign_net} 張)</span>；"
        else: chip_status += f"⚠️ <span style='color:#00cc00;'>外資調節 (賣超 {abs(foreign_net)} 張)</span>；"
        if trust_net > 0: chip_status += f"🔥 <span style='color:#ff3333; font-weight:bold;'>投信力挺 (買超 {trust_net} 張)。</span>"
        else: chip_status += f"⚠️ <span style='color:#00cc00;'>投信減碼 (賣超 {abs(trust_net)} 張)。</span>"
        analysis_bullets.append(chip_status)

    if sc >= 5: 
        v_t, v_c = "🟢 S級買點：強烈建議佈局", "#00cc00"
        v_a = f"✅ <b>進場判斷：強烈買進</b><br>動能、量能、型態三道關卡全數確認過關！<br>📌 建議建倉區間：{data['BB_DN']:.2f} ~ {data['20MA']:.2f} 之間分批加碼。"
    elif sc >= 2: 
        v_t, v_c = "🟡 A級機會：偏多試單", "#ffcc00"
        v_a = f"✅ <b>進場判斷：分批試單</b><br>滿足跌深超賣條件，動能防護及格。<br>📌 建議短線建倉點：{data['收盤價']:.2f} 附近，跌破 {data['BB_DN']:.2f} 嚴格停損。"
    elif sc >= -1: 
        v_t, v_c = "⚪ 中性觀望：多空不明", "#888888"
        v_a = f"⏳ <b>進場判斷：暫緩進場</b><br>多空拉扯劇烈，或動能尚在向下延伸，建議靜待訊號明朗化。"
    else: 
        v_t, v_c = "🔴 極度危險：嚴禁做多", "#ff3333"
        v_a = f"⛔ <b>進場判斷：絕對空手</b><br>量能、動能完全走空。強烈建議空手觀望，切勿拿資金接落下的飛刀。"
    return analysis_bullets, v_t, v_c, v_a

def draw_professional_chart(df, ticker_name, latest_price, view_days, is_light_mode, show_buy_signal=False, f_data=None, show_sup_res=False, show_signals=True):
    df_view = df.tail(view_days)
    colors = ['#ff3333' if row['Close'] >= row['Open'] else '#00cc00' for _, row in df_view.iterrows()]
    x_vals = df_view.index.strftime('%Y-%m-%d')
    fig = make_subplots(rows=4, cols=1, shared_xaxes=True, row_heights=[0.45, 0.15, 0.15, 0.25], vertical_spacing=0.06)
    line_k, line_d, line_j = ("#0066cc", "#ff9900", "#9900cc") if is_light_mode else ("white", "yellow", "magenta")
    grid_c = "rgba(0,0,0,0.1)" if is_light_mode else "rgba(255,255,255,0.1)"
    bg_c = "#ffffff" if is_light_mode else "#0e1117"
    text_c = "#333" if is_light_mode else "#ccc"
    
    # 🚀 顯示 5T/10T/20T 的終極解決方案：不再使用 customdata，直接透過空白 name 來迴避衝突
    fig.add_trace(go.Candlestick(x=x_vals, open=df_view['Open'], high=df_view['High'], low=df_view['Low'], close=df_view['Close'], increasing_line_color='#ff3333', decreasing_line_color='#00cc00', name="K線"), row=1, col=1)
    
    # 直接加入均線並賦予 hovertemplate，不使用 hoverinfo="skip"
    fig.add_trace(go.Scatter(x=x_vals, y=df_view['5MA'], line=dict(color='orange', width=2), name="5T", hovertemplate="%{y:.2f}<extra></extra>"), row=1, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df_view['10MA'], line=dict(color='#ffcc00', width=2), name="10T", hovertemplate="%{y:.2f}<extra></extra>"), row=1, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df_view['20MA'], line=dict(color='cyan', width=2), name="20T", hovertemplate="%{y:.2f}<extra></extra>"), row=1, col=1)
    
    # 第二重保險：建立一個與 Close 價重疊的隱形參考點，強制將所有 MA 資料印在它的文字框上
    custom_text = [f"5T: {row['5MA']:.2f}<br>10T: {row['10MA']:.2f}<br>20T: {row['20MA']:.2f}" for _, row in df_view.iterrows()]
    fig.add_trace(go.Scatter(x=x_vals, y=df_view['Close'], mode='markers', marker=dict(color='rgba(0,0,0,0)'), name="均線數值", hovertemplate="%{text}<extra></extra>", text=custom_text, showlegend=False), row=1, col=1)

    fig.add_hline(y=latest_price, line_dash="dash", line_color="#ffcc00", row=1, col=1,
                  annotation_text=f" 🎯 最新報價: {latest_price:.2f} ",
                  annotation_position="top right",
                  annotation_font=dict(color="#ffcc00", size=15, weight="bold"))
    
    if show_sup_res:
        highest_price = df_view['High'].max()
        lowest_price = df_view['Low'].min()
        fig.add_hline(y=highest_price, line_dash="dash", line_color="#ff3333", row=1, col=1, annotation_text=f"壓力 {highest_price:.2f}", annotation_position="top left", annotation_font=dict(size=12, color="#ff3333"))
        fig.add_hline(y=lowest_price, line_dash="dash", line_color="#00cc00", row=1, col=1, annotation_text=f"支撐 {lowest_price:.2f}", annotation_position="bottom left", annotation_font=dict(size=12, color="#00cc00"))
    
    re_x, re_y, re_text = [], [], []
    be_x, be_y, be_text = [], [], []
    sup_x, sup_y, sup_text = [], [], []
    res_x, res_y, res_text = [], [], []
    deduct_up_x, deduct_up_y, deduct_up_text = [], [], []
    deduct_down_x, deduct_down_y, deduct_down_text = [], [], []
    
    start_pos = len(df) - len(df_view)
    
    for i, date in enumerate(df_view.index):
        pos = start_pos + i
        if pos >= 1:
            t = df.iloc[pos]
            p = df.iloc[pos-1]
            
            t_open, t_close, t_high, t_low = t['Open'], t['Close'], t['High'], t['Low']
            p_open, p_close = p['Open'], p['Close']
            
            is_red = (p_open > p_close) and (t_close > t_open) and (t_close > p_open) and (t_open < p_close)
            is_black = (p_close > p_open) and (t_open > t_close) and (t_open > p_close) and (t_close < p_open)
            
            # 階梯式四層圖層分佈：第一層 (吞)
            if is_red:
                re_x.append(date.strftime('%Y-%m-%d'))
                re_y.append(t_low * 0.98) 
                re_text.append("<b>吞</b>")
            if is_black:
                be_x.append(date.strftime('%Y-%m-%d'))
                be_y.append(t_high * 1.02) 
                be_text.append("<b>吞</b>")
            
            total_range = t_high - t_low
            if total_range == 0: total_range = 0.001
            upper_shadow = t_high - max(t_open, t_close)
            lower_shadow = min(t_open, t_close) - t_low
            body = abs(t_close - t_open)

            is_support_pullback = (lower_shadow > body * 1.5) and (lower_shadow / total_range > 0.4) and (t_low < p_close) and (t_close >= min(p_open, p_close))
            ma_resistance = min(t['5MA'], t['10MA']) 
            is_resistance_rejection = (upper_shadow > body * 1.5) and (upper_shadow / total_range > 0.4) and (t_high >= ma_resistance) and (t_close < ma_resistance)

            # 階梯式四層圖層分佈：第二層 (撐/壓)
            if is_support_pullback:
                sup_x.append(date.strftime('%Y-%m-%d'))
                sup_y.append(t_low * 0.95) 
                sup_text.append("<b>撐</b>")
            if is_resistance_rejection:
                res_x.append(date.strftime('%Y-%m-%d'))
                res_y.append(t_high * 1.05) 
                res_text.append("<b>壓</b>")

            if pos >= 5:
                curr_deduct_5 = df.iloc[pos - 5]['Close']
                curr_5_up = (t_close >= t['5MA']) and (t_close > curr_deduct_5)
                curr_down_5 = (t_close < t['5MA']) and (t_close < curr_deduct_5)
                
                prev_5_up = False
                prev_down_5 = False
                if pos >= 6:
                    prev_deduct_5 = df.iloc[pos - 6]['Close']
                    prev_5_up = (p_close >= p['5MA']) and (p_close > prev_deduct_5)
                    prev_down_5 = (p_close < p['5MA']) and (p_close < prev_deduct_5)
                
                # 階梯式四層圖層分佈：第四層 (扣抵轉折 ↗️ ↘️)
                if curr_5_up and not prev_5_up:
                    deduct_up_x.append(date.strftime('%Y-%m-%d'))
                    deduct_up_y.append(t_low * 0.85) 
                    deduct_up_text.append("<b>↗️</b>")
                
                if curr_down_5 and not prev_down_5:
                    deduct_down_x.append(date.strftime('%Y-%m-%d'))
                    deduct_down_y.append(t_high * 1.15)
                    deduct_down_text.append("<b>↘️</b>")

    if show_signals:
        if re_x: fig.add_trace(go.Scatter(x=re_x, y=re_y, mode='text', text=re_text, textposition="bottom center", textfont=dict(color="#ff3333", size=13), name="紅吞", hoverinfo='skip'), row=1, col=1)
        if be_x: fig.add_trace(go.Scatter(x=be_x, y=be_y, mode='text', text=be_text, textposition="top center", textfont=dict(color="#00cc00", size=13), name="黑吞", hoverinfo='skip'), row=1, col=1)
        if sup_x: fig.add_trace(go.Scatter(x=sup_x, y=sup_y, mode='text', text=sup_text, textposition="bottom center", textfont=dict(color="#ff9900" if is_light_mode else "#ffcc00", size=13), name="回測有撐", hoverinfo='skip'), row=1, col=1)
        if res_x: fig.add_trace(go.Scatter(x=res_x, y=res_y, mode='text', text=res_text, textposition="top center", textfont=dict(color="#0066cc" if is_light_mode else "#00ccff", size=13), name="反彈遇壓", hoverinfo='skip'), row=1, col=1)
        if deduct_up_x: fig.add_trace(go.Scatter(x=deduct_up_x, y=deduct_up_y, mode='text', text=deduct_up_text, textposition="bottom center", textfont=dict(color="#ff3333", size=13), name="扣低上彎", hoverinfo='skip'), row=1, col=1)
        if deduct_down_x: fig.add_trace(go.Scatter(x=deduct_down_x, y=deduct_down_y, mode='text', text=deduct_down_text, textposition="top center", textfont=dict(color="#00cc00", size=13), name="扣高下彎", hoverinfo='skip'), row=1, col=1)

    if show_buy_signal and f_data:
        buy_x, buy_y, buy_text = [], [], []
        for i in range(len(df_view)):
            current_date = df_view.index[i]
            pos = df.index.get_loc(current_date)
            sub_df = df.iloc[:pos+1]
            if len(sub_df) >= 5:
                t_data = analyze_today(sub_df, ticker_name, inst_data=None) 
                if t_data and t_data['Score'] >= 2:
                    buy_x.append(current_date.strftime('%Y-%m-%d'))
                    # 階梯式四層圖層分佈：第三層 (買訊)，與撐、↗️ 完全錯開
                    buy_y.append(df_view['Low'].iloc[i] * 0.90) 
                    buy_text.append("買")
        if buy_x:
            fig.add_trace(go.Scatter(x=buy_x, y=buy_y, mode='markers+text', marker=dict(symbol='triangle-up', size=14, color='#00ffcc' if not is_light_mode else '#0066cc'), text=buy_text, textposition="bottom center", textfont=dict(color="#00ffcc" if not is_light_mode else '#0066cc', size=11, weight="bold"), name="買進訊號", hoverinfo='skip'), row=1, col=1)
            
    fig.add_trace(go.Bar(x=x_vals, y=df_view['Volume'], marker_color=colors, name="VOL", hovertemplate="%{y:,.0f}"), row=2, col=1)
    macd_colors = ['#ff3333' if val > 0 else '#00cc00' for val in df_view['MACD_Hist']]
    fig.add_trace(go.Bar(x=x_vals, y=df_view['MACD_Hist'], marker_color=macd_colors, name="OSC", hovertemplate="%{y:.3f}"), row=3, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df_view['MACD'], line=dict(color=line_k, width=1.5), name="DIF", hovertemplate="%{y:.3f}"), row=3, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df_view['Signal'], line=dict(color=line_d, width=1.5), name="MACD", hovertemplate="%{y:.3f}"), row=3, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df_view['K'], line=dict(color=line_k, width=1.5), name="K", hovertemplate="%{y:.2f}"), row=4, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df_view['D'], line=dict(color=line_d, width=1.5), name="D", hovertemplate="%{y:.2f}"), row=4, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df_view['J'], line=dict(color=line_j, width=1.5), name="J", hovertemplate="%{y:.2f}"), row=4, col=1)

    fig.update_xaxes(type='category', nticks=15, fixedrange=True, showgrid=True, gridcolor=grid_c)
    fig.update_yaxes(fixedrange=True, showgrid=True, gridcolor=grid_c)
    
    fig.update_layout(xaxis_rangeslider_visible=False, template="plotly_white" if is_light_mode else "plotly_dark", height=850, margin=dict(l=10, r=10, t=10, b=30), paper_bgcolor=bg_c, plot_bgcolor=bg_c, hovermode='x unified', hoverlabel=dict(bgcolor=bg_c, font_size=13, font_color=text_c), dragmode=False, showlegend=False)
    fig.add_annotation(text="📊 資料來源: yfinance / TWSE / WantGoo", xref="paper", yref="paper", x=1.0, y=-0.05, showarrow=False, font=dict(size=12, color=text_c))
    return fig

def open_pred_logic(twii_df, twii_close, twii_change, twii_time_str=""):
    macro_data = get_global_macro_data()
    if twii_df is None or len(twii_df) < 2: 
        return "資料不足", "無法分析", "資料不足", "無法預測", "", "", 50, macro_data
    if twii_close > 0:
        t_close = twii_close
        p_close = twii_close - twii_change
        t_open = twii_df['Open'].iloc[-1]
    else:
        t_open, t_close, p_close = twii_df['Open'].iloc[-1], twii_df['Close'].iloc[-1], twii_df['Close'].iloc[-2]
    tz_tpe = timezone(timedelta(hours=8))
    if twii_time_str and "/" in twii_time_str:
        last_dt_str = twii_time_str.split(" ")[0]
        try: last_dt = datetime.strptime(last_dt_str, '%Y/%m/%d')
        except: last_dt = datetime.now(tz_tpe)
    else:
        last_dt = datetime.now(tz_tpe)
        last_dt_str = last_dt.strftime('%Y/%m/%d')
    next_dt = last_dt + timedelta(days=1)
    TW_MARKET_HOLIDAYS = {"2026/01/01", "2026/02/16", "2026/02/17", "2026/02/18", "2026/02/19", "2026/02/20", "2026/02/23", "2026/02/27", "2026/04/02", "2026/04/03", "2026/05/01", "2026/06/19", "2026/09/25", "2026/10/09"}
    while True:
        if next_dt.weekday() >= 5: 
            next_dt += timedelta(days=1)
            continue
        if next_dt.strftime('%Y/%m/%d') in TW_MARKET_HOLIDAYS: 
            next_dt += timedelta(days=1)
            continue
        break
    next_dt_str = next_dt.strftime('%Y/%m/%d')
    
    today_title, today_desc = "⚖️ 平盤震盪", "大盤開在平盤附近，法人現貨買賣超多空拉扯，量價關係呈現縮量，盤勢陷入震盪整理。"
    if t_open > p_close * 1.003:
        if t_close > t_open: today_title, today_desc = "🔥 開高走高", "大盤受外資買盤與美股溢價激勵跳空開高，配合融資餘額增加與量能放大，盤勢極度偏多。"
        else: today_title, today_desc = "⚠️ 開高走低", "大盤跳空開高後遭遇短線獲利了結賣壓，動能指標有進入超買區疑慮，呈現高檔回落。"
    elif t_open < p_close * 0.997:
        if t_close > t_open: today_title, today_desc = "💪 開低走高", "大盤受國際盤回檔影響開低，但低檔投信承接買盤強勁，出現開低走高收紅K型態。"
        else: today_title, today_desc = "🩸 開低走低", "大盤弱勢開低，恐慌指數上升引發散戶多殺多停損賣壓，盤勢極度偏空。"
    else:
        if t_close > p_close * 1.003: today_title, today_desc = "📈 平盤走高", "大盤開平盤附近，隨後受權值股買盤帶動，均線乖離擴大，多方發力穩步墊高。"
        elif t_close < p_close * 0.997: today_title, today_desc = "📉 平盤走低", "大盤開平盤附近，但缺乏主力買盤支撐，資金動能不足導致震盪向下。"

    risk_score = 50 
    ma5 = twii_df['5MA'].iloc[-1] if '5MA' in twii_df.columns else twii_df['Close'].tail(5).mean()
    if t_close < ma5: risk_score += 15
    else: risk_score -= 10
    sox_data = macro_data.get('^SOX', {"price": 0, "pct": 0})
    sox_pct = sox_data.get('pct', 0)
    if sox_pct < -2.0: risk_score += 20
    elif sox_pct < -0.5: risk_score += 10
    elif sox_pct > 1.5: risk_score -= 15
    vix_data = macro_data.get('^VIX', {"price": 0, "pct": 0})
    vix_curr = vix_data.get('price', 0)
    vix_pct = vix_data.get('pct', 0)
    if vix_curr > 20 or vix_pct > 10.0: risk_score += 20
    elif vix_pct < -5.0: risk_score -= 10
    jpy_data = macro_data.get('JPY=X', {"price": 0, "pct": 0})
    jpy_pct = jpy_data.get('pct', 0)
    if jpy_pct < -0.8: risk_score += 15 
    elif jpy_pct > 0.5: risk_score -= 5
    
    wtx_close, _, _, _ = get_futures_quote()
    night_diff_text = ""
    if wtx_close > 0 and t_close > 0:
        night_diff = wtx_close - t_close
        if night_diff < -150:
            risk_score += 25
            night_diff_text = f"<br>⚠️ <span style='color:#00cc00;'><b>期貨逆價差過大 ({night_diff:.0f}點)</b>：夜盤重挫，開低風險劇增！</span>"
        elif night_diff < -50:
            risk_score += 10
            night_diff_text = f"<br>⚠️ <span style='color:#00cc00;'><b>期貨逆價差 ({night_diff:.0f}點)</b>：夜盤走勢偏弱。</span>"
        elif night_diff > 150:
            risk_score -= 20
            night_diff_text = f"<br>🔥 <span style='color:#ff3333;'><b>期貨強勢正價差 (+{night_diff:.0f}點)</b>：夜盤大漲，開高機率極高！</span>"
        elif night_diff > 50:
            risk_score -= 10
            night_diff_text = f"<br>🔥 <span style='color:#ff3333;'><b>期貨正價差 (+{night_diff:.0f}點)</b>：夜盤走勢偏多。</span>"

    risk_score = max(5, min(95, int(risk_score))) 
    if risk_score < 40: tmr_title, tmr_desc = "🚀 安全偏多", f"總經與期貨夜盤環境穩定，預估次一交易日 ({next_dt_str}) 有極高機率開平高盤挑戰上檔壓力。{night_diff_text}"
    elif risk_score < 70: tmr_title, tmr_desc = "⚠️ 偏空震盪", f"國際變數增加或台股跌破關鍵短均線，預防 ({next_dt_str}) 開平低盤回測下檔支撐。{night_diff_text}"
    else: tmr_title, tmr_desc = "🚨 極度警戒", f"全球宏觀風險飆高（費半重挫或夜盤大跌），強烈建議減碼防範 ({next_dt_str}) 跳空重挫的系統性風險。{night_diff_text}"
    
    return today_title, today_desc, tmr_title, tmr_desc, last_dt_str, next_dt_str, risk_score, macro_data

def render_index_board():
    try:
        twii_close, twii_change, twii_time_str = get_twii_quote()
        twii_color = '#ff3333' if twii_change >= 0 else '#00cc00'
        wtx_close, wtx_change, wtx_prev, wtx_time_str = get_futures_quote()
        wtx_color = '#ff3333' if wtx_change >= 0 else '#00cc00'
        twii_df_for_pred = get_stock_data("^TWII")
        today_title, today_desc, tmr_title, tmr_desc, last_dt_str, next_dt_str, risk_score, macro = open_pred_logic(twii_df_for_pred, twii_close, twii_change, twii_time_str)
        
        with st.container(border=True):
            col1, col2, col3 = st.columns([1, 1, 1.5])
            with col1:
                st.markdown(f"<div style='text-align: center; font-size: 1.1rem; font-weight: bold;'>台灣加權指數 🔗</div>", unsafe_allow_html=True)
                st.markdown(f"<div style='text-align: center; font-size: 2.1rem; font-weight: 900; color: {twii_color}; margin: 0;'>{twii_close:,.0f}</div>", unsafe_allow_html=True)
                st.markdown(f"<div style='text-align: center; font-size: 1.1rem; font-weight: bold; color: {twii_color};'>{'↑' if twii_change > 0 else '↓'} {abs(twii_change):.0f}</div>", unsafe_allow_html=True)
                st.markdown(f"<div style='text-align: center; font-size: 0.85rem; color: #888;'>🕒 抓取時間: {twii_time_str}</div>", unsafe_allow_html=True)
            with col2:
                st.markdown(f"<div style='text-align: center; font-size: 1.1rem; font-weight: bold;'>台指期貨 (最後收盤/夜盤) 🌙</div>", unsafe_allow_html=True)
                if wtx_close > 0:
                    st.markdown(f"<div style='text-align: center; font-size: 2.1rem; font-weight: 900; color: {wtx_color}; margin: 0;'>{wtx_close:,.0f}</div>", unsafe_allow_html=True)
                    st.markdown(f"<div style='text-align: center; font-size: 1.1rem; font-weight: bold; color: {wtx_color};'>{'↑' if wtx_change > 0 else '↓'} {abs(wtx_change):.0f}</div>", unsafe_allow_html=True)
                    
                    wtx_url = "https://tw.stock.yahoo.com/quote/FITXAMP"
                    st.markdown(f"<div style='text-align: center; font-size: 0.8rem; color: #888; margin-top:2px;'>前日收盤: {wtx_prev:,.0f} | 🕒 抓取: {wtx_time_str[-13:] if len(wtx_time_str) >= 13 else wtx_time_str}<br><a href='{wtx_url}' target='_blank' style='color:#888; text-decoration:none;'>🔗 來源: Yahoo 股市 / HiStock</a></div>", unsafe_allow_html=True)
                else:
                    st.markdown(f"<div style='text-align: center; font-size: 1.2rem; color: #888; margin-top:15px;'>暫無報價</div>", unsafe_allow_html=True)
            with col3:
                st.markdown(f"<div style='text-align: left; color: #ffcc00; font-size: 1.05rem; font-weight: bold;'>📝 盤勢分析 ({last_dt_str})</div>", unsafe_allow_html=True)
                st.markdown(f"<div style='text-align: left; font-size: 1.1rem; font-weight: bold;'>{today_title}</div>", unsafe_allow_html=True)
                st.markdown(f"<div style='text-align: left; font-size: 0.85rem; margin-top: 2px; margin-bottom: 8px; line-height: 1.4;'>{today_desc}</div>", unsafe_allow_html=True)
                st.markdown(f"<div style='text-align: left; color: #00ffcc; font-size: 1.05rem; font-weight: bold;'>🔮 次日開盤預測 ({next_dt_str})</div>", unsafe_allow_html=True)
                st.markdown(f"<div style='text-align: left; font-size: 1.1rem; font-weight: bold;'>{tmr_title}</div>", unsafe_allow_html=True)
                st.markdown(f"<div style='text-align: left; font-size: 0.85rem; margin-top: 2px; line-height: 1.4;'>{tmr_desc}</div>", unsafe_allow_html=True)
            
            if st.button("🔄 手動更新即時大盤報價", use_container_width=True): st.cache_data.clear(); st.rerun()
        
        st.markdown("<h4 style='margin-top:20px; text-align:center;'>🌍 全球總經與次日開盤風險評估</h4>", unsafe_allow_html=True)
        st.markdown(f"<div style='text-align:center; font-size:0.85rem; color:#888; margin-top:-10px; margin-bottom:10px;'>🕒 總經最後收盤時間: {macro.get('global_time', '無')}</div>", unsafe_allow_html=True)

        bar_color = "#00cc00" if risk_score < 40 else ("#ffcc00" if risk_score < 70 else "#ff3333")
        risk_label = "🟢 資金充沛，安心佈局" if risk_score < 40 else ("🟡 變數增加，控制倉位" if risk_score < 70 else "🔴 系統風險，嚴格減碼")
        st.markdown(f"<div style='text-align:center; font-size:1.1rem; font-weight:bold;'>系統量化開低風險度：<span style='color:{bar_color};'>{risk_score}%</span></div>", unsafe_allow_html=True)
        st.markdown(f"""
        <div class="risk-bar-container">
            <div class="risk-bar-fill" style="width: {risk_score}%; background-color: {bar_color};"></div>
        </div>
        <div style='text-align:center; font-size:0.9rem; color:{bar_color}; font-weight:bold; margin-bottom:15px;'>{risk_label}</div>
        """, unsafe_allow_html=True)
        
        mc1, mc2, mc3, mc4 = st.columns(4)
        sox_data = macro.get('^SOX', {"price": 0, "pct": 0, "time": "無", "url": "https://finance.yahoo.com/quote/^SOX"})
        sox_p = sox_data.get('pct', 0)
        sox_c = "#ff3333" if sox_p >= 0 else "#00cc00"
        with mc1.container(border=True):
            st.markdown(f"<div style='text-align:center; font-size:0.85rem;'>費城半導體</div><div style='text-align:center; font-size:1.1rem; font-weight:bold; color:{sox_c};'>{sox_data.get('price', 0):,.1f}<br>{'+' if sox_p>0 else ''}{sox_p:.2f}%</div><div style='text-align:center; font-size:0.75rem; color:#888; margin-top:5px;'>🕒 {sox_data.get('time', '無')}<br><a href='{sox_data.get('url', '#')}' target='_blank' style='color:#888; text-decoration:none;'>🔗 Yahoo Finance</a></div>", unsafe_allow_html=True)
        
        vix_data = macro.get('^VIX', {"price": 0, "pct": 0, "time": "無", "url": "https://finance.yahoo.com/quote/^VIX"})
        vix_p = vix_data.get('pct', 0)
        vix_c = "#00cc00" if vix_p <= 0 else "#ff3333"
        with mc2.container(border=True):
            st.markdown(f"<div style='text-align:center; font-size:0.85rem;'>VIX 恐慌指數</div><div style='text-align:center; font-size:1.1rem; font-weight:bold; color:{vix_c};'>{vix_data.get('price', 0):,.2f}<br>{'+' if vix_p>0 else ''}{vix_p:.2f}%</div><div style='text-align:center; font-size:0.75rem; color:#888; margin-top:5px;'>🕒 {vix_data.get('time', '無')}<br><a href='{vix_data.get('url', '#')}' target='_blank' style='color:#888; text-decoration:none;'>🔗 Yahoo Finance</a></div>", unsafe_allow_html=True)
        
        jpy_data = macro.get('JPY=X', {"price": 0, "pct": 0, "time": "無", "url": "https://finance.yahoo.com/quote/JPY=X"})
        jpy_p = jpy_data.get('pct', 0)
        jpy_c = "#ffcc00" 
        jpy_status = "央行趨緩" if jpy_p > 0 else "升息撤資警戒"
        with mc3.container(border=True):
            st.markdown(f"<div style='text-align:center; font-size:0.85rem;'>日圓動向(USD/JPY)</div><div style='text-align:center; font-size:1.1rem; font-weight:bold; color:{jpy_c};'>{jpy_data.get('price', 0):,.2f}<br>{jpy_status}</div><div style='text-align:center; font-size:0.75rem; color:#888; margin-top:5px;'>🕒 {jpy_data.get('time', '無')}<br><a href='{jpy_data.get('url', '#')}' target='_blank' style='color:#888; text-decoration:none;'>🔗 Yahoo Finance</a></div>", unsafe_allow_html=True)
            
        wtx_diff_text = "無報價"
        wtx_diff_color = "#888"
        if wtx_close > 0 and twii_close > 0:
            diff = wtx_close - twii_close
            wtx_diff_color = "#ff3333" if diff >= 0 else "#00cc00"
            wtx_diff_text = f"{'+' if diff>0 else ''}{diff:.0f}點 (逆/正價差)"
        with mc4.container(border=True):
            st.markdown(f"<div style='text-align:center; font-size:0.85rem;'>台指期夜盤動態</div><div style='text-align:center; font-size:1.1rem; font-weight:bold; color:{wtx_diff_color};'>{wtx_close:,.0f}<br>{wtx_diff_text}</div><div style='text-align:center; font-size:0.75rem; color:#888; margin-top:5px;'>🕒 {wtx_time_str[-13:] if len(wtx_time_str) >= 13 else wtx_time_str}<br><a href='https://tw.stock.yahoo.com/quote/FITXAMP' target='_blank' style='color:#888; text-decoration:none;'>🔗 Yahoo 股市</a></div>", unsafe_allow_html=True)
    except Exception as e: 
        st.error(f"大盤儀表板渲染發生錯誤，防護系統啟動中。({str(e)})")

# ==========================================
# 🚀 背景極速快取模組 (0.1秒秒發快取引擎)
# ==========================================
@st.cache_data(ttl=180, show_spinner=False)
def get_global_scan_results(pool_tuple):
    scan_results = []
    def process_scan(stock):
        df = get_stock_data(stock)
        if df is not None: return analyze_today(df, stock, inst_data=None)
        return None
        
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(process_scan, stock): stock for stock in pool_tuple}
        for future in concurrent.futures.as_completed(futures):
            try:
                res = future.result()
                if res: scan_results.append(res)
            except: pass
    return scan_results

# ==========================================
# 🚀 頁面路由控制中心
# ==========================================
if st.session_state.page == "home":
    st.markdown("<h1 style='text-align: center;'>🇹🇼 雷達總機</h1>", unsafe_allow_html=True)
    render_index_board()
    
    st.markdown("<h3 style='margin-top: 15px;'>🎯 策略條件篩選</h3>", unsafe_allow_html=True)
    btn_col1, btn_col2, btn_col3 = st.columns(3)
    if btn_col1.button("✅ 綜合買點榜", use_container_width=True): st.session_state.scan_mode = "buy"; st.rerun()
    if btn_col2.button("🔥 紅吞反轉榜", use_container_width=True): st.session_state.scan_mode = "red_engulf"; st.rerun()
    if btn_col3.button("📊 近五日成交量", use_container_width=True): st.session_state.scan_mode = "recent"; st.rerun()
    
    top_100_pool = fetch_twse_top_100()
    pool = tuple(set(top_100_pool + st.session_state.custom_pool + list(STOCK_NAMES.keys())))
    
    with st.spinner("🚀 大腦背景資料庫存取中 (若無快取約需 5 秒，快取命中則 0.1 秒極速秒發)..."):
        scan_results = get_global_scan_results(pool)
            
    if scan_results:
        df_results = pd.DataFrame(scan_results)
        
        df_results['Bullish_Count'] = df_results.apply(
            lambda r: (1 if r.get('紅吞') or r.get('近七日紅吞') else 0) + 
                      (1 if r.get('回測有撐') else 0) + 
                      (1 if r.get('5日線即將上彎') else 0), axis=1)

        if st.session_state.scan_mode == "recent":
            st.markdown("##### 📊 近五日成交量排行榜")
            df_disp = df_results.sort_values(by="成交量", ascending=False).head(20)
        elif st.session_state.scan_mode == "red_engulf":
            st.markdown("##### 🔥 近七日觸發「紅吞（多頭吞噬）」反轉型態標的 (S、A級)")
            # 🚀 修正：紅吞榜現在只顯示 S 級與 A 級的股票
            df_disp = df_results[(df_results['近七日紅吞'] == True) & (df_results['Score'] >= 2)].sort_values(
                by=['Score', 'Bullish_Count', '漲跌幅'], ascending=[False, False, False]
            ).head(20)
            if df_disp.empty: st.info("💡 目前雷達池內近七日內暫無符合「紅吞反轉型態」的強勢個股。")
        elif st.session_state.scan_mode == "buy":
            st.markdown("##### 🎯 尋找買點榜單 (高靈敏度動能捕捉榜)")
            df_disp = df_results[df_results['Score'] >= 2].sort_values(
                by=['Score', 'Bullish_Count', '漲跌幅'], ascending=[False, False, False]
            ).head(20)
            if df_disp.empty: st.info("目前雷達池內沒有符合條件的標的。")
            
        st.session_state.nav_pool = df_disp['ticker_raw'].tolist()
        st.session_state.nav_pool_data = df_disp.to_dict('records') 
            
        for _, r in df_disp.iterrows():
            p_val = r['漲跌']
            sign = "+" if p_val > 0 else ""
            trend_icon = "🔺" if p_val > 0 else ("🔻" if p_val < 0 else "➖")
            s_score = r['Score']
            score_icon = "🟢 S級" if s_score >= 5 else ("🟡 A級" if s_score >= 2 else "⚪ 觀望")
            
            tags = []
            if r.get('紅吞'): tags.append("🔺吞")
            elif r.get('黑吞'): tags.append("🔻吞")
            elif r.get('近七日紅吞'): tags.append("🔸吞")
            
            if r.get('回測有撐'): tags.append("📌撐")
            elif r.get('反彈遇壓'): tags.append("⚠️壓")
            
            if '5日線即將上彎' in r:
                tags.append("↗️" if r.get('5日線即將上彎') else "↘️")
                
            tag_display = " | ".join(tags)
            if tag_display: tag_display = f" | {tag_display}"
            
            button_label = f"▪️ {r['代號']} {r['名稱']} {trend_icon}{r['收盤價']}({sign}{r['漲跌幅']}%) | {score_icon}{tag_display}"
            if st.button(button_label, key=f"btn_{r['ticker_raw']}_{st.session_state.scan_mode}", use_container_width=True):
                st.session_state.update({"current_stock": r['ticker_raw'], "page": "analysis", "date_offset": 0})
                st.rerun()

elif st.session_state.page == "analysis":
    target = st.session_state.current_stock
    c_name = get_stock_name(target)
    
    n_pool = st.session_state.get('nav_pool', [])
    p_stk, n_stk = None, None
    if target in n_pool and len(n_pool) > 1:
        i = n_pool.index(target)
        p_stk = n_pool[i - 1] if i > 0 else None
        n_stk = n_pool[i + 1] if i < len(n_pool) - 1 else None

    c1, c2, c3 = st.columns([1, 1, 1])
    with c1:
        if p_stk and st.button(f"⬅ 上一檔", use_container_width=True): st.session_state.update({"current_stock": p_stk}); st.rerun()
    with c2:
        if st.button("🏠 回雷達總機", use_container_width=True): st.session_state.page = "home"; st.rerun()
    with c3:
        if n_stk and st.button(f"下一檔 ➡", use_container_width=True): st.session_state.update({"current_stock": n_stk}); st.rerun()

    def set_view_days(days):
        st.session_state.view_days = days

    load_ph = st.empty()
    pre_rendered_fig = None  

    with load_ph.container():
        st.markdown(f"<h4 style='text-align:center;'>🚀 正在喚醒【{target} {c_name}】AI 分析大腦...</h4>", unsafe_allow_html=True)
        p_bar = st.progress(0)
        status = st.empty()
        status.markdown("<div style='text-align:center; color:#888;'>⏳ 讀取 K 線與價量歷史...</div>", unsafe_allow_html=True)
        df_chart = get_stock_data(target)
        p_bar.progress(20)

        if df_chart is not None:
            df_slice = df_chart.iloc[:len(df_chart) + st.session_state.date_offset] if st.session_state.date_offset < 0 else df_chart
            if len(df_slice) < 5:
                load_ph.empty()
                st.warning("歷史資料不足")
            else:
                status.markdown("<div style='text-align:center; color:#888;'>🏦 追蹤三大法人籌碼流向...</div>", unsafe_allow_html=True)
                inst_data = get_institutional_trading(target)
                p_bar.progress(40)
                status.markdown("<div style='text-align:center; color:#888;'>🧠 啟動動能與型態精算模組...</div>", unsafe_allow_html=True)
                data = analyze_today(df_slice, target, inst_data)
                sc = data['Score']
                p_bar.progress(60)
                status.markdown("<div style='text-align:center; color:#888;'>📑 獲取基本面與產業定位...</div>", unsafe_allow_html=True)
                f_data = get_fundamental_and_industry_data(target, data['收盤價'])
                p_bar.progress(75)
                status.markdown("<div style='text-align:center; color:#888;'>🌍 交叉比對總體經濟與大盤風險...</div>", unsafe_allow_html=True)
                twii_close, twii_change, twii_time_str = get_twii_quote()
                twii_df_for_pred = get_stock_data("^TWII")
                t_title, t_desc, tmr_title, tmr_desc, l_dt, n_dt, risk_score, macro = open_pred_logic(twii_df_for_pred, twii_close, twii_change, twii_time_str)
                p_bar.progress(85)
                
                status.markdown("<div style='text-align:center; color:#888;'>🎨 繪製高畫質技術線圖中...</div>", unsafe_allow_html=True)
                current_show_buy = st.session_state.get('toggle_buy_sig', True)
                current_show_sup = st.session_state.get('toggle_sup_res', False)
                current_show_signals = st.session_state.get('toggle_signals', True)
                pre_rendered_fig = draw_professional_chart(df_slice, target, data['收盤價'], st.session_state.view_days, is_light_mode, current_show_buy, f_data, current_show_sup, current_show_signals)
                p_bar.progress(95)

                status.markdown("<div style='text-align:center; color:#00cc00; font-weight:bold;'>✅ 解析完成！即將顯示...</div>", unsafe_allow_html=True)
                p_bar.progress(100)
                time.sleep(0.1) 
        else:
            load_ph.empty()
            st.error("查無此股票資料。")

    if df_chart is not None and len(df_slice) >= 5:
        load_ph.empty()
        
        display_time = get_stock_live_time(target)
        p_color = '#ff3333' if data['漲跌'] >= 0 else '#00cc00'
        analysis_date = df_slice.index[-1].strftime('%Y/%m/%d')
        
        col_main_view, col_right_menu = st.columns([3.9, 1.1])
        
        with col_main_view:
            st.markdown(f"<h2 style='text-align: center; margin-bottom: 5px;'>🎯 {target} {c_name}</h2>", unsafe_allow_html=True)
            st.markdown(f"<div style='text-align: center; color: #888; font-size: 1.1rem;'>【{f_data['Industry']}】</div>", unsafe_allow_html=True)
            st.markdown(f"<h3 style='text-align: center; color: {p_color}; font-size: 2.2rem; margin-bottom: 0px;'>{data['收盤價']} ({'+' if data['漲跌']>0 else ''}{data['漲跌幅']}%)</h3>", unsafe_allow_html=True)
            st.markdown(f"<div style='text-align: center; color: #888; font-size: 1rem; margin-top: 5px;'>昨日收盤: {data['昨日收盤價']} | 最新報價: {data['收盤價']}</div>", unsafe_allow_html=True)
            st.markdown(f"<div style='text-align: center; color: #888; font-size: 0.9rem; margin-bottom: 10px;'>🕒 盤勢分析日期: {analysis_date} | 抓取時間: {display_time}</div>", unsafe_allow_html=True)
            
            _, up_c, _ = st.columns([1, 2, 1])
            if up_c.button("🔄 更新個股即時數值", use_container_width=True): st.cache_data.clear(); st.rerun()
            st.markdown("---")
            
            stop_loss_html = ""
            recent_20 = df_slice.tail(20)
            recent_signals = []
            for idx in range(len(recent_20)):
                temp_df = df_slice.iloc[:len(df_slice) - 20 + idx + 1]
                if len(temp_df) >= 5:
                    t_data = analyze_today(temp_df, target, inst_data=None)
                    if t_data and t_data['Score'] >= 2: recent_signals.append((temp_df.index[-1], t_data['收盤價']))
            
            if recent_signals:
                last_sig_date, last_sig_price = recent_signals[-1]
                if data['收盤價'] <= last_sig_price * 0.95:
                    loss_pct = (data['收盤價'] - last_sig_price) / last_sig_price * 100
                    stop_loss_html = f'''<div style="background-color: #ffe6e6; border-left: 6px solid #ff3333; padding: 15px; margin-bottom: 20px; border-radius: 4px;"><h4 style="color: #ff3333; margin-top: 0; font-size: 1.3rem;">🚨 【嚴格停損警報】觸發 5% 停損防護線</h4><span style="color: #333; font-size: 1.05rem; line-height: 1.6;">系統偵測到最近一次策略買訊 ({last_sig_date.strftime('%Y/%m/%d')}) 基準成本為 <b>{last_sig_price:.2f}</b>。<br>目前現價 <b>{data['收盤價']}</b> 已跌穿 5% 鐵律防護線 (預估帳面分歧為 <span style="color:#ff3333; font-weight:bold;">{loss_pct:.2f}%</span>)。<br><b>防範警訊：中線趨勢支撐已破，強烈建議嚴守交易紀律，果斷停損出場觀望，切勿盲目攤平接刀！</b></span></div>'''
            if stop_loss_html: st.markdown(stop_loss_html, unsafe_allow_html=True)

            st.markdown("##### 💡 近一個月歷史買點回測與趨勢分析")
            recent_30 = df_slice.tail(30)
            s_count, a_count = 0, 0
            buy_points_prices = []
            price_30_days_ago = recent_30['Close'].iloc[0]
            current_price = recent_30['Close'].iloc[-1]
            month_trend_pct = (current_price - price_30_days_ago) / price_30_days_ago * 100
            trend_color = "#ff3333" if month_trend_pct >= 0 else "#00cc00"
            trend_text = "上漲" if month_trend_pct >= 0 else "下跌"
            sign_t = "+" if month_trend_pct > 0 else ""
            
            for idx in range(len(recent_30)):
                temp_df = df_slice.iloc[:len(df_slice) - 30 + idx + 1]
                if len(temp_df) >= 5:
                    t_data = analyze_today(temp_df, target, inst_data=None)
                    if t_data:
                        if t_data['Score'] >= 5: s_count += 1; buy_points_prices.append(t_data['收盤價'])
                        elif t_data['Score'] >= 2: a_count += 1; buy_points_prices.append(t_data['收盤價'])
            
            with st.container(border=True):
                col_sum1, col_sum2, col_sum3 = st.columns(3)
                with col_sum1: st.markdown(f"<div style='text-align:center;'>近一月趨勢<br><span style='color:{trend_color}; font-size:1.6rem; font-weight:900;'>{trend_text} {sign_t}{month_trend_pct:.2f}%</span></div>", unsafe_allow_html=True)
                with col_sum2: st.markdown(f"<div style='text-align:center;'>🟢 S級 強烈買進<br><span style='font-size:1.6rem; font-weight:900; color:#00cc00;'>{s_count} 次</span></div>", unsafe_allow_html=True)
                with col_sum3: st.markdown(f"<div style='text-align:center;'>🟡 A級 偏多試單<br><span style='font-size:1.6rem; font-weight:900; color:#ffcc00;'>{a_count} 次</span></div>", unsafe_allow_html=True)
                if not buy_points_prices:
                    summary_text = "近一個月股價呈現高檔推升或低迷打底，未曾觸發 any A/S 級超賣點條件，建議控制風險。"
                else:
                    avg_buy_price = sum(buy_points_prices) / len(buy_points_prices)
                    profit_pct = (current_price - avg_buy_price) / avg_buy_price * 100
                    prof_color = "#ff3333" if profit_pct >= 0 else "#00cc00"
                    prof_text = "獲利" if profit_pct >= 0 else "虧損"
                    summary_text = f"本月共發出 **{s_count + a_count}** 次策略買點。等額分批建倉平均成本約為 **{avg_buy_price:.2f}**。對比今日收盤，策略回測呈 <span style='color:{prof_color}; font-weight:bold;'>{prof_text} {'+' if profit_pct>0 else ''}{profit_pct:.2f}%</span>。"
                st.markdown(f"<div style='margin-top:12px; padding:12px; background-color:{'#f0f8ff' if is_light_mode else '#1e2433'}; border-radius:8px; line-height: 1.6;'>📝 <b>大腦回測總結：</b>{summary_text}</div>", unsafe_allow_html=True)

            bullets, v_t, v_c, v_a = generate_comprehensive_analysis(data, inst_data, sc, t_title, tmr_title)
            bullets_html = "".join([f"<li style='margin-bottom: 8px;'>{b}</li>" for b in bullets])
            st.markdown(f'''<div style="border: 2px solid {v_c}; border-radius: 10px; padding: 20px; margin-bottom: 20px; background-color: {bg_col};"><h3 style="text-align: center; color: {v_c}; margin-top: 0; font-size: 1.8rem;">🤖 AI 決策大腦：{v_t.replace('🟢 ', '').replace('🟡 ', '').replace('⚪ ', '').replace('🟠 ', '').replace('🔴 ', '')}</h3><hr style="border-color: {border_col}; margin: 15px 0;"><div style="margin-bottom: 15px;"><h4 style="color: {text_col}; margin-bottom: 10px;">🔍 綜合技術、型態與籌碼防護診斷：</h4><ul style="font-size: 1rem; color: {text_col}; line-height: 1.6;">{bullets_html}</ul></div><div style="background-color: {'#f0f8ff' if is_light_mode else '#1e2433'}; padding: 15px; border-radius: 8px; border-left: 5px solid {v_c};"><p style="font-size: 1.15rem; color: {text_col}; margin: 0; line-height: 1.6;">{v_a}</p></div></div>''', unsafe_allow_html=True)
            
            dc1, dc2, dc3, dc4, dc5, dc6, dc7 = st.columns([0.8, 0.8, 0.8, 0.8, 1.3, 1.3, 1.3])
            dc1.button("1個月", on_click=set_view_days, args=(20,))
            dc2.button("3個月", on_click=set_view_days, args=(60,))
            dc3.button("6個月", on_click=set_view_days, args=(120,))
            dc4.button("1年", on_click=set_view_days, args=(240,))
            with dc5: st.toggle("🛒 顯示買進", value=True, key='toggle_buy_sig')
            with dc6: st.toggle("📏 支撐/壓力", value=False, key='toggle_sup_res')
            with dc7: st.toggle("🏷️ 顯示符號", value=True, key='toggle_signals')
                
            if pre_rendered_fig is not None:
                st.plotly_chart(pre_rendered_fig, use_container_width=True, config={'displayModeBar': False, 'scrollZoom': False})
            
            st.markdown("### 🕵️‍♂️ 進階數據面板")
            a1, a2 = st.columns(2)
            with a1.container(border=True): st.markdown(f"##### 📊 技術與動能指標<br><br>**月線乖離率:** `{data['BIAS']}%`<br><br>**MACD 柱狀體:** `{data['MACD柱']}`<br><br>**5日均量:** `{data['5日均量']} 張`", unsafe_allow_html=True)
            with a2.container(border=True):
                eps = f_data['EPS']; m_eps = round(float(eps)/3, 2) if eps != "無" else "無"
                st.markdown(f"##### 📑 基本面價值評估<br><br>**當季每股盈餘 (EPS):** `{eps}`<br><br>**換算單月 EPS:** `{m_eps}`<br><br>**最新即時本益比 (P/E):** `{f_data['PE']}`", unsafe_allow_html=True)
            st.divider()
            st.subheader("🏦 近期三大法人逐日買賣超")
            if inst_data: 
                st.dataframe(pd.DataFrame(inst_data), use_container_width=True, hide_index=True)
            else: st.info("目前無法自動抓取籌碼資料。")
            
            st.divider()
            st.subheader("⭐ 自選群組管理")
            all_groups = list(st.session_state.fav_groups.keys())
            current_groups = [g for g, s in st.session_state.fav_groups.items() if target in s]
            selected_groups = st.multiselect("將此標的加入以下群組：", options=all_groups, default=current_groups)
            if st.button("💾 儲存自選設定", use_container_width=True, type="primary"):
                for g in all_groups:
                    if g in selected_groups and target not in st.session_state.fav_groups[g]: st.session_state.fav_groups[g].append(target)
                    elif g not in selected_groups and target in st.session_state.fav_groups[g]: st.session_state.fav_groups[g].remove(target)
                save_json(FAV_GROUPS_FILE, st.session_state.fav_groups)
                st.success("✅ 群組設定已更新！")
                st.rerun()

        with col_right_menu:
            mode_titles = {
                "buy": "✅ 綜合買點榜", 
                "red_engulf": "🔥 紅吞反轉榜", 
                "recent": "📊 近五日成交量"
            }
            active_title = mode_titles.get(st.session_state.scan_mode, "📋 當前雷達清單")
            
            st.markdown(f'''<div style="text-align: center; font-size: 1.15rem; font-weight: bold; background-color: {bg_col}; border: 1px solid {border_col}; padding: 8px; border-radius: 6px; color: #ffcc00 !important; margin-bottom: 12px;">{active_title}</div>''', unsafe_allow_html=True)
            
            if n_pool:
                nav_data = st.session_state.get('nav_pool_data', [])
                
                for stock_id in n_pool:
                    is_current = (stock_id == target)
                    stock_info = next((item for item in nav_data if item["ticker_raw"] == stock_id), None)
                    
                    if stock_info:
                        p_val = stock_info['漲跌']
                        sign = "+" if p_val > 0 else ""
                        trend_icon = "🔺" if p_val > 0 else ("🔻" if p_val < 0 else "➖")
                        s_score = stock_info['Score']
                        score_icon = "🟢 S級" if s_score >= 5 else ("🟡 A級" if s_score >= 2 else "⚪ 觀望")
                        
                        tags = []
                        if stock_info.get('紅吞'): tags.append("🔺吞")
                        elif stock_info.get('黑吞'): tags.append("🔻吞")
                        elif stock_info.get('近七日紅吞'): tags.append("🔸吞")
                        
                        if stock_info.get('回測有撐'): tags.append("📌撐")
                        elif stock_info.get('反彈遇壓'): tags.append("⚠️壓")
                        
                        if '5日線即將上彎' in stock_info:
                            tags.append("↗️" if stock_info.get('5日線即將上彎') else "↘️")
                            
                        tag_display = " | ".join(tags)
                        if tag_display: tag_display = f" | {tag_display}"
                        
                        btn_prefix = "⭐ " if is_current else "▪️ "
                        btn_label = f"{btn_prefix}{stock_info['代號']} {stock_info['名稱']} {trend_icon}{stock_info['收盤價']}({sign}{stock_info['漲跌幅']}%) | {score_icon}{tag_display}"
                    else:
                        btn_label = f"⭐ {stock_id} {get_stock_name(stock_id)}" if is_current else f"▪️ {stock_id} {get_stock_name(stock_id)}"
                    
                    if st.button(btn_label, key=f"right_nav_{stock_id}_{st.session_state.scan_mode}", use_container_width=True):
                        st.session_state.current_stock = stock_id
                        st.rerun()
            else:
                st.info("暫無榜單暫存。請先返回首頁執行篩選掃描。")
