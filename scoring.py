# scoring.py - 100 分量化決策核心


def _num(value, default=0.0):
    try:
        if value is None:
            return default
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return default


def get_decision_score(data, fund_data, inst_data=None, mode="post", with_reason=True):
    sc = 0
    rs = []

    data = data or {}
    fund_data = fund_data or {}
    adx = _num(data.get("ADX"))
    roc_20 = _num(data.get("ROC_20"))
    is_trending = adx >= 25
    macd_penalty = -1 if mode == "realtime" else -3

    def add(score, text):
        nonlocal sc
        sc += score
        if with_reason and text:
            sign = "+" if score > 0 else ""
            rs.append(f"{text} ({sign}{score}分)")

    close = _num(data.get("收盤價"))
    ma5 = _num(data.get("5MA"), close)
    ma20 = _num(data.get("20MA"), close)
    bb_dn = _num(data.get("BB_DN"), close)
    bb_up = _num(data.get("BB_UP"), close)
    bias = _num(data.get("BIAS"))
    volume = _num(data.get("成交量"))
    volume_5d = _num(data.get("5日均量"))
    macd_hist = _num(data.get("MACD柱"))
    prev_macd_hist = _num(data.get("前日MACD柱"), -999)
    j_value = _num(data.get("J值"), 50)
    rsi = _num(data.get("RSI"), 50)
    momentum_score = _num(data.get("Momentum_Score"), 50)
    whale_net = _num(data.get("Whale_Net"), 0)
    confidence = _num(data.get("Confidence"), 100)
    tomorrow_turn_price = _num(data.get("明日5MA扣抵價"), 0)
    ma5_up = bool(data.get("5MA已上彎", data.get("5日線即將上彎", False)))
    signal_conflict = str(data.get("Signal_Conflict", "低"))
    entry_pattern = str(data.get("Entry_Pattern", ""))
    vix = _num(fund_data.get("VIX"), 0)
    mom = _num(data.get("MoM"))
    yoy = _num(data.get("YoY"))

    if data.get("訊號", False):
        if is_trending:
            add(3, f"✅ 穩在月線上且動能充沛 (ADX:{adx:.1f} 趨勢明確)")
        else:
            add(1, f"⚠️ 穩在月線上 (但 ADX:{adx:.1f} 偏盤整，動能稍弱)")

    if close and bb_dn and close <= bb_dn * 1.02:
        add(2, "✅ 觸及布林下軌支撐")
    if bias < -5:
        add(1, "✅ 負乖離過大，具反彈空間")

    if roc_20 > 10:
        add(2, f"🔥 近月漲幅 {roc_20:.2f}% 表現亮眼")
    elif roc_20 < -5:
        add(-2, f"🩸 近月跌幅 {roc_20:.2f}% 表現弱勢，避免接刀")

    if mom > 0 and yoy > 0:
        add(3, f"🔥 月營收雙增 (MoM: {mom:.2f}%, YoY: {yoy:.2f}%)")
    elif yoy > 15:
        add(2, f"✅ 月營收年增達 {yoy:.2f}%，營運動能強")

    eps_f = _num(fund_data.get("EPS"), 0.0)
    if eps_f > 0:
        add(2, "✅ EPS 為正，具獲利支撐")
    elif eps_f < 0:
        add(-1, "⚠️ 基本面虧損")

    if data.get("Volume_Confirmed") is False:
        add(-1, "⚠️ 盤中量能尚未確認，避免假量追高")
    elif volume_5d > 0 and volume > volume_5d * 1.1:
        add(2, "✅ 量能放大，具主力進場特徵")
    else:
        add(-1, "⚠️ 量能未明顯放大")

    if macd_hist > prev_macd_hist:
        add(2, "✅ MACD 綠柱收斂或紅柱放大")
    else:
        add(macd_penalty, "⚠️ MACD 空方動能擴大")

    if data.get("紅吞", False):
        add(4 if is_trending else 1, "🔥 出現紅吞反轉型態")
    if data.get("黑吞", False):
        add(-3, "🩸 出現黑吞反轉型態")
    if data.get("回測有撐", False):
        add(2, "🔥 帶量長下影線，回測支撐成功")
    if data.get("反彈遇壓", False):
        add(-2, "🩸 反彈遇壓，留意上方賣壓")

    if close >= ma5 and ma5_up:
        add(1, "🔥 5MA 已上彎，短線結構轉強")
    if close < ma5 and not ma5_up:
        add(-1, "⚠️ 短均線仍有壓力")
    if tomorrow_turn_price > 0 and close < tomorrow_turn_price:
        add(-1, f"⚠️ 明日 5MA 扣抵價 {tomorrow_turn_price:.2f}，短線轉強門檻仍高")

    if momentum_score >= 75:
        add(2, f"✅ 趨勢品質佳 ({momentum_score:.0f}/100)")
    elif momentum_score <= 35:
        add(-2, f"⚠️ 趨勢品質偏弱 ({momentum_score:.0f}/100)")

    if whale_net > 1000:
        add(2, f"✅ 法人三日淨買 {whale_net:,.0f} 張")
    elif whale_net < -1000:
        add(-2, f"⚠️ 法人三日淨賣 {abs(whale_net):,.0f} 張")

    if j_value >= 80:
        add(-3, "⚠️ KDJ 高檔過熱")
    elif j_value <= 20 and close >= ma20:
        add(1, "✅ KDJ 低檔但仍守月線")
    if rsi >= 75:
        add(-2, "⚠️ RSI 過熱")
    elif 45 <= rsi <= 65 and close >= ma20:
        add(1, "✅ RSI 位於健康動能區")
    if close and bb_up and close >= bb_up * 0.98:
        add(-2, "⚠️ 接近布林上軌壓力")
    if bias > 7:
        add(-2, "⚠️ 正乖離過大")
    if close and ma20 and close < ma20:
        add(-2, "⚠️ 跌破月線支撐")
    if vix >= 25:
        add(-2, f"⚠️ VIX {vix:.1f} 偏高，系統性風險升溫")
    if confidence < 60:
        add(-2, f"⚠️ 資料信心偏低 ({confidence:.0f}%)，分數僅供保守參考")
    elif confidence < 80:
        add(-1, f"⚠️ 資料信心中等 ({confidence:.0f}%)，需留意缺失資料")
    final_score = max(5, min(99, int(50 + sc * 3)))

    if final_score >= 60:
        label = "🟢 強勢買進"
    elif final_score >= 45:
        label = "🟡 偏多觀察"
    else:
        label = "⚪ 忽略"

    feature = "一般狀態"
    if data.get("紅吞", False):
        feature = "🔥 紅吞表態"
    elif data.get("回測有撐", False):
        feature = "💪 回檔有撐"

    return final_score, label, rs if with_reason else [], feature
