# test.py - 交易雷達主程式 (100分新制 + 完整UI與風險計算器)
import firebase_admin
from firebase_admin import credentials, firestore
import yfinance as yf
import streamlit as st
import pandas as pd
import requests
import time
from datetime import datetime, timezone, timedelta
import re
import concurrent.futures
import numpy as np
import logging
from streamlit_autorefresh import st_autorefresh
import charts

logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(levelname)s - %(message)s')

FINMIND_TOKEN = st.secrets["FINMIND_TOKEN"]
FUGLE_API_KEY = st.secrets["FUGLE_API_KEY"]

st.set_page_config(page_title="專業交易雷達", layout="wide", initial_sidebar_state="collapsed")

is_light_mode = st.sidebar.toggle("🌞 黑白底色切換", False, key="toggle_theme_mode")

if st.sidebar.button("🗑️ 強制清除快取資料", use_container_width=True):
    st.cache_data.clear()
    if "scan_results" in st.session_state: del st.session_state["scan_results"]
    st.sidebar.success("已清除暫存，請重整網頁！")

bg_col = "#ffffff" if is_light_mode else "#0b1120"
border_col = "#ddd" if is_light_mode else "#1e293b"
text_col = "#333" if is_light_mode else "#e2e8f0"
app_bg = "#f4f6f9" if is_light_mode else "#0b1120"
pill_bg = "#ffffff" if is_light_mode else "#1e293b"
pill_border = "#d1d5db" if is_light_mode else "#334155"
pill_text = "#374151" if is_light_mode else "#94a3b8"

css_style = f"""
<style>
    .stApp {{ background-color: {app_bg}; -webkit-tap-highlight-color: transparent; overflow-x: hidden; }}
    #MainMenu {{visibility: hidden;}} footer {{visibility: hidden;}}
    [data-testid="collapsedControl"] {{ border: 1px solid {border_col} !important; border-radius: 8px !important; background-color: {bg_col} !important; padding: 5px 12px !important; display: flex !important; align-items: center !important; width: auto !important; transition: 0.3s; z-index: 1000; }}
    [data-testid="collapsedControl"]::after {{ content: " ⭐ 我的群組"; font-size: 1.1rem; font-weight: bold; color: #ffcc00; margin-left: 8px; }}
    a.stock-card-link {{ text-decoration: none; color: inherit; display: block; }}
</style>
"""
st.markdown(css_style, unsafe_allow_html=True)

CURRENT_STOCK_NAMES = { "2330": "台積電", "2317": "鴻海", "2454": "聯發科", "2382": "廣達"}
@st.cache_data(ttl=86400)
def get_all_tw_stock_names():
    names = CURRENT_STOCK_NAMES.copy()
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
    search_input = st.text_input("隱藏", placeholder="輸入股票代號或名稱...", label_visibility="collapsed")
    if st.form_submit_button("送出搜尋", use_container_width=True) and search_input:
        s_val = search_input.strip().replace(" ", "")
        target = s_val if re.match(r'^[A-Za-z0-9]+$', s_val) else next((k for k, v in CURRENT_STOCK_NAMES.items() if s_val in v), None)
        if target:
            st.session_state.current_stock = target.upper()
            st.session_state.page = "analysis"
            st.session_state.date_offset = 0
            st.rerun() 

auto_refresh = st.sidebar.toggle("🟢 開啟自動更新", False)
if auto_refresh: st_autorefresh(interval=30000, limit=None)

if st.sidebar.button("📋 經理人績效儀表板", use_container_width=True):
    st.session_state.page = "simulated_orders"; st.rerun()

def get_stock_name(ticker):
    t_str = str(ticker).strip().upper().replace(".TW", "").replace(".TWO", "")
    return CURRENT_STOCK_NAMES.get(t_str, t_str)

if not firebase_admin._apps:
    firebase_admin.initialize_app(credentials.Certificate(dict(st.secrets["firebase"])))
db = firestore.client()

def load_cloud_data(c, d, default):
    try:
        doc = db.collection(c).document(d).get()
        return doc.to_dict().get('data', default) if doc.exists else default
    except: return default

def save_cloud_data(c, d, data):
    try: db.collection(c).document(d).set({'data': data})
    except: pass

if 'page' not in st.session_state: st.session_state.page = "home"
if 'simulated_orders' not in st.session_state: st.session_state.simulated_orders = load_cloud_data("user_data", "simulated_orders", [])
if 'fav_groups' not in st.session_state: st.session_state.fav_groups = load_cloud_data("user_settings", "fav_groups", {"預設群組": ["1802", "2330"]})
if 'custom_pool' not in st.session_state: st.session_state.custom_pool = ["2330", "2317", "2454"]

if 'stock' in st.query_params:
    q_stock = st.query_params['stock']
    if st.session_state.get('last_q_stock') != q_stock:
        st.session_state.current_stock = q_stock
        st.session_state.page = "analysis"
        st.session_state.last_q_stock = q_stock

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_twse_index_history():
    try:
        df = yf.Ticker("^TWII").history(period="1y")
        df.index = pd.to_datetime(df.index.strftime('%Y-%m-%d'))
        return df[['Open', 'High', 'Low', 'Close', 'Volume']]
    except: return None

@st.cache_data(ttl=60, show_spinner=False) 
def get_stock_data(ticker_number):
    t_str = str(ticker_number).strip().upper()
    try:
        df = yf.Ticker(f"{t_str}.TW").history(period="1y").dropna(subset=['Close'])
        if df.empty: df = yf.Ticker(f"{t_str}.TWO").history(period="1y").dropna(subset=['Close'])
        if df.empty: return None
        df.index = pd.to_datetime(df.index.strftime('%Y-%m-%d'))
        
        try:
            res = requests.get(f"https://api.fugle.tw/marketdata/v1.0/stock/intraday/quote/{t_str}", headers={'X-API-KEY': FUGLE_API_KEY}, timeout=3).json()
            c_p = float(res.get('closePrice', res.get('lastPrice', df['Close'].iloc[-1])))
            dt_live = pd.to_datetime(datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d'))
            if datetime.now(timezone(timedelta(hours=8))).weekday() < 5:
                if dt_live not in df.index:
                    df = pd.concat([df, pd.DataFrame({'Open': [float(res.get('openPrice', c_p))], 'High': [float(res.get('highPrice', c_p))], 'Low': [float(res.get('lowPrice', c_p))], 'Close': [c_p], 'Volume': [float(res.get('total', {}).get('tradeVolume', 0))]}, index=[dt_live])])
                else:
                    df.at[dt_live, 'Close'] = c_p
                    df.at[dt_live, 'High'] = max(df.at[dt_live, 'High'], float(res.get('highPrice', c_p)))
                    df.at[dt_live, 'Low'] = min(df.at[dt_live, 'Low'], float(res.get('lowPrice', c_p)))
                    df.at[dt_live, 'Volume'] = max(df.at[dt_live, 'Volume'], float(res.get('total', {}).get('tradeVolume', 0)))
        except: pass

        df['5MA'] = df['Close'].rolling(5).mean()
        df['10MA'] = df['Close'].rolling(10).mean()
        df['20MA'] = df['Close'].rolling(20).mean()
        df['60MA'] = df['Close'].rolling(60).mean()
        df['MACD'] = df['Close'].ewm(span=12, adjust=False).mean() - df['Close'].ewm(span=26, adjust=False).mean()
        df['Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
        df['MACD_Hist'] = df['MACD'] - df['Signal']
        df['STD20'] = df['Close'].rolling(20).std()
        df['BB_UP'] = df['20MA'] + (2 * df['STD20'])
        df['BB_DN'] = df['20MA'] - (2 * df['STD20'])
        df['BIAS_20'] = (df['Close'] - df['20MA']) / df['20MA'] * 100
        
        low_9, high_9 = df['Low'].rolling(9).min(), df['High'].rolling(9).max()
        rsv = (df['Close'] - low_9) / (high_9 - low_9) * 100
        df['K'] = rsv.ewm(com=2, adjust=False).mean()
        df['D'] = df['K'].ewm(com=2, adjust=False).mean()
        df['J'] = 3 * df['K'] - 2 * df['D']

        tr1, tr2, tr3 = df['High'] - df['Low'], (df['High'] - df['Close'].shift(1)).abs(), (df['Low'] - df['Close'].shift(1)).abs()
        df['ATR'] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1).rolling(14).mean().bfill()
        
        up_m, dn_m = df['High'] - df['High'].shift(1), df['Low'].shift(1) - df['Low']
        p_dm, n_dm = np.where((up_m > dn_m) & (up_m > 0), up_m, 0.0), np.where((dn_m > up_m) & (dn_m > 0), dn_m, 0.0)
        p_di = 100 * (pd.Series(p_dm, index=df.index).ewm(span=14, adjust=False).mean() / df['ATR'])
        n_di = 100 * (pd.Series(n_dm, index=df.index).ewm(span=14, adjust=False).mean() / df['ATR'])
        df['ADX'] = (100 * (p_di - n_di).abs() / (p_di + n_di).replace(0, 1)).ewm(span=14, adjust=False).mean().bfill()
        return df
    except: return None

@st.cache_data(ttl=86400, show_spinner=False)
def get_fundamental_and_industry_data(ticker):
    eps_val, pe_val, ind = "0", "999", "一般產業"
    try:
        res = requests.get(f"https://ws.cnyes.com/twstock/api/v1/company/profile/{ticker}", timeout=3).json()
        ind = res['data']['categoryName']
        eps_val = f"{float(res['data']['eps']):.2f}"
    except: pass
    return {"EPS": eps_val, "PE": pe_val, "Industry": ind if ind else "一般產業"}

@st.cache_data(ttl=86400, show_spinner=False)
def get_finmind_chip_and_revenue(ticker):
    bp, mom, yoy = 0.0, 0.0, 0.0
    try:
        url_chip = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockHoldingSharesPer&data_id={ticker}&start_date={(datetime.now() - timedelta(days=60)).strftime('%Y-%m-%d')}&token={FINMIND_TOKEN}"
        d_chip = requests.get(url_chip, timeout=5).json().get('data', [])
        if d_chip:
            l_d = max(x['date'] for x in d_chip)
            bp = sum(float(str(x['percent']).replace(',','')) for x in d_chip if x['date'] == l_d and int(x['HoldingSharesLevel']) >= 12)
        
        url_rev = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockMonthRevenue&data_id={ticker}&start_date={(datetime.now() - timedelta(days=500)).strftime('%Y-%m-%d')}&token={FINMIND_TOKEN}"
        d_rev = pd.DataFrame(requests.get(url_rev, timeout=5).json().get('data', []))
        if not d_rev.empty:
            d_rev['revenue'] = pd.to_numeric(d_rev['revenue'], errors='coerce').fillna(0)
            if len(d_rev) >= 2 and d_rev['revenue'].iloc[-2] > 0: mom = (d_rev['revenue'].iloc[-1] - d_rev['revenue'].iloc[-2]) / d_rev['revenue'].iloc[-2] * 100
            if len(d_rev) >= 13 and d_rev['revenue'].iloc[-13] > 0: yoy = (d_rev['revenue'].iloc[-1] - d_rev['revenue'].iloc[-13]) / d_rev['revenue'].iloc[-13] * 100
    except: pass
    return round(bp, 2), round(mom, 2), round(yoy, 2)

@st.cache_data(ttl=3600, show_spinner=False)
def get_institutional_trading(ticker):
    try:
        url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInstitutionalInvestorsBuySell&data_id={ticker}&start_date={(datetime.now() - timedelta(days=20)).strftime('%Y-%m-%d')}&token={FINMIND_TOKEN}"
        d = requests.get(url, timeout=5).json().get('data', [])
        if d:
            df = pd.DataFrame(d)
            df['net'] = (df['buy'] - df['sell']) / 1000  
            df['type'] = '其他'
            df.loc[df['name'].str.contains('Foreign|外資', case=False, na=False), 'type'] = '外資'
            df.loc[df['name'].str.contains('Trust|投信', case=False, na=False), 'type'] = '投信'
            df.loc[df['name'].str.contains('Dealer|自營', case=False, na=False), 'type'] = '自營商'
            pivot = df.groupby(['date', 'type'])['net'].sum().unstack(fill_value=0).reset_index()
            for col in ['外資', '投信', '自營商']:
                if col not in pivot.columns: pivot[col] = 0
            pivot['單日合計'] = pivot['外資'] + pivot['投信'] + pivot['自營商']
            return [{"日期": r['date'][-5:].replace("-", "/"), "外資(張)": int(r['外資']), "投信(張)": int(r['投信']), "自營商(張)": int(r['自營商']), "單日合計(張)": int(r['單日合計'])} for _, r in pivot.sort_values('date', ascending=False).head(10).iterrows()]
    except: pass
    return []

# ==========================================
# 🚀 終極 100 分量化模型引擎
# ==========================================
def get_decision_score_100(data, fund_data, inst_data=None, df=None):
    score = 0
    reasons = []

    close = data.get('收盤價', 0)
    ma5, ma20, ma60 = data.get('5MA', 0), data.get('20MA', 0), data.get('60MA', 0)
    vol, vol_ma5 = data.get('成交量', 0), data.get('5日均量', 0)
    adx, roc = data.get('ADX', 0), data.get('ROC_20', 0)
    macd_h, macd_h_prev = data.get('MACD柱', 0), data.get('前日MACD柱', 0)
    j_val, bias = data.get('J值', 50), data.get('BIAS', 0)
    red_engulf = data.get('紅吞', False)

    high_20 = df['High'].tail(20).max() if df is not None and len(df) >= 20 else close

    if close > ma20: score += 10; reasons.append("✅ 股價站上月線 (+10)")
    if ma20 > ma60: score += 5; reasons.append("✅ 月季線多頭排列 (+5)")
    if close >= high_20 * 0.99: score += 5; reasons.append("🚀 突破或逼近20日新高 (+5)")
    if adx >= 25: score += 5; reasons.append("🔥 ADX大於25趨勢明確 (+5)")
    else: score -= 3; reasons.append("⚠️ ADX低於25盤整扣分 (-3)")

    if macd_h > 0 and macd_h > macd_h_prev: score += 8; reasons.append("📈 MACD紅柱放大 (+8)")
    if roc > 5: score += 6; reasons.append("🔥 近月漲幅強勢 (+6)")
    if close > ma5: score += 3; reasons.append("✅ 站上5日線 (+3)")
    if red_engulf or (close > high_20): score += 3; reasons.append("🧨 紅吞或過高表態 (+3)")

    if vol > vol_ma5 * 1.2: score += 8; reasons.append("💰 量能大於均量1.2倍 (+8)")
    foreign_buy_days = sum(1 for row in (inst_data or [])[:2] if int(str(row.get('外資(張)', '0')).replace(',', '')) > 0)
    if foreign_buy_days >= 1: score += 6; reasons.append("🏦 外資近期買超 (+6)")
    if fund_data.get('BigPlayer', 0) > 30: score += 6; reasons.append(f"👑 大戶持股>30% (+6)")

    if data.get('YoY', 0) > 15: score += 6; reasons.append(f"📈 YoY > 15% (+6)")
    if data.get('MoM', 0) > 0: score += 4; reasons.append(f"📈 MoM 成長 (+4)")
    try: eps = float(str(fund_data.get('EPS', '0')).replace(',', ''))
    except: eps = 0
    if eps > 0: score += 3; reasons.append("💰 正 EPS (+3)")

    if bias > 10: score -= 3; reasons.append("⚠️ 乖離大於10過熱 (-3)")
    if j_val > 90: score -= 3; reasons.append("⚠️ KDJ 高檔過熱 (-3)")
    if close < ma5: score -= 3; reasons.append("⚠️ 跌破5日線 (-3)")
    if fund_data.get('VIX', 0) > 20: score -= 3; reasons.append("🚨 大盤 VIX > 20 (-3)")

    if score >= 60: label = "🟢 強勢買進"
    elif score >= 45: label = "🟡 偏多觀察"
    else: label = "⚪ 忽略"

    feature = "一般狀態"
    if close > high_20 and vol > vol_ma5 * 1.5: feature = "🔥 爆量突破"
    elif close > ma60 and close <= ma20 * 1.02: feature = "💪 回檔有撐"
    elif close < ma20 and red_engulf: feature = "🔄 底部反轉"
    elif red_engulf: feature = "🔥 紅吞表態"

    return score, label, reasons, feature

def analyze_today(df, ticker, inst_data=None, pre_fund=None):
    if df is None or len(df) < 5: return None
    t, p = df.iloc[-1], df.iloc[-2]
    fund = pre_fund if pre_fund else get_fundamental_and_industry_data(ticker)
    
    if 'BigPlayer' not in fund:
        bp, mom, yoy = get_finmind_chip_and_revenue(ticker)
        fund['BigPlayer'], fund['MoM'], fund['YoY'] = bp, mom, yoy
    
    t_close, p_close = float(t['Close']), float(p['Close'])
    red_mask = (df['Open'].shift(1) > df['Close'].shift(1)) & (df['Close'] > df['Open']) & (df['Close'] > df['Open'].shift(1)) & (df['Open'] < df['Close'].shift(1))

    whale_net = sum(int(str(x['單日合計(張)']).replace(',', '')) for x in (inst_data or [])[:3])
    est_vol_ratio = t['Volume'] / df['Volume'].tail(5).mean() if df['Volume'].tail(5).mean() > 0 else 1

    data = {
        "代號": ticker, "名稱": get_stock_name(ticker), "ticker_raw": ticker,
        "產業": fund['Industry'], "收盤價": round(t_close, 2), "漲跌幅": round((t_close - p_close) / p_close * 100, 2), 
        "成交量": int(t['Volume']), "5日均量": int(df['Volume'].tail(5).mean()),
        "5MA": round(t.get('5MA', t_close), 2), "20MA": round(t.get('20MA', t_close), 2), "60MA": round(t.get('60MA', t_close), 2),
        "BIAS": round(t.get('BIAS_20', 0), 2), "MACD柱": round(t.get('MACD_Hist', 0), 3), "前日MACD柱": round(p.get('MACD_Hist', 0), 3),
        "K": round(t.get('K', 50), 2), "D": round(t.get('D', 50), 2), "J值": round(t.get('J', 50), 2),
        "ADX": round(t.get('ADX', 0), 1), "ROC_20": round((t_close - float(df['Close'].iloc[-20])) / float(df['Close'].iloc[-20]) * 100 if len(df)>=20 else 0, 2), 
        "MoM": fund.get('MoM', 0), "YoY": fund.get('YoY', 0), "紅吞": bool(red_mask.iloc[-1]),
        "Whale_Net": whale_net, "Est_Vol_Ratio": est_vol_ratio,
        "ATR": round(t.get('ATR', t_close*0.03), 2), "ATR_Target": round(t_close + (t.get('ATR', t_close*0.03)*1.5), 1), "ATR_Stop": round(t_close - (t.get('ATR', t_close*0.03)*1.0), 1), "RRR": 1.5
    }
    
    sc, label, rs, feature = get_decision_score_100(data, fund, inst_data, df)
    data['Score'] = sc; data['Reasons'] = rs; data['評級'] = label; data['Feature'] = feature
    data['WinRate'] = 0.0 
    return data

def generate_comprehensive_analysis(data, sc):
    if sc >= 60: text_desc = "目前系統判定該股具備強大的波段上漲動能，各項技術與資金指標皆已表態，屬於勝率較高之強勢多頭格局，建議可設定好停損後伺機介入。"
    elif sc >= 45: text_desc = "目前該股動能逐漸加溫，但可能有部分指標過熱或尚未完全突破，屬於偏多觀察階段，建議留意後續量能變化。"
    else: text_desc = "目前該股動能偏弱或陷入盤整，風險大於預期報酬，建議維持空手觀望，等待更明確的型態出現。"
    return f"<div style='background-color: rgba(30,41,59,0.5); padding: 15px; border-radius: 8px; margin-bottom:15px;'><p style='color: #cbd5e1; font-size: 1.05rem; line-height: 1.6; margin: 0;'>{text_desc}</p></div>"

def generate_cards_html(df_disp):
    cards_html = ""
    for _, r in df_disp.iterrows():
        p_col, p_bg = ("#ef4444", "rgba(239,68,68,0.1)") if r.get('漲跌幅', 0) >= 0 else ("#22c55e", "rgba(34,197,94,0.1)")
        score = r.get('Score', 0)
        s_col = "#ef4444" if score >= 60 else ("#facc15" if score >= 45 else "#22c55e")
        rating = r.get('評級', '⚪ 忽略').replace('🟢 ', '').replace('🟡 ', '').replace('⚪ ', '')
        
        cards_html += f"<div style='background-color: #0f172a; border: 1px solid #1e293b; border-radius: 12px; padding: 14px; margin-bottom: 12px;'>"
        cards_html += f"<div style='display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px;'>"
        cards_html += f"<div style='display: flex; align-items: center; gap: 12px;'><div style='width: 50px; height: 50px; border-radius: 50%; background: radial-gradient(circle, #1e293b 0%, #0b1120 100%); border: 1px solid #334155; display: flex; flex-direction: column; align-items: center; justify-content: center;'><span style='color: {s_col}; font-weight: 800; font-size: 1.2rem; line-height: 1;'>{score}</span><span style='color: {s_col}; font-size: 0.65rem; font-weight: 800; margin-top: 2px;'>{rating}</span></div>"
        cards_html += f"<a href='/?stock={r.get('代號', '')}' target='_self' style='text-decoration:none;'><div style='display: flex; align-items: center; gap: 6px;'><span style='color: #f8fafc; font-weight: bold; font-size: 1.15rem;'>{r.get('名稱', '')}</span><span style='font-size: 0.7rem; background-color: rgba(79,70,229,0.15); color: #818cf8; border: 1px solid rgba(79,70,229,0.3); padding: 2px 6px; border-radius: 4px; font-weight: 600;'>🏷️ {r.get('產業', '一般產業')}</span></div><div style='font-size: 0.8rem; color: #64748b; margin-top: 4px; font-family: monospace;'>{r.get('代號', '')}</div></a></div>"
        cards_html += f"<div style='text-align: right;'><div style='color: {p_col}; font-weight: 800; font-size: 1.2rem; font-family: monospace;'>{r.get('收盤價', 0):.1f}</div><div style='background-color: {p_bg}; color: {p_col}; font-size: 0.75rem; padding: 2px 6px; border-radius: 4px; display: inline-block; font-weight: 800; font-family: monospace; margin-top: 4px;'>{'+' if r.get('漲跌幅', 0)>0 else ''}{r.get('漲跌幅', 0)}%</div></div></div>"
        
        cards_html += f"<div style='display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; background-color: rgba(30,41,59,0.4); border: 1px solid rgba(51,65,85,0.5); padding: 10px; border-radius: 8px; font-size: 0.75rem; margin-bottom: 10px;'><div style='display: flex; flex-direction: column;'><span style='color: #64748b; margin-bottom: 4px;'>歷史勝率</span><span style='color: {'#ef4444' if r.get('WinRate', 0)>=60 else '#facc15'}; font-weight: bold; font-family: monospace;'>{r.get('WinRate', 0):.1f}%</span></div><div style='display: flex; flex-direction: column;'><span style='color: #64748b; margin-bottom: 4px;'>風報比 RRR</span><span style='color: #e2e8f0; font-weight: bold; font-family: monospace;'>1 : 1.5</span></div><div style='display: flex; flex-direction: column;'><span style='color: #64748b; margin-bottom: 4px;'>法人淨買</span><span style='color: {'#ef4444' if r.get('Whale_Net',0)>0 else '#94a3b8'}; font-weight: bold; font-family: monospace;'>{r.get('Whale_Net', 0):,}</span></div></div>"
        cards_html += f"<div style='font-size: 0.75rem; color: #fbbf24;'><span style='font-weight: 500;'>⚡ 進場型態：{r.get('Feature', '一般')}</span></div></div>"
    return cards_html

# ==========================================
# 🚀 路由控制
# ==========================================
if st.session_state.page == "home":
    st.markdown("<h2 style='text-align: center; color: #818cf8; margin-bottom: 20px;'>極致精準：100分量化雷達</h2>", unsafe_allow_html=True)
    
    if "scan_results" not in st.session_state or not st.session_state.scan_results:
        with st.spinner("🔮 正在同步全市場量化名單..."): st.session_state.scan_results = load_cloud_data("market_data", "daily_scan", [])
            
    if st.session_state.scan_results:
        radar_mode = st.radio("引擎模式：", ["盤後波段精算", "盤中動能快篩"], horizontal=True, label_visibility="collapsed")
        is_intraday = "盤中" in radar_mode
        
        # 安全讀取全域變數解決多執行緒閃退
        cached_list = list(st.session_state.get('scan_results', []))
        
        if is_intraday:
            with st.spinner("⚡ 混合動力引擎啟動：即時運算 100 分模型 (約需 3-5 秒)..."):
                fb_df = pd.DataFrame(cached_list)
                targets = list(set([str(t) for t in fb_df['代號'].tolist()[:50]] + st.session_state.custom_pool))
                live_data = []
                
                def process_live(ticker):
                    df = get_stock_data(ticker)
                    if df is not None:
                        base = next((x for x in cached_list if str(x['代號']) == str(ticker)), None)
                        if base:
                            fund = {"Industry": base.get('產業', '一般產業'), "BigPlayer": base.get('BigPlayer', 0), "EPS": base.get('EPS', '0'), "MoM": base.get('MoM', 0), "YoY": base.get('YoY', 0)}
                            wr = base.get('WinRate', 0.0)
                        else:
                            fund = {"Industry": "一般產業", "BigPlayer": 0, "EPS": "0", "MoM": 0, "YoY": 0}
                            wr = 0.0
                        res = analyze_today(df, ticker, None, fund)
                        if res:
                            res['WinRate'] = wr
                            return res
                    return None
                    
                with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                    for r in executor.map(process_live, targets):
                        if r: live_data.append(r)
                df_results = pd.DataFrame(live_data) if live_data else fb_df
        else:
            df_results = pd.DataFrame(cached_list)
        
        if not df_results.empty: 
            df_disp = df_results.sort_values(by=['Score', '漲跌幅'], ascending=[False, False]).head(30)
            st.markdown(generate_cards_html(df_disp), unsafe_allow_html=True)
        else: st.info("此條件下暫無標的。")

elif st.session_state.page == "analysis":
    target = st.session_state.current_stock
    df_chart = get_stock_data(target)
    
    if st.button("🏠 回雷達總機", use_container_width=True): st.session_state.page = "home"; st.rerun()
    
    if df_chart is not None:
        inst_data = get_institutional_trading(target)
        data = analyze_today(df_chart, target, inst_data)
        sc = data['Score']
        
        st.markdown(f"<h2 style='text-align: center;'>🎯 {target} {data['名稱']}</h2>", unsafe_allow_html=True)
        st.markdown(f"<h3 style='text-align: center; color: {'#ef4444' if data['漲跌幅']>=0 else '#22c55e'};'>{data['收盤價']} ({'+' if data['漲跌幅']>0 else ''}{data['漲跌幅']}%)</h3>", unsafe_allow_html=True)
        
        st.markdown(f"### 🤖 100分量化大腦：{data['評級']} ({sc}分)")
        st.markdown(generate_comprehensive_analysis(data, sc), unsafe_allow_html=True)
        
        with st.expander("📝 點此展開各項加扣分明細"):
            for r in data['Reasons']:
                if "✅" in r or "🔥" in r or "🚀" in r or "💰" in r or "📈" in r or "🏦" in r or "👑" in r or "🧨" in r: st.markdown(f"<span style='color:#ef4444; font-weight:bold;'>{r}</span>", unsafe_allow_html=True)
                elif "⚠️" in r or "🚨" in r: st.markdown(f"<span style='color:#22c55e; font-weight:bold;'>{r}</span>", unsafe_allow_html=True)

        st.markdown("---")
        st.markdown("### 🧮 資金控管與零股計算器")
        c1, c2, c3 = st.columns(3)
        with c1: max_loss = st.selectbox("單筆最高可接受虧損 (元)", [5000, 10000, 15000, 20000, 30000])
        with c2: stop_loss_price = st.number_input("設定停損價格", value=float(data['收盤價'] * 0.95), step=0.1)
        
        risk_per_share = data['收盤價'] - stop_loss_price
        if risk_per_share > 0:
            suggested_shares = int(max_loss / risk_per_share)
            with c3: st.markdown(f"<div style='background:rgba(239,68,68,0.1); padding:10px; border-radius:8px; text-align:center;'><span style='font-size:0.8rem; color:#ef4444;'>建議買進股數</span><br><span style='font-size:1.8rem; font-weight:bold; color:#ef4444;'>{suggested_shares} 股</span></div>", unsafe_allow_html=True)
        else:
            with c3: st.warning("停損價必須低於現價")

        st.markdown("---")
        fig = charts.draw_professional_chart(df_chart, data['收盤價'], 90, is_light_mode, show_buy_signal=True, show_sup_res=True)
        st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False, 'scrollZoom': True})
    else: st.error("查無此股票資料。")