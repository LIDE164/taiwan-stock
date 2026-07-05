# scanner.py - 雲端自動掃描機器人 (背景執行版)
import streamlit as st
import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore
import yfinance as yf
import pandas as pd
import requests
import time
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup
import re
import concurrent.futures
import numpy as np
import logging
import functools

# 設定日誌系統，方便在 GitHub Actions 裡面看進度
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# === 讀取金鑰 ===
FINMIND_TOKEN = st.secrets["FINMIND_TOKEN"]
FUGLE_API_KEY = st.secrets["FUGLE_API_KEY"]

# === 初始化 Firebase ===
if not firebase_admin._apps:
    cert_dict = dict(st.secrets["firebase"])
    cred = credentials.Certificate(cert_dict)
    firebase_admin.initialize_app(cred)
db = firestore.client()

# ==========================================
# 核心常數與輔助函數 (無 UI 版)
# ==========================================
ENG_TO_TW_INDUSTRY = {
    "Semiconductors": "半導體業", "Consumer Electronics": "消費性電子", "Electronic Components": "電子零組件",
    "Computer Hardware": "電腦及週邊設備", "Building Materials": "玻璃陶瓷", "Marine Shipping": "航運業",
    "Electrical Equipment & Parts": "電機機械", "Software - Entertainment": "文化創意業", "Technology": "電子科技",
    "Industrials": "工業", "Basic Materials": "原物料", "Financial Services": "金融業",
    "Consumer Cyclical": "非必需消費品", "Healthcare": "生技醫療", "Real Estate": "建材營造",
    "Utilities": "公用事業", "Energy": "能源", "Communication Services": "通信網路"
}
STOCK_NAMES = { "2330": "台積電", "2317": "鴻海", "2454": "聯發科", "2308": "台達電", "2382": "廣達", "3231": "緯創", "2891": "中信金"}

@functools.lru_cache(maxsize=None)
def get_all_tw_stock_names():
    names = STOCK_NAMES.copy()
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", timeout=5)
        if res.status_code == 200:
            for i in res.json(): names[i['Code']] = i['Name']
        res2 = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes", timeout=5)
        if res2.status_code == 200:
            for i in res2.json(): names[i['SecuritiesCompanyCode']] = i['CompanyName']
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
def fetch_twse_index_history():
    try:
        df = yf.Ticker("^TWII").history(period="1y")
        if not df.empty:
            df.index = pd.to_datetime(df.index.strftime('%Y-%m-%d'))
            return df[['Open', 'High', 'Low', 'Close', 'Volume']]
    except: return None

# ==========================================
# 抓取與計算邏輯 (導入 lru_cache 避免重複抓取加速執行)
# ==========================================
@functools.lru_cache(maxsize=None)
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
        except: return None

    df = fetch_twse_index_history() if base_ticker == "^TWII" else fetch_clean(f"{base_ticker}.TW")
    if df is None and base_ticker != "^TWII": df = fetch_clean(f"{base_ticker}.TWO")
    if df is None: return None
    
    try:
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
        
        low_9 = df['Low'].rolling(9).min()
        high_9 = df['High'].rolling(9).max()
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
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1)
        df['ADX'] = dx.ewm(span=14, adjust=False).mean().bfill()
    except Exception as e:
        df['ATR'] = df['Close'] * 0.03
        df['ADX'] = 20
        
    return df

@functools.lru_cache(maxsize=None)
def get_fundamental_and_industry_data(ticker_number, current_price=0):
    base_ticker = str(ticker_number).strip().upper().replace(".TW", "").replace(".TWO", "")
    eps_val, pe_val, ind = "無", "無", "一般產業"
    try:
        info = yf.Ticker(f"{base_ticker}.TW").info
        if not info or 'industry' not in info: info = yf.Ticker(f"{base_ticker}.TWO").info
        sec, ind_eng = info.get("sector", ""), info.get("industry", "")
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

@functools.lru_cache(maxsize=None)
def get_institutional_trading(ticker):
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

# 綜合評分邏輯 (與 test.py 相同，移除文字原因以減輕資料庫負擔)
def analyze_today_score(df, ticker_number, inst_data=None, pre_fund=None):
    if df is None or len(df) < 5: return None
    t, p = df.iloc[-1], df.iloc[-2]
    
    t_open, t_close, t_high, t_low = float(t['Open']), float(t['Close']), float(t['High']), float(t['Low'])
    p_open, p_close = float(p['Open']), float(p['Close'])
    
    f_net_10d, t_net_10d, d_net_10d = 0, 0, 0
    whale_net_buy = 0
    if inst_data:
        f_net_today = sum([int(str(x['外資(張)']).replace(',', '')) for x in inst_data[:3] if str(x['外資(張)']).replace(',', '').lstrip('-').isdigit()])
        t_net_today = sum([int(str(x['投信(張)']).replace(',', '')) for x in inst_data[:3] if str(x['投信(張)']).replace(',', '').lstrip('-').isdigit()])
        d_net_today = sum([int(str(x['自營商(張)']).replace(',', '')) for x in inst_data[:3] if str(x['自營商(張)']).replace(',', '').lstrip('-').isdigit()])
        whale_net_buy = f_net_today + t_net_today + d_net_today

    red_mask = (df['Open'].shift(1) > df['Close'].shift(1)) & (df['Close'] > df['Open']) & (df['Close'] > df['Open'].shift(1)) & (df['Open'] < df['Close'].shift(1))
    total_range = t_high - t_low if t_high - t_low != 0 else 0.001
    lower_shadow = min(t_open, t_close) - t_low
    body = abs(t_close - t_open)

    sc = 0
    adx = t.get('ADX', 0)
    is_trending = adx >= 25 
    
    if (t_close > t.get('20MA', 0)) and (t_close < t.get('5MA', 9999)): sc += 3 if is_trending else 1
    if t_close <= t.get('BB_DN', t_close) * 1.02: sc += 2
    if t.get('BIAS_20', 0) < -5: sc += 1
    if t.get('MACD_Hist', 0) > p.get('MACD_Hist', 0): sc += 2
    else: sc -= 3
    
    if bool(red_mask.iloc[-1]): sc += 4 if is_trending else 1
    if (lower_shadow > body * 1.5) and (lower_shadow / total_range > 0.4) and (t_low < p_close): sc += 2
    if t.get('J', 50) >= 80: sc -= 3
    if t_close >= t.get('BB_UP', t_close) * 0.98: sc -= 2
    if t_close < t.get('20MA', 0): sc -= 2

    feature = "一般狀態"
    if sc >= 2:
        if whale_net_buy > 500: feature = "法人連買"
        elif bool(red_mask.iloc[-1]): feature = "紅吞表態"
        elif (lower_shadow > body * 1.5): feature = "支撐防守"
        
    vwap_approx = (t_open + t_high + t_low + t_close) / 4
    vwap_dev = (t_close - vwap_approx) / vwap_approx * 100
    est_vol_ratio = t['Volume'] / df['Volume'].tail(5).mean() if df['Volume'].tail(5).mean() > 0 else 1
    
    intraday_score = 40
    if vwap_dev > 0: intraday_score += min(30, vwap_dev * 10)
    else: intraday_score += max(-30, vwap_dev * 10)
    if est_vol_ratio > 1.5: intraday_score += 20
    elif est_vol_ratio > 1.0: intraday_score += 10
    if t_close > p_close: intraday_score += 10
    intraday_score = max(10, min(99, int(intraday_score)))
    
    flow = "內外盤拉扯"
    if est_vol_ratio > 1.5 and t_close > vwap_approx: flow = "大單敲進"

    # 動態主題 (簡化版)
    theme_name = "一般題材"
    ind = pre_fund['Industry'] if pre_fund else "一般產業"
    icon_map = { "半導體": "⚙️", "電子": "⚡", "綠能": "🌱", "航運": "🚢", "金融": "💰", "AI": "💡", "機器人": "🤖" }
    for kw, ic in icon_map.items():
        if kw in ind: theme_name = ind; break

    return {
        "代號": ticker_number, "名稱": get_stock_name(ticker_number), "ticker_raw": ticker_number,
        "收盤價": round(t_close, 2), "漲跌幅": round((t_close - p_close) / p_close * 100, 2), "漲跌": round(t_close - p_close, 2),
        "Score": sc, "Intraday_Score": intraday_score, 
        "Feature": feature, "Theme_Name": theme_name,
        "VWAP_Dev": round(vwap_dev, 2), "Est_Vol_Ratio": round(est_vol_ratio, 2), "Flow": flow,
        "Whale_Net": whale_net_buy, "WinRate": 0.0, "RRR": 1.5
    }

# ==========================================
# 🚀 主執行任務
# ==========================================
def run_daily_scan():
    logging.info("🚀 開始執行全市場雷達掃描...")
    start_time = time.time()
    
    # 取得掃描名單
    top_100 = fetch_twse_top_100()
    custom_pool = ["2330", "2317", "2454", "2382", "3231", "2891", "9904", "1809", "0050", "2027", "1409", "3016"]
    pool_list = list(set(top_100 + custom_pool))
    
    logging.info(f"📊 總計掃描標的數量: {len(pool_list)} 檔")
    
    scan_results = []
    completed = 0
    
    def process_stock(stock):
        try:
            df = get_stock_data(stock)
            if df is not None:
                inst_data = get_institutional_trading(stock)
                fund = get_fundamental_and_industry_data(stock, round(df['Close'].iloc[-1], 2))
                data = analyze_today_score(df, stock, inst_data, fund)
                if data and data['Score'] >= 1: # 只存有訊號或分數的，節省資料庫空間
                    return data
        except Exception as e:
            logging.error(f"❌ 掃描 {stock} 失敗: {e}")
        return None

    # 使用多執行緒加速掃描
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(process_stock, stock): stock for stock in pool_list}
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res:
                scan_results.append(res)
            completed += 1
            if completed % 20 == 0:
                logging.info(f"⏳ 進度: {completed} / {len(pool_list)}...")

    # 將結果寫入 Firebase
    logging.info(f"💾 掃描完成！準備將 {len(scan_results)} 筆有效數據寫入 Firebase...")
    
    try:
        db.collection("market_data").document("daily_scan").set({
            "data": scan_results,
            "update_time": firestore.SERVER_TIMESTAMP
        })
        logging.info(f"✅ 寫入成功！總耗時: {time.time() - start_time:.2f} 秒")
    except Exception as e:
        logging.error(f"❌ Firebase 寫入失敗: {e}")

if __name__ == "__main__":
    run_daily_scan()
