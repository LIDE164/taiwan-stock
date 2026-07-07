# scoring.py - 100分量化決策大腦核心算法
def get_decision_score(data, fund_data, inst_data=None, mode="post", with_reason=True):
    sc, rs = 0, []
    
    adx = data.get('ADX', 0)
    roc_20 = data.get('ROC_20', 0)
    is_trending = adx >= 25 
    
    # 均線與趨勢動能
    if data.get('訊號', False): 
        if is_trending:
            sc+=3; rs.append(f"✅ 穩在月線上且動能充沛 (ADX:{adx:.1f} 趨勢明確)")
        else:
            sc+=1; rs.append(f"⚠️ 穩在月線上 (但 ADX:{adx:.1f} 盤整區間，動能稍弱)")
            
    # 布林通道與乖離
    if data.get('收盤價', 0) <= data.get('BB_DN', 0) * 1.02: sc+=2; rs.append("✅ 觸及布林下軌支撐")
    if data.get('BIAS', 0) < -5: sc+=1; rs.append("✅ 負乖離過大")
    
    # 近月位階
    if roc_20 > 10:
        sc+=2; rs.append(f"🔥 近月漲幅 {roc_20:.2f}% 表現亮眼，具備市場主流強勢股特徵")
    elif roc_20 < -5:
        sc-=2; rs.append(f"🩸 近月跌幅 {roc_20:.2f}% 表現弱勢，請避開弱勢接刀陷阱")
    
    # 基本面營收 (盤中可能無此資料，需給預設值)
    if data.get('MoM', 0) > 0 and data.get('YoY', 0) > 0:
        sc+=3; rs.append(f"🔥 月營收雙增 (MoM: {data.get('MoM')}%, YoY: {data.get('YoY')}%)，具備長線黑馬特質")
    elif data.get('YoY', 0) > 15:
        sc+=2; rs.append(f"✅ 月營收年增達 {data.get('YoY')}%，營運動能強勁")
        
    try: eps_f = float(str(fund_data.get('EPS', '0')).replace(',', ''))
    except: eps_f = 0.0
    if eps_f > 0: sc+=2; rs.append("✅ 歷史 EPS 獲利體質")
    if eps_f < 0: sc-=1; rs.append("⚠️ 基本面虧損")
    
    # 量能與指標
    if data.get('成交量', 0) > data.get('5日均量', 0) * 1.1: sc+=2; rs.append("✅ 量能放大 (具備主力進場點火特徵)")
    else: sc-=1; rs.append("⚠️ 量能未明顯放大 (打底或缺乏點火動能)")
        
    if data.get('MACD柱', 0) > data.get('前日MACD柱', -999): sc+=2; rs.append("✅ MACD 綠柱收斂或紅柱放大 (動能防禦過關)")
    else: sc-=3; rs.append("⚠️ MACD 空方動能持續擴大 (型態脆弱嚴防接刀)")

    # K線型態
    if data.get('紅吞', False): 
        if is_trending:
            sc+=4; rs.append("🔥 出現「紅吞」反轉型態 (趨勢確認，強烈買訊)")
        else:
            sc+=1; rs.append("⚠️ 出現「紅吞」(但 ADX 偏低處於盤整，提防假突破)")
            
    if data.get('黑吞', False): sc-=3; rs.append("🩸 出現「黑吞」反轉型態 (強烈空頭逃命訊號)")

    if data.get('回測有撐', False): sc+=2; rs.append("🔥 帶量長下影線 (主力回測支撐成功)")
    if data.get('反彈遇壓', False): sc-=2; rs.append("🩸 反彈遇均線壓力留長上影線 (空方壓制)")
    
    if data.get('收盤價', 0) >= data.get('5MA', 0) and data.get('5日線即將上彎', False): 
        rs.append("🔥 5日線扣低值 (短均線準備上彎發散，短線動能轉強)")
    if data.get('收盤價', 0) < data.get('5MA', 0) and not data.get('5日線即將上彎', False): 
        rs.append("⚠️ 5日線扣高值 (短均線即遇蓋頭壓力)")

    # 扣分項目
    if data.get('J值', 50) >= 80: sc-=3; rs.append("⚠️ KDJ高檔過熱")
    if data.get('收盤價', 0) >= data.get('BB_UP', 9999) * 0.98: sc-=2; rs.append("⚠️ 觸及布林上軌壓力")
    if data.get('BIAS', 0) > 7: sc-=2; rs.append("⚠️ 正乖離過大")
    if data.get('收盤價', 0) < data.get('20MA', 0): sc-=2; rs.append("⚠️ 跌破月線支撐")

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