# charts.py - AI強化K線圖 (含AI買點 / 突破 / 假突破 / 支撐壓力)
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
import numpy as np

# === 🤖 AI訊號計算 ===
def compute_ai_signals(df):
    df = df.copy()

    df['trend_up'] = (df['20MA'] > df['60MA']) & (df['Close'] > df['20MA'])
    df['momentum'] = df['Close'].pct_change(3)
    df['vol_strength'] = df['Volume'] > df['Volume'].rolling(5).mean() * 1.3

    df['breakout'] = df['Close'] > df['High'].rolling(20).max().shift(1)

    df['fake_breakout'] = (
        (df['High'] > df['High'].rolling(20).max().shift(1)) &
        (df['Close'] < df['20MA'])
    )

    score = (
        df['trend_up'].astype(int) * 2 +
        df['breakout'].astype(int) * 3 +
        df['vol_strength'].astype(int) * 2 +
        (df['momentum'] > 0).astype(int)
    )

    df['ai_score'] = score
    df['ai_buy'] = score >= 5

    return df


def draw_professional_chart(df, latest_price, view_days, is_light_mode,
                           show_buy_signal=False, show_sup_res=False,
                           show_signals=True, buy_dates=[]):

    # === 🤖 AI加入 ===
    df = compute_ai_signals(df)

    df_view = df.tail(view_days).copy()
    df_view = df_view.reset_index()

    x_vals = df_view['index'].dt.strftime('%Y-%m-%d')

    colors = ['#ef4444' if row['Close'] >= row['Open'] else '#22c55e' for _, row in df_view.iterrows()]

    line_k, line_d, line_j = ("#3b82f6", "#f59e0b", "#a855f7") if is_light_mode else ("#60a5fa", "#fbbf24", "#c084fc")
    grid_c = "rgba(0,0,0,0.05)" if is_light_mode else "rgba(255,255,255,0.05)"
    bg_c = "#ffffff" if is_light_mode else "#0b1120"

    fig = make_subplots(rows=4, cols=1, shared_xaxes=True,
                        row_heights=[0.5, 0.15, 0.15, 0.2],
                        vertical_spacing=0.03)

    # === K線 ===
    fig.add_trace(go.Candlestick(
        x=x_vals,
        open=df_view['Open'],
        high=df_view['High'],
        low=df_view['Low'],
        close=df_view['Close'],
        increasing_line_color='#ef4444',
        decreasing_line_color='#22c55e'
    ), row=1, col=1)

    fig.add_trace(go.Scatter(x=x_vals, y=df_view['5MA'], line=dict(color='#facc15', width=1.5)), row=1, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df_view['20MA'], line=dict(color='cyan', width=1.5)), row=1, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df_view['60MA'], line=dict(color='#a855f7', width=1.5)), row=1, col=1)

    # === 原始K棒訊號（修正過條件🔥）===
    re_x, re_y, be_x, be_y = [], [], [], []
    sup_x, sup_y, res_x, res_y = [], [], [], []

    for i in range(1, len(df_view)):
        row = df_view.iloc[i]
        p = df_view.iloc[i-1]
        date = x_vals[i]

        # 紅吞
        if p['Open'] > p['Close'] and row['Close'] > row['Open'] and row['Close'] > p['Open'] and row['Open'] < p['Close']:
            re_x.append(date)
            re_y.append(row['Low'] * 0.995)

        # 黑吞
        if p['Close'] > p['Open'] and row['Open'] > row['Close'] and row['Open'] > p['Close'] and row['Close'] < p['Open']:
            be_x.append(date)
            be_y.append(row['High'] * 1.005)

        total = max(row['High'] - row['Low'], 0.001)
        body = abs(row['Close'] - row['Open'])
        lower = min(row['Open'], row['Close']) - row['Low']
        upper = row['High'] - max(row['Open'], row['Close'])

        if lower > body and lower / total > 0.3:
            sup_x.append(date)
            sup_y.append(row['Low'] * 0.995)

        if upper > body and upper / total > 0.3:
            res_x.append(date)
            res_y.append(row['High'] * 1.005)

    if show_signals:
        if re_x:
            fig.add_trace(go.Scatter(x=re_x, y=re_y, mode='text', text=["紅吞"]*len(re_x), textfont=dict(color="#ef4444")), row=1, col=1)
        if be_x:
            fig.add_trace(go.Scatter(x=be_x, y=be_y, mode='text', text=["黑吞"]*len(be_x), textfont=dict(color="#22c55e")), row=1, col=1)
        if sup_x:
            fig.add_trace(go.Scatter(x=sup_x, y=sup_y, mode='text', text=["撐"]*len(sup_x), textfont=dict(color="#facc15")), row=1, col=1)
        if res_x:
            fig.add_trace(go.Scatter(x=res_x, y=res_y, mode='text', text=["壓"]*len(res_x), textfont=dict(color="#60a5fa")), row=1, col=1)

    # === 🤖 AI訊號 ===
    ai_x, ai_y, ai_text = [], [], []
    break_x, break_y = [], []
    fake_x, fake_y = [], []

    for i in range(len(df_view)):
        row = df_view.iloc[i]
        date = x_vals[i]

        if row.get('ai_buy', False):
            ai_x.append(date)
            ai_y.append(row['Low'] * 0.97)
            ai_text.append(f"🤖{int(row['ai_score'])}")

        if row.get('breakout', False):
            break_x.append(date)
            break_y.append(row['High'] * 1.02)

        if row.get('fake_breakout', False):
            fake_x.append(date)
            fake_y.append(row['High'] * 1.02)

    if ai_x:
        fig.add_trace(go.Scatter(
            x=ai_x, y=ai_y,
            mode='markers+text',
            marker=dict(symbol='star', size=12, color='#22c55e'),
            text=ai_text,
            textposition="bottom center"
        ), row=1, col=1)

    if break_x:
        fig.add_trace(go.Scatter(
            x=break_x, y=break_y,
            mode='markers',
            marker=dict(symbol='triangle-up', size=10, color='#f59e0b')
        ), row=1, col=1)

    if fake_x:
        fig.add_trace(go.Scatter(
            x=fake_x, y=fake_y,
            mode='markers',
            marker=dict(symbol='x', size=10, color='#ef4444')
        ), row=1, col=1)

    # === AI勝率 ===
    win_rate = df['ai_buy'].mean() * 100

    fig.add_annotation(
        x=x_vals.iloc[-1],
        y=latest_price * 1.05,
        text=f"🤖勝率 {win_rate:.1f}%",
        showarrow=False,
        bgcolor="rgba(34,197,94,0.2)"
    )

    # === 成交量 ===
    fig.add_trace(go.Bar(x=x_vals, y=df_view['Volume'], marker_color=colors), row=2, col=1)

    # === MACD ===
    fig.add_trace(go.Bar(x=x_vals, y=df_view.get('MACD_Hist', 0)), row=3, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df_view.get('MACD', 0)), row=3, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df_view.get('Signal', 0)), row=3, col=1)

    # === KDJ ===
    fig.add_trace(go.Scatter(x=x_vals, y=df_view.get('K', 50)), row=4, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df_view.get('D', 50)), row=4, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df_view.get('J', 50)), row=4, col=1)

    fig.update_layout(
        template="plotly_white" if is_light_mode else "plotly_dark",
        height=800,
        hovermode="x unified",
        showlegend=False
    )

    return fig