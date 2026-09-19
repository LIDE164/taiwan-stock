# 最後修改時間: 2026-07-07 (修復群組顯示、分數同步、雷達清單與營收籌碼回補防呆版)
import firebase_admin
from firebase_admin import credentials, firestore
import yfinance as yf
import streamlit as st
import pandas as pd
import time
import os
import uuid
from datetime import datetime, timezone, timedelta
import plotly.graph_objects as go
import concurrent.futures
import logging
import math
import json
from streamlit_autorefresh import st_autorefresh

# 引入自訂繪圖函式與共用大腦核心演算法
from analysis_core import BACKTEST_LOOKBACK_DAYS, BACKTEST_SCOPE, ENG_TO_TW_INDUSTRY, apply_technical_indicators, calculate_historical_performance
from analysis_live import fetch_analysis_live_quote
from app_security import (
    build_stock_url,
    escape_html,
    normalize_ticker,
    resolve_stock_identifier,
    safe_iso_date,
    safe_mode,
    scoped_document_name,
)
from charts import draw_professional_chart
from chunked_firestore import load_chunked_items
from data_providers import (
    clear_provider_cache,
    fetch_financial_quality,
    fetch_institutional_rows,
    fetch_revenue_growth,
)
from entry_readiness import build_entry_readiness, ensure_entry_readiness
from market_http import call_with_backoff, http_get
from intraday_ranking import (
    annotate_intraday_score,
    institutional_aggregate_from_record,
    institutional_rows_from_record,
    original_ranking_targets,
    support_data_from_postclose_record,
)
from intraday_quotes import fetch_yahoo_live_history_bundle, merge_intraday_quote_into_history
from scan_state import (
    build_daily_scan_status,
    build_model_confidence,
    build_scan_quality,
    latest_trading_date,
)
from scoring import decision_label, get_decision_score
from strategy_advice import build_strategy_text
from trade_journal import analyze_journal, normalize_record
try:
    from ui_components import (
        credibility_label,
        generate_cards_html as build_cards_html,
        render_app_style,
        render_daily_scan_status_card,
        render_home_side_panel,
        render_market_status_cards,
        render_metric_grid,
        render_stock_hero,
    )
except Exception as ui_import_error:
    ui_import_error_text = str(ui_import_error)
    def credibility_label(sample_count):
        try:
            n = int(sample_count)
        except (TypeError, ValueError):
            return "--", "#94A3B8"
        if n < 10:
            return "樣本嚴重不足", "#EF4444"
        if n < 30:
            return "僅供參考", "#FACC15"
        if n < 50:
            return "中等可信", "#60A5FA"
        return "統計較穩定", "#22C55E"

    def render_app_style(is_light_mode=False):
        app_bg = "#f4f6f9" if is_light_mode else "#0b1120"
        st.markdown(f"<style>.stApp {{ background-color:{app_bg}; }} a.stock-card-link {{ text-decoration:none; color:inherit; display:block; }}</style>", unsafe_allow_html=True)
        st.caption(f"UI 模組載入失敗，已使用內建備援版：{ui_import_error_text}")

    def render_market_status_cards(items):
        cols = st.columns(len(items))
        for col, item in zip(cols, items):
            with col:
                st.metric(item.get("label", ""), item.get("value", "--"), item.get("sub", ""))

    def render_home_side_panel(title, rows, empty_text="暫無資料"):
        st.markdown(f"**{title}**")
        if not rows:
            st.caption(empty_text)
        for row in rows[:6]:
            st.write(f"{row.get('title', '')}  {row.get('value', '')}")
            st.caption(row.get("sub", ""))

    def render_daily_scan_status_card(status):
        st.markdown("**每日掃描狀態**")
        cols = st.columns(4)
        values = (
            ("交易日", status.get("trading_date", "--")),
            ("結果數量", status.get("result_count_text", "--")),
            ("開始時間", status.get("started_at", "--")),
            ("完成時間", status.get("finished_at", "--")),
        )
        for col, (label, value) in zip(cols, values):
            with col:
                st.metric(label, value)
        st.caption(f"狀態：{status.get('status_label', '狀態不明')}")
        if status.get("error_summary"):
            st.error(f"安全錯誤摘要：{status['error_summary']}")

    def render_stock_hero(data, target, name, strategy_text):
        st.markdown(f"## {target} {name}")
        st.caption(f"{data.get('產業', '一般產業')}｜{data.get('Score_Mode', '盤後正式分數')}｜資料完整度 {data.get('Data_Completeness', data.get('Confidence', 0))}%")
        st.metric("現價", data.get("收盤價", "--"), f"{data.get('漲跌幅', 0):+.2f}%")
        st.info(f"建議策略：{strategy_text}")

    def render_metric_grid(metrics):
        cols = st.columns(len(metrics))
        for col, metric in zip(cols, metrics):
            with col:
                st.metric(metric.get("label", ""), metric.get("value", "--"), metric.get("sub", ""))

    def build_cards_html(df_disp, **kwargs):
        card_html = ""
        no_score = kwargs.get("no_score", False)
        for _, row in df_disp.iterrows():
            code = normalize_ticker(row.get("代號", ""))
            name = escape_html(row.get("名稱", ""))
            score = row.get("Score", 0)
            score_text = "形態觀察" if no_score else f"{score}分"
            card_html += (
                f"<a href='{build_stock_url(code)}' class='stock-card-link'>"
                "<div style='background:#0F172A; border:1px solid #1E293B; border-radius:10px; padding:14px; margin-bottom:10px; color:#E2E8F0;'>"
                f"<b>{escape_html(code)} {name}</b><span style='float:right; color:#60A5FA; font-weight:900;'>{escape_html(score_text)}</span><br>"
                f"<span style='color:#94A3B8;'>技術面勝率 {escape_html(row.get('WinRate', '--'))}%｜樣本 {escape_html(row.get('Backtest_Samples', '--'))}</span></div></a>"
            )
        return card_html

logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(levelname)s - %(message)s')

def get_secret(name, default=""):
    try:
        value = st.secrets.get(name, "")
    except Exception:
        value = ""
    return value or os.getenv(name, default)


FINMIND_TOKEN = get_secret("FINMIND_TOKEN")
FUGLE_API_KEY = get_secret("FUGLE_API_KEY")
LIVE_SCORE_CACHE_SECONDS = 30
POST_ANALYSIS_CACHE_SECONDS = 21600
ANALYSIS_CACHE_SCHEMA_VERSION = 4
DEFAULT_RADAR_TICKERS = ["2330", "2317", "2454", "2308", "2382", "3231", "6176", "3094"]
LOW_FIREBASE_READ_MODE = True
CLOUD_READ_TTL_SECONDS = {
    "market_data/daily_scan": 120,
    "system_locks/daily_scan": 60,
}

st.set_page_config(page_title="專業交易雷達", layout="wide", initial_sidebar_state="collapsed")

st.markdown('''
<head>
    <link rel="manifest" href="/manifest.json">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
</head>
''', unsafe_allow_html=True)

st.sidebar.title("⚙️ 介面設定")
is_light_mode = st.sidebar.toggle("黑白底色切換", False, key="toggle_theme_mode")
if LOW_FIREBASE_READ_MODE:
    st.sidebar.caption("Firebase 低讀取模式：開啟")

if st.session_state.pop("_cache_clear_notice", False):
    st.sidebar.success("已清除資料快取。")

if st.sidebar.button("強制清除快取資料", width="stretch"):
    st.session_state["_clear_data_caches_requested"] = True
    if "scan_results" in st.session_state: del st.session_state["scan_results"]
    if "scan_results_is_local" in st.session_state: del st.session_state["scan_results_is_local"]
    if "_cloud_doc_cache" in st.session_state: del st.session_state["_cloud_doc_cache"]
    if "_analysis_session_cache" in st.session_state: del st.session_state["_analysis_session_cache"]
    # A journal write must be tied to the revision loaded from Firestore.
    # Invalidating the document cache also invalidates its in-session scope.
    st.session_state.pop("_trade_journal_scope", None)

render_app_style(is_light_mode)

STOCK_NAMES = { "2330": "台積電", "2317": "鴻海", "2454": "聯發科", "2308": "台達電", "2382": "廣達", "3231": "緯創", "2891": "中信金"}

@st.cache_data(ttl=86400)
def get_all_tw_stock_names_v3():
    names = STOCK_NAMES.copy()
    try:
        res = http_get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", timeout=10)
        res.raise_for_status()
        if res.status_code == 200:
            for i in res.json(): names[i['Code']] = i['Name']
    except Exception as e:
        logging.warning("上市股票名稱取得失敗: %s", e)
    try:
        res2 = http_get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes", timeout=10)
        res2.raise_for_status()
        if res2.status_code == 200:
            for i in res2.json(): names[i['SecuritiesCompanyCode']] = i['CompanyName']
    except Exception as e:
        logging.warning("上櫃股票名稱取得失敗: %s", e)
    return names

CURRENT_STOCK_NAMES = get_all_tw_stock_names_v3()

def get_stock_name(ticker):
    ticker_str = normalize_ticker(ticker)
    return CURRENT_STOCK_NAMES.get(ticker_str, ticker_str)

def is_financial_stock(ticker, industry=""):
    s = normalize_ticker(ticker)
    ind = str(industry).strip()
    if s.startswith("28"):
        return True
    financial_keywords = ["金融", "銀行", "保險", "金控", "證券", "期貨", "Financial"]
    return any(k in ind for k in financial_keywords)

def is_realtime_score_record(record):
    if not isinstance(record, dict):
        return False
    text = " ".join(str(record.get(k, "")) for k in ["Score_Mode_Raw", "Score_Mode", "Score_Source"])
    return "realtime" in text.lower() or "盤中" in text

def safe_num(value, default=0.0):
    try:
        if value is None or pd.isna(value):
            return default
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return default


def optional_num(value):
    """Return a finite numeric value or None; never turn missing data into zero."""
    try:
        if value is None or pd.isna(value):
            return None
        parsed = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


@st.fragment
def render_analysis_k_chart(
    df_slice,
    latest_price,
    buy_dates,
    light_mode,
    ticker,
    initial_days=30,
    initial_show_buy=True,
    initial_show_sup=True,
    initial_show_signals=True,
):
    """Render chart controls in isolation so period changes do not rerun analysis."""
    ticker_key = normalize_ticker(ticker) or "stock"
    day_key = f"k_chart_days_{ticker_key}"
    flag_defaults = {
        f"k_chart_buy_{ticker_key}": bool(initial_show_buy),
        f"k_chart_sup_{ticker_key}": bool(initial_show_sup),
        f"k_chart_signals_{ticker_key}": bool(initial_show_signals),
    }
    if day_key not in st.session_state:
        st.session_state[day_key] = int(initial_days) if int(initial_days) in (30, 60, 90) else 30
    for key, default in flag_defaults.items():
        if key not in st.session_state:
            st.session_state[key] = default

    selected_days = st.segmented_control(
        "K 線期間",
        options=[30, 60, 90],
        format_func=lambda days: f"{days}日",
        key=day_key,
        required=True,
        width="stretch",
    )
    st.session_state.view_days = int(selected_days)

    control_columns = st.columns(3)
    with control_columns[0]:
        show_buy = st.toggle("買進訊號", key=f"k_chart_buy_{ticker_key}", width="stretch")
    with control_columns[1]:
        show_sup = st.toggle("高低點", key=f"k_chart_sup_{ticker_key}", width="stretch")
    with control_columns[2]:
        show_signals = st.toggle("圖表符號", key=f"k_chart_signals_{ticker_key}", width="stretch")

    fig = draw_professional_chart(
        df_slice,
        latest_price,
        int(selected_days),
        light_mode,
        show_buy,
        show_sup,
        show_signals,
        buy_dates=buy_dates,
    )
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False, "scrollZoom": True})

def get_favorite_stock_set():
    favs = set()
    for stocks in st.session_state.get('fav_groups', {}).values():
        favs.update(normalize_ticker(s) for s in stocks)
    return favs

def get_simulated_order_stock_set():
    return {normalize_ticker(o.get("ticker", "")) for o in st.session_state.get("simulated_orders", []) if o.get("ticker")}

def get_market_progress(now_tpe=None):
    now_tpe = now_tpe or datetime.now(timezone(timedelta(hours=8)))
    start = now_tpe.replace(hour=9, minute=0, second=0, microsecond=0)
    end = now_tpe.replace(hour=13, minute=30, second=0, microsecond=0)
    if now_tpe <= start:
        return 0.0
    if now_tpe >= end:
        return 1.0
    return max(0.0, min(1.0, (now_tpe - start).total_seconds() / (end - start).total_seconds()))

def get_market_state(now_tpe=None):
    now_tpe = now_tpe or datetime.now(timezone(timedelta(hours=8)))
    if now_tpe.weekday() >= 5:
        return "holiday"
    preopen = now_tpe.replace(hour=8, minute=30, second=0, microsecond=0)
    start = now_tpe.replace(hour=9, minute=0, second=0, microsecond=0)
    end = now_tpe.replace(hour=13, minute=30, second=0, microsecond=0)
    if now_tpe < preopen:
        return "closed"
    if now_tpe < start:
        return "preopen"
    if now_tpe <= end:
        return "open"
    return "closed"

def is_regular_market_open(now_tpe=None):
    return get_market_state(now_tpe) == "open"

def resolve_score_mode(request_intraday=False):
    market_state = get_market_state()
    if request_intraday and market_state == "open":
        return "realtime", "盤中參考分數", True
    return "post", "盤後正式分數", False

def build_data_quality(
    price_status="ok",
    volume_status="ok",
    institutional_days=0,
    fundamental_status="unknown",
    revenue_status="unknown",
    financial_status="unknown",
    macro_status=None,
    txf_status="ok",
):
    macro_status = macro_status or {}
    return build_scan_quality({
        "price": price_status,
        "volume": volume_status,
        "fundamental": fundamental_status,
        "institutional": "ok" if institutional_days else "missing",
        "revenue": revenue_status,
        "financial": financial_status,
        "macro": "ok" if macro_status and all(v == "ok" for v in macro_status.values()) else "partial",
        "txf": txf_status,
    }, institutional_days=institutional_days)

def adjust_intraday_volume(volume, avg_volume_5d, is_intraday=False):
    volume = safe_num(volume)
    avg_volume_5d = safe_num(avg_volume_5d)
    progress = get_market_progress()
    if not is_intraday or avg_volume_5d <= 0:
        return volume, volume / avg_volume_5d if avg_volume_5d > 0 else 1.0, True
    projected = volume / max(progress, 0.18)
    confirmed = progress >= 0.82 or volume >= avg_volume_5d * 1.1
    effective_volume = volume if confirmed else min(projected, avg_volume_5d * 1.05)
    return effective_volume, effective_volume / avg_volume_5d, confirmed

def render_sidebar_favorites(container):
    link_color = "#333" if is_light_mode else "#e2e8f0"
    with container.container():
        st.title("⭐ 我的自選群組")
        fav_groups = st.session_state.get('fav_groups', {})
        if fav_groups:
            for g_name, g_stocks in fav_groups.items():
                stocks = [normalize_ticker(s) for s in g_stocks]
                with st.expander(f"📁 {g_name} ({len(stocks)} 檔)"):
                    for s in stocks:
                        s_name = get_stock_name(s)
                        st.markdown(f"- <a href='{build_stock_url(s)}' target='_self' style='text-decoration:none; color:{link_color}; font-weight:bold;'>{escape_html(s)} {escape_html(s_name)}</a>", unsafe_allow_html=True)
        else:
            st.info("尚未加入任何標的")

st.sidebar.title("🔍 快速搜尋")
with st.sidebar.form(key="search_form"):
    search_input = st.text_input("隱藏", placeholder="輸入股票代號或中文名稱...", label_visibility="collapsed")
    submit_search = st.form_submit_button("送出搜尋", width="stretch")
    
if submit_search and search_input:
    s_val = search_input.strip().replace(" ", "")
    target_ticker = normalize_ticker(s_val)
    if not target_ticker:
        target_ticker = None
    if target_ticker is None:
        for code, name in CURRENT_STOCK_NAMES.items():
            if s_val in name:
                target_ticker = normalize_ticker(code)
                break
    if target_ticker:
        st.session_state.current_stock = target_ticker
        st.session_state.page = "analysis"
        st.session_state.date_offset = 0
        st.rerun() 

st.sidebar.divider()
st.sidebar.title("⏱️ 盤中即時跳動")
_market_open_now = is_regular_market_open()
if _market_open_now:
    auto_refresh = st.sidebar.toggle("開啟自動更新 (每30秒)", False, key="auto_refresh_toggle")
    if auto_refresh: st_autorefresh(interval=30000, limit=None)
else:
    st.sidebar.caption("🔴 非交易時段，無需自動刷新")
    auto_refresh = False

st.sidebar.divider()
st.sidebar.title("🛒 模擬交易中心")
if st.sidebar.button("經理人績效儀表板", width="stretch"):
    st.session_state.page = "simulated_orders"; st.rerun()
if st.sidebar.button("🏆 Top 10 自動追蹤績效", width="stretch"):
    st.session_state.page = "top10_tracking"; st.rerun()
if st.sidebar.button("📝 交易日誌分析師", width="stretch"):
    st.session_state.page = "trade_journal"; st.rerun()

st.sidebar.divider()
fav_sidebar_slot = st.sidebar.empty()

try:
    firebase_admin.get_app()
except ValueError:
    try:
        cert_dict = dict(st.secrets["firebase"])
        cred = credentials.Certificate(cert_dict)
        firebase_admin.initialize_app(cred)
    except Exception as e:
        st.session_state.cloud_last_error = f"Firebase 初始化失敗：{e}"
        logging.error(f"Firebase 初始化失敗: {e}")

try:
    db = firestore.client()
except Exception as e:
    db = None
    st.session_state.cloud_last_error = f"Firestore client 建立失敗：{e}"


def resolve_user_document_names():
    identity = {}
    try:
        user = st.user
        if getattr(user, "is_logged_in", False):
            identity = {"sub": user.get("sub", ""), "email": user.get("email", "")}
    except Exception:
        pass
    namespace = get_secret("USER_DATA_NAMESPACE")
    is_ephemeral = not identity and not namespace
    if is_ephemeral:
        if "_anonymous_user_scope" not in st.session_state:
            st.session_state._anonymous_user_scope = uuid.uuid4().hex
        fallback = st.session_state._anonymous_user_scope
    else:
        fallback = namespace
    return (
        scoped_document_name("simulated_orders", identity, fallback),
        scoped_document_name("fav_groups", identity, fallback),
        is_ephemeral,
    )


USER_ORDERS_DOC, USER_FAVORITES_DOC, USER_SCOPE_EPHEMERAL = resolve_user_document_names()
CLOUD_READ_TTL_SECONDS[f"user_data/{USER_ORDERS_DOC}"] = 600
CLOUD_READ_TTL_SECONDS[f"user_settings/{USER_FAVORITES_DOC}"] = 600
if USER_SCOPE_EPHEMERAL:
    st.sidebar.caption("匿名模式：自選與模擬單只綁定本次工作階段；設定登入或 USER_DATA_NAMESPACE 可跨工作階段保存。")

def load_cloud_data(collection_name, document_name, default_data):
    target = f"{collection_name}/{document_name}"
    cache_key = f"{collection_name}:{document_name}"
    now_ts = time.time()
    if "_cloud_doc_cache" not in st.session_state:
        st.session_state._cloud_doc_cache = {}
    ttl = CLOUD_READ_TTL_SECONDS.get(target, 300)
    cached_entry = st.session_state._cloud_doc_cache.get(cache_key)
    if LOW_FIREBASE_READ_MODE and cached_entry and now_ts - cached_entry.get("ts", 0) <= ttl:
        return cached_entry.get("value", default_data)
    if db is None:
        if collection_name == "market_data":
            st.session_state.cloud_last_error = "Firebase 未初始化，無法讀取雲端掃描名單"
        return default_data
    try:
        doc = db.collection(collection_name).document(document_name).get()
        if not doc.exists:
            if collection_name == "market_data":
                st.session_state.cloud_last_error = f"{target} 文件不存在"
            st.session_state._cloud_doc_cache[cache_key] = {"value": default_data, "ts": now_ts, "revision": 0}
            return default_data
        payload = doc.to_dict() or {}
        value = payload.get('data', default_data)
        revision = int(payload.get("revision", 0) or 0)
        st.session_state._cloud_doc_cache[cache_key] = {"value": value, "ts": now_ts, "revision": revision}
        if collection_name == "market_data":
            if isinstance(value, list) and len(value) == 0:
                st.session_state.cloud_last_error = f"{target} 的 data 欄位是空清單"
            else:
                st.session_state.cloud_last_error = ""
        return value
    except Exception as e:
        if collection_name == "market_data":
            st.session_state.cloud_last_error = f"讀取 {target} 失敗：{e}"
        st.session_state._cloud_doc_cache[cache_key] = {"value": default_data, "ts": now_ts}
    return default_data

class CloudDocumentReadError(RuntimeError):
    """A Firestore document could not be read; distinct from a missing document."""


def load_cloud_doc(collection_name, document_name, *, raise_on_error=False):
    target = f"{collection_name}/{document_name}"
    cache_key = f"{target}:doc"
    now_ts = time.time()
    if "_cloud_doc_cache" not in st.session_state:
        st.session_state._cloud_doc_cache = {}
    ttl = CLOUD_READ_TTL_SECONDS.get(target, 300)
    cached_entry = st.session_state._cloud_doc_cache.get(cache_key)
    if LOW_FIREBASE_READ_MODE and cached_entry and now_ts - cached_entry.get("ts", 0) <= ttl:
        if not raise_on_error or "document_exists" in cached_entry:
            return cached_entry.get("value", {})
    if db is None:
        message = f"Firebase 未初始化，無法讀取 {target}"
        if collection_name == "market_data":
            st.session_state.cloud_last_error = message
        if raise_on_error:
            raise CloudDocumentReadError(message)
        return {}
    try:
        doc = db.collection(collection_name).document(document_name).get()
        if not doc.exists:
            st.session_state._cloud_doc_cache[cache_key] = {
                "value": {},
                "ts": now_ts,
                "document_exists": False,
            }
            return {}
        value = doc.to_dict()
        if not isinstance(value, dict) or not value:
            raise CloudDocumentReadError(f"{target} 文件存在但內容為空或格式錯誤")
        st.session_state._cloud_doc_cache[cache_key] = {
            "value": value,
            "ts": now_ts,
            "document_exists": True,
        }
        return value
    except Exception as e:
        message = f"讀取 {target} 失敗：{e}"
        if collection_name == "market_data":
            st.session_state.cloud_last_error = message
        # A transport/authentication failure is not an empty document.  Do not
        # cache a fabricated {}, otherwise the next hydration would erase the
        # last complete scan and its provenance for the entire TTL window.
        if raise_on_error:
            raise CloudDocumentReadError(message) from e
        return {}


def hydrate_manifest_items(manifest, *, collection_name, ids_key, legacy_key):
    """Load a bounded Firestore manifest once per chunk revision in this session."""
    if not isinstance(manifest, dict):
        return []
    try:
        schema = int(manifest.get("storage_schema") or 1)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Firestore 分段版本欄位格式錯誤") from exc
    if schema < 2:
        legacy = manifest.get(legacy_key, [])
        return [dict(row) for row in legacy if isinstance(row, dict)] if isinstance(legacy, list) else []

    raw_chunk_ids = manifest.get(ids_key, [])
    if not isinstance(raw_chunk_ids, list):
        raise RuntimeError(f"Firestore 分段索引 {ids_key} 格式錯誤")
    chunk_ids = tuple(str(item) for item in raw_chunk_ids if str(item))
    revision = str(manifest.get("content_hash") or manifest.get("update_time") or "")
    cache_key = f"{collection_name}:{revision}:{'|'.join(chunk_ids)}"
    if "_chunk_payload_cache" not in st.session_state:
        st.session_state._chunk_payload_cache = {}
    cached = st.session_state._chunk_payload_cache.get(cache_key)
    if isinstance(cached, list):
        return cached
    rows = load_chunked_items(
        db,
        manifest,
        collection_name=collection_name,
        ids_key=ids_key,
        legacy_key=legacy_key,
    )
    # Only a handful of revisions are useful within one browser session.
    if len(st.session_state._chunk_payload_cache) >= 6:
        oldest_key = next(iter(st.session_state._chunk_payload_cache))
        st.session_state._chunk_payload_cache.pop(oldest_key, None)
    st.session_state._chunk_payload_cache[cache_key] = rows
    return rows

def save_cloud_data(collection_name, document_name, data):
    cache_key = f"{collection_name}:{document_name}"
    if "_cloud_doc_cache" not in st.session_state:
        st.session_state._cloud_doc_cache = {}
    if db is None:
        st.session_state.cloud_last_error = f"Firebase 未初始化，{collection_name}/{document_name} 僅保留於目前工作階段"
        return False
    try:
        doc_ref = db.collection(collection_name).document(document_name)
        cached_entry = st.session_state._cloud_doc_cache.get(cache_key)
        expected_revision = cached_entry.get("revision") if isinstance(cached_entry, dict) else None
        transaction = db.transaction()

        @firestore.transactional
        def save_if_current(transaction):
            snapshot = doc_ref.get(transaction=transaction)
            payload = snapshot.to_dict() or {} if snapshot.exists else {}
            server_revision = int(payload.get("revision", 0) or 0)
            if expected_revision is not None and server_revision != expected_revision:
                raise RuntimeError("資料已在其他頁籤更新，請重新整理後再試")
            next_revision = server_revision + 1
            transaction.set(doc_ref, {
                'data': data,
                'revision': next_revision,
                'updated_at': firestore.SERVER_TIMESTAMP,
            })
            return next_revision

        next_revision = save_if_current(transaction)
        st.session_state._cloud_doc_cache[cache_key] = {"value": data, "ts": time.time(), "revision": next_revision}
        st.session_state.cloud_last_error = ""
        return True
    except Exception as e:
        st.session_state.cloud_last_error = f"寫入 {collection_name}/{document_name} 失敗：{e}"
        logging.error("寫入 %s/%s 失敗: %s", collection_name, document_name, e)
        return False

def _analysis_cache_key(ticker, context="latest"):
    safe_context = safe_iso_date(context) or ("realtime" if context == "realtime" else "latest")
    return f"{normalize_ticker(ticker)}__{safe_context}"


def load_analysis_cache(ticker, max_age_seconds=900, context="latest"):
    cache_key = _analysis_cache_key(ticker, context)
    if "_analysis_session_cache" not in st.session_state:
        st.session_state._analysis_session_cache = {}
    local_cached = st.session_state._analysis_session_cache.get(cache_key)
    if isinstance(local_cached, dict):
        try:
            if local_cached.get("schema_version") != ANALYSIS_CACHE_SCHEMA_VERSION:
                raise ValueError("legacy analysis cache")
            saved_at = datetime.fromisoformat(local_cached.get("saved_at", ""))
            if saved_at.tzinfo is None:
                saved_at = saved_at.replace(tzinfo=timezone(timedelta(hours=8)))
            age = (datetime.now(timezone(timedelta(hours=8))) - saved_at).total_seconds()
            if age <= max_age_seconds:
                return local_cached
        except Exception:
            pass
    if LOW_FIREBASE_READ_MODE:
        return None
    cached = load_cloud_data("analysis_cache", cache_key, None)
    if not isinstance(cached, dict):
        return None
    try:
        if cached.get("schema_version") != ANALYSIS_CACHE_SCHEMA_VERSION:
            return None
        saved_at = datetime.fromisoformat(cached.get("saved_at", ""))
        if saved_at.tzinfo is None:
            saved_at = saved_at.replace(tzinfo=timezone(timedelta(hours=8)))
        age = (datetime.now(timezone(timedelta(hours=8))) - saved_at).total_seconds()
        if age <= max_age_seconds:
            return cached
    except Exception:
        return None
    return None

def save_analysis_cache(ticker, payload, context="latest"):
    if not isinstance(payload, dict):
        return
    compact = dict(payload)
    compact["schema_version"] = ANALYSIS_CACHE_SCHEMA_VERSION
    compact["saved_at"] = datetime.now(timezone(timedelta(hours=8))).isoformat()
    if "_analysis_session_cache" not in st.session_state:
        st.session_state._analysis_session_cache = {}
    cache_key = _analysis_cache_key(ticker, context)
    st.session_state._analysis_session_cache[cache_key] = compact
    if LOW_FIREBASE_READ_MODE or db is None:
        return
    save_cloud_data("analysis_cache", cache_key, compact)

def get_latest_expected_scan_date():
    """Use actual TWII bars instead of guessing holidays from weekdays."""
    df = fetch_twse_index_history()
    if df is None or df.empty:
        return ""
    values = list(df.index)
    now_tpe = datetime.now(timezone(timedelta(hours=8)))
    latest = latest_trading_date(values)
    today = now_tpe.strftime('%Y-%m-%d')
    postclose = now_tpe.replace(hour=14, minute=30, second=0, microsecond=0)
    if latest == today and now_tpe < postclose and len(values) >= 2:
        return latest_trading_date(values[:-1])
    return latest

def hydrate_scan_results(force=False):
    now_ts = time.time()
    last_sync = safe_num(st.session_state.get("scan_results_synced_at"), 0)
    should_sync = (
        force
        or "scan_results" not in st.session_state
        or not st.session_state.scan_results
        or now_ts - last_sync >= CLOUD_READ_TTL_SECONDS["market_data/daily_scan"]
    )
    if should_sync:
        try:
            scan_doc = load_cloud_doc("market_data", "daily_scan", raise_on_error=True)
            data = hydrate_manifest_items(
                scan_doc,
                collection_name="daily_scan_chunks",
                ids_key="chunk_ids",
                legacy_key="data",
            )
        except Exception as exc:
            logging.error("讀取分段掃描資料失敗: %s", exc)
            st.session_state.cloud_last_error = f"每日掃描資料不完整，暫時保留上一版：{type(exc).__name__}"
            # Preserve the complete previous snapshot *and its provenance*.
            # Applying a newer manifest date to older rows would mix versions.
            st.session_state.scan_results_synced_at = now_ts
            return st.session_state.get("scan_results", [])
        scan_date = scan_doc.get("scan_date", "")
        expected_date = get_latest_expected_scan_date()
        st.session_state.scan_results_stale = bool(expected_date and (not scan_date or scan_date < expected_date))
        st.session_state.expected_scan_date = expected_date
        st.session_state.scan_date = scan_date
        st.session_state.scan_limit = scan_doc.get("scan_limit")
        st.session_state.universe_size = scan_doc.get("universe_size")
        st.session_state.scan_profile = scan_doc.get("scan_profile", "")
        st.session_state.scan_results = data if isinstance(data, list) else []
        st.session_state.scan_results_synced_at = now_ts
    return st.session_state.get("scan_results", [])

def restore_nav_pool(min_score=60):
    records = hydrate_scan_results()
    if not records:
        return []
    valid_results = [x for x in records if x.get('Score', 0) >= min_score and not is_financial_stock(x.get('代號'), x.get('產業'))]
    if not valid_results:
        valid_results = [x for x in records if not is_financial_stock(x.get('代號'), x.get('產業'))]
    df_nav = pd.DataFrame(valid_results)
    if df_nav.empty or '代號' not in df_nav.columns:
        return []
    sort_cols = [c for c in ['Score', '漲跌幅'] if c in df_nav.columns]
    if sort_cols:
        df_nav = df_nav.sort_values(by=sort_cols, ascending=[False] * len(sort_cols))
    df_nav = df_nav.head(10).copy()
    df_nav['代號'] = df_nav['代號'].astype(str).map(normalize_ticker)
    st.session_state.nav_pool_data = df_nav.to_dict('records')
    st.session_state.nav_pool = df_nav['代號'].tolist()
    return st.session_state.nav_pool_data

def get_radar_targets(records=None, limit=200):
    targets = []
    records = records or []
    for row in records[:limit]:
        code = normalize_ticker(row.get("代號", ""))
        if code and not is_financial_stock(code, row.get("產業", "")):
            targets.append(code)
    targets.extend([t for t in st.session_state.get("custom_pool", []) if not is_financial_stock(t)])
    targets.extend([t for t in get_favorite_stock_set() if not is_financial_stock(t)])
    targets.extend([t for t in DEFAULT_RADAR_TICKERS if not is_financial_stock(t)])
    seen, unique = set(), []
    for ticker in targets:
        code = normalize_ticker(ticker)
        if code and code not in seen:
            seen.add(code)
            unique.append(code)
    return unique

if 'page' not in st.session_state: st.session_state.page = "home"
if 'current_stock' not in st.session_state: st.session_state.current_stock = "2330"
if 'view_days' not in st.session_state: st.session_state.view_days = 30
if 'date_offset' not in st.session_state: st.session_state.date_offset = 0
if 'custom_pool' not in st.session_state: st.session_state.custom_pool = ["2330", "2317", "2454", "2382", "3231", "2891"]

if 'simulated_orders' not in st.session_state or not isinstance(st.session_state.simulated_orders, list):
    st.session_state.simulated_orders = load_cloud_data("user_data", USER_ORDERS_DOC, [])
if not isinstance(st.session_state.simulated_orders, list):
    st.session_state.simulated_orders = []

if 'fav_groups' not in st.session_state or not isinstance(st.session_state.fav_groups, dict):
    st.session_state.fav_groups = load_cloud_data("user_settings", USER_FAVORITES_DOC, {"預設群組": []})
if not isinstance(st.session_state.fav_groups, dict):
    st.session_state.fav_groups = {"預設群組": []}

st.session_state.fav_groups = {
    str(name): [normalize_ticker(s) for s in stocks] if isinstance(stocks, list) else []
    for name, stocks in st.session_state.fav_groups.items()
}
render_sidebar_favorites(fav_sidebar_slot)

if 'stock' in st.query_params or 'query' in st.query_params:
    q_stock = normalize_ticker(st.query_params.get('stock', ''))
    q_status = "ok" if q_stock else "empty"
    if not q_stock and 'query' in st.query_params:
        q_stock, q_status = resolve_stock_identifier(
            st.query_params.get('query', ''),
            CURRENT_STOCK_NAMES,
        )
        if q_status == "ambiguous":
            st.warning("股票名稱不夠明確，請改用股票代號開啟解析。")
        elif q_status == "not_found":
            st.warning("找不到這個股票名稱，請確認名稱或改用股票代號。")
    q_mode = safe_mode(st.query_params.get('mode', ''))
    q_target_date = safe_iso_date(st.query_params.get('target_date', ''))
    if q_target_date:
        st.session_state.target_date = q_target_date
    elif 'target_date' in st.session_state:
        del st.session_state['target_date']
    if q_mode in ("intraday", "realtime"):
        _, q_score_mode_label, q_is_intraday = resolve_score_mode(True)
        st.session_state.is_intraday = q_is_intraday
        st.session_state.score_mode_label = q_score_mode_label
    if q_stock:
        if st.session_state.get('last_q_stock') != q_stock:
            st.session_state.date_offset = 0
        st.session_state.current_stock = q_stock
        st.session_state.page = "analysis"
        st.session_state.last_q_stock = q_stock

# ENG_TO_TW_INDUSTRY 已統一定義於 analysis_core.py，此處不再重複定義

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_twse_index_history():
    try:
        df = call_with_backoff(lambda: yf.Ticker("^TWII").history(period="2y"), attempts=3)
        if not df.empty:
            df.index = pd.to_datetime(df.index.strftime('%Y-%m-%d'))
            df = df[~df.index.duplicated(keep='last')]
            return df[['Open', 'High', 'Low', 'Close', 'Volume']]
    except Exception as e:
        logging.warning("加權指數歷史資料取得失敗: %s", e)
        return None


@st.cache_data(ttl=3600, show_spinner=False)
def _get_ohlcv_base(ticker_number):
    """Layer 1: Fetch and cache raw OHLCV from yfinance (slow, cache 1hr)."""
    base_ticker = normalize_ticker(ticker_number)
    if not base_ticker:
        return None
    def fetch_clean(sym):
        try:
            d = call_with_backoff(lambda: yf.Ticker(sym).history(period="2y"), attempts=2).dropna(subset=['Close'])
            if len(d) >= 20:
                d.index = pd.to_datetime(d.index.strftime('%Y-%m-%d'))
                d = d[~d.index.duplicated(keep='last')]
                return d
        except Exception as e:
            logging.debug("歷史行情取得失敗 %s: %s", sym, e)
            return None
    if base_ticker == "^TWII":
        return fetch_twse_index_history()
    df = fetch_clean(f"{base_ticker}.TW")
    if df is None: df = fetch_clean(f"{base_ticker}.TWO")
    return df


@st.cache_data(ttl=60, show_spinner=False)
def get_stock_data(ticker_number, target_date=None, intraday_quote=None):
    """Layer 2: Apply indicators & merge intraday quote (fast, cache 60s)."""
    base_ticker = normalize_ticker(ticker_number)
    if not base_ticker:
        return None
    base_df = _get_ohlcv_base(ticker_number)
    if base_df is None: return None
    df = base_df.copy()  # 必須 copy，避免修改快取的唯讀 DataFrame
    intraday_quote_status = "not_requested"
    intraday_quote_source = ""
    
    if target_date:
        try:
            cutoff = pd.to_datetime(target_date).tz_localize(None)
            df.index = df.index.tz_localize(None)
            df = df[df.index <= cutoff]
        except Exception as e:
            logging.warning("忽略無效歷史日期 %s: %s", target_date, e)
    else:
        try:
            market_state = get_market_state()
            fallback_quote = dict(intraday_quote or {}) if isinstance(intraday_quote, dict) else {}
            quote = fallback_quote
            if not quote and base_ticker != "^TWII" and market_state == "open" and FUGLE_API_KEY:
                try:
                    url = f"https://api.fugle.tw/marketdata/v1.0/stock/intraday/quote/{base_ticker}"
                    res = http_get(url, headers={'X-API-KEY': FUGLE_API_KEY}, timeout=5)
                    res.raise_for_status()
                    if res.status_code == 200:
                        q = res.json()
                        total = q.get('total', {}) or {}
                        live_value = float(total.get('tradeValue', total.get('tradeValueAmount', 0)) or 0)
                        live_volume = total.get('tradeVolume')
                        candidate_quote = {
                            "close": q.get('closePrice', q.get('lastPrice')),
                            "open": q.get('openPrice'),
                            "high": q.get('highPrice'),
                            "low": q.get('lowPrice'),
                            "volume": live_volume,
                            "vwap": live_value / float(live_volume) if live_volume and live_value > 0 else None,
                            "source": "Fugle",
                        }
                        if all(candidate_quote.get(key) is not None for key in ("close", "open", "high", "low", "volume")):
                            quote = candidate_quote
                except Exception as fugle_error:
                    logging.warning("Fugle 即時行情失敗，改用公開行情 %s: %s", base_ticker, fugle_error)
            if base_ticker != "^TWII" and market_state == "open" and quote:
                required_quote = ("close", "open", "high", "low", "volume")
                if any(quote.get(key) is None for key in required_quote):
                    raise ValueError("即時 OHLCV 欄位不完整")
                c_price = float(quote["close"])
                open_price = float(quote["open"])
                high_price = float(quote["high"])
                low_price = float(quote["low"])
                live_volume = float(quote["volume"])
                if (
                    min(c_price, open_price, high_price, low_price, live_volume) <= 0
                    or high_price < max(c_price, open_price)
                    or low_price > min(c_price, open_price)
                ):
                    raise ValueError("即時 OHLCV 數值不合理")
                real_vwap = safe_num(quote.get("vwap"), 0)
                now_tpe = datetime.now(timezone(timedelta(hours=8)))
                dt_live = pd.to_datetime(now_tpe.strftime('%Y-%m-%d')).tz_localize(None)
                df.index = df.index.tz_localize(None)
                if dt_live not in df.index:
                    new_row = pd.DataFrame({'Open': [open_price], 'High': [high_price], 'Low': [low_price], 'Close': [c_price], 'Volume': [live_volume]}, index=[dt_live])
                    if 0 < real_vwap < c_price * 2:
                        new_row['VWAP'] = real_vwap
                    df = pd.concat([df, new_row])
                else:
                    df.loc[dt_live, 'Close'] = c_price
                    df.loc[dt_live, 'High'] = max(float(df.loc[dt_live, 'High']), high_price)
                    df.loc[dt_live, 'Low'] = min(float(df.loc[dt_live, 'Low']), low_price)
                    df.loc[dt_live, 'Volume'] = max(float(df.loc[dt_live, 'Volume']), live_volume)
                    if 0 < real_vwap < c_price * 2:
                        df.loc[dt_live, 'VWAP'] = real_vwap
                intraday_quote_status = "realtime"
                intraday_quote_source = str(quote.get("source") or "即時行情")
            elif base_ticker != "^TWII" and market_state == "open":
                intraday_quote_status = "missing"
        except Exception as e:
            intraday_quote_status = "error"
            logging.warning("盤中即時行情合併失敗 %s: %s", base_ticker, e)

    try:
        result = apply_technical_indicators(df)
        result.attrs["intraday_quote_status"] = intraday_quote_status
        result.attrs["intraday_quote_source"] = intraday_quote_source
        return result
    except Exception as e:
        logging.error("技術指標計算失敗 %s：%s；本次不產生分析", ticker_number, e)
        return None

@st.cache_data(ttl=86400, show_spinner=False)
def _get_company_profile(base_ticker):
    eps_val, ind = "無", "一般產業"
    eps_period = "missing"
    yahoo_ok = False
    cnyes_ok = False
    try:
        info = yf.Ticker(f"{base_ticker}.TW").info
        if not info or 'industry' not in info: info = yf.Ticker(f"{base_ticker}.TWO").info
        yahoo_ok = bool(info)
        raw_sector = info.get("sector", "")
        if raw_sector in ENG_TO_TW_INDUSTRY: ind = ENG_TO_TW_INDUSTRY[raw_sector]
        elif info.get("industry") in ENG_TO_TW_INDUSTRY: ind = ENG_TO_TW_INDUSTRY[info.get("industry")]
        if 'trailingEps' in info and info['trailingEps'] is not None:
            eps_val = str(round(info['trailingEps'], 2))
            eps_period = "ttm"
    except Exception as e:
        logging.debug("Yahoo 基本面資料取得失敗 %s: %s", base_ticker, e)

    # 單次呼叫 CNYES API，同時取得產業名稱和 EPS，避免重複請求
    if ind == "一般產業" or eps_val == "無":
        try:
            response = http_get(
                f"https://ws.cnyes.com/twstock/api/v1/company/profile/{base_ticker}", timeout=3
            )
            response.raise_for_status()
            res_cnyes = response.json()
            cnyes_data = res_cnyes.get('data', {})
            cnyes_ok = bool(cnyes_data)
            if ind == "一般產業" and 'categoryName' in cnyes_data:
                ind = cnyes_data['categoryName']
            if eps_val == "無" and 'eps' in cnyes_data:
                try:
                    eps_val = f"{float(cnyes_data['eps']):.2f}"
                    eps_period = "provider"
                except (TypeError, ValueError):
                    pass
        except Exception as e:
            logging.debug("CNYES 基本面資料取得失敗 %s: %s", base_ticker, e)

    if eps_val != "無" and ind != "一般產業":
        status = "ok"
    elif yahoo_ok or cnyes_ok:
        status = "partial"
    else:
        status = "missing"
    return {"EPS": eps_val, "EPS_Period": eps_period, "Industry": ind, "_status": status}


def get_fundamental_and_industry_data(ticker_number, current_price=0):
    base_ticker = normalize_ticker(ticker_number)
    profile = dict(_get_company_profile(base_ticker))
    eps_val = profile.get("EPS", "無")
    pe_val = "無"

    if eps_val != "無" and current_price > 0:
        try:
            pe_val = str(round(float(current_price) / float(eps_val), 2)) if float(eps_val) > 0 else "虧損"
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    profile["PE"] = pe_val
    return profile


@st.cache_data(ttl=60, show_spinner=False)
def get_public_intraday_bundle(records):
    """Fetch lightweight live/history JSON without blocking on yfinance metadata."""
    now_tpe = datetime.now(timezone(timedelta(hours=8)))
    return fetch_yahoo_live_history_bundle(records, now_tpe=now_tpe)


@st.cache_data(ttl=25, show_spinner=False)
def get_analysis_live_quote(ticker, revenue_source="", institutional_source=""):
    """Refresh one analysis-page quote independently from the full ranking."""
    now_tpe = datetime.now(timezone(timedelta(hours=8)))
    return fetch_analysis_live_quote(
        ticker,
        {
            "代號": ticker,
            "Revenue_Source": revenue_source,
            "Institutional_Source": institutional_source,
        },
        api_key=FUGLE_API_KEY,
        now_tpe=now_tpe,
    )

@st.cache_data(ttl=21600, show_spinner=False)
def get_finmind_chip_and_revenue_payload(ticker):
    payload = fetch_revenue_growth(ticker, FINMIND_TOKEN)
    return {
        "mom": payload["mom"],
        "yoy": payload["yoy"],
        "period": payload.get("period", ""),
        "source": payload.get("source", ""),
        "status": {"revenue": payload["status"]},
    }


@st.cache_data(ttl=21600, show_spinner=False)
def get_financial_quality_payload(ticker):
    return fetch_financial_quality(ticker)


@st.cache_data(ttl=30, show_spinner=False)
def get_twii_quote():
    tz_tpe = timezone(timedelta(hours=8))
    update_time_str = datetime.now(tz_tpe).strftime('%Y/%m/%d %H:%M:%S')
    latest_close, latest_change = None, None
    try:
        df = yf.Ticker("^TWII").history(period="1mo").dropna(subset=['Close'])
        if not df.empty and len(df) >= 2:
            latest_close = float(df['Close'].iloc[-1])
            latest_change = float(df['Close'].iloc[-1] - df['Close'].iloc[-2])
    except Exception as e:
        logging.warning("即時加權指數取得失敗: %s", e)
    return latest_close, latest_change, update_time_str

def is_plausible_txf_price(price, previous=None, reference_index=None):
    price = safe_num(price, None)
    previous = safe_num(previous, None)
    reference_index = safe_num(reference_index, None)
    if price is None or not (10000 < price < 100000):
        return False
    if previous is not None and previous > 0 and abs(price - previous) / previous > 0.08:
        return False
    if reference_index is not None and reference_index > 10000 and abs(price - reference_index) / reference_index > 0.03:
        return False
    return True


def select_finmind_txf_series(rows, *, now_tpe=None):
    """Select two closes from one front-month/session series; never mix contracts."""
    if not isinstance(rows, list) or not rows:
        return None
    frame = pd.DataFrame(rows)
    required = {"date", "contract_date", "trading_session"}
    if not required.issubset(frame.columns):
        return None
    if "futures_id" in frame.columns:
        frame = frame[frame["futures_id"].astype(str).str.upper().eq("TX")]
    if frame.empty:
        return None

    price = pd.Series(float("nan"), index=frame.index, dtype="float64")
    for column in ("close", "settlement_price", "Close"):
        if column in frame.columns:
            price = price.fillna(pd.to_numeric(frame[column], errors="coerce"))
    frame = frame.assign(
        _price=price,
        _date=frame["date"].astype(str).str.slice(0, 10),
        _contract=frame["contract_date"].astype(str).str.strip(),
        _session=frame["trading_session"].astype(str).str.strip(),
    )
    frame = frame[
        frame["_date"].str.fullmatch(r"\d{4}-\d{2}-\d{2}", na=False)
        & frame["_contract"].str.fullmatch(r"\d{6}", na=False)
        & frame["_session"].ne("")
        & frame["_price"].between(10000, 100000, inclusive="neither")
    ]
    if frame.empty:
        return None

    now_tpe = now_tpe or datetime.now(timezone(timedelta(hours=8)))
    prefer_night = now_tpe.hour >= 15 or now_tpe.hour < 5
    candidates = []
    for session, session_frame in frame.groupby("_session", sort=False):
        latest_date = str(session_frame["_date"].max())
        latest_rows = session_frame[session_frame["_date"].eq(latest_date)]
        if latest_rows.empty:
            continue
        reference_month = int(latest_date[:7].replace("-", ""))
        contract_months = sorted({int(value) for value in latest_rows["_contract"]})
        current_or_later = [value for value in contract_months if value >= reference_month]
        front_month = min(current_or_later or contract_months)
        contract = f"{front_month:06d}"
        series = session_frame[session_frame["_contract"].eq(contract)].copy()
        series = series.sort_values("_date").drop_duplicates("_date", keep="last")
        if len(series) < 2:
            continue
        session_key = str(session).lower()
        is_night = any(token in session_key for token in ("after", "night", "夜", "盤後"))
        session_preference = int(is_night == prefer_night)
        candidates.append((latest_date, session_preference, str(session), contract, series))

    if not candidates:
        return None
    _, _, session, contract, series = max(
        candidates,
        key=lambda item: (item[0], item[1], item[3]),
    )
    current_row, previous_row = series.iloc[-1], series.iloc[-2]
    return {
        "current": float(current_row["_price"]),
        "previous": float(previous_row["_price"]),
        "date": str(current_row["_date"]),
        "contract_date": contract,
        "trading_session": session,
    }

@st.cache_resource(show_spinner=False)
def init_shioaji_api(api_key, secret_key, simulation):
    try:
        import shioaji as sj
        api = sj.Shioaji(simulation=simulation)
        api.login(api_key, secret_key)
        return api
    except Exception as e:
        logging.error(f"Shioaji 登入失敗: {e}")
        return None

@st.cache_data(ttl=60, show_spinner=False)
def get_txf_quote(reference_index=None):
    shioaji_key = get_secret("SHIOAJI_API_KEY")
    shioaji_secret = get_secret("SHIOAJI_SECRET_KEY")
    shioaji_sim = get_secret("SHIOAJI_SIMULATION", "false").lower() == "true"
    
    if shioaji_key and shioaji_secret:
        try:
            api = init_shioaji_api(shioaji_key, shioaji_secret, shioaji_sim)
            if api:
                contract = None
                try:
                    contract = api.Contracts.Futures.TXF.TXFR1
                except AttributeError:
                    try:
                        contract = api.Contracts.Futures['TXF']['TXFR1']
                    except Exception:
                        pass
                
                if contract:
                    snapshots = api.snapshots([contract])
                    if snapshots:
                        snap = snapshots[0]
                        curr = getattr(snap, "close", 0.0)
                        change = getattr(snap, "change_price", 0.0)
                        if curr > 0 and change is not None:
                            snap_ts = getattr(snap, "ts", 0)
                            import datetime as dt
                            if snap_ts > 0:
                                try:
                                    ts_len = len(str(snap_ts))
                                    if ts_len >= 16:
                                        snap_time = dt.datetime.fromtimestamp(snap_ts / 1e9, tz=timezone(timedelta(hours=8))).strftime('%Y/%m/%d %H:%M')
                                    else:
                                        snap_time = dt.datetime.fromtimestamp(snap_ts, tz=timezone(timedelta(hours=8))).strftime('%Y/%m/%d %H:%M')
                                except Exception:
                                    snap_time = "時間未提供"
                            else:
                                snap_time = "時間未提供"
                            
                            prev = curr - change if change is not None else curr
                            if is_plausible_txf_price(curr, prev, reference_index):
                                return curr, change, f"Shioaji TX ({contract.code})", snap_time
        except Exception as e:
            logging.error(f"Shioaji 取得期貨報價失敗: {e}")

    if FINMIND_TOKEN:
        try:
            start_date = (datetime.now() - timedelta(days=20)).strftime('%Y-%m-%d')
            response = http_get(
                "https://api.finmindtrade.com/api/v4/data",
                params={
                    "dataset": "TaiwanFuturesDaily",
                    "data_id": "TX",
                    "start_date": start_date,
                    "token": FINMIND_TOKEN,
                },
                timeout=8,
            )
            response.raise_for_status()
            res = response.json()
            rows = res.get("data", [])
            selected = select_finmind_txf_series(rows)
            if selected:
                curr = selected["current"]
                prev = selected["previous"]
                if is_plausible_txf_price(curr, prev, reference_index):
                    source = (
                        f"FinMind TX {selected['contract_date']} "
                        f"({selected['trading_session']})"
                    )
                    return curr, curr - prev, source, selected["date"]
        except Exception as e:
            logging.warning("FinMind 台指期資料取得失敗 (%s)", type(e).__name__)
    return None, None, "資料源受限", "暫無資料"

def get_stock_data_time(df, is_intraday=False):
    """Describe the actual last market bar without inventing a quote timestamp."""
    if df is None or df.empty:
        return "資料時間不明"
    try:
        data_date = pd.Timestamp(df.index[-1]).strftime("%Y/%m/%d")
    except (TypeError, ValueError):
        return "資料時間不明"
    suffix = "盤中合併資料" if is_intraday else "日 K 資料"
    return f"{data_date}（{suffix}）"

def preserve_lot_value(value, default=None):
    number = optional_num(value)
    if number is None:
        return default
    rounded = round(number, 3)
    return int(rounded) if rounded.is_integer() else rounded


def normalize_institutional_rows(rows):
    normalized = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        date_text = str(row.get("date", ""))
        try:
            foreign = preserve_lot_value(row["foreign"])
            trust = preserve_lot_value(row["trust"])
            dealer = preserve_lot_value(row["dealer"])
            if None in (foreign, trust, dealer):
                continue
            total = preserve_lot_value(row.get("total", foreign + trust + dealer))
            if total is None:
                continue
        except (KeyError, TypeError, ValueError):
            continue
        normalized.append({
            "日期": date_text[-5:].replace("-", "/"),
            "外資(張)": foreign,
            "投信(張)": trust,
            "自營商(張)": dealer,
            "單日合計(張)": total,
            "_source": str(row.get("source", "")),
        })
    return normalized


@st.cache_data(ttl=300, show_spinner=False)
def get_institutional_trading(ticker, with_status=False):
    rows, status = fetch_institutional_rows(ticker, FINMIND_TOKEN)
    normalized = normalize_institutional_rows(rows)
    return (normalized, status) if with_status else normalized


def get_analysis_support_data(ticker, current_price, cached_doc=None):
    """Load source-aware fundamentals and chip data without inventing missing values."""
    fund = get_fundamental_and_industry_data(ticker, current_price)
    revenue = get_finmind_chip_and_revenue_payload(ticker)
    fund["MoM"], fund["YoY"] = revenue["mom"], revenue["yoy"]
    fund["Revenue_Period"] = revenue.get("period", "")
    fund["Revenue_Source"] = revenue.get("source", "")
    fund["_data_status"] = revenue["status"]
    financial = get_financial_quality_payload(ticker)
    fund.update({
        "Financial_Period": financial.get("period", ""),
        "Financial_Source": financial.get("source", ""),
        "Financial_Status": financial.get("status", "unknown"),
        "Financial_Revenue": financial.get("revenue"),
        "Financial_Gross_Profit": financial.get("gross_profit"),
        "Financial_Operating_Income": financial.get("operating_income"),
        "Financial_Net_Income": financial.get("net_income"),
        "Financial_EPS": financial.get("eps"),
        "Financial_Gross_Margin": financial.get("gross_margin"),
        "Financial_Operating_Margin": financial.get("operating_margin"),
        "Financial_Net_Margin": financial.get("net_margin"),
        "Financial_Debt_Ratio": financial.get("debt_ratio"),
        "Financial_Current_Ratio": financial.get("current_ratio"),
        "Financial_Risk_Level": financial.get("risk_level", "unknown"),
        "Financial_Risk_Flags": financial.get("risk_flags", []),
    })
    fund["_data_status"]["financial"] = financial.get("status", "unknown")
    inst_data, inst_status = get_institutional_trading(ticker, with_status=True)
    if not inst_data:
        saved_rows = institutional_rows_from_record(cached_doc)
        if saved_rows:
            inst_data = saved_rows
            inst_status = str((cached_doc or {}).get("Institutional_Status") or "partial")
    fund["_institutional_status"] = inst_status
    fund["Institutional_Source"] = inst_data[0].get("_source", "") if inst_data else ""
    return fund, inst_data


def repair_cached_institutional_data(ticker, fund, inst_data, cached_doc=None):
    """Retry an empty analysis cache without retaining a transient provider failure."""
    cached_rows = [row for row in (inst_data or []) if isinstance(row, dict)]
    if cached_rows:
        return dict(fund or {}), cached_rows, False

    saved_rows = institutional_rows_from_record(cached_doc)
    if saved_rows:
        updated_fund = dict(fund or {})
        updated_fund["_institutional_status"] = str((cached_doc or {}).get("Institutional_Status") or "partial")
        updated_fund["Institutional_Source"] = saved_rows[0].get("_source", "")
        return updated_fund, saved_rows, True

    retry_key = f"institutional_retry_{normalize_ticker(ticker)}"
    now = time.time()
    last_retry = safe_num(st.session_state.get(retry_key), 0)
    if now - last_retry < 60:
        return dict(fund or {}), [], False
    st.session_state[retry_key] = now

    rows, status = fetch_institutional_rows(ticker, FINMIND_TOKEN)
    fresh_rows = normalize_institutional_rows(rows)
    updated_fund = dict(fund or {})
    updated_fund["_institutional_status"] = status
    updated_fund["Institutional_Source"] = fresh_rows[0].get("_source", "") if fresh_rows else ""
    return updated_fund, fresh_rows, bool(fresh_rows)

@st.cache_data(ttl=300, show_spinner=False)
def get_global_macro_data():
    data = {"global_time": datetime.now(timezone(timedelta(hours=8))).strftime('%Y/%m/%d %H:%M:%S'), "status": {}}
    for t, url in {"^SOX": "https://finance.yahoo.com/quote/^SOX", "^VIX": "https://finance.yahoo.com/quote/^VIX", "TWD=X": "https://finance.yahoo.com/quote/TWD=X"}.items():
        try:
            df = yf.Ticker(t).history(period="5d").dropna(subset=['Close'])
            if len(df) >= 2:
                c, p = float(df['Close'].iloc[-1]), float(df['Close'].iloc[-2])
                data[t] = {"price": c, "pct": (c-p)/p*100 if p != 0 else 0, "time": df.index[-1].strftime('%Y/%m/%d'), "url": url, "status": "ok"}
                data["status"][t] = "ok"
            else:
                data[t] = {"price": None, "pct": None, "time": "暫無資料", "url": url, "status": "missing"}
                data["status"][t] = "missing"
        except Exception:
            data[t] = {"price": None, "pct": None, "time": "暫無資料", "url": url, "status": "missing"}
            data["status"][t] = "missing"
    try:
        twii_ref, _, _ = get_twii_quote()
        txf_price, txf_change, txf_symbol, txf_time = get_txf_quote(twii_ref)
        if txf_price is not None and txf_change is not None:
            prev = txf_price - txf_change
            data["TX=F"] = {"price": txf_price, "pct": txf_change / prev * 100 if prev else 0, "time": txf_time, "url": txf_symbol, "status": "ok"}
            data["status"]["TX=F"] = "ok"
        else:
            data["TX=F"] = {"price": None, "pct": None, "time": "暫無資料", "url": txf_symbol, "status": "missing"}
            data["status"]["TX=F"] = "missing"
    except Exception:
        data["TX=F"] = {"price": None, "pct": None, "time": "暫無資料", "url": "資料源受限", "status": "missing"}
        data["status"]["TX=F"] = "missing"
    return data

def open_pred_logic(twii_df, twii_close, twii_change, twii_time_str=""):
    macro_data = get_global_macro_data()
    if twii_df is None or len(twii_df) < 2:
        return "資料不足", "無法分析", "資料不足", "不產生風險指標", "", "下一交易日", None, macro_data
    t_open, t_close, p_close = twii_df['Open'].iloc[-1], twii_df['Close'].iloc[-1], twii_df['Close'].iloc[-2]
    if safe_num(twii_close, 0) > 0:
        t_close = twii_close
        p_close = twii_close - safe_num(twii_change, 0)
    
    try:
        last_dt_str = pd.Timestamp(twii_df.index[-1]).strftime("%Y/%m/%d")
    except (TypeError, ValueError):
        last_dt_str = "資料日期不明"
    
    today_title, today_desc = "⚖️ 開近平盤", "開盤接近前一日收盤；本描述只反映 OHLC 形態，不推測買賣原因。"
    if t_open > p_close * 1.003:
        if t_close > t_open:
            today_title, today_desc = "🔥 開高走高", "開盤高於前收 0.3% 以上，且收盤高於開盤。"
        else:
            today_title, today_desc = "⚠️ 開高走低", "開盤高於前收 0.3% 以上，但收盤低於或等於開盤。"
    elif t_open < p_close * 0.997:
        if t_close > t_open:
            today_title, today_desc = "💪 開低走高", "開盤低於前收 0.3% 以上，但收盤高於開盤。"
        else:
            today_title, today_desc = "🩸 開低走低", "開盤低於前收 0.3% 以上，且收盤低於或等於開盤。"

    risk_score = 50 
    if t_close < (twii_df['5MA'].iloc[-1] if '5MA' in twii_df.columns else t_close): risk_score += 15
    else: risk_score -= 10
    sox_pct = macro_data.get('^SOX', {}).get('pct')
    vix_price = macro_data.get('^VIX', {}).get('price')
    twd_pct = macro_data.get('TWD=X', {}).get('pct')
    txf_pct = macro_data.get('TX=F', {}).get('pct')
    if sox_pct is not None and sox_pct < -2.0: risk_score += 20
    if vix_price is not None and vix_price > 20: risk_score += 20
    if twd_pct is not None and twd_pct > 0.4: risk_score += 8
    if txf_pct is not None and txf_pct < -0.5: risk_score += 10
    # Missing feeds lower coverage; they are not evidence that market risk rose.
    missing_macro = sum(1 for v in macro_data.get("status", {}).values() if v != "ok")
    risk_score = max(5, min(95, int(risk_score))) 
    
    if risk_score < 40:
        tmr_title, tmr_desc = "較低風險區", "依目前可用的短均與外部市場規則計算；這是風險指標，不是漲跌機率。"
    elif risk_score < 70:
        tmr_title, tmr_desc = "中性風險區", "部分條件未同步或資料不完整；這是風險指標，不是開盤預測。"
    else:
        tmr_title, tmr_desc = "較高風險區", "目前可用條件觸發較多風險規則；這不代表下一交易日必然下跌。"
    if missing_macro:
        tmr_desc += f" 另有 {missing_macro} 項外部資料缺漏，未納入風險加減分。"
    return today_title, today_desc, tmr_title, tmr_desc, last_dt_str, "下一交易日", risk_score, macro_data

def render_index_board():
    try:
        twii_close, twii_change, twii_time_str = get_twii_quote()
        txf_close, txf_change, txf_symbol, txf_time = get_txf_quote(twii_close)
        twii_available = twii_close is not None and twii_change is not None
        twii_color = '#94a3b8' if not twii_available else ('#ef4444' if twii_change >= 0 else '#22c55e')
        txf_available = txf_close is not None and txf_change is not None
        txf_color = '#94a3b8' if not txf_available else ('#ef4444' if txf_change >= 0 else '#22c55e')
        txf_price_text = f"{txf_close:,.0f}" if txf_available else "資料源受限"
        txf_change_text = f"{'↑' if txf_change > 0 else '↓'} {abs(txf_change):.0f}" if txf_available else "請改用 FinMind/券商源"
        twii_df_for_pred = get_stock_data("^TWII")
        today_title, today_desc, tmr_title, tmr_desc, last_dt_str, next_dt_str, risk_score, macro = open_pred_logic(twii_df_for_pred, twii_close, twii_change, twii_time_str)
        sox = macro.get('^SOX', {"price": None, "pct": None})
        vix = macro.get('^VIX', {"price": None, "pct": None})
        twd = macro.get('TWD=X', {"price": None, "pct": None})
        risk_available = risk_score is not None
        bar_color = (
            "#94a3b8" if not risk_available
            else "#22c55e" if risk_score < 40
            else "#facc15" if risk_score < 70
            else "#ef4444"
        )
        risk_width = risk_score if risk_available else 0
        render_market_status_cards([
            {
                "label": "台股加權",
                "value": f"{twii_close:,.0f}" if twii_available else "--",
                "sub": f"{'+' if twii_change > 0 else ''}{twii_change:.0f}" if twii_available else "資料不足",
                "color": twii_color,
            },
            {"label": f"台指期 ({txf_symbol})", "value": txf_price_text, "sub": txf_change_text, "color": txf_color},
            {"label": "費城半導體", "value": "--" if sox.get("price") is None else f"{sox.get('price'):,.1f}", "sub": "--" if sox.get("pct") is None else f"{sox.get('pct'):+.2f}%", "color": "#ef4444" if (sox.get("pct") or 0) >= 0 else "#22c55e"},
            {"label": "VIX", "value": "--" if vix.get("price") is None else f"{vix.get('price'):,.2f}", "sub": "--" if vix.get("pct") is None else f"{vix.get('pct'):+.2f}%", "color": "#22c55e" if vix.get("pct") is not None and vix.get("pct") <= 0 else "#ef4444"},
            {"label": "美元台幣", "value": "--" if twd.get("price") is None else f"{twd.get('price'):,.3f}", "sub": "--" if twd.get("pct") is None else ("台幣貶值" if twd.get("pct") > 0 else "台幣升值"), "color": "#facc15"},
            {"label": "規則型風險指標", "value": f"{risk_score}/100" if risk_available else "--", "sub": "非漲跌機率" if risk_available else "資料不足", "color": bar_color},
        ])
        st.markdown(
            f"""
<div style="background:#0F172A; border:1px solid #1E293B; border-radius:10px; padding:12px 14px; margin:10px 0 14px 0;">
  <div style="display:flex; justify-content:space-between; gap:14px; align-items:center; flex-wrap:wrap;">
    <div style="flex:1; min-width:260px;">
      <span style="color:#FACC15; font-weight:900;">盤勢分析 ({escape_html(last_dt_str)})</span>
      <span style="color:#E2E8F0; font-weight:900; margin-left:10px;">{escape_html(today_title)}</span>
      <span style="color:#94A3B8; margin-left:8px; font-size:0.86rem;">{escape_html(today_desc)}</span>
    </div>
    <div style="flex:1; min-width:260px;">
      <span style="color:#60A5FA; font-weight:900;">風險觀察 ({escape_html(next_dt_str)})</span>
      <span style="color:{bar_color}; font-weight:900; margin-left:10px;">{escape_html(tmr_title)}</span>
      <span style="color:#94A3B8; margin-left:8px; font-size:0.86rem;">{escape_html(tmr_desc)}</span>
    </div>
  </div>
  <div style="width:100%; height:8px; background-color:#1E293B; border-radius:6px; overflow:hidden; margin-top:10px;">
    <div style="width:{risk_width}%; height:100%; background-color:{bar_color};"></div>
  </div>
</div>
""",
            unsafe_allow_html=True,
        )
        if st.button("手動更新即時大盤報價", width="stretch"):
            get_twii_quote.clear()
            get_txf_quote.clear()
            get_global_macro_data.clear()
            fetch_twse_index_history.clear()
            st.rerun()
    except Exception: st.error("大盤儀表板加載中...")

def get_dynamic_theme(ticker, industry):
    ind = str(industry).strip() if pd.notna(industry) and industry != "無" else "一般產業"
    for kw, ic in { "半導體": "⚙️", "電子": "⚡", "綠能": "🌱", "航運": "🚢", "金融": "💰", "AI": "💡", "機器人": "🤖" }.items():
        if kw in ind: return (ind, ic)
    return (ind, "🏷️")

MIN_ANALYSIS_BARS = 60


def require_analysis_result(result, ticker_number, df):
    """Stop this Streamlit page cleanly when the technical history is incomplete."""
    if isinstance(result, dict):
        return result
    available = len(df) if df is not None else 0
    st.warning(
        f"{normalize_ticker(ticker_number)} 目前只有 {available} 個交易日的完整行情，"
        f"至少需要 {MIN_ANALYSIS_BARS} 個交易日才能計算 60MA 與完整解析。"
    )
    st.caption("系統不會以缺值或推估值補齊指標，資料足夠後會自動恢復。")
    st.stop()
    return {}  # pragma: no cover - st.stop() interrupts the current Streamlit run.


@st.cache_data(ttl=5, show_spinner=False)
def analyze_today(
    df,
    ticker_number,
    inst_data=None,
    is_light_mode=False,
    pre_fund=None,
    cached_doc=None,
    is_intraday=False,
    historical_date="",
):
    if df is None or len(df) < MIN_ANALYSIS_BARS:
        return None
    required_columns = {
        "Open", "High", "Low", "Close", "Volume", "5MA", "20MA",
        "60MA", "BB_UP", "BB_DN", "MACD_Hist", "K", "D", "J", "RSI", "ATR", "ADX",
    }
    if not required_columns.issubset(df.columns):
        logging.error("%s 缺少必要技術欄位，本次不產生分析", ticker_number)
        return None
    t, p = df.iloc[-1], df.iloc[-2]
    if any(safe_num(t.get(column), None) is None for column in required_columns):
        logging.error("%s 最新技術欄位含缺值，本次不產生分析", ticker_number)
        return None
    if any(optional_num(p.get(column)) is None for column in ("Open", "Close", "MACD_Hist")):
        logging.error("%s 前一交易日必要欄位含缺值，本次不產生分析", ticker_number)
        return None
    score_mode, score_mode_label, effective_intraday = resolve_score_mode(is_intraday)
    historical_date = safe_iso_date(historical_date)
    
    if pre_fund:
        fund = dict(pre_fund)
    elif historical_date:
        fund = {
            "EPS": "無", "EPS_Period": "missing", "PE": "無",
            "Industry": "一般產業", "_status": "missing",
            "MoM": None, "YoY": None, "_data_status": {"revenue": "missing"},
        }
    else:
        fund, loaded_inst_data = get_analysis_support_data(
            ticker_number, round(t['Close'], 2), cached_doc=cached_doc
        )
        if inst_data is None:
            inst_data = loaded_inst_data
        
    if historical_date:
        macro = {
            "status": {"^SOX": "missing", "^VIX": "missing", "TWD=X": "missing", "TX=F": "missing"}
        }
        fund['VIX'] = None
    else:
        macro = get_global_macro_data()
        fund['VIX'] = macro.get('^VIX', {}).get('price')
    
    twii_df = fetch_twse_index_history()
    if historical_date and twii_df is not None:
        cutoff = pd.Timestamp(historical_date)
        twii_df = twii_df[pd.to_datetime(twii_df.index).tz_localize(None) <= cutoff]
    market_regime = ""
    market_return = None
    if twii_df is not None and len(twii_df) >= 60:
        ma20_twii = twii_df['Close'].rolling(20).mean()
        ma60_twii = twii_df['Close'].rolling(60).mean()
        fund['TWII_Close'] = float(twii_df['Close'].iloc[-1])
        fund['TWII_MA20'] = float(ma20_twii.iloc[-1])
        fund['TWII_MA60'] = float(ma60_twii.iloc[-1])
        previous_twii_close = float(twii_df['Close'].iloc[-2])
        market_return = (
            (fund['TWII_Close'] / previous_twii_close - 1) * 100
            if previous_twii_close > 0 else None
        )
        market_regime = (
            "多頭"
            if fund['TWII_Close'] >= fund['TWII_MA20'] and fund['TWII_Close'] >= fund['TWII_MA60']
            else "空頭"
            if fund['TWII_Close'] < fund['TWII_MA20'] and fund['TWII_Close'] < fund['TWII_MA60']
            else "震盪"
        )
        
    t_open, t_close, t_high, t_low = float(t['Open']), float(t['Close']), float(t['High']), float(t['Low'])
    p_open, p_close = float(p['Open']), float(p['Close'])
    
    red_mask = (df['Open'].shift(1) > df['Close'].shift(1)) & (df['Close'] > df['Open']) & (df['Close'] > df['Open'].shift(1)) & (df['Open'] < df['Close'].shift(1))
    black_mask = (df['Close'].shift(1) > df['Open'].shift(1)) & (df['Open'] > df['Close']) & (df['Open'] > df['Close'].shift(1)) & (df['Close'] < df['Open'].shift(1))

    cached_whale_net = optional_num(cached_doc.get("Whale_Net")) if cached_doc else None
    cached_inst_days = int(safe_num(cached_doc.get("Institutional_Days"), 0)) if cached_doc else 0
    whale_net_buy = None
    whale_net_days = 0
    f_net_10d, t_net_10d, d_net_10d = 0, 0, 0
    inst_days = len(inst_data) if inst_data else (cached_inst_days if cached_whale_net is not None else 0)
    # 法人資料有幾天算幾天，避免少於 3 天時把籌碼歸零
    if inst_data:
        f_net_10d = round(sum(safe_num(x['外資(張)']) for x in inst_data), 3)
        t_net_10d = round(sum(safe_num(x['投信(張)']) for x in inst_data), 3)
        d_net_10d = round(sum(safe_num(x['自營商(張)']) for x in inst_data), 3)
        sample_days = min(3, inst_days)
        f_net = sum(safe_num(x['外資(張)']) for x in inst_data[:sample_days])
        t_net = sum(safe_num(x['投信(張)']) for x in inst_data[:sample_days])
        d_net = sum(safe_num(x['自營商(張)']) for x in inst_data[:sample_days])
        whale_net_buy = round(f_net + t_net + d_net, 3)
        whale_net_days = sample_days
    elif cached_whale_net is not None:
        whale_net_buy = cached_whale_net
        whale_net_days = int(safe_num(cached_doc.get('Whale_Net_Days'), 0))

    theme_name, theme_icon = get_dynamic_theme(ticker_number, fund['Industry'])
    price_anchor = safe_num(t.get('VWAP'), None)
    has_real_vwap = price_anchor is not None and price_anchor > 0
    price_dev_source = "real_vwap" if has_real_vwap else "missing"
    price_dev = (t_close - price_anchor) / price_anchor * 100 if has_real_vwap else None
    if effective_intraday and len(df) >= 6:
        avg_vol_5 = df['Volume'].iloc[-6:-1].mean()
    else:
        avg_vol_5 = df['Volume'].tail(5).mean()
    effective_volume, est_vol_ratio, volume_confirmed = adjust_intraday_volume(t['Volume'], avg_vol_5, effective_intraday)
    
    intraday_score = None
    if effective_intraday and has_real_vwap:
        intraday_score = max(
            10,
            min(99, int(40 + (price_dev * 10) + (20 if est_vol_ratio > 1.5 else (10 if est_vol_ratio > 1.0 else -10)))),
        )
    if not effective_intraday:
        flow = "盤後不適用"
    elif not has_real_vwap:
        flow = "VWAP 資料不足"
    elif est_vol_ratio > 1.5 and t_close > price_anchor:
        flow = "量價偏強（非大單判定）"
    else:
        flow = "量價未同步轉強"

    body_len = abs(t_close - t_open)
    lower_shadow = min(t_close, t_open) - t_low
    upper_shadow = t_high - max(t_close, t_open)
    
    trend_quality = 0
    if t_close > t.get('20MA', t_close): trend_quality += 1
    if t.get('20MA', t_close) > t.get('60MA', t_close): trend_quality += 1
    if t.get('MACD_Hist', 0) > p.get('MACD_Hist', 0): trend_quality += 1
    if t.get('ADX', 0) >= 25: trend_quality += 1
    momentum_score = round((trend_quality / 4) * 100, 1)

    has_support = (lower_shadow > body_len * 1.5) and (effective_volume > avg_vol_5) and volume_confirmed
    hit_pressure = (upper_shadow > body_len * 1.5)
    ma5_up_today = bool(len(df) >= 6 and float(df['Close'].iloc[-1]) > float(df['Close'].iloc[-6]))
    tomorrow_turn_price = float(df['Close'].iloc[-4]) if len(df) >= 4 else t_close
    bullish_count = sum([
        t_close > t.get('20MA', t_close),
        t.get('MACD_Hist', 0) > p.get('MACD_Hist', 0),
        effective_volume > avg_vol_5 * 1.1 if avg_vol_5 > 0 else False,
        bool(red_mask.iloc[-1]),
        has_support,
        ma5_up_today,
    ])
    bearish_count = sum([
        t_close < t.get('20MA', t_close),
        t.get('MACD_Hist', 0) <= p.get('MACD_Hist', 0),
        t.get('RSI', 50) >= 75,
        bool(black_mask.iloc[-1]),
        hit_pressure,
        t_close < tomorrow_turn_price if tomorrow_turn_price > 0 else False,
    ])
    conflict_score = min(bullish_count, bearish_count) / max(bullish_count, bearish_count, 1)
    signal_conflict = "高" if conflict_score >= 0.55 else ("中" if conflict_score >= 0.3 else "低")
    if hit_pressure and t.get('RSI', 50) >= 75:
        entry_pattern = "過熱追高型"
    elif t_close > t.get('20MA', t_close) and est_vol_ratio > 1.5 and t.get('MACD_Hist', 0) > p.get('MACD_Hist', 0):
        entry_pattern = "趨勢突破型"
    elif has_support and t_close > t.get('20MA', t_close):
        entry_pattern = "回測支撐型"
    elif t.get('RSI', 50) <= 35 and t_close > p_close:
        entry_pattern = "低檔反彈型"
    elif t_close > t.get('20MA', t_close) and hit_pressure:
        entry_pattern = "假突破風險型"
    else:
        entry_pattern = "一般觀察型"
    source_status = fund.get("_data_status", {}) if isinstance(fund.get("_data_status", {}), dict) else {}
    data_quality, confidence = build_data_quality(
        price_status="realtime" if effective_intraday else "ok",
        volume_status="confirmed" if volume_confirmed else "estimated",
        institutional_days=inst_days,
        fundamental_status=fund.get("_status", "unknown"),
        revenue_status=source_status.get("revenue", "unknown"),
        financial_status=source_status.get("financial", "unknown"),
        macro_status=macro.get("status", {}),
        txf_status=macro.get("status", {}).get("TX=F", "missing")
    )

    data = {
        "代號": ticker_number, "名稱": get_stock_name(ticker_number), "ticker_raw": ticker_number,
        "Data_Date": pd.Timestamp(df.index[-1]).strftime("%Y-%m-%d"),
        "產業": fund['Industry'], "昨日收盤價": round(p_close, 2),
        "開盤價": round(t_open, 2), "最高價": round(t_high, 2), "最低價": round(t_low, 2),
        "收盤價": round(t_close, 2),
        "漲跌": round(t_close - p_close, 2), "漲跌幅": round((t_close - p_close) / p_close * 100, 2), 
        "成交量": int(t['Volume']), "估算成交量": int(effective_volume), "原始成交量": int(t['Volume']), "5日均量": int(avg_vol_5),
        "5MA": round(t.get('5MA', t_close), 2), "10MA": round(t.get('10MA', t_close), 2), 
        "20MA": round(t.get('20MA', t_close), 2), "60MA": round(t.get('60MA', t_close), 2),
        "BB_UP": round(t.get('BB_UP', t_close), 2), "BB_DN": round(t.get('BB_DN', t_close), 2), 
        "BIAS": round(t.get('BIAS_20', 0), 2), "MACD柱": round(t.get('MACD_Hist', 0), 3), "前日MACD柱": round(p.get('MACD_Hist', 0), 3),
        "K": round(t.get('K', 50), 2), "D": round(t.get('D', 50), 2), "J值": round(t.get('J', 50), 2),
        "ADX": round(t.get('ADX', 0), 1), "RSI": round(t.get('RSI', 50), 1),
        "ROC_20": round((t_close - float(df['Close'].iloc[-21])) / float(df['Close'].iloc[-21]) * 100 if len(df)>=21 else 0, 2),
        "MoM": fund.get('MoM'), "YoY": fund.get('YoY'),
        "Revenue_Status": source_status.get("revenue", "unknown"),
        "Revenue_Period": fund.get("Revenue_Period", ""),
        "Revenue_Source": fund.get("Revenue_Source", ""),
        "Institutional_Status": fund.get("_institutional_status", "ok" if inst_days else "missing"),
        "Institutional_Source": fund.get("Institutional_Source", ""),
        "ForeignNet10d": f_net_10d, "TrustNet10d": t_net_10d, "DealerNet10d": d_net_10d, 
        "紅吞": bool(red_mask.iloc[-1]), "黑吞": bool(black_mask.iloc[-1]),
        "訊號": t_close > t.get('20MA', t_close), 
        "回測有撐": has_support,
        "反彈遇壓": hit_pressure,
        "5MA已上彎": ma5_up_today, "明日5MA扣抵價": round(tomorrow_turn_price, 2),
        "5日線即將上彎": ma5_up_today,
        "Whale_Net": whale_net_buy, "Whale_Net_Days": whale_net_days,
        "Institutional_Sell_Streak": next((index for index, row in enumerate((inst_data or [])[:3]) if safe_num(row.get('單日合計(張)'), 0) >= 0), min(3, len(inst_data or []))) if inst_data else None,
        "Foreign_Net": f_net if inst_data else None,
        "Trust_Net": t_net if inst_data else None,
        "Theme_Name": theme_name, "Theme_Icon": theme_icon,
        "Price_Dev": price_dev, "Price_Dev_Source": price_dev_source, "Ohlc_Avg_Dev": None,
        "VWAP_Dev": price_dev if has_real_vwap else None,
        "Est_Vol_Ratio": est_vol_ratio, "Volume_Confirmed": volume_confirmed,
        "Flow": flow, "Intraday_Score": intraday_score, "Momentum_Score": momentum_score,
        "Institutional_Days": inst_days, "Data_Quality": data_quality,
        "Confidence": confidence, "Data_Completeness": confidence,
        "Market_Regime": market_regime, "Market_Return": round(market_return, 2) if market_return is not None else None,
        "Financial_Period": fund.get("Financial_Period", ""),
        "Financial_Source": fund.get("Financial_Source", ""),
        "Financial_Status": fund.get("Financial_Status", "unknown"),
        "Financial_Revenue": fund.get("Financial_Revenue"),
        "Financial_Gross_Profit": fund.get("Financial_Gross_Profit"),
        "Financial_Operating_Income": fund.get("Financial_Operating_Income"),
        "Financial_Net_Income": fund.get("Financial_Net_Income"),
        "Financial_EPS": fund.get("Financial_EPS"),
        "Financial_Gross_Margin": fund.get("Financial_Gross_Margin"),
        "Financial_Operating_Margin": fund.get("Financial_Operating_Margin"),
        "Financial_Net_Margin": fund.get("Financial_Net_Margin"),
        "Financial_Debt_Ratio": fund.get("Financial_Debt_Ratio"),
        "Financial_Current_Ratio": fund.get("Financial_Current_Ratio"),
        "Financial_Risk_Level": fund.get("Financial_Risk_Level", "unknown"),
        "Financial_Risk_Flags": fund.get("Financial_Risk_Flags", []),
        "Signal_Conflict": signal_conflict, "Conflict_Score": round(conflict_score, 2), "Entry_Pattern": entry_pattern,
        "ATR": round(float(t['ATR']), 2),
        "ATR_Target": round(t_close + (float(t['ATR']) * 1.5), 1),
        "ATR_Stop": round(t_close - float(t['ATR']), 1),
        "RRR": 1.5,
        "Intraday_Signal": (
            "VWAP 資料不足" if not has_real_vwap
            else "量價站上 VWAP" if t_close > price_anchor and est_vol_ratio > 1.3 and volume_confirmed
            else "價格站上 VWAP" if t_close > price_anchor
            else "價格低於 VWAP"
        ),
    }
    
    sc, label, rs, feature = get_decision_score(
        data, 
        fund, 
        inst_data, 
        mode=score_mode, 
        with_reason=True
    )
    
    data['Score'] = sc
    data['評級'] = label
    data['Reasons'] = rs
    data['Feature'] = feature
    default_backtest = calculate_historical_performance(
        df,
        1.5,
        1.0,
        lookback_days=BACKTEST_LOOKBACK_DAYS,
    )
    data['WinRate'] = default_backtest.get('win_rate')
    data['Backtest_Samples'] = default_backtest.get('closed_signals')
    data['Backtest_Scope'] = default_backtest.get('backtest_scope')
    data['Validation_WinRate'] = default_backtest.get('validation_win_rate')
    data['Validation_Samples'] = default_backtest.get('validation_samples')
    model_confidence, model_confidence_label = build_model_confidence(
        data.get('WinRate'),
        data.get('Backtest_Samples'),
        data.get('Validation_WinRate'),
        data.get('Validation_Samples'),
    )
    data['Model_Confidence'] = model_confidence
    data['Model_Confidence_Label'] = model_confidence_label
    data['Score_Mode'] = score_mode_label
    data['Score_Mode_Raw'] = score_mode
    data.update(build_entry_readiness(data, intraday=effective_intraday, baseline_plan=cached_doc))

    return data

def calculate_historical_winrate_interactive(
    df_slice, 
    target_mult, 
    stop_mult, 
    score_threshold=65,
    enable_trailing=False, 
    filter_low_conf=False, 
    filter_high_conflict=False
):
    result = calculate_historical_performance(
        df_slice, 
        target_mult, 
        stop_mult, 
        score_threshold=score_threshold,
        enable_trailing=enable_trailing,
        filter_low_conf=filter_low_conf,
        filter_high_conflict=filter_high_conflict
    )
    return result["win_rate"], result["closed_signals"], result["wins"], result["buy_dates"], result

def generate_comprehensive_analysis_sections(data, inst_data, sc, f_data, is_light_mode=False):
    t_text_c = "#333" if is_light_mode else "#e2e8f0"
    card_bg = "#f4f6f9" if is_light_mode else "#0f172a"
    sum_bg = "rgba(0,0,0,0.05)" if is_light_mode else "rgba(30,41,59,0.5)"
    b_col = "#ddd" if is_light_mode else "#1e293b"

    if sc <= 0:
        text_desc = "必要資料不足，本次不產生量化判斷。"
    elif sc >= 60:
        text_desc = "目前可用的規則型技術條件偏強；分數不是勝率或報酬保證，仍需等待價格與量能確認。"
    elif sc >= 45:
        text_desc = "目前可用的規則型條件偏多，但仍有未確認項目；建議等待後續訊號。"
    else:
        text_desc = "目前可用的規則型條件不足，暫不形成主動進場判斷。"
    
    tech_html = f"<div style='border: 1px solid {b_col}; border-radius: 8px; padding: 15px; margin-bottom: 15px; background-color: {card_bg};'>"
    tech_html += f"<h4 style='color: #60a5fa; margin-top: 0; font-size: 1.2rem;'>💯 技術面</h4>"
    quality = data.get("Data_Quality", {}) if isinstance(data.get("Data_Quality", {}), dict) else {}
    confidence = safe_num(data.get("Data_Completeness", data.get("Confidence")), 0)
    missing_quality = [
        key for key, value in quality.items()
        if value not in ("ok", "realtime", "confirmed")
        and not str(value).endswith(("d", "日"))
    ]
    quality_text = "資料完整" if not missing_quality else "需留意：" + "、".join(str(key) for key in missing_quality)
    tech_html += f"<div style='display:flex; gap:8px; flex-wrap:wrap; margin-bottom:10px; font-size:0.82rem;'>"
    tech_html += f"<span style='border:1px solid {b_col}; border-radius:6px; padding:4px 8px; color:{t_text_c}; background-color:{sum_bg};'>資料完整度 {confidence}%</span>"
    tech_html += f"<span style='border:1px solid {b_col}; border-radius:6px; padding:4px 8px; color:{t_text_c}; background-color:{sum_bg};'>{escape_html(quality_text)}</span>"
    tech_html += f"</div>"
    
    tech_html += f"<ul style='line-height: 1.6; margin-top: 10px; font-size: 0.95rem; color: {t_text_c}; list-style-type: none; padding-left: 0;'>"
    for r in data.get('Reasons', []):
        r = str(r)
        safe_reason = escape_html(r)
        if "✅" in r or "🔥" in r or "🚀" in r or "💰" in r or "📈" in r or "🏦" in r or "👑" in r or "🧨" in r: 
            tech_html += f"<li style='margin-bottom: 5px;'><span style='color:#ef4444; font-weight:bold;'>{safe_reason}</span></li>"
        elif "⚠️" in r or "🚨" in r or "🩸" in r or "📦" in r: 
            tech_html += f"<li style='margin-bottom: 5px;'><span style='color:#22c55e;'><b>{safe_reason}</b></span></li>"
        else:
            tech_html += f"<li style='margin-bottom: 5px;'>{safe_reason}</li>"
    tech_html += f"</ul>"
    
    tech_html += f"<div style='background-color: {sum_bg}; padding: 12px; border-radius: 6px; border-left: 4px solid #60a5fa; font-size: 0.95rem; color: {t_text_c}; margin-top: 15px;'><b>【總結】</b>{text_desc}</div>"
    tech_html += f"</div>"

    chip_res_text = "中立觀望"
    tables_html = ""
    th_color = "#ccc" if not is_light_mode else "#555"
    def get_c(val): return "#ef4444" if safe_num(val) > 0 else ("#22c55e" if safe_num(val) < 0 else t_text_c)

    f_net = preserve_lot_value(data.get('ForeignNet10d', 0), 0)
    t_net = preserve_lot_value(data.get('TrustNet10d', 0), 0)
    d_net = preserve_lot_value(data.get('DealerNet10d', 0), 0)
    institutional_status = str(data.get("Institutional_Status") or f_data.get("_institutional_status") or "unknown")
    institutional_source = str(
        data.get("Institutional_Source")
        or f_data.get("Institutional_Source")
        or (inst_data[0].get("_source", "") if inst_data and isinstance(inst_data[0], dict) else "")
    )
    aggregate_snapshot = institutional_aggregate_from_record(data)
    
    if inst_data:
        sample_days = min(3, len(inst_data))
        f_net_today = round(sum(safe_num(x.get('外資(張)', 0)) for x in inst_data[:sample_days] if isinstance(x, dict)), 3)
        t_net_today = round(sum(safe_num(x.get('投信(張)', 0)) for x in inst_data[:sample_days] if isinstance(x, dict)), 3)
        if f_net_today > 0 and t_net_today > 0:
            chip_res_text = f"近 {sample_days} 個可用交易日，外資與投信合計皆為買超；不推論後續走勢。"
        elif f_net_today < 0 and t_net_today < 0:
            chip_res_text = f"近 {sample_days} 個可用交易日，外資與投信合計皆為賣超；不推論籌碼流向對象。"
        else:
            chip_res_text = f"近 {sample_days} 個可用交易日，外資與投信方向不一致。"

        tables_html += f"<div style='display: flex; gap: 15px; flex-wrap: wrap; margin-top: 15px; width: 100%;'>"
        tables_html += f"<div style='flex: 1; min-width: 260px; border: 1px solid {b_col}; border-radius: 6px; padding: 15px; background-color: {sum_bg};'>"
        tables_html += f"<div style='font-weight: bold; color: {t_text_c}; font-size: 1rem; margin-bottom: 15px;'>🎯 進階籌碼監控 (真實數據)</div>"
        tables_html += f"<div style='font-size: 0.9rem; font-weight: bold; margin-bottom: 10px; color: {t_text_c};'>⚖️ 法人資料近 {len(inst_data)} 日可用，累積買賣超</div>"
        tables_html += f"<div style='display: flex; justify-content: space-between; font-size: 0.85rem; margin-bottom: 8px;'><span>外資及陸資</span><span style='color: {get_c(f_net)}; font-weight: bold;'>{'+' if f_net>0 else ''}{f_net:,} 張</span></div>"
        tables_html += f"<div style='display: flex; justify-content: space-between; font-size: 0.85rem; margin-bottom: 8px;'><span>投信</span><span style='color: {get_c(t_net)}; font-weight: bold;'>{'+' if t_net>0 else ''}{t_net:,} 張</span></div>"
        tables_html += f"<div style='display: flex; justify-content: space-between; font-size: 0.85rem;'><span>自營商</span><span style='color: {get_c(d_net)}; font-weight: bold;'>{'+' if d_net>0 else ''}{d_net:,} 張</span></div></div>"
        
        tables_html += f"<div style='flex: 1.5; min-width: 320px;'><div style='font-weight: bold; color: {t_text_c}; font-size: 0.95rem; margin-bottom: 10px;'>⏳ 近 {min(5, len(inst_data))} 日三大法人逐日買賣超明細 (張)</div>"
        tables_html += f"<table style='width: 100%; text-align: center; border-collapse: collapse; font-size: 0.9rem; border: 1px solid {b_col}; color: {t_text_c};'>"
        tables_html += f"<tr style='background-color: {sum_bg}; color: {th_color};'><th style='border: 1px solid {b_col}; padding: 8px 4px;'>日期</th><th style='border: 1px solid {b_col}; padding: 8px 4px;'>外資</th><th style='border: 1px solid {b_col}; padding: 8px 4px;'>投信</th><th style='border: 1px solid {b_col}; padding: 8px 4px;'>自營商</th><th style='border: 1px solid {b_col}; padding: 8px 4px;'>合計</th></tr>"
        
        for row in inst_data[:5]:
            foreign = preserve_lot_value(row.get('外資(張)', 0), 0)
            trust = preserve_lot_value(row.get('投信(張)', 0), 0)
            dealer = preserve_lot_value(row.get('自營商(張)', 0), 0)
            total = preserve_lot_value(row.get('單日合計(張)', 0), 0)
            tables_html += f"<tr><td style='border: 1px solid {b_col}; padding: 8px 4px;'>{escape_html(row.get('日期', ''))}</td><td style='border: 1px solid {b_col}; padding: 8px 4px; color: {get_c(foreign)}; font-weight: 500;'>{foreign}</td><td style='border: 1px solid {b_col}; padding: 8px 4px; color: {get_c(trust)}; font-weight: 500;'>{trust}</td><td style='border: 1px solid {b_col}; padding: 8px 4px; color: {get_c(dealer)}; font-weight: 500;'>{dealer}</td><td style='border: 1px solid {b_col}; padding: 8px 4px; color: {get_c(total)}; font-weight: 500;'>{total}</td></tr>"
        source_label = escape_html(institutional_source or "來源未標示")
        tables_html += f"</table><div style='text-align: right; font-size: 0.75rem; color: #888; margin-top: 10px;'>來源: {source_label}</div></div></div>"
    elif aggregate_snapshot:
        aggregate_net = aggregate_snapshot["net"]
        aggregate_days = aggregate_snapshot["days"]
        aggregate_source = institutional_source or aggregate_snapshot.get("source") or "盤後掃描保存值（來源未標示）"
        chip_res_text = (
            f"盤後掃描保存的近 {aggregate_days} 個交易日三大法人合計為"
            f"{'買超' if aggregate_net > 0 else '賣超' if aggregate_net < 0 else '持平'} "
            f"{abs(aggregate_net):,} 張；逐日明細暫時無法取得。"
        )
        tables_html = (
            f"<div style='margin-top:15px; border:1px solid {b_col}; border-radius:6px; padding:15px; background-color:{sum_bg};'>"
            f"<div style='font-weight:bold; color:{t_text_c}; margin-bottom:10px;'>🎯 法人合計備援（真實保存值）</div>"
            f"<div style='display:flex; justify-content:space-between; gap:12px; font-size:0.9rem;'>"
            f"<span>近 {aggregate_days} 日三大法人合計</span>"
            f"<span style='color:{get_c(aggregate_net)}; font-weight:bold;'>{aggregate_net:+,} 張</span></div>"
            f"<div style='color:#94a3b8; font-size:0.78rem; margin-top:10px;'>"
            "目前僅有已保存的合計值；不以 0 張拆分外資、投信與自營商，也不生成逐日明細。"
            f"</div><div style='text-align:right; color:#888; font-size:0.75rem; margin-top:8px;'>"
            f"來源：{escape_html(aggregate_source)}</div></div>"
        )
    else:
        status_messages = {
            "missing": "資料來源未設定且官方備援未取得資料",
            "empty": "官方來源查無此標的近期法人資料",
            "error": "法人資料來源暫時連線或解析失敗",
            "partial": "法人資料僅取得部分日期",
        }
        missing_reason = status_messages.get(institutional_status, "法人資料狀態尚未確認")
        tables_html = (
            f"<div style='color: {t_text_c}; font-size: 0.9rem; padding: 10px; border: 1px dashed {b_col}; border-radius: 6px;'>"
            f"目前暫無籌碼資料（{escape_html(missing_reason)}）；不以 0 張代替。</div>"
        )

    chip_html = f"<div style='border: 1px solid {b_col}; border-radius: 8px; padding: 15px; margin-bottom: 15px; background-color: {card_bg};'>"
    chip_html += f"<h4 style='color: #facc15; margin-top: 0; font-size: 1.2rem;'>🏦 籌碼面分析</h4>{tables_html}"
    chip_html += f"<div style='background-color: {sum_bg}; padding: 12px; border-radius: 6px; border-left: 4px solid #facc15; font-size: 0.95rem; color: {t_text_c}; margin-top: 15px;'><b>【總結】</b>{chip_res_text}</div></div>"

    fund_bullets = []
    eps = f_data.get('EPS', '無')
    pe = f_data.get('PE', '無')
    ind = str(f_data.get('Industry', '一般產業'))
    
    ticker_code = normalize_ticker(data.get('代號', ''))
    yahoo_news_url = f"https://tw.stock.yahoo.com/quote/{ticker_code}/news"
    if ind and ind not in ("一般產業", "無"):
        industry_text = f"資料源分類為【{escape_html(ind)}】；分類本身不代表受惠或成長。"
    else:
        industry_text = "產業分類資料不足。"
    fund_bullets.append(
        f"⚪ <b>產業分類</b>：{industry_text} "
        f"<a href='{yahoo_news_url}' target='_blank' rel='noopener noreferrer' "
        "style='color:#60a5fa; text-decoration:none;'>[查看相關新聞]</a>"
    )

    mom_raw = data.get('MoM')
    yoy_raw = data.get('YoY')
    mom_value = safe_num(mom_raw, None)
    yoy_value = safe_num(yoy_raw, None)
    revenue_period = str(data.get("Revenue_Period") or f_data.get("Revenue_Period") or "")
    revenue_source = str(data.get("Revenue_Source") or f_data.get("Revenue_Source") or "")
    revenue_status = str(data.get("Revenue_Status") or f_data.get("_data_status", {}).get("revenue", "unknown"))
    # Legacy snapshots did not retain period/source provenance; do not present their old zeros as verified growth.
    if not revenue_period and not revenue_source:
        mom_value = yoy_value = None
        if revenue_status == "ok":
            revenue_status = "unverified"
    revenue_meta = "、".join(part for part in (revenue_period, revenue_source) if part)
    if mom_value is None and yoy_value is None:
        status_messages = {
            "missing": "來源未設定",
            "empty": "官方來源查無此標的營收",
            "error": "來源暫時連線或解析失敗",
            "partial": "比較基期不足",
            "unverified": "舊資料缺少月份與來源，已停用",
        }
        reason = status_messages.get(revenue_status, "資料不足")
        fund_bullets.append(f"⚪ <b>最新月營收動能</b>：{escape_html(reason)}，不以 0% 代替。")
    else:
        mom_text = "--" if mom_value is None else f"{mom_value:.2f}%"
        yoy_text = "--" if yoy_value is None else f"{yoy_value:.2f}%"
        mom_c = "#ef4444" if mom_value is not None and mom_value > 0 else ("#22c55e" if mom_value is not None and mom_value < 0 else t_text_c)
        yoy_c = "#ef4444" if yoy_value is not None and yoy_value > 0 else ("#22c55e" if yoy_value is not None and yoy_value < 0 else t_text_c)
        fund_bullets.append(
            f"⚪ <b>最新月營收動能</b>：MoM <span style='color:{mom_c}; font-weight:bold;'>{mom_text}</span>，"
            f"YoY <span style='color:{yoy_c}; font-weight:bold;'>{yoy_text}</span>。"
            f" <span style='color:#94a3b8; font-size:0.82rem;'>({escape_html(revenue_meta)})</span>"
        )
    financial_period = str(data.get("Financial_Period") or f_data.get("Financial_Period") or "")
    financial_source = str(data.get("Financial_Source") or f_data.get("Financial_Source") or "")
    financial_status = str(data.get("Financial_Status") or f_data.get("Financial_Status") or "unknown")
    financial_risk = str(data.get("Financial_Risk_Level") or f_data.get("Financial_Risk_Level") or "unknown")
    operating_margin = safe_num(
        data.get("Financial_Operating_Margin", f_data.get("Financial_Operating_Margin")), None
    )
    net_margin = safe_num(
        data.get("Financial_Net_Margin", f_data.get("Financial_Net_Margin")), None
    )
    debt_ratio = safe_num(
        data.get("Financial_Debt_Ratio", f_data.get("Financial_Debt_Ratio")), None
    )
    current_ratio = safe_num(
        data.get("Financial_Current_Ratio", f_data.get("Financial_Current_Ratio")), None
    )
    if financial_status in {"ok", "partial"} and financial_period:
        risk_label = {"high": "高風險", "medium": "需留意", "low": "未觸發警示"}.get(
            financial_risk, "風險未定"
        )
        margin_text = (
            f"營益率 {'--' if operating_margin is None else f'{operating_margin:.2f}%'}、"
            f"淨利率 {'--' if net_margin is None else f'{net_margin:.2f}%'}、"
            f"負債比 {'--' if debt_ratio is None else f'{debt_ratio:.2f}%'}、"
            f"流動比 {'--' if current_ratio is None else f'{current_ratio:.2f}%'}"
        )
        fund_bullets.append(
            f"⚪ <b>最新季度財報品質</b>：{escape_html(financial_period)}｜{escape_html(risk_label)}｜"
            f"{escape_html(margin_text)}。"
            f" <span style='color:#94a3b8; font-size:0.82rem;'>({escape_html(financial_source)}；僅為目前最新快照)</span>"
        )
    else:
        fund_bullets.append("⚪ <b>最新季度財報品質</b>：官方目前快照資料不足，不以 0 代替。")
    eps_period = f_data.get("EPS_Period", "missing")
    eps_label = "近四季 EPS（TTM）" if eps_period == "ttm" else "EPS（資料源口徑）"
    fund_bullets.append(
        f"⚪ <b>{eps_label}</b>：<b>{escape_html(eps)}</b> 元。 | "
        f"<b>依該 EPS 計算的 PE</b>：<b>{escape_html(pe)}</b> 倍。"
    )
    
    try: 
        eps_f, float_pe = float(eps), float(pe) if pe != "無" else 999
        pe_low, pe_high = 10, 20
        if any(k in ind for k in ["半導體", "電子", "AI", "軟體"]): pe_low, pe_high = 15, 30
        elif any(k in ind for k in ["金融", "銀行", "保險"]): pe_low, pe_high = 8, 16
        elif any(k in ind for k in ["航運", "鋼鐵", "營建"]): pe_low, pe_high = 6, 14
        valuation = "偏低" if float_pe < pe_low else ("合理" if float_pe <= pe_high else "偏高")
        if mom_value is None or yoy_value is None:
            growth = "資料不足"
        else:
            growth = "轉強" if yoy_value > 10 and mom_value > 0 else ("非負成長" if yoy_value >= 0 else "年減")
        profit = "穩定" if eps_f > 0 else "虧損/不足"
        if eps_f <= 0:
            fund_res = f"⚪ 估值：資料不足｜成長：{growth}｜獲利：{profit}。"
        elif valuation == "偏高":
            fund_res = f"⚠️ 規則型估值參考：{valuation}（程式區間 {pe_low}-{pe_high} 倍，非市場共識）｜成長：{growth}｜獲利：{profit}。"
        else:
            fund_res = f"規則型估值參考：{valuation}（程式區間 {pe_low}-{pe_high} 倍，非市場共識）｜成長：{growth}｜獲利：{profit}。"
    except Exception: fund_res = "⚪ 基礎財報數據不足，暫以技術與籌碼面為主。"
    if financial_risk == "high":
        fund_res = "⚠️ 最新季度財報已觸發高風險，系統不列為可執行。" + fund_res

    fund_html = f"<div style='border: 1px solid {b_col}; border-radius: 8px; padding: 15px; margin-bottom: 15px; background-color: {card_bg};'>"
    fund_html += f"<h4 style='color: #c084fc; margin-top: 0; font-size: 1.2rem;'>📑 基本面分析</h4><ul style='font-size: 0.95rem; line-height: 1.6; color: {t_text_c}; list-style-type: none; padding-left: 0;'>"
    for b in fund_bullets: fund_html += f"<li style='margin-bottom:5px;'>{b}</li>"
    fund_html += f"</ul><div style='background-color: {sum_bg}; padding: 12px; border-radius: 6px; border-left: 4px solid #c084fc; font-size: 0.95rem; color: {t_text_c};'><b>【總結】</b>{fund_res}</div></div>"

    return {
        "technical": tech_html,
        "institutional": chip_html,
        "fundamental": fund_html,
    }


def generate_comprehensive_analysis(data, inst_data, sc, f_data, is_light_mode=False):
    sections = generate_comprehensive_analysis_sections(data, inst_data, sc, f_data, is_light_mode)
    return "".join(sections[name] for name in ("technical", "institutional", "fundamental"))

def generate_cards_html(df_disp, is_intraday=False, no_score=False):
    return build_cards_html(
        df_disp,
        is_intraday=is_intraday,
        favorite_set=get_favorite_stock_set(),
        simulated_set=get_simulated_order_stock_set(),
        normalize_ticker=normalize_ticker,
        get_stock_name=get_stock_name,
        safe_num=safe_num,
        is_realtime_score_record=is_realtime_score_record,
        score_mode_label=st.session_state.get("score_mode_label", "盤後正式分數"),
        no_score=no_score,
        target_date=st.session_state.get("scan_date", ""),
    )


if st.session_state.pop("_clear_data_caches_requested", False):
    for cached_function in [
        get_all_tw_stock_names_v3,
        fetch_twse_index_history,
        get_analysis_live_quote,
        _get_ohlcv_base,
        get_stock_data,
        _get_company_profile,
        get_finmind_chip_and_revenue_payload,
        get_twii_quote,
        get_txf_quote,
        get_institutional_trading,
        get_global_macro_data,
        analyze_today,
    ]:
        cached_function.clear()
    st.session_state["_cache_clear_notice"] = True
    st.rerun()

# ==========================================
# 🚀 頁面路由控制中心
# ==========================================
if st.session_state.page == "home":
    st.markdown("<h2 style='text-align: center; color: #818cf8; margin-bottom: 20px;'>極致精準：100分量化雷達</h2>", unsafe_allow_html=True)
    
    render_index_board()
    render_daily_scan_status_card(build_daily_scan_status(load_cloud_doc("system_locks", "daily_scan")))
    st.markdown("<br>", unsafe_allow_html=True)
    
    with st.spinner("🔮 正在自 Firebase 同步全市場量化名單..."):
        hydrate_scan_results()
    if not st.session_state.get("scan_results"):
        st.session_state.scan_results = [{"代號": t, "名稱": get_stock_name(t), "Score": 0, "產業": "一般產業"} for t in get_radar_targets([])]
        st.session_state.scan_results_is_local = True
    else:
        st.session_state.scan_results_is_local = False
            
    if st.session_state.scan_results:
        fetch_time = datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
        scan_date = st.session_state.get("scan_date", "")
        source_badge = "🧭 雲端名單空白，改用本機備援池即時計算" if st.session_state.get("scan_results_is_local") else "☁️ 雲端名單掃描日期"
        scan_date_str = f"{scan_date}" if scan_date else fetch_time
        scan_limit = st.session_state.get("scan_limit")
        universe_size = st.session_state.get("universe_size")
        if safe_num(universe_size) > 0:
            scope_text = f"｜實際掃描 {int(universe_size)} 檔"
        elif safe_num(scan_limit) > 0:
            scope_text = f"｜設定掃描 {int(scan_limit)} 檔"
        else:
            scope_text = ""
        st.markdown(f"<div style='font-size:0.95rem; color:#facc15; margin-bottom:15px; font-weight:bold;'>{escape_html(source_badge)}：{escape_html(scan_date_str)}{escape_html(scope_text)}</div>", unsafe_allow_html=True)
        if st.session_state.get("scan_results_stale"):
            expected = st.session_state.get("expected_scan_date", "")
            st.warning(f"雲端掃描資料尚未更新至最新交易日 {expected}；前台不會啟動全市場掃描，請等待背景排程完成。")
        if st.session_state.get("scan_results_is_local") and st.session_state.get("cloud_last_error"):
            st.caption(f"Firebase 狀態：{st.session_state.cloud_last_error}")

        st.markdown("<div class='terminal-card' style='margin-bottom:12px;'>", unsafe_allow_html=True)
        st.markdown("<div class='section-title'>雷達篩選器</div>", unsafe_allow_html=True)
        col_m1, col_m2, col_m3 = st.columns([1.2, 1.4, 0.8])
        with col_m1:
            st.caption("引擎模式")
            radar_mode = st.radio("引擎模式：", ["盤後波段精算", "盤中動能快篩"], horizontal=True, label_visibility="collapsed")
        with col_m2:
            st.caption("名單分類")
            list_type = st.radio("名單分類：", ["量化雷達 (60分以上)", "形態選股 (不列入評分)", "進階形態選股"], horizontal=True, label_visibility="collapsed")
        with col_m3:
            st.caption("自選群組")
            only_favorites = st.toggle("只看自選群組", value=False)
        st.caption("進場條件（量化雷達適用）")
        entry_filter = st.radio(
            "進場條件：",
            ["現在可執行", "等待確認／拉回", "全部候選"],
            horizontal=True,
            label_visibility="collapsed",
        )
        st.markdown("</div>", unsafe_allow_html=True)
        requested_intraday = "盤中" in radar_mode
        score_mode, score_mode_label, is_intraday = resolve_score_mode(requested_intraday)
        st.session_state.is_intraday = is_intraday
        st.session_state.score_mode_label = score_mode_label
        if requested_intraday and not is_intraday:
            st.caption("目前非台股交易時段，系統已自動改採盤後正式分數。")
        
        cached_list = [
            ensure_entry_readiness(row)
            for row in st.session_state.get('scan_results', [])
            if isinstance(row, dict)
        ]
        for row in cached_list:
            if optional_num(row.get("Score")) is not None:
                row["評級"] = decision_label(row.get("Score"))
        use_local_fallback = st.session_state.get("scan_results_is_local", False)
        cloud_count = 0 if use_local_fallback else len(cached_list)
        
        if is_intraday or use_local_fallback:
            spinner_text = (
                "⚡ 正在批次載入即時行情並重新評分原榜單..."
                if is_intraday
                else "⚡ 正在以本機資料重算榜單..."
            )
            with st.spinner(spinner_text):
                fb_df = pd.DataFrame(cached_list)
                targets = (
                    original_ranking_targets(cached_list)
                    if is_intraday
                    else get_radar_targets(cached_list)
                )
                live_data = []
                if is_intraday:
                    public_intraday_quotes, public_intraday_histories = (
                        get_public_intraday_bundle(cached_list)
                    )
                else:
                    public_intraday_quotes, public_intraday_histories = {}, {}
                intraday_trading_date = datetime.now(
                    timezone(timedelta(hours=8))
                ).strftime("%Y-%m-%d")
                base_by_ticker = {
                    normalize_ticker(row.get("代號", "")): row
                    for row in cached_list
                    if isinstance(row, dict) and normalize_ticker(row.get("代號", ""))
                }
                
                def process_live(ticker):
                    normalized_ticker = normalize_ticker(ticker)
                    base = base_by_ticker.get(normalized_ticker)
                    if is_intraday:
                        quote = public_intraday_quotes.get(normalized_ticker)
                        history = public_intraday_histories.get(normalized_ticker)
                        merged_history = merge_intraday_quote_into_history(
                            history,
                            quote,
                            trading_date=intraday_trading_date,
                        )
                        if merged_history is None:
                            return None
                        try:
                            df = apply_technical_indicators(merged_history)
                            df.attrs["intraday_quote_status"] = "realtime"
                            df.attrs["intraday_quote_source"] = str(quote.get("source") or "公開即時行情")
                        except Exception as indicator_error:
                            logging.warning("盤中批次技術指標失敗 %s: %s", normalized_ticker, indicator_error)
                            return None
                    else:
                        df = get_stock_data(ticker)
                    if df is not None:
                        if is_intraday and df.attrs.get("intraday_quote_status") != "realtime":
                            return None
                        cache_context = "realtime" if is_intraday else "latest"
                        analysis_cache = load_analysis_cache(ticker, LIVE_SCORE_CACHE_SECONDS, context=cache_context)
                        cached_data = analysis_cache.get("data") if analysis_cache else None
                        if is_intraday and isinstance(cached_data, dict) and cached_data.get("Score_Mode_Raw") == "realtime":
                            cached_data = dict(cached_data)
                            return annotate_intraday_score(base, cached_data)

                        if analysis_cache and analysis_cache.get("fund"):
                            fund = analysis_cache.get("fund")
                            inst_data = analysis_cache.get("inst_data", [])
                        elif is_intraday:
                            fund = support_data_from_postclose_record(
                                base,
                                current_price=float(df['Close'].iloc[-1]),
                            )
                            inst_data = institutional_rows_from_record(base)
                        else:
                            fund, inst_data = get_analysis_support_data(
                                ticker, df['Close'].iloc[-1], cached_doc=base
                            )
                        res = analyze_today(df, ticker, inst_data, False, fund, cached_doc=base, is_intraday=is_intraday)
                        if res:
                            if is_intraday:
                                for key in (
                                    "WinRate", "Backtest_Samples", "Validation_WinRate",
                                    "Validation_Samples", "Backtest_Scope",
                                ):
                                    if isinstance(base, dict) and key in base:
                                        res[key] = base[key]
                            else:
                                bt_preview = calculate_historical_performance(df, 1.5, 1.0)
                                res["WinRate"] = bt_preview.get("win_rate", res.get("WinRate", 0.0))
                                res["Backtest_Samples"] = bt_preview.get("closed_signals", 0)
                                res["Validation_WinRate"] = bt_preview.get("validation_win_rate", 0.0)
                                res["Validation_Samples"] = bt_preview.get("validation_samples", 0)
                                res["Backtest_Scope"] = bt_preview.get("backtest_scope", BACKTEST_SCOPE)
                            res["Score_Source"] = "盤中重算" if is_intraday else "本機備援重算"
                            if is_intraday:
                                res["Intraday_Quote_Source"] = df.attrs.get(
                                    "intraday_quote_source",
                                    "盤中延遲行情",
                                )
                            save_analysis_cache(
                                ticker,
                                {"data": res, "fund": fund, "inst_data": inst_data},
                                context=cache_context,
                            )
                            return annotate_intraday_score(base, res) if is_intraday else res
                    return None

                if is_intraday:
                    progress_bar = st.progress(0, text=f"盤中重評 0/{len(targets)}")
                    for index, ticker in enumerate(targets, start=1):
                        result = process_live(ticker)
                        if result:
                            live_data.append(result)
                        progress_bar.progress(
                            index / max(1, len(targets)),
                            text=f"盤中重評 {index}/{len(targets)}",
                        )
                    progress_bar.empty()
                else:
                    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                        for result in executor.map(process_live, targets):
                            if result:
                                live_data.append(result)
                if is_intraday:
                    df_results = pd.DataFrame(live_data)
                    failed_intraday_count = max(0, len(targets) - len(live_data))
                    if failed_intraday_count:
                        st.warning(
                            f"盤中即時重評完成 {len(live_data)}/{len(targets)} 檔；"
                            f"其餘 {failed_intraday_count} 檔因即時行情未取得，不沿用盤後分數。"
                        )
                else:
                    df_results = pd.DataFrame(live_data) if live_data else fb_df
        else:
            df_results = pd.DataFrame(cached_list)
        mode_count = len(df_results)

        if only_favorites:
            favorite_set = get_favorite_stock_set()
            if favorite_set and '代號' in df_results.columns:
                df_results = df_results[df_results['代號'].astype(str).map(normalize_ticker).isin(favorite_set)]
            else:
                df_results = df_results.iloc[0:0]
        favorite_count = len(df_results)
        
        if '產業' not in df_results.columns:
            df_results['產業'] = "一般產業"
        available_themes = ["全部產業"] + sorted(list(set(df_results['產業'].dropna().unique()) - {"一般產業"}))
        st.markdown("<div class='terminal-card' style='margin-bottom:12px;'>", unsafe_allow_html=True)
        col_f1, col_f2 = st.columns([1.6, 1])
        with col_f1:
            st.caption("產業過濾")
            selected_theme = st.radio("產業過濾：", available_themes, horizontal=True, label_visibility="collapsed")
        with col_f2:
            st.caption("排序")
            sort_mode = st.radio("排序：", ["量化分數", "技術面勝率", "資料完整度"], horizontal=True, label_visibility="collapsed")
        st.markdown("</div>", unsafe_allow_html=True)
        if selected_theme != "全部產業": df_results = df_results[df_results['產業'] == selected_theme]
        industry_count = len(df_results)
            
        is_pattern_mode = (list_type == "形態選股 (不列入評分)")
        is_adv_pattern_mode = (list_type == "進階形態選股")
        # 刪除金融股
        if not df_results.empty and '代號' in df_results.columns:
            is_fin_mask = df_results.apply(lambda r: is_financial_stock(r.get('代號'), r.get('產業')), axis=1)
            df_results = df_results[~is_fin_mask]

        for col in ("Score", "漲跌幅", "WinRate", "Confidence"):
            if col not in df_results.columns:
                df_results[col] = float("nan")
            else:
                df_results[col] = pd.to_numeric(df_results[col], errors="coerce")

        if not df_results.empty: 
            if is_adv_pattern_mode:
                if 'Advanced_Pattern' in df_results.columns:
                    if 'Advanced_Pattern_Signal' in df_results.columns:
                        df_results = df_results[df_results['Advanced_Pattern_Signal'].astype(str) == "Buy"]
                    else:
                        df_results = df_results[df_results['Advanced_Pattern'].astype(str).str.startswith("🟢")]
                else:
                    df_results = df_results.iloc[0:0]
                score_count = len(df_results)
            elif is_pattern_mode:
                if 'Feature' in df_results.columns:
                    df_results = df_results[df_results['Feature'].isin(["🔥 紅吞表態", "💪 回檔有撐", "📦 整理突破"])]
                else:
                    df_results = df_results.iloc[0:0]
                score_count = len(df_results)
            else:
                df_results = df_results[df_results['Score'] >= 60]
                score_count = len(df_results)
                if 'Entry_Status_Group' not in df_results.columns:
                    df_results['Entry_Status_Group'] = "watch"
                if entry_filter == "現在可執行":
                    df_results = df_results[df_results['Entry_Status_Group'].astype(str) == "ready"]
                elif entry_filter == "等待確認／拉回":
                    df_results = df_results[df_results['Entry_Status_Group'].astype(str) == "wait"]
            entry_count = len(df_results)
                
            sort_map = {
                "量化分數": ["Score", "漲跌幅"],
                "技術面勝率": ["WinRate", "Score", "漲跌幅"],
                "資料完整度": ["Confidence", "Score", "漲跌幅"],
            }
            df_disp = df_results.sort_values(by=sort_map.get(sort_mode, ["Score", "漲跌幅"]), ascending=[False] * len(sort_map.get(sort_mode, ["Score", "漲跌幅"]))).head(10)
            
            st.session_state.nav_pool = df_disp['代號'].tolist()
            st.session_state.nav_pool_data = df_disp.to_dict('records') 
            
            if is_pattern_mode or is_adv_pattern_mode:
                count_label = f"符合型態 {score_count} 檔"
            else:
                count_label = f"60分以上 {score_count} 檔 → {entry_filter} {entry_count} 檔"
            st.markdown(f"<div style='font-size:0.8rem; color:#94a3b8; border-bottom:1px solid #1e293b; padding-bottom:8px; margin-bottom:16px;'>⚡ 引擎運算完成 | 雲端 {cloud_count} 檔 → 模式 {mode_count} 檔 → 自選 {favorite_count} 檔 → 產業 {industry_count} 檔 → {count_label} | 顯示 {len(df_disp)} 檔</div>", unsafe_allow_html=True)
            if not df_disp.empty:
                left_dash, mid_dash, right_dash = st.columns([1.05, 2.1, 1.05])
                with left_dash:
                    market_rows = [
                        {"title": "掃描來源", "value": "本機" if use_local_fallback else "雲端", "sub": f"符合條件 {entry_count} 檔", "color": "#60A5FA"},
                        {"title": "目前模式", "value": "盤中" if is_intraday else "盤後", "sub": score_mode_label, "color": "#FACC15"},
                        {"title": "進場條件", "value": entry_filter if not (is_pattern_mode or is_adv_pattern_mode) else "型態觀察", "sub": selected_theme, "color": "#94A3B8"},
                    ]
                    render_home_side_panel("市場總覽", market_rows)
                with mid_dash:
                    title_label = "形態選股清單 (不計分)" if is_pattern_mode else "量化雷達清單"
                    st.markdown(f"<div class='section-title'>{title_label}</div>", unsafe_allow_html=True)
                    st.markdown(generate_cards_html(df_disp, is_intraday, no_score=is_pattern_mode), unsafe_allow_html=True)
                with right_dash:
                    favorite_set = get_favorite_stock_set()
                    fav_rows = []
                    for _, row in df_disp.head(20).iterrows():
                        if normalize_ticker(row.get("代號", "")) in favorite_set:
                            code = normalize_ticker(row.get("代號", ""))
                            name = get_stock_name(code)
                            display_title = f"{code} {name}" if code != name else code
                            fav_score = optional_num(row.get("Score"))
                            fav_rows.append({"title": display_title, "value": f"{fav_score:.0f}分" if fav_score is not None else "--", "sub": row.get("Feature") or "資料不足", "color": "#FACC15"})
                    
                    mover_rows = []
                    for _, r in df_disp.sort_values(by="漲跌幅", ascending=False).head(3).iterrows():
                        code = normalize_ticker(r.get('代號', ''))
                        name = get_stock_name(code)
                        display_title = f"{code} {name}" if code != name else code
                        mover_change = optional_num(r.get("漲跌幅"))
                        mover_rows.append({"title": display_title, "value": f"{mover_change:+.1f}%" if mover_change is not None else "--", "sub": r.get("Feature") or "資料不足", "color": "#EF4444" if mover_change is not None and mover_change >= 0 else "#22C55E"})

                    order_rows = []
                    for o in st.session_state.get("simulated_orders", [])[:3]:
                        ticker = o.get('ticker')
                        curr_price_str = ""
                        pl_str = ""
                        days_str = ""
                        stop_dist_str = ""
                        try:
                            if not df_results.empty and '代號' in df_results.columns:
                                match = df_results[df_results['代號'].astype(str).apply(normalize_ticker) == normalize_ticker(ticker)]
                                if not match.empty:
                                    cp = safe_num(match['收盤價'].values[0])
                                    bp = safe_num(o.get('buy_price', cp))
                                    curr_price_str = f" 現價{cp:.1f}"
                                    if bp > 0:
                                        pl_pct = (cp - bp) / bp * 100
                                        pl_str = f" {'▲' if pl_pct>=0 else '▼'}{abs(pl_pct):.1f}%"
                        except Exception: pass
                        try:
                            buy_time = o.get('time', '')
                            if buy_time:
                                buy_dt = datetime.fromisoformat(buy_time[:10]).replace(tzinfo=None)
                                hold_days = (datetime.now() - buy_dt).days
                                days_str = f"持倉{hold_days}天"
                        except Exception: pass
                        try:
                            sp = optional_num(o.get('stop_price'))
                            cp_val = optional_num(o.get('curr_price'))
                            if sp is not None and cp_val is not None and sp > 0 and cp_val > 0:
                                stop_dist = (cp_val - sp) / cp_val * 100
                                stop_dist_str = f" 離停{stop_dist:.1f}%"
                        except Exception: pass
                        sub_text = " | ".join(filter(None, [days_str, stop_dist_str, f"目標 {o.get('target_price', '--')}"]))
                        stock_name = o.get('name', '') or get_stock_name(ticker)
                        order_rows.append({"title": f"{ticker} {stock_name}{curr_price_str}{pl_str}", "value": f"停損 {o.get('stop_price', '--')}", "sub": sub_text, "color": "#60A5FA"})
                    render_home_side_panel("我的自選", fav_rows, "目前顯示名單沒有自選股")
                    render_home_side_panel("今日異動", mover_rows)
                    render_home_side_panel("模擬交易提醒", order_rows, "目前沒有模擬交易")
            else:
                if not (is_pattern_mode or is_adv_pattern_mode) and entry_filter == "現在可執行":
                    st.info("目前沒有同時進入觀察區間、量比達標且資料完整度足夠的股票；可切換「等待確認／拉回」查看候選。")
                elif not (is_pattern_mode or is_adv_pattern_mode) and entry_filter == "等待確認／拉回":
                    st.info("目前沒有等待量能、觸發或拉回的候選；可切換「全部候選」查看待新掃描資料。")
                else:
                    st.info("目前沒有符合此篩選條件的標的。")
        else:
            score_count = 0
            st.markdown(f"<div style='font-size:0.8rem; color:#94a3b8; border-bottom:1px solid #1e293b; padding-bottom:8px; margin-bottom:16px;'>⚡ 篩選過程 | 雲端 {cloud_count} 檔 → 模式 {mode_count} 檔 → 自選 {favorite_count} 檔 → 產業 {industry_count} 檔 → 60分以上 {score_count} 檔</div>", unsafe_allow_html=True)
            st.info("此條件下暫無標的。")
    else: st.info("💡 雲端資料庫目前無暫存數據。")

# ==========================================
# 📊 模擬交易中心 2.0：經理人績效儀表板
# ==========================================
elif st.session_state.page == "simulated_orders":
    st.markdown("<h2 style='text-align: center; color: #818cf8; margin-bottom: 20px;'>📊 經理人績效儀表板 2.0</h2>", unsafe_allow_html=True)
    
    col_home, col_clear = st.columns([1, 1])
    with col_home:
        if st.button("回雷達總機", width="stretch"):
            st.query_params.clear()
            st.session_state.page = "home"
            st.rerun()
    with col_clear:
        if st.button("清空所有紀錄", width="stretch"):
            st.session_state.simulated_orders = []
            saved = save_cloud_data("user_data", USER_ORDERS_DOC, [])
            if saved:
                st.success("已清除所有紀錄！")
            else:
                st.warning("紀錄已從本次工作階段清除，但雲端寫入失敗。")
            
    orders = st.session_state.get('simulated_orders', [])
    if not orders: st.info("目前沒有模擬下單紀錄，去解析頁面建立你的第一筆策略單吧！")
    else:
        if "delete_order_id" in st.session_state:
            st.session_state.simulated_orders = [o for o in orders if o.get('id') != st.session_state.delete_order_id]
            save_cloud_data("user_data", USER_ORDERS_DOC, st.session_state.simulated_orders)
            del st.session_state["delete_order_id"]; st.rerun()
            
        total_cost, total_value, wins = 0, 0, 0
        priced_orders, missing_quotes = 0, 0
        order_metrics = []
        
        for order in orders:
            df_temp = get_stock_data(order['ticker'])
            curr_price = optional_num(df_temp['Close'].iloc[-1]) if df_temp is not None and not df_temp.empty else None
            buy_price = optional_num(order.get('buy_price'))
            if curr_price is None or buy_price is None or buy_price <= 0:
                missing_quotes += 1
                order['curr_price'] = None
                order['pl_pct'] = None
                order['quote_status'] = "missing"
                continue

            priced_orders += 1
            order['quote_status'] = "ok"
            highest_price = optional_num(order.get('highest_price'))
            if highest_price is None:
                highest_price = buy_price
                order['highest_price'] = buy_price
            if curr_price > highest_price:
                order['highest_price'] = curr_price
                save_cloud_data("user_data", USER_ORDERS_DOC, st.session_state.simulated_orders)

            pl_val = curr_price - buy_price
            pl_pct = (pl_val / buy_price) * 100

            total_cost += buy_price * 1000
            total_value += curr_price * 1000
            if pl_val > 0: wins += 1
            
            order_metrics.append({"name": order['name'], "pct": pl_pct, "color": "#ef4444" if pl_pct >= 0 else "#22c55e"})
            order['curr_price'] = curr_price
            order['pl_pct'] = pl_pct

        total_pl = total_value - total_cost
        total_pl_pct = (total_pl / total_cost) * 100 if total_cost > 0 else None
        win_rate = (wins / priced_orders) * 100 if priced_orders > 0 else None
        total_pl_text = f"{'+' if total_pl > 0 else ''}{total_pl:,.0f} 元" if priced_orders else "--"
        total_pl_pct_text = f"({'+' if total_pl_pct and total_pl_pct > 0 else ''}{total_pl_pct:.2f}%)" if total_pl_pct is not None else "報價不足"
        win_rate_text = f"{win_rate:.1f}%" if win_rate is not None else "--"
        total_value_text = f"{total_value:,.0f} 元" if priced_orders else "--"
        
        st.markdown(f"""
        <div style='display: grid; grid-template-columns: repeat(3, 1fr); gap: 15px; margin-bottom: 25px;'>
            <div style='background-color: rgba(30,41,59,0.5); padding: 15px; border-radius: 10px; text-align: center; border: 1px solid #1e293b;'>
                <div style='color: #94a3b8; font-size: 0.9rem; margin-bottom: 5px;'>投資組合總損益</div>
                <div style='color: {'#ef4444' if total_pl>=0 else '#22c55e'}; font-size: 1.8rem; font-weight: bold; font-family: monospace;'>{total_pl_text}</div>
                <div style='color: {'#ef4444' if total_pl>=0 else '#22c55e'}; font-size: 0.9rem;'>{total_pl_pct_text}</div>
            </div>
            <div style='background-color: rgba(30,41,59,0.5); padding: 15px; border-radius: 10px; text-align: center; border: 1px solid #1e293b;'>
                <div style='color: #94a3b8; font-size: 0.9rem; margin-bottom: 5px;'>當前整體勝率</div>
                <div style='color: #facc15; font-size: 1.8rem; font-weight: bold; font-family: monospace;'>{win_rate_text}</div>
                <div style='color: #64748b; font-size: 0.9rem;'>(賺: {wins} / 已報價: {priced_orders})</div>
            </div>
            <div style='background-color: rgba(30,41,59,0.5); padding: 15px; border-radius: 10px; text-align: center; border: 1px solid #1e293b;'>
                <div style='color: #94a3b8; font-size: 0.9rem; margin-bottom: 5px;'>總投入市值</div>
                <div style='color: #e2e8f0; font-size: 1.8rem; font-weight: bold; font-family: monospace;'>{total_value_text}</div>
                <div style='color: #64748b; font-size: 0.9rem;'>(假設每檔 1 張)</div>
            </div>
        </div>
        """, unsafe_allow_html=True)
        if missing_quotes:
            st.warning(f"目前有 {missing_quotes} 筆模擬單無法取得行情，未納入市值、損益及勝率計算。")
        
        if order_metrics:
            df_m = pd.DataFrame(order_metrics)
            fig = go.Figure(data=[go.Bar(x=df_m['name'], y=df_m['pct'], marker_color=df_m['color'])])
            fig.update_layout(title="個股當前報酬率分佈 (%)", template="plotly_dark", height=300, margin=dict(l=10, r=10, t=40, b=10), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig, width="stretch", config={'displayModeBar': False})

        st.markdown("<h4 style='color: #818cf8; border-bottom: 1px solid #1e293b; padding-bottom: 10px; margin-top: 20px;'>📝 策略明細清單</h4>", unsafe_allow_html=True)
        
        for idx, order in enumerate(orders):
            order_ticker = normalize_ticker(order.get('ticker', ''))
            order_name = escape_html(order.get('name', order_ticker))
            order_time = escape_html(order.get('time', ''))
            order_pl = optional_num(order.get('pl_pct'))
            order_price = optional_num(order.get('curr_price'))
            buy_price = optional_num(order.get('buy_price'))
            highest_price = optional_num(order.get('highest_price'))
            order_rrr = safe_num(order.get('rrr'), 1.5)
            pl_col = "#94a3b8" if order_pl is None else ("#ef4444" if order_pl >= 0 else "#22c55e")
            order_price_text = "--" if order_price is None else f"{order_price:.1f}"
            order_pl_text = "報價不足" if order_pl is None else f"{'+' if order_pl > 0 else ''}{order_pl:.2f}%"
            buy_price_text = "--" if buy_price is None else f"{buy_price:.1f}"
            highest_price_text = "--" if highest_price is None else f"{highest_price:.1f}"
            with st.container(border=False):
                html = f"<div style='background-color: #0f172a; border: 1px solid #1e293b; border-radius: 12px; padding: 16px; margin-bottom: 14px;'><div style='display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 12px;'>"
                html += f"<a href='{build_stock_url(order_ticker)}' target='_self' style='text-decoration:none;'><div style='display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; margin-bottom: 4px;'><span style='color: #f8fafc; font-weight: bold; font-size: 1.25rem;'>{order_name}</span><span style='color: #64748b; font-family: monospace; font-size: 0.9rem;'>{escape_html(order_ticker)}</span></div><div style='font-size: 0.75rem; color: #64748b;'>下單時間: {order_time}</div></a>"
                html += f"<div style='text-align: right;'><div style='font-size: 0.8rem; color: #94a3b8; margin-bottom: 2px;'>最新現價 / 報酬率</div><div style='font-size: 1.3rem; font-weight: bold; font-family: monospace; color: {pl_col}; line-height: 1.1;'>{order_price_text}</div><div style='font-size: 0.85rem; font-weight: bold; font-family: monospace; color: {pl_col}; margin-top: 4px;'>{order_pl_text}</div></div></div>"
                html += f"<div style='display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; background-color: rgba(0,0,0,0.2); border: 1px solid rgba(255,255,255,0.05); padding: 10px; border-radius: 8px;'>"
                html += f"<div style='display: flex; flex-direction: column; align-items: center;'><span style='font-size: 0.7rem; color: #64748b; margin-bottom: 4px;'>買進成本</span><span style='font-size: 1rem; font-weight: bold; color: #e2e8f0; font-family: monospace;'>{buy_price_text}</span></div>"
                html += f"<div style='display: flex; flex-direction: column; align-items: center;'><span style='font-size: 0.7rem; color: #64748b; margin-bottom: 4px;'>創高紀錄</span><span style='font-size: 1rem; font-weight: bold; color: #facc15; font-family: monospace;'>{highest_price_text}</span></div>"
                html += f"<div style='display: flex; flex-direction: column; align-items: center;'><span style='font-size: 0.7rem; color: #64748b; margin-bottom: 4px;'>風報比參數</span><span style='font-size: 1rem; font-weight: bold; color: #34d399; font-family: monospace;'>1 : {order_rrr}</span></div></div>"
                st.markdown(html, unsafe_allow_html=True)
                if st.button(f"刪除此單 ({order.get('name', order_ticker)})", key=f"btn_del_{order.get('id', idx)}_{idx}"):
                    st.session_state.delete_order_id = order['id']; st.rerun()

# ==========================================
# 📝 手動真實交易日誌（與模擬單、Top10 假設成交分離）
# ==========================================
elif st.session_state.page == "trade_journal":
    st.title("📝 交易日誌分析師")
    st.caption("只分析你手動確認的實際交易及明確記錄的錯過機會；不把模擬單或 Top10 假設成交當成真實績效。")
    if st.button("回雷達總機", key="journal_home"):
        st.session_state.page = "home"
        st.rerun()

    journal_identity = {}
    try:
        journal_user = st.user
        if getattr(journal_user, "is_logged_in", False):
            journal_identity = {
                "sub": journal_user.get("sub", ""),
                "email": journal_user.get("email", ""),
            }
    except Exception:
        pass
    journal_authenticated = bool(journal_identity.get("sub") or journal_identity.get("email"))
    journal_document = (
        scoped_document_name("trade_journal", journal_identity, "")
        if journal_authenticated else None
    )
    journal_scope = journal_document or "anonymous_session"
    journal_storage_key = f"user_data:{journal_document}" if journal_document else ""
    if st.session_state.get("_trade_journal_scope") != journal_scope:
        if journal_document:
            try:
                journal_payload = load_cloud_doc("user_data", journal_document, raise_on_error=True)
                stored_records = journal_payload.get("data", []) if journal_payload else []
                if not isinstance(stored_records, list):
                    raise ValueError("雲端日誌格式不是清單")
                if len(stored_records) > 500:
                    raise ValueError("雲端日誌超過 500 筆安全上限")
                verified_records = [normalize_record(row) for row in stored_records]
                analyze_journal(verified_records)
                revision = int(journal_payload.get("revision", 0) or 0) if journal_payload else 0
                st.session_state._cloud_doc_cache[journal_storage_key] = {
                    "value": verified_records,
                    "ts": time.time(),
                    "revision": revision,
                }
            except Exception as exc:
                st.error(f"交易日誌讀取或格式驗證失敗，已停止新增以避免覆寫：{exc}")
                st.stop()
            st.session_state.trade_journal_records = verified_records
        else:
            st.session_state.trade_journal_records = []
        st.session_state._trade_journal_scope = journal_scope

    def save_journal_records(next_records):
        if len(next_records) > 500:
            st.error("日誌已達 500 筆上限；請先匯出備份並整理舊紀錄。")
            return False
        try:
            analyze_journal(next_records)
        except (TypeError, ValueError, OverflowError) as exc:
            st.error(f"日誌驗證失敗，原紀錄未更動：{exc}")
            return False
        if len(json.dumps(next_records, ensure_ascii=False).encode("utf-8")) > 800_000:
            st.error("日誌內容已接近雲端文件大小限制；請先匯出備份並精簡舊紀錄。")
            return False
        if journal_document:
            loaded_entry = st.session_state.get("_cloud_doc_cache", {}).get(journal_storage_key)
            if (
                not isinstance(loaded_entry, dict)
                or not isinstance(loaded_entry.get("revision"), int)
                or isinstance(loaded_entry.get("revision"), bool)
                or loaded_entry["revision"] < 0
            ):
                st.error("交易日誌版本資訊已失效；請先按『重新載入雲端日誌』，避免覆蓋其他分頁的紀錄。")
                return False
            if not save_cloud_data("user_data", journal_document, next_records):
                st.error("雲端寫入失敗，原紀錄未更動；請按『重新載入雲端日誌』後再試。")
                return False
        st.session_state.trade_journal_records = next_records
        return True

    def journal_optional_number(value):
        text = str(value or "").strip().replace(",", "")
        return None if not text else float(text)

    records = st.session_state.trade_journal_records
    if journal_document:
        st.success("已登入：交易日誌以個人範圍儲存在雲端，並檢查跨頁籤版本衝突。")
        if st.button("重新載入雲端日誌", key="journal_reload_cloud"):
            st.session_state.pop("_trade_journal_scope", None)
            st.session_state._cloud_doc_cache.pop(journal_storage_key, None)
            st.session_state._cloud_doc_cache.pop(f"user_data/{journal_document}:doc", None)
            st.rerun()
    else:
        st.warning("未登入：交易日誌只保留在目前工作階段。請用下方 JSON 匯出備份；登入後可匯入。")

    with st.expander("新增實際交易或錯過機會", expanded=not records):
        record_status = st.radio(
            "紀錄類型",
            ["closed", "open", "missed"],
            format_func=lambda value: {
                "closed": "已完成交易", "open": "持有中", "missed": "錯過機會",
            }[value],
            horizontal=True,
            key="journal_record_status",
        )
        with st.form("journal_new_record_form"):
            ticker_input = st.text_input("股票代號", placeholder="例如 2330")
            decision_date_value = st.date_input(
                "進場日／決策日", datetime.now(timezone(timedelta(hours=8))).date(),
            )
            if record_status != "missed":
                entry_col, shares_col = st.columns(2)
                with entry_col:
                    entry_price_value = st.number_input("實際進場價", min_value=0.0, value=0.0, step=0.1)
                with shares_col:
                    shares_value = st.number_input("實際股數", min_value=1, value=1, step=1)
            else:
                entry_price_value = None
                shares_value = None
            plan_col_a, plan_col_b = st.columns(2)
            with plan_col_a:
                planned_entry_low_value = st.text_input("原計畫入場下限（可留空）")
                planned_stop_value = st.text_input("原計畫停損價（可留空）")
            with plan_col_b:
                planned_entry_high_value = st.text_input("原計畫入場上限（可留空）")
                planned_target_value = st.text_input("原計畫目標價（可留空）")
            exit_date_value = None
            exit_price_value = None
            actual_costs_value = None
            observed_date_value = None
            observed_price_value = None
            if record_status == "closed":
                exit_col_a, exit_col_b = st.columns(2)
                with exit_col_a:
                    exit_date_value = st.date_input("實際出場日")
                    actual_costs_value = st.text_input("實付費稅合計（元；留空則淨損益未知）")
                with exit_col_b:
                    exit_price_value = st.number_input("實際出場價", min_value=0.0, value=0.0, step=0.1)
            elif record_status == "missed":
                observed_col_a, observed_col_b = st.columns(2)
                with observed_col_a:
                    observed_date_value = st.date_input("事後觀察日")
                with observed_col_b:
                    observed_price_value = st.number_input("事後觀察價", min_value=0.0, value=0.0, step=0.1)
                st.caption("觀察價只用來檢視後續價格變化，不當成實際可成交價或漏賺金額。")
            entry_reason_value = st.text_input("進場理由／原計畫依據（可留空）")
            exit_reason_value = st.text_input("出場理由（可留空）") if record_status == "closed" else ""
            missed_reason_value = st.text_input("當時未進場原因") if record_status == "missed" else ""
            emotion_value = st.selectbox(
                "當時自評的情緒／行為（可不標記）",
                ["", "怕錯過", "急於回本", "不願認賠", "怕獲利回吐", "臨時改單", "其他"],
                format_func=lambda value: value or "未標記",
            )
            notes_value = st.text_area("覆盤備註（可留空）", max_chars=1000)
            add_record = st.form_submit_button("儲存日誌", width="stretch")
        if add_record:
            code = normalize_ticker(ticker_input)
            if not code or code.startswith("^"):
                st.error("請輸入有效的個股代號。")
            else:
                try:
                    next_record = normalize_record({
                        "id": uuid.uuid4().hex,
                        "status": record_status,
                        "ticker": code,
                        "name": get_stock_name(code),
                        "entry_date": decision_date_value.isoformat() if record_status != "missed" else None,
                        "decision_date": decision_date_value.isoformat() if record_status == "missed" else None,
                        "entry_price": entry_price_value,
                        "shares": shares_value,
                        "exit_date": exit_date_value.isoformat() if exit_date_value else None,
                        "exit_price": exit_price_value,
                        "actual_costs": journal_optional_number(actual_costs_value),
                        "planned_entry_low": journal_optional_number(planned_entry_low_value),
                        "planned_entry_high": journal_optional_number(planned_entry_high_value),
                        "planned_stop": journal_optional_number(planned_stop_value),
                        "planned_target": journal_optional_number(planned_target_value),
                        "entry_reason": entry_reason_value,
                        "exit_reason": exit_reason_value,
                        "emotion": emotion_value,
                        "missed_reason": missed_reason_value,
                        "observed_date": observed_date_value.isoformat() if observed_date_value else None,
                        "observed_price": observed_price_value,
                        "notes": notes_value,
                    })
                except (TypeError, ValueError) as exc:
                    st.error(f"紀錄未儲存：{exc}")
                else:
                    if save_journal_records([next_record, *records]):
                        st.success("日誌已儲存。")
                        st.rerun()

    open_records = [row for row in records if row.get("status") == "open"]
    if open_records:
        with st.expander(f"補填出場結果（持有中 {len(open_records)} 筆）"):
            chosen_open_id = st.selectbox(
                "選擇持有紀錄",
                [row["id"] for row in open_records],
                format_func=lambda key: next(
                    f"{row['ticker']} {row['name']}｜{row['entry_date']} 進場"
                    for row in open_records if row["id"] == key
                ),
            )
            chosen_open = next(row for row in open_records if row["id"] == chosen_open_id)
            with st.form("journal_close_record_form"):
                close_col_a, close_col_b = st.columns(2)
                with close_col_a:
                    close_date = st.date_input("出場日", key="journal_close_date")
                    close_costs = st.text_input("實付買賣費稅合計（可留空）", key="journal_close_costs")
                with close_col_b:
                    close_price = st.number_input("實際出場價", min_value=0.0, value=0.0, step=0.1, key="journal_close_price")
                close_reason = st.text_input("出場理由", key="journal_close_reason")
                close_submit = st.form_submit_button("確認實際出場", width="stretch")
            if close_submit:
                try:
                    closed_record = normalize_record({
                        **chosen_open,
                        "status": "closed",
                        "exit_date": close_date.isoformat(),
                        "exit_price": close_price,
                        "actual_costs": journal_optional_number(close_costs),
                        "exit_reason": close_reason,
                    })
                except (TypeError, ValueError) as exc:
                    st.error(f"出場結果未儲存：{exc}")
                else:
                    next_records = [
                        closed_record if row["id"] == chosen_open_id else row
                        for row in records
                    ]
                    if save_journal_records(next_records):
                        st.success("實際出場結果已更新。")
                        st.rerun()

    analysis = analyze_journal(records, recent_limit=30)
    summary = analysis["summary"]
    st.subheader("最近交易檢視")
    st.caption("下列規律僅使用最近 30 筆手動日誌；整體統計與最近規律分開顯示。")
    metric_cols = st.columns(4)
    metric_cols[0].metric("日誌筆數", summary["total_records"])
    metric_cols[1].metric("已完成", summary["closed_count"])
    metric_cols[2].metric("持有中", summary["open_count"])
    metric_cols[3].metric("錯過機會", summary["missed_count"])
    if summary["closed_count"]:
        st.caption(f"實際已實現毛損益：NT${summary['realized_gross_pnl']:+,.0f}。")
        if summary["unknown_cost_count"]:
            st.info(
                f"{summary['unknown_cost_count']} 筆已完成交易未填實付費稅；"
                "不計入淨損益與淨勝率，避免把毛利誤當淨利。"
            )
        if summary["known_net_count"]:
            st.caption(
                f"已填費稅的 {summary['known_net_count']} 筆淨損益："
                f"NT${summary['known_net_pnl']:+,.0f}；"
                f"淨勝率 {summary['net_win_rate_pct']:.1f}%。"
            )

    st.subheader("反覆錯誤、錯失機會與可能偏誤")
    display_findings = [finding for finding in analysis["findings"] if finding["count"] > 0]
    if not display_findings:
        st.info("目前沒有足夠的可核對事件；先持續記錄原計畫、實際成交與決策原因。")
    for finding in display_findings:
        st.markdown(
            f"**{finding['label']}：{finding['count']} / {finding['denominator']} 筆**"
        )
        st.caption(finding["interpretation"])
        if finding.get("evidence"):
            st.caption(
                "依據：" + "、".join(
                    f"{item['ticker']}（{item['event_date']}）"
                    for item in finding["evidence"][:5]
                )
            )

    st.subheader("3 條執行規則（樣本足夠時個人化）")
    for number, rule in enumerate(analysis["rules"], start=1):
        badge = "個人紀錄" if rule["personalized"] else "基礎守則／樣本不足"
        st.markdown(f"**{number}. {rule['title']}**  ·  {badge}")
        st.write(rule["text"])
        st.caption(rule["basis"])
    st.caption("以上為紀律覆盤，不是心理診斷或獲利保證；價格事後上漲不等於當時一定能成交。")
    st.caption("5,000 元單筆風險使用台股費稅與停損滑價模型估計；跳空或流動性不足仍可能使實際損失超過估計。")

    recent_display_rows = []
    for item in analysis["rows"]:
        row = item["record"]
        metrics = item["metrics"]
        recent_display_rows.append({
            "日期": row.get("decision_date") or item["event_date"],
            "類型": {"closed": "已完成", "open": "持有中", "missed": "錯過機會"}[row["status"]],
            "股票": f"{row['ticker']} {row['name']}",
            "進場價": row.get("entry_price"),
            "出場價": row.get("exit_price"),
            "股數": row.get("shares"),
            "毛損益": metrics.get("gross_pnl"),
            "淨損益": metrics.get("net_pnl"),
            "當時原因": row.get("missed_reason") or row.get("entry_reason") or "--",
        })
    if recent_display_rows:
        st.dataframe(pd.DataFrame(recent_display_rows), hide_index=True, width="stretch")
    else:
        st.info("尚無真實交易日誌；先新增一筆已完成交易、持有中部位或明確錯過的機會。")

    st.download_button(
        "匯出個人日誌 JSON 備份",
        data=json.dumps(records, ensure_ascii=False, indent=2),
        file_name="trade-journal-backup.json",
        mime="application/json",
        disabled=not records,
    )
    with st.expander("匯入 JSON 備份或修正誤植"):
        uploaded_journal = st.file_uploader("選擇日誌備份", type=["json"], key="journal_import_file")
        if uploaded_journal is not None and st.button("驗證後合併備份", key="journal_import_button"):
            try:
                if uploaded_journal.size > 1_000_000:
                    raise ValueError("檔案超過 1 MB 上限")
                imported = json.loads(uploaded_journal.getvalue().decode("utf-8"))
                if not isinstance(imported, list) or len(imported) > 500:
                    raise ValueError("備份必須是不超過 500 筆的日誌清單")
                normalized_import = [normalize_record(row) for row in imported]
                imported_ids = [row["id"] for row in normalized_import]
                if len(imported_ids) != len(set(imported_ids)):
                    raise ValueError("備份含重複紀錄 ID")
                existing_ids = {row["id"] for row in records}
                merged = records + [row for row in normalized_import if row["id"] not in existing_ids]
                if len(merged) > 500:
                    raise ValueError("合併後超過 500 筆上限")
            except (UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
                st.error(f"備份未匯入：{exc}")
            else:
                if save_journal_records(merged):
                    st.success(f"已新增 {len(merged) - len(records)} 筆；相同 ID 已略過。")
                    st.rerun()
        if records:
            delete_id = st.selectbox(
                "選擇要刪除的誤植紀錄",
                [row["id"] for row in records],
                format_func=lambda key: next(
                    f"{row['ticker']} {row['name']}｜{row.get('decision_date') or row.get('entry_date')}｜{row['status']}"
                    for row in records if row["id"] == key
                ),
                key="journal_delete_id",
            )
            confirm_delete = st.checkbox("確認刪除此筆日誌", key="journal_confirm_delete")
            if st.button("刪除此筆", disabled=not confirm_delete, key="journal_delete_button"):
                if save_journal_records([row for row in records if row["id"] != delete_id]):
                    st.success("該筆日誌已刪除。")
                    st.rerun()

# ==========================================
# 🚀 進入單一個股解析頁面
# ==========================================
# 🏆 Top 10 自動追蹤績效
# ==========================================
elif st.session_state.page == "top10_tracking":
    st.markdown("<h2 style='text-align: center; color: #f59e0b; margin-bottom: 20px;'>🏆 Top 10 自動追蹤績效</h2>", unsafe_allow_html=True)
    if st.button("回雷達總機", width="stretch"):
        st.session_state.page = "home"; st.rerun()
    
    st.markdown("<div style='background-color:rgba(245,158,11,0.1); border:1px solid rgba(245,158,11,0.3); padding:15px; border-radius:10px; margin-bottom:20px;'><h4 style='color:#fbbf24; margin-top:0;'>🤖 自動結算機制</h4><p style='color:#cbd5e1; font-size:0.9rem; margin-bottom:0;'>新訊號不再用分析日收盤假設成交：只在<b>下一交易日</b>的真實 OHLC 觸及凍結進場區時成交，並沿用入榜時的策略停損與目標；未觸及即失效。舊版既有持倉仍沿用 +15%／-10%，同日雙觸及時保守先計停損。</p></div>", unsafe_allow_html=True)
    
    tracker_document = load_cloud_doc("market_data", "top10_tracker")
    tracker_data = tracker_document.get("data", tracker_document) if isinstance(tracker_document, dict) else {}
    try:
        positions = hydrate_manifest_items(
            tracker_data,
            collection_name="top10_tracker_chunks",
            ids_key="position_chunk_ids",
            legacy_key="positions",
        )
        st.session_state._last_complete_tracker_positions = positions
    except Exception as exc:
        logging.error("讀取分段追蹤部位失敗: %s", exc)
        positions = st.session_state.get("_last_complete_tracker_positions", [])
        st.error("追蹤部位資料不完整，暫時保留上一個完整版本；系統不會用部分資料計算績效。")
    positions = [position for position in positions if isinstance(position, dict)]

    def entry_backtest_text(record):
        """Format the immutable entry-day backtest without turning missing values into zero."""
        samples_value = optional_num(record.get("entry_backtest_samples"))
        win_rate_value = optional_num(record.get("entry_win_rate"))
        if samples_value is None:
            return "--", "資料缺失", "#94A3B8"
        sample_count = max(int(samples_value), 0)
        cred_text, cred_color = credibility_label(sample_count)
        win_rate_text = f"{win_rate_value:.1f}%" if sample_count > 0 and win_rate_value is not None else "--"
        return win_rate_text, f"{sample_count}｜{cred_text}", cred_color

    positions_by_id = {
        str(position.get("position_id", "")): position
        for position in positions
        if position.get("position_id")
    }

    def with_position_entry_backtest(record):
        """Use the position's saved entry snapshot for legacy daily rows that omitted it."""
        position = positions_by_id.get(str(record.get("position_id", "")), {})
        if not position:
            return record
        merged = dict(record)
        for key in (
            "entry_win_rate", "entry_backtest_samples", "entry_backtest_scope", "entry_backtest_status",
            "signal_date", "execution_schema", "entry_plan_type", "planned_entry_low",
            "planned_entry_high", "stop_price", "target_price", "signal_score",
            "signal_rank", "signal_change_pct", "signal_industry", "signal_rsi",
            "signal_bias", "signal_volume_ratio", "signal_conflict", "signal_pattern",
            "signal_confidence",
        ):
            if merged.get(key) is None and position.get(key) is not None:
                merged[key] = position.get(key)
        return merged

    latest_date = str(tracker_data.get("latest_date", "")) if isinstance(tracker_data, dict) else ""
    latest_snapshots = tracker_data.get("latest_snapshots", []) if isinstance(tracker_data, dict) else []
    history_dates = tracker_data.get("history_dates", []) if isinstance(tracker_data, dict) else []
    missing_ranking_dates = set(tracker_data.get("missing_ranking_dates", [])) if isinstance(tracker_data, dict) else set()
    partial_ranking_dates = set(tracker_data.get("partial_ranking_dates", [])) if isinstance(tracker_data, dict) else set()
    unverified_ranking_dates = set(tracker_data.get("unverified_ranking_dates", [])) if isinstance(tracker_data, dict) else set()
    available_dates = sorted({str(item) for item in history_dates if item} | ({latest_date} if latest_date else set()), reverse=True)

    def stored_ranking_status(date_text):
        if date_text in missing_ranking_dates:
            return "missing"
        if date_text in unverified_ranking_dates:
            return "unverified"
        if date_text in partial_ranking_dates:
            return "partial"
        return "ok"

    st.subheader("📅 每日追蹤明細")
    if not available_dates:
        st.info("目前尚無每日追蹤明細；下一次盤後掃描完成後會開始建立完整日誌。")
    else:
        selected_tracking_date = st.selectbox("追蹤交易日", available_dates, key="top10_tracking_date")
        if selected_tracking_date == latest_date:
            daily_records = latest_snapshots
            ranking_status = stored_ranking_status(selected_tracking_date)
        else:
            daily_payload = load_cloud_data("top10_tracking_history", selected_tracking_date, {})
            daily_records = daily_payload.get("records", []) if isinstance(daily_payload, dict) else []
            ranking_status = daily_payload.get("ranking_status", stored_ranking_status(selected_tracking_date)) if isinstance(daily_payload, dict) else "missing"
        daily_records = [row for row in daily_records if isinstance(row, dict)]
        if ranking_status != "ok":
            st.warning("此日期的原始 Top10 榜單缺失或僅能部分核對；持倉行情只使用真實 OHLC 續追，未用事後資料重算或假造當日排名。")
        if not daily_records:
            st.warning("此交易日沒有可顯示的追蹤明細。")
        else:
            action_labels = {
                "SIGNAL": "待次日觸價", "WAIT_ENTRY_SESSION": "等待預定交易日",
                "ENTRY": "區間成交", "ENTRY_EXPIRED": "進場訊號失效",
                "EXECUTION_UNRESOLVED": "當日價格順序不明",
                "EXECUTION_DATA_GAP": "持有期間行情缺漏（排除績效）",
                "HOLD": "持有", "TAKE_PROFIT": "停利", "STOP_LOSS": "停損",
                "DATA_MISSING": "行情缺漏", "EXIT": "已出場",
            }
            status_labels = {
                "PENDING": "等待次日觸價", "OPEN": "持有中", "EXPIRED": "進場訊號失效",
                "UNRESOLVED": "成交後結果不明（排除績效）",
                "EXCLUDED_UNRESOLVED": "當日價格順序不明（已終止並排除績效）",
                "EXCLUDED_DATA_GAP": "持有期間行情缺漏（已終止並排除績效）",
                "CLOSED_TP": "停利出場", "CLOSED_SL": "停損出場",
            }
            display_rows = []
            for row in daily_records:
                row = with_position_entry_backtest(row)
                entry_win_rate_text, entry_sample_text, _ = entry_backtest_text(row)
                display_rows.append({
                    "日期": row.get("date", selected_tracking_date),
                    "訊號日": row.get("signal_date"),
                    "排名": row.get("top10_rank"),
                    "代號": normalize_ticker(row.get("ticker", "")),
                    "名稱": str(row.get("name", "")),
                    "入榜分數": row.get("signal_score"),
                    "入榜日漲幅%": row.get("signal_change_pct"),
                    "入榜RSI": row.get("signal_rsi"),
                    "入榜乖離%": row.get("signal_bias"),
                    "入榜量比": row.get("signal_volume_ratio"),
                    "訊號衝突": row.get("signal_conflict") or "--",
                    "動作": action_labels.get(str(row.get("action", "")), str(row.get("action", ""))),
                    "狀態": status_labels.get(str(row.get("status", "")), str(row.get("status", ""))),
                    "開盤": row.get("open"),
                    "最高": row.get("high"),
                    "最低": row.get("low"),
                    "收盤": row.get("close"),
                    "追蹤價": row.get("mark_price"),
                    "凍結進場區": (
                        f"{row.get('planned_entry_low')}–{row.get('planned_entry_high')}"
                        if row.get("planned_entry_low") is not None and row.get("planned_entry_high") is not None
                        else "--"
                    ),
                    "策略停損": row.get("stop_price"),
                    "策略目標": row.get("target_price"),
                    "入榜技術勝率": entry_win_rate_text,
                    "入榜樣本/可信度": entry_sample_text,
                    "單日報酬%": row.get("daily_return_pct"),
                    "持有報酬%": row.get("pnl_pct"),
                    "期間最高": row.get("highest_price"),
                    "期間最低": row.get("lowest_price"),
                    "MFE%": row.get("mfe_pct"),
                    "MAE%": row.get("mae_pct"),
                    "跌幅觀察（非因果）": row.get("decline_diagnostic") or "--",
                    "估計淨損益": row.get("net_pnl_amount"),
                    "估計交易成本": row.get("estimated_transaction_cost"),
                    "大盤報酬%": row.get("benchmark_return_pct"),
                    "超額報酬%": row.get("excess_return_pct"),
                    "入榜產業": row.get("signal_industry") or "--",
                    "榜單狀態": "完整" if row.get("ranking_status", ranking_status) == "ok" else "原榜單缺失",
                    "資料狀態": {
                        "ok": "完整",
                        "unresolved": "OHLC 無法判定先後",
                        "execution_data_gap": "持有期間行情缺漏（績效排除）",
                    }.get(str(row.get("data_status") or ""), "缺漏"),
                })
            display_df = pd.DataFrame(display_rows).sort_values(
                by=["排名", "持有報酬%"], ascending=[True, False], na_position="last"
            )
            st.dataframe(display_df, hide_index=True, width="stretch")
            missing_count = sum(1 for row in daily_records if row.get("data_status") == "missing")
            if missing_count:
                st.warning(f"本日有 {missing_count} 筆行情不完整；系統保留前一追蹤價，不會用錯誤價格結算。")
            unresolved_count = sum(1 for row in daily_records if row.get("data_status") == "unresolved")
            if unresolved_count:
                st.info(f"本日有 {unresolved_count} 筆無法由日 K 判定成交後門檻先後，已排除績效且不延續假設持倉。")
            data_gap_count = sum(
                1 for row in daily_records
                if row.get("data_status") == "execution_data_gap"
                or row.get("status") == "EXCLUDED_DATA_GAP"
            )
            if data_gap_count:
                st.warning(
                    f"本日有 {data_gap_count} 筆持有期間行情缺漏已跨日未修復；"
                    "因無法確認缺漏日是否觸發停損或停利，已永久排除績效。"
                )
    
    pending_pos = [p for p in positions if p.get("status") == "PENDING"]
    open_pos = [p for p in positions if p.get("status") == "OPEN"]
    expired_pos = [p for p in positions if p.get("status") == "EXPIRED"]
    unresolved_pos = [
        p for p in positions
        if p.get("status") in {"UNRESOLVED", "EXCLUDED_UNRESOLVED"}
    ]
    data_gap_pos = [p for p in positions if p.get("status") == "EXCLUDED_DATA_GAP"]
    closed_pos = [p for p in positions if str(p.get("status") or "").startswith("CLOSED_")]

    def tracking_number_text(value, digits=1, suffix=""):
        number = optional_num(value)
        return "--" if number is None else f"{number:.{digits}f}{suffix}"

    def tracking_money_text(value):
        number = optional_num(value)
        return "--" if number is None else f"NT${number:+,.0f}"

    def tracking_shares_text(value):
        number = optional_num(value)
        return "--" if number is None or number <= 0 else f"{int(number):,}"

    if unresolved_pos:
        st.subheader("⚪ 當日成交後結果無法判定")
        st.warning(
            f"共 {len(unresolved_pos)} 筆：日 K 同時涵蓋進場與出場門檻，但無逐筆先後順序；"
            "系統不延續假設持倉，並永久排除於績效統計。"
        )

    if data_gap_pos:
        st.subheader("⚪ 持有期間行情缺漏")
        st.warning(
            f"共 {len(data_gap_pos)} 筆：缺漏交易日無完整 OHLC，無法確認是否曾觸發停損或停利；"
            "系統已終止追蹤並永久排除於績效統計。"
        )
        st.dataframe(
            pd.DataFrame([
                {
                    "代號": normalize_ticker(position.get("ticker", "")),
                    "名稱": position.get("name"),
                    "訊號日": position.get("signal_date"),
                    "進場日": position.get("entry_date"),
                    "缺漏日": position.get("execution_data_gap_date"),
                    "原因": position.get("resolution_reason") or "持有期間缺少完整 OHLC",
                }
                for position in data_gap_pos
            ]),
            hide_index=True,
            width="stretch",
        )
        st.dataframe(
            pd.DataFrame([
                {
                    "代號": normalize_ticker(position.get("ticker", "")),
                    "名稱": position.get("name"),
                    "訊號日": position.get("signal_date"),
                    "觸價日": position.get("entry_date"),
                    "進場價": position.get("entry_price"),
                    "原因": "日 K 無法判定目標價在進場前或後出現",
                }
                for position in unresolved_pos
            ]),
            hide_index=True,
            width="stretch",
        )

    st.subheader("🟡 等待次一交易日觸價")
    st.caption("訊號只保留到下一個有完整 OHLC 的交易日；價格觸及凍結進場區才建立持倉，否則列為失效。")
    if not pending_pos:
        st.info("目前沒有等待成交的進場訊號。")
    else:
        for p in sorted(pending_pos, key=lambda x: (str(x.get("signal_date", "")), int(safe_num(x.get("signal_rank"), 999)))):
            ticker_text = escape_html(normalize_ticker(p.get("ticker", "")))
            name_text = escape_html(p.get("name", ""))
            zone_text = f"{tracking_number_text(p.get('planned_entry_low'))}–{tracking_number_text(p.get('planned_entry_high'))}"
            st.markdown(
                f"<div style='background-color:#172033; padding:14px; border-radius:8px; margin-bottom:10px; border-left:4px solid #facc15;'>"
                f"<b style='color:#f8fafc;'>{ticker_text} {name_text}</b>"
                f"<span style='color:#94a3b8; margin-left:10px;'>訊號日 {escape_html(p.get('signal_date', ''))}</span>"
                f"<div style='color:#cbd5e1; margin-top:6px;'>凍結進場區 <b>{zone_text}</b> ｜ 停損 <b>{tracking_number_text(p.get('stop_price'))}</b> ｜ 目標 <b>{tracking_number_text(p.get('target_price'))}</b></div>"
                f"</div>",
                unsafe_allow_html=True,
            )

    st.subheader("⚪ 未成交／已失效訊號")
    if not expired_pos:
        st.info("目前沒有失效的進場訊號。")
    else:
        for p in sorted(expired_pos, key=lambda x: str(x.get("expire_date", "")), reverse=True):
            ticker_text = escape_html(normalize_ticker(p.get("ticker", "")))
            name_text = escape_html(p.get("name", ""))
            st.markdown(
                f"<div style='background-color:#0f172a; padding:12px; border-radius:8px; margin-bottom:8px; border:1px solid #334155;'>"
                f"<b style='color:#e2e8f0;'>{ticker_text} {name_text}</b>"
                f"<span style='color:#94a3b8; margin-left:10px;'>{escape_html(p.get('signal_date', ''))} 訊號 → {escape_html(p.get('expire_date', ''))} 失效</span>"
                f"<div style='color:#94a3b8; margin-top:5px;'>{escape_html(p.get('expire_reason', '下一交易日未成交'))}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )
    
    st.subheader("🟢 目前持有中 (未實現損益)")
    if not open_pos:
        st.info("目前沒有追蹤中的標的。")
    else:
        for p in sorted(open_pos, key=lambda x: optional_num(x.get("pnl_pct")) if optional_num(x.get("pnl_pct")) is not None else float("-inf"), reverse=True):
            pnl = optional_num(p.get("pnl_pct"))
            entry_win_rate_text, entry_sample_text, entry_cred_color = entry_backtest_text(p)
            ticker_text = escape_html(normalize_ticker(p.get('ticker', '')))
            name_text = escape_html(p.get('name', ''))
            color = "#94a3b8" if pnl is None else ("#ef4444" if pnl >= 0 else "#22c55e")
            pnl_text = "--" if pnl is None else f"{'+' if pnl > 0 else ''}{pnl:.1f}%"
            net_pnl_text = tracking_money_text(p.get("net_pnl_amount"))
            cost_text = tracking_money_text(p.get("estimated_transaction_cost"))
            diagnostic_text = escape_html(p.get("last_snapshot", {}).get("decline_diagnostic", "") or "--")
            st.markdown(f"<div style='background-color:#1e293b; padding:15px; border-radius:8px; margin-bottom:10px; border-left:4px solid {color};'>"
                        f"<div style='display:flex; justify-content:space-between;'>"
                        f"<div><span style='font-size:1.1rem; font-weight:bold; color:#f8fafc;'>{ticker_text} {name_text}</span>"
                        f"<span style='color:#94a3b8; font-size:0.8rem; margin-left:10px;'>進場: {escape_html(p.get('entry_date', ''))}</span></div>"
                        f"<div style='color:{color}; font-size:1.1rem; font-weight:bold;'>{pnl_text}</div>"
                        f"</div>"
                        f"<div style='color:#cbd5e1; font-size:0.85rem; margin-top:5px;'>"
                        f"進場價: <b>{tracking_number_text(p.get('entry_price'))}</b> ｜ 目前價: <b>{tracking_number_text(p.get('current_price'))}</b> ｜ 期間最高: <b>{tracking_number_text(p.get('highest_price'))}</b>"
                        f"</div><div style='color:#cbd5e1; font-size:0.82rem; margin-top:5px;'>"
                        f"{tracking_shares_text(p.get('shares'))} 股 ｜ 停損 <b>{tracking_number_text(p.get('stop_price'))}</b> ｜ 目標 <b>{tracking_number_text(p.get('target_price'))}</b> ｜ 估計淨損益 <b>{net_pnl_text}</b>（成本 {cost_text}）"
                        f"</div><div style='color:#94a3b8; font-size:0.8rem; margin-top:5px;'>"
                        f"入榜技術勝率: <b>{escape_html(entry_win_rate_text)}</b> ｜ 樣本/可信度: <b style='color:{entry_cred_color};'>{escape_html(entry_sample_text)}</b>"
                        f" ｜ 跌幅觀察（非因果）: <b>{diagnostic_text}</b>"
                        f"</div></div>", unsafe_allow_html=True)
                        
    st.subheader("🏁 歷史結算 (已出場)")
    if not closed_pos:
        st.info("目前尚無已結算的歷史紀錄。")
    else:
        valid_closed = [(p, optional_num(p.get("pnl_pct"))) for p in closed_pos]
        valid_closed = [(p, pnl) for p, pnl in valid_closed if pnl is not None]
        wins = sum(1 for p, pnl in valid_closed if p.get("status") == "CLOSED_TP" or pnl > 0)
        win_rate = (wins / len(valid_closed)) * 100 if valid_closed else None
        avg_pnl = sum(pnl for _, pnl in valid_closed) / len(valid_closed) if valid_closed else None
        win_rate_text = "--" if win_rate is None else f"{win_rate:.1f}%"
        avg_pnl_text = "--" if avg_pnl is None else f"{'+' if avg_pnl > 0 else ''}{avg_pnl:.2f}%"
        avg_color = "#94a3b8" if avg_pnl is None else ("#ef4444" if avg_pnl >= 0 else "#22c55e")
        
        st.markdown(f"<div style='display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-bottom:15px;'>"
                    f"<div style='background-color:#0f172a; padding:15px; border-radius:8px; text-align:center;'><div style='color:#94a3b8;'>總勝率</div><div style='color:#38bdf8; font-size:1.5rem; font-weight:bold;'>{win_rate_text}</div></div>"
                    f"<div style='background-color:#0f172a; padding:15px; border-radius:8px; text-align:center;'><div style='color:#94a3b8;'>平均結算報酬</div><div style='color:{avg_color}; font-size:1.5rem; font-weight:bold;'>{avg_pnl_text}</div></div>"
                    f"</div>", unsafe_allow_html=True)
                    
        for p in sorted(closed_pos, key=lambda x: str(x.get("close_date", "")), reverse=True):
            pnl = optional_num(p.get("pnl_pct"))
            entry_win_rate_text, entry_sample_text, entry_cred_color = entry_backtest_text(p)
            ticker_text = escape_html(normalize_ticker(p.get('ticker', '')))
            name_text = escape_html(p.get('name', ''))
            color = "#94a3b8" if pnl is None else ("#ef4444" if pnl >= 0 else "#22c55e")
            pnl_text = "--" if pnl is None else f"{'+' if pnl > 0 else ''}{pnl:.1f}%"
            net_pnl_text = tracking_money_text(p.get("net_pnl_amount"))
            cost_text = tracking_money_text(p.get("estimated_transaction_cost"))
            diagnostic_text = escape_html(p.get("last_snapshot", {}).get("decline_diagnostic", "") or "--")
            status_text = "🎯 停利出場" if p.get("status") == "CLOSED_TP" else "🛑 停損出場"
            st.markdown(f"<div style='background-color:#0f172a; padding:15px; border-radius:8px; margin-bottom:10px; border:1px solid #1e293b; opacity:0.8;'>"
                        f"<div style='display:flex; justify-content:space-between;'>"
                        f"<div><span style='font-size:1rem; font-weight:bold; color:#f8fafc;'>{ticker_text} {name_text}</span> "
                        f"<span style='color:{color}; font-size:0.8rem; border:1px solid {color}; padding:2px 6px; border-radius:4px; margin-left:8px;'>{status_text}</span></div>"
                        f"<div style='color:{color}; font-size:1.1rem; font-weight:bold;'>{pnl_text}</div>"
                        f"</div>"
                        f"<div style='color:#64748b; font-size:0.8rem; margin-top:5px;'>"
                        f"{escape_html(p.get('entry_date', ''))} 進場 ({tracking_number_text(p.get('entry_price'))}) ➔ {escape_html(p.get('close_date', ''))} 出場 ({tracking_number_text(p.get('close_price'))})"
                        f" ｜ {tracking_shares_text(p.get('shares'))} 股 ｜ 估計淨損益 {net_pnl_text}（成本 {cost_text}）"
                        f"</div><div style='color:#64748b; font-size:0.8rem; margin-top:5px;'>"
                        f"入榜技術勝率: <b>{escape_html(entry_win_rate_text)}</b> ｜ 樣本/可信度: <b style='color:{entry_cred_color};'>{escape_html(entry_sample_text)}</b>"
                        f" ｜ 跌幅觀察（非因果）: <b>{diagnostic_text}</b>"
                        f"</div></div>", unsafe_allow_html=True)

elif st.session_state.page == "analysis":
    target = normalize_ticker(st.session_state.current_stock)
    st.session_state.current_stock = target
    c_name = get_stock_name(target)

    if not st.session_state.get('nav_pool_data'):
        restore_nav_pool()
    n_pool = st.session_state.get('nav_pool', [])
    p_stk = n_pool[n_pool.index(target) - 1] if target in n_pool and n_pool.index(target) > 0 else None
    n_stk = n_pool[n_pool.index(target) + 1] if target in n_pool and n_pool.index(target) < len(n_pool) - 1 else None

    c1, c2, c3 = st.columns([1, 1, 1])
    with c1:
        if p_stk and st.button(f"上一檔", width="stretch"): st.session_state.update({"current_stock": p_stk}); st.rerun()
    with c2:
        if st.button("回雷達總機", width="stretch"):
            st.query_params.clear()
            st.session_state.page = "home"
            st.rerun()
    with c3:
        if n_stk and st.button(f"下一檔", width="stretch"): st.session_state.update({"current_stock": n_stk}); st.rerun()

    chart_days_param = str(st.query_params.get("days", st.session_state.view_days))
    if chart_days_param in ("30", "60", "90"):
        st.session_state.view_days = int(chart_days_param)
    def chart_flag(name, default=True):
        raw_value = str(st.query_params.get(name, "1" if default else "0")).lower()
        return raw_value not in ("0", "false", "off", "no")

    analysis_target_date = safe_iso_date(st.session_state.get("target_date", ""))
    current_list = st.session_state.get('nav_pool_data', []) or []
    cached_list = st.session_state.get('scan_results', [])
    cached_doc = next((x for x in current_list if normalize_ticker(x.get('代號', '')) == target), None)
    if cached_doc is None:
        cached_doc = next((x for x in cached_list if normalize_ticker(x.get('代號', '')) == target), None)
    if analysis_target_date and st.session_state.get("scan_date") != analysis_target_date:
        cached_doc = None
    query_mode = safe_mode(st.query_params.get('mode', ''))
    requested_analysis_intraday = query_mode in ("intraday", "realtime")
    live_requested = bool(
        requested_analysis_intraday
        or st.session_state.get('is_intraday', False)
        or is_realtime_score_record(cached_doc)
    )
    _, _, is_intra = resolve_score_mode(live_requested)
    if analysis_target_date:
        is_intra = False
    st.session_state.is_intraday = is_intra
    if is_intra:
        st.session_state.score_mode_label = "盤中參考分數"

    analysis_live_quote = None
    if is_intra:
        analysis_live_quote = get_analysis_live_quote(
            target,
            str((cached_doc or {}).get("Revenue_Source") or ""),
            str((cached_doc or {}).get("Institutional_Source") or ""),
        )
    df_chart = get_stock_data(
        target,
        target_date=analysis_target_date or None,
        intraday_quote=analysis_live_quote,
    )
    if df_chart is not None and len(df_chart) >= 20:
        df_slice = df_chart.iloc[:len(df_chart) + st.session_state.date_offset] if st.session_state.date_offset < 0 else df_chart
        # Enforce the history requirement before consulting any cached score.
        # Otherwise a 20–59 bar frame could reuse an older realtime snapshot
        # and continue into charts/backtests whose 60MA inputs are incomplete.
        if len(df_slice) < MIN_ANALYSIS_BARS:
            require_analysis_result(None, target, df_slice)
        force_key = f"force_analysis_refresh_{target}"
        force_analysis_refresh = st.session_state.pop(force_key, False)
        analysis_context = analysis_target_date or ("realtime" if is_intra else "latest")
        cached_analysis = None if force_analysis_refresh else load_analysis_cache(
            target,
            LIVE_SCORE_CACHE_SECONDS if is_intra else POST_ANALYSIS_CACHE_SECONDS,
            context=analysis_context,
        )

        if cached_analysis and not analysis_target_date:
            cached_fund = cached_analysis.get("fund", {})
            cached_inst_data = cached_analysis.get("inst_data", [])
            repaired_fund, repaired_inst_data, repaired = repair_cached_institutional_data(
                target, cached_fund, cached_inst_data, cached_doc=cached_doc
            )
            cached_analysis = dict(cached_analysis)
            cached_analysis["fund"] = repaired_fund
            cached_analysis["inst_data"] = repaired_inst_data
            if repaired:
                save_analysis_cache(target, cached_analysis, context=analysis_context)

        cached_data = cached_analysis.get("data") if cached_analysis else None
        has_fresh_live_quote = bool(
            is_intra
            and analysis_live_quote
            and df_chart.attrs.get("intraday_quote_status") == "realtime"
        )
        if has_fresh_live_quote:
            if cached_analysis and cached_analysis.get("fund"):
                f_data = cached_analysis.get("fund", {})
                inst_data = cached_analysis.get("inst_data", [])
            else:
                f_data = support_data_from_postclose_record(
                    cached_doc,
                    current_price=float(df_slice['Close'].iloc[-1]),
                )
                inst_data = institutional_rows_from_record(cached_doc)
            data = require_analysis_result(
                analyze_today(
                    df_slice,
                    target,
                    inst_data,
                    is_light_mode,
                    f_data,
                    cached_doc=cached_doc,
                    is_intraday=True,
                ),
                target,
                df_slice,
            )
            data["Score_Source"] = "解析頁盤中即時重算"
            data["Intraday_Quote_Source"] = analysis_live_quote.get("source", "")
            data["Intraday_Quote_Time"] = analysis_live_quote.get("quote_time", "")
            data["Intraday_Quote_Freshness"] = analysis_live_quote.get("freshness", "")
            data["Intraday_Quote_Status"] = "realtime"
            save_analysis_cache(
                target,
                {"data": data, "fund": f_data, "inst_data": inst_data},
                context=analysis_context,
            )
        elif is_intra:
            if isinstance(cached_data, dict) and is_realtime_score_record(cached_data):
                inst_data = cached_analysis.get("inst_data", [])
                f_data = cached_analysis.get("fund", {})
                data = dict(cached_data)
                data["Score_Source"] = "最新報價暫缺，沿用 30 秒內盤中快照"
            elif is_realtime_score_record(cached_doc):
                data = dict(cached_doc)
                f_data = support_data_from_postclose_record(
                    cached_doc,
                    current_price=float(data.get("收盤價") or df_slice['Close'].iloc[-1]),
                )
                inst_data = institutional_rows_from_record(cached_doc)
                data["Score_Source"] = "最新報價暫缺，沿用名單盤中快照"
            else:
                f_data = support_data_from_postclose_record(
                    cached_doc,
                    current_price=float(df_slice['Close'].iloc[-1]),
                )
                inst_data = institutional_rows_from_record(cached_doc)
                data = require_analysis_result(
                    analyze_today(
                        df_slice,
                        target,
                        inst_data,
                        is_light_mode,
                        f_data,
                        cached_doc=cached_doc,
                        is_intraday=False,
                    ),
                    target,
                    df_slice,
                )
                data["Score_Source"] = "盤中報價暫缺，沿用盤後行情"
            data["Intraday_Quote_Status"] = "stale"
        else:
            if cached_analysis:
                inst_data = cached_analysis.get("inst_data", [])
                f_data = cached_analysis.get("fund", {"Industry": (cached_doc or {}).get('產業', '一般產業')})
            elif analysis_target_date:
                source = cached_doc or {}
                eps_value = source.get("EPS", "無")
                eps_period = source.get("EPS_Period", "missing")
                pe_value = "無"
                try:
                    if float(eps_value) > 0:
                        pe_value = f"{float(df_slice['Close'].iloc[-1]) / float(eps_value):.2f}"
                except (TypeError, ValueError, ZeroDivisionError):
                    pass
                revenue_available = source.get("MoM") is not None or source.get("YoY") is not None
                f_data = {
                    "Industry": source.get("產業", "一般產業"),
                    "EPS": eps_value,
                    "EPS_Period": eps_period,
                    "PE": pe_value,
                    "MoM": source.get("MoM"),
                    "YoY": source.get("YoY"),
                    "Revenue_Period": source.get("Revenue_Period", ""),
                    "Revenue_Source": source.get("Revenue_Source", ""),
                    "Financial_Period": source.get("Financial_Period", ""),
                    "Financial_Source": source.get("Financial_Source", ""),
                    "Financial_Status": source.get("Financial_Status", "historical_unavailable"),
                    "Financial_Operating_Income": source.get("Financial_Operating_Income"),
                    "Financial_Net_Income": source.get("Financial_Net_Income"),
                    "Financial_Operating_Margin": source.get("Financial_Operating_Margin"),
                    "Financial_Net_Margin": source.get("Financial_Net_Margin"),
                    "Financial_Debt_Ratio": source.get("Financial_Debt_Ratio"),
                    "Financial_Current_Ratio": source.get("Financial_Current_Ratio"),
                    "Financial_Risk_Level": source.get("Financial_Risk_Level", "unknown"),
                    "Financial_Risk_Flags": source.get("Financial_Risk_Flags", []),
                    "_institutional_status": "historical_unavailable",
                    "Institutional_Source": "",
                    "_status": "ok" if eps_value not in (None, "", "無", "0") else "missing",
                    "_data_status": {
                        "revenue": "ok" if revenue_available else "missing",
                        "financial": source.get("Financial_Status", "historical_unavailable"),
                    },
                }
                inst_data = []
            else:
                f_data, inst_data = get_analysis_support_data(
                    target, df_slice['Close'].iloc[-1], cached_doc=cached_doc
                )
            data = require_analysis_result(
                analyze_today(
                    df_slice,
                    target,
                    inst_data,
                    is_light_mode,
                    f_data,
                    cached_doc=cached_doc,
                    is_intraday=False,
                    historical_date=analysis_target_date,
                ),
                target,
                df_slice,
            )
            if not cached_analysis:
                save_analysis_cache(
                    target,
                    {"data": data, "fund": f_data, "inst_data": inst_data},
                    context=analysis_context,
                )

        analysis_price_date = latest_trading_date(df_slice.index)
        cached_score_date = safe_iso_date(
            cached_doc.get("Data_Date") if cached_doc else ""
        ) or safe_iso_date(st.session_state.get("scan_date", ""))
        use_cached_list_score = bool(
            cached_doc
            and not force_analysis_refresh
            and not is_intra
            and cached_score_date
            and cached_score_date == analysis_price_date
        )
        if use_cached_list_score:
            for k in ["Score", "評級", "Reasons", "Feature", "WinRate", "Backtest_Samples", "Validation_WinRate", "Validation_Samples", "Backtest_Scope", "Model_Confidence", "Model_Confidence_Label", "Score_Mode", "Score_Mode_Raw", "Whale_Net", "Whale_Net_Days", "Institutional_Sell_Streak", "Foreign_Net", "Trust_Net", "Institutional_Days", "Institutional_Status", "Institutional_Source", "Confidence", "Data_Completeness", "Financial_Period", "Financial_Source", "Financial_Status", "Financial_Operating_Income", "Financial_Net_Income", "Financial_Operating_Margin", "Financial_Net_Margin", "Financial_Debt_Ratio", "Financial_Current_Ratio", "Financial_Risk_Level", "Financial_Risk_Flags"]:
                if k in cached_doc:
                    data[k] = cached_doc[k]
            if "Score_Mode" not in data:
                data["Score_Mode"] = st.session_state.get("score_mode_label", "盤後正式分數")
        elif cached_doc and not is_intra and cached_score_date and cached_score_date != analysis_price_date:
            data["Score_Source"] = f"依 {analysis_price_date} 行情重算（未混用 {cached_score_date} 舊榜單分數）"

        sc = data['Score']
        data['評級'] = decision_label(sc)
        
        display_time = get_stock_data_time(df_slice, is_intraday=is_intra)
        quote_source = str(data.get("Intraday_Quote_Source") or "")
        quote_time = str(data.get("Intraday_Quote_Time") or "")
        quote_freshness = str(data.get("Intraday_Quote_Freshness") or "")
        if is_intra and quote_time:
            quote_meta = "｜".join(part for part in (quote_freshness, quote_source) if part)
            display_time = f"{quote_time}（{quote_meta or '盤中行情'}）"
        strategy_text = build_strategy_text(data)
            
        t_date = analysis_target_date
        if t_date:
            st.warning(f"📌 **歷史快照模式**：您正在檢視 `{t_date}` 雷達掃描當下的技術型態與分數，此頁面已暫停即時更新。要查看即時資料，請點擊上方「回雷達總機」重新搜尋，或返回首頁。")
            
        render_stock_hero(data, target, c_name, strategy_text)
        score_source_text = f"　｜　來源：<b>{escape_html(data.get('Score_Source'))}</b>" if data.get("Score_Source") else ""
        refresh_state_text = "每 30 秒自動更新" if is_intra and auto_refresh else ("自動更新未開啟" if is_intra else "盤後模式")
        st.markdown(f"<div style='text-align: center; color: #888; font-size: 0.9rem; margin-bottom: 10px;'>🗓️ 行情資料時間: {escape_html(display_time)}　｜　採用：<b>{escape_html(data.get('Score_Mode', '盤後正式分數'))}</b>　｜　{escape_html(refresh_state_text)}{score_source_text}</div>", unsafe_allow_html=True)
        if is_intra and data.get("Intraday_Quote_Status") != "realtime":
            st.warning("最新盤中報價暫時無法取得，目前明確沿用最近一次盤中快照或盤後行情，不會把舊價標示為即時。")
        
        _, up_c, _ = st.columns([1, 2, 1])
        refresh_label = "重新載入歷史快照" if analysis_target_date else "更新個股即時數值"
        force_refresh_analysis = up_c.button(refresh_label, width="stretch")
        if force_refresh_analysis:
            st.session_state[force_key] = True
            get_analysis_live_quote.clear()
            get_stock_data.clear()
            analyze_today.clear()
            if analysis_target_date:
                _get_ohlcv_base.clear()
            st.rerun()
        st.markdown("---")
        
        st.markdown("##### 策略回測實驗室")
        
        # 建立控制欄位讓使用者調整參數
        col_bt1, col_bt2, col_bt3 = st.columns(3)
        with col_bt1:
            atr_target_mult = st.slider("停利 ATR 倍數", min_value=0.5, max_value=5.0, value=1.5, step=0.1, key="bt_target_mult")
        with col_bt2:
            atr_stop_mult = st.slider("停損 ATR 倍數", min_value=0.5, max_value=3.0, value=1.0, step=0.1, key="bt_stop_mult")
        with col_bt3:
            score_thresh = st.slider("開倉分數門檻", min_value=40, max_value=80, value=65, step=5, key="bt_score_thresh")
            
        dynamic_rrr = round(atr_target_mult / atr_stop_mult, 2) if atr_stop_mult > 0 else 0.0

        # 優化選項
        col_opt1, col_opt2, col_opt3 = st.columns(3)
        with col_opt1:
            enable_trailing = st.checkbox("啟用移動止損 (Trailing Stop)", value=False, key="bt_enable_trailing")
        with col_opt2:
            filter_low_conf = st.checkbox("過濾低資料完整度 (< 60%)", value=False, key="bt_filter_low_conf")
        with col_opt3:
            filter_high_conflict = st.checkbox("過濾高多空衝突", value=False, key="bt_filter_high_conflict")

        backtest_df = df_slice.tail(BACKTEST_LOOKBACK_DAYS)
        win_rate, closed_signals, wins, buy_dates, backtest_stats = calculate_historical_winrate_interactive(
            df_slice, 
            atr_target_mult, 
            atr_stop_mult,
            score_threshold=score_thresh,
            enable_trailing=enable_trailing,
            filter_low_conf=filter_low_conf,
            filter_high_conflict=filter_high_conflict
        )
        validation_win_rate = safe_num(backtest_stats.get("validation_win_rate"), 0)
        validation_samples = int(safe_num(backtest_stats.get("validation_samples"), 0))
        
        # 只有當所有回測設定均為系統預設時，才採用快取的歷史勝率以加速讀取
        is_default_backtest = (
            atr_target_mult == 1.5 and 
            atr_stop_mult == 1.0 and 
            score_thresh == 65 and
            not enable_trailing and 
            not filter_low_conf and 
            not filter_high_conflict
        )
        if use_cached_list_score and is_default_backtest:
            win_rate = safe_num(cached_doc.get("WinRate"), win_rate)
            closed_signals = int(safe_num(cached_doc.get("Backtest_Samples"), closed_signals))
            validation_win_rate = safe_num(cached_doc.get("Validation_WinRate"), validation_win_rate)
            validation_samples = int(safe_num(cached_doc.get("Validation_Samples"), validation_samples))
        if is_intra:
            data['WinRate'] = win_rate
            data['Backtest_Samples'] = closed_signals
            for row in st.session_state.get('nav_pool_data', []) or []:
                if normalize_ticker(row.get('代號', '')) == target:
                    for k in ["Score", "評級", "Reasons", "Feature", "WinRate", "Backtest_Samples", "Validation_WinRate", "Validation_Samples", "Backtest_Scope", "Model_Confidence", "Model_Confidence_Label", "Score_Mode", "Score_Mode_Raw", "Whale_Net", "Whale_Net_Days", "Institutional_Sell_Streak", "Foreign_Net", "Trust_Net", "Institutional_Days", "Institutional_Status", "Institutional_Source", "Confidence", "Data_Completeness", "Financial_Period", "Financial_Source", "Financial_Status", "Financial_Operating_Income", "Financial_Net_Income", "Financial_Operating_Margin", "Financial_Net_Margin", "Financial_Debt_Ratio", "Financial_Current_Ratio", "Financial_Risk_Level", "Financial_Risk_Flags", "Score_Source", "收盤價", "開盤價", "最高價", "最低價", "漲跌", "漲跌幅", "Est_Vol_Ratio", "Volume_Confirmed", "Entry_Status", "Entry_Status_Group", "Entry_Ready", "Entry_Reason", "Entry_Low", "Entry_High", "Entry_Stop", "Entry_Target", "No_Chase_Price", "Intraday_Quote_Source", "Intraday_Quote_Time", "Intraday_Quote_Freshness", "Intraday_Quote_Status"]:
                        if k in data:
                            row[k] = data[k]
                    break
        
        curr_atr = float(df_slice['ATR'].iloc[-1])
        data['ATR_Target'] = round(data['收盤價'] + (curr_atr * atr_target_mult), 1)
        data['ATR_Stop'] = round(data['收盤價'] - (curr_atr * atr_stop_mult), 1)
        data['RRR'] = dynamic_rrr
        render_metric_grid([
            {"label": "量化分數", "value": f"{sc}", "sub": data.get("評級", "").replace("🟢 ", "").replace("🟡 ", "").replace("⚪ ", ""), "color": "#EF4444" if sc >= 60 else "#FACC15"},
            {"label": "技術面勝率", "value": f"{win_rate:.1f}%" if closed_signals > 0 else "--", "sub": f"全期 {closed_signals} 筆", "color": "#EF4444" if closed_signals > 0 and win_rate >= 60 else "#FACC15"},
            {"label": "近期驗證", "value": f"{validation_win_rate:.1f}%" if validation_samples > 0 else "--", "sub": f"末 30%｜{validation_samples} 筆", "color": "#EF4444" if validation_samples > 0 and validation_win_rate >= 60 else "#FACC15"},
            {"label": "風報比", "value": f"1 : {dynamic_rrr}", "sub": f"停損 {atr_stop_mult}x / 停利 {atr_target_mult}x", "color": "#60A5FA"},
        ])
        st.caption(f"回測口徑：{BACKTEST_SCOPE}。成本假設含買賣手續費各 0.1425%、賣出證交稅 0.3%、每筆最低手續費 20 元及雙向滑價各 0.05%。反覆依同一段資料調參會使近期驗證失去樣本外意義。")
        v_c = "#22c55e" if sc < 45 else ("#facc15" if sc < 60 else "#ef4444")
        v_t = escape_html(str(data['評級']).replace('🟢 ', '').replace('🟡 ', '').replace('⚪ ', ''))
        confidence = safe_num(data.get("Data_Completeness", data.get("Confidence")), 0)
        analysis_sections = generate_comprehensive_analysis_sections(
            data, inst_data, sc, f_data, is_light_mode
        )
        st.markdown(f"""
        <div style="border: 2px solid {v_c}; border-radius: 10px; padding: 20px; margin-bottom: 20px; background-color: #0b1120;">
            <h3 style="text-align: center; color: {v_c}; margin-top: 0; font-size: 1.8rem; margin-bottom: 8px;">100 分規則型量化決策：{v_t} ({sc}分)</h3>
            <div style="text-align:center; color:#94a3b8; font-weight:700; margin-bottom:16px;">資料完整度：{confidence}%｜模型可信度：{escape_html(data.get('Model_Confidence_Label', '資料不足'))}｜口徑：{escape_html(data.get('Score_Mode', '盤後正式分數'))}</div>
            <div style="background-color: rgba(30,41,59,0.5); padding: 15px; border-radius: 8px; border-left: 5px solid {v_c}; margin-bottom:20px;">
                <p style="font-size: 1.05rem; color: #f8fafc; margin: 0; line-height: 1.6;">
                    ✅ <b>自訂策略執行規劃</b><br>合理停利目標：<b style='color:#ef4444;'>{data['ATR_Target']}</b> 元<br>嚴格停損防守：<b style='color:#22c55e;'>{data['ATR_Stop']}</b> 元
                </p>
            </div>
        </div>""", unsafe_allow_html=True)
        for section_name in ("technical", "institutional", "fundamental"):
            st.markdown(analysis_sections[section_name], unsafe_allow_html=True)

        st.markdown("---")
        st.markdown("### 🧮 資金控管與零股計算器")
        c1_c, c2_c, c3_c = st.columns(3)
        with c1_c: max_loss = st.selectbox("單筆最高可接受虧損 (元)", [5000, 10000, 15000, 20000, 30000])
        with c2_c: stop_loss_price = st.number_input("設定停損價格 (預設套用上方ATR防守線)", value=float(data['ATR_Stop']), step=0.1)
        
        risk_per_share = data['收盤價'] - stop_loss_price
        if risk_per_share > 0:
            suggested_shares = int(max_loss / risk_per_share)
            with c3_c: st.markdown(f"<div style='background:rgba(239,68,68,0.1); padding:10px; border-radius:8px; text-align:center;'><span style='font-size:0.8rem; color:#ef4444;'>嚴守紀律！建議最高買進股數</span><br><span style='font-size:1.8rem; font-weight:bold; color:#ef4444;'>{suggested_shares} 股</span></div>", unsafe_allow_html=True)
        else:
            with c3_c: st.warning("停損價必須低於現價")
        st.markdown("---")
        
        if st.button("將此自訂策略加入模擬交易", width="stretch"):
            new_order = {
                "id": str(int(time.time())), "ticker": target, "name": c_name, "buy_price": data['收盤價'],
                "highest_price": data['收盤價'], "target_price": data['ATR_Target'], "stop_price": data['ATR_Stop'],
                "rrr": data['RRR'], "time": datetime.now(timezone(timedelta(hours=8))).strftime('%Y/%m/%d %H:%M:%S')
            }
            st.session_state.simulated_orders.insert(0, new_order)
            saved = save_cloud_data("user_data", USER_ORDERS_DOC, st.session_state.simulated_orders)
            if saved:
                st.success(f"✅ 已將風報比 1:{data['RRR']} 的策略單寫入資料庫！")
                st.balloons()
            else:
                st.warning("策略單已加入目前工作階段，但雲端寫入失敗，重新整理後可能遺失。")
        
        render_analysis_k_chart(
            df_slice,
            data['收盤價'],
            buy_dates,
            is_light_mode,
            target,
            initial_days=st.session_state.view_days,
            initial_show_buy=chart_flag("show_buy", True),
            initial_show_sup=chart_flag("show_sup", True),
            initial_show_signals=chart_flag("show_signals", True),
        )
        
        with st.expander("📖 點擊展開：圖表符號與線段對照說明", expanded=False):
            st.markdown("""
            **【線段與區域】**
            * 🟨 **黃線 (5T) / 🟩 綠線 (10T) / 🟦 藍線 (20T)**：短中期移動平均線。
            * 🟪 **紫色虛線**：依成交量分布估算的密集成交區 (Volume Profile)，不是特定主力的真實成本。
            
            **【交易訊號圖示】**
            * 🔼 **藍色三角 (帶數字)**：純技術面 100 分模型買點，下方數字為當日依當時 K 線逐步前推計算的得分，不含歷史 EPS、營收與法人籌碼。
            * **撐 / 壓**：帶量突破成交密集區／回踩守住，或跌破／遇到成交密集區壓力留上影線。
            * **5↗️ / 5↘️**：單一 5 日短均線扣抵值趨勢。代表均線即直剔除的歷史 K 棒位置，箭頭為預判 5 日均線未來**上彎(↗️)**或**下彎(↘️)**的趨勢。
            * **紅吞 / 黑吞**：只描述相鄰 K 線的吞噬型態，不推論特定交易者拉抬或出貨。
            """)

        st.divider()
        st.subheader("⭐ 自選群組管理")
        all_groups = list(st.session_state.fav_groups.keys())
        current_groups = [g for g, s in st.session_state.fav_groups.items() if target in [normalize_ticker(x) for x in s]]
        if current_groups:
            st.caption("目前所在群組：" + "、".join(current_groups))
        else:
            st.caption("目前尚未加入任何自選群組")
        new_group_name = st.text_input("新增群組名稱", placeholder="例如：短線觀察、波段核心", key=f"new_group_{target}")
        selected_groups = st.multiselect("將此標的加入以下群組：", options=all_groups, default=current_groups)
        
        if st.button("儲存自選設定", width="stretch", type="primary"):
            new_fav = {k: list(v) for k, v in st.session_state.fav_groups.items()}
            if new_group_name.strip():
                group_name = new_group_name.strip()
                if group_name not in new_fav:
                    new_fav[group_name] = []
                if group_name not in selected_groups:
                    selected_groups.append(group_name)
            for g in all_groups:
                normalized_members = [normalize_ticker(x) for x in new_fav[g]]
                if g in selected_groups and target not in normalized_members: new_fav[g].append(target)
                elif g not in selected_groups and target in normalized_members: new_fav[g] = [x for x in new_fav[g] if normalize_ticker(x) != target]
            for g in selected_groups:
                if g not in new_fav:
                    new_fav[g] = []
                if target not in [normalize_ticker(x) for x in new_fav[g]]:
                    new_fav[g].append(target)
            st.session_state.fav_groups = new_fav
            saved = save_cloud_data("user_settings", USER_FAVORITES_DOC, new_fav)
            if saved:
                st.success("✅ 群組設定已成功寫入雲端！")
            else:
                st.warning("群組已套用於目前工作階段，但雲端寫入失敗。")
            time.sleep(0.5) 
            st.rerun()

        st.divider()
        st.markdown(f'''<div style="font-size: 1.4rem; font-weight: bold; color: #facc15; margin-bottom: 16px;">同步監控雷達清單</div>''', unsafe_allow_html=True)

        if not st.session_state.get('nav_pool_data'):
            restore_nav_pool()
        if 'nav_pool_data' in st.session_state and len(st.session_state.nav_pool_data) > 0:
            df_nav = pd.DataFrame(st.session_state.nav_pool_data)
            df_nav = df_nav[df_nav['代號'] != target]
            if not df_nav.empty: 
                st.markdown(generate_cards_html(df_nav, st.session_state.get('is_intraday', True)), unsafe_allow_html=True)
            else: 
                st.info("目前雷達清單中已無其他符合條件的標的。")
        else:
            st.info("💡 尚未快取雷達清單。請先至「首頁」執行雷達掃描，即可在此查看並快速切換同步清單。")
    else: 
        st.error("查無此股票資料。")
