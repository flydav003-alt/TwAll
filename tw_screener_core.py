"""
tw_screener_core_v2.py - 升級版
==============================
新增功能：
1. 突破分 (Breakout Score): 衡量接近突破和已經突破的品質
2. 波段分 (Swing Score): 衡量回檔整理的品質，類似原 VCP Score
3. 綜合分 (Composite Score): 整合所有四個維度的評分
"""

import numpy as np
from tw_screener_core import (
    is_otc, yahoo_tw_url, calc_ma120, calc_max_drawdown_pct,
    _find_swing_points, calc_vcp_contraction, _series_to_float_list,
    _zigzag_algorithm, calc_consolidation_vol_ratio, calc_breakout_60d,
    RS_GATE, DD_MIN, DD_MAX, SECOND_PULLBACK_RATIO,
    ZIGZAG_MIN_PERCENT, VCP_MIN_BARS_BETWEEN_TURNS,
)

# ══════════════════════════════════════════════════════════════════════════════
# 突破分 (Breakout Score: 0~100) 新增部分
# ══════════════════════════════════════════════════════════════════════════════

def calc_breakout_score(r):
    """
    突破分（0~100），衡量接近突破和已突破訊號的品質
    
    組成：
    1. 60日新高 (20分) - 已突破
    2. 距52週高距離 (15分) - 接近突破潛力
    3. MA排列強度 (20分) - 短中長週期趨勢
    4. 成交量品質 (20分) - 量能是否足夠
    5. RSI區間品質 (15分) - 過熱程度
    6. 回檔深度 (10分) - 回檔是否過深
    
    需通過 RS >= 85 門檻方可評分（無法達標時回傳 None）
    """
    rs = r.get("rs_score")
    if rs is None or rs < RS_GATE:
        return None
    
    score = 0.0
    
    # ── 1. 60日新高 (20分) ──
    breakout_60d = r.get("vcp_breakout")  # 60日新高 + 爆量
    if breakout_60d is True:
        score += 20
    
    # ── 2. 距52週高距離 (15分) ──
    w52 = r.get("week52_pct")  # 負值，如 -5.0 代表低於高點5%
    if w52 is not None:
        if -5 <= w52 <= 0:
            score += 15
        elif -10 <= w52 < -5:
            score += 11
        elif -15 <= w52 < -10:
            score += 7
        elif -20 <= w52 < -15:
            score += 4
    
    # ── 3. MA排列強度 (20分) ──
    ma20, ma60, ma120 = r.get("ma20"), r.get("ma60"), r.get("ma120")
    if ma20 is not None and ma60 is not None and ma120 is not None:
        if ma20 > ma60 > ma120:
            score += 20
        elif ma20 > ma60 and ma60 > ma120:
            score += 18
        elif ma20 > ma60:
            score += 12
        elif ma60 > ma120:
            score += 6
    
    # ── 4. 成交量品質 (20分) ──
    vol_ratio = r.get("volume_ratio")
    if vol_ratio is not None:
        if 1.5 <= vol_ratio <= 2.5:
            score += 20
        elif 1.0 <= vol_ratio < 1.5:
            score += 14
        elif 2.5 < vol_ratio <= 3.5:
            score += 12
        elif 0.8 <= vol_ratio < 1.0:
            score += 6
        elif vol_ratio > 3.5:
            score += 4
    
    # ── 5. RSI區間品質 (15分) ──
    rsi = r.get("rsi14")
    if rsi is not None:
        if 50 <= rsi <= 65:
            score += 15
        elif 66 <= rsi <= 75:
            score += 10
        elif 40 <= rsi < 50:
            score += 10
        elif 76 <= rsi <= 85:
            score += 5
        elif rsi > 85:
            score += 2
    
    # ── 6. 回檔深度 (10分) ──
    dd60 = r.get("dd60")
    if dd60 is not None:
        if dd60 <= 15:
            score += 10
        elif 15 < dd60 <= 25:
            score += 8
        elif 25 < dd60 <= 35:
            score += 4
        else:
            score += 1
    
    return round(min(score, 100), 1)


# ══════════════════════════════════════════════════════════════════════════════
# 波段分 (Swing Score: 0~100) - 改進原 VCP Score
# ══════════════════════════════════════════════════════════════════════════════

def calc_swing_score_strict(r):
    """
    波段分（0~100），衡量回檔整理形態的品質（類似 VCP 核心概念的改進版）
    
    組成：
    1. MA排列強度 (15分) - MA20 > MA60 > MA120
    2. 回檔深度健康 (20分) - 60日回撤落在 10~30% 健康區間
    3. 二次回檔收縮 (25分) - VCP 核心：第二次回檔較淺
    4. 高點更高 (15分) - 第二次回檔的高點應高於第一次
    5. 量能縮小質量 (10分) - 整理期間量能是否有效縮小
    6. 距52週高距離 (10分) - 距高點 <20% 為最佳
    7. 半年漲幅 (5分) - 60日期間上升態勢
    
    需通過 RS >= 85 門檻方可評分
    """
    rs = r.get("rs_score")
    if rs is None or rs < RS_GATE:
        return None
    
    b = {}
    
    # ── 1. MA排列強度 (15分) ──
    ma20, ma60, ma120 = r.get("ma20"), r.get("ma60"), r.get("ma120")
    b["ma_arrangement"] = 0
    if ma20 is not None and ma60 is not None and ma120 is not None:
        if ma20 > ma60 > ma120:
            b["ma_arrangement"] = 15
        elif ma20 > ma60 or ma60 > ma120:
            b["ma_arrangement"] = 8
    
    # ── 2. 60日回撤健康區間 (20分) ──
    dd60 = r.get("dd60")
    b["drawdown_health"] = 0
    if dd60 is not None:
        if DD_MIN <= dd60 <= DD_MAX:  # 10~30%
            b["drawdown_health"] = 20
        elif 5 <= dd60 < DD_MIN or DD_MAX < dd60 <= 40:
            b["drawdown_health"] = 10
    
    # ── 3. 二次回檔收縮 (25分) - VCP 核心 ──
    contracting = r.get("vcp_contracting", r.get("contracting"))
    leg1, leg2 = r.get("leg1_pct"), r.get("leg2_pct")
    b["pullback_contraction"] = 0
    if contracting is True:
        b["pullback_contraction"] = 25
    elif contracting is False and leg1 is not None and leg2 is not None:
        ratio = leg2 / leg1 if leg1 > 0 else 9
        if ratio < 0.6:
            b["pullback_contraction"] = 15
        elif ratio < 0.8:
            b["pullback_contraction"] = 8
        else:
            b["pullback_contraction"] = 3
    
    # ── 4. 高點更高 (15分) ──
    higher_high = r.get("vcp_higher_high", r.get("higher_high"))
    b["higher_high"] = 15 if higher_high is True else 0
    
    # ── 5. 量能縮小質量 (10分) ──
    vol_q = r.get("vcp_vol_shrink_quality", r.get("vol_shrink_quality"))
    b["vol_shrink_quality"] = 0
    if vol_q is not None:
        b["vol_shrink_quality"] = round(max(0.0, min(1.0, float(vol_q))) * 10, 1)
    else:
        cons_vr = r.get("cons_vol_ratio")
        if cons_vr is not None:
            if cons_vr < 0.7:
                b["vol_shrink_quality"] = 10
            elif cons_vr < 0.9:
                b["vol_shrink_quality"] = 6
            else:
                b["vol_shrink_quality"] = 2
    
    # ── 6. 距52週高距離 (10分) ──
    w52 = r.get("week52_pct")
    b["distance_52w_high"] = 0
    if w52 is not None:
        if -20 <= w52 <= 0:
            b["distance_52w_high"] = 10
        elif -30 <= w52 < -20:
            b["distance_52w_high"] = 5
    
    # ── 7. 半年漲幅 (5分) ──
    ret126 = r.get("ret126d")
    b["six_month_return"] = 0
    if ret126 is not None:
        if ret126 >= 30:
            b["six_month_return"] = 5
        elif 15 <= ret126 < 30:
            b["six_month_return"] = 3
    
    r["swing_score_breakdown"] = b
    return round(min(sum(b.values()), 100), 1)


# ══════════════════════════════════════════════════════════════════════════════
# 綜合分 v2 (Composite Score v2: 0~100)
# 整合 K線分、突破分、波段分三個維度
# ══════════════════════════════════════════════════════════════════════════════

def calc_composite_v2_smart(r):
    """
    綜合分 v2（0~100）- 智能整合版
    
    策略：
    1. 如果 K線分 >= 60，優先突破分 (加權40%) 而不是波段分
    2. 如果 K線分 < 60，優先波段分 (加權40%)
    3. K線分作為基礎，永遠有基本配置
    4. 無法評分的維度（未達 RS 門檻）不計入權重
    
    權重結構（自動歸一化）：
    - K線分: 基礎 30%
    - 突破分: 條件式 40% (K線分 >= 60) 或 15% (K線分 < 60)
    - 波段分: 條件式 15% (K線分 >= 60) 或 40% (K線分 < 60)
    - 綜合評分: 15%
    """
    ks = r.get("kline_score")
    if ks is None:
        return None
    
    # 先計算突破分和波段分（可能為 None）
    bs_breakout = calc_breakout_score(r)
    bs_swing = calc_swing_score_strict(r)
    
    # 決定權重結構
    if ks >= 60:
        # 強勢型：優先突破分
        w_kline, w_breakout, w_swing = 0.30, 0.40, 0.15
    else:
        # 整理型：優先波段分
        w_kline, w_breakout, w_swing = 0.30, 0.15, 0.40
    
    w_composite_base = 0.15  # 綜合評分的基礎貢獻
    
    # 計算綜合基礎分（原有邏輯）
    comp_base = 0.0
    rsi = r.get("rsi14")
    if rsi is not None:
        if 55 <= rsi <= 65:
            comp_base += 20
        elif 45 <= rsi <= 75:
            comp_base += 10
        elif rsi >= 85 or rsi < 35:
            comp_base += 3
    
    vol_ratio = r.get("volume_ratio")
    if vol_ratio is not None:
        if 1.0 <= vol_ratio <= 2.0:
            comp_base += 15
        elif 0.6 <= vol_ratio < 1.0 or 2.0 < vol_ratio <= 2.5:
            comp_base += 8
    
    inst_days = r.get("inst_buy_days", 0) or 0
    if inst_days in (2, 3):
        comp_base += 10
    elif inst_days == 1:
        comp_base += 5
    
    ma60_pct = r.get("price_vs_ma60_pct")
    ma20_pct = r.get("price_vs_ma20_pct")
    if ma60_pct is not None and 0 <= ma60_pct <= 8:
        comp_base += 10
    elif ma20_pct is not None and 0 <= ma20_pct <= 8:
        comp_base += 8
    
    comp_base = min(comp_base, 100)
    
    # 構造加權計算
    score = 0.0
    total_weight = w_kline
    
    # K線分貢獻 (30%)
    score += ks / 100 * w_kline * 100
    
    # 突破分貢獻（如果可計算）
    if bs_breakout is not None:
        score += bs_breakout / 100 * w_breakout * 100
        total_weight += w_breakout
    
    # 波段分貢獻（如果可計算）
    if bs_swing is not None:
        score += bs_swing / 100 * w_swing * 100
        total_weight += w_swing
    
    # 綜合評分貢獻 (15%)
    score += comp_base / 100 * w_composite_base * 100
    total_weight += w_composite_base
    
    # 歸一化到 0~100
    if total_weight > 0:
        final = score / total_weight
    else:
        final = 0
    
    # 存儲拆解資訊供前端使用
    r["composite_v2_breakdown"] = {
        "kline_score": ks,
        "breakout_score": bs_breakout,
        "swing_score": bs_swing,
        "composite_base": round(comp_base, 1),
        "weights": {
            "kline": w_kline,
            "breakout": w_breakout,
            "swing": w_swing,
            "composite": w_composite_base,
        },
        "total_weight": total_weight,
    }
    
    return round(min(final, 100), 1)


def calc_composite_v2_equal(r):
    """
    綜合分 v2 - 等權版（各維度同樣重要）
    
    簡單方案：K線分、突破分、波段分、綜合評分各佔25%
    只計算有效的維度，自動歸一化
    """
    ks = r.get("kline_score")
    if ks is None:
        return None
    
    bs_breakout = calc_breakout_score(r)
    bs_swing = calc_swing_score_strict(r)
    
    # 計算綜合基礎分
    comp_base = 0.0
    rsi = r.get("rsi14")
    if rsi is not None and 55 <= rsi <= 65:
        comp_base = 60  # 最佳區間
    elif rsi is not None and 45 <= rsi <= 75:
        comp_base = 40
    else:
        comp_base = 20
    
    vol_ratio = r.get("volume_ratio")
    if vol_ratio is not None:
        if 1.0 <= vol_ratio <= 2.0:
            comp_base += 20
    
    comp_base = min(comp_base, 100)
    
    # 收集有效分數
    scores = [ks]
    if bs_breakout is not None:
        scores.append(bs_breakout)
    if bs_swing is not None:
        scores.append(bs_swing)
    scores.append(comp_base)
    
    final = round(sum(scores) / len(scores), 1)
    
    r["composite_v2_breakdown"] = {
        "kline_score": ks,
        "breakout_score": bs_breakout,
        "swing_score": bs_swing,
        "composite_base": round(comp_base, 1),
        "method": "equal_weighted",
        "num_components": len(scores),
    }
    
    return final


# ══════════════════════════════════════════════════════════════════════════════
# 輔助函數：將股票記錄豐富化
# ══════════════════════════════════════════════════════════════════════════════

def enrich_record_with_new_scores(r):
    """
    在現有記錄基礎上計算新分數，同時保留舊分數以示對比
    """
    # 計算新分數
    r["breakout_score"] = calc_breakout_score(r)
    r["swing_score"] = calc_swing_score_strict(r)
    
    # 計算新綜合分（優先用智能版）
    r["composite_v2"] = calc_composite_v2_smart(r)
    
    # 保留舊分數供對比
    # r["kline_score"]        已存在
    # r["composite"]          已存在（原綜合分）
    # r["vcp_score"]          已存在（原波段分）
    
    return r


# ══════════════════════════════════════════════════════════════════════════════
# 篩選邏輯：多維度組合
# ══════════════════════════════════════════════════════════════════════════════

def apply_filter_by_four_scores(stocks, kline_min=50, breakout_min=None, 
                                swing_min=None, composite_min=60):
    """
    按四個維度篩選股票
    
    參數：
    - kline_min: K線分最低門檻
    - breakout_min: 突破分最低門檻（None 表示不篩選）
    - swing_min: 波段分最低門檻（None 表示不篩選）
    - composite_min: 綜合分最低門檻
    """
    result = []
    for s in stocks:
        # K線分必須達標
        ks = s.get("kline_score")
        if ks is None or ks < kline_min:
            continue
        
        # 突破分篩選
        if breakout_min is not None:
            bs = s.get("breakout_score")
            if bs is None or bs < breakout_min:
                continue
        
        # 波段分篩選
        if swing_min is not None:
            ss = s.get("swing_score")
            if ss is None or ss < swing_min:
                continue
        
        # 綜合分篩選
        comp = s.get("composite_v2")
        if comp is None or comp < composite_min:
            continue
        
        result.append(s)
    
    return result
