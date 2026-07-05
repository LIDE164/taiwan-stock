# scanner.py - 雲端自動掃描機器人 (包含歷史勝率回測滿血版)
import streamlit as st
import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore
import yfinance as yf
import pandas as pd
import requests
import time
from datetime import datetime, timezone, timedelta
import re
import concurrent.futures
import numpy as np
import logging
import functools

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

FINMIND_TOKEN = st.secrets["FINMIND_TOKEN"]
FUGLE_API_KEY = st.secrets["FUGLE_API_KEY"]

if not firebase_admin._apps:
    cert_dict = dict(st.secrets["firebase"])
    cred = credentials.Certificate(cert_dict)
    firebase_admin.initialize_app(cred)
db = firestore.client()

ENG_TO_TW_INDUSTRY = {
    "Semiconductors": "半導體業", "Consumer Electronics": "消費性電子", "Electronic Components": "電子零組件",
    "Computer Hardware": "電腦及週邊設備", "Marine Shipping": "航運業", "Financial Services": "金融業",
}
STOCK_NAMES = { "2330": "台積電", "2317": "鴻海", "2454": "聯發科", "2382": "廣達", "3231": "緯創", "2891": "中信金"}

@functools.lru_cache(maxsize=None)
def get_all_tw_stock_names():
    names = STOCK_NAMES.copy()
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", timeout=5)
        if res.status_code == 200:
            for i in res.json(): names[i['Code']] = i['Name']
    except: pass
    return names

CURRENT_STOCK_NAMES = get_all_tw_stock_names()

def get_stock_name(ticker):
    ticker_str = str(ticker).strip().upper().replace(".TW", "").replace(".TWO", "")
    return CURRENT_STOCK_NAMES.get(ticker_str, ticker_str)

@functools.lru_cache(maxsize=None)
def fetch_twse_top_100():
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", timeout=10)
        df = pd.DataFrame(res.json())
        df['TradeVolume'] = pd.to_numeric(df['TradeVolume'], errors='coerce')
        return df[df['Code'].str.match(r'^\d{4}$')].sort_values(by='TradeVolume', ascending=False).head(100)['Code'].tolist()
    except:
        return ["2330", "2317", "2454", "2382", "3231"]

@functools.lru_cache(maxsize=None)
def get_stock_data(ticker_number):
    base_ticker = str(ticker_number).strip().upper().replace(".TW", "").replace(".TWO", "")
    try:
        df = yf.Ticker(f"{base_ticker}.TW").history(period="1y").dropna(subset=['Close'])
        if df.empty: df = yf.Ticker(f"{base_ticker}.TWO").history(period="1y").dropna(subset=['Close'])
        if df.empty: return None
        df.index = pd.to_datetime(df.index.strftime('%Y-%m-%d'))
        
        df['5MA'] = df['Close'].rolling(5).mean()
        df['10MA'] = df['Close'].rolling(10).mean()
        df['20MA'] = df['Close'].rolling(20).mean()
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
        df['ADX'] = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1)).ewm(span=14, adjust=False).mean().bfill()
        return df
    except: return None

@functools.lru_cache(maxsize=None)
def get_fundamental_and_industry_data(ticker_number):
    base_ticker = str(ticker_number).strip().upper().replace(".TW", "").replace(".TWO", "")
    ind = "一般產業"
    try:
        info = yf.Ticker(f"{base_ticker}.TW").info
        if not info or 'industry' not in info: info = yf.Ticker(f"{base_ticker}.TWO").info
        ind_temp = ENG_TO_TW_INDUSTRY.get(info.get("sector", ""), info.get("industry", "一般產業"))
        if not re.search(r'[a-zA-Z]', ind_temp): ind = ind_temp
    except: pass
    return {"Industry": ind}

@functools.lru_cache(maxsize=None)
def get_institutional_trading(ticker):
    try:
        url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInstitutionalInvestorsBuySell&data_id={ticker}&start_date={(datetime.now() - timedelta(days=20)).strftime('%Y-%m-%d')}&token={FINMIND_TOKEN}"
        res = requests.get(url, timeout=5).json()
        if res.get('msg') == 'success' and len(res.get('data', [])) > 0:
            df = pd.DataFrame(res['data'])
            df['net'] = (df['buy'] - df['sell']) / 1000  
            df['type'] = '其他'
            df.loc[df['name'].str.contains('Foreign|外資', case=False, na=False), 'type'] = '外資'
            df.loc[df['name'].str.contains('Trust|投信', case=False, na=False), 'type'] = '投信'
            df.loc[df['name'].str.contains('Dealer|自營', case=False, na=False), 'type'] = '自營商'
            pivot = df.groupby(['date', 'type'])['net'].sum().unstack(fill_value=0).reset_index()
            return [{"單日合計(張)": int(r.get('外資',0) + r.get('投信',0) + r.get('自營商',0))} for _, r in pivot.sort_values('date', ascending=False).head(3).iterrows()]
    except: pass
    return []

def analyze_today_score(df, ticker_number, inst_data=None, pre_fund=None):
    if df is None or len(df) < 5: return None
    t, p = df.iloc[-1], df.iloc[-2]
    
    t_open, t_close, t_high, t_low = float(t['Open']), float(t['Close']), float(t['High']), float(t['Low'])
    p_close = float(p['Close'])
    
    whale_net_buy = sum([int(str(x['單日合計(張)']).replace(',', '')) for x in inst_data]) if inst_data else 0
    red_mask = (df['Open'].shift(1) > df['Close'].shift(1)) & (df['Close'] > df['Open']) & (df['Close'] > df['Open'].shift(1)) & (df['Open'] < df['Close'].shift(1))
    
    sc = 0
    if (t_close > t.get('20MA', 0)) and (t_close < t.get('5MA', 9999)): sc += 3 if t.get('ADX', 0) >= 25 else 1
    if t_close <= t.get('BB_DN', t_close) * 1.02: sc += 2
    if t.get('BIAS_20', 0) < -5: sc += 1
    if t.get('MACD_Hist', 0) > p.get('MACD_Hist', 0): sc += 2
    else: sc -= 3
    if bool(red_mask.iloc[-1]): sc += 4 if t.get('ADX', 0) >= 25 else 1
    if (min(t_open, t_close) - t_low > abs(t_close - t_open) * 1.5) and ((min(t_open, t_close) - t_low) / (t_high - t_low if t_high - t_low != 0 else 0.001) > 0.4) and (t_low < p_close): sc += 2
    if t.get('J', 50) >= 80: sc -= 3
    if t_close >= t.get('BB_UP', t_close) * 0.98: sc -= 2
    if t_close < t.get('20MA', 0): sc -= 2

    feature = "一般狀態"
    if sc >= 2:
        if whale_net_buy > 500: feature = "法人連買"
        elif bool(red_mask.iloc[-1]): feature = "紅吞表態"
        elif (min(t_open, t_close) - t_low > abs(t_close - t_open) * 1.5): feature = "支撐防守"
        
    vwap_approx = (t_open + t_high + t_low + t_close) / 4
    est_vol_ratio = t['Volume'] / df['Volume'].tail(5).mean() if df['Volume'].tail(5).mean() > 0 else 1
    intraday_score = max(10, min(99, 40 + int(((t_close - vwap_approx)/vwap_approx*100) * 10) + (20 if est_vol_ratio > 1.5 else (10 if est_vol_ratio > 1.0 else -10))))
    
    ind = pre_fund['Industry'] if pre_fund else "一般產業"
    theme_name = "一般題材"
    for kw, ic in { "半導體": "⚙️", "電子": "⚡", "綠能": "🌱", "航運": "🚢", "金融": "💰", "AI": "💡", "機器人": "🤖" }.items():
        if kw in ind: theme_name = ind; break

    return {
        "代號": ticker_number, "名稱": get_stock_name(ticker_number), "ticker_raw": ticker_number,
        "收盤價": round(t_close, 2), "漲跌幅": round((t_close - p_close) / p_close * 100, 2), "漲跌": round(t_close - p_close, 2),
        "Score": sc, "Intraday_Score": intraday_score, "Feature": feature, "Theme_Name": theme_name,
        "VWAP_Dev": round((t_close - vwap_approx) / vwap_approx * 100, 2), "Est_Vol_Ratio": round(est_vol_ratio, 2), 
        "Flow": "大單敲進" if est_vol_ratio > 1.5 and t_close > vwap_approx else "內外盤拉扯",
        "Whale_Net": whale_net_buy, "WinRate": 0.0, "RRR": 1.5
    }

# 🚀 歷史勝率計算引擎 (重新加入)
def calculate_historical_winrate_for_scanner(df_slice):
    if df_slice is None or len(df_slice) < 14: return 0.0
    recent_90 = df_slice.tail(90)
    wins, closed_signals = 0, 0
    last_buy_idx = -999
    start_idx = len(df_slice) - len(recent_90)
    
    for idx in range(len(recent_90)):
        actual_idx = start_idx + idx
        if actual_idx - last_buy_idx < 5: continue
            
        temp_df = df_slice.iloc[:actual_idx + 1]
        if len(temp_df) >= 14:
            t_data = analyze_today_score(temp_df, "TEST")
            if t_data and t_data['Score'] >= 2:
                last_buy_idx = actual_idx
                buy_price = t_data['收盤價']
                atr_val = temp_df['ATR'].iloc[-1] if 'ATR' in temp_df.columns else buy_price * 0.03
                target_p = buy_price + (atr_val * 1.5)
                stop_p = buy_price - (atr_val / 1.5)
                
                future_df = df_slice.iloc[actual_idx + 1 : actual_idx + 6]
                if len(future_df) > 0:
                    closed_signals += 1
                    if future_df['High'].max() >= target_p and future_df['Low'].min() > stop_p: wins += 1
                    elif future_df['Close'].iloc[-1] > buy_price and future_df['Low'].min() > stop_p: wins += 1
                    
    return (wins / closed_signals * 100) if closed_signals > 0 else 0.0

def run_daily_scan():
    logging.info("🚀 開始執行全市場雷達掃描...")
    start_time = time.time()
    
    pool_list = list(set(fetch_twse_top_100() + ["2330", "2317", "2454", "2382", "3231", "2891", "9904", "1809", "0050", "2027", "1409", "3016"]))
    scan_results = []
    completed = 0
    
    def process_stock(stock):
        try:
            df = get_stock_data(stock)
            if df is not None:
                fund = get_fundamental_and_industry_data(stock)
                data = analyze_today_score(df, stock, get_institutional_trading(stock), fund)
                if data and data['Score'] >= 1: 
                    # 🚀 在這裡啟動勝率計算！
                    if data['Score'] >= 2:
                        data['WinRate'] = round(calculate_historical_winrate_for_scanner(df), 1)
                    return data
        except: pass
        return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(process_stock, stock): stock for stock in pool_list}
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res: scan_results.append(res)
            completed += 1
            if completed % 20 == 0: logging.info(f"⏳ 進度: {completed} / {len(pool_list)}...")

    try:
        db.collection("market_data").document("daily_scan").set({"data": scan_results, "update_time": firestore.SERVER_TIMESTAMP})
        logging.info(f"✅ 寫入成功！總耗時: {time.time() - start_time:.2f} 秒")
    except Exception as e: logging.error(f"❌ 寫入失敗: {e}")

if __name__ == "__main__":
    run_daily_scan()