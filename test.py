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
from bs4 import BeautifulSoup
import re
import concurrent.futures  # 🚀 引入多執行緒模組加速網頁讀取

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
    [data-testid="collapsedControl"]::after {{ content: " ⭐ 我的群組"; font-size: 1.1rem; font-weight: bold; color: #ffcc00; margin-left: 8px; }}
    .stButton button {{ font-weight: bold !important; border-radius: 8px !important; }}
    button[kind="primary"] {{ font-size: 1.5rem !important; padding: 15px !important; border-radius: 12px !important; background-color: #ffcc00 !important; color: #111 !important; border: none !important; }}
    .sticky-header {{ position: sticky; top: 0; z-index: 999; background-color: {sticky_bg}; padding: 10px 0; border-bottom: 1px solid {border_col}; backdrop-filter: blur(5px); margin-top: -15px; margin-bottom: 15px; }}
    div[data-testid="stVerticalBlockBorderWrapper"] > div {{ background-color: {bg_col} !important; border-color: {border_col} !important; padding: 4px !important; }}
    h1, h2, h3, h4, p, span {{ color: {title_col} !important; }}
    .compact-btn button {{ padding: 0.25rem 0.5rem !important; font-size: 1rem !important; }}
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
if 'scan_mode' not in st.session_state: st.session_state.scan_mode = "hot"
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
def fetch_twse_top_50():
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", timeout=10)
        df = pd.DataFrame(res.json())
        df['TradeVolume'] = pd.to_numeric(df['TradeVolume'], errors='coerce')
        return df[df['Code'].str.match(r'^\d{4}$')].sort_values(by='TradeVolume', ascending=False).head(50)['Code'].tolist()
    except: return ["2330", "2317", "2454", "2382", "3231"]

st.sidebar.divider()
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
            else: st.error("至少需保留一個群組！")
                
        for fav in g_stocks:
            if st.button(f"📊 {fav} {get_stock_name(fav)}", key=f"go_{g_name}_{fav}", use_container_width=True):
                st.session_state.update({"current_stock": fav, "page": "analysis", "date_offset": 0})
                st.rerun()

st.sidebar.divider()
st.sidebar.title("⚙️ 雷達池設定")
if st.sidebar.button("🔄 更新熱門股 (Top 50)", use_container_width=True):
    st.session_state.custom_pool = fetch_twse_top_50()
    save_json(POOL_FILE, st.session_state.custom_pool)
    st.sidebar.success("✅ 完成！")
    st.rerun()

@st.cache_data(ttl=3600, show_spinner=False)
def get_fundamental_and_industry_data(ticker_number, current_price=0):
    base_ticker = str(ticker_number).strip().upper().replace(".TW", "").replace(".TWO", "")
    eps_val, pe_val = "無", "無"
    ind = "一般產業"
    try:
        url = f"https://invest.cnyes.com/twstock/TWS/{base_ticker}/overview"
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
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

@st.cache_data(ttl=5, show_spinner=False) 
def get_twii_quote():
    tz_tpe = timezone(timedelta(hours=8))
    update_time_str = datetime.now(tz_tpe).strftime('%Y/%m/%d %H:%M:%S')
    fallback_curr, fallback_change = 0, 0
    try:
        df = yf.Ticker("^TWII").history(period="5d")
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

@st.cache_data(ttl=60, show_spinner=False) 
def get_stock_data(ticker_number):
    base_ticker = str(ticker_number).strip().upper().replace(".TW", "").replace(".TWO", "")
    def fetch_clean(sym):
        try:
            d = yf.Ticker(sym).history(period="1y")
            if d is not None and not d.empty:
                d = d.dropna(subset=['Close'])
                if not sym.startswith('^'): d = d[d['Volume'] > 0] 
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

def get_decision_score(data, fund_data, inst_data=None):
    sc, rs = 0, []
    if data['訊號']: sc+=3; rs.append("✅ 穩在月線上且KDJ超賣")
    if data['收盤價'] <= data['BB_DN'] * 1.02: sc+=2; rs.append("✅ 觸及布林下軌")
    if data['BIAS'] < -5: sc+=1; rs.append("✅ 負乖離擴大")
    
    try: eps_f = float(str(fund_data['EPS']).replace(',', ''))
    except: eps_f = 0.0
    if eps_f > 0: sc+=2; rs.append("✅ 基本面獲利")
    
    # 🛡️ 濾網 1：量能放大確認 (消滅假支撐)
    if data.get('成交量', 0) > data.get('5日均量', 0) * 1.1: 
        sc+=2; rs.append("✅ 量能放大 (具備主力進場點火特徵)")
    else: 
        sc-=2; rs.append("⚠️ 無量反彈 (缺乏實質買盤動能)")
        
    # 🛡️ 濾網 2：MACD 綠柱收斂確認 (防範下墜接刀)
    if data.get('MACD柱', 0) > data.get('前日MACD柱', -999):
        sc+=2; rs.append("✅ MACD 綠柱收斂或紅柱放大 (波段動能防禦過關)")
    else:
        sc-=3; rs.append("⚠️ MACD 空方動能持續擴大 (嚴防型態破壞接刀)")

    # 🛡️ 濾網 3：近三日法人累積籌碼
    if inst_data and len(inst_data) >= 3:
        net_buy = sum([int(str(x['單日合計(張)']).replace(',', '')) for x in inst_data[:3] if str(x['單日合計(張)']).replace(',', '').lstrip('-').isdigit()])
        if net_buy > 0: sc+=2; rs.append(f"✅ 法人近三日偏多 (累計買超 {net_buy} 張)")
        else: sc-=2; rs.append(f"⚠️ 法人近三日偏空 (累計賣超 {abs(net_buy)} 張)")

    if data['J值'] >= 80: sc-=3; rs.append("⚠️ KDJ高檔過熱")
    if data['收盤價'] >= data['BB_UP'] * 0.98: sc-=2; rs.append("⚠️ 觸及布林上軌")
    if data['BIAS'] > 7: sc-=2; rs.append("⚠️ 正乖離過大")
    if data['收盤價'] < data['20MA']: sc-=2; rs.append("⚠️ 跌破月線支撐")
    if eps_f < 0: sc-=1; rs.append("⚠️ 基本面虧損")
    
    return sc, rs

def analyze_today(df, ticker_number, inst_data=None):
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
        "MACD": round(t['MACD'], 2), "MACD柱": round(t['MACD_Hist'], 3), "前日MACD柱": round(p['MACD_Hist'], 3),
        "K": round(t['K'], 2), "D": round(t['D'], 2), "J值": round(t['J'], 2),
        "訊號": (t['Close'] > t['20MA']) and (t['Close'] < t['5MA']) and (t['J'] < 20)
    }
    sc, rs = get_decision_score(data, fund, inst_data)
    data['Score'] = sc
    data['Reasons'] = rs
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
    
    if data['成交量'] > data['5日均量'] * 1.1 and data['漲跌'] > 0: analysis_bullets.append(f"🔥 <span style='color:#ff3333; font-weight:bold;'>量價確認：今日量能放大大於5日均量，主力進場點火信號明確。</span>")
    elif data['成交量'] < data['5日均量']: analysis_bullets.append(f"⚠️ <span style='color:#00cc00;'>量能警訊：反彈或震盪中「量能萎縮」，需防範缺乏真金白銀支撐的虛假反彈。</span>")
        
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
        v_a = f"✅ <b>進場判斷：強烈買進</b><br>動能、量能、籌碼三道關卡全數確認過關！<br>📌 建議建倉區間：{data['BB_DN']:.2f} ~ {data['20MA']:.2f} 之間分批加碼。"
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

def draw_professional_chart(df, ticker_name, latest_price, view_days, is_light_mode, show_buy_signal=False, f_data=None, show_sup_res=False):
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
    
    fig.add_hline(y=latest_price, line_dash="dash", line_color="#ffcc00", row=1, col=1)
    
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
                    buy_y.append(df_view['Low'].iloc[i] * 0.97)
                    buy_text.append("買")
        if buy_x:
            fig.add_trace(go.Scatter(x=buy_x, y=buy_y, mode='markers+text', marker=dict(symbol='triangle-up', size=14, color='#00ffcc' if not is_light_mode else '#0066cc'), text=buy_text, textposition="bottom center", textfont=dict(color="#00ffcc" if not is_light_mode else '#0066cc', size=11, weight="bold"), name="買進訊號", hoverinfo='x'), row=1, col=1)
            
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
    return fig

def predict_tomorrow_open(twii_df, twii_time_str=""):
    if twii_df is None or len(twii_df) < 2: return "資料不足", "無法分析", "資料不足", "無法預測", "", ""
    t_open, t_close, p_close = twii_df['Open'].iloc[-1], twii_df['Close'].iloc[-1], twii_df['Close'].iloc[-2]
    tz_tpe = timezone(timedelta(hours=8))
    now = datetime.now(tz_tpe)
    last_dt = datetime.strptime(twii_time_str.split(" ")[0], '%Y/%m/%d') if twii_time_str and "/" in twii_time_str else now
    if last_dt.weekday() == 5: last_dt -= timedelta(days=1)
    elif last_dt.weekday() == 6: last_dt -= timedelta(days=2)
    next_dt = last_dt + timedelta(days=3 if last_dt.weekday() == 4 else 1)
    
    today_title, today_desc = "⚖️ 平盤震盪", "今日大盤多空拉扯，盤勢陷入震盪整理。"
    if t_open > p_close * 1.003:
        if t_close > t_open: today_title, today_desc = "🔥 開高走高", "大盤受買盤激勵跳空開高，盤勢極度偏多。"
        else: today_title, today_desc = "⚠️ 開高走低", "大盤跳空開高後遭遇短線獲利了結賣壓，高檔回落。"
    elif t_open < p_close * 0.997:
        if t_close > t_open: today_title, today_desc = "💪 開低走高", "大盤低檔承接買盤強勁，出現開低走高收紅K型態。"
        else: today_title, today_desc = "🩸 開低走低", "大盤弱勢開低，引發多殺多停損賣壓，盤勢極度偏空。"

    score = 1 if t_close > twii_df['Close'].tail(5).mean() else -1
    if score >= 1: tmr_title, tmr_desc = "🚀 偏多機率高", f"台股站穩短均線且技術面指標轉強，預估次一交易日有極高機率開平高盤。"
    else: tmr_title, tmr_desc = "⚠️ 偏空震盪", f"台股跌破關鍵短均線，預防次一交易日開平低盤回測下檔支撐。"
    return today_title, today_desc, tmr_title, tmr_desc, last_dt.strftime('%Y/%m/%d'), next_dt.strftime('%Y/%m/%d')

def render_index_board():
    try:
        twii_close, twii_change, twii_time_str = get_twii_quote()
        twii_color = '#ff3333' if twii_change >= 0 else '#00cc00'
        twii_df_for_pred = get_stock_data("^TWII")
        today_title, today_desc, tmr_title, tmr_desc, last_dt_str, next_dt_str = predict_tomorrow_open(twii_df_for_pred, twii_time_str)
        with st.container(border=True):
            col1, col2 = st.columns([1.1, 1.2])
            with col1:
                st.markdown(f"<div style='text-align: center; font-size: 1.1rem; font-weight: bold;'>台灣加權指數 🔗</div>", unsafe_allow_html=True)
                st.markdown(f"<div style='text-align: center; font-size: 2.3rem; font-weight: 900; color: {twii_color}; margin: 5px 0;'>{twii_close:,.0f}</div>", unsafe_allow_html=True)
                st.markdown(f"<div style='text-align: center; font-size: 1.2rem; font-weight: bold; color: {twii_color};'>{'↑' if twii_change > 0 else '↓'} {abs(twii_change):.0f}</div>", unsafe_allow_html=True)
                if st.button("🔄 更新大盤即時報價", use_container_width=True): st.cache_data.clear(); st.rerun()
            with col2:
                st.markdown(f"<div style='text-align: left; color: #ffcc00; font-size: 1.05rem; font-weight: bold;'>📝 今日盤勢分析 ({last_dt_str})</div>", unsafe_allow_html=True)
                st.markdown(f"<div style='text-align: left; font-size: 1.1rem; font-weight: bold;'>{today_title}</div>", unsafe_allow_html=True)
                st.markdown(f"<div style='text-align: left; font-size: 0.85rem; margin-top: 2px; margin-bottom: 8px;'>{today_desc}</div>", unsafe_allow_html=True)
                st.markdown(f"<div style='text-align: left; color: #00ffcc; font-size: 1.05rem; font-weight: bold;'>🔮 次一交易日開盤預測</div>", unsafe_allow_html=True)
                st.markdown(f"<div style='text-align: left; font-size: 1.1rem; font-weight: bold;'>{tmr_title}</div>", unsafe_allow_html=True)
    except: st.error(f"大盤資料載入發生錯誤。")

if st.session_state.page == "home":
    st.markdown("<h1 style='text-align: center;'>🇹🇼 雷達總機</h1>", unsafe_allow_html=True)
    render_index_board()
    st.markdown("<h3 style='margin-top: 15px;'>🎯 掃描買點</h3>", unsafe_allow_html=True)
    btn_col1, btn_col2, btn_col3 = st.columns(3)
    if btn_col1.button("✅ 尋找買點", use_container_width=True): st.session_state.scan_mode = "buy"; st.rerun()
    if btn_col2.button("📋 熱門名單", use_container_width=True): st.session_state.scan_mode = "hot"; st.rerun()
    if btn_col3.button("🔥 近五日熱門", use_container_width=True): st.session_state.scan_mode = "recent"; st.rerun()
    search_val = st.text_input("隱藏", placeholder="🔍 搜尋股票 (輸入代號並按 Enter)", label_visibility="collapsed")
    if search_val: st.session_state.update({"current_stock": search_val, "page": "analysis"}); st.rerun()
    
    scan_results = []
    with st.spinner('🚀 大腦極速掃描中 (已啟動多執行緒併發)...'):
        pool = list(set(st.session_state.custom_pool + ["2330", "2317", "2454", "2308", "2382", "2603", "2881", "2409"]))
        def process_scan(stock):
            df = get_stock_data(stock)
            if df is not None:
                # 掃描大清單時傳入 None，各股進階頁再細抓籌碼，維持首頁最快加載速度
                return analyze_today(df, stock, inst_data=None) 
            return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            results = executor.map(process_scan, pool)
            for res in results:
                if res: scan_results.append(res)
            
    if scan_results:
        df_results = pd.DataFrame(scan_results)
        if st.session_state.scan_mode == "recent":
            st.markdown("##### 🔥 近五日熱門排行榜")
            df_disp = df_results.sort_values(by="成交量", ascending=False).head(20)
        elif st.session_state.scan_mode == "buy":
            st.markdown("##### 🎯 尋找買點榜單 (已掛載量能與MACD動能濾網)")
            df_s = df_results[df_results['Score'] >= 5].sort_values(by='Score', ascending=False)
            df_a = df_results[(df_results['Score'] >= 2) & (df_results['Score'] < 5)].sort_values(by='Score', ascending=False)
            df_disp = pd.concat([df_s, df_a]).head(10)
            if df_disp.empty: st.info("目前雷達池內沒有符合條件的標的。")
        else:
            st.markdown("##### 📋 熱門名單")
            df_disp = df_results.sort_values(by="成交量", ascending=False).head(50)
            
        for _, r in df_disp.iterrows():
            with st.container(border=True):
                p_val = r['漲跌']
                trend_icon = "🔺" if p_val > 0 else ("🔻" if p_val < 0 else "➖")
                sign = "+" if p_val > 0 else ""
                s_score = r['Score']
                score_icon = "🟢 S級" if s_score >= 5 else ("🟡 A級" if s_score >= 2 else "")
                if st.button(f"{r['代號']} {r['名稱']}  │  {trend_icon} {r['收盤價']} ({sign}{r['漲跌幅']}%)  {score_icon}", key=f"name_{r['ticker_raw']}_{st.session_state.scan_mode}", use_container_width=True):
                    st.session_state.update({"current_stock": r['ticker_raw'], "page": "analysis", "date_offset": 0})
                    st.rerun()

elif st.session_state.page == "analysis":
    target = st.session_state.current_stock
    df_chart = get_stock_data(target)
    c_name = get_stock_name(target)
    
    n_pool = st.session_state.get('nav_pool', st.session_state.custom_pool)
    p_stk, n_stk = None, None
    if target in n_pool and len(n_pool) > 1:
        i = n_pool.index(target)
        p_stk = n_pool[i - 1] if i > 0 else None
        n_stk = n_pool[i + 1] if i < len(n_pool) - 1 else None

    c1, c2, c3 = st.columns([1, 1, 1])
    with c1:
        if p_stk and st.button(f"⬅ 上一檔", use_container_width=True): st.session_state.update({"current_stock": p_stk}); st.rerun()
    with c2:
        if st.button("🏠 回首頁", use_container_width=True): st.session_state.page = "home"; st.rerun()
    with c3:
        if n_stk and st.button(f"下一檔 ➡", use_container_width=True): st.session_state.update({"current_stock": n_stk}); st.rerun()
        
    if df_chart is not None:
        df_slice = df_chart.iloc[:len(df_chart) + st.session_state.date_offset] if st.session_state.date_offset < 0 else df_chart
        if len(df_slice) < 5: 
            st.warning("歷史資料不足")
        else:
            inst_data = get_institutional_trading(target)
            data = analyze_today(df_slice, target, inst_data)
            v_dt = df_slice.index[-1].strftime('%Y/%m/%d')
            f_data = get_fundamental_and_industry_data(target, data['收盤價'])
            sc = data['Score']
            
            twii_close, twii_change, twii_time_str = get_twii_quote()
            twii_df_for_pred = get_stock_data("^TWII")
            t_title, t_desc, tmr_title, tmr_desc, l_dt, n_dt = predict_tomorrow_open(twii_df_for_pred, twii_time_str)
            
            display_time = get_stock_live_time(target)
            p_color = '#ff3333' if data['漲跌'] >= 0 else '#00cc00'
            st.markdown(f"<h2 style='text-align: center; margin-bottom: 5px;'>🎯 {target} {c_name}</h2>", unsafe_allow_html=True)
            st.markdown(f"<div style='text-align: center; color: #888; font-size: 1.1rem;'>【{f_data['Industry']}】</div>", unsafe_allow_html=True)
            st.markdown(f"<h3 style='text-align: center; color: {p_color}; font-size: 2.2rem; margin-bottom: 0px;'>{data['收盤價']} ({'+' if data['漲跌']>0 else ''}{data['漲跌幅']}%)</h3>", unsafe_allow_html=True)
            st.markdown(f"<div style='text-align: center; color: #888; font-size: 0.95rem; margin-bottom: 10px;'>📊 檢視日期與時間: [{display_time}]</div>", unsafe_allow_html=True)
            
            _, up_c, _ = st.columns([1, 2, 1])
            if up_c.button("🔄 更新個股即時數值", use_container_width=True): st.cache_data.clear(); st.rerun()
            st.markdown("---")
            
            # ==========================================
            # 🚨 【1. 補回：5% 嚴格停損防護防線警報】
            # ==========================================
            stop_loss_html = ""
            recent_20 = df_slice.tail(20)
            recent_signals = []
            for idx in range(len(recent_20)):
                temp_df = df_slice.iloc[:len(df_slice) - 20 + idx + 1]
                if len(temp_df) >= 5:
                    t_data = analyze_today(temp_df, target, inst_data=None) # 傳入 None 避免極速回測卡網速
                    if t_data and t_data['Score'] >= 2:
                        recent_signals.append((temp_df.index[-1], t_data['收盤價']))
            
            if recent_signals:
                last_sig_date, last_sig_price = recent_signals[-1]
                if data['收盤價'] <= last_sig_price * 0.95:
                    loss_pct = (data['收盤價'] - last_sig_price) / last_sig_price * 100
                    stop_loss_html = f'''<div style="background-color: #ffe6e6; border-left: 6px solid #ff3333; padding: 15px; margin-bottom: 20px; border-radius: 4px;">
                    <h4 style="color: #ff3333; margin-top: 0; font-size: 1.3rem;">🚨 【嚴格停損警報】觸發 5% 停損防護線</h4>
                    <span style="color: #333; font-size: 1.05rem; line-height: 1.6;">系統偵測到最近一次策略買訊 ({last_sig_date.strftime('%Y/%m/%d')}) 基準成本為 <b>{last_sig_price:.2f}</b>。<br>
                    目前現價 <b>{data['收盤價']}</b> 已跌穿 5% 鐵律防護線 (預估帳面分歧為 <span style="color:#ff3333; font-weight:bold;">{loss_pct:.2f}%</span>)。<br>
                    <b>防範警訊：中線趨勢支撐已破，強烈建議嚴守交易紀律，果斷停損出場觀望，切勿盲目攤平接刀！</b></span>
                    </div>'''
            if stop_loss_html: st.markdown(stop_loss_html, unsafe_allow_html=True)

            # ==========================================
            # 📊 【2. 補回：近一個月歷史買點回測與趨勢分析貨櫃】
            # ==========================================
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
                        if t_data['Score'] >= 5:
                            s_count += 1
                            buy_points_prices.append(t_data['收盤價'])
                        elif t_data['Score'] >= 2:
                            a_count += 1
                            buy_points_prices.append(t_data['收盤價'])
            
            with st.container(border=True):
                col_sum1, col_sum2, col_sum3 = st.columns(3)
                with col_sum1: st.markdown(f"<div style='text-align:center;'>近一月趨勢<br><span style='color:{trend_color}; font-size:1.6rem; font-weight:900;'>{trend_text} {sign_t}{month_trend_pct:.2f}%</span></div>", unsafe_allow_html=True)
                with col_sum2: st.markdown(f"<div style='text-align:center;'>🟢 S級 強烈買進<br><span style='font-size:1.6rem; font-weight:900; color:#00cc00;'>{s_count} 次</span></div>", unsafe_allow_html=True)
                with col_sum3: st.markdown(f"<div style='text-align:center;'>🟡 A級 偏多試單<br><span style='font-size:1.6rem; font-weight:900; color:#ffcc00;'>{a_count} 次</span></div>", unsafe_allow_html=True)
                
                if not buy_points_prices:
                    if month_trend_pct > 0: summary_text = "近一個月股價呈現高檔推升或多頭鈍化，因未落入超賣區，未曾觸發任何 A/S 級買點條件，追高需控制風險。"
                    else: summary_text = "近一個月股價持續修正，但可能因成交量不足或 MACD 綠柱擴大被扣分，未曾發出安全訊號，建議保持觀望。"
                else:
                    avg_buy_price = sum(buy_points_prices) / len(buy_points_prices)
                    profit_pct = (current_price - avg_buy_price) / avg_buy_price * 100
                    prof_color = "#ff3333" if profit_pct >= 0 else "#00cc00"
                    prof_text = "獲利" if profit_pct >= 0 else "虧損"
                    summary_text = f"本月大腦共偵測到 **{s_count + a_count}** 次安全買進點。若於訊號出現當日收盤進行等額建倉，綜合平均成本約為 **{avg_buy_price:.2f}**。對比今日收盤，目前策略帳面回測呈 <span style='color:{prof_color}; font-weight:bold;'>{prof_text} {'+' if profit_pct>0 else ''}{profit_pct:.2f}%</span>。"
                st.markdown(f"<div style='margin-top:12px; padding:12px; background-color:{'#f0f8ff' if is_light_mode else '#1e2433'}; border-radius:8px; line-height: 1.6;'>📝 <b>大腦回測總結：</b>{summary_text}</div>", unsafe_allow_html=True)

            # ==========================================
            # AI 決策面板與核心分析渲染
            # ==========================================
            bullets, v_t, v_c, v_a = generate_comprehensive_analysis(data, inst_data, sc, t_title, tmr_title)
            bullets_html = "".join([f"<li style='margin-bottom: 8px;'>{b}</li>" for b in bullets])
            st.markdown(f'''<div style="border: 2px solid {v_c}; border-radius: 10px; padding: 20px; margin-bottom: 20px; background-color: {bg_col}; box-shadow: 0 4px 6px rgba(0,0,0,0.1);"><h3 style="text-align: center; color: {v_c}; margin-top: 0; font-size: 1.8rem;">🤖 AI 決策大腦：{v_t.replace('🟢 ', '').replace('🟡 ', '').replace('⚪ ', '').replace('🟠 ', '').replace('🔴 ', '')}</h3><hr style="border-color: {border_col}; margin: 15px 0;"><div style="margin-bottom: 15px;"><h4 style="color: {text_col}; margin-bottom: 10px;">🔍 綜合技術與籌碼防護診斷：</h4><ul style="font-size: 1rem; color: {text_col}; line-height: 1.6;">{bullets_html}</ul></div><div style="background-color: {'#f0f8ff' if is_light_mode else '#1e2433'}; padding: 15px; border-radius: 8px; border-left: 5px solid {v_c};"><p style="font-size: 1.15rem; color: {text_col}; margin: 0; line-height: 1.6;">{v_a}</p></div></div>''', unsafe_allow_html=True)
            
            dc1, dc2, dc3, dc4, dc5, dc6 = st.columns([1, 1, 1, 1, 1.5, 1.5])
            if dc1.button("1個月"): st.session_state.view_days = 20
            if dc2.button("3個月"): st.session_state.view_days = 60
            if dc3.button("6個月"): st.session_state.view_days = 120
            if dc4.button("1年"): st.session_state.view_days = 240
            with dc5: show_buy_sig = st.toggle("🛒 顯示買進訊號", value=True)
            with dc6: show_sup_res = st.toggle("📏 顯示支撐/壓力", value=False)
                
            fig = draw_professional_chart(df_slice, target, data['收盤價'], st.session_state.view_days, is_light_mode, show_buy_sig, f_data, show_sup_res)
            st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})
            
            st.markdown("### 🕵️‍♂️ 進階數據面板")
            a1, a2 = st.columns(2)
            with a1.container(border=True):
                st.markdown(f"##### 📊 技術與動能指標<br><br>**月線乖離率:** `{data['BIAS']}%`<br><br>**MACD 柱狀體:** `{data['MACD柱']}`<br><br>**5日均量:** `{data['5日均量']} 張`", unsafe_allow_html=True)
            with a2.container(border=True):
                eps = f_data['EPS']; m_eps = round(float(eps)/3, 2) if eps != "無" else "無"
                st.markdown(f"##### 📑 基本面價值評估<br><br>**當季每股盈餘 (EPS):** `{eps}`<br><br>**換算單月 EPS:** `{m_eps}`<br><br>**最新即時本益比 (P/E):** `{f_data['PE']}`", unsafe_allow_html=True)

            st.divider()
            st.subheader("🏦 近期三大法人逐日買賣超")
            if inst_data: st.dataframe(pd.DataFrame(inst_data), use_container_width=True, hide_index=True)
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
                
    else: st.error("查無此股票資料。")
