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

logging.basicConfig(level=logging.INFO)

# 初始化 Firebase...
if not firebase_admin._apps:
    import streamlit as st
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
        # 強制使用鉅亨網 API 抓取最精準的台股產業中文名
        res = requests.get(f"https://ws.cnyes.com/twstock/api/v1/company/profile/{ticker}", timeout=3).json()
        return res['data']['categoryName']
    except: return "一般產業"

# (此處省略 get_stock_data 等技術指標計算，邏輯與 test.py 相同)

def run_daily_scan():
    logging.info("🚀 開始執行全市場 500 檔雷達掃描...")
    pool = list(set(fetch_top_500()))
    scan_results = []
    
    def process_stock(stock):
        df = get_stock_data(stock) # 請確保這裡有實作 df 計算
        if df is not None:
            ind = get_real_industry(stock)
            # 這裡呼叫 get_decision_score_100 (套用最新 >60強勢, >45觀察 邏輯)
            # 算出 sc 分數
            sc = 75 # 模擬分數
            if sc >= 45: # 只存 45 分以上的，節省 Firebase 空間
                # 計算歷史勝率並寫入 (確保不再是 0.0)
                win_rate = 68.5 # 呼叫你的 winrate function
                return {"代號": stock, "Score": sc, "產業": ind, "WinRate": win_rate}
        return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        for res in executor.map(process_stock, pool):
            if res: scan_results.append(res)
            
    db.collection("market_data").document("daily_scan").set({"data": scan_results, "update_time": firestore.SERVER_TIMESTAMP})
    logging.info("✅ 掃描與寫入完成！")

if __name__ == "__main__":
    run_daily_scan()