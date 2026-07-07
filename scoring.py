# scoring.py - 100分量化決策大腦核心算法
def get_decision_score(data, fund_data, inst_data=None, mode="post", with_reason=True):
    sc = 0
    rs = []
    
    adx = data.get('ADX', 0)
    roc_20 = data.get('ROC_20', 0)
    is_trending = adx >= 25 
    
    # ⭐ 關鍵優化：盤中與盤後的動態權重
    if mode == "realtime":
        macd_penalty = -1
    else:
        macd_penalty = -3

    # ⭐ 關鍵優化：統一 scoring 函數，並在理由中附上分值
    def add(score, text):
        nonlocal sc
        sc += score
        if with_reason and text:
            sign = "+" if score > 0 else ""
            rs.append(f"{text} ({sign}{score}分)")

    # 均線與趨勢動能
    if data.get('訊號', False): 
        if is_trending:
            add(3, f"✅ 穩在月線上且動能充沛 (ADX:{adx:.1f})")
        else:
            add(1, f"⚠️ 穩在月線上 (ADX:{adx:.1f} 盤整)")
            
    # 布林通道與乖離
    if data.get('收盤價', 0) <= data.get('BB_DN', 0) * 1.02: add(2, "✅ 觸及布林下軌支撐")
    if data.get('BIAS', 0) < -5: add(1, "✅ 負乖離過大")
    
    # 近月位階
    if roc_20 > 10: add(2, f"🔥 近月漲幅 {roc_20:.2f}% 表現亮眼")
    elif roc_20 < -5: add(-2, f"🩸 近月跌幅 {roc_20:.2f}% 表現弱勢")
    
    # 基本面營收
    if data.get('MoM', 0) > 0 and data.get('YoY', 0) > 0:
        add(3, f"🔥 月營收雙增 (MoM: {data.get('MoM')}%, YoY: {data.get('YoY')}%)")
    elif data.get('YoY', 0) > 15:
        add(2, f"✅ 月營收年增達 {data.get('YoY')}%")
        
    try: eps_f = float(str(fund_data.get('EPS', '0')).replace(',', ''))
    except: eps_f = 0.0
    if eps_f > 0: add(2, "✅ 歷史 EPS 獲利體質")
    if eps_f < 0: add(-1, "⚠️ 基本面虧損")
    
    # 量能與指標
    if data.get('成交量', 0) > data.get('5日均量', 0) * 1.1: add(2, "✅ 量能放大 (具備點火特徵)")
    else: add(-1, "⚠️ 量能未明顯放大")
        
    if data.get('MACD柱', 0) > data.get('前日MACD柱', -999): 
        add(2, "✅ MACD 綠柱收斂或紅柱放大")
    else: 
        add(macd_penalty, "⚠️ MACD 空方動能持續擴大")

    # K線型態
    if data.get('紅吞', False): add(4 if is_trending else 1, "🔥 出現「紅吞」反轉型態")
    if data.get('黑吞', False): add(-3, "🩸 出現「黑吞」反轉型態")

    if data.get('回測有撐', False): add(2, "🔥 帶量長下影線 (回測支撐成功)")
    if data.get('反彈遇壓', False): add(-2, "🩸 反彈遇均線壓力留長上影線")
    
    if data.get('收盤價', 0) >= data.get('5MA', 0) and data.get('5日線即將上彎', False): 
        add(1, "🔥 5日線扣低值 (短線動能轉強)")
    if data.get('收盤價', 0) < data.get('5MA', 0) and not data.get('5日線即將上彎', False): 
        add(-1, "⚠️ 5日線扣高值 (即遇蓋頭壓力)")

    # 扣分項目
    if data.get('J值', 50) >= 80: add(-3, "⚠️ KDJ高檔過熱")
    if data.get('收盤價', 0) >= data.get('BB_UP', 9999) * 0.98: add(-2, "⚠️ 觸及布林上軌壓力")
    if data.get('BIAS', 0) > 7: add(-2, "⚠️ 正乖離過大")
    if data.get('收盤價', 0) < data.get('20MA', 0): add(-2, "⚠️ 跌破月線支撐")

    # 分數與評級結算
    final_score = max(5, min(99, int(50 + sc * 3)))

    if final_score >= 60: label = "🟢 強勢買進"
    elif final_score >= 45: label = "🟡 偏多觀察"
    else: label = "⚪ 忽略"

    # 特徵標籤
    feature = "一般狀態"
    if data.get('紅吞', False): feature = "🔥 紅吞表態"
    elif data.get('回測有撐', False): feature = "💪 回檔有撐"

    if not with_reason: rs = []

    return final_score, label, rs, feature