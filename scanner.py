# scanner.py - 雲端自動掃描機器人 (上市櫃500檔全市場掃描 + 100分新制同步)
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

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# 初始化 Firebase
if not firebase_admin._apps:
    try:
        firebase_admin.initialize_app(credentials.Certificate(dict(st.secrets["firebase"])))
    except Exception as e:
        logging.error(f"Firebase 初始化失敗: {e}")
db = firestore.client()

# ==========================================
# 1. 建立產業快取字典 (解決 API 連續請求痛點)
# ==========================================
INDUSTRY_CACHE = {}
def build_industry_cache():
    global INDUSTRY_CACHE
    logging.info("📦 正在建立全市場產業快取字典...")
    
    # 建立產業對照表
    eng_to_tw = {
        "Semiconductors": "半導體", "Electronic Components": "電子零組件",
        "Computer Hardware": "電腦及週邊設備", "Marine Shipping": "航運業", 
        "Financial Services": "金融業", "Technology": "電子科技"
    }
    
    # 上市
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", timeout=10)
        if res.status_code == 200:
            for item in res.json(): INDUSTRY_CACHE[item['Code']] = item.get('Name', '')
    except: pass
    
    # 上櫃
    try:
        res2 = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes", timeout=10)
        if res2.status_code == 200:
            for item in res2.json(): INDUSTRY_CACHE[item['SecuritiesCompanyCode']] = item.get('CompanyName', '')
    except: pass

def get_real_industry(ticker):
    # 優先從 Yahoo 抓取板塊，若無則回傳一般產業 (避免迴圈內發送 HTTP 請求)
    try:
        info = yf.Ticker(f"{ticker}.TW").info
        sector = info.get("sector", "")
        if sector:
            # 簡易中文化轉換
            themes = {"Semiconductors": "半導體", "Technology": "電子科技", "Financial Services": "金融業", "Industrials": "工業"}
            return themes.get(sector, sector)
    except: pass
    return "一般產業"

# ==========================================
# 2. 獲取全市場成交量 Top 500 (合併上市與上櫃)
# ==========================================
def fetch_top_500():
    all_stocks = []
    logging.info("🔍 正在獲取上市與上櫃成交量排行...")
    
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", timeout=10)
        df_twse = pd.DataFrame(res.json())
        df_twse['TradeVolume'] = pd.to_numeric(df_twse['TradeVolume'], errors='coerce')
        all_stocks.append(df_twse[['Code', 'TradeVolume']])
    except: pass

    try:
        res2 = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes", timeout=10)
        df_tpex = pd.DataFrame(res2.json())
        df_tpex = df_tpex.rename(columns={'SecuritiesCompanyCode': 'Code', 'TradingVolume': 'TradeVolume'})
        df_tpex['TradeVolume'] = pd.to_numeric(df_tpex['TradeVolume'], errors='coerce')
        all_stocks.append(df_tpex[['Code', 'TradeVolume']])
    except: pass

    if all_stocks:
        df_all = pd.concat(all_stocks, ignore_index=True)
        # 篩選掉非數字的 ETF 或權證 (簡易過濾)
        df_all = df_all[df_all['Code'].str.match(r'^\d{4}$')]
        return df_all.sort_values(by='TradeVolume', ascending=False).head(500)['Code'].tolist()
    else:
        return ["2330", "2317", "2454", "3231", "2382"]

# ==========================================
# 3. 核心運算與指標 (與 charts.py 同步)
# ==========================================
def get_stock_data(ticker_number):
    try:
        df = yf.Ticker(f"{ticker_number}.TW").history(period="1y").dropna(subset=['Close'])
        if df.empty: df = yf.Ticker(f"{ticker_number}.TWO").history(period="1y").dropna(subset=['Close'])
        if df.empty or len(df) < 20: return None
        
        df.index = pd.to_datetime(df.index.strftime('%Y-%m-%d'))
        df = df[~df.index.duplicated(keep='last')]
        
        df['5MA'] = df['Close'].rolling(5).mean()
        df['10MA'] = df['Close'].rolling(10).mean()
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

def compute_ai_signals_score(df):
    """完美同步 test.py 與 charts.py 的 AI 100分模型邏輯"""
    t, p = df.iloc[-1], df.iloc[-2]
    
    trend_up = (t['20MA'] > t['60MA']) and (t['Close'] > t['20MA'])
    momentum = (t['Close'] - df['Close'].iloc[-4]) / df['Close'].iloc[-4] if len(df)>4 else 0
    vol_strength = t['Volume'] > df['Volume'].tail(5).mean() * 1.3
    breakout = t['Close'] > df['High'].tail(21).iloc[:20].max() # 突破前20日新高
    
    score = 0
    if trend_up: score += 30
    if breakout: score += 30
    if vol_strength: score += 20
    if momentum > 0: score += 20
    
    # 疊加額外技術型態加扣分
    adx = t.get('ADX', 0)
    if adx >= 25: score += 5
    if t.get('MACD_Hist', 0) > p.get('MACD_Hist', 0): score += 8
    
    red_engulf = (p['Open'] > p['Close']) and (t['Close'] > t['Open']) and (t['Close'] > p['Open']) and (t['Open'] < p['Close'])
    if red_engulf: score += 10
    if t.get('J', 50) > 90: score -= 5
    if t.get('BIAS', 0) > 10: score -= 5
    
    final_score = max(5, min(99, int(score)))
    
    if final_score >= 60: label = "🟢 強勢買進"
    elif final_score >= 45: label = "🟡 偏多觀察"
    else: label = "⚪ 忽略"
    
    return final_score, label

def calculate_historical_winrate(df_slice):
    if df_slice is None or len(df_slice) < 14: return 0.0
    recent_90 = df_slice.tail(90)
    wins, closed_signals, last_buy_idx = 0, 0, -999
    start_idx = len(df_slice) - len(recent_90)
    
    for idx in range(len(recent_90)):
        actual_idx = start_idx + idx
        if actual_idx - last_buy_idx < 5: continue
        temp_df = df_slice.iloc[:actual_idx + 1]
        
        if len(temp_df) >= 20:
            t = temp_df.iloc[-1]
            sc, _ = compute_ai_signals_score(temp_df)
            
            if sc >= 60: # 嚴格篩選 60 分以上才視為買點回測
                last_buy_idx = actual_idx
                buy_price = t['Close']
                atr_val = temp_df['ATR'].iloc[-1] if 'ATR' in temp_df.columns else buy_price * 0.03
                target_p, stop_p = buy_price + (atr_val * 1.5), buy_price - (atr_val * 1.0)
                
                future_df = df_slice.iloc[actual_idx + 1 : actual_idx + 10]
                if len(future_df) > 0:
                    closed_signals += 1
                    if future_df['High'].max() >= target_p and future_df['Low'].min() > stop_p: wins += 1
                    elif future_df['Close'].iloc[-1] > buy_price and future_df['Low'].min() > stop_p: wins += 1
                    
    return round((wins / closed_signals * 100), 1) if closed_signals > 0 else 0.0

# ==========================================
# 4. 執行主迴圈
# ==========================================
def run_daily_scan():
    logging.info("🚀 開始執行全市場 500 檔雷達掃描...")
    build_industry_cache()
    
    pool = list(set(fetch_top_500() + ["2330", "2317", "2454"]))
    scan_results = []
    
    def process_stock(stock):
        df = get_stock_data(stock)
        if df is not None:
            ind = get_real_industry(stock)
            t_close = df['Close'].iloc[-1]
            p_close = df['Close'].iloc[-2]
            
            sc, label = compute_ai_signals_score(df)
            
            # 盤後資料只存 45 分以上的，減輕資料庫負擔，加速前端讀取
            if sc >= 45:
                win_rate = calculate_historical_winrate(df)
                return {
                    "代號": stock, 
                    "名稱": INDUSTRY_CACHE.get(stock, stock),
                    "Score": sc, 
                    "評級": label, 
                    "產業": ind, 
                    "收盤價": round(t_close, 2), 
                    "WinRate": win_rate,
                    "漲跌幅": round((t_close - p_close)/p_close*100, 2)
                }
        return None

    # 使用多執行緒平行運算加速抓取
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        for res in executor.map(process_stock, pool):
            if res: scan_results.append(res)
            
    # 按分數從高到低排序後寫入
    scan_results = sorted(scan_results, key=lambda x: (x['Score'], x['漲跌幅']), reverse=True)
            
    db.collection("market_data").document("daily_scan").set({"data": scan_results, "update_time": firestore.SERVER_TIMESTAMP})
    logging.info(f"✅ 掃描完成！共篩選出 {len(scan_results)} 檔強勢標的寫入雲端。")

if __name__ == "__main__":
    run_daily_scan()