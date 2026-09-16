# scoring.py - 100 分量化決策核心

import math

REQUIRED_SCORE_FIELDS = (
    "收盤價",
    "5MA",
    "20MA",
    "BB_DN",
    "BB_UP",
    "成交量",
    "5日均量",
    "MACD柱",
    "前日MACD柱",
    "J值",
    "RSI",
    "Momentum_Score",
    "Confidence",
)


# The score is a ranking signal, not a probability. A smooth calibration keeps
# unusually strong evidence distinguishable without turning every good setup
# into the same hard-capped 99.
_SCORE_MIDPOINT = 50
_SCORE_SPAN = 49
_SCORE_EVIDENCE_SCALE = 18.0


def _combine_correlated_scores(values, secondary_weight=0.35, limit=8.0):
    """Combine related evidence with diminishing credit after the first signal.

    Indicators such as RSI, KDJ, Bollinger position and bias often describe the
    same price condition. The strongest observation receives full weight;
    additional observations on the same side receive partial confirmation
    weight. Positive and negative observations are reduced independently so a
    genuine conflict is still reflected in the result.
    """
    finite_values = [
        float(value)
        for value in values
        if value is not None and math.isfinite(float(value)) and float(value) != 0
    ]

    def reduce_side(side):
        if not side:
            return 0.0
        strongest = max(side)
        return strongest + secondary_weight * (sum(side) - strongest)

    positive = reduce_side([value for value in finite_values if value > 0])
    negative = reduce_side([-value for value in finite_values if value < 0])
    return max(-limit, min(limit, positive - negative))


def _calibrate_score(evidence):
    """Map accumulated evidence to 5-99 while reducing upper-tail saturation."""
    calibrated = _SCORE_MIDPOINT + _SCORE_SPAN * math.tanh(
        float(evidence) / _SCORE_EVIDENCE_SCALE
    )
    return max(5, min(99, round(calibrated)))


def decision_label(final_score):
    """Describe ranking strength without presenting a score as a buy order."""
    score = _num(final_score)
    if score >= 75:
        return "🟢 強勢候選"
    if score >= 65:
        return "🟡 偏多候選"
    if score >= 60:
        return "🟡 一般觀察"
    return "⚪ 條件不足"


def _num(value, default=0.0):
    try:
        if value is None:
            return default
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return default


def _optional_num(value):
    """Return a finite number, or None when a data point was not reported."""
    try:
        if value is None or isinstance(value, bool):
            return None
        parsed = float(str(value).replace(",", ""))
        return parsed if math.isfinite(parsed) else None
    except (TypeError, ValueError):
        return None


def _optional_bool(value):
    """Parse an optional flag without treating a missing/string value as truthy."""
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "是", "有"}:
        return True
    if normalized in {"0", "false", "no", "n", "否", "無"}:
        return False
    return None


def _first_present(mapping, keys):
    if not isinstance(mapping, dict):
        return None
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def _institutional_risk_inputs(data, inst_data=None):
    """Read optional institutional-risk facts without manufacturing zeroes.

    Callers may provide precomputed aggregate fields, or pass recent daily rows.
    Daily rows are expected newest first. A foreign/trust aggregate is inferred
    only when every inspected row reports both components.
    """
    rows = inst_data
    if rows is None and isinstance(data, dict):
        rows = data.get("Institutional_Rows")
    rows = [row for row in (rows or []) if isinstance(row, dict)][:3]

    sell_streak = _optional_num(_first_present(data, (
        "Institutional_Sell_Streak",
        "Institutional_Consecutive_Sell_Days",
        "Whale_Sell_Streak",
    )))
    foreign_net = _optional_num(_first_present(data, ("Foreign_Net", "Foreign_Net_3D")))
    trust_net = _optional_num(_first_present(data, ("Trust_Net", "Trust_Net_3D")))
    divergence = _optional_bool(_first_present(data, (
        "Institutional_Flow_Divergence",
        "Foreign_Trust_Divergence",
    )))

    if sell_streak is None and rows:
        known_streak = 0
        first_total_known = False
        for row in rows:
            total = _optional_num(_first_present(row, ("單日合計(張)", "total")))
            if total is None:
                break
            first_total_known = True
            if total >= 0:
                break
            known_streak += 1
        if first_total_known:
            sell_streak = float(known_streak)

    if rows and (foreign_net is None or trust_net is None):
        foreign_values = []
        trust_values = []
        components_complete = True
        for row in rows:
            foreign = _optional_num(_first_present(row, ("外資(張)", "foreign")))
            trust = _optional_num(_first_present(row, ("投信(張)", "trust")))
            if foreign is None or trust is None:
                components_complete = False
                break
            foreign_values.append(foreign)
            trust_values.append(trust)
        if components_complete and foreign_values:
            if foreign_net is None:
                foreign_net = sum(foreign_values)
            if trust_net is None:
                trust_net = sum(trust_values)

    if divergence is None and foreign_net is not None and trust_net is not None:
        divergence = foreign_net * trust_net < 0

    return sell_streak, foreign_net, trust_net, divergence


def _has_required_score_data(data):
    if not isinstance(data, dict):
        return False
    for field in REQUIRED_SCORE_FIELDS:
        if field not in data or data[field] is None:
            return False
        try:
            value = float(str(data[field]).replace(",", ""))
        except (TypeError, ValueError):
            return False
        if not math.isfinite(value):
            return False
        if field in {"收盤價", "5MA", "20MA", "BB_DN", "BB_UP"} and value <= 0:
            return False
    return True


def get_decision_score(data, fund_data, inst_data=None, mode="post", with_reason=True):
    if not _has_required_score_data(data):
        reasons = ["⚠️ 必要技術資料不足，未產生量化評分"] if with_reason else []
        return 0, "⚪ 資料不足", reasons, "資料不足"

    sc = 0.0
    correlated: dict[str, list[float]] = {
        "trend": [],
        "reversal": [],
        "breakout": [],
        "overheat": [],
    }
    rs = []

    data = data or {}
    fund_data = fund_data or {}
    adx = _num(data.get("ADX"))
    roc_20 = _num(data.get("ROC_20"))
    is_trending = adx >= 25
    strong_trend = is_trending and roc_20 > 5
    macd_penalty = -1 if mode == "realtime" else -3

    def add(score, text, group=None):
        nonlocal sc
        if group:
            correlated[group].append(score)
        else:
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
    vol_ratio = _num(data.get("Est_Vol_Ratio"), volume / volume_5d if volume_5d > 0 else 0)
    macd_hist = _num(data.get("MACD柱"))
    prev_macd_hist = _num(data.get("前日MACD柱"), -999)
    j_value = _num(data.get("J值"), 50)
    rsi = _num(data.get("RSI"), 50)
    momentum_score = _num(data.get("Momentum_Score"), 50)
    confidence = _num(data.get("Confidence"), 100)
    tomorrow_turn_price = _num(data.get("明日5MA扣抵價"), 0)
    ma5_up = bool(data.get("5MA已上彎", data.get("5日線即將上彎", False)))
    signal_conflict = str(data.get("Signal_Conflict", "低"))
    entry_pattern = str(data.get("Entry_Pattern", ""))
    vix = _num(fund_data.get("VIX"), 0)
    # Prefer the provider payload so a missing fundamental value does not turn
    # into the zero produced by technical-input normalization.
    mom = _optional_num(fund_data.get("MoM")) if "MoM" in fund_data else _optional_num(data.get("MoM"))
    yoy = _optional_num(fund_data.get("YoY")) if "YoY" in fund_data else _optional_num(data.get("YoY"))

    if data.get("訊號", False):
        if is_trending:
            add(3, f"✅ 穩在月線上且動能充沛 (ADX:{adx:.1f} 趨勢明確)", "trend")
        else:
            add(1, f"⚠️ 穩在月線上 (但 ADX:{adx:.1f} 偏盤整，動能稍弱)", "trend")

    if close and bb_dn and close <= bb_dn * 1.02:
        add(2, "✅ 觸及布林下軌支撐", "reversal")
    if bias < -5:
        add(1, "✅ 負乖離過大，具反彈空間", "reversal")

    if roc_20 > 10:
        add(2, f"🔥 近月漲幅 {roc_20:.2f}% 表現亮眼", "trend")
    elif roc_20 < -5:
        add(-2, f"🩸 近月跌幅 {roc_20:.2f}% 表現弱勢，避免接刀", "trend")

    if mom is not None and yoy is not None and mom > 0 and yoy > 0:
        add(3, f"🔥 月營收雙增 (MoM: {mom:.2f}%, YoY: {yoy:.2f}%)")
    elif mom is not None and yoy is not None and mom < 0 and yoy < 0:
        add(-3, f"⚠️ 月營收雙減 (MoM: {mom:.2f}%, YoY: {yoy:.2f}%)")
    else:
        if yoy is not None:
            if yoy > 15:
                add(2, f"✅ 月營收年增達 {yoy:.2f}%，營運動能強")
            elif yoy < -15:
                add(-2, f"⚠️ 月營收年減達 {abs(yoy):.2f}%，營運動能轉弱")
            elif yoy < 0:
                add(-1, f"⚠️ 月營收年減 {abs(yoy):.2f}%，基本面動能偏弱")
        if mom is not None and mom < 0:
            add(-1, f"⚠️ 月營收月減 {abs(mom):.2f}%，留意短期動能降溫")

    # 🛡️ 大盤環境過濾器 (Market Regime Filter)
    twii_close = _num(fund_data.get("TWII_Close"), 0.0)
    twii_ma20 = _num(fund_data.get("TWII_MA20"), 0.0)
    twii_ma60 = _num(fund_data.get("TWII_MA60"), 0.0)
    if twii_close > 0 and twii_ma20 > 0 and twii_ma60 > 0:
        if twii_close < twii_ma20 and twii_close < twii_ma60:
            add(-8, f"🚨 大盤空頭 (加權指數 {twii_close:.1f} 同時跌破月線 {twii_ma20:.1f} 與季線 {twii_ma60:.1f}，保守觀望)")
        elif twii_close < twii_ma20 or twii_close < twii_ma60:
            add(-3, f"⚠️ 大盤震盪 (加權指數 {twii_close:.1f} 跌破月線或季線，留意修正風險)")
        else:
            add(2, f"🛡️ 大盤強多 (加權指數 {twii_close:.1f} 站上月線 {twii_ma20:.1f} 與季線 {twii_ma60:.1f}，適合積極操作)")

    eps_f = _optional_num(fund_data.get("EPS"))
    if eps_f is not None and eps_f > 0:
        add(2, "✅ EPS 為正，具獲利支撐")
    elif eps_f is not None and eps_f < 0:
        if strong_trend:
            add(-2, "⚠️ EPS 為負；即使技術趨勢強，仍保留虧損風險")
        else:
            add(-2, "⚠️ EPS 為負，缺乏獲利支撐")

    financial_risk = str(
        fund_data.get("Financial_Risk_Level")
        or data.get("Financial_Risk_Level")
        or ""
    ).strip().lower()
    operating_margin = _optional_num(
        fund_data.get("Financial_Operating_Margin")
        if "Financial_Operating_Margin" in fund_data
        else data.get("Financial_Operating_Margin")
    )
    net_margin = _optional_num(
        fund_data.get("Financial_Net_Margin")
        if "Financial_Net_Margin" in fund_data
        else data.get("Financial_Net_Margin")
    )
    if financial_risk == "high":
        add(-4, "⚠️ 最新季度財報品質屬高風險，不以技術強勢掩蓋")
    elif financial_risk == "medium":
        add(-2, "⚠️ 最新季度財報品質偏弱，需保守評估")
    elif financial_risk == "low" and operating_margin is not None and net_margin is not None:
        add(1, "✅ 最新季度獲利與償債品質未觸發風險警示")

    is_breakout = data.get("Box_Breakout", False) or data.get("紅吞", False)
    
    if data.get("Volume_Confirmed") is False:
        add(-1, "⚠️ 盤中量能尚未確認，避免假量追高")
    elif vol_ratio >= 4:
        if is_breakout:
            add(1, f"🔥 突破伴隨爆量 {vol_ratio:.1f} 倍，視為關鍵換手", "breakout")
        else:
            add(-4, f"⚠️ 量能爆量 {vol_ratio:.1f} 倍，隔日賣壓風險升高", "overheat")
    elif vol_ratio > 3:
        if is_breakout:
            add(2, f"🔥 突破出量 {vol_ratio:.1f} 倍，動能強勁", "breakout")
        else:
            add(-2, f"⚠️ 量能過熱 {vol_ratio:.1f} 倍，避免追高", "overheat")
    elif 1.2 <= vol_ratio <= 2.5:
        add(2, f"✅ 量能溫和放大 {vol_ratio:.1f} 倍")
    elif volume_5d > 0 and volume > volume_5d * 1.1:
        add(2, "✅ 成交量高於 5 日均量")
    else:
        add(-1, "⚠️ 量能未明顯放大")

    if macd_hist > prev_macd_hist:
        add(2, "✅ MACD 綠柱收斂或紅柱放大", "trend")
    else:
        add(macd_penalty, "⚠️ MACD 空方動能擴大", "trend")

    if data.get("紅吞", False):
        add(4 if is_trending else 1, "🔥 出現紅吞反轉型態", "reversal")
    if data.get("黑吞", False):
        add(-3, "🩸 出現黑吞反轉型態", "reversal")
    if data.get("回測有撐", False):
        add(2, "🔥 帶量長下影線，回測支撐成功", "reversal")
    if data.get("反彈遇壓", False):
        add(-2, "🩸 反彈遇壓，留意上方賣壓", "reversal")

    if close >= ma5 and ma5_up:
        add(1, "🔥 5MA 已上彎，短線結構轉強", "trend")
    if close < ma5 and not ma5_up:
        add(-1, "⚠️ 短均線仍有壓力", "trend")
    if tomorrow_turn_price > 0 and close < tomorrow_turn_price:
        add(-1, f"⚠️ 明日 5MA 扣抵價 {tomorrow_turn_price:.2f}，短線轉強門檻仍高", "trend")

    if momentum_score >= 75:
        add(2, f"✅ 趨勢品質佳 ({momentum_score:.0f}/100)", "trend")
    elif momentum_score <= 35:
        add(-2, f"⚠️ 趨勢品質偏弱 ({momentum_score:.0f}/100)", "trend")

    whale_net = _optional_num(data.get("Whale_Net"))
    whale_days = _optional_num(data.get("Whale_Net_Days"))
    if whale_net is None and inst_data:
        reported_totals = [
            _optional_num(_first_present(row, ("單日合計(張)", "total")))
            for row in list(inst_data)[:3]
            if isinstance(row, dict)
        ]
        reported_totals = [value for value in reported_totals if value is not None]
        if reported_totals:
            whale_net = sum(reported_totals)
            whale_days = float(len(reported_totals))
    if whale_net is not None and (whale_days is None or whale_days <= 0):
        whale_days = 3.0

    whale_vol_ratio = None
    if whale_net is not None and volume_5d > 0 and whale_days:
        # Institutional flow is stored in lots (張); Yahoo volume is shares (股).
        whale_vol_ratio = ((whale_net * 1000) / (volume_5d * whale_days)) * 100
    whale_ratio_text = f"{whale_vol_ratio:.1f}%" if whale_vol_ratio is not None else "未提供"

    if whale_net is not None and ((whale_vol_ratio is not None and whale_vol_ratio > 5) or whale_net > 3000):
        add(2, f"✅ 法人積極買超 (佔均量 {whale_ratio_text} 或 >3千張)")
    elif whale_net is not None and ((whale_vol_ratio is not None and whale_vol_ratio < -5) or whale_net < -3000):
        add(-2, f"⚠️ 法人大量賣超 (佔均量 {whale_ratio_text} 或 <-3千張)")
    elif whale_net is not None and whale_net > 500:
        add(1, "✅ 法人微幅買超")
    elif whale_net is not None and whale_net < -500:
        add(-1, "⚠️ 法人微幅賣超")

    sell_streak, foreign_net, trust_net, flow_divergence = _institutional_risk_inputs(data, inst_data)
    if sell_streak is not None and sell_streak >= 3:
        add(-2, f"⚠️ 法人連續賣超 {int(sell_streak)} 日，籌碼尚未止穩")
    elif sell_streak is not None and sell_streak >= 2:
        add(-1, f"⚠️ 法人連續賣超 {int(sell_streak)} 日，留意籌碼轉弱")
    if flow_divergence is True:
        if foreign_net is not None and trust_net is not None:
            add(-1, f"⚠️ 外資與投信方向分歧 (外資 {foreign_net:,.0f} 張／投信 {trust_net:,.0f} 張)")
        else:
            add(-1, "⚠️ 外資與投信方向分歧，籌碼共識不足")

    if j_value >= 80:
        if not strong_trend:
            add(-3, "⚠️ KDJ 高檔過熱", "overheat")
    elif j_value <= 20 and close >= ma20:
        add(1, "✅ KDJ 低檔但仍守月線", "reversal")
        
    if rsi >= 75:
        if not strong_trend:
            add(-2, "⚠️ RSI 過熱", "overheat")
    elif 45 <= rsi <= 65 and close >= ma20:
        add(1, "✅ RSI 位於健康動能區", "trend")
        
    if close and bb_up and close >= bb_up * 0.98 and not strong_trend:
        add(-2, "⚠️ 接近布林上軌壓力", "overheat")
            
    if bias > 7 and not strong_trend:
        add(-2, "⚠️ 正乖離過大", "overheat")
    if close and ma20 and close < ma20:
        add(-2, "⚠️ 跌破月線支撐", "trend")
    if vix >= 25:
        add(-2, f"⚠️ VIX {vix:.1f} 偏高，系統性風險升溫")
    if confidence < 60:
        add(-2, f"⚠️ 資料完整度偏低 ({confidence:.0f}%)，分數僅供保守參考")
    elif confidence < 80:
        add(-1, f"⚠️ 資料完整度中等 ({confidence:.0f}%)，需留意缺失資料")
    if signal_conflict == "高":
        add(-3, "⚠️ 多空訊號衝突高，不適合列為主清單")
    if entry_pattern in ["過熱追高型", "假突破風險型"]:
        add(-4, f"⚠️ 型態為 {entry_pattern}，隔日追價風險高", "overheat")
    elif entry_pattern in ["趨勢突破型", "回測支撐型"]:
        add(2, f"✅ 型態為 {entry_pattern}，較符合明日觀察", "breakout")
    if data.get("Box_Breakout", False):
        add(2, "✅ 近 10 日整理後突破", "breakout")

    group_limits = {
        "trend": 9.0,
        "reversal": 7.0,
        "breakout": 7.0,
        "overheat": 7.0,
    }
    effective_evidence = sc + sum(
        _combine_correlated_scores(values, limit=group_limits[group])
        for group, values in correlated.items()
    )
    final_score = _calibrate_score(effective_evidence)

    label = decision_label(final_score)

    feature = "一般狀態"
    if data.get("Advanced_Pattern"):
        feature = data.get("Advanced_Pattern")
    elif data.get("紅吞", False):
        feature = "🔥 紅吞表態"
    elif data.get("回測有撐", False):
        feature = "💪 回檔有撐"
    elif data.get("Box_Breakout", False):
        feature = "📦 整理突破"

    return final_score, label, rs if with_reason else [], feature
