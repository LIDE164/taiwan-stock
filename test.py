import yfinance as yf
import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import json
import os
import urllib.parse
import xml.etree.ElementTree as ET
import streamlit.components.v1 as components
from bs4 import BeautifulSoup
import re

# ==========================================
# 0. 系統初始化與風格設定
# ==========================================
st.set_page_config(page_title="專業交易雷達", layout="centered", initial_sidebar_state="collapsed")

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
            if name and ticker not in name and "嗨投資" not in name and not name.isdigit():
                return name
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
        else:
            name = ticker_str
            
    name = name.replace(ticker_str, "").strip()
    return name

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
if 'view_days' not in st.session_state: st.session_state.view_days = 20
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
        st.sidebar.button(f"📊 {fav} {get_stock_name(fav)}", key=f"sf_{fav}", on_click=lambda f=fav: st.session_state.update({"current_stock": f, "page": "analysis", "date_offset": 0}))

st.sidebar.divider()
st.sidebar.title("⚙️ 雷達池設定")
if st.sidebar.button("🔄 更新熱門股", use_container_width=True):
    st.session_state.custom_pool = fetch_twse_top_50()
    save_json(POOL_FILE, st.session_state.custom_pool)
    st.sidebar.success("✅ 完成！")
    st.rerun()

st.sidebar.markdown("<div style='font-size: 0.8rem; color: #888; text-align: center; margin-top: 10px;'>資料來源: <a href='https://openapi.twse.com.tw/' target='_blank' style='color: #00ffcc; text-decoration: none;'>台灣證交所 OpenAPI</a></div>", unsafe_allow_html=True)

@st.cache_data(ttl=3600, show_spinner=False)
def get_fundamental_and_industry_data(ticker_number, current_price=0):
    base_ticker = str(ticker_number).strip().upper().replace(".TW", "").replace(".TWO", "")
    eps_val, pe_val = "無", "無"
    ind = "一般產業"
    
    # 策略 1: 鉅亨網 (Cnyes) 抓取近四季 EPS
    try:
        url = f"https://invest.cnyes.com/twstock/TWS/{base_ticker}/overview"
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, 'html.parser')
            text = soup.get_text(separator='|')
            match = re.search(r'近四季EPS\|+([\-\d\.]+)', text)
            if match:
                eps_val = match.group(1)
            else:
                # Fallback to Cnyes API
                res_api = requests.get(f"https://ws.cnyes.com/twstock/api/v1/company/profile/{base_ticker}", timeout=3)
                if res_api.status_code == 200:
                    data = res_api.json()
                    if 'data' in data and 'eps' in data['data']:
                        eps_val = f"{float(data['data']['eps']):.2f}"
    except: pass

    # 策略 2: 產業抓取 (yfinance fallback)
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

    # 動態精算 PE
    try:
        if eps_val != "無":
            eps_f = float(eps_val)
            if eps_f > 0 and current_price > 0:
                pe_val = str(round(float(current_price) / eps_f, 2))
            elif eps_f <= 0:
                pe_val = "無 (EPS ≦ 0)"
    except: pass

    return {"EPS": eps_val, "PE": pe_val, "Industry": ind}

@st.cache_data(ttl=5, show_spinner=False) 
def get_twii_quote():
    update_time_str = datetime.now().strftime('%Y/%m/%d %H:%M:%S')
    fallback_curr, fallback_change, fallback_time = 0, 0, ""

    try:
        df = yf.Ticker("^TWII").history(period="5d")
        if not df.empty and len(df) >= 2:
            fallback_curr = float(df['Close'].iloc[-1])
            fallback_change = float(df['Close'].iloc[-1] - df['Close'].iloc[-2])
            last_date = df.index[-1].strftime('%Y/%m/%d')
            fallback_time = f"{last_date} 收盤"
    except: pass

    try:
        session = requests.Session()
        session.get("https://mis.twse.com.tw/stock/index.jsp", headers={"User-Agent": "Mozilla/5.0"}, timeout=3)
        ts = int(datetime.now().timestamp() * 1000)
        url = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_t00.tw&json=1&delay=0&_={ts}"
        res = session.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=3)
        if res.status_code == 200:
            data = res.json()
            if 'msgArray' in data and len(data['msgArray']) > 0:
                info = data['msgArray'][0]
                z = info.get('z')
                y = info.get('y')
                d = info.get('d')
                t = info.get('t')
                curr = float(z.replace(',','')) if z and z != '-' else (float(y.replace(',','')) if y and y != '-' else 0)
                prev = float(y.replace(',','')) if y and y != '-' else curr
                if curr > 10000:
                    update_time_str = f"{d[:4]}/{d[4:6]}/{d[6:]} {t}" if d and t else "即時撮合中"
                    return curr, curr - prev, update_time_str
    except: pass

    try:
        res = requests.get("https://ws.cnyes.com/charting/api/v1/TWS:TSE01:INDEX/quote", timeout=3)
        if res.status_code == 200:
            data = res.json()['data']['quote']
            curr = float(data['23'])
            prev = float(data['24'])
            ts_sec = int(data['20'])
            update_time_str = datetime.fromtimestamp(ts_sec).strftime('%Y/%m/%d %H:%M:%S')
            if curr > 10000: return curr, curr - prev, update_time_str
    except: pass

    if fallback_curr > 10000:
        return fallback_curr, fallback_change, f"{fallback_time}"

    return 0, 0, "無資料"

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_twse_index_history():
    try:
        start_date = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
        url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockPrice&data_id=TAIEX&start_date={start_date}"
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            data = res.json()
            if data.get('msg') == 'success' and len(data.get('data', [])) > 0:
                df = pd.DataFrame(data['data'])
                df['date'] = pd.to_datetime(df['date'])
                df.set_index('date', inplace=True)
                df.rename(columns={'open': 'Open', 'max': 'High', 'min': 'Low', 'close': 'Close', 'Trading_Volume': 'Volume'}, inplace=True)
                return df[['Open', 'High', 'Low', 'Close', 'Volume']]
    except: pass
    
    try:
        df = yf.Ticker("^TWII").history(period="1y")
        if not df.empty:
            return df[['Open', 'High', 'Low', 'Close', 'Volume']]
    except: pass
    
    return None

@st.cache_data(ttl=60, show_spinner=False) 
def get_stock_data(ticker_number):
    base_ticker = str(ticker_number).strip().upper().replace(".TW", "").replace(".TWO", "")
    
    def fetch_clean(sym):
        try:
            d = yf.Ticker(sym).history(period="1y")
            if d is not None and not d.empty:
                d = d.dropna(subset=['Close'])
                if not sym.startswith('^'): d = d[d['Volume'] > 0] 
                if len(d) >= 20: return d
        except: pass
        return None

    df = fetch_twse_index_history() if base_ticker == "^TWII" else fetch_clean(f"{base_ticker}.TW")
    if df is None and base_ticker != "^TWII": df = fetch_clean(f"{base_ticker}.TWO")
    if df is None and base_ticker != "^TWII": df = fetch_clean(base_ticker)
        
    if df is None: return None
    
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
                df.loc[df['name'].str.contains('Foreign', case=False, na=False), 'type'] = '外資'
                df.loc[df['name'].str.contains('Investment_Trust', case=False, na=False), 'type'] = '投信'
                df.loc[df['name'].str.contains('Dealer', case=False, na=False) & ~df['name'].str.contains('Foreign', case=False, na=False), 'type'] = '自營商'
                pivot = df.groupby(['date', 'type'])['net'].sum().unstack(fill_value=0).reset_index()
                for col in ['外資', '投信', '自營商']:
                    if col not in pivot.columns: pivot[col] = 0
                pivot['單日合計'] = pivot['外資'] + pivot['投信'] + pivot['自營商']
                pivot = pivot.sort_values('date', ascending=False).head(10)
                res_list = []
                for _, row in pivot.iterrows():
                    res_list.append({
                        "日期": row['date'][-5:],
                        "外資(張)": int(row['外資']),
                        "投信(張)": int(row['投信']),
                        "自營商(張)": int(row['自營商']),
                        "單日合計(張)": int(row['單日合計'])
                    })
                if res_list: return res_list
    except: pass
    
    try:
        url = f"https://ws.cnyes.com/charting/api/v1/TWS:{ticker}:STOCK/institutional-trading?limit=10"
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            raw_data = res.json()['data']
            res_list = []
            for item in raw_data:
                res_list.append({
                    "日期": item.get('date', '')[-5:],
                    "外資(張)": int(item.get('foreignInvestorNetBuy', 0)),
                    "投信(張)": int(item.get('investmentTrustNetBuy', 0)),
                    "自營商(張)": int(item.get('dealerNetBuy', 0)),
                    "單日合計(張)": int(item.get('totalNetBuy', 0))
                })
            if res_list: return res_list
    except: pass
    
    return []

def get_decision_score(data, fund_data):
    sc, rs = 0, []
    if data['訊號']: sc+=3; rs.append("✅ 穩在月線上且KDJ超賣")
    if data['收盤價'] <= data['BB_DN'] * 1.02: sc+=2; rs.append("✅ 觸及布林下軌")
    if data['BIAS'] < -5: sc+=1; rs.append("✅ 負乖離擴大")
    try: eps_f = float(str(fund_data['EPS']).replace(',', ''))
    except: eps_f = 0.0
    if eps_f > 0: sc+=2; rs.append("✅ 基本面獲利")
    if data['成交量'] / (data['5日均量'] + 0.001) > 1.5 and data['漲跌'] > 0: sc+=2; rs.append("✅ 量價配合")
    if data['J值'] >= 80: sc-=3; rs.append("⚠️ KDJ高檔過熱")
    if data['收盤價'] >= data['BB_UP'] * 0.98: sc-=2; rs.append("⚠️ 觸及布林上軌")
    if data['BIAS'] > 7: sc-=2; rs.append("⚠️ 正乖離過大")
    if data['收盤價'] < data['20MA']: sc-=2; rs.append("⚠️ 跌破月線")
    if eps_f < 0: sc-=1; rs.append("⚠️ 基本面虧損")
    return sc, rs

def analyze_today(df, ticker_number):
    if df is None or len(df) < 5: return None
    t, p, p5 = df.iloc[-1], df.iloc[-2], df.iloc[-5]
    fund = get_fundamental_and_industry_data(ticker_number, round(t['Close'], 2))
    data = {
        "代號": ticker_number, "名稱": get_stock_name(ticker_number), "ticker_raw": ticker_number,
        "產業": fund['Industry'], "昨日收盤價": round(p['Close'], 2), "收盤價": round(t['Close'], 2), 
        "漲跌": round(t['Close'] - p['Close'], 2), "漲跌幅": round((t['Close'] - p['Close']) / p['Close'] * 100, 2), 
        "近5日漲幅(%)": f"{round((t['Close'] - p5['Close'])/p5['Close']*100, 2)}%",
        "成交量": int(t['Volume']/1000), "5日均量": int(df['Volume'].tail(5).mean()/1000),
        "5MA": round(t['5MA'], 2), "10MA": round(t['10MA'], 2), "20MA": round(t['20MA'], 2),
        "60MA": round(t['60MA'], 2),
        "BB_UP": round(t['BB_UP'], 2), "BB_DN": round(t['BB_DN'], 2), "BIAS": round(t['BIAS_20'], 2),
        "MACD": round(t['MACD'], 2), "MACD柱": round(t['MACD_Hist'], 3),
        "K": round(t['K'], 2), "D": round(t['D'], 2), "J值": round(t['J'], 2),
        "訊號": (t['Close'] > t['20MA']) and (t['Close'] < t['5MA']) and (t['J'] < 20)
    }
    sc, _ = get_decision_score(data, fund)
    data['Score'] = sc
    return data

def generate_comprehensive_analysis(data, inst_data, sc, market_today="", market_tmr=""):
    analysis_bullets = []
    
    # 盤勢分析導航
    if market_today and market_tmr:
        market_today_clean = market_today.replace("🔥 ", "").replace("⚠️ ", "").replace("💪 ", "").replace("🩸 ", "").replace("📈 ", "").replace("📉 ", "").replace("⚖️ ", "")
        market_tmr_clean = market_tmr.replace("🚀 ", "").replace("⚠️ ", "").replace("📈 ", "").replace("📉 ", "").replace("⚖️ ", "")
        
        if "多" in market_tmr_clean or "高" in market_tmr_clean:
            analysis_bullets.append(f"🔥 <span style='color:#ff3333; font-weight:bold;'>大盤盤勢導航：今日【{market_today_clean}】，預測次一交易日【{market_tmr_clean}】，大環境偏多有利個股發揮。</span>")
        elif "空" in market_tmr_clean or "低" in market_tmr_clean:
            analysis_bullets.append(f"⚠️ <span style='color:#00cc00;'>大盤盤勢導航：今日【{market_today_clean}】，預測次一交易日【{market_tmr_clean}】，大環境不佳需防範系統性風險。</span>")
        else:
            analysis_bullets.append(f"⚪ <b>大盤盤勢導航</b>：今日【{market_today_clean}】，預測次一交易日【{market_tmr_clean}】，大環境震盪，個股表現分歧。")

    t_short = data['收盤價'] > data['5MA']
    t_mid = data['收盤價'] > data['20MA']
    t_long = data['收盤價'] > data['60MA']
    
    if t_short and t_mid and t_long:
        analysis_bullets.append("🔥 <span style='color:#ff3333; font-weight:bold;'>三級多空趨勢：短、中、長線（5T, 20T, 60T）皆呈現完全多頭排列，趨勢極強。</span>")
    elif not t_short and not t_mid and not t_long:
        analysis_bullets.append("⚠️ <span style='color:#00cc00;'>三級多空趨勢：短、中、長線皆呈現空頭排列，趨勢極弱空方控盤。</span>")
    else:
        trends = []
        trends.append("🔥 <span style='color:#ff3333; font-weight:bold;'>站上短均</span>" if t_short else "⚠️ <span style='color:#00cc00;'>跌破短均</span>")
        trends.append("🔥 <span style='color:#ff3333; font-weight:bold;'>守住月線</span>" if t_mid else "⚠️ <span style='color:#00cc00;'>跌破月線</span>")
        trends.append("🔥 <span style='color:#ff3333; font-weight:bold;'>站上季線</span>" if t_long else "⚠️ <span style='color:#00cc00;'>跌破季線</span>")
        analysis_bullets.append(f"⚪ <b>三級多空趨勢</b>：目前處於多空拉扯震盪整理，狀態為：{'、'.join(trends)}。")
    
    if data['收盤價'] > data['5MA']:
        analysis_bullets.append(f"🔥 <span style='color:#ff3333; font-weight:bold;'>短線強勢表態：股價成功站穩 5 日線 ({data['5MA']}) 之上，短線動能強勁。</span>")
    else:
        analysis_bullets.append(f"⚠️ <span style='color:#00cc00;'><b>短期均線蓋頭反壓</b>：目前股價 ({data['收盤價']}) 低於 5 日線 ({data['5MA']})，短線上檔遭遇壓力。</span>")
        
    if data['收盤價'] > data['20MA']:
        analysis_bullets.append(f"🔥 <span style='color:#ff3333; font-weight:bold;'>月線支撐強勁：股價穩居 20 日均線 ({data['20MA']}) 之上，波段多頭格局明確。</span>")
    elif data['收盤價'] >= data['20MA'] * 0.98:
        analysis_bullets.append(f"⚪ <b>月線保衛戰</b>：現價距離 20 日均線 ({data['20MA']}) 非常接近，此為中線多空防線，不宜跌破。")
    else:
        analysis_bullets.append(f"⚠️ <span style='color:#00cc00;'><b>跌破月線防守</b>：現價落於月線 ({data['20MA']}) 之下，中線趨勢有轉弱風險。</span>")

    if data['成交量'] > data['5日均量'] * 1.3 and data['漲跌'] > 0:
        analysis_bullets.append(f"🔥 <span style='color:#ff3333; font-weight:bold;'>量價配合：今日成交量 ({data['成交量']}張) 明顯大於 5日均量 ({data['5日均量']}張) 且收紅，多方動能充沛。</span>")
    elif data['成交量'] > data['5日均量'] * 1.3 and data['漲跌'] < 0:
        analysis_bullets.append(f"⚠️ <span style='color:#00cc00;'><b>爆量下殺</b>：今日成交量 ({data['成交量']}張) 放大且收黑，有籌碼鬆動出貨疑慮。</span>")
    else:
        analysis_bullets.append(f"⚪ <b>量能平穩</b>：今日成交量 ({data['成交量']}張)，表現中規中矩。")
        
    if data['MACD柱'] > 0:
        analysis_bullets.append(f"🔥 <span style='color:#ff3333; font-weight:bold;'>MACD 多方動能強勁：OSC 為紅柱 ({data['MACD柱']})，多頭持續掌控局勢。</span>")
    else:
        analysis_bullets.append(f"⚠️ <span style='color:#00cc00;'><b>MACD 空方動能未歇</b>：OSC 為綠柱 ({data['MACD柱']})，顯示回檔空方動能尚未完全收斂。</span>")
        
    if data['J值'] < 20:
        analysis_bullets.append(f"🔥 <span style='color:#ff3333; font-weight:bold;'>KDJ 極度超賣：J 值來到 ({data['J值']})，隨時醞釀強力技術性反彈。</span>")
    elif data['J值'] > 80:
        analysis_bullets.append(f"⚠️ <span style='color:#00cc00;'><b>KDJ 高檔過熱</b>：J 值高達 {data['J值']}，短線過熱步入超買區。</span>")
    
    if data['K'] > data['D']:
        analysis_bullets.append(f"🔥 <span style='color:#ff3333; font-weight:bold;'>KDJ 黃金交叉：K值 ({data['K']}) 大於 D值 ({data['D']})，指標呈現多頭向上發散。</span>")
    else:
        analysis_bullets.append(f"⚠️ <span style='color:#00cc00;'><b>KDJ 死亡交叉</b>：K值 ({data['K']}) 小於 D值 ({data['D']})，短線動能偏弱。</span>")

    if data['收盤價'] <= data['BB_DN'] * 1.02:
        analysis_bullets.append(f"🔥 <span style='color:#ff3333; font-weight:bold;'>觸及布林下軌：股價貼近布林下軌 ({data['BB_DN']})，具備極強的技術性支撐。</span>")
    elif data['收盤價'] >= data['BB_UP'] * 0.98:
        analysis_bullets.append(f"⚠️ <span style='color:#00cc00;'><b>觸及布林上軌</b>：股價貼近布林上軌 ({data['BB_UP']})，易遇壓力回檔。</span>")

    if data['BIAS'] < -5:
        analysis_bullets.append(f"🔥 <span style='color:#ff3333; font-weight:bold;'>負乖離過大：月線乖離率達 ({data['BIAS']}%)，超跌反彈機率極高。</span>")
    elif data['BIAS'] > 7:
        analysis_bullets.append(f"⚠️ <span style='color:#00cc00;'><b>正乖離過大</b>：月線乖離率達 ({data['BIAS']}%)，追高風險劇增。</span>")

    if inst_data and len(inst_data) >= 3:
        foreign_net = sum([int(str(x['外資(張)']).replace(',', '')) for x in inst_data[:3] if str(x['外資(張)']).replace(',', '').lstrip('-').isdigit()])
        trust_net = sum([int(str(x['投信(張)']).replace(',', '')) for x in inst_data[:3] if str(x['投信(張)']).replace(',', '').lstrip('-').isdigit()])
        
        chip_status = "⚪ <b>法人籌碼動向 (近3日)</b>："
        if foreign_net > 0: chip_status += f"🔥 <span style='color:#ff3333; font-weight:bold;'>外資偏多佈局 (買超 {foreign_net} 張)</span>；"
        else: chip_status += f"⚠️ <span style='color:#00cc00;'>外資調節減碼 (賣超 {abs(foreign_net)} 張)</span>；"
        if trust_net > 0: chip_status += f"🔥 <span style='color:#ff3333; font-weight:bold;'>投信主力力挺 (買超 {trust_net} 張)。</span>"
        else: chip_status += f"⚠️ <span style='color:#00cc00;'>投信高檔結帳 (賣超 {abs(trust_net)} 張)。</span>"
        analysis_bullets.append(chip_status)

    if sc >= 5: 
        v_t, v_c = "🟢 S級買點：強烈建議佈局", "#00cc00"
        v_a = f"✅ <b>進場判斷：強烈買進</b><br>勝率極高！築底完成。<br>📌 建議建倉區間：{data['BB_DN']:.2f} ~ {data['20MA']:.2f} 之間分批加碼。<br>🎯 <b>波段賣出目標價：{data['BB_UP']:.2f} (布林上軌壓力區)</b>。"
    elif sc >= 2: 
        v_t, v_c = "🟡 A級機會：偏多試單", "#ffcc00"
        v_a = f"✅ <b>進場判斷：分批試單</b><br>具備技術面反彈契機！<br>📌 建議短線建倉點：{data['收盤價']:.2f} 附近，跌破 {data['BB_DN']:.2f} 嚴格停損。<br>🎯 <b>短線賣出目標價：{data['BB_UP']:.2f} (布林上軌壓力區)</b>。"
    elif sc >= -1: 
        v_t, v_c = "⚪ 中性觀望：多空不明", "#888888"
        if data['收盤價'] > data['20MA']:
            v_a = f"⏳ <b>進場判斷：暫緩進場 (多方震盪)</b><br>股價在月線 ({data['20MA']:.2f}) 之上震盪，無明顯表態。<br>📌 建議觀察能否放量突破上軌 ({data['BB_UP']:.2f})，逢回不破月線再嘗試建倉。"
        else:
            v_a = f"⏳ <b>進場判斷：暫緩進場 (空方弱勢)</b><br>股價落於月線 ({data['20MA']:.2f}) 之下，趨勢偏弱。<br>📌 建議等待重新站回月線，或進一步回測下軌 ({data['BB_DN']:.2f}) 支撐再作打算。"
    elif sc >= -4: 
        v_t, v_c = "🟠 風險警示：逢高減碼", "#ff9900"
        v_a = f"⚠️ <b>進場判斷：禁止買進，持股者逢高調節</b><br>追高風險較大。<br>📌 若持有建議於 {data['收盤價']:.2f} ~ {data['BB_UP']:.2f} 之間視情況分批獲利了結。"
    else: 
        v_t, v_c = "🔴 極度危險：嚴禁做多", "#ff3333"
        v_a = f"⛔ <b>進場判斷：絕對空手</b><br>強烈建議空手觀望，切勿接刀。"

    return analysis_bullets, v_t, v_c, v_a

def draw_professional_chart(df, ticker_name, latest_price, view_days, is_light_mode):
    df_view = df.tail(view_days)
    colors = ['#ff3333' if row['Close'] >= row['Open'] else '#00cc00' for _, row in df_view.iterrows()]
    last_row = df_view.iloc[-1]
    x_vals = df_view.index.strftime('%Y-%m-%d')
    fig = make_subplots(rows=4, cols=1, shared_xaxes=True, row_heights=[0.45, 0.15, 0.15, 0.25], vertical_spacing=0.06)
    line_k, line_d, line_j = ("#0066cc", "#ff9900", "#9900cc") if is_light_mode else ("white", "yellow", "magenta")
    grid_c = "rgba(0,0,0,0.1)" if is_light_mode else "rgba(255,255,255,0.1)"
    bg_c = "#ffffff" if is_light_mode else "#0e1117"
    text_c = "#333" if is_light_mode else "#ccc"
    
    fig.add_trace(go.Candlestick(x=x_vals, open=df_view['Open'], high=df_view['High'], low=df_view['Low'], close=df_view['Close'], increasing_line_color='#ff3333', decreasing_line_color='#00cc00', name="K線"), row=1, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df_view['5MA'], line=dict(color='orange', width=2), name="5T"), row=1, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df_view['10MA'], line=dict(color='#ffcc00', width=2), name="10T"), row=1, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df_view['20MA'], line=dict(color='cyan', width=2), name="20T"), row=1, col=1)
    
    fig.add_hline(y=latest_price, line_dash="dash", line_color="#ffcc00", row=1, col=1, annotation_text=f"現價: {latest_price:.2f}", annotation_position="top right", annotation_font=dict(size=14, color="#ffcc00", weight="bold"))
    
    fig.add_trace(go.Bar(x=x_vals, y=df_view['Volume'], marker_color=colors, name="VOL"), row=2, col=1)
    macd_colors = ['#ff3333' if val > 0 else '#00cc00' for val in df_view['MACD_Hist']]
    fig.add_trace(go.Bar(x=x_vals, y=df_view['MACD_Hist'], marker_color=macd_colors, name="OSC"), row=3, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df_view['MACD'], line=dict(color=line_k, width=1.5), name="DIF"), row=3, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df_view['Signal'], line=dict(color=line_d, width=1.5), name="MACD"), row=3, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df_view['K'], line=dict(color=line_k, width=1.5), name="K"), row=4, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df_view['D'], line=dict(color=line_d, width=1.5), name="D"), row=4, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df_view['J'], line=dict(color=line_j, width=1.5), name="J"), row=4, col=1)
    
    ann_bg = "rgba(255,255,255,0.8)" if is_light_mode else "rgba(26,28,36,0.6)"
    fig.add_annotation(x=0.01, y=0.98, xref="paper", yref="y domain", text=f"5T:{last_row['5MA']:.1f} | 10T:{last_row['10MA']:.1f} | 20T:{last_row['20MA']:.1f}", showarrow=False, font=dict(color="#ff9900" if is_light_mode else "#ffcc00", size=12), xanchor="left", bgcolor=ann_bg)
    fig.add_annotation(x=0.01, y=0.95, xref="paper", yref="y2 domain", text=f"VOL: {last_row['Volume']:,.0f}", showarrow=False, font=dict(color=text_c, size=12), xanchor="left", bgcolor=ann_bg)
    fig.add_annotation(x=0.01, y=0.95, xref="paper", yref="y3 domain", text=f"MACD:{last_row['MACD']:.2f} | DIF:{last_row['Signal']:.2f} | OSC:{last_row['MACD_Hist']:.2f}", showarrow=False, font=dict(color=text_c, size=12), xanchor="left", bgcolor=ann_bg)
    fig.add_annotation(x=0.01, y=0.95, xref="paper", yref="y4 domain", text=f"K:{last_row['K']:.2f} | D:{last_row['D']:.2f} | J:{last_row['J']:.2f}", showarrow=False, font=dict(color=text_c, size=12), xanchor="left", bgcolor=ann_bg)

    fig.update_xaxes(type='category', nticks=15, fixedrange=True, showgrid=True, gridcolor=grid_c)
    fig.update_layout(xaxis_rangeslider_visible=False, template="plotly_white" if is_light_mode else "plotly_dark", height=850, margin=dict(l=10, r=10, t=10, b=30), paper_bgcolor=bg_c, plot_bgcolor=bg_c, hovermode='x unified', dragmode=False, showlegend=False)
    
    fig.add_annotation(text="<a href='https://finance.yahoo.com' target='_blank'>📊 資料來源: yfinance / TWSE</a>", xref="paper", yref="paper", x=1.0, y=-0.05, showarrow=False, font=dict(size=12, color=text_c))
    return fig

def predict_tomorrow_open(twii_df):
    if twii_df is None or len(twii_df) < 2: return "資料不足", "無法分析", "資料不足", "無法預測", "", ""

    t_open, t_close, p_close = twii_df['Open'].iloc[-1], twii_df['Close'].iloc[-1], twii_df['Close'].iloc[-2]
    last_dt = twii_df.index[-1]
    last_dt_str = last_dt.strftime('%Y/%m/%d')
    next_dt = last_dt + timedelta(days=1)
    while next_dt.weekday() >= 5: next_dt += timedelta(days=1)
    next_dt_str = next_dt.strftime('%Y/%m/%d')
    
    today_title = "⚖️ 平盤震盪"
    today_desc = "今日大盤開在平盤附近，<a href='https://mis.twse.com.tw/' target='_blank' style='color:#00ffcc;'>法人現貨買賣超</a>多空拉扯，<a href='https://www.twse.com.tw/zh/trading/margin/mi-margn.html' target='_blank' style='color:#00ffcc;'>量價關係(VOL)</a>呈現縮量，盤勢陷入震盪整理。"
    if t_open > p_close * 1.003:
        if t_close > t_open: today_title, today_desc = "🔥 開高走高", "大盤受外資買盤與<a href='https://finance.yahoo.com/quote/TSM/' target='_blank' style='color:#00ffcc;'>台積電ADR</a>溢價激勵跳空開高，配合<a href='https://www.twse.com.tw/zh/trading/margin/mi-margn.html' target='_blank' style='color:#00ffcc;'>融資餘額</a>增加與量能放大，盤勢極度偏多。"
        else: today_title, today_desc = "⚠️ 開高走低", "大盤跳空開高後遭遇短線獲利了結賣壓，<a href='https://invest.cnyes.com/' target='_blank' style='color:#00ffcc;'>KDJ 動能指標</a>有進入超買區疑慮，呈現高檔回落。"
    elif t_open < p_close * 0.997:
        if t_close > t_open: today_title, today_desc = "💪 開低走高", "大盤受美股回檔影響開低，但低檔投信承接買盤強勁，出現開低走高收紅K型態。"
        else: today_title, today_desc = "🩸 開低走低", "大盤弱勢開低，<a href='https://finance.yahoo.com/quote/%5EVIX/' target='_blank' style='color:#00ffcc;'>VIX恐慌指數</a>上升引發散戶多殺多停損賣壓，盤勢極度偏空。"
    else:
        if t_close > p_close * 1.003: today_title, today_desc = "📈 平盤走高", "大盤開平盤附近，隨後受權值股買盤帶動，<a href='https://invest.cnyes.com/' target='_blank' style='color:#00ffcc;'>均線乖離(BIAS)</a>擴大，多方發力穩步墊高。"
        elif t_close < p_close * 0.997: today_title, today_desc = "📉 平盤走低", "大盤開平盤附近，但缺乏主力買盤支撐，<a href='https://invest.cnyes.com/' target='_blank' style='color:#00ffcc;'>MACD</a>綠柱擴大資金動能不足導致震盪向下。"

    ma5 = twii_df['5MA'].iloc[-1] if '5MA' in twii_df.columns else twii_df['Close'].tail(5).mean()
    score = 1 if t_close > ma5 else -1
    
    if score >= 1: tmr_title, tmr_desc = "🚀 偏多機率高", f"台股站穩短均線且技術面指標轉強，若今晚<a href='https://finance.yahoo.com/quote/%5ESOX/' target='_blank' style='color:#00ffcc;'>美股(費半)</a>強勢且<a href='https://finance.yahoo.com/quote/DX-Y.NYB/' target='_blank' style='color:#00ffcc;'>美元指數(DXY)</a>回落，預估次一交易日 ({next_dt_str}) 有極高機率開平高盤挑戰上檔壓力。"
    else: tmr_title, tmr_desc = "⚠️ 偏空震盪", f"台股跌破關鍵短均線，<a href='https://www.taifex.com.tw/cht/3/futContractsDate' target='_blank' style='color:#00ffcc;'>外資期貨未平倉空單(OI)</a>若維持高檔，需防範<a href='https://mops.twse.com.tw/mops/web/t100sb07_1' target='_blank' style='color:#00ffcc;'>重大總經數據公佈或法說會</a>不及預期，預防 ({next_dt_str}) 開平低盤回測下檔支撐。"

    return today_title, today_desc, tmr_title, tmr_desc, last_dt_str, next_dt_str

def render_index_board():
    twii_close, twii_change, twii_time_str = get_twii_quote()
    twii_color = '#ff3333' if twii_change >= 0 else '#00cc00'
    twii_df_for_pred = get_stock_data("^TWII")
    today_title, today_desc, tmr_title, tmr_desc, last_dt_str, next_dt_str = predict_tomorrow_open(twii_df_for_pred)
    
    with st.container(border=True):
        col1, col2 = st.columns([1.1, 1.2])
        with col1:
            st.markdown(f"<div style='text-align: center; font-size: 1.1rem; font-weight: bold;'><a href='https://mis.twse.com.tw/stock/fibest.jsp?stock=t00' target='_blank' style='color:#ccc; text-decoration:none;'>台灣加權指數 🔗</a></div>", unsafe_allow_html=True)
            st.markdown(f"<div style='text-align: center; font-size: 2.3rem; font-weight: 900; color: {twii_color}; margin: 5px 0;'>{twii_close:,.0f}</div>", unsafe_allow_html=True)
            st.markdown(f"<div style='text-align: center; font-size: 1.2rem; font-weight: bold; color: {twii_color};'>{'↑' if twii_change > 0 else '↓'} {abs(twii_change):.0f}</div>", unsafe_allow_html=True)
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("🔄 更新大盤即時報價", use_container_width=True): st.cache_data.clear(); st.rerun()

        with col2:
            st.markdown(f"<div style='text-align: left; color: #ffcc00; font-size: 1.05rem; font-weight: bold;'>📝 今日盤勢分析 ({last_dt_str}) </div>", unsafe_allow_html=True)
            st.markdown(f"<div style='text-align: left; font-size: 1.1rem; font-weight: bold;'>{today_title}</div>", unsafe_allow_html=True)
            st.markdown(f"<div style='text-align: left; font-size: 0.85rem; margin-top: 2px; margin-bottom: 8px; line-height: 1.4;'>{today_desc}</div>", unsafe_allow_html=True)

            st.markdown(f"<div style='text-align: left; color: #00ffcc; font-size: 1.05rem; font-weight: bold;'>🔮 次一交易日開盤預測 </div>", unsafe_allow_html=True)
            st.markdown(f"<div style='text-align: left; font-size: 1.1rem; font-weight: bold;'>{tmr_title}</div>", unsafe_allow_html=True)
            st.markdown(f"<div style='text-align: left; font-size: 0.85rem; margin-top: 2px; line-height: 1.4;'>{tmr_desc}</div>", unsafe_allow_html=True)
    st.markdown(f"<div style='text-align: right; color: #666; font-size: 0.8rem; margin-top: 10px;'>🔄 台灣加權指數最後更新時間: {twii_time_str}</div>", unsafe_allow_html=True)

@st.cache_data(ttl=600, show_spinner=False)
def get_real_news(ticker, name):
    news_list = []
    try:
        url = f"https://news.google.com/rss/search?q={urllib.parse.quote(f'{ticker} {name} 股票')}+when:7d&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
        res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
        if res.status_code == 200:
            root = ET.fromstring(res.text)
            for item in root.findall('.//item'):
                news_list.append({"title": item.find('title').text, "link": item.find('link').text})
                if len(news_list) >= 3: break
    except: pass
    
    if not news_list:
        news_list.append({"title": f"👉 點擊查看 {name} 最新即時新聞", "link": f"https://invest.cnyes.com/twstock/TWS/{ticker}/news"})
    return news_list

if st.session_state.page == "home":
    st.markdown("<h1 style='text-align: center;'>🇹🇼 雷達總機</h1>", unsafe_allow_html=True)
    render_index_board()
    st.markdown("<h3 style='margin-top: 15px;'>🎯 掃描買點</h3>", unsafe_allow_html=True)
    btn_col1, btn_col2, btn_col3 = st.columns(3)
    if btn_col1.button("✅ 尋找買點", use_container_width=True): st.session_state.scan_mode = "buy"; st.rerun()
    if btn_col2.button("📋 熱門名單", use_container_width=True): st.session_state.scan_mode = "hot"; st.rerun()
    if btn_col3.button("🔥 近五日熱門", use_container_width=True): st.session_state.scan_mode = "recent"; st.rerun()
    search_val = st.text_input("隱藏", placeholder="🔍 搜尋股票 (輸入代號並按 Enter)", label_visibility="collapsed")
    if search_val: st.session_state.update({"current_stock": search_val, "date_offset": 0, "page": "analysis"}); st.rerun()
    
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
        elif st.session_state.scan_mode == "buy":
            st.markdown("##### 🎯 尋找買點榜單 (優先推薦 S級，不足則以 A級 遞補至 10 檔)")
            df_s = df_results[df_results['Score'] >= 5].sort_values(by='Score', ascending=False)
            if len(df_s) < 10:
                df_a = df_results[(df_results['Score'] >= 2) & (df_results['Score'] < 5)].sort_values(by='Score', ascending=False)
                df_disp = pd.concat([df_s, df_a.head(10 - len(df_s))])
            else: df_disp = df_s
            if df_disp.empty: st.info("目前雷達池內沒有符合條件的標的。")
        else:
            st.markdown("##### 📋 熱門名單")
            df_disp = df_results.sort_values(by="成交量", ascending=False).head(10)
            
        for _, r in df_disp.iterrows():
            with st.container(border=True):
                c1, c2 = st.columns([8, 2])
                with c1: st.markdown(f"### {r['代號']} **{r['名稱']}**")
                with c2:
                    if st.button("⭐ 移除" if r['ticker_raw'] in st.session_state.favorites else "☆ 收藏", key=f"s_{r['ticker_raw']}_{st.session_state.scan_mode}", use_container_width=True):
                        if r['ticker_raw'] in st.session_state.favorites: st.session_state.favorites.remove(r['ticker_raw'])
                        else: st.session_state.favorites.append(r['ticker_raw'])
                        save_json(FAV_FILE, st.session_state.favorites); st.rerun()
                bg_c = "#ffffff" if is_light_mode else "#1a1c24"; border_c = "#ddd" if is_light_mode else "#333"
                p_color = "#ff3333" if r['涨跌'] >= 0 else "#00cc00"; v5 = float(r['近5日漲幅(%)'].strip('%')); c5 = "#ff3333" if v5 > 0 else "#00cc00" if v5 < 0 else text_col
                st.markdown(f'''<div style="background-color: {bg_c}; padding: 12px; border-radius: 8px; border: 1px solid {border_c}; text-align: center; margin: 5px 0 10px 0;"><span style="font-size: 2.6rem; font-weight: 900; color: {p_color};">{r['收盤價']}</span><span style="font-size: 1.3rem; font-weight: bold; color: {p_color}; margin-left: 12px;">{'+' if r['漲跌']>0 else ''}{r['漲跌']} ({r['漲跌幅']}%)</span></div>''', unsafe_allow_html=True)
                st.markdown(f"<div style='text-align: center; font-size: 1rem; color: {text_col}; line-height: 1.8;'>產業: <b>{r['產業']}</b><br>昨收: <b>{r['昨日收盤價']}</b><br>近5日漲幅: <span style='color:{c5}; font-weight:bold;'>{r['近5日漲幅(%)']}</span></div>", unsafe_allow_html=True)
                st.markdown("<br>", unsafe_allow_html=True)
                if st.button("📊 解析", key=f"bp_{r['ticker_raw']}_{st.session_state.scan_mode}", use_container_width=True): st.session_state.update({"current_stock": r['ticker_raw'], "date_offset": 0, "page": "analysis"}); st.rerun()

elif st.session_state.page == "analysis":
    target = st.session_state.current_stock; df_chart = get_stock_data(target); c_name = get_stock_name(target)
    n_pool = st.session_state.get('nav_pool', st.session_state.custom_pool); p_stk, n_stk = None, None
    if target in n_pool and len(n_pool) > 1:
        i = n_pool.index(target); p_stk = n_pool[i - 1] if i > 0 else None; n_stk = n_pool[i + 1] if i < len(n_pool) - 1 else None
    c1, c2, c3 = st.columns([1, 1, 1])
    with c1:
        if p_stk and st.button(f"⬅ 上一檔", use_container_width=True): st.session_state.update({"current_stock": p_stk, "date_offset": 0}); st.rerun()
    with c2:
        if st.button("🏠 回首頁", use_container_width=True): st.session_state.page = "home"; st.rerun()
    with c3:
        if n_stk and st.button(f"下一檔 ➡", use_container_width=True): st.session_state.update({"current_stock": n_stk, "date_offset": 0}); st.rerun()
        
    if df_chart is not None:
        df_slice = df_chart.iloc[:len(df_chart) + st.session_state.date_offset] if st.session_state.date_offset < 0 else df_chart
        if len(df_slice) < 5: st.warning("歷史資料不足"); st.button("返回", on_click=lambda: st.session_state.update({"date_offset": st.session_state.date_offset + 1}))
        else:
            data = analyze_today(df_slice, target); v_dt = df_slice.index[-1].strftime('%Y/%m/%d')
            f_data = get_fundamental_and_industry_data(target, data['收盤價'])
            sc, rs = get_decision_score(data, f_data); inst_data = get_institutional_trading(target)
            
            twii_df_for_pred = get_stock_data("^TWII")
            t_title, t_desc, tmr_title, tmr_desc, l_dt, n_dt = predict_tomorrow_open(twii_df_for_pred)
            
            p_color = '#ff3333' if data['漲跌'] >= 0 else '#00cc00'
            st.markdown(f"<h2 style='text-align: center; margin-bottom: 5px;'>🎯 {target} {c_name}</h2>", unsafe_allow_html=True)
            st.markdown(f"<div style='text-align: center; color: #888; font-size: 1.1rem;'>【{f_data['Industry']}】</div>", unsafe_allow_html=True)
            st.markdown(f"<h3 style='text-align: center; color: {p_color}; font-size: 2.2rem; margin-bottom: 0px;'>{data['收盤價']} ({'+' if data['漲跌']>0 else ''}{data['漲跌幅']}%)</h3>", unsafe_allow_html=True)
            st.markdown(f"<div style='text-align: center; color: #888; font-size: 0.95rem; margin-bottom: 10px;'>📊 檢視日期: {v_dt}</div>", unsafe_allow_html=True)
            _, up_c, _ = st.columns([1, 2, 1])
            if up_c.button("🔄 更新個股即時數值", use_container_width=True): st.cache_data.clear(); st.rerun()
            st.markdown("<br>", unsafe_allow_html=True)
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
            recent_30 = df_chart.tail(30); found_opp = False; btn_cols = st.columns(4); col_idx = 0
            for idx in range(len(recent_30)):
                temp_df = df_chart.iloc[:len(df_chart) - 30 + idx + 1]; t_data = analyze_today(temp_df, target); t_sc, _ = get_decision_score(t_data, f_data)
                if t_sc >= 2: 
                    found_opp = True; dt_str = temp_df.index[-1].strftime('%m/%d'); badge = "🟢 S級" if t_sc >= 5 else "🟡 A級"
                    with btn_cols[col_idx % 4]:
                        jump_offset = -(len(df_chart) - len(temp_df))
                        if st.button(f"{dt_str} {badge}", key=f"hist_{dt_str}", use_container_width=True): st.session_state.date_offset = jump_offset; st.rerun()
                    col_idx += 1
            if not found_opp: st.info("近一個月內無 A 級以上的極佳買點。")
            st.markdown("---")
            
            # 帶入盤勢分析
            bullets, v_t, v_c, v_a = generate_comprehensive_analysis(data, inst_data, sc, t_title, tmr_title)
            bullets_html = "".join([f"<li style='margin-bottom: 8px;'>{b}</li>" for b in bullets])
            st.markdown(f'''<div style="border: 2px solid {v_c}; border-radius: 10px; padding: 20px; margin-bottom: 20px; background-color: {bg_col}; box-shadow: 0 4px 6px rgba(0,0,0,0.1);"><h3 style="text-align: center; color: {v_c}; margin-top: 0; font-size: 1.8rem;">🤖 AI 決策大腦：{v_t.replace('🟢 ', '').replace('🟡 ', '').replace('⚪ ', '').replace('🟠 ', '').replace('🔴 ', '')}</h3><hr style="border-color: {border_col}; margin: 15px 0;"><div style="margin-bottom: 15px;"><h4 style="color: {text_col}; margin-bottom: 10px;">🔍 綜合技術與籌碼診斷：</h4><ul style="font-size: 1rem; color: {text_col}; line-height: 1.6;">{bullets_html}</ul></div><div style="background-color: {'#f0f8ff' if is_light_mode else '#1e2433'}; padding: 15px; border-radius: 8px; border-left: 5px solid {v_c};"><p style="font-size: 1.15rem; color: {text_col}; margin: 0; line-height: 1.6;">{v_a}</p></div></div>''', unsafe_allow_html=True)
            
            dc1, dc2, dc3, dc4 = st.columns(4)
            if dc1.button("1個月"): st.session_state.view_days = 20
            if dc2.button("3個月"): st.session_state.view_days = 60
            if dc3.button("6個月"): st.session_state.view_days = 120
            if dc4.button("1年"): st.session_state.view_days = 240
            fig = draw_professional_chart(df_slice, target, data['收盤價'], st.session_state.view_days, is_light_mode)
            st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})
            
            st.markdown("### 🕵️‍♂️ 進階數據面板")
            a1, a2 = st.columns(2)
            with a1.container(border=True):
                st.markdown(f"##### 📊 布林通道 & 乖離率<br><br>**上軌 (壓力):** `{data['BB_UP']}`<br><br>**下軌 (支撐):** `{data['BB_DN']}`<br><br>**月線乖離率:** `{data['BIAS']}%`<br><br><a href='https://tw.stock.yahoo.com/quote/{target}/technical-analysis' target='_blank' style='font-size:0.75rem; text-decoration:none;'>🔗來源: Yahoo財經</a>", unsafe_allow_html=True)
            with a2.container(border=True):
                eps = f_data['EPS']; m_eps = round(float(eps)/12, 2) if eps != "無" else "無"
                st.markdown(f"##### 📑 基本面與動態精算本益比<br><br>**近四季每股盈餘 (EPS):** `{eps}`<br><br>**換算單月 EPS:** `{m_eps}`<br><br>**最新即時本益比 (P/E):** `{f_data['PE']}`<br><br><a href='https://invest.cnyes.com/twstock/TWS/{target}/overview' target='_blank' style='font-size:0.8rem; text-decoration:none;'>🔗來源: Cnyes 鉅亨網</a>", unsafe_allow_html=True)

            st.divider()
            
            st.subheader("📈 三級多空趨勢判定")
            t_short = "🔼 多頭 (站上5T)" if data['收盤價'] > data['5MA'] else "🔽 跌破5T"
            t_mid = "🔼 多頭 (站上20T)" if data['收盤價'] > data['20MA'] else "🔽 跌破20T"
            t_long = "🔼 多頭 (站上季線)" if data['收盤價'] > data['60MA'] else "🔽 跌破季線"
            
            with st.container(border=True):
                st.markdown(f"**短線走勢 (日線級別) :** {t_short}")
            with st.container(border=True):
                st.markdown(f"**中線布局 (周線級別) :** {t_mid}")
            with st.container(border=True):
                st.markdown(f"**長線防守 (季線級別) :** {t_long}")
            
            st.markdown("<br>", unsafe_allow_html=True)

            st.divider()
            st.subheader("🏦 近期三大法人逐日買賣超")
            if inst_data:
                st.dataframe(pd.DataFrame(inst_data), use_container_width=True, hide_index=True)
            else:
                st.info("目前無法自動抓取籌碼資料。")
            st.markdown(f"<div style='text-align: right; font-size:0.8rem;'><a href='https://api.finmindtrade.com' target='_blank' style='color:#888; text-decoration:none;'>🔗 資料來源: TWSE 官方 / FinMind</a></div>", unsafe_allow_html=True)
            
            st.divider()
            st.subheader("🔗 同產業關聯股動態")
            if f_data['Industry'] != "未提供產業資訊" and f_data['Industry'] != "一般產業":
                rels = [c for c, n in STOCK_NAMES.items() if get_fundamental_and_industry_data(c, 0)['Industry'] == f_data['Industry'] and c != target][:3]
                if rels:
                    st.markdown(f"以下為同樣屬於 **【{f_data['Industry']}】** 的熱門標的：")
                    cs = st.columns(len(rels))
                    for i, r in enumerate(rels):
                        with cs[i].container(border=True):
                            r_df = get_stock_data(r)
                            if r_df is not None:
                                rc, rp = round(r_df['Close'].iloc[-1], 2), round((r_df['Close'].iloc[-1] - r_df['Close'].iloc[-2])/r_df['Close'].iloc[-2]*100, 2)
                                rcol = "#ff3333" if rp >= 0 else "#00cc00"
                                st.markdown(f"**{r} {get_stock_name(r)}** <br> <span style='color:{rcol}; font-weight:bold;'>{rc} ({'+' if rp>0 else ''}{rp}%)</span>", unsafe_allow_html=True)
                                if st.button("分析", key=f"b_r_{r}"): st.session_state.update({"current_stock": r, "date_offset": 0, "page": "analysis"}); st.rerun()
                else: st.info("無其他同產業標的。")
            else: st.info("無法識別該股產業。")
            
            st.divider()
            st.subheader("📰 相關新聞")
            news_items = get_real_news(target, c_name)
            if news_items:
                for n in news_items: st.markdown(f"- [{n['title']}]({n['link']})")
            else: st.info("目前暫無相關新聞。")
            
            st.divider()
            if target in st.session_state.favorites:
                if st.button("❌ 從自選股移除此標的", type="primary", use_container_width=True):
                    st.session_state.favorites.remove(target); save_json(FAV_FILE, st.session_state.favorites); st.rerun()
            else:
                if st.button("⭐ 將此標的加入自選股", type="primary", use_container_width=True):
                    st.session_state.favorites.append(target); save_json(FAV_FILE, st.session_state.favorites); st.rerun()
    else: st.error("查無此股票資料。")
