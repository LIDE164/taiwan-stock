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
from analysis_core import BACKTEST_LOOKBACK_DAYS, ENG_TO_TW_INDUSTRY, apply_technical_indicators, build_score_input, calculate_historical_winrate
from scoring import get_decision_score

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def get_secret(name, default=""):
    try:
        if st.secrets and name in st.secrets:
            return st.secrets[name]
    except Exception:
        pass
    try:
        import tomllib
        secrets_path = os.path.join(".streamlit", "secrets.toml")
        if os.path.exists(secrets_path):
            with open(secrets_path, "rb") as f:
                secrets = tomllib.load(f)
                if name in secrets:
                    return secrets[name]
    except Exception:
        pass
    return os.getenv(name, default)


def init_firestore():
    try:
        firebase_admin.get_app()
        return firestore.client()
    except ValueError:
        try:
            firebase_admin.initialize_app(credentials.Certificate(dict(st.secrets["firebase"])))
            return firestore.client()
        except Exception as e:
            logging.error("Firebase 初始化失敗: %s", e)
            return None
    except Exception as e:
        logging.error("Firebase 初始化失敗: %s", e)
        return None


db = init_firestore()
FINMIND_TOKEN = get_secret("FINMIND_TOKEN")

# ENG_TO_TW_INDUSTRY 已移至 analysis_core.py 統一管理，此處直接 import

INDUSTRY_CACHE = {}
def build_industry_cache():
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    global INDUSTRY_CACHE
    logging.info("📦 正在建立全市場產業快取字典...")
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", timeout=10, verify=False)
        if res.status_code == 200:
            for item in res.json(): INDUSTRY_CACHE[item['Code']] = item.get('Name', '')
    except: pass
    try:
        res2 = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes", timeout=10, verify=False)
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
        df = yf.Ticker(f"{ticker_number}.TW").history(period="2y").dropna(subset=['Close'])
        if df.empty: df = yf.Ticker(f"{ticker_number}.TWO").history(period="2y").dropna(subset=['Close'])
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
    win_rate, closed_signals, _, _ = calculate_historical_winrate(df_slice, 1.5, 1.0, lookback_days=BACKTEST_LOOKBACK_DAYS)
    return win_rate, closed_signals

def should_run_postclose_scan(now_tpe=None):
    now_tpe = now_tpe or datetime.now(timezone(timedelta(hours=8)))
    if os.getenv("FORCE_SCAN") == "1":
        return True
    if now_tpe.weekday() >= 5:
        return False
    postclose_time = now_tpe.replace(hour=14, minute=30, second=0, microsecond=0)
    return now_tpe >= postclose_time

def update_top10_tracker(top10_results):
    if db is None: return
    try:
        date_str = datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d')
        tracker_ref = db.collection("market_data").document("top10_tracker")
        doc = tracker_ref.get()
        positions = []
        if doc.exists:
            data_field = doc.to_dict().get("data", {})
            # Fallback for old structure if necessary
            positions = data_field.get("positions", doc.to_dict().get("positions", []))
        
        open_positions = [p for p in positions if p.get("status") == "OPEN"]
        closed_positions = [p for p in positions if p.get("status") != "OPEN"]
        top10_tickers = {str(x["代號"]): x for x in top10_results}
        
        for p in open_positions:
            ticker = str(p["ticker"])
            cp = p.get("current_price")
            if ticker in top10_tickers:
                cp = top10_tickers[ticker]["收盤價"]
            else:
                df = get_stock_data(ticker)
                if df is not None and not df.empty:
                    cp = float(df.iloc[-1]['Close'])
            
            p["current_price"] = cp
            if cp > p.get("highest_price", p["entry_price"]): p["highest_price"] = cp
            if cp < p.get("lowest_price", p["entry_price"]): p["lowest_price"] = cp
            
            pnl_pct = (cp - p["entry_price"]) / p["entry_price"] * 100
            p["pnl_pct"] = round(pnl_pct, 2)
            
            if pnl_pct >= 15.0:
                p["status"] = "CLOSED_TP"
                p["close_date"] = date_str
                p["close_price"] = cp
            elif pnl_pct <= -10.0:
                p["status"] = "CLOSED_SL"
                p["close_date"] = date_str
                p["close_price"] = cp
                
        open_tickers = {str(p["ticker"]) for p in open_positions if p.get("status") == "OPEN"}
        for x in top10_results:
            ticker = str(x["代號"])
            if ticker not in open_tickers:
                new_pos = {
                    "ticker": ticker,
                    "name": x["名稱"],
                    "entry_date": date_str,
                    "entry_price": x["收盤價"],
                    "status": "OPEN",
                    "close_date": None,
                    "close_price": None,
                    "highest_price": x["收盤價"],
                    "lowest_price": x["收盤價"],
                    "current_price": x["收盤價"],
                    "pnl_pct": 0.0
                }
                open_positions.append(new_pos)
                open_tickers.add(ticker)
                
        all_positions = closed_positions + open_positions
        tracker_ref.set({"data": {"positions": all_positions}, "update_time": firestore.SERVER_TIMESTAMP})
        logging.info("自動追蹤紀錄已更新，目前未平倉檔數: %d", len([p for p in all_positions if p.get("status")=="OPEN"]))
    except Exception as e:
        logging.error("更新 top10_tracker 失敗: %s", e)

def run_daily_scan():
    if not should_run_postclose_scan():
        logging.info("尚未到台北時間 14:30 盤後掃描時間，本次略過。")
        return []
    logging.info("🚀 開始執行全市場 500 檔雷達掃描...")
    build_industry_cache()
    
    twii_close, twii_ma20, twii_ma60 = 0.0, 0.0, 0.0
    try:
        twii_df = yf.Ticker("^TWII").history(period="4mo")
        if not twii_df.empty and len(twii_df) >= 60:
            twii_df['MA20'] = twii_df['Close'].rolling(20).mean()
            twii_df['MA60'] = twii_df['Close'].rolling(60).mean()
            twii_close = float(twii_df['Close'].iloc[-1])
            twii_ma20 = float(twii_df['MA20'].iloc[-1])
            twii_ma60 = float(twii_df['MA60'].iloc[-1])
    except Exception as e:
        logging.error("雷達獲取大盤加權指數失敗: %s", e)
    
    pool = list(set(fetch_top_500() + ["2330", "2317", "2454"]))
    scan_results = []
    
    previous_streaks = {}
    previous_ranks = {}
    if db is not None:
        try:
            prev_doc = db.collection("market_data").document("daily_scan").get()
            if prev_doc.exists:
                prev_data = prev_doc.to_dict().get("data", [])
                previous_streaks = {str(x.get("代號")): int(x.get("Streak", 0)) for x in prev_data}
                previous_ranks = {str(x.get("代號")): idx + 1 for idx, x in enumerate(prev_data)}
        except Exception as e:
            logging.error("讀取歷史掃描名單失敗: %s", e)
    
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
            fund = {"EPS": f_data.get('EPS', '0'), "MoM": mom, "YoY": yoy, "BigPlayer": bp, "TWII_Close": twii_close, "TWII_MA20": twii_ma20, "TWII_MA60": twii_ma60}
            data = build_score_input(df, fund)
            
            sc, label, rs, feature = get_decision_score(data, fund, mode="post", with_reason=True)
            
            has_adv_pattern = bool(data.get("Advanced_Pattern"))
            if sc >= 45 or has_adv_pattern:
                # ⭐ 同步將 WinRate 和 Whale_Net 存入資料庫
                wr, samples = calc_winrate(df)
                inst = get_institutional_trading(stock)
                whale_net = sum([int(str(x['單日合計(張)']).replace(',', '')) for x in inst[:3]]) if inst else 0

                return {
                    "代號": stock, "名稱": INDUSTRY_CACHE.get(stock, stock),
                    "Score": sc, "評級": label, "產業": f_data['Industry'], 
                    "收盤價": round(t_close, 2), "WinRate": wr, "Whale_Net": whale_net,
                    "漲跌幅": round((t_close - p_close)/p_close*100, 2),
                    "Feature": feature, "Reasons": rs, "Backtest_Samples": samples,
                    "EPS": fund['EPS'], "MoM": fund['MoM'], "YoY": fund['YoY'], "BigPlayer": bp,
                    "Advanced_Pattern": data.get("Advanced_Pattern", ""),
                    "Streak": previous_streaks.get(stock, 0) + 1,
                    "Prev_Rank": previous_ranks.get(stock, 999)
                }
        return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        for res in executor.map(process_stock, pool):
            if res: scan_results.append(res)
            
    scan_results = sorted(scan_results, key=lambda x: (x['Score'], x['漲跌幅']), reverse=True)
    
    for idx, res in enumerate(scan_results):
        curr_rank = idx + 1
        res["Rank"] = curr_rank
        res["Rank_Diff"] = res["Prev_Rank"] - curr_rank if res["Prev_Rank"] != 999 else "NEW"
            
    if db is None:
        logging.error("Firestore 尚未初始化，掃描結果未寫入雲端。")
        return scan_results

    db.collection("market_data").document("daily_scan").set({"data": scan_results, "update_time": firestore.SERVER_TIMESTAMP})
    
    try:
        top10 = scan_results[:10]
        date_str = datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d')
        history_data = [{"代號": x["代號"], "名稱": x["名稱"], "收盤價": x["收盤價"], "Score": x["Score"]} for x in top10]
        db.collection("top10_history").document(date_str).set({"data": history_data, "update_time": firestore.SERVER_TIMESTAMP})
        logging.info("已記錄 %s 前十名歷史價格", date_str)
        update_top10_tracker(top10)
    except Exception as e:
        logging.error("記錄歷史前十名失敗: %s", e)

    logging.info(f"✅ 掃描完成！共篩選出 {len(scan_results)} 檔標的。")
    return scan_results

if __name__ == "__main__":
    run_daily_scan()
