def get_decision_score(data, fund_data, inst_data=None, with_reason=False):
    sc, rs = 0, []

    adx = data.get('ADX', 0)
    roc_20 = data.get('ROC_20', 0)
    is_trending = adx >= 25 

    def add(score, text=None):
        nonlocal sc
        sc += score
        if with_reason and text:
            rs.append(text)

    if data.get('訊號', False): 
        add(3 if is_trending else 1,
            f"訊號成立 ADX={adx:.1f}")

    if data['收盤價'] <= data.get('BB_DN', 0) * 1.02:
        add(2, "布林下軌支撐")

    if data.get('BIAS', 0) < -5:
        add(1, "負乖離")

    if roc_20 > 10:
        add(2, f"強勢股 {roc_20:.2f}%")
    elif roc_20 < -5:
        add(-2, f"弱勢股 {roc_20:.2f}%")

    if data.get('MoM', 0) > 0 and data.get('YoY', 0) > 0:
        add(3, "營收雙增")

    try:
        eps_f = float(str(fund_data.get('EPS', '0')).replace(',', ''))
    except:
        eps_f = 0

    if eps_f > 0:
        add(2, "EPS正")

    if data.get('成交量', 0) > data.get('5日均量', 0) * 1.1:
        add(2, "量增")
    else:
        add(-1, "量不足")

    if data.get('MACD柱', 0) > data.get('前日MACD柱', -999):
        add(2, "MACD好轉")
    else:
        add(-3, "MACD轉弱")

    if data.get('紅吞', False):
        add(4 if is_trending else 1, "紅吞")

    if data.get('黑吞', False):
        add(-3, "黑吞")

    if data.get('回測有撐', False):
        add(2, "回測支撐")

    if data.get('反彈遇壓', False):
        add(-2, "反彈壓力")

    if data.get('J值', 50) >= 80:
        add(-3, "過熱")

    if data['收盤價'] >= data.get('BB_UP', 9999) * 0.98:
        add(-2, "上軌壓力")

    if data.get('BIAS', 0) > 7:
        add(-2, "正乖離過大")

    if data['收盤價'] < data.get('20MA', 0):
        add(-2, "跌破月線")

    if eps_f < 0:
        add(-1, "EPS負")

    final_score = max(5, min(99, int(50 + sc * 3)))

    if final_score >= 60:
        label = "🟢 強勢買進"
    elif final_score >= 45:
        label = "🟡 偏多觀察"
    else:
        label = "⚪ 忽略"

    feature = "一般狀態"
    if data.get('紅吞', False):
        feature = "🔥 紅吞表態"
    elif data.get('回測有撐', False):
        feature = "💪 回檔有撐"

    return (final_score, label, rs, feature) if with_reason else (final_score, label, feature)