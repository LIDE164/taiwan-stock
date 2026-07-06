# charts.py - AI強化版圖表模組（可直接用）
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
import numpy as np

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

    df = df.copy()

    # ===== 防呆：補欄位 =====
    for col in ['5MA','20MA','60MA']:
        if col not in df:
            df[col] = df['Close'].rolling(int(col.replace('MA',''))).mean()

    for col in ['MACD','Signal','MACD_Hist','K','D','J']:
        if col not in df:
            df[col] = 0

    df_view = df.tail(view_days)

    x_vals = df_view.index.strftime('%Y-%m-%d')

    colors = ['#ef4444' if c >= o else '#22c55e'
              for c, o in zip(df_view['Close'], df_view['Open'])]

    fig = make_subplots(
        rows=4, cols=1, shared_xaxes=True,
        row_heights=[0.5,0.15,0.15,0.2],
        vertical_spacing=0.03
    )

    # ===== K線 =====
    fig.add_trace(go.Candlestick(
        x=x_vals,
        open=df_view['Open'],
        high=df_view['High'],
        low=df_view['Low'],
        close=df_view['Close'],
        increasing_line_color='#ef4444',
        decreasing_line_color='#22c55e'
    ), row=1, col=1)

    # ===== 均線 =====
    fig.add_trace(go.Scatter(x=x_vals, y=df_view['5MA'], line=dict(color='#facc15', width=1.5)))
    fig.add_trace(go.Scatter(x=x_vals, y=df_view['20MA'], line=dict(color='cyan', width=1.5)))
    fig.add_trace(go.Scatter(x=x_vals, y=df_view['60MA'], line=dict(color='#a855f7', width=1.5)))

    # ===== 現價線 =====
    fig.add_hline(y=latest_price, line_dash="dash", line_color="#facc15", opacity=0.6)

    # ===== K棒訊號 =====
    re_x, re_y, be_x, be_y = [], [], [], []

    for i in range(1, len(df_view)):
        p = df_view.iloc[i-1]
        c = df_view.iloc[i]

        date = df_view.index[i].strftime('%Y-%m-%d')

        # 紅吞
        if p['Close'] < p['Open'] and c['Close'] > c['Open'] and c['Close'] > p['Open']:
            re_x.append(date)
            re_y.append(c['Low'] * 0.97)

        # 黑吞
        if p['Close'] > p['Open'] and c['Close'] < c['Open'] and c['Open'] > p['Close']:
            be_x.append(date)
            be_y.append(c['High'] * 1.03)

    if show_signals:
        fig.add_trace(go.Scatter(x=re_x, y=re_y, mode='text',
                                text=['紅吞']*len(re_x),
                                textfont=dict(color='red', size=10)))

        fig.add_trace(go.Scatter(x=be_x, y=be_y, mode='text',
                                text=['黑吞']*len(be_x),
                                textfont=dict(color='lime', size=10)))

    # ===== 買點 =====
    if show_buy_signal and buy_dates:
        bx, by = [], []
        for d in buy_dates:
            if d in df_view.index:
                bx.append(d.strftime('%Y-%m-%d'))
                by.append(df_view.loc[d]['Low'] * 0.92)

        fig.add_trace(go.Scatter(
            x=bx,
            y=by,
            mode='markers+text',
            marker=dict(size=12, color='#00ffcc', symbol='triangle-up'),
            text=['買']*len(bx),
            textposition="bottom center"
        ))

    # ===== 支撐壓力 =====
    if show_sup_res:
        high = df_view['High'].max()
        low = df_view['Low'].min()

        fig.add_hline(y=high, line_dash="dot", line_color="red")
        fig.add_hline(y=low, line_dash="dot", line_color="green")

    # ===== AI 趨勢 =====
    trend = "盤整"
    if df['5MA'].iloc[-1] > df['20MA'].iloc[-1] > df['60MA'].iloc[-1]:
        trend = "多頭🔥"
    elif df['5MA'].iloc[-1] < df['20MA'].iloc[-1] < df['60MA'].iloc[-1]:
        trend = "空頭❄️"

    fig.add_annotation(
        x=x_vals[-1],
        y=df_view['High'].max(),
        text=f"AI趨勢: {trend}",
        showarrow=False,
        font=dict(size=12, color="white"),
        bgcolor="rgba(0,0,0,0.6)"
    )

    # ===== 成交量 =====
    fig.add_trace(go.Bar(x=x_vals, y=df_view['Volume'], marker_color=colors), row=2, col=1)

    # ===== MACD =====
    fig.add_trace(go.Bar(x=x_vals, y=df_view['MACD_Hist']), row=3, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df_view['MACD']), row=3, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df_view['Signal']), row=3, col=1)

    # ===== KDJ =====
    fig.add_trace(go.Scatter(x=x_vals, y=df_view['K']), row=4, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df_view['D']), row=4, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df_view['J']), row=4, col=1)

    # ===== 版面 =====
    fig.update_layout(
        template="plotly_dark",
        height=850,
        showlegend=False,
        hovermode="x unified",
        dragmode="zoom"
    )

    return fig