# charts.py - AI機構級強化版圖表模組（字體防重疊/100分制/扣抵趨勢版）
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
import numpy as np

def find_levels(df, bins=60):
    price = (df['High'] + df['Low'] + df['Close']) / 3
    volume = df['Volume']
    hist, edges = np.histogram(price, bins=bins, weights=volume)
    
    levels = []
    for i in range(1, len(hist)-1):
        if hist[i] > hist[i-1] and hist[i] > hist[i+1]:
            lvl = (edges[i] + edges[i+1]) / 2
            levels.append((lvl, hist[i]))
            
    levels = sorted(levels, key=lambda x: x[1], reverse=True)[:3]
    if not levels: return [], []
    return [l[0] for l in levels], [l[1] for l in levels]

def detect_trend(df):
    if '5MA' not in df or '20MA' not in df or '60MA' not in df: return "盤整⚠️"
    ma5, ma20, ma60 = df['5MA'].iloc[-1], df['20MA'].iloc[-1], df['60MA'].iloc[-1]
    if ma5 > ma20 > ma60: return "多頭🔥"
    elif ma5 < ma20 < ma60: return "空頭❄️"
    else: return "盤整⚠️"

def detect_breakout(df_view, levels):
    signals = []
    for lvl in levels:
        for i in range(1, len(df_view)):
            c_prev = df_view['Close'].iloc[i-1]
            c_curr = df_view['Close'].iloc[i]
            date = df_view.index[i].strftime('%Y-%m-%d')
            low_p = df_view['Low'].iloc[i]
            high_p = df_view['High'].iloc[i]
            
            if c_curr > lvl and c_prev <= lvl: signals.append(("buy", date, low_p))
            if c_curr < lvl and c_prev >= lvl: signals.append(("sell", date, high_p))
    return signals

def compute_ai_signals(df):
    df = df.copy()
    if '20MA' not in df.columns: df['20MA'] = df['Close'].rolling(20).mean()
    if '60MA' not in df.columns: df['60MA'] = df['Close'].rolling(60).mean()

    df['trend_up'] = (df['20MA'] > df['60MA']) & (df['Close'] > df['20MA'])
    df['momentum'] = df['Close'].pct_change(3)
    df['vol_strength'] = df['Volume'] > df['Volume'].rolling(5).mean() * 1.3
    df['breakout'] = df['Close'] > df['High'].rolling(20).max().shift(1)

    # 將 AI 分數嚴格映射到 100 分制
    score = (
        df['trend_up'].astype(int) * 30 +
        df['breakout'].astype(int) * 30 +
        df['vol_strength'].astype(int) * 20 +
        (df['momentum'] > 0).astype(int) * 20
    )
    df['ai_score'] = score
    df['ai_buy'] = (score >= 60) # 60分以上及格視為強勢買點
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

    # 📌 鎖定左上角顯示「現價 + 均線數值」
    ma_text = f"現價: {latest_price:.1f} | 5T: {df_view['5MA'].iloc[-1]:.1f} | 10T: {df_view['10MA'].iloc[-1]:.1f} | 20T: {df_view['20MA'].iloc[-1]:.1f}"
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

    # 📌 5日均線單一扣抵值趨勢 (加上 ↗️ ↘️)
    if len(df_view) >= 5:
        idx = -5
        d_date = df_view.index[idx].strftime('%Y-%m-%d')
        # 距離拉開避免重疊
        d_price = df_view['Low'].iloc[idx] * 0.85 
        
        deduct_close = df_view['Close'].iloc[idx]
        curr_close = df_view['Close'].iloc[-1]
        trend_dir = "↗️" if curr_close >= deduct_close else "↘️"
        
        fig.add_trace(go.Scatter(
            x=[d_date], y=[d_price], mode='text',
            text=[f"5{trend_dir}"], textposition="bottom center", textfont=dict(size=12, color='#facc15', weight='bold'),
            name="5扣抵", hoverinfo='skip'
        ), row=1, col=1)

    # ===== 訊號與 AI 標示 (智能間距防重疊) =====
    re_x, re_y, be_x, be_y = [], [], [], []
    for i in range(len(df_view)):
        row = df_view.iloc[i]
        date_str = x_vals[i]
        idx = df.index.get_loc(df_view.index[i])
        if isinstance(idx, slice) or isinstance(idx, np.ndarray): continue
        if idx > 0:
            p = df.iloc[idx-1]
            if p['Open'] > p['Close'] and row['Close'] > row['Open'] and row['Close'] > p['Open'] and row['Open'] < p['Close']:
                re_x.append(date_str); re_y.append(row['Low'] * 0.98) # 第一層偏移
            if p['Close'] > p['Open'] and row['Open'] > row['Close'] and row['Open'] > p['Close'] and row['Close'] < p['Open']:
                be_x.append(date_str); be_y.append(row['High'] * 1.02) # 第一層偏移

    if show_signals:
        if re_x: fig.add_trace(go.Scatter(x=re_x, y=re_y, mode='text', text=["紅吞"]*len(re_x), textposition="bottom center", textfont=dict(color="#ef4444", size=11, weight="bold"), name="紅吞", hoverinfo='skip'), row=1, col=1)
        if be_x: fig.add_trace(go.Scatter(x=be_x, y=be_y, mode='text', text=["黑吞"]*len(be_x), textposition="top center", textfont=dict(color="#22c55e", size=11, weight="bold"), name="黑吞", hoverinfo='skip'), row=1, col=1)

    # 支撐壓力與文字訊號
    if show_sup_res:
        levels, strengths = find_levels(df_view)
        for lvl in levels:
            fig.add_hline(y=lvl, line_dash="dot", line_width=2, line_color="#c084fc", opacity=0.8, row=1, col=1)

        signals = detect_breakout(df_view, levels)
        buy_x, buy_y, sell_x, sell_y = [], [], [], []
        for typ, x, ref_p in signals:
            if typ == "buy": 
                buy_x.append(x); buy_y.append(ref_p * 0.95) # 第二層偏移
            else: 
                sell_x.append(x); sell_y.append(ref_p * 1.05) # 第二層偏移
                
        if buy_x: fig.add_trace(go.Scatter(x=buy_x, y=buy_y, mode='text', text=["撐"]*len(buy_x), textposition="bottom center", textfont=dict(color='#22c55e', size=13, weight='bold'), name="支撐買點", hoverinfo='skip'), row=1, col=1)
        if sell_x: fig.add_trace(go.Scatter(x=sell_x, y=sell_y, mode='text', text=["壓"]*len(sell_x), textposition="top center", textfont=dict(color='#ef4444', size=13, weight='bold'), name="壓力賣點", hoverinfo='skip'), row=1, col=1)

        fig.add_hline(y=highest, line_dash="dash", line_color="#ef4444", row=1, col=1, annotation_text=f"高 {highest:.1f}", annotation_position="top right", annotation_font_color="#ef4444")
        fig.add_hline(y=lowest, line_dash="dash", line_color="#22c55e", row=1, col=1, annotation_text=f"低 {lowest:.1f}", annotation_position="bottom right", annotation_font_color="#22c55e")

    # AI 滿分 100 模型與藍色箭頭標示
    if show_buy_signal:
        ai_x, ai_y, ai_text = [], [], []
        for i in range(len(df_view)):
            row = df_view.iloc[i]
            if row.get('ai_buy', False):
                ai_x.append(x_vals[i])
                ai_y.append(row['Low'] * 0.91) # 第三層偏移，保證絕對不會重疊
                ai_text.append(f"{int(row['ai_score'])}")

        if ai_x:
            fig.add_trace(go.Scatter(
                x=ai_x, y=ai_y, mode='markers+text', 
                marker=dict(symbol='triangle-up', size=12, color='#3b82f6'), # 藍色🔼
                text=ai_text, textposition="bottom center", 
                textfont=dict(color="#3b82f6", size=10, weight="bold"), 
                name="AI買點", hoverinfo='skip'
            ), row=1, col=1)

    fig.update_yaxes(range=[lowest * 0.8, highest * 1.15], row=1, col=1) # 拉開Y軸範圍確保底部的字顯示得出來

    # ===== 2. 成交量 =====
    fig.add_trace(go.Bar(x=x_vals, y=df_view['Volume'], marker_color=colors, opacity=0.85, name="VOL"), row=2, col=1)
    vol_last = df_view['Volume'].iloc[-1]
    fig.add_annotation(
        xref="x domain", yref="y2 domain", x=0.01, y=0.95, text=f"VOL: {vol_last:,.0f}", 
        showarrow=False, xanchor='left', yanchor='top',
        font=dict(color=txt_c, size=11, weight="bold"), row=2, col=1, bgcolor="rgba(0,0,0,0.3)"
    )

    # ===== 3. MACD =====
    macd_c = ['#ef4444' if val > 0 else '#22c55e' for val in df_view.get('MACD_Hist', [0]*len(df_view))]
    fig.add_trace(go.Bar(x=x_vals, y=df_view.get('MACD_Hist', 0), marker_color=macd_c, opacity=0.85, name="MACD柱"), row=3, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df_view.get('MACD', 0), line=dict(color="#3b82f6", width=1), name="DIF"), row=3, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df_view.get('Signal', 0), line=dict(color="#f59e0b", width=1), name="MACD"), row=3, col=1)
    
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
        
        k_last = df_view['K'].iloc[-1]
        d_last = df_view['D'].iloc[-1]
        j_last = df_view['J'].iloc[-1]
        fig.add_annotation(
            xref="x domain", yref="y4 domain", x=0.01, y=0.95, 
            text=f"K: {k_last:.1f} | D: {d_last:.1f} | J: {j_last:.1f}", 
            showarrow=False, xanchor='left', yanchor='top',
            font=dict(color=txt_c, size=11, weight="bold"), row=4, col=1, bgcolor="rgba(0,0,0,0.3)"
        )

    fig.update_xaxes(
        type='category', nticks=15, showgrid=True, gridcolor=grid_c, fixedrange=True, 
        showspikes=True, spikemode="across", spikesnap="cursor", showline=True, spikedash="solid", spikethickness=1
    )
    fig.update_yaxes(showgrid=True, gridcolor=grid_c, fixedrange=True)

    fig.update_layout(
        xaxis_rangeslider_visible=False,
        template="plotly_white" if is_light_mode else "plotly_dark",
        height=850, margin=dict(l=10, r=10, t=10, b=10),
        paper_bgcolor=bg_c, plot_bgcolor=bg_c,
        hovermode="x unified", dragmode=False, showlegend=False,
        hoverlabel=dict(font_size=12)
    )

    return fig