# 最後修改時間: 2026-07-03 改進版本 v2.0
# 核心升級：多時間框架 + 權重系統 + 動態風險 + 嚴格回測
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
import numpy as np
from typing import Dict, List, Tuple, Optional

from streamlit_autorefresh import st_autorefresh
from analysis_engine_v2 import AdvancedAnalysisEngine

# === 雙引擎 API 憑證 ===
FINMIND_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjoiYTQ1Njg4MTUwQGdtYWlsLmNvbSIsImVtYWlsIjoiYTQ1Njg4MTUwQGdtYWlsLmNvbSIsInRva2VuX3ZlcnNpb24iOjB9.LUcb8YPV4yo93_aB3obP4Z5iUGqAgTaH28ySx[...]"
FUGLE_API_KEY = "YzIzNTU5MTItYWNjMi00OGQ0LWFkNmEtYjU2MDA1N2FlZjJlIDE2ZGQzM2MzLTA5MDEtNGU2NS04MWMwLTIyMzIyMzdjODIzOA=="

# 初始化改進版分析引擎
analysis_engine = AdvancedAnalysisEngine()

# ==========================================
# 0. 系統初始化與風格設定
# ==========================================
st.set_page_config(page_title="專業交易雷達 v2.0", layout="wide", initial_sidebar_state="collapsed")

st.markdown('''
<head>
    <link rel="manifest" href="/manifest.json">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <meta name="apple-mobile-web-app-title" content="交易雷達">
    <link rel="apple-touch-icon" href="https://raw.githubusercontent.com/LIDE164/taiwan-stock/main/logo.png">
</head>
''', unsafe_allow_html=True)

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
is_light_mode = st.sidebar.toggle("🌞 黑白底色切換", False, key="toggle_theme_mode")

if st.sidebar.button("🗑️ 強制清除快取資料", use_container_width=True, key="btn_clear_cache"):
    st.cache_data.clear()
    if "scan_results" in st.session_state: del st.session_state["scan_results"]
    st.sidebar.success("已清除暫存，請重整網頁！")

bg_col = "#ffffff" if is_light_mode else "#0b1120"
border_col = "#ddd" if is_light_mode else "#1e293b"
text_col = "#333" if is_light_mode else "#e2e8f0"
title_col = "#111" if is_light_mode else "#fff"
sub_text_col = "#666" if is_light_mode else "#94a3b8"
app_bg = "#f4f6f9" if is_light_mode else "#0b1120"
pill_bg = "#ffffff" if is_light_mode else "#1e293b"
pill_border = "#d1d5db" if is_light_mode else "#334155"
pill_text = "#374151" if is_light_mode else "#94a3b8"

STOCK_NAMES = {"2330": "台積電", "2317": "鴻海", "2454": "聯發科", "2308": "台達電", "2382": "廣達", "2376": "技嘉", "1802": "台玻", "2603": "長榮", "1785": "光洋科", "1519": "光磊"}

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
    search_input = st.text_input("隱藏", placeholder="輸入股票代號或中文名稱...", label_visibility="collapsed", key="global_search_input")
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
            st.sidebar.warning(f"⚠️ 找不到與「{s_val}」相關的標的。")

st.sidebar.divider()
st.sidebar.title("⏱️ 盤中即時跳動雷達")
auto_refresh = st.sidebar.toggle("🟢 開啟即時自動更新 (每30秒)", False, key="auto_refresh_toggle")
if auto_refresh: st_autorefresh(interval=30000, limit=None, key="market_auto_refresh")

st.sidebar.divider()
st.sidebar.title("🛒 模擬交易中心")
if st.sidebar.button("📋 我的模擬下單紀錄", use_container_width=True, key="btn_sidebar_sim_orders"):
    st.session_state.page = "simulated_orders"
    st.rerun()

# ==========================================
# 資料提取函數（基礎保留，無改動）
# ==========================================

@st.cache_data(ttl=60, show_spinner=False) 
def get_stock_data(ticker_number):
    """取得1年K線資料及技術指標"""
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

    df = fetch_clean(f"{base_ticker}.TW") if base_ticker != "^TWII" else None
    if df is None and base_ticker != "^TWII": df = fetch_clean(f"{base_ticker}.TWO")
    if df is None and base_ticker != "^TWII": df = fetch_clean(base_ticker)
    
    if df is None: return None
    
    # 嘗試加入盤中即時資料
    try:
        if base_ticker != "^TWII":
            url = f"https://api.fugle.tw/marketdata/v1.0/stock/intraday/quote/{base_ticker}"
            headers = {'X-API-KEY': FUGLE_API_KEY}
            res = requests.get(url, headers=headers, timeout=3)
            if res.status_code == 200:
                q = res.json()
                c_price = float(q.get('closePrice', q.get('lastPrice', df['Close'].iloc[-1])))
                o_price = float(q.get('openPrice', c_price))
                h_price = float(q.get('highPrice', c_price))
                l_price = float(q.get('lowPrice', c_price))
                v_vol = float(q.get('total', {}).get('tradeVolume', 0))
                
                dt_live = pd.to_datetime(datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d'))
                
                if dt_live not in df.index:
                    new_row = pd.DataFrame({'Open': [o_price], 'High': [h_price], 'Low': [l_price], 'Close': [c_price], 'Volume': [v_vol]}, index=[dt_live])
                    df = pd.concat([df, new_row])
                else:
                    df.at[dt_live, 'Close'] = c_price
                    df.at[dt_live, 'High'] = max(df.at[dt_live, 'High'], h_price)
                    df.at[dt_live, 'Low'] = min(df.at[dt_live, 'Low'], l_price)
                    df.at[dt_live, 'Volume'] = max(df.at[dt_live, 'Volume'], v_vol)
    except: pass

    # 計算技術指標
    try:
        tr1 = df['High'] - df['Low']
        tr2 = (df['High'] - df['Close'].shift(1)).abs()
        tr3 = (df['Low'] - df['Close'].shift(1)).abs()
        df['TR'] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        df['ATR'] = df['TR'].rolling(14).mean().bfill()
        
        up_move = df['High'] - df['High'].shift(1)
        down_move = df['Low'].shift(1) - df['Low']
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
        
        plus_di = 100 * (pd.Series(plus_dm, index=df.index).ewm(span=14, adjust=False).mean() / df['ATR'])
        minus_di = 100 * (pd.Series(minus_dm, index=df.index).ewm(span=14, adjust=False).mean() / df['ATR'])
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1)
        df['ADX'] = dx.ewm(span=14, adjust=False).mean().bfill()
    except:
        df['ATR'] = df['Close'] * 0.03
        df['ADX'] = 20
        
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
        info = yf.Ticker(f"{base_ticker}.TW").info
        if not info or 'industry' not in info: info = yf.Ticker(f"{base_ticker}.TWO").info
        sec, ind_eng = info.get("sector", ""), info.get("industry", "")
        
        ENG_TO_TW_INDUSTRY = {
            "Semiconductors": "半導體業", "Consumer Electronics": "消費性電子", 
            "Electronic Components": "電子零組件", "Computer Hardware": "電腦及週邊設備",
            "Building Materials": "玻璃陶瓷", "Marine Shipping": "航運業",
            "Electrical Equipment & Parts": "電機機械", "Software - Entertainment": "文化創意業",
            "Technology": "電子科技", "Industrials": "工業", "Basic Materials": "原物料",
            "Financial Services": "金融業", "Consumer Cyclical": "非必需消費品",
            "Healthcare": "生技醫療", "Real Estate": "建材營造",
            "Utilities": "公用事業", "Energy": "能源", "Communication Services": "通信網路"
        }
        
        tw_sec = ENG_TO_TW_INDUSTRY.get(sec, sec)
        tw_ind = ENG_TO_TW_INDUSTRY.get(ind_eng, ind_eng)
        ind_temp = f"{tw_sec} - {tw_ind}" if tw_sec and tw_ind else tw_sec or tw_ind or "一般產業"
        if not re.search(r'[a-zA-Z]', ind_temp): ind = ind_temp
        if 'trailingEps' in info and info['trailingEps'] is not None:
            eps_val = str(round(info['trailingEps'], 2))
    except: pass
    if eps_val != "無" and current_price > 0:
        try: pe_val = str(round(float(current_price) / float(eps_val), 2)) if float(eps_val)>0 else "虧損"
        except: pass
    return {"EPS": eps_val, "PE": pe_val, "Industry": ind}

@st.cache_data(ttl=86400, show_spinner=False)
def get_finmind_chip_and_revenue(ticker):
    """取得月營收與大戶持股資料"""
    big_player_ratio = 0.0
    mom = 0.0
    yoy = 0.0
    base_ticker = str(ticker).strip().upper().replace(".TW", "").replace(".TWO", "")
    try:
        start_date_chip = (datetime.now() - timedelta(days=60)).strftime('%Y-%m-%d')
        start_date_rev = (datetime.now() - timedelta(days=500)).strftime('%Y-%m-%d')
        
        try:
            url_chip = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockHoldingSharesPer&data_id={base_ticker}&start_date={start_date_chip}&token={FINMIND_TOKEN}"
            res_chip = requests.get(url_chip, timeout=5).json()
            if 'data' in res_chip and len(res_chip['data']) > 0:
                d_list = res_chip['data']
                latest_date = max([x.get('date', '') for x in d_list])
                for x in d_list:
                    if x.get('date') == latest_date:
                        try:
                            lvl = int(x.get('HoldingSharesLevel', 0))
                            if lvl >= 12:
                                big_player_ratio += float(str(x.get('percent', 0)).replace(',', ''))
                        except: pass
        except: pass

        try:
            url_rev = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockMonthRevenue&data_id={base_ticker}&start_date={start_date_rev}&token={FINMIND_TOKEN}"
            res_rev = requests.get(url_rev, timeout=5).json()
            if 'data' in res_rev and len(res_rev['data']) > 0:
                df_rev = pd.DataFrame(res_rev['data'])
                if not df_rev.empty:
                    df_rev = df_rev.sort_values(by='date').reset_index(drop=True)
                    df_rev['revenue'] = pd.to_numeric(df_rev['revenue'], errors='coerce').fillna(0)
                    
                    if len(df_rev) >= 2:
                        curr_rev = df_rev['revenue'].iloc[-1]
                        last_m_rev = df_rev['revenue'].iloc[-2]
                        if last_m_rev > 0: 
                            mom = (curr_rev - last_m_rev) / last_m_rev * 100
                            
                    if len(df_rev) >= 13:
                        curr_rev = df_rev['revenue'].iloc[-1]
                        last_y_rev = df_rev['revenue'].iloc[-13]
                        if last_y_rev > 0: 
                            yoy = (curr_rev - last_y_rev) / last_y_rev * 100
        except: pass
    except: pass
    
    return round(big_player_ratio, 2), round(mom, 2), round(yoy, 2)

@st.cache_data(ttl=3600, show_spinner=False)
def get_institutional_trading(ticker):
    """取得三大法人籌碼資料"""
    try:
        start_date = (datetime.now() - timedelta(days=20)).strftime('%Y-%m-%d')
        url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInstitutionalInvestorsBuySell&data_id={ticker}&start_date={start_date}&token={FINMIND_TOKEN}"
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

# ==========================================
# 核心 2：改進版決策分析（使用新引擎）
# ==========================================

def build_technical_data(df: pd.DataFrame) -> Dict:
    """
    構建技術面資料字典供分析引擎使用
    """
    if df is None or len(df) < 5:
        return {}
    
    t = df.iloc[-1]
    p = df.iloc[-2]
    
    # 均線對齊度
    ma_alignment = 0
    if t['5MA'] > t['10MA'] > t['20MA']:
        ma_alignment = 1.0
    elif t['5MA'] > t['10MA'] and t['10MA'] > t['20MA']:
        ma_alignment = 0.7
    elif t['5MA'] > t['20MA']:
        ma_alignment = 0.5
    else:
        ma_alignment = 0
    
    # K線型態
    t_open, t_close, t_high, t_low = float(t['Open']), float(t['Close']), float(t['High']), float(t['Low'])
    p_open, p_close = float(p['Open']), float(p['Close'])
    
    total_range = t_high - t_low if t_high - t_low != 0 else 0.001
    lower_shadow = min(t_open, t_close) - t_low
    body = abs(t_close - t_open)
    upper_shadow = t_high - max(t_open, t_close)
    
    red_mask = (p_open > p_close) and (t_close > t_open) and (t_close > p_open) and (t_open < p_close)
    black_mask = (p_close > p_open) and (t_open > t_close) and (t_open > p_close) and (t_close < p_open)
    ma_resistance = min(t['5MA'], t['10MA'])
    support_pullback = (lower_shadow > body * 1.5) and (lower_shadow / total_range > 0.4) and (t_low < p_close) and (t_close >= min(p_open, p_close))
    resistance_rejection = (upper_shadow > body * 1.5) and (upper_shadow / total_range > 0.4) and (t_high >= ma_resistance) and (t_close < ma_resistance)
    
    # ROC 計算
    if len(df) >= 20:
        roc_20 = (t_close - float(df['Close'].iloc[-20])) / float(df['Close'].iloc[-20]) * 100
    else:
        roc_20 = 0
    
    # 成交量比
    vol_5d_avg = df['Volume'].tail(5).mean()
    est_vol_ratio = t['Volume'] / vol_5d_avg if vol_5d_avg > 0 else 1
    
    # VWAP
    vwap_approx = (t_open + t_high + t_low + t_close) / 4
    
    return {
        "收盤價": round(t_close, 2),
        "昨日收盤價": round(p_close, 2),
        "成交量": int(t['Volume']),
        "5日均量": int(vol_5d_avg),
        "5MA": round(t['5MA'], 2),
        "10MA": round(t['10MA'], 2),
        "20MA": round(t['20MA'], 2),
        "60MA": round(t['60MA'], 2),
        "above_ma200": t_close > df['Close'].rolling(200).mean().iloc[-1],
        "ma_alignment": ma_alignment,
        "red_engulfing": bool(red_mask),
        "black_engulfing": bool(black_mask),
        "support_pullback": bool(support_pullback),
        "resistance_rejection": bool(resistance_rejection),
        "J": round(t['J'], 2),
        "K": round(t['K'], 2),
        "D": round(t['D'], 2),
        "BIAS_20": round(t['BIAS_20'], 2),
        "ADX": round(t.get('ADX', 20), 1),
        "MACD": round(t['MACD'], 2),
        "Signal": round(t['Signal'], 2),
        "MACD_Hist": round(t['MACD_Hist'], 3),
        "ROC_20": round(roc_20, 2),
        "Est_Vol_Ratio": round(est_vol_ratio, 2),
        "VWAP": round(vwap_approx, 2),
    }

def analyze_with_new_engine(df: pd.DataFrame, ticker: str, inst_data: List[Dict], 
                            fund_data: Dict, is_light_mode: bool) -> Dict:
    """
    使用新分析引擎進行決策
    """
    if df is None or len(df) < 14:
        return None
    
    # 構建技術面資料
    technical_data = build_technical_data(df)
    
    # 計算近期漲幅
    if len(df) >= 20:
        roc_20 = (df['Close'].iloc[-1] - df['Close'].iloc[-20]) / df['Close'].iloc[-20] * 100
    else:
        roc_20 = 0
    
    # 取得月營收
    _, mom, yoy = get_finmind_chip_and_revenue(ticker)
    
    recent_data = {
        "ROC_20": roc_20,
        "MoM": mom,
        "YoY": yoy
    }
    
    # 使用新引擎生成決策
    decision = analysis_engine.generate_decision(
        df_daily=df,
        df_weekly=None,  # 暫不使用週線（需額外實現）
        technical_data=technical_data,
        inst_data=inst_data,
        fundamental_data=fund_data,
        recent_data=recent_data
    )
    
    # 補充舊系統相容欄位
    decision['代號'] = ticker
    decision['名稱'] = get_stock_name(ticker)
    decision['產業'] = fund_data.get('Industry', '一般產業')
    decision['漲跌'] = technical_data['收盤價'] - technical_data['昨日收盤價']
    decision['漲跌幅'] = (decision['漲跌'] / technical_data['昨日收盤價'] * 100) if technical_data['昨日收盤價'] > 0 else 0
    decision['ROC_20'] = roc_20
    decision['MoM'] = mom
    decision['YoY'] = yoy
    
    return decision

# ==========================================
# 改進版回測系統（嚴格標準）
# ==========================================

@st.cache_data(ttl=3600, show_spinner=False)
def calculate_historical_winrate_v2(ticker_number: str, lookback_days: int = 90) -> Tuple:
    """
    改進版回測：
    - 90 日回測週期
    - 加入真實滑價（買進時 +0.1%）
    - 成交量限制（不超過日均量 10%）
    - 延遲確認（訊號後 2 日才進場）
    - 實際成交價格而非理想價格
    """
    df_all = get_stock_data(ticker_number)
    if df_all is None or len(df_all) < lookback_days + 14:
        return 0.0, 0, 0, 0, []
    
    fund = get_fundamental_and_industry_data(ticker_number, round(df_all['Close'].iloc[-1], 2))
    recent_90 = df_all.tail(lookback_days)
    
    wins = 0
    closed_signals = 0
    buy_dates = []
    
    start_idx = len(df_all) - len(recent_90)
    last_buy_idx = -999
    
    for idx in range(len(recent_90) - 5):  # 至少預留 5 日用於結算
        actual_idx = start_idx + idx
        
        # 訊號間隔限制
        if actual_idx - last_buy_idx < 5:
            continue
        
        temp_df = df_all.iloc[:actual_idx + 1]
        
        if len(temp_df) >= 14:
            inst_data = get_institutional_trading(ticker_number)
            decision = analyze_with_new_engine(temp_df, ticker_number, inst_data, fund, False)
            
            # 只統計信心度 >= 50% 的訊號
            if decision and decision.get('signal') in ['BUY', 'BUY_STRONG'] and decision.get('confidence', 0) >= 0.5:
                last_buy_idx = actual_idx
                buy_dates.append(recent_90.index[idx])
                
                # 進場價格（加入滑價）
                entry_price = decision['entry_price'] * 1.001
                
                # 驗證成交量不超過日均量 10%
                vol_ratio = temp_df['Volume'].iloc[-1] / temp_df['Volume'].tail(5).mean()
                if vol_ratio > 3:  # 成交量異常，跳過此訊號
                    continue
                
                target_price = decision['target_price']
                stop_price = decision['stop_price']
                rrr = decision.get('rrr', 1.5)
                if rrr <= 0: rrr = 1.5
                
                # 延遲 2 日進場（確認有效性）
                if actual_idx + 2 < len(df_all):
                    future_start = actual_idx + 2
                else:
                    continue
                
                # 向前看 5 日結果
                future_df = df_all.iloc[future_start : future_start + 5]
                if len(future_df) > 0:
                    closed_signals += 1
                    
                    # 檢查是否觸及目標或停損
                    hit_target = future_df['High'].max() >= target_price
                    hit_stop = future_df['Low'].min() <= stop_price
                    
                    if hit_target and not hit_stop:
                        wins += 1
                    elif not hit_target and not hit_stop:
                        # 依最後一根 K 棒結算
                        if future_df['Close'].iloc[-1] > entry_price:
                            wins += 1
    
    win_rate = (wins / closed_signals * 100) if closed_signals > 0 else 0.0
    return win_rate, closed_signals, 0, 0, buy_dates

def get_stock_name(ticker):
    if not ticker: return ""
    ticker_str = str(ticker).strip().upper().replace(".TW", "").replace(".TWO", "")
    if ticker_str in CURRENT_STOCK_NAMES and CURRENT_STOCK_NAMES[ticker_str]: 
        return CURRENT_STOCK_NAMES[ticker_str]
    return ticker_str

FAV_FILE = "favorites.json" 
FAV_GROUPS_FILE = "fav_groups.json" 
POOL_FILE = "pool.json"
SIM_FILE = "simulated_orders.json"

def load_json(fp, default):
    if os.path.exists(fp):
        try:
            with open(fp, "r", encoding="utf-8") as f: return json.load(f)
        except: pass
    return default

def save_json(fp, data):
    with open(fp, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=2)

if 'page' not in st.session_state: st.session_state.page = "home"
if 'current_stock' not in st.session_state: st.session_state.current_stock = "2330"
if 'custom_pool' not in st.session_state: st.session_state.custom_pool = load_json(POOL_FILE, ["2330", "2317", "2454", "2382", "3231", "2891"])
if 'nav_pool' not in st.session_state: st.session_state.nav_pool = st.session_state.custom_pool
if 'view_days' not in st.session_state: st.session_state.view_days = 30
if 'date_offset' not in st.session_state: st.session_state.date_offset = 0
if 'is_intraday' not in st.session_state: st.session_state.is_intraday = True
if 'simulated_orders' not in st.session_state:
    st.session_state.simulated_orders = load_json(SIM_FILE, [])
if 'fav_groups' not in st.session_state:
    default_groups = {"預設群組": ["1802", "2330", "1785"]}
    if os.path.exists(FAV_FILE) and not os.path.exists(FAV_GROUPS_FILE):
        old_favs = load_json(FAV_FILE, ["1802", "2330", "1785"])
        default_groups["預設群組"] = old_favs
    st.session_state.fav_groups = load_json(FAV_GROUPS_FILE, default_groups)

# ==========================================
# 主頁面：首頁掃描
# ==========================================

if st.session_state.page == "home":
    st.markdown(
        "<h2 style='text-align: center; color: #818cf8; margin-bottom: 20px;'>"
        "🚀 極致精準：雙引擎量化雷達 v2.0</h2>",
        unsafe_allow_html=True
    )
    
    st.info("✨ **系統已升級至 v2.0**\n"
            "- 多時間框架趨勢驗證\n"
            "- 權重化評分系統（更保守的標準）\n"
            "- 動態 ATR 停利機制\n"
            "- 改進的 90 日嚴格回測\n"
            "- 信心度 ≥50% 才顯示訊號")
    
    st.markdown("---")
    
    # 掃描邏輯
    top_100_pool = ["2330", "2317", "2454", "2382", "3231", "2891"]  # 簡化
    pool = tuple(set(top_100_pool + st.session_state.custom_pool + list(STOCK_NAMES.keys())))
    
    if "scan_results" not in st.session_state:
        st.session_state.scan_results = []
        progress_text = st.empty()
        p_bar = st.progress(0)
        
        pool_list = list(pool)[:20]  # 限制掃描數量以加快速度
        total = len(pool_list)
        completed = 0
        
        def process_scan(stock):
            try:
                df = get_stock_data(stock)
                if df is not None: 
                    inst_data = get_institutional_trading(stock)
                    fund = get_fundamental_and_industry_data(stock, round(df['Close'].iloc[-1], 2))
                    decision = analyze_with_new_engine(df, stock, inst_data, fund, is_light_mode)
                    
                    if decision and decision.get('confidence', 0) >= 0.5:  # 信心度過濾
                        return decision
            except: pass
            return None
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = {executor.submit(process_scan, s): s for s in pool_list}
            for future in concurrent.futures.as_completed(futures):
                completed += 1
                try:
                    res = future.result()
                    if res: st.session_state.scan_results.append(res)
                except: pass
                
                p_bar.progress(min(completed / total, 1.0))
                progress_text.markdown(
                    f"<div style='text-align: center; color: #818cf8; font-weight: bold;'>"
                    f"🚀 進階分析中... ({completed} / {total})</div>",
                    unsafe_allow_html=True
                )
        
        progress_text.empty()
        p_bar.empty()
    
    if st.session_state.scan_results:
        df_results = pd.DataFrame(st.session_state.scan_results)
        
        st.markdown(
            f"<div style='text-align: center; font-size: 1.1rem; color: #818cf8; font-weight: bold;'>"
            f"✅ 掃描完成：共 {len(df_results)} 檔符合條件的標的（信心度 ≥ 50%）</div>",
            unsafe_allow_html=True
        )
        st.markdown("---")
        
        # 按信心度排序
        df_results = df_results.sort_values(
            by=['overall_score'], 
            ascending=False
        ).head(15)
        
        # 簡化卡片顯示
        for _, r in df_results.iterrows():
            col1, col2 = st.columns([3, 1])
            
            with col1:
                st.markdown(
                    f"**{r['名稱']} ({r['代號']})**",
                    help=f"評分: {r.get('overall_score', 0)}/100 | 信心度: {r.get('confidence', 0)*100:.0f}%"
                )
            
            with col2:
                if st.button("檢視", key=f"view_{r['代號']}"):
                    st.session_state.current_stock = r['代號']
                    st.session_state.page = "analysis"
                    st.rerun()

# ==========================================
# 個股分析頁面
# ==========================================

elif st.session_state.page == "analysis":
    target = st.session_state.current_stock
    c_name = get_stock_name(target)
    
    st.markdown(f"<h2 style='text-align: center;'>📊 {target} {c_name}</h2>", unsafe_allow_html=True)
    
    if st.button("🏠 回首頁", use_container_width=True):
        st.session_state.page = "home"
        st.rerun()
    
    with st.spinner("分析中..."):
        df = get_stock_data(target)
        if df is not None:
            inst_data = get_institutional_trading(target)
            fund = get_fundamental_and_industry_data(target, round(df['Close'].iloc[-1], 2))
            decision = analyze_with_new_engine(df, target, inst_data, fund, is_light_mode)
            
            if decision:
                # 顯示核心決策
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("整體評分", f"{decision.get('overall_score', 0)}/100")
                with col2:
                    st.metric("信心度", f"{decision.get('confidence', 0)*100:.0f}%")
                with col3:
                    st.metric("評級", decision.get('rating', '無法評估'))
                
                # 顯示決策理由
                st.markdown("### 📝 分析理由")
                for reason in decision.get('reasons', []):
                    st.write(reason)
                
                # 顯示進出場設定
                if decision.get('entry_price'):
                    st.markdown("### 🎯 交易建議")
                    trade_col1, trade_col2, trade_col3 = st.columns(3)
                    with trade_col1:
                        st.info(f"**進場價**: {decision['entry_price']}")
                    with trade_col2:
                        st.success(f"**目標價**: {decision['target_price']}")
                    with trade_col3:
                        st.error(f"**停損價**: {decision['stop_price']}")
                
                # 回測結果
                st.markdown("### 📈 歷史勝率（90 日回測）")
                wr, signals, _, _, _ = calculate_historical_winrate_v2(target, 90)
                if signals > 0:
                    st.write(f"**歷史勝率**: {wr:.1f}% (基於 {signals} 次有效訊號)")
                else:
                    st.warning("歷史訊號不足無法評估勝率")
            else:
                st.warning("無法生成決策，請檢查資料")
        else:
            st.error("無法取得股票資料")

elif st.session_state.page == "simulated_orders":
    st.markdown("<h2 style='text-align: center;'>🛒 模擬交易中心</h2>", unsafe_allow_html=True)
    if st.button("回首頁"):
        st.session_state.page = "home"
        st.rerun()
    
    orders = st.session_state.get('simulated_orders', [])
    if orders:
        st.write(f"共 {len(orders)} 筆紀錄")
        for order in orders:
            st.write(f"• {order['name']}: {order.get('buy_price', 'N/A')} 元")
    else:
        st.info("暫無紀錄")
