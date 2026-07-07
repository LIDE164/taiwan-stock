# scanner.py - 雲端自動掃描機器人 (上市櫃500檔全市場掃描 + 100分新制決策大腦同步版)
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
# 1. 建立產業快取字典 (全市場中文對應)
# ==========================================
ENG_TO_TW_INDUSTRY = {
    "Semiconductors": "半導體", "Consumer Electronics": "消費性電子", "Electronic Components": "電子零組件",
    "Computer Hardware": "電腦及週邊設備", "Marine Shipping": "航運業", "Financial Services": "金融業",
    "Building Materials": "玻璃陶瓷", "Electrical Equipment & Parts": "電機機械", "Software - Entertainment": "文化創意", 
    "Technology": "電子科技", "Industrials": "工業", "Basic Materials": "原物料", "Consumer Cyclical": "非必需消費品", 
    "Healthcare": "生技醫療", "Real Estate": "建材營造", "Utilities": "公用事業", "Energy": "能源", 
    "Communication Services": "通信網路", "Auto Parts": "汽車工業", "Chemicals": "化學工業", 
    "Textile Manufacturing": "紡織纖維", "Food": "食品工業", "Steel": "鋼鐵工業", "Rubber": "橡膠工業", 
    "Plastics": "塑膠工業", "Biotechnology": "生技醫療", "Specialty Retail": "貿易百貨", "Consumer Defensive": "核心消費品"
}

INDUSTRY_CACHE = {}
def build_industry_cache():
    global INDUSTRY_CACHE
    logging.info("📦 正在建立全市場產業快取字典...")
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", timeout=10)
        if res.status_code == 200:
            for item in res.json(): INDUSTRY_CACHE[item['Code']] = item.get('Name', '')
    except: pass
    try:
        res2 = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes", timeout=10)
        if res2.status_code == 200:
            for item in res2.json(): INDUSTRY_CACHE[item['SecuritiesCompanyCode']] = item.get('CompanyName', '')
    except: pass

def get_real_industry(ticker):
    try:
        info = yf.Ticker(f"{ticker}.TW").info
        sector = info.get("sector", "")
        if sector in ENG_TO_TW_INDUSTRY: return ENG_TO_TW_INDUSTRY[sector]
        if sector: return sector
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
        df_all = df_all[df_all['Code'].str.match(r'^\d{4}$')]
        return df_all.sort_values(by='TradeVolume', ascending=False).head(500)['Code'].tolist()
    else:
        return ["2330", "2317", "2454", "3231", "2382"]

# ==========================================
# 3. 核心運算與指標
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
        
        df['STD20'] = df['Close'].rolling(20).std()
        df['BB_UP'] = df['20MA'] + (2 * df['STD20'])
        df['BB_DN'] = df['20MA'] - (2 * df['STD20'])
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

# 同步 test.py 的決策大腦
def get_decision_score(data, fund_data):
    sc = 0
    adx = data.get('ADX', 0)
    roc_20 = data.get('ROC_20', 0)
    is_trending = adx >= 25 
    
    if data.get('訊號', False): sc += 3 if is_trending else 1
    if data['收盤價'] <= data.get('BB_DN', 0) * 1.02: sc += 2
    if data.get('BIAS', 0) < -5: sc += 1
    
    if roc_20 > 10: sc += 2
    elif roc_20 < -5: sc -= 2
    
    if data.get('MoM', 0) > 0 and data.get('YoY', 0) > 0: sc += 3
    elif data.get('YoY', 0) > 15: sc += 2
        
    try: eps_f = float(str(fund_data.get('EPS', '0')).replace(',', ''))
    except: eps_f = 0.0
    if eps_f > 0: sc += 2
    
    if data.get('成交量', 0) > data.get('5日均量', 0) * 1.1: sc += 2
    else: sc -= 1
        
    if data.get('MACD柱', 0) > data.get('前日MACD柱', -999): sc += 2
    else: sc -= 3

    if data.get('紅吞', False): sc += 4 if is_trending else 1
    if data.get('黑吞', False): sc -= 3
    if data.get('回測有撐', False): sc += 2
    if data.get('反彈遇壓', False): sc -= 2
    
    if data.get('J值', 50) >= 80: sc -= 3
    if data['收盤價'] >= data.get('BB_UP', 9999) * 0.98: sc -= 2
    if data.get('BIAS', 0) > 7: sc -= 2
    if data['收盤價'] < data.get('20MA', 0): sc -= 2
    if eps_f < 0: sc -= 1

    final_score = max(5, min(99, int(50 + sc * 3)))
    if final_score >= 60: label = "🟢 強勢買進"
    elif final_score >= 45: label = "🟡 偏多觀察"
    else: label = "⚪ 忽略"
    return final_score, label

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
            t = df.iloc[-1]
            p = df.iloc[-2]
            t_close, t_open, t_high, t_low = t['Close'], t['Open'], t['High'], t['Low']
            p_close, p_open = p['Close'], p['Open']
            
            # 基本面預設為0 (盤後掃描只抓技術面分數與粗略基本面)
            fund = {"EPS": "0", "MoM": 0, "YoY": 0} 
            try:
                info = yf.Ticker(f"{stock}.TW").info
                if 'trailingEps' in info: fund['EPS'] = str(info['trailingEps'])
            except: pass

            red_mask = (p_open > p_close) and (t_close > t_open) and (t_close > p_open) and (t_open < p_close)
            black_mask = (p_close > p_open) and (t_open > t_close) and (t_open > p_close) and (t_close < p_open)

            body_len = abs(t_close - t_open)
            lower_shadow = min(t_close, t_open) - t_low
            upper_shadow = t_high - max(t_close, t_open)
            has_support = (lower_shadow > body_len * 1.5) and (t['Volume'] > df['Volume'].tail(5).mean())
            hit_pressure = (upper_shadow > body_len * 1.5)

            data = {
                "ADX": t.get('ADX', 0),
                "ROC_20": (t_close - df['Close'].iloc[-20])/df['Close'].iloc[-20]*100 if len(df)>=20 else 0,
                "訊號": t_close > t.get('20MA', t_close),
                "收盤價": t_close, "BB_DN": t.get('BB_DN', t_close), "BB_UP": t.get('BB_UP', t_close),
                "BIAS": t.get('BIAS', 0), "MoM": fund['MoM'], "YoY": fund['YoY'],
                "成交量": t['Volume'], "5日均量": df['Volume'].tail(5).mean(),
                "MACD柱": t.get('MACD_Hist', 0), "前日MACD柱": p.get('MACD_Hist', 0),
                "紅吞": red_mask, "黑吞": black_mask, "回測有撐": has_support, "反彈遇壓": hit_pressure,
                "5MA": t.get('5MA', t_close), "20MA": t.get('20MA', t_close),
                "5日線即將上彎": t_close >= df['Close'].iloc[-5] if len(df)>=5 else False,
                "J值": t.get('J', 50)
            }
            
            sc, label = get_decision_score(data, fund)
            
            if sc >= 45:
                # 簡單計算勝率
                wins, closed_signals, last_buy_idx = 0, 0, -999
                recent_90 = df.tail(90)
                start_idx = len(df) - len(recent_90)
                for idx in range(len(recent_90)):
                    actual_idx = start_idx + idx
                    if actual_idx - last_buy_idx < 5: continue
                    temp_df = df.iloc[:actual_idx + 1]
                    if len(temp_df) >= 20:
                        t_t = temp_df.iloc[-1]
                        if t_t['Close'] > t_t.get('20MA', 0) and t_t.get('MACD_Hist', 0) > temp_df.iloc[-2].get('MACD_Hist', 0):
                            last_buy_idx = actual_idx
                            atr_val = temp_df['ATR'].iloc[-1] if 'ATR' in temp_df.columns else t_t['Close'] * 0.03
                            target_p, stop_p = t_t['Close'] + (atr_val * 1.5), t_t['Close'] - (atr_val * 1.0)
                            future_df = df.iloc[actual_idx + 1 : actual_idx + 10]
                            if len(future_df) > 0:
                                closed_signals += 1
                                if future_df['High'].max() >= target_p and future_df['Low'].min() > stop_p: wins += 1
                                elif future_df['Close'].iloc[-1] > t_t['Close'] and future_df['Low'].min() > stop_p: wins += 1
                win_rate = round((wins / closed_signals * 100), 1) if closed_signals > 0 else 0.0

                return {
                    "代號": stock, "名稱": INDUSTRY_CACHE.get(stock, stock),
                    "Score": sc, "評級": label, "產業": ind, 
                    "收盤價": round(t_close, 2), "WinRate": win_rate,
                    "漲跌幅": round((t_close - p_close)/p_close*100, 2)
                }
        return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        for res in executor.map(process_stock, pool):
            if res: scan_results.append(res)
            
    scan_results = sorted(scan_results, key=lambda x: (x['Score'], x['漲跌幅']), reverse=True)
            
    db.collection("market_data").document("daily_scan").set({"data": scan_results, "update_time": firestore.SERVER_TIMESTAMP})
    logging.info(f"✅ 掃描完成！共篩選出 {len(scan_results)} 檔標的。")

if __name__ == "__main__":
    run_daily_scan()