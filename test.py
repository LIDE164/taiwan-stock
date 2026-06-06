import yfinance as yf

import streamlit as st

import pandas as pd

import requests

from datetime import datetime

import plotly.graph_objects as go

from plotly.subplots import make_subplots

import json

import os



# ==========================================

# 0. 系統初始化與風格設定

# ==========================================

st.set_page_config(page_title="專業交易雷達", layout="centered", initial_sidebar_state="collapsed")



# 隱藏預設頂部選單，保持介面極簡乾淨

st.markdown('''

<style>

    #MainMenu {visibility: hidden;}

    footer {visibility: hidden;}

    

    /* 左上角側欄箭頭旁加上文字 */

    [data-testid="collapsedControl"] {

        border: 1px solid #444 !important;

        border-radius: 8px !important;

        background-color: #1a1c24 !important;

        padding: 5px 12px !important;

        display: flex !important;

        align-items: center !important;

        width: auto !important;

        transition: 0.3s;

    }

    [data-testid="collapsedControl"]::after {

        content: " ⭐ 我的自選股";

        font-size: 1.1rem;

        font-weight: bold;

        color: #ffcc00;

        margin-left: 8px;

    }

    

    .stButton button { font-weight: bold !important; border-radius: 8px !important; }

    

    /* 解析頁面股價凍結置頂 */

    .sticky-header {

        position: sticky; top: 0; z-index: 999;

        background-color: rgba(26, 28, 36, 0.95);

        padding: 10px 0; border-bottom: 1px solid #333;

        backdrop-filter: blur(5px); margin-top: -15px; margin-bottom: 15px;

    }

    

    /* 三級多空趨勢方塊 */

    .trend-box {

        background-color: #1a1c24; border: 1px solid #333; border-radius: 8px;

        padding: 15px 10px; text-align: center; box-shadow: 0 4px 6px rgba(0,0,0,0.3);

    }

    .trend-title { font-size: 1rem; color: #888; font-weight: bold; margin-bottom: 5px; border-bottom: 1px solid #333; padding-bottom: 3px;}

    .trend-status { font-size: 1.1rem; font-weight: 900; }

    

    /* 籌碼模擬表 */

    .chip-table { width: 100%; text-align: center; border-collapse: collapse; font-size: 0.9rem; margin-top: 2px;}

    .chip-table th { color: #888; border-bottom: 1px solid #444; padding: 2px; font-weight: normal;}

    .chip-table td { padding: 4px 2px; border-bottom: 1px solid #2a2d3a; font-family: monospace; font-size: 1rem;}

    .buy-color { color: #ff3333; font-weight: bold; }

    .sell-color { color: #00cc00; font-weight: bold; }

</style>

''', unsafe_allow_html=True)



STOCK_NAMES = {

    "2330": "台積電", "2317": "鴻海", "2454": "聯發科", "2308": "台達電", "2382": "廣達",

    "3231": "緯創", "2356": "英業達", "3008": "大立光", "2324": "仁寶", "1802": "台玻",

    "2603": "長榮", "2609": "陽明", "2615": "萬海", "2881": "富邦金", "2882": "國泰金"

}



@st.cache_data(ttl=3600)

def get_all_tw_stock_names():

    names = STOCK_NAMES.copy()

    try:

        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", timeout=5)

        for item in res.json():

            names[item['Code']] = item['Name']

    except:

        pass

    return names



CURRENT_STOCK_NAMES = get_all_tw_stock_names()



FAV_FILE = "favorites.json"

POOL_FILE = "pool.json"



def load_json(file_path, default_data):

    if os.path.exists(file_path):

        try:

            with open(file_path, "r", encoding="utf-8") as f: return json.load(f)

        except: pass

    return default_data



def save_json(file_path, data):

    with open(file_path, "w", encoding="utf-8") as f: json.dump(data, f)



if 'page' not in st.session_state: st.session_state.page = "home"

if 'current_stock' not in st.session_state: st.session_state.current_stock = "1802"

if 'favorites' not in st.session_state: st.session_state.favorites = load_json(FAV_FILE, ["1802", "2330"])

if 'custom_pool' not in st.session_state: st.session_state.custom_pool = load_json(POOL_FILE, list(STOCK_NAMES.keys()))

if 'nav_pool' not in st.session_state: st.session_state.nav_pool = st.session_state.custom_pool

if 'filter_buy_only' not in st.session_state: st.session_state.filter_buy_only = False



@st.cache_data(ttl=1800)

def fetch_twse_top_50():

    try:

        url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"

        res = requests.get(url, timeout=10)

        df = pd.DataFrame(res.json())

        df['TradeVolume'] = pd.to_numeric(df['TradeVolume'], errors='coerce')

        df_stocks = df[df['Code'].str.match(r'^\d{4}$')]

        top_50 = df_stocks.sort_values(by='TradeVolume', ascending=False).head(50)

        return top_50['Code'].tolist()

    except:

        return list(STOCK_NAMES.keys())



# ─── 側邊欄控制 ───

st.sidebar.title("⭐ 我的自選股清單")

if st.session_state.favorites:

    for fav in st.session_state.favorites:

        fav_name = CURRENT_STOCK_NAMES.get(fav, fav)

        if st.sidebar.button(f"📊 {fav} {fav_name}", key=f"side_fav_{fav}", use_container_width=True):

            st.session_state.current_stock = fav

            st.session_state.page = "analysis"

            st.rerun()

else:

    st.sidebar.info("目前無自選股。")



st.sidebar.divider()

st.sidebar.title("⚙️ 雷達池設定")

if st.sidebar.button("🔄 自動抓取當日成交量前 50 名", use_container_width=True):

    st.session_state.custom_pool = fetch_twse_top_50()

    save_json(POOL_FILE, st.session_state.custom_pool)

    st.sidebar.success("池名單已保存！")

    st.rerun()



# ==========================================

# 1. 核心大腦 (技術數據運算與繪圖)

# ==========================================

@st.cache_data(ttl=300) 

def get_stock_data(ticker_number):

    if ticker_number == "^TWII": return yf.Ticker("^TWII").history(period="5d")

    base_ticker = ticker_number.upper().replace(".TW", "").replace(".TWO", "")

    try:

        df = yf.Ticker(f"{base_ticker}.TW").history(period="90d")

        if df.empty or len(df) < 20: df = yf.Ticker(f"{base_ticker}.TWO").history(period="90d")

        if df.empty or len(df) < 20: return None

        

        df['5MA'] = df['Close'].rolling(window=5).mean()

        df['10MA'] = df['Close'].rolling(window=10).mean()

        df['20MA'] = df['Close'].rolling(window=20).mean()

        df['60MA'] = df['Close'].rolling(window=60).mean()

        

        exp1 = df['Close'].ewm(span=12, adjust=False).mean()

        exp2 = df['Close'].ewm(span=26, adjust=False).mean()

        df['MACD'] = exp1 - exp2

        df['Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()

        df['MACD_Hist'] = df['MACD'] - df['Signal']

        

        low_9 = df['Low'].rolling(window=9).min()

        high_9 = df['High'].rolling(window=9).max()

        rsv = (df['Close'] - low_9) / (high_9 - low_9) * 100

        df['K'] = rsv.ewm(com=2, adjust=False).mean()

        df['D'] = df['K'].ewm(com=2, adjust=False).mean()

        df['J'] = 3 * df['K'] - 2 * df['D']

        

        delta = df['Close'].diff()

        gain = delta.where(delta > 0, 0).ewm(alpha=1/14, adjust=False).mean()

        loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()

        rs = gain / loss

        df['RSI'] = 100 - (100 / (1 + rs))

        

        df['STD'] = df['Close'].rolling(window=20).std()

        df['UB'] = df['20MA'] + 2 * df['STD']

        df['LB'] = df['20MA'] - 2 * df['STD']

        

        return df

    except: return None



def analyze_today(df, ticker_number):

    if df is None: return None

    today = df.iloc[-1]

    prev = df.iloc[-2]

    c_name = CURRENT_STOCK_NAMES.get(ticker_number, "")

    

    is_golden_pit = (today['Close'] > today['20MA']) and (today['Close'] < today['5MA']) and (today['J'] < 20)

    change_percent = (today['Close'] - prev['Close']) / prev['Close'] * 100

    

    return {

        "代號": ticker_number, "名稱": c_name, "ticker_raw": ticker_number,

        "收盤價": round(today['Close'], 2), "漲跌": round(today['Close'] - prev['Close'], 2),

        "漲跌幅": round(change_percent, 2), 

        "成交量": int(today['Volume'] / 1000),

        "5日均量": int(df['Volume'].tail(5).mean() / 1000),

        "5MA": round(today['5MA'], 2), "10MA": round(today['10MA'], 2),

        "20MA": round(today['20MA'], 2), "60MA": round(today['60MA'], 2) if not pd.isna(today['60MA']) else 0,

        "MACD": round(today['MACD'], 2), "MACD柱": round(today['MACD_Hist'], 3),

        "K": round(today['K'], 2), "D": round(today['D'], 2), "J值": round(today['J'], 2),

        "RSI": round(today['RSI'], 2) if not pd.isna(today['RSI']) else 50,

        "UB": round(today['UB'], 2) if not pd.isna(today['UB']) else 0,

        "LB": round(today['LB'], 2) if not pd.isna(today['LB']) else 0,

        "訊號": is_golden_pit

    }



def generate_mock_chips_html(df):

    recent_5 = df.tail(5).iloc[::-1]

    html = "<table class='chip-table'><tr><th>日期</th><th>外資</th><th>投信</th></tr>"

    for date, row in recent_5.iterrows():

        d_str = date.strftime("%m/%d")

        change = row['Close'] - row['Open']

        base_vol = row['Volume'] / 1000

        fi_buy = int(change * 200 + (base_vol * 0.08)) 

        it_buy = int(change * 80 + (base_vol * 0.03))

        

        if fi_buy == 0: fi_buy = int(base_vol * 0.01) + 10

        if it_buy == 0: it_buy = -int(base_vol * 0.005) - 5

        

        fi_class = "buy-color" if fi_buy > 0 else "sell-color"

        it_class = "buy-color" if it_buy > 0 else "sell-color"

        

        fi_str = f"+{fi_buy:,}" if fi_buy > 0 else f"{fi_buy:,}"

        it_str = f"+{it_buy:,}" if it_buy > 0 else f"{it_buy:,}"

        

        html += f"<tr><td>{d_str}</td><td class='{fi_class}'>{fi_str}</td><td class='{it_class}'>{it_str}</td></tr>"

    html += "</table>"

    return html



def draw_professional_chart(df, ticker_name, latest_price):

    df_30 = df.tail(30)

    colors = ['#ff3333' if row['Close'] >= row['Open'] else '#00cc00' for _, row in df_30.iterrows()]

    

    last_row = df_30.iloc[-1]

    latest_vol = last_row['Volume']

    latest_macd = last_row['MACD']

    latest_j = last_row['J']

    

    fig = make_subplots(rows=4, cols=1, shared_xaxes=True, row_heights=[0.45, 0.15, 0.15, 0.25], vertical_spacing=0.06)

    

    # 需求4：主圖只保留 K線、5T、10T、20T，不畫布林通道 (UB/LB刪除)

    fig.add_trace(go.Candlestick(x=df_30.index, open=df_30['Open'], high=df_30['High'], low=df_30['Low'], close=df_30['Close'], increasing_line_color='#ff3333', decreasing_line_color='#00cc00', name="K線"), row=1, col=1)

    fig.add_trace(go.Scatter(x=df_30.index, y=df_30['5MA'], line=dict(color='orange', width=2), name="5T"), row=1, col=1)

    fig.add_trace(go.Scatter(x=df_30.index, y=df_30['10MA'], line=dict(color='yellow', width=2), name="10T"), row=1, col=1)

    fig.add_trace(go.Scatter(x=df_30.index, y=df_30['20MA'], line=dict(color='cyan', width=2), name="20T"), row=1, col=1)

    fig.add_hline(y=latest_price, line_dash="dash", line_color="#ffcc00", row=1, col=1, annotation_text=f"現價: {latest_price:.2f}", annotation_position="top right", annotation_font=dict(size=14, color="#ffcc00"))

    

    fig.add_trace(go.Bar(x=df_30.index, y=df_30['Volume'], marker_color=colors, name="VOL"), row=2, col=1)

    fig.add_hline(y=latest_vol, line_dash="dash", line_color="#888888", row=2, col=1)

    

    macd_colors = ['#ff3333' if val > 0 else '#00cc00' for val in df_30['MACD_Hist']]

    fig.add_trace(go.Bar(x=df_30.index, y=df_30['MACD_Hist'], marker_color=macd_colors, name="OSC(柱)"), row=3, col=1)

    fig.add_trace(go.Scatter(x=df_30.index, y=df_30['MACD'], line=dict(color='white', width=1.5), name="DIF"), row=3, col=1)

    fig.add_trace(go.Scatter(x=df_30.index, y=df_30['Signal'], line=dict(color='yellow', width=1.5), name="MACD"), row=3, col=1)

    

    fig.add_trace(go.Scatter(x=df_30.index, y=df_30['K'], line=dict(color='white', width=1.5), name="K"), row=4, col=1)

    fig.add_trace(go.Scatter(x=df_30.index, y=df_30['D'], line=dict(color='yellow', width=1.5), name="D"), row=4, col=1)

    fig.add_trace(go.Scatter(x=df_30.index, y=df_30['J'], line=dict(color='magenta', width=1.5), name="J"), row=4, col=1)

    

    # 需求4：將圖例 (Legend) 移到最上方，避免擋住 K 線，並關閉側邊工具列

    # 將 hoverlabel font_size 縮小，讓點擊後的資訊方塊變得精巧且不干擾視線

    fig.update_layout(

        xaxis_rangeslider_visible=False, template="plotly_dark", height=850, 

        margin=dict(l=10, r=10, t=50, b=10), paper_bgcolor='#0e1117', plot_bgcolor='#0e1117', 

        hovermode='x unified', hoverlabel=dict(font_size=11, bgcolor="rgba(26,28,36,0.85)"),

        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)

    )

    

    fig.update_xaxes(title_text="CANDLESTICK / MA", row=1, col=1, title_font=dict(size=14, color="#888888", weight="bold"))

    fig.update_xaxes(title_text="VOLUME", row=2, col=1, title_font=dict(size=14, color="#888888", weight="bold"))

    fig.update_xaxes(title_text="MACD / OSC", row=3, col=1, title_font=dict(size=14, color="#888888", weight="bold"))

    fig.update_xaxes(title_text="KDJ", row=4, col=1, title_font=dict(size=14, color="#888888", weight="bold"))

    

    return fig



def render_index_board():

    now = datetime.now()

    twii_df = get_stock_data("^TWII")

    twii_close = twii_df['Close'].iloc[-1] if twii_df is not None else 0

    twii_change = (twii_df['Close'].iloc[-1] - twii_df['Close'].iloc[-2]) if twii_df is not None else 0

    twii_color = '#ff3333' if twii_change >= 0 else '#00cc00'

    

    with st.container(border=True):

        st.markdown(f"<div style='text-align: center; color: #aaa; font-size: 1.2rem; font-weight: bold;'>加權指數 ({now.strftime('%m/%d %H:%M')})</div>", unsafe_allow_html=True)

        st.markdown(f"<div style='text-align: center; font-size: 2.5rem; font-weight: 900; color: {twii_color}; margin: 5px 0;'>{twii_close:,.2f}</div>", unsafe_allow_html=True)

        st.markdown(f"<div style='text-align: center; font-size: 1.3rem; font-weight: bold; color: {twii_color};'>漲跌: {'+' if twii_change > 0 else ''}{twii_change:,.2f}</div>", unsafe_allow_html=True)



# ==========================================

# 2. 畫面路由 (SPA 導航控制)

# ==========================================



if st.session_state.page == "home":

    st.markdown(f"<h1 style='text-align: center;'>🇹🇼 台股戰術監控總機</h1>", unsafe_allow_html=True)

    

    render_index_board()

        

    st.markdown("<h3 style='margin-top: 15px;'>🎯 戰術掃描：一鍵尋找買點</h3>", unsafe_allow_html=True)

    

    btn_col1, btn_col2 = st.columns(2)

    if btn_col1.button("✅ 搜尋【適合買進】標的", use_container_width=True):

        st.session_state.filter_buy_only = True

        st.rerun()

    if btn_col2.button("📋 顯示全部熱門名單", use_container_width=True):

        st.session_state.filter_buy_only = False

        st.rerun()

        

    search_val = st.text_input("隱藏標籤2", placeholder="手動輸入代號看解析 (如: 2330)", label_visibility="collapsed")

    if search_val:

        st.session_state.current_stock = search_val

        st.session_state.page = "analysis"

        st.rerun()



    if st.session_state.filter_buy_only:

        st.markdown("<h3 style='margin-top: 20px; color: #00cc00;'>🎯 今日符合【極佳買點】標的</h3>", unsafe_allow_html=True)

    else:

        st.markdown("<h3 style='margin-top: 20px;'>📡 今日黃金坑榜單 (超賣前 10 名)</h3>", unsafe_allow_html=True)

        

    scan_results = []

    with st.spinner('智慧雷達掃描中...'):

        for stock in st.session_state.custom_pool:

            data = analyze_today(get_stock_data(stock), stock)

            if data: scan_results.append(data)

            

    if scan_results:

        df_results = pd.DataFrame(scan_results)

        

        if st.session_state.filter_buy_only:

            df_display = df_results[df_results['訊號'] == True]

            if df_display.empty:

                st.info("💡 今日雷達池中，暫無同時符合「多頭回檔」與「極度超賣」的極佳買點標的，建議保持耐心觀望！")

        else:

            df_top50_vol = df_results.sort_values(by="成交量", ascending=False).head(50)

            df_display = df_top50_vol.sort_values(by="J值", ascending=True).head(10)

        

        st.session_state.nav_pool = df_display['ticker_raw'].tolist()

        

        for _, row in df_display.iterrows():

            with st.container(border=True):

                is_fav = row['ticker_raw'] in st.session_state.favorites

                star_icon = "⭐ 移除自選" if is_fav else "☆ 加入自選"

                sign = "+" if row['漲跌'] > 0 else ""

                p_color = "#ff3333" if row['漲跌'] >= 0 else "#00cc00"

                

                c_title, c_star = st.columns([8, 2])

                with c_title:

                    st.markdown(f"### `{row['代號']}` **{row['名稱']}**")

                with c_star:

                    if st.button(star_icon, key=f"star_{row['ticker_raw']}", use_container_width=True):

                        if is_fav: st.session_state.favorites.remove(row['ticker_raw'])

                        else: st.session_state.favorites.append(row['ticker_raw'])

                        save_json(FAV_FILE, st.session_state.favorites)

                        st.rerun()

                

                st.markdown(f'''

                <div style="background-color: #1a1c24; padding: 12px; border-radius: 8px; border: 1px solid #333; text-align: center; margin: 5px 0 10px 0;">

                    <span style="font-size: 2.6rem; font-weight: 900; color: {p_color};">{row['收盤價']}</span>

                    <span style="font-size: 1.3rem; font-weight: bold; color: {p_color}; margin-left: 12px;">{sign}{row['漲跌']} ({sign}{row['漲跌幅']}%)</span>

                </div>

                ''', unsafe_allow_html=True)

                

                st.markdown(f"📊 當前動態 ➜ **J值:** `{row['J值']}`")

                

                if st.button("📊 深度個股解析", key=f"btn_{row['ticker_raw']}", use_container_width=True):

                    st.session_state.current_stock = row['ticker_raw']

                    st.session_state.page = "analysis"

                    st.rerun()

    else: st.info("目前雷達池無資料，請至左側設定選單新增。")



elif st.session_state.page == "analysis":

    target = st.session_state.current_stock

    df_chart = get_stock_data(target)

    clean_name = CURRENT_STOCK_NAMES.get(target, "")

    

    if df_chart is not None:

        data = analyze_today(df_chart, target)

        p_color = '#ff3333' if data['漲跌'] >= 0 else '#00cc00'

        sign = "+" if data['漲跌'] > 0 else ""

        

        st.markdown(f'''

        <div class="sticky-header">

            <h2 style='text-align: center; margin: 0; padding-bottom: 5px;'>🎯 {target} {clean_name}</h2>

            <h3 style='text-align: center; color: {p_color}; font-size: 2.2rem; font-weight: 900; margin: 0;'>{data['收盤價']} ({sign}{data['漲跌幅']}%)</h3>

        </div>

        ''', unsafe_allow_html=True)

        

        nav_pool = st.session_state.get('nav_pool', st.session_state.custom_pool)

        if target in nav_pool and len(nav_pool) > 1:

            idx = nav_pool.index(target)

            prev_stock = nav_pool[idx - 1] if idx > 0 else None

            next_stock = nav_pool[idx + 1] if idx < len(nav_pool) - 1 else None

        else:

            prev_stock, next_stock = None, None



        c_nav1, c_nav2, c_nav3 = st.columns([1, 1, 1])

        with c_nav1:

            if prev_stock and st.button(f"⬅ {prev_stock}", use_container_width=True):

                st.session_state.current_stock = prev_stock

                st.rerun()

        with c_nav2:

            if st.button("🏠 首頁", use_container_width=True):

                st.session_state.page = "home"

                st.rerun()

        with c_nav3:

            if next_stock:

                if st.button(f"{next_stock} ➡", use_container_width=True):

                    st.session_state.current_stock = next_stock

                    st.rerun()

            else:

                if st.button("🏠 回首頁 ➡", use_container_width=True):

                    st.session_state.page = "home"

                    st.rerun()

            

        st.markdown("<br>", unsafe_allow_html=True)

        

        # 需求2：戰術判定自動給出「建議入手區間」

        if data['訊號']:

            buy_zone_low = data['20MA']

            buy_zone_high = round(data['20MA'] * 1.02, 2)

            st.success(f"✅ **戰術判定：【極佳買點】** 股價穩在月線之上，短線急跌且 KDJ 極度超賣。\n\n🎯 **建議入手區間：** 接近月線支撐約 `{buy_zone_low} ~ {buy_zone_high}` 附近佈局！")

        else:

            if data['J值'] >= 80:

                st.error(f"⚠️ **戰術判定：【高檔過熱】** J值過高，有回檔風險。\n\n🎯 **建議操作：** 目前溢價風險高，建議等拉回至 10日線 `{data['10MA']}` 附近再行觀察。")

            elif data['收盤價'] < data['20MA']:

                st.warning(f"⛔ **戰術判定：【趨勢偏空】** 股價跌破月線支撐，中線趨勢轉弱。\n\n🎯 **建議操作：** 空頭走勢中，建議空手觀望，或等突破月線 `{data['20MA']}` 再行進場。")

            else:

                st.info(f"⏳ **戰術判定：【觀望中】** 雖然在多頭趨勢，但目前未達極度超賣區。\n\n🎯 **建議操作：** 可於 `{data['10MA']}`(10T) 至 `{data['20MA']}`(月線) 區間分批逢低佈局。")

        

        # 恢復穩定排版，1行2格共3排，確保手機不跑版

        st.subheader("📊 技術與籌碼參數")

        

        row1_col1, row1_col2 = st.columns(2)

        with row1_col1.container(border=True):

            st.markdown("#### 🔹 均線 (MA)")

            st.markdown(f"* 5T ➜ **`{data['5MA']}`**")

            st.markdown(f"* 10T ➜ **`{data['10MA']}`**")

            st.markdown(f"* 20T ➜ **`{data['20MA']}`**")

            

        with row1_col2.container(border=True):

            st.markdown("#### 🔹 動能 (MACD)")

            st.markdown(f"* DIF ➜ **`{data['MACD']}`**")

            st.markdown(f"* OSC ➜ **`{data['MACD柱']}`**")

            st.markdown("<br>", unsafe_allow_html=True) 

            

        row2_col1, row2_col2 = st.columns(2)

        with row2_col1.container(border=True):

            st.markdown("#### 🔹 隨機指標 (KDJ)")

            st.markdown(f"* K ➜ **`{data['K']}`**")

            st.markdown(f"* D ➜ **`{data['D']}`**")

            st.markdown(f"* J ➜ **`{data['J值']}`**")

            

        with row2_col2.container(border=True):

            st.markdown("#### 🔹 RSI & 布林(後台計算)")

            rsi_status = "超賣" if data['RSI'] < 30 else "超買" if data['RSI'] > 70 else "中性"

            st.markdown(f"* RSI ➜ **`{data['RSI']}`** ({rsi_status})")

            st.markdown(f"* LB(下軌) ➜ **`{data['LB']}`**")

            st.markdown(f"* UB(上軌) ➜ **`{data['UB']}`**")

            

        row3_col1, row3_col2 = st.columns(2)

        with row3_col1.container(border=True):

            st.markdown("#### 🔹 市場量能")

            st.markdown(f"* 今日量 ➜")

            st.markdown(f"**`{data['成交量']} 張`**")

            st.markdown(f"* 5日均量 ➜")

            st.markdown(f"**`{data['5日均量']} 張`**")

            

        with row3_col2.container(border=True):

            st.markdown("#### 🔹 籌碼(模擬)")

            mock_table_html = generate_mock_chips_html(df_chart)

            st.markdown(mock_table_html, unsafe_allow_html=True)



        fig = draw_professional_chart(df_chart, target, data['收盤價'])

        st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})

        

        st.divider()



        st.subheader("📈 三級多空趨勢判定")

        

        t_short_text = "強勢格局" if data['收盤價'] > data['5MA'] else "短線弱勢"

        t_short_color = "#ff3333" if data['收盤價'] > data['5MA'] else "#00cc00"

        

        t_mid_text = "中期偏多" if data['收盤價'] > data['20MA'] else "跌破月線"

        t_mid_color = "#ff3333" if data['收盤價'] > data['20MA'] else "#00cc00"

        

        t_long_text = "長線保護" if data['收盤價'] > data['60MA'] else "趨勢轉空"

        t_long_color = "#ff3333" if data['收盤價'] > data['60MA'] else "#00cc00"

        

        t1, t2, t3 = st.columns(3)

        with t1:

            st.markdown(f"""

            <div class="trend-box">

                <div class="trend-title">短線 (日線)</div>

                <div class="trend-status" style="color: {t_short_color};">{t_short_text}</div>

            </div>

            """, unsafe_allow_html=True)

            

        with t2:

            st.markdown(f"""

            <div class="trend-box">

                <div class="trend-title">中線 (周線)</div>

                <div class="trend-status" style="color: {t_mid_color};">{t_mid_text}</div>

            </div>

            """, unsafe_allow_html=True)

            

        with t3:

            st.markdown(f"""

            <div class="trend-box">

                <div class="trend-title">長線 (月線)</div>

                <div class="trend-status" style="color: {t_long_color};">{t_long_text}</div>

            </div>

            """, unsafe_allow_html=True)

        

        st.markdown("<br>", unsafe_allow_html=True)

        

        if target in st.session_state.favorites:

            if st.button("❌ 從自選股移除", use_container_width=True):

                st.session_state.favorites.remove(target)

                save_json(FAV_FILE, st.session_state.favorites) 

                st.rerun()

        else:

            if st.button("⭐ 將此標的加入自選", use_container_width=True):

                st.session_state.favorites.append(target)

                save_json(FAV_FILE, st.session_state.favorites) 

                st.rerun()

    else: st.error("無法載入該股票資料，請確認代號是否正確。")

