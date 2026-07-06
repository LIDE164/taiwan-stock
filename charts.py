# charts.py - AI機構級強化版圖表模組（極簡俐落化 + 副圖數值標示版）
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
import numpy as np

# ==============================
# 🔥 AI 主力成本 + 支撐壓力 (Volume Profile)
# ==============================
def find_levels(df, bins=60):
    """改良版：只抓取成交量最大的 Top 3 關鍵點位，避免圖表過度雜亂"""
    price = (df['High'] + df['Low'] + df['Close']) / 3
    volume = df['Volume']
    hist, edges = np.histogram(price, bins=bins, weights=volume)
    
    levels = []
    # 找出所有峰值
    for i in range(1, len(hist)-1):
        if hist[i] > hist[i-1] and hist[i] > hist[i+1]:
            lvl = (edges[i] + edges[i+1]) / 2
            levels.append((lvl, hist[i]))
            
    # 根據成交量(權重)排序，只取前 3 大主力成本區
    levels = sorted(levels, key=lambda x: x[1], reverse=True)[:3]
    
    if not levels:
        return [], []
        
    return [l[0] for l in levels], [l[1] for l in levels]

# ==============================
# 🔥 趨勢判斷（機構邏輯）
# ==============================
def detect_trend(df):
    if '5MA' not in df or '20MA' not in df or '60MA' not in df:
        return "盤整⚠️"
    ma5 = df['5MA'].iloc[-1]
    ma20 = df['20MA'].iloc[-1]
    ma60 = df['60MA'].iloc[-1]

    if ma5 > ma20 > ma60:
        return "多頭🔥"
    elif ma5 < ma20 < ma60:
        return "空頭❄️"
    else:
        return "盤整⚠️"

# ==============================
# 🔥 突破判斷（過濾假突破）
# ==============================
def detect_breakout(df_view, levels):
    signals = []
    for lvl in levels:
        for i in range(1, len(df_view)):
            c_prev = df_view['Close'].iloc[i-1]
            c_curr = df_view['Close'].iloc[i]
            date = df_view.index[i].strftime('%Y-%m-%d')
            
            # 突破條件（站上 + 收盤確認）
            if c_curr > lvl and c_prev <= lvl:
                signals.append(("buy", date, c_curr))
            # 跌破
            if c_curr < lvl and c_prev >= lvl:
                signals.append(("sell", date, c_curr))
    return signals

def compute_ai_signals(df):
    df = df.copy()
    if '20MA' not in df.columns: df['20MA'] = df['Close'].rolling(20).mean()
    if '60MA' not in df.columns: df['60MA'] = df['Close'].rolling(60).mean()

    df['trend_up'] = (df['20MA'] > df['60MA']) & (df['Close'] > df['20MA'])
    df['momentum'] = df['Close'].pct_change(3)
    df['vol_strength'] = df['Volume'] > df['Volume'].rolling(5).mean() * 1.3
    df['breakout'] = df['Close'] > df['High'].rolling(20).max().shift(1)
    df['fake_breakout'] = (df['High'] > df['High'].rolling(20).max().shift(1)) & (df['Close'] < df['20MA'])

    score = (
        df['trend_up'].astype(int) * 2 +
        df['breakout'].astype(int) * 3 +
        df['vol_strength'].astype(int) * 2 +
        (df['momentum'] > 0).astype(int)
    )
    df['ai_score'] = score
    df['ai_buy'] = (score >= 5)
    return df

def draw_professional_chart(df, latest_price, view_days=120, is_light_mode=False, show_buy_signal=True, show_sup_res=True, show_signals=True, buy_dates=None):
    if buy_dates is None: buy_dates = []

    df = compute_ai_signals(df)
    df_view = df.tail(view_days).copy()
    
    x_vals = df_view.index.strftime('%Y-%m-%d')
    colors = ['#ef4444' if row['Close'] >= row['Open'] else '#22c55e' for _, row in df_view.iterrows()]

    line_k, line_d, line_j = ("#3b82f6", "#f59e0b", "#a855f7") if is_light_mode else ("#60a5fa", "#fbbf24", "#c084fc")
    grid_c = "rgba(0,0,0,0.05)" if is_light_mode else "rgba(255,255,255,0.05)"
    bg_c = "#ffffff" if is_light_mode else "#0b1120"
    txt_c = "#333" if is_light_mode else "#e2e8f0"

    fig = make_subplots(rows=4, cols=1, shared_xaxes=True, row_heights=[0.5, 0.15, 0.15, 0.2], vertical_spacing=0.03)

    # ===== 1. K線與均線 =====
    fig.add_trace(go.Candlestick(
        x=x_vals, open=df_view['Open'], high=df_view['High'], low=df_view['Low'], close=df_view['Close'],
        increasing_line_color='#ef4444', decreasing_line_color='#22c55e', name="K線"
    ), row=1, col=1)

    if '5MA' in df_view: fig.add_trace(go.Scatter(x=x_vals, y=df_view['5MA'], line=dict(color='#facc15', width=1.5), name="5T"), row=1, col=1)
    if '10MA' in df_view: fig.add_trace(go.Scatter(x=x_vals, y=df_view['10MA'], line=dict(color='#34d399', width=1.5), name="10T"), row=1, col=1)
    if '20MA' in df_view: fig.add_trace(go.Scatter(x=x_vals, y=df_view['20MA'], line=dict(color='#60a5fa', width=2), name="20T"), row=1, col=1)
    
    highest, lowest = df_view['High'].max(), df_view['Low'].min()

    # 📌 鎖定左上角顯示最新均線數值 (主圖)
    ma_text = f"5T: {df_view['5MA'].iloc[-1]:.1f} | 10T: {df_view['10MA'].iloc[-1]:.1f} | 20T: {df_view['20MA'].iloc[-1]:.1f}"
    fig.add_annotation(
        xref="x domain", yref="y domain", x=0.01, y=0.98, text=ma_text, 
        showarrow=False, xanchor='left', yanchor='top',
        font=dict(color="#facc15", size=12, weight="bold"), row=1, col=1, bgcolor="rgba(0,0,0,0.4)"
    )

    # 📌 趨勢顯示 (右上角)
    trend = detect_trend(df_view)
    fig.add_annotation(
        xref="x domain", yref="y domain", x=0.99, y=0.98, text=f"機構趨勢: {trend}", 
        showarrow=False, xanchor='right', yanchor='top',
        bgcolor="rgba(0,0,0,0.6)", font=dict(color="white", size=12, weight="bold"), row=1, col=1
    )

    fig.add_hline(y=latest_price, line_dash="dash", line_color="#facc15", row=1, col=1, opacity=0.5)

    # 📌 均線扣抵值
    for period, color, name in [(5, '#facc15', '5扣抵'), (10, '#34d399', '10扣抵'), (20, '#60a5fa', '20扣抵')]:
        if len(df_view) >= period:
            idx = -period
            d_date = df_view.index[idx].strftime('%Y-%m-%d')
            d_price = df_view['Low'].iloc[idx] * 0.95
            fig.add_trace(go.Scatter(
                x=[d_date], y=[d_price], mode='markers+text',
                marker=dict(symbol='triangle-up-open', size=8, color=color, line=dict(width=2)),
                text=[name], textposition="bottom center", textfont=dict(size=9, color=color),
                name=name, hoverinfo='skip'
            ), row=1, col=1)

    # ===== AI 支撐壓力與訊號標示 (極簡版) =====
    if show_sup_res:
        levels, strengths = find_levels(df_view)
        
        # 畫支撐壓力線 (最多3條)
        for lvl in levels:
            fig.add_hline(y=lvl, line_dash="dot", line_width=2, line_color="#c084fc", opacity=0.8, row=1, col=1)

        # 畫突破訊號 (回踩支撐/跌破壓力)
        signals = detect_breakout(df_view, levels)
        buy_x, buy_y, sell_x, sell_y = [], [], [], []
        for typ, x, y in signals:
            if typ == "buy": buy_x.append(x); buy_y.append(y * 0.98)
            else: sell_x.append(x); sell_y.append(y * 1.02)
                
        if buy_x: fig.add_trace(go.Scatter(x=buy_x, y=buy_y, mode='markers', marker=dict(symbol='triangle-up', size=10, color='#22c55e'), name="支撐買點", hoverinfo='skip'), row=1, col=1)
        if sell_x: fig.add_trace(go.Scatter(x=sell_x, y=sell_y, mode='markers', marker=dict(symbol='triangle-down', size=10, color='#ef4444'), name="壓力賣點", hoverinfo='skip'), row=1, col=1)

        # 歷史高低點
        fig.add_hline(y=highest, line_dash="dash", line_color="#ef4444", row=1, col=1, annotation_text=f"高 {highest:.1f}", annotation_position="top right", annotation_font_color="#ef4444")
        fig.add_hline(y=lowest, line_dash="dash", line_color="#22c55e", row=1, col=1, annotation_text=f"低 {lowest:.1f}", annotation_position="bottom right", annotation_font_color="#22c55e")

    # 傳統吞噬訊號與 AI 模型訊號
    if show_buy_signal:
        ai_x, ai_y, ai_text = [], [], []
        for i in range(len(df_view)):
            row = df_view.iloc[i]
            if row.get('ai_buy', False):
                ai_x.append(x_vals[i])
                ai_y.append(row['Low'] * 0.92)
                ai_text.append(f"🤖{int(row['ai_score'])}")

        if ai_x:
            fig.add_trace(go.Scatter(x=ai_x, y=ai_y, mode='markers+text', marker=dict(symbol='star', size=12, color='#22c55e'), text=ai_text, textposition="bottom center", textfont=dict(color="#22c55e", size=10, weight="bold"), name="AI買點", hoverinfo='skip'), row=1, col=1)

    fig.update_yaxes(range=[lowest * 0.85, highest * 1.15], row=1, col=1)

    # ===== 2. 成交量 =====
    fig.add_trace(go.Bar(x=x_vals, y=df_view['Volume'], marker_color=colors, name="VOL"), row=2, col=1)
    
    # 📌 VOL 最新數值 (左上角)
    vol_last = df_view['Volume'].iloc[-1]
    fig.add_annotation(
        xref="x domain", yref="y2 domain", x=0.01, y=0.95, text=f"VOL: {vol_last:,.0f}", 
        showarrow=False, xanchor='left', yanchor='top',
        font=dict(color=txt_c, size=11, weight="bold"), row=2, col=1, bgcolor="rgba(0,0,0,0.3)"
    )

    # ===== 3. MACD =====
    macd_c = ['#ef4444' if val > 0 else '#22c55e' for val in df_view.get('MACD_Hist', [0]*len(df_view))]
    fig.add_trace(go.Bar(x=x_vals, y=df_view.get('MACD_Hist', 0), marker_color=macd_c, name="MACD柱"), row=3, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df_view.get('MACD', 0), line=dict(color="#3b82f6", width=1), name="DIF"), row=3, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df_view.get('Signal', 0), line=dict(color="#f59e0b", width=1), name="MACD"), row=3, col=1)

    # 📌 MACD 最新數值 (左上角)
    macd_last = df_view['MACD'].iloc[-1] if 'MACD' in df_view else 0
    sig_last = df_view['Signal'].iloc[-1] if 'Signal' in df_view else 0
    osc_last = df_view['MACD_Hist'].iloc[-1] if 'MACD_Hist' in df_view else 0
    fig.add_annotation(
        xref="x domain", yref="y3 domain", x=0.01, y=0.95, 
        text=f"MACD: {macd_last:.2f} | DIF: {sig_last:.2f} | OSC: {osc_last:.2f}", 
        showarrow=False, xanchor='left', yanchor='top',
        font=dict(color=txt_c, size=11, weight="bold"), row=3, col=1, bgcolor="rgba(0,0,0,0.3)"
    )

    # ===== 4. KDJ =====
    if 'K' in df_view:
        fig.add_trace(go.Scatter(x=x_vals, y=df_view.get('K', 50), line=dict(color=line_k, width=1.2), name="K"), row=4, col=1)
        fig.add_trace(go.Scatter(x=x_vals, y=df_view.get('D', 50), line=dict(color=line_d, width=1.2), name="D"), row=4, col=1)
        fig.add_trace(go.Scatter(x=x_vals, y=df_view.get('J', 50), line=dict(color=line_j, width=1.2), name="J"), row=4, col=1)

        # 📌 KDJ 最新數值 (左上角)
        k_last = df_view['K'].iloc[-1]
        d_last = df_view['D'].iloc[-1]
        j_last = df_view['J'].iloc[-1]
        fig.add_annotation(
            xref="x domain", yref="y4 domain", x=0.01, y=0.95, 
            text=f"K: {k_last:.1f} | D: {d_last:.1f} | J: {j_last:.1f}", 
            showarrow=False, xanchor='left', yanchor='top',
            font=dict(color=txt_c, size=11, weight="bold"), row=4, col=1, bgcolor="rgba(0,0,0,0.3)"
        )

    # 📌 防亂跑鎖定設定 (fixedrange=True 鎖死縮放與平移)
    fig.update_xaxes(
        type='category', nticks=15, showgrid=True, gridcolor=grid_c, 
        fixedrange=True, # 🔒 鎖定 X 軸縮放拖曳
        showspikes=True, spikemode="across", spikesnap="cursor", showline=True, spikedash="solid", spikethickness=1
    )
    fig.update_yaxes(showgrid=True, gridcolor=grid_c, fixedrange=True) # 🔒 鎖定 Y 軸縮放拖曳

    fig.update_layout(
        xaxis_rangeslider_visible=False,
        template="plotly_white" if is_light_mode else "plotly_dark",
        height=850,
        margin=dict(l=10, r=10, t=10, b=10),
        paper_bgcolor=bg_c,
        plot_bgcolor=bg_c,
        hovermode="x unified", 
        dragmode=False, # 🔒 徹底關閉拖拽模式，防止按到圖表亂跑
        showlegend=False,
        hoverlabel=dict(font_size=12)
    )

    return fig# charts.py - AI機構級強化版圖表模組（極簡俐落化 + 副圖數值標示版）
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
import numpy as np

# ==============================
# 🔥 AI 主力成本 + 支撐壓力 (Volume Profile)
# ==============================
def find_levels(df, bins=60):
    """改良版：只抓取成交量最大的 Top 3 關鍵點位，避免圖表過度雜亂"""
    price = (df['High'] + df['Low'] + df['Close']) / 3
    volume = df['Volume']
    hist, edges = np.histogram(price, bins=bins, weights=volume)
    
    levels = []
    # 找出所有峰值
    for i in range(1, len(hist)-1):
        if hist[i] > hist[i-1] and hist[i] > hist[i+1]:
            lvl = (edges[i] + edges[i+1]) / 2
            levels.append((lvl, hist[i]))
            
    # 根據成交量(權重)排序，只取前 3 大主力成本區
    levels = sorted(levels, key=lambda x: x[1], reverse=True)[:3]
    
    if not levels:
        return [], []
        
    return [l[0] for l in levels], [l[1] for l in levels]

# ==============================
# 🔥 趨勢判斷（機構邏輯）
# ==============================
def detect_trend(df):
    if '5MA' not in df or '20MA' not in df or '60MA' not in df:
        return "盤整⚠️"
    ma5 = df['5MA'].iloc[-1]
    ma20 = df['20MA'].iloc[-1]
    ma60 = df['60MA'].iloc[-1]

    if ma5 > ma20 > ma60:
        return "多頭🔥"
    elif ma5 < ma20 < ma60:
        return "空頭❄️"
    else:
        return "盤整⚠️"

# ==============================
# 🔥 突破判斷（過濾假突破）
# ==============================
def detect_breakout(df_view, levels):
    signals = []
    for lvl in levels:
        for i in range(1, len(df_view)):
            c_prev = df_view['Close'].iloc[i-1]
            c_curr = df_view['Close'].iloc[i]
            date = df_view.index[i].strftime('%Y-%m-%d')
            
            # 突破條件（站上 + 收盤確認）
            if c_curr > lvl and c_prev <= lvl:
                signals.append(("buy", date, c_curr))
            # 跌破
            if c_curr < lvl and c_prev >= lvl:
                signals.append(("sell", date, c_curr))
    return signals

def compute_ai_signals(df):
    df = df.copy()
    if '20MA' not in df.columns: df['20MA'] = df['Close'].rolling(20).mean()
    if '60MA' not in df.columns: df['60MA'] = df['Close'].rolling(60).mean()

    df['trend_up'] = (df['20MA'] > df['60MA']) & (df['Close'] > df['20MA'])
    df['momentum'] = df['Close'].pct_change(3)
    df['vol_strength'] = df['Volume'] > df['Volume'].rolling(5).mean() * 1.3
    df['breakout'] = df['Close'] > df['High'].rolling(20).max().shift(1)
    df['fake_breakout'] = (df['High'] > df['High'].rolling(20).max().shift(1)) & (df['Close'] < df['20MA'])

    score = (
        df['trend_up'].astype(int) * 2 +
        df['breakout'].astype(int) * 3 +
        df['vol_strength'].astype(int) * 2 +
        (df['momentum'] > 0).astype(int)
    )
    df['ai_score'] = score
    df['ai_buy'] = (score >= 5)
    return df

def draw_professional_chart(df, latest_price, view_days=120, is_light_mode=False, show_buy_signal=True, show_sup_res=True, show_signals=True, buy_dates=None):
    if buy_dates is None: buy_dates = []

    df = compute_ai_signals(df)
    df_view = df.tail(view_days).copy()
    
    x_vals = df_view.index.strftime('%Y-%m-%d')
    colors = ['#ef4444' if row['Close'] >= row['Open'] else '#22c55e' for _, row in df_view.iterrows()]

    line_k, line_d, line_j = ("#3b82f6", "#f59e0b", "#a855f7") if is_light_mode else ("#60a5fa", "#fbbf24", "#c084fc")
    grid_c = "rgba(0,0,0,0.05)" if is_light_mode else "rgba(255,255,255,0.05)"
    bg_c = "#ffffff" if is_light_mode else "#0b1120"
    txt_c = "#333" if is_light_mode else "#e2e8f0"

    fig = make_subplots(rows=4, cols=1, shared_xaxes=True, row_heights=[0.5, 0.15, 0.15, 0.2], vertical_spacing=0.03)

    # ===== 1. K線與均線 =====
    fig.add_trace(go.Candlestick(
        x=x_vals, open=df_view['Open'], high=df_view['High'], low=df_view['Low'], close=df_view['Close'],
        increasing_line_color='#ef4444', decreasing_line_color='#22c55e', name="K線"
    ), row=1, col=1)

    if '5MA' in df_view: fig.add_trace(go.Scatter(x=x_vals, y=df_view['5MA'], line=dict(color='#facc15', width=1.5), name="5T"), row=1, col=1)
    if '10MA' in df_view: fig.add_trace(go.Scatter(x=x_vals, y=df_view['10MA'], line=dict(color='#34d399', width=1.5), name="10T"), row=1, col=1)
    if '20MA' in df_view: fig.add_trace(go.Scatter(x=x_vals, y=df_view['20MA'], line=dict(color='#60a5fa', width=2), name="20T"), row=1, col=1)
    
    highest, lowest = df_view['High'].max(), df_view['Low'].min()

    # 📌 鎖定左上角顯示最新均線數值 (主圖)
    ma_text = f"5T: {df_view['5MA'].iloc[-1]:.1f} | 10T: {df_view['10MA'].iloc[-1]:.1f} | 20T: {df_view['20MA'].iloc[-1]:.1f}"
    fig.add_annotation(
        xref="x domain", yref="y domain", x=0.01, y=0.98, text=ma_text, 
        showarrow=False, xanchor='left', yanchor='top',
        font=dict(color="#facc15", size=12, weight="bold"), row=1, col=1, bgcolor="rgba(0,0,0,0.4)"
    )

    # 📌 趨勢顯示 (右上角)
    trend = detect_trend(df_view)
    fig.add_annotation(
        xref="x domain", yref="y domain", x=0.99, y=0.98, text=f"機構趨勢: {trend}", 
        showarrow=False, xanchor='right', yanchor='top',
        bgcolor="rgba(0,0,0,0.6)", font=dict(color="white", size=12, weight="bold"), row=1, col=1
    )

    fig.add_hline(y=latest_price, line_dash="dash", line_color="#facc15", row=1, col=1, opacity=0.5)

    # 📌 均線扣抵值
    for period, color, name in [(5, '#facc15', '5扣抵'), (10, '#34d399', '10扣抵'), (20, '#60a5fa', '20扣抵')]:
        if len(df_view) >= period:
            idx = -period
            d_date = df_view.index[idx].strftime('%Y-%m-%d')
            d_price = df_view['Low'].iloc[idx] * 0.95
            fig.add_trace(go.Scatter(
                x=[d_date], y=[d_price], mode='markers+text',
                marker=dict(symbol='triangle-up-open', size=8, color=color, line=dict(width=2)),
                text=[name], textposition="bottom center", textfont=dict(size=9, color=color),
                name=name, hoverinfo='skip'
            ), row=1, col=1)

    # ===== AI 支撐壓力與訊號標示 (極簡版) =====
    if show_sup_res:
        levels, strengths = find_levels(df_view)
        
        # 畫支撐壓力線 (最多3條)
        for lvl in levels:
            fig.add_hline(y=lvl, line_dash="dot", line_width=2, line_color="#c084fc", opacity=0.8, row=1, col=1)

        # 畫突破訊號 (回踩支撐/跌破壓力)
        signals = detect_breakout(df_view, levels)
        buy_x, buy_y, sell_x, sell_y = [], [], [], []
        for typ, x, y in signals:
            if typ == "buy": buy_x.append(x); buy_y.append(y * 0.98)
            else: sell_x.append(x); sell_y.append(y * 1.02)
                
        if buy_x: fig.add_trace(go.Scatter(x=buy_x, y=buy_y, mode='markers', marker=dict(symbol='triangle-up', size=10, color='#22c55e'), name="支撐買點", hoverinfo='skip'), row=1, col=1)
        if sell_x: fig.add_trace(go.Scatter(x=sell_x, y=sell_y, mode='markers', marker=dict(symbol='triangle-down', size=10, color='#ef4444'), name="壓力賣點", hoverinfo='skip'), row=1, col=1)

        # 歷史高低點
        fig.add_hline(y=highest, line_dash="dash", line_color="#ef4444", row=1, col=1, annotation_text=f"高 {highest:.1f}", annotation_position="top right", annotation_font_color="#ef4444")
        fig.add_hline(y=lowest, line_dash="dash", line_color="#22c55e", row=1, col=1, annotation_text=f"低 {lowest:.1f}", annotation_position="bottom right", annotation_font_color="#22c55e")

    # 傳統吞噬訊號與 AI 模型訊號
    if show_buy_signal:
        ai_x, ai_y, ai_text = [], [], []
        for i in range(len(df_view)):
            row = df_view.iloc[i]
            if row.get('ai_buy', False):
                ai_x.append(x_vals[i])
                ai_y.append(row['Low'] * 0.92)
                ai_text.append(f"🤖{int(row['ai_score'])}")

        if ai_x:
            fig.add_trace(go.Scatter(x=ai_x, y=ai_y, mode='markers+text', marker=dict(symbol='star', size=12, color='#22c55e'), text=ai_text, textposition="bottom center", textfont=dict(color="#22c55e", size=10, weight="bold"), name="AI買點", hoverinfo='skip'), row=1, col=1)

    fig.update_yaxes(range=[lowest * 0.85, highest * 1.15], row=1, col=1)

    # ===== 2. 成交量 =====
    fig.add_trace(go.Bar(x=x_vals, y=df_view['Volume'], marker_color=colors, name="VOL"), row=2, col=1)
    
    # 📌 VOL 最新數值 (左上角)
    vol_last = df_view['Volume'].iloc[-1]
    fig.add_annotation(
        xref="x domain", yref="y2 domain", x=0.01, y=0.95, text=f"VOL: {vol_last:,.0f}", 
        showarrow=False, xanchor='left', yanchor='top',
        font=dict(color=txt_c, size=11, weight="bold"), row=2, col=1, bgcolor="rgba(0,0,0,0.3)"
    )

    # ===== 3. MACD =====
    macd_c = ['#ef4444' if val > 0 else '#22c55e' for val in df_view.get('MACD_Hist', [0]*len(df_view))]
    fig.add_trace(go.Bar(x=x_vals, y=df_view.get('MACD_Hist', 0), marker_color=macd_c, name="MACD柱"), row=3, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df_view.get('MACD', 0), line=dict(color="#3b82f6", width=1), name="DIF"), row=3, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df_view.get('Signal', 0), line=dict(color="#f59e0b", width=1), name="MACD"), row=3, col=1)

    # 📌 MACD 最新數值 (左上角)
    macd_last = df_view['MACD'].iloc[-1] if 'MACD' in df_view else 0
    sig_last = df_view['Signal'].iloc[-1] if 'Signal' in df_view else 0
    osc_last = df_view['MACD_Hist'].iloc[-1] if 'MACD_Hist' in df_view else 0
    fig.add_annotation(
        xref="x domain", yref="y3 domain", x=0.01, y=0.95, 
        text=f"MACD: {macd_last:.2f} | DIF: {sig_last:.2f} | OSC: {osc_last:.2f}", 
        showarrow=False, xanchor='left', yanchor='top',
        font=dict(color=txt_c, size=11, weight="bold"), row=3, col=1, bgcolor="rgba(0,0,0,0.3)"
    )

    # ===== 4. KDJ =====
    if 'K' in df_view:
        fig.add_trace(go.Scatter(x=x_vals, y=df_view.get('K', 50), line=dict(color=line_k, width=1.2), name="K"), row=4, col=1)
        fig.add_trace(go.Scatter(x=x_vals, y=df_view.get('D', 50), line=dict(color=line_d, width=1.2), name="D"), row=4, col=1)
        fig.add_trace(go.Scatter(x=x_vals, y=df_view.get('J', 50), line=dict(color=line_j, width=1.2), name="J"), row=4, col=1)

        # 📌 KDJ 最新數值 (左上角)
        k_last = df_view['K'].iloc[-1]
        d_last = df_view['D'].iloc[-1]
        j_last = df_view['J'].iloc[-1]
        fig.add_annotation(
            xref="x domain", yref="y4 domain", x=0.01, y=0.95, 
            text=f"K: {k_last:.1f} | D: {d_last:.1f} | J: {j_last:.1f}", 
            showarrow=False, xanchor='left', yanchor='top',
            font=dict(color=txt_c, size=11, weight="bold"), row=4, col=1, bgcolor="rgba(0,0,0,0.3)"
        )

    # 📌 防亂跑鎖定設定 (fixedrange=True 鎖死縮放與平移)
    fig.update_xaxes(
        type='category', nticks=15, showgrid=True, gridcolor=grid_c, 
        fixedrange=True, # 🔒 鎖定 X 軸縮放拖曳
        showspikes=True, spikemode="across", spikesnap="cursor", showline=True, spikedash="solid", spikethickness=1
    )
    fig.update_yaxes(showgrid=True, gridcolor=grid_c, fixedrange=True) # 🔒 鎖定 Y 軸縮放拖曳

    fig.update_layout(
        xaxis_rangeslider_visible=False,
        template="plotly_white" if is_light_mode else "plotly_dark",
        height=850,
        margin=dict(l=10, r=10, t=10, b=10),
        paper_bgcolor=bg_c,
        plot_bgcolor=bg_c,
        hovermode="x unified", 
        dragmode=False, # 🔒 徹底關閉拖拽模式，防止按到圖表亂跑
        showlegend=False,
        hoverlabel=dict(font_size=12)
    )

    return fig# charts.py - AI機構級強化版圖表模組（極簡俐落化 + 副圖數值標示版）
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
import numpy as np

# ==============================
# 🔥 AI 主力成本 + 支撐壓力 (Volume Profile)
# ==============================
def find_levels(df, bins=60):
    """改良版：只抓取成交量最大的 Top 3 關鍵點位，避免圖表過度雜亂"""
    price = (df['High'] + df['Low'] + df['Close']) / 3
    volume = df['Volume']
    hist, edges = np.histogram(price, bins=bins, weights=volume)
    
    levels = []
    # 找出所有峰值
    for i in range(1, len(hist)-1):
        if hist[i] > hist[i-1] and hist[i] > hist[i+1]:
            lvl = (edges[i] + edges[i+1]) / 2
            levels.append((lvl, hist[i]))
            
    # 根據成交量(權重)排序，只取前 3 大主力成本區
    levels = sorted(levels, key=lambda x: x[1], reverse=True)[:3]
    
    if not levels:
        return [], []
        
    return [l[0] for l in levels], [l[1] for l in levels]

# ==============================
# 🔥 趨勢判斷（機構邏輯）
# ==============================
def detect_trend(df):
    if '5MA' not in df or '20MA' not in df or '60MA' not in df:
        return "盤整⚠️"
    ma5 = df['5MA'].iloc[-1]
    ma20 = df['20MA'].iloc[-1]
    ma60 = df['60MA'].iloc[-1]

    if ma5 > ma20 > ma60:
        return "多頭🔥"
    elif ma5 < ma20 < ma60:
        return "空頭❄️"
    else:
        return "盤整⚠️"

# ==============================
# 🔥 突破判斷（過濾假突破）
# ==============================
def detect_breakout(df_view, levels):
    signals = []
    for lvl in levels:
        for i in range(1, len(df_view)):
            c_prev = df_view['Close'].iloc[i-1]
            c_curr = df_view['Close'].iloc[i]
            date = df_view.index[i].strftime('%Y-%m-%d')
            
            # 突破條件（站上 + 收盤確認）
            if c_curr > lvl and c_prev <= lvl:
                signals.append(("buy", date, c_curr))
            # 跌破
            if c_curr < lvl and c_prev >= lvl:
                signals.append(("sell", date, c_curr))
    return signals

def compute_ai_signals(df):
    df = df.copy()
    if '20MA' not in df.columns: df['20MA'] = df['Close'].rolling(20).mean()
    if '60MA' not in df.columns: df['60MA'] = df['Close'].rolling(60).mean()

    df['trend_up'] = (df['20MA'] > df['60MA']) & (df['Close'] > df['20MA'])
    df['momentum'] = df['Close'].pct_change(3)
    df['vol_strength'] = df['Volume'] > df['Volume'].rolling(5).mean() * 1.3
    df['breakout'] = df['Close'] > df['High'].rolling(20).max().shift(1)
    df['fake_breakout'] = (df['High'] > df['High'].rolling(20).max().shift(1)) & (df['Close'] < df['20MA'])

    score = (
        df['trend_up'].astype(int) * 2 +
        df['breakout'].astype(int) * 3 +
        df['vol_strength'].astype(int) * 2 +
        (df['momentum'] > 0).astype(int)
    )
    df['ai_score'] = score
    df['ai_buy'] = (score >= 5)
    return df

def draw_professional_chart(df, latest_price, view_days=120, is_light_mode=False, show_buy_signal=True, show_sup_res=True, show_signals=True, buy_dates=None):
    if buy_dates is None: buy_dates = []

    df = compute_ai_signals(df)
    df_view = df.tail(view_days).copy()
    
    x_vals = df_view.index.strftime('%Y-%m-%d')
    colors = ['#ef4444' if row['Close'] >= row['Open'] else '#22c55e' for _, row in df_view.iterrows()]

    line_k, line_d, line_j = ("#3b82f6", "#f59e0b", "#a855f7") if is_light_mode else ("#60a5fa", "#fbbf24", "#c084fc")
    grid_c = "rgba(0,0,0,0.05)" if is_light_mode else "rgba(255,255,255,0.05)"
    bg_c = "#ffffff" if is_light_mode else "#0b1120"
    txt_c = "#333" if is_light_mode else "#e2e8f0"

    fig = make_subplots(rows=4, cols=1, shared_xaxes=True, row_heights=[0.5, 0.15, 0.15, 0.2], vertical_spacing=0.03)

    # ===== 1. K線與均線 =====
    fig.add_trace(go.Candlestick(
        x=x_vals, open=df_view['Open'], high=df_view['High'], low=df_view['Low'], close=df_view['Close'],
        increasing_line_color='#ef4444', decreasing_line_color='#22c55e', name="K線"
    ), row=1, col=1)

    if '5MA' in df_view: fig.add_trace(go.Scatter(x=x_vals, y=df_view['5MA'], line=dict(color='#facc15', width=1.5), name="5T"), row=1, col=1)
    if '10MA' in df_view: fig.add_trace(go.Scatter(x=x_vals, y=df_view['10MA'], line=dict(color='#34d399', width=1.5), name="10T"), row=1, col=1)
    if '20MA' in df_view: fig.add_trace(go.Scatter(x=x_vals, y=df_view['20MA'], line=dict(color='#60a5fa', width=2), name="20T"), row=1, col=1)
    
    highest, lowest = df_view['High'].max(), df_view['Low'].min()

    # 📌 鎖定左上角顯示最新均線數值 (主圖)
    ma_text = f"5T: {df_view['5MA'].iloc[-1]:.1f} | 10T: {df_view['10MA'].iloc[-1]:.1f} | 20T: {df_view['20MA'].iloc[-1]:.1f}"
    fig.add_annotation(
        xref="x domain", yref="y domain", x=0.01, y=0.98, text=ma_text, 
        showarrow=False, xanchor='left', yanchor='top',
        font=dict(color="#facc15", size=12, weight="bold"), row=1, col=1, bgcolor="rgba(0,0,0,0.4)"
    )

    # 📌 趨勢顯示 (右上角)
    trend = detect_trend(df_view)
    fig.add_annotation(
        xref="x domain", yref="y domain", x=0.99, y=0.98, text=f"機構趨勢: {trend}", 
        showarrow=False, xanchor='right', yanchor='top',
        bgcolor="rgba(0,0,0,0.6)", font=dict(color="white", size=12, weight="bold"), row=1, col=1
    )

    fig.add_hline(y=latest_price, line_dash="dash", line_color="#facc15", row=1, col=1, opacity=0.5)

    # 📌 均線扣抵值
    for period, color, name in [(5, '#facc15', '5扣抵'), (10, '#34d399', '10扣抵'), (20, '#60a5fa', '20扣抵')]:
        if len(df_view) >= period:
            idx = -period
            d_date = df_view.index[idx].strftime('%Y-%m-%d')
            d_price = df_view['Low'].iloc[idx] * 0.95
            fig.add_trace(go.Scatter(
                x=[d_date], y=[d_price], mode='markers+text',
                marker=dict(symbol='triangle-up-open', size=8, color=color, line=dict(width=2)),
                text=[name], textposition="bottom center", textfont=dict(size=9, color=color),
                name=name, hoverinfo='skip'
            ), row=1, col=1)

    # ===== AI 支撐壓力與訊號標示 (極簡版) =====
    if show_sup_res:
        levels, strengths = find_levels(df_view)
        
        # 畫支撐壓力線 (最多3條)
        for lvl in levels:
            fig.add_hline(y=lvl, line_dash="dot", line_width=2, line_color="#c084fc", opacity=0.8, row=1, col=1)

        # 畫突破訊號 (回踩支撐/跌破壓力)
        signals = detect_breakout(df_view, levels)
        buy_x, buy_y, sell_x, sell_y = [], [], [], []
        for typ, x, y in signals:
            if typ == "buy": buy_x.append(x); buy_y.append(y * 0.98)
            else: sell_x.append(x); sell_y.append(y * 1.02)
                
        if buy_x: fig.add_trace(go.Scatter(x=buy_x, y=buy_y, mode='markers', marker=dict(symbol='triangle-up', size=10, color='#22c55e'), name="支撐買點", hoverinfo='skip'), row=1, col=1)
        if sell_x: fig.add_trace(go.Scatter(x=sell_x, y=sell_y, mode='markers', marker=dict(symbol='triangle-down', size=10, color='#ef4444'), name="壓力賣點", hoverinfo='skip'), row=1, col=1)

        # 歷史高低點
        fig.add_hline(y=highest, line_dash="dash", line_color="#ef4444", row=1, col=1, annotation_text=f"高 {highest:.1f}", annotation_position="top right", annotation_font_color="#ef4444")
        fig.add_hline(y=lowest, line_dash="dash", line_color="#22c55e", row=1, col=1, annotation_text=f"低 {lowest:.1f}", annotation_position="bottom right", annotation_font_color="#22c55e")

    # 傳統吞噬訊號與 AI 模型訊號
    if show_buy_signal:
        ai_x, ai_y, ai_text = [], [], []
        for i in range(len(df_view)):
            row = df_view.iloc[i]
            if row.get('ai_buy', False):
                ai_x.append(x_vals[i])
                ai_y.append(row['Low'] * 0.92)
                ai_text.append(f"🤖{int(row['ai_score'])}")

        if ai_x:
            fig.add_trace(go.Scatter(x=ai_x, y=ai_y, mode='markers+text', marker=dict(symbol='star', size=12, color='#22c55e'), text=ai_text, textposition="bottom center", textfont=dict(color="#22c55e", size=10, weight="bold"), name="AI買點", hoverinfo='skip'), row=1, col=1)

    fig.update_yaxes(range=[lowest * 0.85, highest * 1.15], row=1, col=1)

    # ===== 2. 成交量 =====
    fig.add_trace(go.Bar(x=x_vals, y=df_view['Volume'], marker_color=colors, name="VOL"), row=2, col=1)
    
    # 📌 VOL 最新數值 (左上角)
    vol_last = df_view['Volume'].iloc[-1]
    fig.add_annotation(
        xref="x domain", yref="y2 domain", x=0.01, y=0.95, text=f"VOL: {vol_last:,.0f}", 
        showarrow=False, xanchor='left', yanchor='top',
        font=dict(color=txt_c, size=11, weight="bold"), row=2, col=1, bgcolor="rgba(0,0,0,0.3)"
    )

    # ===== 3. MACD =====
    macd_c = ['#ef4444' if val > 0 else '#22c55e' for val in df_view.get('MACD_Hist', [0]*len(df_view))]
    fig.add_trace(go.Bar(x=x_vals, y=df_view.get('MACD_Hist', 0), marker_color=macd_c, name="MACD柱"), row=3, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df_view.get('MACD', 0), line=dict(color="#3b82f6", width=1), name="DIF"), row=3, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df_view.get('Signal', 0), line=dict(color="#f59e0b", width=1), name="MACD"), row=3, col=1)

    # 📌 MACD 最新數值 (左上角)
    macd_last = df_view['MACD'].iloc[-1] if 'MACD' in df_view else 0
    sig_last = df_view['Signal'].iloc[-1] if 'Signal' in df_view else 0
    osc_last = df_view['MACD_Hist'].iloc[-1] if 'MACD_Hist' in df_view else 0
    fig.add_annotation(
        xref="x domain", yref="y3 domain", x=0.01, y=0.95, 
        text=f"MACD: {macd_last:.2f} | DIF: {sig_last:.2f} | OSC: {osc_last:.2f}", 
        showarrow=False, xanchor='left', yanchor='top',
        font=dict(color=txt_c, size=11, weight="bold"), row=3, col=1, bgcolor="rgba(0,0,0,0.3)"
    )

    # ===== 4. KDJ =====
    if 'K' in df_view:
        fig.add_trace(go.Scatter(x=x_vals, y=df_view.get('K', 50), line=dict(color=line_k, width=1.2), name="K"), row=4, col=1)
        fig.add_trace(go.Scatter(x=x_vals, y=df_view.get('D', 50), line=dict(color=line_d, width=1.2), name="D"), row=4, col=1)
        fig.add_trace(go.Scatter(x=x_vals, y=df_view.get('J', 50), line=dict(color=line_j, width=1.2), name="J"), row=4, col=1)

        # 📌 KDJ 最新數值 (左上角)
        k_last = df_view['K'].iloc[-1]
        d_last = df_view['D'].iloc[-1]
        j_last = df_view['J'].iloc[-1]
        fig.add_annotation(
            xref="x domain", yref="y4 domain", x=0.01, y=0.95, 
            text=f"K: {k_last:.1f} | D: {d_last:.1f} | J: {j_last:.1f}", 
            showarrow=False, xanchor='left', yanchor='top',
            font=dict(color=txt_c, size=11, weight="bold"), row=4, col=1, bgcolor="rgba(0,0,0,0.3)"
        )

    # 📌 防亂跑鎖定設定 (fixedrange=True 鎖死縮放與平移)
    fig.update_xaxes(
        type='category', nticks=15, showgrid=True, gridcolor=grid_c, 
        fixedrange=True, # 🔒 鎖定 X 軸縮放拖曳
        showspikes=True, spikemode="across", spikesnap="cursor", showline=True, spikedash="solid", spikethickness=1
    )
    fig.update_yaxes(showgrid=True, gridcolor=grid_c, fixedrange=True) # 🔒 鎖定 Y 軸縮放拖曳

    fig.update_layout(
        xaxis_rangeslider_visible=False,
        template="plotly_white" if is_light_mode else "plotly_dark",
        height=850,
        margin=dict(l=10, r=10, t=10, b=10),
        paper_bgcolor=bg_c,
        plot_bgcolor=bg_c,
        hovermode="x unified", 
        dragmode=False, # 🔒 徹底關閉拖拽模式，防止按到圖表亂跑
        showlegend=False,
        hoverlabel=dict(font_size=12)
    )

    return fig