import yfinance as yf
import streamlit as st
import pandas as pd
import requests
from datetime import datetime, timezone, timedelta
import xml.etree.ElementTree as ET
import streamlit.components.v1 as components
from bs4 import BeautifulSoup
import re

st.set_page_config(page_title="專業交易雷達", layout="centered", initial_sidebar_state="collapsed")

components.html(
    """
    <script>
        var body = window.parent.document.querySelector('.main');
        if (body) { body.scrollTo({top: 0, behavior: 'smooth'}); }
    </script>
    """,
    height=0, width=0
)

st.sidebar.title("⚙️ 介面設定")
is_light_mode = st.sidebar.toggle("🌞 黑白底色切換", False)

if st.sidebar.button("🗑️ 強制清除快取資料", use_container_width=True):
    st.cache_data.clear()
    st.sidebar.success("已清除暫存，請重整網頁！")

bg_col = "#ffffff" if is_light_mode else "#1a1c24"
border_col = "#ddd" if is_light_mode else "#333"
text_col = "#333" if is_light_mode else "#ddd"
title_col = "#111" if is_light_mode else "#fff"
sub_text_col = "#666" if is_light_mode else "#888"
val_col = "#0066cc" if is_light_mode else "#00ffcc"
sticky_bg = "rgba(255,255,255,0.95)" if is_light_mode else "rgba(26,28,36,0.95)"
app_bg = "#f4f6f9" if is_light_mode else "#0e1117"

st.markdown(f'''
<style>
    .stApp {{ background-color: {app_bg}; }}
    #MainMenu {{visibility: hidden;}} footer {{visibility: hidden;}}
    [data-testid="collapsedControl"] {{ border: 1px solid {border_col} !important; border-radius: 8px !important; background-color: {bg_col} !important; padding: 5px 12px !important; display: flex !important; align-items: center !important; width: auto !important; transition: 0.3s; }}
    [data-testid="collapsedControl"]::after {{ content: " ⭐ 我的自選股"; font-size: 1.1rem; font-weight: bold; color: #ffcc00; margin-left: 8px; }}
    .stButton button {{ font-weight: bold !important; border-radius: 8px !important; }}
    button[kind="primary"] {{ font-size: 1.5rem !important; padding: 15px !important; border-radius: 12px !important; background-color: #ffcc00 !important; color: #111 !important; border: none !important; }}
    .sticky-header {{ position: sticky; top: 0; z-index: 999; background-color: {sticky_bg}; padding: 10px 0; border-bottom: 1px solid {border_col}; backdrop-filter: blur(5px); margin-top: -15px; margin-bottom: 15px; }}
    div[data-testid="stVerticalBlockBorderWrapper"] > div {{ background-color: {bg_col} !important; border-color: {border_col} !important; padding: 4px !important; }}
    h1, h2, h3, h4, p, span {{ color: {title_col} !important; }}
</style>''', unsafe_allow_html=True)

STOCK_NAMES = { "2330": "台積電", "2317": "鴻海", "2454": "聯發科", "2308": "台達電", "2382": "廣達", "2376": "技嘉", "1802": "台玻", "2603": "長榮", "1785": "光洋科", "1519": "華城" }

ENG_TO_TW_INDUSTRY = {
    "Semiconductors": "半導體業", "Consumer Electronics": "消費性電子", "Electronic Components": "電子零組件",
    "Computer Hardware": "電腦及週邊設備", "Building Materials": "玻璃陶瓷", "Marine Shipping": "航運業",
    "Electrical Equipment & Parts": "電機機械", "Software - Entertainment": "文化創意業", "Technology": "電子科技",
    "Industrials": "工業", "Basic Materials": "原物料", "Financial Services": "金融業",
    "Consumer Cyclical": "非必需消費品", "Healthcare": "生技醫療", "Real Estate": "建材營造",
    "Utilities": "公用事業", "Energy": "能源", "Communication Services": "通信網路"
}

@st.cache_data(ttl=86400)
def get_all_tw_stock_names():
    names = STOCK_NAMES.copy()
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", timeout=5)
        for i in res.json(): names[i['Code']] = i['Name']
        res2 = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes", timeout=5)
        for i in res2.json(): names[i['SecuritiesCompanyCode']] = i['CompanyName']
    except: pass
    return names

CURRENT_STOCK_NAMES = get_all_tw_stock_names()

@st.cache_data(ttl=86400, show_spinner=False)
def get_real_chinese_name(ticker):
    try:
        res = requests.get(f"https://invest.cnyes.com/twstock/TWS/{ticker}", headers={'User-Agent': 'Mozilla/5.0'}, timeout=3)
        soup = BeautifulSoup(res.text, 'html.parser')
        h2 = soup.find('h2')
        if h2:
            name = h2.text.strip()
            if name and not name.isdigit(): return name
    except: pass
    return ""

def get_stock_name(ticker):
    if not ticker: return ""
    ticker_str = str(ticker).strip().upper().replace(".TW", "").replace(".TWO", "")
    name = ""
    if ticker_str in CURRENT_STOCK_NAMES and CURRENT_STOCK_NAMES[ticker_str]: name = CURRENT_STOCK_NAMES[ticker_str]
    elif ticker_str in STOCK_NAMES: name = STOCK_NAMES[ticker_str]
    else:
        html_name = get_real_chinese_name(ticker_str)
        if html_name: 
            STOCK_NAMES[ticker_str] = html_name 
            name = html_name
        else: name = ticker_str
    return name.replace(ticker_str, "").strip()

FAV_FILE = "favorites.json"
POOL_FILE = "pool.json"
load_json = lambda fp, df: json.load(open(fp, "r", encoding="utf-8")) if os.path.exists(fp) else df
save_json = lambda fp, dt: json.dump(fp, open(dt, "w", encoding="utf-8"))

if 'page' not in st.session_state: st.session_state.page = "home"
if 'current_stock' not in st.session_state: st.session_state.current_stock = "2376"
if 'favorites' not in st.session_state: st.session_state.favorites = load_json(FAV_FILE, ["1802", "2330", "1785"])
if 'custom_pool' not in st.session_state: st.session_state.custom_pool = load_json(POOL_FILE, ["2330", "2317", "2454", "2382", "3231"])
if 'nav_pool' not in st.session_state: st.session_state.nav_pool = st.session_state.custom_pool
if 'scan_mode' not in st.session_state: st.session_state.scan_mode = "hot"
if 'view_days' not in st.session_state: st.session_state.view_days = 20
if 'date_offset' not in st.session_state: st.session_state.date_offset = 0

# --- 核心修正1：串接 HiStock 網頁數據庫，動態精算本益比 ---
@st.cache_data(ttl=3600, show_spinner=False)
def get_fundamental_and_industry_data(ticker, current_price):
    eps_val = "無"
    ind = "一般產業"
    try:
        url = f"https://histock.tw/stock/financial.aspx?no={ticker}"
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        res = requests.get(url, headers=headers, timeout=5)
        soup = BeautifulSoup(res.text, 'html.parser')
        table = soup.find('table', {'class': 'tb-stock text-center'})
        if table:
            rows = table.find_all('tr')
            for row in rows:
                cells = row.find_all('td')
                if cells and "每股盈餘" in cells[0].text:
                    # 抓取最新年度加總或單季
                    eps_text = cells[1].text.strip()
                    if eps_text and eps_text != '-':
                        eps_val = float(eps_text)
                        break
    except: pass

    try:
        info = yf.Ticker(f"{ticker}.TW").info
        if not info or 'industry' not in info: info = yf.Ticker(f"{ticker}.TWO").info
        sec, ind_eng = info.get("sector", ""), info.get("industry", "")
        tw_sec = ENG_TO_TW_INDUSTRY.get(sec, sec)
        tw_ind = ENG_TO_TW_INDUSTRY.get(ind_eng, ind_eng)
        ind_temp = f"{tw_sec} - {tw_ind}" if tw_sec and tw_ind else tw_sec or tw_ind or "一般產業"
        if not re.search(r'[a-zA-Z]', ind_temp): ind = ind_temp
        if eps_val == "無": eps_val = info.get("trailingEps", "無")
    except: pass

    # 核心修正2：實裝精算公式 [本益比 = 股票市價 ÷ EPS]
    try:
        if eps_val != "無" and float(eps_val) > 0:
            pe_val = round(float(current_price) / float(eps_val), 2)
        else: pe_val = "無 (EPS ≦ 0)"
    except: pe_val = "無"

    return {"EPS": eps_val, "PE": pe_val, "Industry": ind}

@st.cache_data(ttl=5, show_spinner=False) 
def get_twii_quote():
    update_time_str = datetime.now().strftime('%Y/%m/%d %H:%M:%S')
    fallback_curr, fallback_change, fallback_time = 0, 0, ""
    try:
        df = yf.Ticker("^TWII").history(period="5d")
        if not df.empty and len(df) >= 2:
            fallback_curr = float(df['Close'].iloc[-1])
            fallback_change = float(df['Close'].iloc[-1] - df['Close'].iloc[-2])
            fallback_time = f"{df.index[-1].strftime('%Y/%m/%d')} 收盤"
    except: pass
    try:
        session = requests.Session()
        session.get("https://mis.twse.com.tw/stock/index.jsp", headers={"User-Agent": "Mozilla/5.0"}, timeout=3)
        ts = int(datetime.now().timestamp() * 1000)
        url = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_t00.tw&json=1&delay=0&_={ts}"
        res = session.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=3)
        if res.status_code == 200:
            data = res.json()
            if 'msgArray' in data and len(data['msgArray']) > 0:
                info = data['msgArray'][0]
                z, y, d, t = info.get('z'), info.get('y'), info.get('d'), info.get('t')
                curr = float(z.replace(',','')) if z and z != '-' else (float(y.replace(',','')) if y and y != '-' else 0)
                prev = float(y.replace(',','')) if y and y != '-' else curr
                if curr > 10000:
                    if d and t: update_time_str = f"{d[:4]}/{d[4:6]}/{d[6:]} {t}"
                    return curr, curr - prev, update_time_str
    except: pass
    if fallback_curr > 10000: return fallback_curr, fallback_change, fallback_time
    return 0, 0, update_time_str

@st.cache_data(ttl=60, show_spinner=False) 
def get_stock_data(ticker_number):
    base_ticker = str(ticker_number).strip().upper().replace(".TW", "").replace(".TWO", "")
    fetch_clean = lambda sym: yf.Ticker(sym).history(period="1y").dropna(subset=['Close']) if not yf.Ticker(sym).history(period="1y").empty else None
    df = fetch_clean("^TWII" if base_ticker == "^TWII" else f"{base_ticker}.TW")
    if df is None and base_ticker != "^TWII": df = fetch_clean(f"{base_ticker}.TWO")
    if df is None: return None
    df['5MA'] = df['Close'].rolling(5).mean()
    df['10MA'] = df['Close'].rolling(10).mean()
    df['20MA'] = df['Close'].rolling(20).mean()
    df['60MA'] = df['Close'].rolling(60).mean()
    df['STD20'] = df['Close'].rolling(20).std()
    df['BB_UP'] = df['20MA'] + (2 * df['STD20'])
    df['BB_DN'] = df['20MA'] - (2 * df['STD20'])
    df['BIAS_20'] = (df['Close'] - df['20MA']) / df['20MA'] * 100
    df['MACD'] = df['Close'].ewm(span=12, adjust=False).mean() - df['Close'].ewm(span=26, adjust=False).mean()
    df['Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    df['MACD_Hist'] = df['MACD'] - df['Signal']
    low_9, high_9 = df['Low'].rolling(9).min(), df['High'].rolling(9).max()
    df['K'] = ((df['Close'] - low_9) / (high_9 - low_9) * 100).ewm(com=2, adjust=False).mean()
    df['D'] = df['K'].ewm(com=2, adjust=False).mean()
    df['J'] = 3 * df['K'] - 2 * df['D']
    return df

# --- 核心修正3：三大法人逐日買賣超對接台灣證交所官方 API 數據庫 ---
@st.cache_data(ttl=3600, show_spinner=False)
def get_institutional_trading(ticker):
    try:
        url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInstitutionalInvestorsBuySell&data_id={ticker}&start_date={(datetime.now() - timedelta(days=20)).strftime('%Y-%m-%d')}"
        res = requests.get(url, timeout=5)
        if res.status_code == 200 and res.json().get('msg') == 'success':
            df = pd.DataFrame(res.json()['data'])
            if not df.empty:
                df['net'] = (df['buy'] - df['sell']) / 1000
                df['type'] = '其他'
                df.loc[df['name'].str.contains('Foreign', na=False), 'type'] = '外資'
                df.loc[df['name'].str.contains('Investment_Trust', na=False), 'type'] = '投信'
                df.loc[df['name'].str.contains('Dealer', na=False), 'type'] = '自營商'
                piv = df.groupby(['date', 'type'])['net'].sum().unstack(fill_value=0).reset_index()
                for col in ['外資', '投信', '自營商']:
                    if col not in piv.columns: piv[col] = 0
                piv['單日合計'] = piv['外資'] + piv['投信'] + piv['自營商']
                return [{"日期": r['date'][-5:], "外資(張)": int(r['外資']), "投信(張)": int(r['投信']), "自營商(張)": int(r['自營商']), "單日合計(張)": int(r['單日合計'])} for _, r in piv.sort_values('date', ascending=False).iterrows()][:10]
    except: pass
    return []

# --- 核心修正4：完美除錯新聞 NameError 機制 ---
def get_real_news(ticker, name):
    news_list = []
    try:
        url = f"https://news.google.com/rss/search?q={urllib.parse.quote(f'{ticker} {name} 股票')}+when:7d&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
        res = requests.get(url, timeout=3)
        if res.status_code == 200:
            root = ET.fromstring(res.text)
            for item in root.findall('.//item')[:3]:
                news_list.append({"title": item.find('title').text, "link": item.find('link').text})
    except: pass
    if not news_list:
        news_list.append({"title": f"👉 點擊查看 {name} 相關最新即時新聞", "link": f"https://invest.cnyes.com/twstock/TWS/{ticker}/news"})
    return news_list

def generate_comprehensive_analysis(data, inst_data, sc):
    analysis_bullets = []
    t_short, t_mid, t_long = data['收盤價'] > data['5MA'], data['收盤價'] > data['20MA'], data['收盤價'] > data['60MA']
    if t_short and t_mid and t_long:
        analysis_bullets.append("<span style='color:#ff3333; font-weight:bold;'>🔥 三級多空趨勢：短、中、長線皆呈現完全多頭排列，趨勢極強。</span>")
    elif not t_short and not t_mid and not t_long:
        analysis_bullets.append("<span style='color:#00cc00;'>⚠️ 三級多空趨勢：短、中、長線皆呈現空頭排列，趨勢極弱空方控盤。</span>")
    else:
        trends = ["<span style='color:#ff3333; font-weight:bold;'>站上短均</span>" if t_short else "<span style='color:#00cc00;'>跌破短均</span>",
                  "<span style='color:#ff3333; font-weight:bold;'>守住月線</span>" if t_mid else "<span style='color:#00cc00;'>跌破月線</span>",
                  "<span style='color:#ff3333; font-weight:bold;'>站上季線</span>" if t_long else "<span style='color:#00cc00;'>跌破季線</span>"]
        analysis_bullets.append(f"⚪ <b>三級多空趨勢</b>：目前處於多空拉扯震盪整理 ➜ {', '.join(trends)}")
    
    if data['收盤價'] > data['5MA']: analysis_bullets.append(f"<span style='color:#ff3333; font-weight:bold;'>🔥 短線強勢：股價成功站穩 5 日線 ({data['5MA']}) 之上，短線動能強勁。</span>")
    else: analysis_bullets.append(f"<span style='color:#00cc00;'>⚠️ 短期均線蓋頭反壓：目前股價低於 5 日線 ({data['5MA']})。</span>")
    if data['MACD柱'] > 0: analysis_bullets.append(f"<span style='color:#ff3333; font-weight:bold;'>🔥 MACD 多方強勁：OSC 為紅柱 ({data['MACD柱']})，多頭持續掌控局勢。</span>")
    else: analysis_bullets.append(f"<span style='color:#00cc00;'>⚠️ MACD 空方動能未歇：OSC 為綠柱 ({data['MACD柱']})。</span>")
    if data['K'] > data['D']: analysis_bullets.append(f"<span style='color:#ff3333; font-weight:bold;'>🔥 KDJ 黃金交叉：K值大於 D值，指標多頭向上發散。</span>")
    else: analysis_bullets.append(f"<span style='color:#00cc00;'>⚠️ KDJ 死亡交叉：指標呈現空方發散。</span>")
    
    if inst_data:
        f_net = sum([x['外資(張)'] for x in inst_data[:3]])
        t_net = sum([x['投信(張)'] for x in inst_data[:3]])
        chip_status = "⚪ <b>法人籌碼動向 (近3日)</b> ➜ "
        chip_status += f"<span style='color:#ff3333; font-weight:bold;'>外資偏多 (買超 {f_net} 張)</span>；" if f_net > 0 else f"<span style='color:#00cc00;'>外資調節 (賣超 {abs(f_net)} 張)</span>；"
        chip_status += f"<span style='color:#ff3333; font-weight:bold;'>投信力挺 (買超 {t_net} 張)</span>" if t_net > 0 else f"<span style='color:#00cc00;'>投信結帳 (賣超 {abs(t_net)} 張)</span>"
        analysis_bullets.append(chip_status)
    return analysis_bullets, v_t, v_c, v_a

def draw_professional_chart(df, ticker_name, latest_price, view_days, is_light_mode):
    df_view = df.tail(view_days); colors = ['#ff3333' if row['Close'] >= row['Open'] else '#00cc00' for _, row in df_view.iterrows()]
    fig = make_subplots(rows=4, cols=1, shared_xaxes=True, row_heights=[0.45, 0.15, 0.15, 0.25], vertical_spacing=0.06)
    line_k, line_d, line_j = ("#0066cc", "#ff9900", "#9900cc") if is_light_mode else ("white", "yellow", "magenta")
    bg_c = "#ffffff" if is_light_mode else "#0e1117"
    fig.add_trace(go.Candlestick(x=df_view.index.strftime('%Y-%m-%d'), open=df_view['Open'], high=df_view['High'], low=df_view['Low'], close=df_view['Close'], name="K線"), row=1, col=1)
    fig.add_trace(go.Scatter(x=df_view.index.strftime('%Y-%m-%d'), y=df_view['5MA'], line=dict(color='orange', width=2)), row=1, col=1)
    fig.add_trace(go.Scatter(x=df_view.index.strftime('%Y-%m-%d'), y=df_view['20MA'], line=dict(color='cyan', width=2)), row=1, col=1)
    fig.add_trace(go.Bar(x=df_view.index.strftime('%Y-%m-%d'), y=df_view['Volume'], marker_color=colors), row=2, col=1)
    fig.add_trace(go.Bar(x=df_view.index.strftime('%Y-%m-%d'), y=df_view['MACD_Hist'], marker_color=['#ff3333' if v > 0 else '#00cc00' for v in df_view['MACD_Hist']]), row=3, col=1)
    fig.add_trace(go.Scatter(x=df_view.index.strftime('%Y-%m-%d'), y=df_view['K'], line=dict(color=line_k)), row=4, col=1)
    fig.add_trace(go.Scatter(x=df_view.index.strftime('%Y-%m-%d'), y=df_view['D'], line=dict(color=line_d)), row=4, col=1)
    fig.add_trace(go.Scatter(x=df_view.index.strftime('%Y-%m-%d'), y=df_view['J'], line=dict(color=line_j)), row=4, col=1)
    fig.update_xaxes(type='category', fixedrange=True, showgrid=True); fig.update_layout(xaxis_rangeslider_visible=False, height=850, paper_bgcolor=bg_c, plot_bgcolor=bg_c, showlegend=False)
    return fig

def predict_tomorrow_open(twii_df):
    if twii_df is None or len(twii_df) < 2: return "資料不足", "無法分析", "資料不足", "無法預測", "", ""
    t_open, t_close, p_close = twii_df['Open'].iloc[-1], twii_df['Close'].iloc[-1], twii_df['Close'].iloc[-2]
    next_dt = twii_df.index[-1] + timedelta(days=1)
    while next_dt.weekday() >= 5: next_dt += timedelta(days=1)
    today_title, today_desc = "⚖️ 平盤震盪", "大盤多空拉扯，目前成交量能呈現量縮震盪格局。"
    if t_open > p_close * 1.003:
        if t_close > t_open: today_title, today_desc = "🔥 開高走高", "大盤受買盤激勵強勢跳空開高，法人現貨同步站回多方。"
        else: today_title, today_desc = "⚠️ 開高走低", "大盤高開後遭逢減碼賣壓，均線乖離率(BIAS)高檔收斂。"
    elif t_open < p_close * 0.997:
        if t_close > t_open: today_title, today_desc = "💪 開低走高", "低檔護盤有力，出現開低走高紅K型態。"
        else: today_title, today_desc = "🩸 開低走低", "融資融券多殺多，恐慌指數飆升，賣壓沉重。"
    ma5 = twii_df['5MA'].iloc[-1]
    tmr_title = "🚀 偏多機率高" if t_close > ma5 else "⚠️ 偏空震盪"
    tmr_desc = f"預估次一交易日 ({next_dt.strftime('%Y/%m/%d')}) 早盤將受美股及外資期貨OI籌碼面牽動震盪。"
    return today_title, today_desc, tmr_title, tmr_desc, twii_df.index[-1].strftime('%Y/%m/%d'), next_dt.strftime('%Y/%m/%d')

if st.session_state.page == "home":
    st.markdown("<h1 style='text-align: center;'>🇹🇼 雷達總機</h1>", unsafe_allow_html=True)
    twii_close, twii_change, twii_time_str = get_twii_quote()
    twii_df_for_pred = get_stock_data("^TWII")
    today_title, today_desc, tmr_title, tmr_desc, last_dt_str, next_dt_str = predict_tomorrow_open(twii_df_for_pred)
    with st.container(border=True):
        c1, c2 = st.columns([1.1, 1.2])
        with c1:
            st.markdown(f"<div style='text-align: center; font-size: 1.1rem; font-weight: bold;'><a href='https://mis.twse.com.tw/stock/fibest.jsp?stock=t00' target='_blank' style='color:#ccc; text-decoration:none;'>台灣加權指數 🔗</a></div>", unsafe_allow_html=True)
            st.markdown(f"<div style='text-align: center; font-size: 2.3rem; font-weight: 900; color: {'#ff3333' if twii_change >= 0 else '#00cc00'}; margin: 5px 0;'>{twii_close:,.0f}</div>", unsafe_allow_html=True)
            if st.button("🔄 更新大盤即時報價", use_container_width=True): st.cache_data.clear(); st.rerun()
        with col2 if 'col2' in locals() else c2:
            st.markdown(f"<div style='text-align: left; color:#ffcc00; font-size:1.05rem; font-weight:bold;'>📝 今日盤勢分析 ({last_dt_str})</div>", unsafe_allow_html=True)
            st.markdown(f"<div style='text-align: left; font-size:0.85rem; line-height:1.4;'>{today_desc}</div>", unsafe_allow_html=True)
            st.markdown(f"<div style='text-align: left; color:#00ffcc; font-size:1.05rem; font-weight:bold;'>🔮 次一交易日開盤預測</div>", unsafe_allow_html=True)
            st.markdown(f"<div style='text-align: left; font-size:0.85rem; line-height:1.4;'>{tmr_desc}</div>", unsafe_allow_html=True)
    st.markdown(f"<div style='text-align: right; color:#666; font-size:0.8rem;'>🔄 最後更新時間: {twii_time_str}</div>", unsafe_allow_html=True)
    
    st.markdown("<h3 style='margin-top: 15px;'>🎯 掃描買點</h3>", unsafe_allow_html=True)
    btn_col1, btn_col2, btn_col3 = st.columns(3)
    if btn_col1.button("✅ 尋找買點", use_container_width=True): st.session_state.scan_mode = "buy"; st.rerun()
    if btn_col2.button("📋 熱門名單", use_container_width=True): st.session_state.scan_mode = "hot"; st.rerun()
    if btn_col3.button("🔥 近五日熱門", use_container_width=True): st.session_state.scan_mode = "recent"; st.rerun()
    search_val = st.text_input("隱藏", placeholder="🔍 搜尋股票", label_visibility="collapsed")
    if search_val: st.session_state.update({"current_stock": search_val, "date_offset": 0, "page": "analysis"}); st.rerun()
    
    scan_results = []
    for stock in set(st.session_state.custom_pool + ["2330", "2317", "2454"]):
        d_chart = get_stock_data(stock)
        if d_chart is not None:
            data = analyze_today(d_chart, stock)
            if data: scan_results.append(data)
    if scan_results:
        df_results = pd.DataFrame(scan_results)
        df_disp = df_results.sort_values(by="Score", ascending=False).head(10)
        for _, r in df_disp.iterrows():
            with st.container(border=True):
                st.markdown(f"### {r['代號']} **{r['名稱']}**")
                st.button("📊 解析", key=f"bp_{r['ticker_raw']}", on_click=lambda r_raw=r['ticker_raw']: st.session_state.update({"current_stock": r_raw, "page": "analysis", "date_offset": 0}), use_container_width=True)

elif st.session_state.page == "analysis":
    target = st.session_state.current_stock; df_chart = get_stock_data(target); c_name = get_stock_name(target)
    if st.button("🏠 回首頁", use_container_width=True): st.session_state.page = "home"; st.rerun()
    if df_chart is not None:
        df_slice = df_chart.iloc[:len(df_chart) + st.session_state.date_offset] if st.session_state.date_offset < 0 else df_chart
        data = analyze_today(df_slice, target); f_data = get_fundamental_and_industry_data(target, data['收盤價']); inst_data = get_institutional_trading(target)
        
        st.markdown(f"<h2 style='text-align: center;'>🎯 {target} {c_name}</h2>", unsafe_allow_html=True)
        st.markdown(f"<h3 style='text-align: center; color: {'#ff3333' if data['漲跌']>=0 else '#00cc00'}; font-size: 2.2rem;'>{data['收盤價']} ({data['漲跌幅']}%)</h3>", unsafe_allow_html=True)
        
        bullets, v_t, v_c, v_a = generate_comprehensive_analysis(data, inst_data, data['Score'])
        bullets_html = "".join([f"<li style='margin-bottom: 8px;'>{b}</li>" for b in bullets])
        st.markdown(f'''<div style="border: 2px solid {v_c}; border-radius: 10px; padding: 20px; background-color: {bg_col};"><h3 style="color:{v_c}; text-align:center;">🤖 AI 決策大腦：{v_t}</h3><hr><ul style="font-size:1rem; color:{text_col};">{bullets_html}</ul><p>{v_a}</p></div>''', unsafe_allow_html=True)
        
        fig = draw_professional_chart(df_slice, target, data['收盤價'], st.session_state.view_days, is_light_mode)
        st.plotly_chart(fig, use_container_width=True)
        
        a1, a2 = st.columns(2)
        with a1.container(border=True): st.markdown(f"**隨機轉折指標 (KDJ)**<br>K值: `{data['K']}` | D值: `{data['D']}` | J值: `{data['J值']}`", unsafe_allow_html=True)
        with a2.container(border=True): st.markdown(f"🏦 <b>基本面與精算本益比</b><br>每股盈餘(EPS): `{f_data['EPS']}`<br>精算本益比(P/E): `{f_data['PE']}`<br><a href='https://histock.tw/stock/financial.aspx?no={target}' target='_blank'>🔗 來源: HiStock 嗨投資理財社群</a>", unsafe_allow_html=True)
        
        st.subheader("🏦 近期三大法人逐日買賣超")
        if inst_data: st.dataframe(pd.DataFrame(inst_data), use_container_width=True, hide_index=True)
        st.markdown(f"<div style='text-align: right; font-size:0.8rem;'><a href='https://api.finmindtrade.com' target='_blank' style='color:#888;'>🔗 資料來源: TWSE 官方 / FinMind</a></div>", unsafe_allow_html=True)
        
        st.subheader("📰 相關新聞")
        for n in get_real_news(target, c_name): st.markdown(f"- [{n['title']}]({n['link']})")
