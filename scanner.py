# scanner.py - 雲端自動掃描機器人
import firebase_admin
from firebase_admin import credentials, firestore
import yfinance as yf
import pandas as pd
import requests
import concurrent.futures
import logging
from datetime import datetime, timezone, timedelta
import numpy as np
import streamlit as st

# 引入共用核心演算法
from scoring import get_decision_score

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

if not firebase_admin._apps:
    try:
        firebase_admin.initialize_app(credentials.Certificate(dict(st.secrets["firebase"])))
    except Exception as e:
        logging.error(f"Firebase 初始化失敗: {e}")
db = firestore.client()

FINMIND_TOKEN = st.secrets["FINMIND_TOKEN"]

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

def get_fundamental_and_industry_data(ticker_number, current_price=0):
    base_ticker = str(ticker_number).strip().upper().replace(".TW", "").replace(".TWO", "")
    eps_val, ind = "0", "一般產業"
    try:
        info = yf.Ticker(f"{base_ticker}.TW").info
        if not info or 'industry' not in info: info = yf.Ticker(f"{base_ticker}.TWO").info
        raw_sector = info.get("sector", "")
        if raw_sector in ENG_TO_TW_INDUSTRY: ind = ENG_TO_TW_INDUSTRY[raw_sector]
        elif info.get("industry") in ENG_TO_TW_INDUSTRY: ind = ENG_TO_TW_INDUSTRY[info.get("industry")]
        if ind == "一般產業":
            res_cnyes = requests.get(f"https://ws.cnyes.com/twstock/api/v1/company/profile/{base_ticker}", timeout=3).json()
            if 'data' in res_cnyes and 'categoryName' in res_cnyes['data']: ind = res_cnyes['data']['categoryName']
        if 'trailingEps' in info and info['trailingEps'] is not None: eps_val = str(round(info['trailingEps'], 2))
    except: pass
    return {"EPS": eps_val, "Industry": ind}

def get_finmind_chip_and_revenue(ticker):
    big_player_ratio, mom, yoy = 0.0, 0.0, 0.0
    base_ticker = str(ticker).strip().upper().replace(".TW", "").replace(".TWO", "")
    try:
        start_date_rev = (datetime.now() - timedelta(days=500)).strftime('%Y-%m-%d')
        url_rev = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockMonthRevenue&data_id={base_ticker}&start_date={start_date_rev}&token={FINMIND_TOKEN}"
        res_rev = requests.get(url_rev, timeout=5).json()
        if 'data' in res_rev and len(res_rev['data']) > 0:
            df_rev = pd.DataFrame(res_rev['data']).sort_values(by='date').reset_index(drop=True)
            df_rev['revenue'] = pd.to_numeric(df_rev['revenue'], errors='coerce').fillna(0)
            if len(df_rev) >= 2 and df_rev['revenue'].iloc[-2] > 0:
                mom = (df_rev['revenue'].iloc[-1] - df_rev['revenue'].iloc[-2]) / df_rev['revenue'].iloc[-2] * 100
            if len(df_rev) >= 13 and df_rev['revenue'].iloc[-13] > 0:
                yoy = (df_rev['revenue'].iloc[-1] - df_rev['revenue'].iloc[-13]) / df_rev['revenue'].iloc[-13] * 100
    except: pass
    return round(big_player_ratio, 2), round(mom, 2), round(yoy, 2)

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
    else: return ["2330", "2317", "2454", "3231", "2382"]

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

def run_daily_scan():
    logging.info("🚀 開始執行全市場 500 檔雷達掃描...")
    build_industry_cache()
    
    pool = list(set(fetch_top_500() + ["2330", "2317", "2454"]))
    scan_results = []
    
    def process_stock(stock):
        df = get_stock_data(stock)
        if df is not None:
            t = df.iloc[-1]
            p = df.iloc[-2]
            t_close, t_open, t_high, t_low = t['Close'], t['Open'], t['High'], t['Low']
            p_close, p_open = p['Close'], p['Open']
            
            basic_tech_sc = t_close > t.get('20MA', t_close)
            if not basic_tech_sc and t.get('MACD_Hist', 0) < 0:
                return None

            f_data = get_fundamental_and_industry_data(stock, t_close)
            bp, mom, yoy = get_finmind_chip_and_revenue(stock)
            fund = {"EPS": f_data.get('EPS', '0'), "MoM": mom, "YoY": yoy}

            red_mask = (p_open > p_close) and (t_close > t_open) and (t_close > p_open) and (t_open < p_close)
            black_mask = (p_close > p_open) and (t_open > t_close) and (t_open > p_close) and (t_close < p_open)

            body_len = abs(t_close - t_open)
            has_support = (min(t_close, t_open) - t_low > body_len * 1.5) and (t['Volume'] > df['Volume'].tail(5).mean())
            hit_pressure = (t_high - max(t_close, t_open) > body_len * 1.5)

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
            
            # ⭐ 調用共用演算法並取得 Reasons，嚴格帶入 mode="post"
            sc, label, rs, feature = get_decision_score(data, fund, mode="post", with_reason=True)
            
            if sc >= 45:
                return {
                    "代號": stock, "名稱": INDUSTRY_CACHE.get(stock, stock),
                    "Score": sc, "評級": label, "產業": f_data['Industry'], 
                    "收盤價": round(t_close, 2), "WinRate": 0.0,
                    "漲跌幅": round((t_close - p_close)/p_close*100, 2),
                    "Feature": feature, "Reasons": rs, # ⭐ 儲存解析結果供前端直接調用
                    "EPS": fund['EPS'], "MoM": fund['MoM'], "YoY": fund['YoY']
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