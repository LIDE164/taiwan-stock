import numpy as np
import pandas as pd

def get_pivots(df: pd.DataFrame, order: int = 5) -> tuple:
    """找出給定資料的局部高低點(Pivots)"""
    if len(df) < order * 2:
        return [], []
        
    prices_high = df['High'].values
    prices_low = df['Low'].values
    
    # 尋找局部極大值與極小值的索引 (使用 pandas rolling 取代 scipy.signal 以節省記憶體)
    highs_series = df['High']
    lows_series = df['Low']
    
    rolling_max = highs_series.rolling(window=2*order+1, center=True).max()
    rolling_min = lows_series.rolling(window=2*order+1, center=True).min()
    
    high_idx = np.where(highs_series == rolling_max)[0]
    low_idx = np.where(lows_series == rolling_min)[0]
    
    highs = [(int(i), float(prices_high[i])) for i in high_idx]
    lows = [(int(i), float(prices_low[i])) for i in low_idx]
    
    return highs, lows

def detect_pattern(df: pd.DataFrame) -> dict:
    """
    偵測 12 種進階形態
    回傳字典: {"Pattern_Name": str, "Signal": str}
    Signal: "Buy", "Sell", "Wait", 或 None
    """
    if len(df) < 30:
        return {}
        
    # 我們取最近的 60 根 K 線來做型態辨識，避免太久遠的歷史干擾
    work_df = df.tail(60).reset_index(drop=True)
    highs, lows = get_pivots(work_df, order=4)
    
    # 確保有足夠的轉折點來辨識形態
    if len(highs) < 2 or len(lows) < 2:
        return {}
        
    # 取得最新的收盤價做為突破判斷基準
    current_close = work_df['Close'].iloc[-1]
    
    # 取最後三個高點與三個低點進行幾何比對
    recent_highs = [h[1] for h in highs[-3:]]
    recent_lows = [l[1] for l in lows[-3:]]
    
    # 預設容錯率
    tolerance = 0.02 
    
    def is_flat(prices):
        if len(prices) < 2: return False
        return (max(prices) - min(prices)) / min(prices) <= tolerance
        
    def is_rising(prices):
        if len(prices) < 2: return False
        return prices[-1] > prices[0] * (1 + tolerance)
        
    def is_falling(prices):
        if len(prices) < 2: return False
        return prices[-1] < prices[0] * (1 - tolerance)

    # ---------------- 🔴 賣出形態 ----------------
    
    # 1. M頭 (Double Top)
    if len(recent_highs) >= 2 and len(recent_lows) >= 1:
        if is_flat(recent_highs[-2:]):
            neckline = recent_lows[-1]
            if current_close < neckline:
                return {"Pattern_Name": "M頭 (Double Top)", "Signal": "Sell"}

    # 2. 三重頂 (Triple Top)
    if len(recent_highs) >= 3 and len(recent_lows) >= 2:
        if is_flat(recent_highs[-3:]):
            neckline = min(recent_lows[-2:])
            if current_close < neckline:
                return {"Pattern_Name": "三重頂 (Triple Top)", "Signal": "Sell"}
                
    # 3. 上升楔形 (Rising Wedge)
    if len(recent_highs) >= 2 and len(recent_lows) >= 2:
        if is_rising(recent_highs[-2:]) and is_rising(recent_lows[-2:]):
            high_diff = recent_highs[-1] - recent_highs[-2]
            low_diff = recent_lows[-1] - recent_lows[-2]
            if high_diff < low_diff:
                support_line = recent_lows[-1]
                if current_close < support_line:
                    return {"Pattern_Name": "上升楔形 (Rising Wedge)", "Signal": "Sell"}

    # 4. 下跌旗形 (Bear Flag)
    if len(recent_highs) >= 2 and len(recent_lows) >= 2:
        first_low_idx = lows[-2][0] if len(lows) >= 2 else lows[-1][0]
        start_price = work_df['High'].iloc[max(0, first_low_idx-15)]
        if start_price > recent_lows[-2] * 1.1:
            if is_rising(recent_highs[-2:]) and is_rising(recent_lows[-2:]):
                support_line = recent_lows[-1]
                if current_close < support_line:
                    return {"Pattern_Name": "下跌旗形 (Bear Flag)", "Signal": "Sell"}

    # ---------------- 🟢 買入形態 ----------------
    
    # 5. 頭肩底 (Inverse Head and Shoulders)
    if len(recent_lows) >= 3 and len(recent_highs) >= 2:
        left_shoulder, head, right_shoulder = recent_lows[-3], recent_lows[-2], recent_lows[-1]
        if head < left_shoulder and head < right_shoulder:
            if abs(left_shoulder - right_shoulder) / min(left_shoulder, right_shoulder) < tolerance * 2:
                neckline = max(recent_highs[-2:])
                if current_close > neckline:
                    return {"Pattern_Name": "頭肩底 (Inverse H&S)", "Signal": "Buy"}
                    
    # 6. 上升三角形 (Ascending Triangle)
    if len(recent_highs) >= 2 and len(recent_lows) >= 2:
        if is_flat(recent_highs[-2:]) and is_rising(recent_lows[-2:]):
            resistance = max(recent_highs[-2:])
            if current_close > resistance:
                return {"Pattern_Name": "上升三角形 (Ascending Triangle)", "Signal": "Buy"}
                
    # 7. 下降楔形 (Falling Wedge)
    if len(recent_highs) >= 2 and len(recent_lows) >= 2:
        if is_falling(recent_highs[-2:]) and is_falling(recent_lows[-2:]):
            high_diff = abs(recent_highs[-1] - recent_highs[-2])
            low_diff = abs(recent_lows[-1] - recent_lows[-2])
            if high_diff > low_diff:
                resistance = recent_highs[-1]
                if current_close > resistance:
                    return {"Pattern_Name": "下降楔形 (Falling Wedge)", "Signal": "Buy"}

    # 8. 上升旗形 (Bull Flag)
    if len(recent_highs) >= 2 and len(recent_lows) >= 2:
        first_high_idx = highs[-2][0] if len(highs) >= 2 else highs[-1][0]
        start_price = work_df['Low'].iloc[max(0, first_high_idx-15)]
        if start_price < recent_highs[-2] * 0.9:
            if is_falling(recent_highs[-2:]) and is_falling(recent_lows[-2:]):
                resistance = recent_highs[-1]
                if current_close > resistance:
                    return {"Pattern_Name": "上升旗形 (Bull Flag)", "Signal": "Buy"}

    # ---------------- ⚫ 盤整形態 (等待) ----------------
    
    # 9. 三角收斂 (Symmetrical Triangle)
    if len(recent_highs) >= 2 and len(recent_lows) >= 2:
        if is_falling(recent_highs[-2:]) and is_rising(recent_lows[-2:]):
            return {"Pattern_Name": "三角收斂 (Symmetrical Triangle)", "Signal": "Wait"}
            
    # 10. 箱型盤整 (Rectangle)
    if len(recent_highs) >= 2 and len(recent_lows) >= 2:
        if is_flat(recent_highs[-2:]) and is_flat(recent_lows[-2:]):
            return {"Pattern_Name": "箱型盤整 (Rectangle)", "Signal": "Wait"}
            
    # 11. 擴散三角形 (Expanding Triangle)
    if len(recent_highs) >= 2 and len(recent_lows) >= 2:
        if is_rising(recent_highs[-2:]) and is_falling(recent_lows[-2:]):
            return {"Pattern_Name": "擴散三角形 (Expanding Triangle)", "Signal": "Wait"}
            
    # 12. 上升通道 (Ascending Channel)
    if len(recent_highs) >= 2 and len(recent_lows) >= 2:
        if is_rising(recent_highs[-2:]) and is_rising(recent_lows[-2:]):
            high_diff = recent_highs[-1] - recent_highs[-2]
            low_diff = recent_lows[-1] - recent_lows[-2]
            if low_diff > 0 and abs(high_diff - low_diff) / low_diff < tolerance * 5:
                return {"Pattern_Name": "上升通道 (Ascending Channel)", "Signal": "Wait"}

    return {}
