<!-- ... existing code ... -->
    if show_sup_res:
        highest_price = df_view['High'].max()
        lowest_price = df_view['Low'].min()
        fig.add_hline(y=highest_price, line_dash="dash", line_color="#ff3333", row=1, col=1, annotation_text=f"壓力 {highest_price:.2f}", annotation_position="top left", annotation_font=dict(size=12, color="#ff3333"))
        fig.add_hline(y=lowest_price, line_dash="dash", line_color="#00cc00", row=1, col=1, annotation_text=f"支撐 {lowest_price:.2f}", annotation_position="bottom left", annotation_font=dict(size=12, color="#00cc00"))
    
    re_x, re_y, re_text = [], [], []
    be_x, be_y, be_text = [], [], []
    sup_x, sup_y, sup_text = [], [], []
    res_x, res_y, res_text = [], [], []
    deduct_up_x, deduct_up_y, deduct_up_text = [], [], []
    deduct_down_x, deduct_down_y, deduct_down_text = [], [], []
    
    start_pos = len(df) - len(df_view)
    
    for i, date in enumerate(df_view.index):
        pos = start_pos + i
        if pos >= 1:
            t = df.iloc[pos]
            p = df.iloc[pos-1]
            
            t_open, t_close, t_high, t_low = t['Open'], t['Close'], t['High'], t['Low']
            p_open, p_close = p['Open'], p['Close']
            
            is_red = (p_open > p_close) and (t_close > t_open) and (t_close > p_open) and (t_open < p_close)
            is_black = (p_close > p_open) and (t_open > t_close) and (t_open > p_close) and (t_close < p_open)
            
            # 🚀 距離全部大幅拉近，不再壓縮 K 線
            if is_red:
                re_x.append(date.strftime('%Y-%m-%d'))
                re_y.append(t_low * 0.98) 
                re_text.append("<b>吞</b>")
            if is_black:
                be_x.append(date.strftime('%Y-%m-%d'))
                be_y.append(t_high * 1.02) 
                be_text.append("<b>吞</b>")
            
            total_range = t_high - t_low
            if total_range == 0: total_range = 0.001
            upper_shadow = t_high - max(t_open, t_close)
            lower_shadow = min(t_open, t_close) - t_low
            body = abs(t_close - t_open)

            is_support_pullback = (lower_shadow > body * 1.5) and (lower_shadow / total_range > 0.4) and (t_low < p_close) and (t_close >= min(p_open, p_close))
            ma_resistance = min(t['5MA'], t['10MA']) 
            is_resistance_rejection = (upper_shadow > body * 1.5) and (upper_shadow / total_range > 0.4) and (t_high >= ma_resistance) and (t_close < ma_resistance)

            if is_support_pullback:
                sup_x.append(date.strftime('%Y-%m-%d'))
                sup_y.append(t_low * 0.96) 
                sup_text.append("<b>撐</b>")
            if is_resistance_rejection:
                res_x.append(date.strftime('%Y-%m-%d'))
                res_y.append(t_high * 1.04) 
                res_text.append("<b>壓</b>")

            if pos >= 5:
                curr_deduct = df.iloc[pos - 5]['Close']
                curr_up = (t_close >= t['5MA']) and (t_close > curr_deduct)
                curr_down = (t_close < t['5MA']) and (t_close < curr_deduct)
                
                prev_up = False
                prev_down = False
                if pos >= 6:
                    prev_deduct = df.iloc[pos - 6]['Close']
                    prev_up = (p_close >= p['5MA']) and (p_close > prev_deduct)
                    prev_down = (p_close < p['5MA']) and (p_close < prev_deduct)
                
                # 🚀 捨棄 Emoji，改用乾淨的純文本箭頭 (⬈, ⬊)
                if curr_up and not prev_up:
                    deduct_up_x.append(date.strftime('%Y-%m-%d'))
                    deduct_up_y.append(t_low * 0.94) 
                    deduct_up_text.append("<b>⬈扣</b>")
                if curr_down and not prev_down:
                    deduct_down_x.append(date.strftime('%Y-%m-%d'))
                    deduct_down_y.append(t_high * 1.06)
                    deduct_down_text.append("<b>⬊扣</b>")

    if show_signals:
        # 字體微縮為 12，看起來更細緻
        if re_x: fig.add_trace(go.Scatter(x=re_x, y=re_y, mode='text', text=re_text, textposition="bottom center", textfont=dict(color="#ff3333", size=12), name="紅吞", hoverinfo='skip'), row=1, col=1)
        if be_x: fig.add_trace(go.Scatter(x=be_x, y=be_y, mode='text', text=be_text, textposition="top center", textfont=dict(color="#00cc00", size=12), name="黑吞", hoverinfo='skip'), row=1, col=1)
        if sup_x: fig.add_trace(go.Scatter(x=sup_x, y=sup_y, mode='text', text=sup_text, textposition="bottom center", textfont=dict(color="#ff9900" if is_light_mode else "#ffcc00", size=12), name="回測有撐", hoverinfo='skip'), row=1, col=1)
        if res_x: fig.add_trace(go.Scatter(x=res_x, y=res_y, mode='text', text=res_text, textposition="top center", textfont=dict(color="#0066cc" if is_light_mode else "#00ccff", size=12), name="反彈遇壓", hoverinfo='skip'), row=1, col=1)
        if deduct_up_x: fig.add_trace(go.Scatter(x=deduct_up_x, y=deduct_up_y, mode='text', text=deduct_up_text, textposition="bottom center", textfont=dict(color="#ff3333", size=12), name="扣低上彎", hoverinfo='skip'), row=1, col=1)
        if deduct_down_x: fig.add_trace(go.Scatter(x=deduct_down_x, y=deduct_down_y, mode='text', text=deduct_down_text, textposition="top center", textfont=dict(color="#00cc00", size=12), name="扣高下彎", hoverinfo='skip'), row=1, col=1)

    if show_buy_signal and f_data:
        buy_x, buy_y = [], []
        for i in range(len(df_view)):
            current_date = df_view.index[i]
            pos = df.index.get_loc(current_date)
            sub_df = df.iloc[:pos+1]
            if len(sub_df) >= 5:
                t_data = analyze_today(sub_df, ticker_name, inst_data=None) 
                if t_data and t_data['Score'] >= 2:
                    buy_x.append(current_date.strftime('%Y-%m-%d'))
                    buy_y.append(df_view['Low'].iloc[i] * 0.92) # 大幅拉近買進訊號距離
        if buy_x:
            # 🚀 拿掉文字，只保留純粹的藍色三角形 (size 微調為 12)
            fig.add_trace(go.Scatter(x=buy_x, y=buy_y, mode='markers', marker=dict(symbol='triangle-up', size=12, color='#0066cc' if is_light_mode else '#00ffcc'), name="買進訊號", hoverinfo='x'), row=1, col=1)
            
    fig.add_trace(go.Bar(x=x_vals, y=df_view['Volume'], marker_color=colors, name="VOL"), row=2, col=1)
<!-- ... existing code ... -->
