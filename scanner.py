# scanner.py - 雲端自動掃描機器人
import firebase_admin
from firebase_admin import credentials, firestore
import yfinance as yf
import pandas as pd
import requests
import concurrent.futures
import logging
import os
from datetime import datetime, timezone, timedelta
import numpy as np
import streamlit as st

# 引入共用核心演算法
from analysis_core import BACKTEST_LOOKBACK_DAYS, apply_technical_indicators, build_score_input, calculate_historical_performance
from scoring import get_decision_score

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def get_secret(name, default=""):
    try:
        value = st.secrets.get(name, "")
    except Exception:
        value = ""
    return value or os.getenv(name, default)


def init_firestore():
    try:
        if not firebase_admin._apps:
            firebase_admin.initialize_app(credentials.Certificate(dict(st.secrets["firebase"])))
        return firestore.client()
    except Exception as e:
        logging.error("Firebase 初始化失敗: %s", e)
        return None


db = init_firestore()
FINMIND_TOKEN = get_secret("FINMIND_TOKEN")

FALLBACK_SCAN_POOL = [
    "2330", "2317", "2454", "2308", "2382", "2412", "2881", "2882", "2891", "2886",
    "2303", "3711", "2357", "2379", "3034", "3008", "3231", "3661", "3017", "3324",
    "2345", "2360", "2356", "2327", "4938", "2376", "6669", "5269", "2395", "2059",
    "2603", "2609", "2615", "2618", "2606", "2610", "2618", "2637", "2645", "5608",
    "1301", "1303", "1326", "6505", "2002", "2014", "2027", "2105", "2201", "2207",
    "1216", "1227", "1231", "1402", "1476", "1590", "1605", "1717", "1722", "1785",
    "1802", "1904", "2006", "2049", "2408", "2409", "2449", "2498", "2515", "2542",
    "2614", "2801", "2809", "2880", "2883", "2884", "2885", "2887", "2890", "2892",
    "2912", "3037", "3045", "3094", "3105", "3189", "3406", "3443", "3481", "3533",
    "3702", "4904", "5347", "5434", "5871", "5876", "6176", "6239", "6415", "8046",
]
MIN_SCAN_RESULTS_TO_OVERWRITE = 20

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
    if not FINMIND_TOKEN:
        logging.warning("FINMIND_TOKEN 未設定，略過 %s 的 FinMind 資料", base_ticker)
        return big_player_ratio, mom, yoy
    try:
        start_date_chip = (datetime.now() - timedelta(days=60)).strftime('%Y-%m-%d')
        url_chip = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockHoldingSharesPer&data_id={base_ticker}&start_date={start_date_chip}&token={FINMIND_TOKEN}"
        res_chip = requests.get(url_chip, timeout=5).json()
        if 'data' in res_chip and len(res_chip['data']) > 0:
            latest_date = max([x.get('date', '') for x in res_chip['data']])
            for x in res_chip['data']:
                if x.get('date') == latest_date and int(x.get('HoldingSharesLevel', 0)) >= 12:
                    big_player_ratio += float(str(x.get('percent', 0)).replace(',', ''))

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
    except Exception as e:
        logging.warning("FinMind 資料取得失敗 %s: %s", base_ticker, e)
    return round(big_player_ratio, 2), round(mom, 2), round(yoy, 2)

def fetch_top_500():
    all_stocks = []
    logging.info("🔍 正在獲取上市與上櫃成交量排行...")
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", timeout=10)
        res.raise_for_status()
        df_twse = pd.DataFrame(res.json())
        df_twse['TradeVolume'] = pd.to_numeric(df_twse['TradeVolume'], errors='coerce')
        all_stocks.append(df_twse[['Code', 'TradeVolume']])
    except Exception as e:
        logging.warning("上市成交量排行取得失敗，改用備援池: %s", e)
    try:
        res2 = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes", timeout=10)
        res2.raise_for_status()
        df_tpex = pd.DataFrame(res2.json())
        df_tpex = df_tpex.rename(columns={'SecuritiesCompanyCode': 'Code', 'TradingVolume': 'TradeVolume'})
        df_tpex['TradeVolume'] = pd.to_numeric(df_tpex['TradeVolume'], errors='coerce')
        all_stocks.append(df_tpex[['Code', 'TradeVolume']])
    except Exception as e:
        logging.warning("上櫃成交量排行取得失敗，改用備援池: %s", e)

    if all_stocks:
        df_all = pd.concat(all_stocks, ignore_index=True)
        df_all = df_all[df_all['Code'].str.match(r'^\d{4}$')]
        ranked = df_all.sort_values(by='TradeVolume', ascending=False).head(500)['Code'].tolist()
        merged = list(dict.fromkeys(ranked + FALLBACK_SCAN_POOL))
        logging.info("股票池完成：交易所 %s 檔，合併備援後 %s 檔", len(ranked), len(merged))
        return merged
    logging.warning("交易所股票池完全取得失敗，使用核心備援池 %s 檔", len(FALLBACK_SCAN_POOL))
    return FALLBACK_SCAN_POOL

def get_stock_data(ticker_number):
    try:
        df = yf.Ticker(f"{ticker_number}.TW").history(period="1y").dropna(subset=['Close'])
        if df.empty: df = yf.Ticker(f"{ticker_number}.TWO").history(period="1y").dropna(subset=['Close'])
        if df.empty or len(df) < 20: return None
        
        df.index = pd.to_datetime(df.index.strftime('%Y-%m-%d'))
        df = df[~df.index.duplicated(keep='last')]
        return apply_technical_indicators(df)
    except Exception as e:
        logging.warning("股價資料處理失敗 %s: %s", ticker_number, e)
        return None

# ⭐ 補上法人籌碼抓取功能
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
            for col in ['外資', '投信', '自營商']:
                if col not in pivot.columns: pivot[col] = 0
            pivot['單日合計'] = pivot['外資'] + pivot['投信'] + pivot['自營商']
            return [{"單日合計(張)": int(r['單日合計'])} for _, r in pivot.sort_values('date', ascending=False).head(10).iterrows()]
    except: pass
    return []

# ⭐ 補上歷史勝率簡易精算器
def calc_winrate(df_slice):
    result = calculate_historical_performance(df_slice, 1.5, 1.0, lookback_days=BACKTEST_LOOKBACK_DAYS)
    return result.get("win_rate", 0.0), result.get("closed_signals", 0)

def should_run_postclose_scan(now_tpe=None):
    now_tpe = now_tpe or datetime.now(timezone(timedelta(hours=8)))
    if os.getenv("FORCE_SCAN") == "1":
        return True
    if now_tpe.weekday() >= 5:
        return False
    postclose_time = now_tpe.replace(hour=14, minute=30, second=0, microsecond=0)
    return now_tpe >= postclose_time

def run_daily_scan():
    if not should_run_postclose_scan():
        logging.info("尚未到台北時間 14:30 盤後掃描時間，本次略過。")
        return []
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
            fund = {"EPS": f_data.get('EPS', '0'), "MoM": mom, "YoY": yoy, "BigPlayer": bp}
            data = build_score_input(df, fund)
            
            sc, label, rs, feature = get_decision_score(data, fund, mode="post", with_reason=True)
            
            if sc >= 45:
                # ⭐ 同步將 WinRate 和 Whale_Net 存入資料庫
                wr, sample_count = calc_winrate(df)
                inst = get_institutional_trading(stock)
                whale_net = sum([int(str(x['單日合計(張)']).replace(',', '')) for x in inst[:3]]) if inst else 0

                return {
                    "代號": stock, "名稱": INDUSTRY_CACHE.get(stock, stock),
                    "Score": sc, "評級": label, "產業": f_data['Industry'], 
                    "收盤價": round(t_close, 2), "WinRate": wr, "Backtest_Samples": sample_count, "Whale_Net": whale_net,
                    "漲跌幅": round((t_close - p_close)/p_close*100, 2),
                    "Feature": feature, "Reasons": rs,
                    "EPS": fund['EPS'], "MoM": fund['MoM'], "YoY": fund['YoY'], "BigPlayer": bp,
                    "Confidence": data.get("Confidence", 100),
                    "Signal_Conflict": data.get("Signal_Conflict", "低"),
                    "Conflict_Score": data.get("Conflict_Score", 0),
                    "Entry_Pattern": data.get("Entry_Pattern", "一般觀察型"),
                    "Est_Vol_Ratio": data.get("Est_Vol_Ratio", 0),
                    "BIAS": data.get("BIAS", 0),
                    "RSI": data.get("RSI", 50),
                    "20MA": data.get("20MA", 0),
                    "MACD柱": data.get("MACD柱", 0),
                    "前日MACD柱": data.get("前日MACD柱", 0),
                    "Box_Breakout": data.get("Box_Breakout", False),
                    "Box_Range_Pct": data.get("Box_Range_Pct", 0),
                    "Tomorrow_Plan": data.get("Tomorrow_Plan", {})
                }
        return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        for res in executor.map(process_stock, pool):
            if res: scan_results.append(res)
            
    scan_results = sorted(scan_results, key=lambda x: (x['Score'], x['漲跌幅']), reverse=True)
            
    if db is None:
        logging.error("Firestore 尚未初始化，掃描結果未寫入雲端。")
        return scan_results

    if len(scan_results) < MIN_SCAN_RESULTS_TO_OVERWRITE:
        logging.error(
            "掃描結果僅 %s 檔，低於安全覆蓋門檻 %s 檔，本次不覆蓋 Firebase daily_scan。",
            len(scan_results),
            MIN_SCAN_RESULTS_TO_OVERWRITE,
        )
        try:
            old_doc = db.collection("market_data").document("daily_scan").get()
            if old_doc.exists:
                old_data = old_doc.to_dict().get("data", [])
                if isinstance(old_data, list) and len(old_data) >= len(scan_results):
                    logging.info("保留既有雲端名單 %s 檔。", len(old_data))
                    return old_data
        except Exception as e:
            logging.warning("讀取既有雲端名單失敗: %s", e)
        return scan_results

    db.collection("market_data").document("daily_scan").set({"data": scan_results, "update_time": firestore.SERVER_TIMESTAMP})
    logging.info(f"✅ 掃描完成！共篩選出 {len(scan_results)} 檔標的。")
    return scan_results

if __name__ == "__main__":
    run_daily_scan()
