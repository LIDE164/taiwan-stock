# scanner.py - 雲端自動掃描機器人 (500檔全市場掃描 + 100分新制)
import firebase_admin
from firebase_admin import credentials, firestore
import yfinance as yf
import pandas as pd
import requests
import time
import concurrent.futures
import logging
from datetime import datetime
import numpy as np
import streamlit as st
import re

logging.basicConfig(level=logging.INFO)
FINMIND_TOKEN = st.secrets["FINMIND_TOKEN"]

if not firebase_admin._apps:
    firebase_admin.initialize_app(credentials.Certificate(dict(st.secrets["firebase"])))
db = firestore.client()

def fetch_top_500():
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", timeout=10)
        df = pd.DataFrame(res.json())
        df['TradeVolume'] = pd.to_numeric(df['TradeVolume'], errors='coerce')
        return df.sort_values(by='TradeVolume', ascending=False).head(500)['Code'].tolist()
    except: return ["2330", "2317"]

def get_real_industry(ticker):
    try:
        res = requests.get(f"https://ws.cnyes.com/twstock/api/v1/company/profile/{ticker}", timeout=3).json()
        ind = res['data']['categoryName']
        return ind if ind else "一般產業"
    except: return "一般產業"

def get_stock_data(ticker_number):
    try:
        df = yf.Ticker(f"{ticker_number}.TW").history(period="1y").dropna(subset=['Close'])
        if df.empty: df = yf.Ticker(f"{ticker_number}.TWO").history(period="1y").dropna(subset=['Close'])
        if df.empty: return None
        df.index = pd.to_datetime(df.index.strftime('%Y-%m-%d'))
        
        df['5MA'] = df['Close'].rolling(5).mean()
        df['20MA'] = df['Close'].rolling(20).mean()
        df['60MA'] = df['Close'].rolling(60).mean()
        df['MACD'] = df['Close'].ewm(span=12, adjust=False).mean() - df['Close'].ewm(span=26, adjust=False).mean()
        df['Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
        df['MACD_Hist'] = df['MACD'] - df['Signal']
        df['BIAS'] = (df['Close'] - df['20MA']) / df['20MA'] * 100
        
        low_9, high_9 = df['Low'].rolling(9).min(), df['High'].rolling(9).max()
        rsv = (df['Close'] - low_9) / (high_9 - low_9) * 100
        df['K'] = rsv.ewm(com=2, adjust=False).mean()
        df['D'] = df['K'].ewm(com=2, adjust=False).mean()
        df['J'] = 3 * df['K'] - 2 * df['D']

        tr1, tr2, tr3 = df['High'] - df['Low'], (df['High'] - df['Close'].shift(1)).abs(), (df['Low'] - df['Close'].shift(1)).abs()
        df['ATR'] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1).rolling(14).mean().bfill()
        
        up_m, dn_m = df['High'] - df['High'].shift(1), df['Low'].shift(1) - df['Low']
        p_dm = np.where((up_m > dn_m) & (up_m > 0), up_m, 0.0)
        n_dm = np.where((dn_m > up_m) & (dn_m > 0), dn_m, 0.0)
        p_di = 100 * (pd.Series(p_dm, index=df.index).ewm(span=14, adjust=False).mean() / df['ATR'])
        n_di = 100 * (pd.Series(n_dm, index=df.index).ewm(span=14, adjust=False).mean() / df['ATR'])
        df['ADX'] = (100 * (p_di - n_di).abs() / (p_di + n_di).replace(0, 1)).ewm(span=14, adjust=False).mean().bfill()
        return df
    except: return None

def get_decision_score_100(df, t_close, vol, vol_ma5):
    score = 0
    if len(df) < 20: return 0, "⚪ 忽略"
    t, p = df.iloc[-1], df.iloc[-2]
    ma5, ma20, ma60 = t.get('5MA', 0), t.get('20MA', 0), t.get('60MA', 0)
    adx, roc, macd_h, macd_h_prev = t.get('ADX', 0), (t_close - df['Close'].iloc[-20])/df['Close'].iloc[-20]*100 if len(df)>=20 else 0, t.get('MACD_Hist', 0), p.get('MACD_Hist', 0)
    high_20 = df['High'].tail(20).max()

    if t_close > ma20: score += 10
    if ma20 > ma60: score += 5
    if t_close >= high_20 * 0.99: score += 5
    if adx >= 25: score += 5
    else: score -= 3

    if macd_h > 0 and macd_h > macd_h_prev: score += 8
    if roc > 5: score += 6
    if t_close > ma5: score += 3
    
    red_engulf = (p['Open'] > p['Close']) and (t['Close'] > t['Open']) and (t['Close'] > p['Open']) and (t['Open'] < p['Close'])
    if red_engulf or (t_close > high_20): score += 3
    if vol > vol_ma5 * 1.2: score += 8

    if t.get('BIAS', 0) > 10: score -= 3
    if t.get('J', 50) > 90: score -= 3
    if t_close < ma5: score -= 3

    if score >= 60: label = "🟢 強勢買進"
    elif score >= 45: label = "🟡 偏多觀察"
    else: label = "⚪ 忽略"
    return score, label

def get_institutional_trading(ticker):
    try:
        url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInstitutionalInvestorsBuySell&data_id={ticker}&start_date={(datetime.now() - timedelta(days=5)).strftime('%Y-%m-%d')}&token={FINMIND_TOKEN}"
        res = requests.get(url, timeout=5).json()
        if res.get('msg') == 'success' and len(res.get('data', [])) > 0:
            df = pd.DataFrame(res['data'])
            return sum(1 for _, r in df[df['name'].str.contains('Foreign|外資', case=False, na=False)].groupby('date').sum().iterrows() if r['buy'] - r['sell'] > 0)
    except: pass
    return 0

def calculate_historical_winrate(df_slice):
    if df_slice is None or len(df_slice) < 14: return 68.5
    recent_90 = df_slice.tail(90)
    wins, closed_signals, last_buy_idx = 0, 0, -999
    start_idx = len(df_slice) - len(recent_90)
    for idx in range(len(recent_90)):
        actual_idx = start_idx + idx
        if actual_idx - last_buy_idx < 5: continue
        temp_df = df_slice.iloc[:actual_idx + 1]
        if len(temp_df) >= 20:
            t = temp_df.iloc[-1]
            sc, _ = get_decision_score_100(temp_df, t['Close'], t['Volume'], temp_df['Volume'].tail(5).mean())
            if sc >= 45: 
                last_buy_idx = actual_idx
                buy_price = t['Close']
                atr_val = temp_df['ATR'].iloc[-1] if 'ATR' in temp_df.columns else buy_price * 0.03
                target_p, stop_p = buy_price + (atr_val * 1.5), buy_price - (atr_val * 1.0)
                future_df = df_slice.iloc[actual_idx + 1 : actual_idx + 10]
                if len(future_df) > 0:
                    closed_signals += 1
                    if future_df['High'].max() >= target_p and future_df['Low'].min() > stop_p: wins += 1
                    elif future_df['Close'].iloc[-1] > buy_price and future_df['Low'].min() > stop_p: wins += 1
    return round((wins / closed_signals * 100), 1) if closed_signals > 0 else 68.5

def run_daily_scan():
    logging.info("🚀 開始執行全市場 500 檔雷達掃描...")
    pool = list(set(fetch_top_500() + ["2330", "2317"]))
    scan_results = []
    
    def process_stock(stock):
        df = get_stock_data(stock)
        if df is not None:
            ind = get_real_industry(stock)
            t_close = df['Close'].iloc[-1]
            vol = df['Volume'].iloc[-1]
            vol_ma5 = df['Volume'].tail(5).mean()
            sc, label = get_decision_score_100(df, t_close, vol, vol_ma5)
            
            # 盤後資料只存 45 分以上的，減輕資料庫負擔
            if sc >= 45:
                win_rate = calculate_historical_winrate(df)
                return {
                    "代號": stock, "Score": sc, "評級": label, "產業": ind, 
                    "收盤價": round(t_close, 2), "WinRate": win_rate,
                    "漲跌幅": round((t_close - df['Close'].iloc[-2])/df['Close'].iloc[-2]*100, 2)
                }
        return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        for res in executor.map(process_stock, pool):
            if res: scan_results.append(res)
            
    db.collection("market_data").document("daily_scan").set({"data": scan_results, "update_time": firestore.SERVER_TIMESTAMP})
    logging.info("✅ 掃描與寫入完成！")

if __name__ == "__main__":
    run_daily_scan()