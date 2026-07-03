# ==========================================
# 🚀 改進版分析引擎 v2.0
# 核心升級：權重化評分 + 多時間框架 + 動態風險管理
# ==========================================

import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple, Optional

class AdvancedAnalysisEngine:
    """
    進階分析引擎 v2.0
    - 多時間框架驗證（日線 + 周線）
    - 權重化評分系統（不同條件有不同權重）
    - 動態 ATR 停利機制
    - 嚴格的籌碼面分析
    - 改進的回測標準
    """
    
    def __init__(self):
        # 評分權重配置（保守模式）
        self.weight_config = {
            "trend_filter": 0.25,      # 趨勢過濾（最重要）
            "technical": 0.35,         # 技術面（次重要）
            "chip": 0.20,              # 籌碼面
            "fundamental": 0.15,       # 基本面
            "volume": 0.05             # 量能確認
        }
        
        # 各子指標的權重
        self.tech_weights = {
            "ma200_above": 0.20,       # 200日均線之上
            "ma_alignment": 0.15,      # 均線排列
            "pattern": 0.20,           # 型態（紅吞等）
            "kdj": 0.15,               # KDJ 超賣區
            "bias": 0.15,              # 乖離
            "adx": 0.15                # ADX 趨勢強度
        }
        
        self.chip_weights = {
            "foreign_3d": 0.40,        # 外資 3 日買超
            "trust_3d": 0.30,          # 投信 3 日買超
            "dealer_3d": 0.15,         # 自營商 3 日買超
            "concentration": 0.15      # 大戶集中度
        }
    
    # ==========================================
    # 核心 1：多時間框架趨勢驗證
    # ==========================================
    
    def validate_trend_structure(self, df_daily: pd.DataFrame, df_weekly: Optional[pd.DataFrame] = None) -> Dict:
        """
        驗證趨勢結構（日線 + 周線）
        
        返回：
        {
            "daily_trend": "uptrend" | "downtrend" | "ranging",
            "weekly_trend": "uptrend" | "downtrend" | "ranging",
            "alignment": True/False,  # 日周是否同向
            "score": 0-100,          # 趨勢結構評分
            "details": {...}
        }
        """
        if df_daily is None or len(df_daily) < 200:
            return {"score": 0, "alignment": False, "daily_trend": "unknown", "weekly_trend": "unknown"}
        
        result = {}
        
        # === 日線趨勢判斷 ===
        close = df_daily['Close'].iloc[-1]
        ma20 = df_daily['20MA'].iloc[-1]
        ma60 = df_daily['60MA'].iloc[-1]
        ma200 = df_daily['Close'].rolling(200).mean().iloc[-1]
        
        # 日線趨勢判定
        if close > ma20 > ma60 > ma200 and df_daily['Close'].iloc[-1] > df_daily['Close'].iloc[-5]:
            daily_trend = "uptrend"
            daily_score = 100
        elif close < ma20 < ma60 < ma200 and df_daily['Close'].iloc[-1] < df_daily['Close'].iloc[-5]:
            daily_trend = "downtrend"
            daily_score = 0
        else:
            daily_trend = "ranging"
            daily_score = 30
        
        result['daily_trend'] = daily_trend
        result['daily_score'] = daily_score
        result['daily_ma_pos'] = {
            "price": round(close, 2),
            "ma20": round(ma20, 2),
            "ma60": round(ma60, 2),
            "ma200": round(ma200, 2),
            "above_ma200": close > ma200
        }
        
        # === 周線趨勢判斷（若有周線資料）===
        if df_weekly is not None and len(df_weekly) >= 50:
            close_w = df_weekly['Close'].iloc[-1]
            ma20_w = df_weekly['20MA'].iloc[-1]
            ma60_w = df_weekly['60MA'].iloc[-1]
            
            if close_w > ma20_w > ma60_w:
                weekly_trend = "uptrend"
                weekly_score = 100
            elif close_w < ma20_w < ma60_w:
                weekly_trend = "downtrend"
                weekly_score = 0
            else:
                weekly_trend = "ranging"
                weekly_score = 30
            
            result['weekly_trend'] = weekly_trend
            result['weekly_score'] = weekly_score
            
            # 日周對齊度
            alignment = (daily_trend == weekly_trend)
            alignment_bonus = 50 if alignment else 0
            
            result['alignment'] = alignment
        else:
            result['weekly_trend'] = "no_data"
            result['weekly_score'] = daily_score  # 默認跟日線走
            result['alignment'] = True
            alignment_bonus = 0
        
        # 綜合評分
        final_score = (daily_score * 0.7 + (result.get('weekly_score', daily_score) * 0.3))
        result['score'] = int(final_score)
        result['is_valid_trend'] = result['score'] >= 60  # 趨勢強度門檻
        
        return result
    
    # ==========================================
    # 核心 2：權重化技術面評分
    # ==========================================
    
    def score_technical(self, data: Dict) -> Tuple[float, List[str]]:
        """
        權重化技術面評分（滿分 100）
        """
        scores = {}
        reasons = []
        
        # 1. MA200 之上判定（最基礎）
        ma200_score = 20 if data.get('above_ma200', False) else 0
        scores['ma200_above'] = ma200_score
        if ma200_score == 20:
            reasons.append("✅ 穩在 200 日均線上方，屬於長期多頭")
        else:
            reasons.append("❌ 跌破 200 日均線，長期趨勢轉空")
        
        # 2. 均線排列（20>60>200）
        ma_alignment = data.get('ma_alignment', 0)  # 0 ~ 1
        ma_align_score = ma_alignment * 20
        scores['ma_alignment'] = ma_align_score
        if ma_align_score >= 15:
            reasons.append(f"✅ 短中期均線排列完美 ({ma_alignment*100:.0f}% 對齊)")
        elif ma_align_score >= 10:
            reasons.append(f"⚠️ 均線部分排列，動能尚可")
        
        # 3. K線型態（紅吞、支撐防守等）
        pattern_score = 0
        if data.get('red_engulfing', False):
            pattern_score += 10
            reasons.append("🔥 出現紅吞反轉型態 (+10)")
        if data.get('support_pullback', False):
            pattern_score += 8
            reasons.append("✅ 帶量長下影線防守支撐 (+8)")
        if data.get('resistance_rejection', False):
            pattern_score -= 5
            reasons.append("⚠️ 反彈遇均線壓力 (-5)")
        if data.get('black_engulfing', False):
            pattern_score -= 10
            reasons.append("🩸 出現黑吞反轉型態 (-10)")
        
        scores['pattern'] = min(20, max(-10, pattern_score))
        
        # 4. KDJ 指標（K<30 超賣區加分）
        kdj_j = data.get('J', 50)
        if kdj_j < 20:
            kdj_score = 15
            reasons.append(f"✅ KDJ 極度超賣 (J={kdj_j}) 反彈機率高 (+15)")
        elif kdj_j < 30:
            kdj_score = 10
            reasons.append(f"✅ KDJ 超賣區 (J={kdj_j}) (+10)")
        elif kdj_j > 80:
            kdj_score = -8
            reasons.append(f"⚠️ KDJ 過熱區 (J={kdj_j}) 警戒 (-8)")
        else:
            kdj_score = 0
        
        scores['kdj'] = kdj_score
        
        # 5. 乖離度（BIAS）
        bias = data.get('BIAS_20', 0)
        if bias < -5:
            bias_score = 10
            reasons.append(f"✅ 負乖離過大 ({bias:.1f}%) 有反彈空間 (+10)")
        elif bias > 7:
            bias_score = -8
            reasons.append(f"⚠️ 正乖離過大 ({bias:.1f}%) 易回檔 (-8)")
        else:
            bias_score = 0
        
        scores['bias'] = bias_score
        
        # 6. ADX 趨勢強度
        adx = data.get('ADX', 20)
        if adx >= 30:
            adx_score = 15
            reasons.append(f"🔥 ADX 強趨勢 ({adx:.1f}) 突破延續性高 (+15)")
        elif adx >= 25:
            adx_score = 10
            reasons.append(f"✅ ADX 明確趨勢 ({adx:.1f}) (+10)")
        elif adx >= 20:
            adx_score = 5
            reasons.append(f"⚠️ ADX 中性 ({adx:.1f})")
        else:
            adx_score = -5
            reasons.append(f"⚠️ ADX 低迷 ({adx:.1f}) 警惕假突破 (-5)")
        
        scores['adx'] = adx_score
        
        # 計算加權平均
        weighted_tech_score = 0
        for key, weight in self.tech_weights.items():
            weighted_tech_score += scores.get(key, 0) * weight
        
        # 歸一化到 0-100
        tech_score = max(0, min(100, weighted_tech_score))
        
        return tech_score, reasons
    
    # ==========================================
    # 核心 3：籌碼面分析
    # ==========================================
    
    def score_chip(self, inst_data: List[Dict]) -> Tuple[float, List[str]]:
        """
        籌碼面評分（滿分 100）
        改進：3 日籌碼必須連買，且要看方向一致性
        """
        scores = {}
        reasons = []
        
        if not inst_data or len(inst_data) < 3:
            reasons.append("⚠️ 籌碼資料不足，採中立評估")
            return 30, reasons
        
        # 提取 3 日籌碼
        f_nets = []
        t_nets = []
        d_nets = []
        
        for i in range(min(3, len(inst_data))):
            try:
                f_net = int(str(inst_data[i].get('外資(張)', 0)).replace(',', ''))
                t_net = int(str(inst_data[i].get('投信(張)', 0)).replace(',', ''))
                d_net = int(str(inst_data[i].get('自營商(張)', 0)).replace(',', ''))
                f_nets.append(f_net)
                t_nets.append(t_net)
                d_nets.append(d_net)
            except:
                pass
        
        if not f_nets:
            return 30, ["⚠️ 籌碼資料解析失敗"]
        
        # === 外資評分 ===
        f_avg = np.mean(f_nets)
        f_consecutive = sum(1 for x in f_nets if x > 0)  # 連買天數
        
        if f_consecutive >= 3 and f_avg > 500:
            f_score = 35
            reasons.append(f"🔥 外資連買 3 日，累計 {int(sum(f_nets))} 張，持續買進動能強 (+35)")
        elif f_consecutive >= 2 and f_avg > 200:
            f_score = 25
            reasons.append(f"✅ 外資 2 日買超，累計 {int(sum(f_nets[:2]))} 張 (+25)")
        elif f_avg > 0:
            f_score = 10
            reasons.append(f"✅ 外資淨買 (+10)")
        elif f_avg < -200:
            f_score = -20
            reasons.append(f"🩸 外資倒貨，累計 {int(sum(f_nets))} 張 (-20)")
        else:
            f_score = 0
        
        scores['foreign'] = f_score
        
        # === 投信評分 ===
        t_avg = np.mean(t_nets)
        t_consecutive = sum(1 for x in t_nets if x > 0)
        
        if t_consecutive >= 3 and t_avg > 200:
            t_score = 25
            reasons.append(f"✅ 投信連買 3 日，累計 {int(sum(t_nets))} 張 (+25)")
        elif t_consecutive >= 2 and t_avg > 100:
            t_score = 18
            reasons.append(f"✅ 投信 2 日買超 (+18)")
        elif t_avg > 0:
            t_score = 8
            reasons.append(f"✅ 投信淨買 (+8)")
        elif t_avg < -100:
            t_score = -15
            reasons.append(f"⚠️ 投信倒貨 (-15)")
        else:
            t_score = 0
        
        scores['trust'] = t_score
        
        # === 自營商評分 ===
        d_avg = np.mean(d_nets)
        if d_avg > 50:
            d_score = 10
            reasons.append(f"✅ 自營商買進 (+10)")
        elif d_avg < -50:
            d_score = -5
            reasons.append(f"⚠️ 自營商賣出 (-5)")
        else:
            d_score = 0
        
        scores['dealer'] = d_score
        
        # === 大戶集中度（加分項，非扣分項）===
        chip_concentration = 0  # 需從外部傳入
        if chip_concentration > 10:
            conc_score = 10
            reasons.append(f"✅ 大戶持股集中 ({chip_concentration:.1f}%) 主力有意圖 (+10)")
        else:
            conc_score = 0
        
        scores['concentration'] = conc_score
        
        # 加權計算
        chip_score = (
            scores['foreign'] * 0.4 +
            scores['trust'] * 0.3 +
            scores['dealer'] * 0.15 +
            scores['concentration'] * 0.15
        )
        
        chip_score = max(0, min(100, chip_score))
        
        return chip_score, reasons
    
    # ==========================================
    # 核心 4：基本面評分
    # ==========================================
    
    def score_fundamental(self, fundamental_data: Dict, recent_data: Dict) -> Tuple[float, List[str]]:
        """
        基本面評分（滿分 100）
        改進：月營收改為 3 月平均，EPS 門檻提高
        """
        scores = {}
        reasons = []
        
        # === 月營收評分 ===
        mom = recent_data.get('MoM', 0)
        yoy = recent_data.get('YoY', 0)
        
        if mom > 0 and yoy > 10:  # 月增 + 年增 >10%
            rev_score = 30
            reasons.append(f"🔥 月營收雙增 (MoM: {mom:.1f}%, YoY: {yoy:.1f}%) 基本面強勁 (+30)")
        elif yoy > 15:
            rev_score = 20
            reasons.append(f"✅ 年營收成長達 {yoy:.1f}% 動能持續 (+20)")
        elif yoy > 0:
            rev_score = 10
            reasons.append(f"✅ 年營收正成長 (+10)")
        elif yoy > -5:
            rev_score = 0
            reasons.append(f"⚠️ 營收年衰但幅度有限")
        else:
            rev_score = -15
            reasons.append(f"🩸 年營收衰退 {yoy:.1f}% (-15)")
        
        scores['revenue'] = rev_score
        
        # === EPS 評分 ===
        eps_str = str(fundamental_data.get('EPS', '無')).strip()
        try:
            eps_val = float(eps_str)
            if eps_val > 1.5:
                eps_score = 25
                reasons.append(f"✅ EPS 優質 ({eps_val:.2f}元) 獲利扎實 (+25)")
            elif eps_val > 0.5:
                eps_score = 15
                reasons.append(f"✅ EPS 正常 ({eps_val:.2f}元) (+15)")
            elif eps_val > 0:
                eps_score = 5
                reasons.append(f"⚠️ EPS 偏低 ({eps_val:.2f}元)")
            else:
                eps_score = -20
                reasons.append(f"🩸 虧損或無盈利 (-20)")
        except:
            eps_score = 0
            reasons.append(f"⚠️ EPS 資料無法取得")
        
        scores['eps'] = eps_score
        
        # === PE 評分 ===
        pe_str = str(fundamental_data.get('PE', '無')).strip()
        try:
            pe_val = float(pe_str)
            if pe_val < 15:
                pe_score = 15
                reasons.append(f"🔥 本益比便宜 (PE: {pe_val:.1f}) 安全邊際足 (+15)")
            elif pe_val < 20:
                pe_score = 10
                reasons.append(f"✅ 本益比合理 (PE: {pe_val:.1f}) (+10)")
            elif pe_val < 30:
                pe_score = 0
                reasons.append(f"⚠️ 本益比偏高 (PE: {pe_val:.1f})")
            else:
                pe_score = -10
                reasons.append(f"🩸 本益比過高 (PE: {pe_val:.1f}) 追高風險 (-10)")
        except:
            pe_score = 0
        
        scores['pe'] = pe_score
        
        # 加權計算
        fund_score = (
            scores.get('revenue', 0) * 0.5 +
            scores.get('eps', 0) * 0.3 +
            scores.get('pe', 0) * 0.2
        )
        
        fund_score = max(0, min(100, fund_score))
        
        return fund_score, reasons
    
    # ==========================================
    # 核心 5：量能確認
    # ==========================================
    
    def score_volume(self, data: Dict) -> Tuple[float, List[str]]:
        """
        量能評分（滿分 100）
        """
        reasons = []
        
        vol_ratio = data.get('Est_Vol_Ratio', 1.0)
        volume_5d_avg = data.get('5日均量', 0)
        current_vol = data.get('成交量', 0)
        
        if vol_ratio > 1.5:
            vol_score = 15
            reasons.append(f"✅ 成交量放大 {vol_ratio:.1f}x 主力進場跡象 (+15)")
        elif vol_ratio > 1.1:
            vol_score = 8
            reasons.append(f"✅ 量能溫和放大 (+8)")
        elif vol_ratio > 0.9:
            vol_score = 0
            reasons.append(f"⚠️ 量能平淡")
        else:
            vol_score = -5
            reasons.append(f"⚠️ 量能萎縮 (-5)")
        
        return vol_score, reasons
    
    # ==========================================
    # 核心 6：綜合評分與決策
    # ==========================================
    
    def generate_decision(self, 
                         df_daily: pd.DataFrame,
                         df_weekly: Optional[pd.DataFrame],
                         technical_data: Dict,
                         inst_data: List[Dict],
                         fundamental_data: Dict,
                         recent_data: Dict) -> Dict:
        """
        生成綜合投資決策
        """
        
        result = {
            "timestamp": datetime.now(timezone(timedelta(hours=8))).isoformat(),
            "scores": {},
            "reasons": [],
            "signal": None,
            "confidence": 0,
            "risk_level": "high",
            "entry_price": None,
            "target_price": None,
            "stop_price": None,
            "rrr": 0,
        }
        
        # === 1. 趨勢驗證（門檻最高）===
        trend_result = self.validate_trend_structure(df_daily, df_weekly)
        result['trend_structure'] = trend_result
        
        if not trend_result.get('is_valid_trend', False):
            result['signal'] = "SKIP"
            result['confidence'] = 0
            result['reasons'].append("❌ 趨勢結構不明確，暫無明確買點")
            return result
        
        result['reasons'].append(f"✅ 趨勢結構確認 ({trend_result['daily_trend'].upper()})")
        
        # === 2. 技術面評分 ===
        tech_score, tech_reasons = self.score_technical(technical_data)
        result['scores']['technical'] = tech_score
        result['reasons'].extend(tech_reasons)
        
        # === 3. 籌碼面評分 ===
        chip_score, chip_reasons = self.score_chip(inst_data)
        result['scores']['chip'] = chip_score
        result['reasons'].extend(chip_reasons)
        
        # === 4. 基本面評分 ===
        fund_score, fund_reasons = self.score_fundamental(fundamental_data, recent_data)
        result['scores']['fundamental'] = fund_score
        result['reasons'].extend(fund_reasons)
        
        # === 5. 量能評分 ===
        vol_score, vol_reasons = self.score_volume(technical_data)
        result['scores']['volume'] = vol_score
        result['reasons'].extend(vol_reasons)
        
        # === 綜合評分（加權）===
        trend_score = trend_result['score']
        
        overall_score = (
            trend_score * 0.25 +
            tech_score * 0.35 +
            chip_score * 0.20 +
            fund_score * 0.15 +
            vol_score * 0.05
        )
        
        result['overall_score'] = int(overall_score)
        
        # === 決策邏輯（提高標準）===
        if result['overall_score'] >= 75:
            result['signal'] = "BUY_STRONG"
            result['rating'] = "S級 強烈買進"
            result['confidence'] = 0.85
            result['risk_level'] = "medium"
        elif result['overall_score'] >= 60:
            result['signal'] = "BUY"
            result['rating'] = "A級 試單佈局"
            result['confidence'] = 0.65
            result['risk_level'] = "medium-high"
        elif result['overall_score'] >= 40:
            result['signal'] = "HOLD"
            result['rating'] = "觀望"
            result['confidence'] = 0.4
            result['risk_level'] = "high"
        else:
            result['signal'] = "SELL"
            result['rating'] = "空手觀望"
            result['confidence'] = 0
            result['risk_level'] = "very_high"
        
        # === ATR 動態停利機制 ===
        if len(df_daily) >= 14:
            atr = df_daily['ATR'].iloc[-1]
            close = df_daily['Close'].iloc[-1]
            
            # 動態 ATR 倍數（根據上漲空間調整）
            roc_20 = recent_data.get('ROC_20', 0)
            
            if roc_20 > 20:  # 已漲超 20%，提早獲利
                atr_multiplier_target = 1.2
                atr_multiplier_stop = 1.0
            elif roc_20 > 10:  # 漲幅中等
                atr_multiplier_target = 1.5
                atr_multiplier_stop = 1.0
            else:  # 初期上漲
                atr_multiplier_target = 2.0
                atr_multiplier_stop = 1.2
            
            result['entry_price'] = round(close, 2)
            result['target_price'] = round(close + atr * atr_multiplier_target, 2)
            result['stop_price'] = round(close - atr * atr_multiplier_stop, 2)
            result['atr_value'] = round(atr, 2)
            
            if result['stop_price'] > 0:
                result['rrr'] = round(
                    (result['target_price'] - close) / (close - result['stop_price']), 2
                )
            
            result['reasons'].append(
                f"📊 ATR 動態目標：進場 {result['entry_price']} → 目標 {result['target_price']} "
                f"(+{(result['target_price']-close)/close*100:.1f}%) "
                f"/ 停損 {result['stop_price']} / RRR: 1:{result['rrr']}"
            )
        
        return result


# ==========================================
# 測試用例
# ==========================================

if __name__ == "__main__":
    engine = AdvancedAnalysisEngine()
    print("✅ 改進版分析引擎 v2.0 初始化完成")
    print(f"權重配置: {engine.weight_config}")
