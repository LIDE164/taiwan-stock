# charts.py - AI強化版圖表模組（整合 AI 訊號計算與完美連動版）
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
import numpy as np

def compute_ai_signals(df):
    df = df.copy()

    # 確保所需均線存在
    if '20MA' not in df.columns:
        df['20MA'] = df['Close'].rolling(20).mean()
    if '60MA' not in df.columns:
        df['60MA'] = df['Close'].rolling(60).mean()

    # === 趨勢 ===
    df['trend_up'] = (df['20MA'] > df['60MA']) & (df['Close'] > df['20MA'])

    # === 動能 ===
    df['momentum'] = df['Close'].pct_change(3)

    # === 量能 ===
    df['vol_strength'] = df['Volume'] > df['Volume'].rolling(5).mean() * 1.3

    # === 突破 ===
    df['breakout'] = df['Close'] > df['High'].rolling(20).max().shift(1)

    # === 假突破（關鍵🔥）===
    df['fake_breakout'] = (
        (df['High'] > df['High'].rolling(20).max().shift(1)) &
        (df['Close'] < df['20MA'])
    )

    # === AI分數 ===
    score = (
        df['trend_up'].astype(int) * 2 +
        df['breakout'].astype(int) * 3 +
        df['vol_strength'].astype(int) * 2 +
        (df['momentum'] > 0).astype(int)
    )

    df['ai_score'] = score

    # === AI買點 ===
    df['ai_buy'] = (score >= 5)

    return df

def draw_professional_chart(
    df,
    latest_price,
    view_days=120,
    is_light_mode=False,
    show_buy_signal=True,
    show_sup_res=True,
    show_signals=True,
    buy_dates=None
):
    if buy_dates is None:
        buy_dates = []

    # 1. 預處理 AI 訊號
    df = compute_ai_signals(df)

    # 2. 擷取所需天數
    df_view = df.tail(view_days).copy()
    
    # 轉為字串以隱藏假日缺口
    x_vals = df_view.index.strftime('%Y-%m-%d')
    colors = ['#ef4444' if row['Close'] >= row['Open'] else '#22c55e' for _, row in df_view.iterrows()]

    # 動態佈景主題色
    line_k, line_d, line_j = ("#3b82f6", "#f59e0b", "#a855f7") if is_light_mode else ("#60a5fa", "#fbbf24", "#c084fc")
    grid_c = "rgba(0,0,0,0.05)" if is_light_mode else "rgba(255,255,255,0.05)"
    bg_c = "#ffffff" if is_light_mode else "#0b1120"

    # 設定子圖 (K線, 量, MACD, KDJ)
    fig = make_subplots(
        rows=4, cols=1, shared_xaxes=True,
        row_heights=[0.5, 0.15, 0.15, 0.2],
        vertical_spacing=0.03
    )

    # ===== 1. K線與均線 =====
    fig.add_trace(go.Candlestick(
        x=x_vals, open=df_view['Open'], high=df_view['High'], low=df_view['Low'], close=df_view['Close'],
        increasing_line_color='#ef4444', decreasing_line_color='#22c55e', name="K線"
    ), row=1, col=1)

    if '5MA' in df_view:
        fig.add_trace(go.Scatter(x=x_vals, y=df_view['5MA'], line=dict(color='#facc15', width=1.5), name="5T"), row=1, col=1)
    if '10MA' in df_view:
        fig.add_trace(go.Scatter(x=x_vals, y=df_view['10MA'], line=dict(color='#34d399', width=1.5), name="10T"), row=1, col=1)
    if '20MA' in df_view:
        fig.add_trace(go.Scatter(x=x_vals, y=df_view['20MA'], line=dict(color='#60a5fa', width=2), name="20T"), row=1, col=1)
    
    highest = df_view['High'].max()
    lowest = df_view['Low'].min()

    # 圖表左上角的均線數值標籤
    ma_text = f"5T:{df_view['5MA'].iloc[-1]:.1f} | 10T:{df_view['10MA'].iloc[-1]:.1f} | 20T:{df_view['20MA'].iloc[-1]:.1f}"
    if len(x_vals) > 0:
        fig.add_annotation(
            x=x_vals[0], y=highest * 1.05, text=ma_text, 
            showarrow=False, xanchor='left', yanchor='bottom',
            font=dict(color="#facc15", size=11, weight="bold"), row=1, col=1
        )

    fig.add_hline(y=latest_price, line_dash="dash", line_color="#facc15", row=1, col=1, opacity=0.5)

    # ===== 訊號與 AI 標示 =====
    re_x, re_y, be_x, be_y = [], [], [], []
    ai_x, ai_y, ai_text = [], [], []
    fake_x, fake_y = [], []
    break_x, break_y = [], []

    for i in range(len(df_view)):
        row = df_view.iloc[i]
        date_str = x_vals[i]
        
        # 尋找原始 df 的 index (為了看前一天算吞噬)
        idx = df.index.get_loc(df_view.index[i])
        if isinstance(idx, slice) or isinstance(idx, np.ndarray): continue
        if idx > 0:
            p = df.iloc[idx-1]
            # 紅吞
            if p['Open'] > p['Close'] and row['Close'] > row['Open'] and row['Close'] > p['Open'] and row['Open'] < p['Close']:
                re_x.append(date_str); re_y.append(row['Low'] * 0.96)
            # 黑吞
            if p['Close'] > p['Open'] and row['Open'] > row['Close'] and row['Open'] > p['Close'] and row['Close'] < p['Open']:
                be_x.append(date_str); be_y.append(row['High'] * 1.04)

        # 🤖 AI 買點
        if row.get('ai_buy', False):
            ai_x.append(date_str)
            ai_y.append(row['Low'] * 0.92)  # 稍微往下放避免重疊
            ai_text.append(f"🤖{int(row['ai_score'])}")

        # 🔥 真突破
        if row.get('breakout', False):
            break_x.append(date_str)
            break_y.append(row['Low'] * 0.94)

        # ❌ 假突破
        if row.get('fake_breakout', False):
            fake_x.append(date_str)
            fake_y.append(row['High'] * 1.02)

    # 繪製吞噬訊號
    if show_signals:
        if re_x: fig.add_trace(go.Scatter(x=re_x, y=re_y, mode='text', text=["紅吞"]*len(re_x), textposition="bottom center", textfont=dict(color="#ef4444", size=11, weight="bold"), name="紅吞", hoverinfo='skip'), row=1, col=1)
        if be_x: fig.add_trace(go.Scatter(x=be_x, y=be_y, mode='text', text=["黑吞"]*len(be_x), textposition="top center", textfont=dict(color="#22c55e", size=11, weight="bold"), name="黑吞", hoverinfo='skip'), row=1, col=1)

    # 繪製 AI 新增訊號與回測買點
    if show_buy_signal:
        # 歷史回測買點保留 (確保 test.py 的回測功能也能顯示)
        buy_x, buy_y = [], []
        for d in buy_dates:
            if d in df_view.index:
                buy_x.append(d.strftime('%Y-%m-%d')); buy_y.append(df_view.loc[d, 'Low'] * 0.88)
        if buy_x: fig.add_trace(go.Scatter(x=buy_x, y=buy_y, mode='markers+text', marker=dict(symbol='triangle-up', size=10, color='#3b82f6'), text=["買"]*len(buy_x), textposition="bottom center", textfont=dict(color="#3b82f6", size=10, weight="bold"), name="回測買點", hoverinfo='skip'), row=1, col=1)

        # 🤖 AI買點
        if ai_x:
            fig.add_trace(go.Scatter(
                x=ai_x, y=ai_y, mode='markers+text',
                marker=dict(symbol='star', size=13, color='#22c55e'),
                text=ai_text, textposition="bottom center", textfont=dict(color="#22c55e", size=10, weight="bold"),
                name="AI買點", hoverinfo='skip'
            ), row=1, col=1)

        # 🔥 突破
        if break_x:
            fig.add_trace(go.Scatter(
                x=break_x, y=break_y, mode='markers',
                marker=dict(symbol='triangle-up', size=11, color='#f59e0b'),
                name="突破", hoverinfo='skip'
            ), row=1, col=1)

        # ❌ 假突破
        if fake_x:
            fig.add_trace(go.Scatter(
                x=fake_x, y=fake_y, mode='markers',
                marker=dict(symbol='x', size=11, color='#ef4444', line=dict(width=2, color='#ef4444')),
                name="假突破", hoverinfo='skip'
            ), row=1, col=1)

    # 支撐壓力線與勝率標籤
    if show_sup_res:
        fig.add_hline(y=highest, line_dash="dash", line_color="#ef4444", row=1, col=1, 
                      annotation_text=f"壓力 {highest:.2f}", annotation_position="top right", annotation_font_color="#ef4444")
        fig.add_hline(y=lowest, line_dash="dash", line_color="#22c55e", row=1, col=1, 
                      annotation_text=f"支撐 {lowest:.2f}", annotation_position="bottom right", annotation_font_color="#22c55e")

        # === AI勝率文字標籤 ===
        win_rate = df['ai_buy'].mean() * 100
        if len(x_vals) > 0:
            last_date = x_vals[-1]
            fig.add_annotation(
                x=last_date, y=highest * 1.05, text=f"🤖 AI勝率 {win_rate:.1f}%",
                showarrow=False, bgcolor="rgba(34,197,94,0.2)", bordercolor="#22c55e",
                font=dict(color="#22c55e" if is_light_mode else "#f8fafc", size=11),
                xanchor="right", yanchor="bottom", row=1, col=1
            )
            
    # 拉開 Y 軸空間給文字
    fig.update_yaxes(range=[lowest * 0.85, highest * 1.15], row=1, col=1)

    # ===== 2. 成交量 =====
    fig.add_trace(go.Bar(x=x_vals, y=df_view['Volume'], marker_color=colors, name="VOL"), row=2, col=1)

    # ===== 3. MACD =====
    macd_c = ['#ef4444' if val > 0 else '#22c55e' for val in df_view.get('MACD_Hist', [0]*len(df_view))]
    fig.add_trace(go.Bar(x=x_vals, y=df_view.get('MACD_Hist', 0), marker_color=macd_c, name="MACD柱"), row=3, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df_view.get('MACD', 0), line=dict(color="#3b82f6", width=1), name="DIF"), row=3, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df_view.get('Signal', 0), line=dict(color="#f59e0b", width=1), name="MACD"), row=3, col=1)

    # ===== 4. KDJ =====
    if 'K' in df_view:
        fig.add_trace(go.Scatter(x=x_vals, y=df_view.get('K', 50), line=dict(color=line_k, width=1.2), name="K"), row=4, col=1)
        fig.add_trace(go.Scatter(x=x_vals, y=df_view.get('D', 50), line=dict(color=line_d, width=1.2), name="D"), row=4, col=1)
        fig.add_trace(go.Scatter(x=x_vals, y=df_view.get('J', 50), line=dict(color=line_j, width=1.2), name="J"), row=4, col=1)

    # ===== 完美連動配置 =====
    fig.update_xaxes(
        type='category', nticks=15, showgrid=True, gridcolor=grid_c, fixedrange=False,
        showspikes=True, spikemode="across", spikesnap="cursor", showline=True, spikedash="solid", spikethickness=1
    )
    fig.update_yaxes(showgrid=True, gridcolor=grid_c, fixedrange=False)

    fig.update_layout(
        xaxis_rangeslider_visible=False,
        template="plotly_white" if is_light_mode else "plotly_dark",
        height=850,
        margin=dict(l=10, r=10, t=10, b=10),
        paper_bgcolor=bg_c,
        plot_bgcolor=bg_c,
        hovermode="x unified", 
        dragmode="zoom",
        showlegend=False,
        hoverlabel=dict(font_size=12)
    )

    return fig