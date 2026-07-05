import streamlit as st
import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

def test_connection():
    logging.info("啟動雲端自動掃描機器人...")
    
    try:
        # 測試讀取 GitHub 注入的金鑰
        fm_token = st.secrets["FINMIND_TOKEN"]
        logging.info("✅ 成功讀取 API 金鑰！")
        
        # 測試 Firebase 連線
        if not firebase_admin._apps:
            cert_dict = dict(st.secrets["firebase"])
            cred = credentials.Certificate(cert_dict)
            firebase_admin.initialize_app(cred)
        
        db = firestore.client()
        logging.info("✅ 成功連線至 Firebase 資料庫！")
        
        # 寫入一筆測試資料
        db.collection("system_logs").document("last_scan").set({
            "status": "success",
            "time": firestore.SERVER_TIMESTAMP
        })
        logging.info("✅ 測試資料寫入完成！任務結束。")
        
    except Exception as e:
        logging.error(f"❌ 發生錯誤: {e}")

if __name__ == "__main__":
    test_connection()