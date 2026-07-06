# charts.py - AI強化版圖表模組（連動優化版）
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

    # 確保資料格式
    df_view = df.tail(view_days).copy()
    
    # 轉為字串以隱藏假日缺口 (Category 類型)
    x_vals = df_view.index.strftime('%Y-%m-%d')
    colors = ['#ef4444' if row['Close'] >= row['Open'] else '#22c55e' for _, row in df_view.iterrows()]

    # 動態佈景主題色
    line_k, line_d, line_j = ("#3b82f6", "#f59e0b", "#a855f7") if is_light_mode else ("#60a5fa", "#fbbf24", "#c084fc")
    grid_c = "rgba(0,0,0,0.05)" if is_light_mode else "rgba(255,255,255,0.05)"
    bg_c = "#ffffff" if is_light_mode else "#0b1120"

    # 設定子圖，shared_xaxes=True 強制共享 X 軸
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
        fig.add_trace(go.Scatter(x=x_vals, y=df_view['5MA'], line=dict(color='#facc15', width=1.5), name="5MA"), row=1, col=1)
    if '10MA' in df_view:
        fig.add_trace(go.Scatter(x=x_vals, y=df_view['10MA'], line=dict(color='#34d399', width=1.5), name="10MA"), row=1, col=1)
    if '20MA' in df_view:
        fig.add_trace(go.Scatter(x=x_vals, y=df_view['20MA'], line=dict(color='#60a5fa', width=2), name="20MA"), row=1, col=1)
    if '60MA' in df_view:
        fig.add_trace(go.Scatter(x=x_vals, y=df_view['60MA'], line=dict(color='#a855f7', width=2), name="60MA"), row=1, col=1)

    fig.add_hline(y=latest_price, line_dash="dash", line_color="#facc15", row=1, col=1, opacity=0.5)

    # ===== 訊號標示 =====
    re_x, re_y, be_x, be_y = [], [], [], []
    for date, row in df_view.iterrows():
        idx = df.index.get_loc(date)
        # 處理如果 index 有重複的極端情況
        if isinstance(idx, slice) or isinstance(idx, np.ndarray): continue
        if idx > 0:
            p = df.iloc[idx-1]
            if p['Open'] > p['Close'] and row['Close'] > row['Open'] and row['Close'] > p['Open'] and row['Open'] < p['Close']:
                re_x.append(date.strftime('%Y-%m-%d')); re_y.append(row['Low'] * 0.98)
            if p['Close'] > p['Open'] and row['Open'] > row['Close'] and row['Open'] > p['Close'] and row['Close'] < p['Open']:
                be_x.append(date.strftime('%Y-%m-%d')); be_y.append(row['High'] * 1.02)

    if show_signals:
        if re_x: fig.add_trace(go.Scatter(x=re_x, y=re_y, mode='text', text=["紅吞"]*len(re_x), textposition="bottom center", textfont=dict(color="#ef4444", size=11, weight="bold"), name="紅吞", hoverinfo='skip'), row=1, col=1)
        if be_x: fig.add_trace(go.Scatter(x=be_x, y=be_y, mode='text', text=["黑吞"]*len(be_x), textposition="top center", textfont=dict(color="#22c55e", size=11, weight="bold"), name="黑吞", hoverinfo='skip'), row=1, col=1)

    if show_buy_signal and buy_dates:
        buy_x, buy_y = [], []
        for d in buy_dates:
            if d in df_view.index:
                buy_x.append(d.strftime('%Y-%m-%d')); buy_y.append(df_view.loc[d, 'Low'] * 0.95)
        if buy_x: fig.add_trace(go.Scatter(x=buy_x, y=buy_y, mode='markers+text', marker=dict(symbol='triangle-up', size=12, color='#34d399'), text=["買"]*len(buy_x), textposition="bottom center", textfont=dict(color="#34d399", size=11, weight="bold"), name="歷史買點", hoverinfo='skip'), row=1, col=1)

    if show_sup_res:
        fig.add_hline(y=df_view['High'].max(), line_dash="dot", line_color="#ef4444", row=1, col=1, annotation_text=f"壓力 {df_view['High'].max():.1f}")
        fig.add_hline(y=df_view['Low'].min(), line_dash="dot", line_color="#22c55e", row=1, col=1, annotation_text=f"支撐 {df_view['Low'].min():.1f}")

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
    # 強制所有子圖的 x 軸同步 (spikemode=across 讓十字線垂直貫穿所有指標圖)
    fig.update_xaxes(
        type='category', nticks=15, showgrid=True, gridcolor=grid_c, fixedrange=False,
        showspikes=True, spikemode="across", spikesnap="cursor", showline=True, spikedash="solid", spikethickness=1
    )
    fig.update_yaxes(showgrid=True, gridcolor=grid_c, fixedrange=False)

    fig.update_layout(
        xaxis_rangeslider_visible=False,
        template="plotly_white" if is_light_mode else "plotly_dark",
        height=800,
        margin=dict(l=10, r=10, t=10, b=10),
        paper_bgcolor=bg_c,
        plot_bgcolor=bg_c,
        hovermode="x unified", # 開啟 x軸全局聯合顯示
        dragmode="zoom",
        showlegend=False,
        hoverlabel=dict(font_size=12)
    )

    return fig# charts.py - AI強化版圖表模組（連動優化版）
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

    # 確保資料格式
    df_view = df.tail(view_days).copy()
    
    # 轉為字串以隱藏假日缺口 (Category 類型)
    x_vals = df_view.index.strftime('%Y-%m-%d')
    colors = ['#ef4444' if row['Close'] >= row['Open'] else '#22c55e' for _, row in df_view.iterrows()]

    # 動態佈景主題色
    line_k, line_d, line_j = ("#3b82f6", "#f59e0b", "#a855f7") if is_light_mode else ("#60a5fa", "#fbbf24", "#c084fc")
    grid_c = "rgba(0,0,0,0.05)" if is_light_mode else "rgba(255,255,255,0.05)"
    bg_c = "#ffffff" if is_light_mode else "#0b1120"

    # 設定子圖，shared_xaxes=True 強制共享 X 軸
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
        fig.add_trace(go.Scatter(x=x_vals, y=df_view['5MA'], line=dict(color='#facc15', width=1.5), name="5MA"), row=1, col=1)
    if '10MA' in df_view:
        fig.add_trace(go.Scatter(x=x_vals, y=df_view['10MA'], line=dict(color='#34d399', width=1.5), name="10MA"), row=1, col=1)
    if '20MA' in df_view:
        fig.add_trace(go.Scatter(x=x_vals, y=df_view['20MA'], line=dict(color='#60a5fa', width=2), name="20MA"), row=1, col=1)
    if '60MA' in df_view:
        fig.add_trace(go.Scatter(x=x_vals, y=df_view['60MA'], line=dict(color='#a855f7', width=2), name="60MA"), row=1, col=1)

    fig.add_hline(y=latest_price, line_dash="dash", line_color="#facc15", row=1, col=1, opacity=0.5)

    # ===== 訊號標示 =====
    re_x, re_y, be_x, be_y = [], [], [], []
    for date, row in df_view.iterrows():
        idx = df.index.get_loc(date)
        # 處理如果 index 有重複的極端情況
        if isinstance(idx, slice) or isinstance(idx, np.ndarray): continue
        if idx > 0:
            p = df.iloc[idx-1]
            if p['Open'] > p['Close'] and row['Close'] > row['Open'] and row['Close'] > p['Open'] and row['Open'] < p['Close']:
                re_x.append(date.strftime('%Y-%m-%d')); re_y.append(row['Low'] * 0.98)
            if p['Close'] > p['Open'] and row['Open'] > row['Close'] and row['Open'] > p['Close'] and row['Close'] < p['Open']:
                be_x.append(date.strftime('%Y-%m-%d')); be_y.append(row['High'] * 1.02)

    if show_signals:
        if re_x: fig.add_trace(go.Scatter(x=re_x, y=re_y, mode='text', text=["紅吞"]*len(re_x), textposition="bottom center", textfont=dict(color="#ef4444", size=11, weight="bold"), name="紅吞", hoverinfo='skip'), row=1, col=1)
        if be_x: fig.add_trace(go.Scatter(x=be_x, y=be_y, mode='text', text=["黑吞"]*len(be_x), textposition="top center", textfont=dict(color="#22c55e", size=11, weight="bold"), name="黑吞", hoverinfo='skip'), row=1, col=1)

    if show_buy_signal and buy_dates:
        buy_x, buy_y = [], []
        for d in buy_dates:
            if d in df_view.index:
                buy_x.append(d.strftime('%Y-%m-%d')); buy_y.append(df_view.loc[d, 'Low'] * 0.95)
        if buy_x: fig.add_trace(go.Scatter(x=buy_x, y=buy_y, mode='markers+text', marker=dict(symbol='triangle-up', size=12, color='#34d399'), text=["買"]*len(buy_x), textposition="bottom center", textfont=dict(color="#34d399", size=11, weight="bold"), name="歷史買點", hoverinfo='skip'), row=1, col=1)

    if show_sup_res:
        fig.add_hline(y=df_view['High'].max(), line_dash="dot", line_color="#ef4444", row=1, col=1, annotation_text=f"壓力 {df_view['High'].max():.1f}")
        fig.add_hline(y=df_view['Low'].min(), line_dash="dot", line_color="#22c55e", row=1, col=1, annotation_text=f"支撐 {df_view['Low'].min():.1f}")

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
    # 強制所有子圖的 x 軸同步 (spikemode=across 讓十字線垂直貫穿所有指標圖)
    fig.update_xaxes(
        type='category', nticks=15, showgrid=True, gridcolor=grid_c, fixedrange=False,
        showspikes=True, spikemode="across", spikesnap="cursor", showline=True, spikedash="solid", spikethickness=1
    )
    fig.update_yaxes(showgrid=True, gridcolor=grid_c, fixedrange=False)

    fig.update_layout(
        xaxis_rangeslider_visible=False,
        template="plotly_white" if is_light_mode else "plotly_dark",
        height=800,
        margin=dict(l=10, r=10, t=10, b=10),
        paper_bgcolor=bg_c,
        plot_bgcolor=bg_c,
        hovermode="x unified", # 開啟 x軸全局聯合顯示
        dragmode="zoom",
        showlegend=False,
        hoverlabel=dict(font_size=12)
    )

    return fig