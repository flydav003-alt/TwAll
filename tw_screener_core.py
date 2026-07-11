"""
tw_screener_core.py
===================
純計算邏輯模組（不含 IO / API / 顯示）
供 fetch_data.py 與 streamlit_app.py 共用
"""

import numpy as np

# ──────────────────────────────────────────────────────────────
# 市場判斷
# ──────────────────────────────────────────────────────────────
_OTC_PREFIXES = {"6", "7", "8"}

def is_otc(stock_id: str) -> bool:
    sid = str(stock_id)
    if len(sid) == 4 and sid[0] in _OTC_PREFIXES:
        return True
    return False


def yahoo_tw_url(stock_id: str, market: str = "TW") -> str:
    suffix = ".TWO" if market == "TWO" else ".TW"
    return f"https://tw.stock.yahoo.com/quote/{stock_id}{suffix}"


# ──────────────────────────────────────────────────────────────
# SEPA+VCP 回檔波段分（須先通過 RS 門檻才計分）
# ──────────────────────────────────────────────────────────────
RS_GATE = 85          # RS分 >= 85 才有資格進入 VCP 評分（SEPA 規格）
DD_MIN, DD_MAX = 10, 30      # 60日最大回撤健康區間 %
SECOND_PULLBACK_RATIO = 0.6  # 第二次回檔 < 第一次 × 0.6 才算「收縮」


DD_MIN, DD_MAX = 10, 30
ZIGZAG_MIN_PERCENT = 3.0
VCP_MIN_BARS_BETWEEN_TURNS = 3


def calc_ma120(closes):
    if closes is None or len(closes) < 1:
        return None
    if len(closes) >= 120:
        return round(float(closes.iloc[-120:].mean()), 2)
    return round(float(closes.mean()), 2)  # 不足120天就用抓到的全部資料代替


def calc_max_drawdown_pct(closes, n=60):
    """近n日最大回撤(%)，資料不足就用抓到的全部資料代替"""
    if closes is None or len(closes) < 2:
        return None
    window = closes.iloc[-min(n, len(closes)):]
    roll_max = window.cummax()
    dd = (roll_max - window) / roll_max
    val = dd.max()
    return round(float(val) * 100, 1) if not np.isnan(val) else None


def _find_swing_points(values, window=5):
    """簡化版局部高低點偵測：用滾動窗口找局部極值，回傳 [(idx, price, 'H'/'L'), ...]"""
    n = len(values)
    points = []
    for i in range(window, n - window):
        seg = values[i - window:i + window + 1]
        if values[i] == seg.max():
            points.append((i, values[i], "H"))
        elif values[i] == seg.min():
            points.append((i, values[i], "L"))
    return points


def calc_vcp_contraction(closes, lookback=90, window=5):
    """
    取最近 lookback 天，找局部高低點，組成「高→低」回檔波段，
    比較最近兩次回檔深度是否收縮（第二次 < 第一次 × 0.6）。
    回傳 dict: {contracting: bool|None, leg1_pct, leg2_pct}
    資料不足或找不到兩次回檔時 contracting=None（不是False，避免誤判為「沒收縮」）
    """
    if closes is None or len(closes) < window * 2 + 2:
        return {"contracting": None, "leg1_pct": None, "leg2_pct": None}

    recent = closes.iloc[-lookback:] if len(closes) > lookback else closes
    vals = recent.values
    pts = _find_swing_points(vals, window=window)
    if len(pts) < 2:
        return {"contracting": None, "leg1_pct": None, "leg2_pct": None}

    # 過濾成 H/L 交替序列，同類型只留較極端者
    filtered = []
    for p in pts:
        if not filtered or filtered[-1][2] != p[2]:
            filtered.append(p)
        elif p[2] == "H" and p[1] > filtered[-1][1]:
            filtered[-1] = p
        elif p[2] == "L" and p[1] < filtered[-1][1]:
            filtered[-1] = p

    legs = []
    for i in range(len(filtered) - 1):
        a, b = filtered[i], filtered[i + 1]
        if a[2] == "H" and b[2] == "L" and a[1] > 0:
            legs.append(round((a[1] - b[1]) / a[1] * 100, 1))

    if len(legs) < 2:
        return {"contracting": None, "leg1_pct": None, "leg2_pct": None}

    leg1, leg2 = legs[-2], legs[-1]   # 倒數第二次、最近一次
    contracting = leg2 < leg1 * SECOND_PULLBACK_RATIO
    return {"contracting": contracting, "leg1_pct": leg1, "leg2_pct": leg2}


def _series_to_float_list(series):
    if series is None:
        return []
    out = []
    for v in series:
        try:
            out.append(float(v))
        except Exception:
            out.append(np.nan)
    return out


def _zigzag_algorithm(highs, lows=None, min_percent=ZIGZAG_MIN_PERCENT, min_bars=VCP_MIN_BARS_BETWEEN_TURNS):
    hi = _series_to_float_list(highs)
    lo = _series_to_float_list(lows) if lows is not None else hi
    n = min(len(hi), len(lo))
    if n < 3:
        return []
    hi, lo = hi[-n:], lo[-n:]

    first = next((i for i in range(n) if not np.isnan(hi[i]) and not np.isnan(lo[i])), None)
    if first is None:
        return []

    pivots = []
    trend = None
    cand_hi_i = cand_lo_i = first
    cand_hi, cand_lo = hi[first], lo[first]

    for i in range(first + 1, n):
        if np.isnan(hi[i]) or np.isnan(lo[i]):
            continue
        if hi[i] >= cand_hi:
            cand_hi, cand_hi_i = hi[i], i
        if lo[i] <= cand_lo:
            cand_lo, cand_lo_i = lo[i], i

        up_move = (hi[i] / cand_lo - 1) * 100 if cand_lo > 0 else 0
        down_move = (cand_hi - lo[i]) / cand_hi * 100 if cand_hi > 0 else 0

        if trend is None:
            if up_move >= min_percent and i - cand_lo_i >= min_bars:
                pivots.append((cand_lo_i, cand_lo, "L"))
                trend = "up"
                cand_hi, cand_hi_i = hi[i], i
            elif down_move >= min_percent and i - cand_hi_i >= min_bars:
                pivots.append((cand_hi_i, cand_hi, "H"))
                trend = "down"
                cand_lo, cand_lo_i = lo[i], i
            continue

        if trend == "up":
            if hi[i] >= cand_hi:
                cand_hi, cand_hi_i = hi[i], i
            elif (cand_hi - lo[i]) / cand_hi * 100 >= min_percent and i - cand_hi_i >= min_bars:
                pivots.append((cand_hi_i, cand_hi, "H"))
                trend = "down"
                cand_lo, cand_lo_i = lo[i], i
        else:
            if lo[i] <= cand_lo:
                cand_lo, cand_lo_i = lo[i], i
            elif (hi[i] / cand_lo - 1) * 100 >= min_percent and i - cand_lo_i >= min_bars:
                pivots.append((cand_lo_i, cand_lo, "L"))
                trend = "up"
                cand_hi, cand_hi_i = hi[i], i

    if trend == "up" and (not pivots or pivots[-1][2] != "H"):
        pivots.append((cand_hi_i, cand_hi, "H"))
    elif trend == "down" and (not pivots or pivots[-1][2] != "L"):
        pivots.append((cand_lo_i, cand_lo, "L"))

    filtered = []
    for p in sorted(pivots, key=lambda x: x[0]):
        if not filtered or filtered[-1][2] != p[2]:
            filtered.append(p)
        elif p[2] == "H" and p[1] > filtered[-1][1]:
            filtered[-1] = p
        elif p[2] == "L" and p[1] < filtered[-1][1]:
            filtered[-1] = p
    return filtered


def calc_vcp_contraction_strict(closes, highs=None, lows=None, volumes=None, lookback=90,
                                min_percent=ZIGZAG_MIN_PERCENT):
    if closes is None or len(closes) < 40:
        return {
            "contracting": None, "higher_high": None, "vol_shrink_quality": None,
            "leg1_pct": None, "leg2_pct": None, "detail": "insufficient price history",
        }

    recent_c = closes.iloc[-lookback:] if len(closes) > lookback else closes
    recent_h = highs.iloc[-len(recent_c):] if highs is not None else recent_c
    recent_l = lows.iloc[-len(recent_c):] if lows is not None else recent_c
    recent_v = volumes.iloc[-len(recent_c):] if volumes is not None else None

    pivots = _zigzag_algorithm(recent_h, recent_l, min_percent=min_percent)
    pullbacks = []
    for i in range(len(pivots) - 1):
        h, l = pivots[i], pivots[i + 1]
        if h[2] == "H" and l[2] == "L" and h[1] > 0 and l[0] > h[0]:
            pullbacks.append({"hi": h, "lo": l, "pct": round((h[1] - l[1]) / h[1] * 100, 1)})

    if len(pullbacks) < 2:
        return {
            "contracting": None, "higher_high": None, "vol_shrink_quality": None,
            "leg1_pct": None, "leg2_pct": None,
            "detail": f"not enough pullbacks; pivots={[(p[0], round(p[1], 2), p[2]) for p in pivots]}",
        }

    pb1, pb2 = pullbacks[-2], pullbacks[-1]
    leg1, leg2 = pb1["pct"], pb2["pct"]
    contracting = leg2 < leg1 * SECOND_PULLBACK_RATIO
    higher_high = bool(pb2["hi"][1] > pb1["hi"][1] * 1.01)

    vol_q = None
    if recent_v is not None:
        v = _series_to_float_list(recent_v)
        a0, a1 = pb1["hi"][0], pb1["lo"][0] + 1
        b0, b1 = pb2["hi"][0], pb2["lo"][0] + 1
        if a1 > a0 and b1 > b0:
            v1 = [x for x in v[a0:a1] if not np.isnan(x) and x > 0]
            v2 = [x for x in v[b0:b1] if not np.isnan(x) and x > 0]
            if v1 and v2:
                avg1, avg2 = float(np.mean(v1)), float(np.mean(v2))
                if avg1 > 0:
                    vol_q = round(max(0.0, min(1.0, 1 - avg2 / avg1)), 3)

    detail = (
        f"pivots={[(p[0], round(p[1], 2), p[2]) for p in pivots]}; "
        f"leg1={leg1}%, leg2={leg2}%, ratio={(leg2 / leg1 if leg1 else 0):.2f}; "
        f"higher_high={higher_high}; vol_shrink_quality={vol_q}"
    )
    return {
        "contracting": contracting,
        "higher_high": higher_high,
        "vol_shrink_quality": vol_q,
        "leg1_pct": leg1,
        "leg2_pct": leg2,
        "detail": detail,
    }


def calc_consolidation_vol_ratio(volumes, recent_n=10, base_n=60):
    """整理量 ÷ 60日均量：近recent_n日均量 / 近base_n日均量"""
    if volumes is None or len(volumes) < base_n:
        return None
    base_avg = volumes.iloc[-base_n:].mean()
    if base_avg <= 0:
        return None
    recent_avg = volumes.iloc[-recent_n:].mean()
    return round(float(recent_avg / base_avg), 2)


def calc_breakout_60d(closes, volumes, n=60, vol_mult=1.5):
    """今日是否60日新高 + 量達60日均量的vol_mult倍以上"""
    if closes is None or volumes is None or len(closes) < 2:
        return False
    window_c = closes.iloc[-min(n, len(closes)):]
    is_new_high = float(closes.iloc[-1]) >= float(window_c.max())
    if len(volumes) < min(n, len(closes)):
        return False
    base_avg = volumes.iloc[-min(n, len(volumes)):].mean()
    vol_ok = base_avg > 0 and float(volumes.iloc[-1]) >= base_avg * vol_mult
    return bool(is_new_high and vol_ok)


def calc_vcp_score(r):
    """
    SEPA+VCP 回檔波段分（0~100），需先通過 RS >= RS_GATE 門檻，否則回傳 None（未達標，非0分）。
    組成：MA趨勢排列(20) + 距52週高(15) + 半年漲幅(15) + 60日回撤健康區間(15) + 二次回檔收縮(25) + 整理量縮(10)
    """
    rs = r.get("rs_score")
    if rs is None or rs < RS_GATE:
        return None

    score = 0.0

    # MA20 > MA60 > MA120 排列 (20分)
    ma20, ma60, ma120 = r.get("ma20"), r.get("ma60"), r.get("ma120")
    if ma20 is not None and ma60 is not None and ma120 is not None:
        if ma20 > ma60 > ma120:
            score += 20
        elif ma20 > ma60 or ma60 > ma120:
            score += 8

    # 距52週高 <20% 最佳 (15分)
    w52 = r.get("week52_pct")   # 負值，例如 -8.0 代表低於高點8%
    if w52 is not None:
        if -20 <= w52 <= 0:
            score += 15
        elif -30 <= w52 < -20:
            score += 8

    # 半年漲幅(ret126d) >30% (15分)
    ret126 = r.get("ret126d")
    if ret126 is not None:
        if ret126 >= 30:
            score += 15
        elif 15 <= ret126 < 30:
            score += 8

    # 60日最大回撤落在 10~30% 健康區間 (15分)
    dd60 = r.get("dd60")
    if dd60 is not None:
        if DD_MIN <= dd60 <= DD_MAX:
            score += 15
        elif (5 <= dd60 < DD_MIN) or (DD_MAX < dd60 <= 40):
            score += 8

    # 第二次回檔較第一次收縮 (25分，VCP核心訊號)
    contracting = r.get("vcp_contracting")
    if contracting is True:
        score += 25
    elif contracting is False:
        score += 8   # 有偵測到兩次回檔但沒收縮，給部分分數

    # 整理量縮 <0.7×60日均量 (10分)
    cons_vr = r.get("cons_vol_ratio")
    if cons_vr is not None:
        if cons_vr < 0.7:
            score += 10
        elif cons_vr < 0.9:
            score += 5

    return round(min(score, 100), 1)


def calc_vcp_score_strict(r):
    rs = r.get("rs_score")
    if rs is None or rs < RS_GATE:
        r["vcp_score_breakdown"] = {}
        return None

    b = {}

    ma20, ma60, ma120 = r.get("ma20"), r.get("ma60"), r.get("ma120")
    b["ma_arrangement"] = 0
    if ma20 is not None and ma60 is not None and ma120 is not None:
        if ma20 > ma60 > ma120:
            b["ma_arrangement"] = 15
        elif ma20 > ma60 or ma60 > ma120:
            b["ma_arrangement"] = 6

    dd60 = r.get("dd60")
    b["drawdown_health"] = 0
    if dd60 is not None:
        if 10 <= dd60 <= 30:
            b["drawdown_health"] = 20
        elif 5 <= dd60 < 10 or 30 < dd60 <= 40:
            b["drawdown_health"] = 8

    contracting = r.get("vcp_contracting", r.get("contracting"))
    leg1, leg2 = r.get("leg1_pct"), r.get("leg2_pct")
    b["pullback_contraction"] = 0
    if contracting is True:
        b["pullback_contraction"] = 25
    elif contracting is False and leg1 is not None and leg2 is not None:
        ratio = leg2 / leg1 if leg1 else 9
        b["pullback_contraction"] = 10 if ratio < 0.85 else 4

    b["higher_high"] = 15 if r.get("vcp_higher_high", r.get("higher_high")) is True else 0

    vol_q = r.get("vcp_vol_shrink_quality", r.get("vol_shrink_quality"))
    b["vol_shrink_quality"] = 0
    if vol_q is not None:
        b["vol_shrink_quality"] = round(max(0.0, min(1.0, float(vol_q))) * 10, 1)
    else:
        cons_vr = r.get("cons_vol_ratio")
        if cons_vr is not None:
            b["vol_shrink_quality"] = 6 if cons_vr < 0.7 else (3 if cons_vr < 0.9 else 0)

    w52 = r.get("week52_pct")
    b["distance_52w_high"] = 0
    if w52 is not None:
        if -20 <= w52 <= 0:
            b["distance_52w_high"] = 10
        elif -30 <= w52 < -20:
            b["distance_52w_high"] = 4

    ret126 = r.get("ret126d")
    b["six_month_return"] = 0
    if ret126 is not None:
        if ret126 >= 30:
            b["six_month_return"] = 5
        elif 15 <= ret126 < 30:
            b["six_month_return"] = 2

    r["vcp_score_breakdown"] = b
    return round(min(sum(b.values()), 100), 1)


# ──────────────────────────────────────────────────────────────
# 波段分（Big-Swing Pullback Score）- 從舊版移植
# ──────────────────────────────────────────────────────────────
def calc_swing_score(r):
    """
    Big-swing pullback score. This is intentionally separate from VCP breakout
    scoring: it rewards strong stocks after a real pullback, before a volume
    breakout has already happened.
    評分組成：RS強度(20) + 趨勢排列(15) + 回撤深度(20) + 量縮(15) + 轉強訊號(15) + 距高點(10) + 風控(5)
    """
    b = {}

    rs = r.get("rs_score")
    b["rs_strength"] = 0
    if rs is not None:
        if rs >= 85:
            b["rs_strength"] = 20
        elif rs >= 75:
            b["rs_strength"] = 14
        elif rs >= 65:
            b["rs_strength"] = 8

    price, ma60, ma120 = r.get("price"), r.get("ma60"), r.get("ma120")
    b["trend"] = 0
    if price is not None and ma60 is not None and ma120 is not None:
        if price > ma60 > ma120:
            b["trend"] = 15
        elif price > ma60:
            b["trend"] = 10
        elif price >= ma60 * 0.97:
            b["trend"] = 6

    dd60 = r.get("dd60")
    b["pullback_depth"] = 0
    if dd60 is not None:
        if 12 <= dd60 <= 25:
            b["pullback_depth"] = 20
        elif 8 <= dd60 < 12 or 25 < dd60 <= 32:
            b["pullback_depth"] = 12

    cons_vr = r.get("cons_vol_ratio")
    b["pullback_volume_shrink"] = 0
    if cons_vr is not None:
        if cons_vr < 0.7:
            b["pullback_volume_shrink"] = 15
        elif cons_vr < 0.9:
            b["pullback_volume_shrink"] = 9

    b["turning_up"] = 0
    ma20 = r.get("ma20")
    prev_close = r.get("prev_close")
    ma20_pct = r.get("price_vs_ma20_pct")
    if price is not None and ma20 is not None:
        if prev_close is not None and prev_close < ma20 <= price:
            b["turning_up"] += 6
        elif r.get("ma20_rising") is True and ma20_pct is not None and 0 <= ma20_pct <= 5:
            b["turning_up"] += 4

    rsi, prev_rsi = r.get("rsi14"), r.get("prev_rsi14")
    if rsi is not None and prev_rsi is not None and 40 <= rsi <= 65:
        if prev_rsi < 50 <= rsi:
            b["turning_up"] += 5
        elif rsi - prev_rsi >= 3:
            b["turning_up"] += 4

    mh, mh_prev = r.get("macd_hist"), r.get("macd_hist_prev")
    if mh is not None and mh_prev is not None:
        if mh > mh_prev and mh > -0.01:
            b["turning_up"] += 4
        elif mh > mh_prev:
            b["turning_up"] += 2
    b["turning_up"] = min(b["turning_up"], 15)

    w52 = r.get("week52_pct")
    b["not_too_close_to_breakout"] = 0
    if w52 is not None:
        if -25 <= w52 <= -10:
            b["not_too_close_to_breakout"] = 10
        elif -10 < w52 <= -5:
            b["not_too_close_to_breakout"] = 6
        elif -5 < w52 <= -3:
            b["not_too_close_to_breakout"] = 2

    b["risk_control"] = 0
    if price is not None and ma60 is not None:
        if price >= ma60 * 0.97 and (dd60 is None or dd60 <= 32):
            b["risk_control"] = 5

    score = sum(b.values())
    if r.get("vcp_breakout") is True or (r.get("volume_ratio") or 0) >= 2.0:
        score = min(score, 65)
    if rsi is not None and rsi > 75:
        score = min(score, 70)
    if w52 is not None and w52 > -3:
        score = min(score, 70)
    if price is not None and ma120 is not None and price < ma120:
        score = min(score, 50)

    r["swing_score_breakdown"] = b
    return round(min(score, 100), 1)


def calc_vcp_status(r):
    rs = r.get("rs_score")
    score = r.get("vcp_score")
    w52 = r.get("week52_pct")
    breakout = bool(r.get("vcp_breakout"))
    contracting = r.get("vcp_contracting", r.get("contracting"))
    higher_high = r.get("vcp_higher_high", r.get("higher_high"))
    vol_q = r.get("vcp_vol_shrink_quality", r.get("vol_shrink_quality"))
    rs5d = r.get("rs5d")

    if rs is None or rs < RS_GATE:
        return "RS未達標"
    if score is None:
        return "未評分"
    if breakout:
        if rs5d is not None and rs5d >= 10:
            return "突破過熱"
        return "已突破"
    if score < 45 or contracting is None:
        return "結構不足"
    if w52 is not None and -8 <= w52 <= 0 and score >= 65:
        return "接近突破"
    if contracting is True and higher_high is True and (vol_q is None or vol_q >= 0.25):
        return "高品質整理"
    if score >= 60:
        return "整理中"
    return "觀察"


# ──────────────────────────────────────────────────────────────
# 布林通道分（BB分）— 擠壓／下軌反轉／上軌突破 三選一 + 防呆Gate
# ──────────────────────────────────────────────────────────────
# 設計精神：跟VCP（zigzag型態法）互補，BB分是純統計法（標準差），
# 抓的是「進場時機」而非「選股」，主用途是跟其他四維度搭配做組合回測。
BB_PERIOD = 20
BB_WIDTH_LOOKBACK = 120     # 算寬度百分位的回顧天數
BB_LOWER_TOUCH_PB = 0.2     # %B <= 此值視為觸及下軌
BB_UPPER_BREAK_PB = 0.85    # %B >= 此值視為接近/站上上軌
BB_BAND_WALK_WINDOW = 10    # 判斷「沿下軌走空頭」的觀察天數
BB_BAND_WALK_MIN_TOUCHES = 4  # 這段天數內碰下軌達此次數 → 視為band walk空頭，非反轉


def calc_bb_data(closes, period=BB_PERIOD, width_lookback=BB_WIDTH_LOOKBACK,
                  band_walk_window=BB_BAND_WALK_WINDOW):
    """
    回傳最新一天的布林通道原始數據，供 calc_bb_score 使用。
    - bb_width_pct：目前通道寬度在近 width_lookback 天中的百分位（越低＝越擠壓）
    - bb_percent_b：價格在通道中的相對位置，0=下軌，1=上軌
    - bb_mid_rising：中軌（=MA20）近5日是否上揚
    - bb_lower_touch_days_10：近 band_walk_window 天內 %B<=BB_LOWER_TOUCH_PB 的次數
      （用來判斷是否為「沿下軌走的空頭趨勢」而非健康拉回反轉）
    """
    if closes is None or len(closes) < period + 5:
        return None

    ma = closes.rolling(period).mean()
    std = closes.rolling(period).std(ddof=0)
    upper = ma + 2 * std
    lower = ma - 2 * std
    width = (upper - lower) / ma  # 標準化寬度，可跨股票比較

    last_close = float(closes.iloc[-1])
    last_upper = float(upper.iloc[-1])
    last_lower = float(lower.iloc[-1])
    last_mid   = float(ma.iloc[-1])

    if np.isnan(last_upper) or np.isnan(last_lower) or np.isnan(last_mid):
        return None

    hist_width = width.iloc[-min(width_lookback, len(width)):].dropna()
    last_width = float(width.iloc[-1]) if not np.isnan(width.iloc[-1]) else None
    width_pct = None
    if last_width is not None and len(hist_width) >= 20:
        width_pct = round(float((hist_width < last_width).sum() / len(hist_width) * 100), 1)

    band_range = last_upper - last_lower
    percent_b = round((last_close - last_lower) / band_range, 3) if band_range > 0 else None

    mid_rising = None
    ma_valid = ma.dropna()
    if len(ma_valid) >= 6:
        mid_rising = bool(ma_valid.iloc[-1] > ma_valid.iloc[-6])

    pb_series = (closes - lower) / (upper - lower).replace(0, np.nan)
    recent_pb = pb_series.iloc[-min(band_walk_window, len(pb_series)):].dropna()
    lower_touch_days = int((recent_pb <= BB_LOWER_TOUCH_PB).sum()) if len(recent_pb) else 0

    # 突破前擠壓：改用「3~7天前的區間最小寬度百分位」取代單一天snapshot，
    # 避免單日雜訊（例如剛好那天資料異常窄）誤判成「有經過真正擠壓」。
    pre_squeeze_min_pct = None
    if len(width) >= 8 and len(hist_width) >= 20:
        window_vals = width.iloc[-8:-2].dropna()  # 3~7天前的區間
        if len(window_vals):
            pcts = [float((hist_width < w).sum() / len(hist_width) * 100) for w in window_vals]
            pre_squeeze_min_pct = round(min(pcts), 1)

    # 突破新鮮度：近10日內 %B>=BB_UPPER_BREAK_PB 的天數，用來分辨「剛突破」vs「已經噴一段時間才追」
    upper_touch_days = int((recent_pb >= BB_UPPER_BREAK_PB).sum()) if len(recent_pb) else 0

    # 擠壓持續天數：近5日內，寬度百分位<=35（squeeze門檻）的天數有幾天
    # 用來區分「已經窄了一段時間的真蓄勢」vs「今天剛好雜訊掉到最低的單日假象」
    squeeze_persist_days = 0
    if len(hist_width) >= 20 and len(width) >= 5:
        recent_width = width.iloc[-5:].dropna()
        for w in recent_width:
            wp_i = float((hist_width < w).sum() / len(hist_width) * 100)
            if wp_i <= 35:
                squeeze_persist_days += 1

    return {
        "bb_upper": round(last_upper, 2),
        "bb_lower": round(last_lower, 2),
        "bb_mid": round(last_mid, 2),
        "bb_width_pct": width_pct,
        "bb_pre_squeeze_min_pct": pre_squeeze_min_pct,
        "bb_percent_b": percent_b,
        "bb_mid_rising": mid_rising,
        "bb_lower_touch_days_10": lower_touch_days,
        "bb_upper_touch_days_10": upper_touch_days,
        "bb_squeeze_persist_days": squeeze_persist_days,
    }


def detect_bb_setup(r):
    """三選一 setup 判定，判斷不出來（中性區）回傳 'neutral'。"""
    pb = r.get("bb_percent_b")
    wp = r.get("bb_width_pct")
    if pb is None:
        return None
    if pb <= BB_LOWER_TOUCH_PB:
        return "lower_reversal"
    if pb >= BB_UPPER_BREAK_PB:
        return "upper_breakout"
    if 0.3 <= pb <= 0.65 and wp is not None and wp <= 35:
        return "squeeze_consolidation"
    return "neutral"


def bb_gate_multiplier(r, setup):
    """
    防呆 Gate：趨勢死亡 / band walk(沿下軌走空頭) 的下軌反轉直接砍分，
    不讓「還在跌」被誤判成「要反彈」。
    """
    if setup != "lower_reversal":
        return 1.0

    price, ma120 = r.get("price"), r.get("ma120")
    trend_alive = ma120 is None or price is None or price >= ma120 * 0.93

    band_walk_days = r.get("bb_lower_touch_days_10", 0) or 0
    is_band_walk = band_walk_days >= BB_BAND_WALK_MIN_TOUCHES

    if is_band_walk:
        return 0.15
    if not trend_alive:
        return 0.5
    return 1.0


def _clamp01(x):
    return max(0.0, min(1.0, x))


def _score_lower_reversal(r):
    """
    大波段下軌反轉，設計滿分100（乘Gate前）：
      - 基礎觸及 10（固定，代表「符合了pb<=0.2這件事本身」）
      - 反轉訊號 35（連續）：底背離給滿35；沒有背離時，用RSI超賣程度連續給分（RSI越低分越高）
      - 量能 25（連續）：量縮越深分越高，爆量下殺則倒扣
      - 首次觸及加成 15：近10日「第一次」碰到下軌，比已經碰了好幾次更值得重視
      - 中軌止跌翻揚 15：MA20已經翻揚，代表止跌訊號更成熟
    連續函數取代原本的「碰到就給8分」門檻式設計，避免同一批股票分數擠在同一個數字上。
    """
    b = {}
    rsi = r.get("rsi14")
    bull_div = r.get("rsi_div") == "bull"
    vr = r.get("volume_ratio")
    touch_days = r.get("bb_lower_touch_days_10", 0) or 0

    b["base_touch"] = 10

    if bull_div:
        b["reversal_signal"] = 35
    elif rsi is not None:
        # RSI 28 以上不給分，RSI 越接近 0 分數越高，斜率在 28→0 之間連續分布
        b["reversal_signal"] = round(_clamp01((28 - rsi) / 28) * 20, 1)
    else:
        b["reversal_signal"] = 0

    if vr is not None:
        if vr <= 1.0:
            b["volume"] = round(_clamp01(1 - vr) * 25, 1)      # 量縮越深分越高，vr=0時滿分25
        else:
            b["volume"] = -round(_clamp01((vr - 1) / 1.5) * 20, 1)  # 爆量下殺倒扣，最多扣20
    else:
        b["volume"] = 0

    # 首次觸及：這段10日內只碰過1次分數最高，碰越多次（還沒到band walk門檻）分數遞減
    if touch_days <= 1:
        b["first_touch_bonus"] = 15
    elif touch_days <= 2:
        b["first_touch_bonus"] = 8
    else:
        b["first_touch_bonus"] = 0

    b["mid_turn_bonus"] = 15 if r.get("bb_mid_rising") is True else 0
    return b


def _score_squeeze(r):
    """
    擠壓蓄勢，設計滿分100（乘Gate前）：
      - 擠壓強度 50（連續）：寬度百分位線性換算，越窄分越高（0百分位=50分，>=60百分位=0分）
      - 中軌方向 20：MA20上揚才給分（定多空方向）
      - 量縮程度 30（連續）：量能越縮越像盤整末端
      - 持續性乘數：擠壓強度+量縮程度這兩項天生高度相關（真正的盤整末端本來就會同時出現），
        單日雜訊就能讓兩項一起衝滿分，鑑別力等於只驗證了一件事卻假裝驗證了兩件事。
        用「近5日內有幾天寬度百分位<=35」當持續性乘數：只有1天在窄檔（可能是雜訊）打6折，
        持續2~3天打8~9折，持續4天以上（真正蓄勢一段時間）才給滿額。
    """
    b = {}
    wp = r.get("bb_width_pct")
    vr = r.get("volume_ratio")
    persist_days = r.get("bb_squeeze_persist_days", 0) or 0

    squeeze_raw = round(_clamp01((60 - wp) / 60) * 50, 1) if wp is not None else 0
    shrink_raw = round(_clamp01(1 - vr) * 30, 1) if (vr is not None and vr <= 1.0) else 0

    if persist_days <= 1:
        persist_mult = 0.6
    elif persist_days <= 3:
        persist_mult = 0.6 + (persist_days - 1) * 0.15   # 2天0.75, 3天0.9
    else:
        persist_mult = 1.0

    b["squeeze"] = round(squeeze_raw * persist_mult, 1)
    b["volume_shrink"] = round(shrink_raw * persist_mult, 1)
    b["mid_trend"] = 20 if r.get("bb_mid_rising") is True else 0
    b["persist_mult_applied"] = persist_mult
    return b


def _score_upper_breakout(r):
    """
    上軌突破確認，設計滿分100（乘Gate前）：
      - 基礎站上 10
      - 爆量確認 40（連續）：量比越大分越高，vr>=2.5給滿分
      - 突破前擠壓加成 50（連續）：用3~7天前「區間最小」寬度百分位（而非單一天snapshot），
        越窄代表這是「擠壓後噴出」而非「已經噴一大段」，同時降低單日雜訊誤判的機率
      - 追高扣分：目前寬度百分位若已經很寬（>50），代表不是剛起漲，用連續函數倒扣最多25分
      - 新鮮度乘數：爆量確認+突破前擠壓加成這兩項是在獎勵「這是一次真突破事件」，
        如果近10日已經有好幾天都站上上軌（代表股票已經噴了一段時間才追進來），
        今天剛好爆一次量不代表這是「突破當下」，要打折避免追高被誤判成剛起漲。
    """
    b = {}
    vr = r.get("volume_ratio")
    wp = r.get("bb_width_pct")
    wp_pre = r.get("bb_pre_squeeze_min_pct")
    touch_days = r.get("bb_upper_touch_days_10", 0) or 0

    volume_raw = round(_clamp01((vr - 1) / 1.5) * 40, 1) if vr is not None else 0
    squeeze_bonus_raw = round(_clamp01((30 - wp_pre) / 30) * 50, 1) if wp_pre is not None else 0

    if touch_days <= 1:
        fresh_mult = 1.0     # 第一天站上上軌，最新鮮
    elif touch_days <= 3:
        fresh_mult = 0.7
    elif touch_days <= 5:
        fresh_mult = 0.4
    else:
        fresh_mult = 0.15    # 已經噴超過5天才追，視為追高不是真突破

    b["base_touch"] = 10
    b["volume_confirm"] = round(volume_raw * fresh_mult, 1)
    b["post_squeeze_bonus"] = round(squeeze_bonus_raw * fresh_mult, 1)
    b["fresh_mult_applied"] = fresh_mult

    if wp is not None and wp > 50:
        b["chase_penalty"] = -round(_clamp01((wp - 50) / 50) * 25, 1)
    else:
        b["chase_penalty"] = 0
    return b


def calc_bb_score(r):
    """
    布林通道分（0~100）。三種高勝率型態互斥判定 + 防呆Gate（非線性，直接乘）。
    回傳 (score, setup)；setup 為 None 時代表資料不足，score 也是 None。
    """
    setup = detect_bb_setup(r)
    if setup is None:
        r["bb_score_breakdown"] = {}
        return None, None

    if setup == "lower_reversal":
        b = _score_lower_reversal(r)
    elif setup == "squeeze_consolidation":
        b = _score_squeeze(r)
    elif setup == "upper_breakout":
        b = _score_upper_breakout(r)
    else:
        b = {"neutral": 0}

    raw = sum(v for k, v in b.items() if k not in ("gate_multiplier", "persist_mult_applied", "fresh_mult_applied"))
    mult = bb_gate_multiplier(r, setup)
    final = round(max(0, min(raw * mult, 100)), 1)

    b["gate_multiplier"] = mult
    r["bb_score_breakdown"] = b
    return final, setup


# ──────────────────────────────────────────────────────────────
# 多週期漲幅 & 橫向排名 RS 分數
# ──────────────────────────────────────────────────────────────
RS_PERIODS = [("ret21d", 21, 0.4), ("ret63d", 63, 0.3), ("ret126d", 126, 0.2), ("ret252d", 252, 0.1)]


def calc_ret_n(closes, n):
    """算 n 個交易日漲幅。資料不足 n 天時，用抓到的最遠資料代替，不額外要求補抓。"""
    if closes is None or len(closes) < 2:
        return None
    idx = min(n, len(closes) - 1)
    try:
        return round((float(closes.iloc[-1]) / float(closes.iloc[-(idx + 1)]) - 1) * 100, 2)
    except Exception:
        return None


def calc_rs_score(results):
    """
    跨股票橫向排名，算出 RS 分數（0~100，越高代表相對同期股票越強勢）。
    近期權重最重：0.4×21日 + 0.3×63日 + 0.2×126日 + 0.1×252日
    需在所有股票資料都收集完之後（results 為 list[dict]）才能呼叫，
    單檔逐筆抓取時無法算出橫向排名。
    會直接把 rs_score 寫回每個 dict。
    """
    for key, _, _ in RS_PERIODS:
        vals = sorted(set(r[key] for r in results if r.get(key) is not None))
        if not vals:
            continue
        rank_of = {v: i for i, v in enumerate(vals)}
        n = len(vals)
        for r in results:
            v = r.get(key)
            r[f"{key}_rank"] = round(rank_of[v] / (n - 1) * 100, 1) if v is not None and n > 1 else None

    for r in results:
        total_w = 0.0
        score = 0.0
        for key, _, w in RS_PERIODS:
            rk = r.get(f"{key}_rank")
            if rk is not None:
                score += w * rk
                total_w += w
        # 缺值時用剩餘權重正規化，避免新股(資料不足)被低估
        r["rs_score"] = round(score / total_w, 1) if total_w > 0 else None
    return results


# ──────────────────────────────────────────────────────────────
# 今日訊號優先順序
# ──────────────────────────────────────────────────────────────
SIGNAL_RANK = {
    "💥突破放量": 4,
    "🚀主力進場": 3,
    "✅洗盤結束": 2,
    "📉量縮整理": 1,
    "": 0,
}

SORT_MODES = {
    "kline":     ("kline_score",   False, "K線技術評分"),
    "composite": ("composite",     False, "綜合評分"),
    "volume":    ("volume_ratio",  False, "爆量倍數"),
    "rsi":       ("rsi14",         True,  "RSI超賣（越低越超賣）"),
    "inst":      ("inst_buy_days", False, "三大法人連買天數"),
    "signal":    ("signal_rank",   False, "今日訊號"),
}


# ──────────────────────────────────────────────────────────────
# RSI 計算
# ──────────────────────────────────────────────────────────────
def calc_rsi(series, period=14):
    if len(series) < period + 1:
        return None
    delta = series.diff().dropna()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, np.nan)
    rsi   = 100 - (100 / (1 + rs))
    val   = rsi.iloc[-1]
    return round(float(val), 1) if not np.isnan(val) else None


# ──────────────────────────────────────────────────────────────
# RSI 背離偵測
# ──────────────────────────────────────────────────────────────
def _detect_rsi_divergence(data, rsi_arr):
    n = len(data)
    if n < 20:
        return None
    wb = 8
    price_highs, price_lows = [], []
    rsi_highs,   rsi_lows   = [], []
    for i in range(wb, n - wb):
        rv = rsi_arr[i]
        if rv is None:
            continue
        window_rsi = [v for v in rsi_arr[i-wb:i+wb+1] if v is not None]
        is_p_high = all(data[j]["close"] <= data[i]["close"] for j in range(i-wb, i+wb+1) if j != i)
        is_p_low  = all(data[j]["close"] >= data[i]["close"] for j in range(i-wb, i+wb+1) if j != i)
        is_r_high = all(r <= rv for r in window_rsi)
        is_r_low  = all(r >= rv for r in window_rsi)
        if is_p_high and is_r_high:
            price_highs.append({"i": i, "price": data[i]["close"]})
            rsi_highs.append({"i": i, "val": rv})
        if is_p_low and is_r_low:
            price_lows.append({"i": i, "price": data[i]["close"]})
            rsi_lows.append({"i": i, "val": rv})
    if (len(price_highs) >= 2 and len(rsi_highs) >= 2
            and price_highs[-1]["price"] > price_highs[-2]["price"]
            and rsi_highs[-1]["val"]    < rsi_highs[-2]["val"]
            and price_highs[-1]["i"] - price_highs[-2]["i"] >= 10):
        return "bear"
    if (len(price_lows) >= 2 and len(rsi_lows) >= 2
            and price_lows[-1]["price"] < price_lows[-2]["price"]
            and rsi_lows[-1]["val"]    > rsi_lows[-2]["val"]
            and price_lows[-1]["i"] - price_lows[-2]["i"] >= 10):
        return "bull"
    return None


# ──────────────────────────────────────────────────────────────
# K線技術評分引擎（完整移植，零修改）
# ──────────────────────────────────────────────────────────────
_STRATEGY_WEIGHTS = {
    "balanced": dict(ma=1.0, rsi=1.0, kd=1.0, macd=1.0, vol=1.0, pattern=1.0),
    "breakout": dict(ma=1.3, rsi=0.6, kd=0.7, macd=1.4, vol=1.8, pattern=1.3),
    "pullback": dict(ma=1.4, rsi=1.2, kd=1.2, macd=0.5, vol=1.3, pattern=1.1),
    "reversal": dict(ma=0.4, rsi=2.0, kd=2.0, macd=0.6, vol=1.5, pattern=1.8),
}


def calc_kline_score(ohlcv):
    data = ohlcv
    n = len(data)
    if n < 20:
        return None

    def _ma(p):
        return [None if i < p-1 else sum(d["close"] for d in data[i-p+1:i+1]) / p
                for i in range(n)]

    def _rsi_arr(p=14):
        g, l = [], []
        for i in range(1, n):
            dv = data[i]["close"] - data[i-1]["close"]
            g.append(dv if dv > 0 else 0)
            l.append(-dv if dv < 0 else 0)
        return [None if i < p else
                (100 if sum(l[i-p:i]) == 0 else
                 100 - 100 / (1 + sum(g[i-p:i])/p / (sum(l[i-p:i])/p)))
                for i in range(n)]

    def _kdj(p=9):
        pk, pd_ = 50.0, 50.0
        result = []
        for i in range(n):
            if i < p-1:
                result.append(None); continue
            sl = data[i-p+1:i+1]
            hi = max(b["high"] for b in sl)
            lo = min(b["low"]  for b in sl)
            rsv = 50 if hi == lo else (data[i]["close"] - lo) / (hi - lo) * 100
            kv = pk * 2/3 + rsv / 3
            dv = pd_ * 2/3 + kv / 3
            pk, pd_ = kv, dv
            result.append({"k": kv, "d": dv})
        return result

    def _bb(p=20):
        ma_arr = _ma(p)
        result = []
        for i in range(n):
            if ma_arr[i] is None:
                result.append(None); continue
            sl = data[i-p+1:i+1]; mean = ma_arr[i]
            std = (sum((d["close"] - mean)**2 for d in sl) / p) ** 0.5
            result.append({"upper": mean + 2*std, "lower": mean - 2*std, "mid": mean})
        return result

    def _avg_vol(p=20):
        return [None if i < p-1 else sum(d["volume"] for d in data[i-p+1:i+1]) / p
                for i in range(n)]

    def _macd(fast=12, slow=26, sig=9):
        closes = [d["close"] for d in data]
        def ema(arr, p):
            if not arr: return []
            k = 2/(p+1); e = arr[0]; r = []
            for i, v in enumerate(arr):
                e = v*k + e*(1-k) if i > 0 else e
                r.append(e)
            return r
        ef, es = ema(closes, fast), ema(closes, slow)
        ml = [ef[i] - es[i] for i in range(n)]
        ml_slice = ml[slow-1:]
        sl2 = ema(ml_slice, sig) if len(ml_slice) >= sig else []
        result = []
        for i in range(n):
            if i < slow-1:
                result.append(None); continue
            si = i - (slow-1); m = ml[i]
            sg = sl2[si-(sig-1)] if sl2 and si >= sig-1 else None
            result.append({"macd": m, "signal": sg, "hist": m-sg if sg is not None else None})
        return result

    ma5a  = _ma(5); ma10a = _ma(10); ma20a = _ma(20); ma60a = _ma(60)
    rsiA  = _rsi_arr(); kdjA = _kdj(); bbA = _bb(); avgVA = _avg_vol()
    macdA = _macd()

    last = data[n-1]; prev = data[n-2]
    lma5  = ma5a[n-1];  lma10 = ma10a[n-1]; lma20 = ma20a[n-1]; lma60 = ma60a[n-1]
    pma5  = ma5a[n-2];  pma10 = ma10a[n-2]
    lrsi  = rsiA[n-1];  prsi  = rsiA[n-2]
    lkdj  = kdjA[n-1];  pkdj  = kdjA[n-2]
    lbb   = bbA[n-1];   lavg  = avgVA[n-1]
    lmacd = macdA[n-1]; pmacd = macdA[n-2]

    body       = abs(last["close"] - last["open"])
    range_     = last["high"] - last["low"]
    upShadow   = last["high"] - max(last["close"], last["open"])
    downShadow = min(last["close"], last["open"]) - last["low"]
    isBull = last["close"] > last["open"]
    isBear = last["close"] < last["open"]
    volRatio = (last["volume"] / lavg) if lavg else 1.0

    vol20avg_as = sum(d["volume"] for d in data[-20:]) / min(20, n)
    vol5avg_as  = sum(d["volume"] for d in data[-5:])  / min(5, n)
    volShrink_as = vol5avg_as < vol20avg_as * 0.85
    lma60_as = (sum(d["close"] for d in data[-60:]) / 60) if n >= 60 else None
    week5  = sum(d["close"] for d in data[-5:])  / min(5, n)
    week20 = sum(d["close"] for d in data[-20:]) / min(20, n)

    if lrsi is not None and lrsi < 35 and lkdj and lkdj["k"] < 30:
        auto_strat = "reversal"
    elif (volRatio > 1.5 and isBull and lmacd and lmacd["macd"] is not None
          and lmacd["macd"] > 0 and lmacd["hist"] is not None and lmacd["hist"] > 0):
        auto_strat = "breakout"
    elif lma20 and abs(last["close"] - lma20)/lma20 < 0.05 and volShrink_as and lrsi and 38 < lrsi < 62:
        auto_strat = "pullback"
    elif lma60_as and abs(last["close"] - lma60_as)/lma60_as < 0.06 and volShrink_as and lrsi and lrsi > 35:
        auto_strat = "pullback"
    elif week5 > week20 and lma5 and lma10 and lma20 and lma5 > lma10 > lma20:
        auto_strat = "breakout"
    else:
        auto_strat = "balanced"

    w = _STRATEGY_WEIGHTS[auto_strat]

    ma_bull = 0;  ma_bear = 0
    rsi_bull = 0; rsi_bear = 0
    kd_bull  = 0; kd_bear  = 0
    macd_bull= 0; macd_bear= 0
    vol_bull = 0; vol_bear = 0
    pat_bull = 0; pat_bear = 0
    extra_bull = 0; extra_bear = 0

    if lma5 and lma10 and lma20 and lma60:
        if lma5 > lma10 > lma20 > lma60:
            ma_bull += 15
        elif lma5 > lma10 > lma20:
            ma_bull += 8
        elif lma5 < lma10 < lma20:
            ma_bear += 4 if auto_strat == "pullback" else 12
    if lma5 and pma5 and lma10 and pma10:
        if pma5 <= pma10 and lma5 > lma10:   ma_bull += 10
        elif pma5 >= pma10 and lma5 < lma10: ma_bear += 10
    if lma20:
        if last["close"] > lma20: ma_bull += 5
    if lma60:
        if last["close"] > lma60: ma_bull += 5
    if lma20 and n >= 2:
        prev_close = data[n-2]["close"]
        if prev_close <= lma20 and last["close"] > lma20:
            ma_bull += 8
        elif prev_close >= lma20 and last["close"] < lma20:
            ma_bear += 8

    _RSI_THRESHOLDS = {
        "balanced": (70, 35),
        "breakout": (85, 30),
        "pullback": (65, 38),
        "reversal": (58, 42),
    }
    rsiOB, rsiOS = _RSI_THRESHOLDS[auto_strat]
    if lrsi is not None:
        if prsi is not None and prsi < rsiOS and lrsi > rsiOS:
            rsi_bull += 10
        elif lrsi < rsiOS:
            rsi_bull += 6
        elif prsi is not None and prsi > rsiOB and lrsi < rsiOB:
            rsi_bear += 8
        elif lrsi > 80:
            rsi_bear += 8
        elif lrsi > rsiOB:
            rsi_bear += 4
        elif lrsi >= 50:
            rsi_bull += 4

    div_result = _detect_rsi_divergence(data, rsiA)
    if div_result == "bull":   rsi_bull += 14
    elif div_result == "bear": rsi_bear += 14

    if lkdj and pkdj:
        if pkdj["k"] < pkdj["d"] and lkdj["k"] > lkdj["d"] and lkdj["k"] < 50:
            kd_bull += 12
        elif pkdj["k"] < pkdj["d"] and lkdj["k"] > lkdj["d"]:
            kd_bull += 7
        elif pkdj["k"] > pkdj["d"] and lkdj["k"] < lkdj["d"] and lkdj["k"] > 80:
            kd_bear += 12
        elif pkdj["k"] > pkdj["d"] and lkdj["k"] < lkdj["d"]:
            kd_bear += 6
        elif lkdj["k"] > lkdj["d"] and lkdj["k"] < 80:
            kd_bull += 3

    if lmacd and pmacd:
        if pmacd["macd"] is not None and lmacd["macd"] is not None:
            if lmacd["macd"] > 0:  macd_bull += 5
            else:                   macd_bear += 5
            if pmacd["macd"] < 0 and lmacd["macd"] >= 0:   macd_bull += 12
            elif pmacd["macd"] > 0 and lmacd["macd"] <= 0: macd_bear += 12
        if all(x is not None for x in [pmacd["signal"], lmacd["signal"], pmacd["macd"], lmacd["macd"]]):
            if pmacd["macd"] < pmacd["signal"] and lmacd["macd"] > lmacd["signal"]: macd_bull += 8
            elif pmacd["macd"] > pmacd["signal"] and lmacd["macd"] < lmacd["signal"]: macd_bear += 8
        if lmacd["hist"] is not None and pmacd["hist"] is not None:
            if lmacd["hist"] > 0 and lmacd["hist"] > pmacd["hist"]:    macd_bull += 4
            elif lmacd["hist"] < 0 and lmacd["hist"] < pmacd["hist"]:  macd_bear += 4

    if isBull and volRatio > 1.5:
        vol_bull += 10
    elif isBull and volRatio > 1.2:
        vol_bull += 6
    elif isBull and 0.6 <= volRatio <= 1.2 and auto_strat == "pullback":
        vol_bull += 8
    elif isBear and volRatio > 1.5:
        vol_bear += 10
    elif volRatio < 0.5 and auto_strat == "pullback":
        vol_bull += 5

    if lbb:
        if last["close"] < lbb["lower"]:   pat_bull += 7
        elif last["close"] > lbb["upper"]: pat_bear += 4

    r60 = data[-min(60, n):]
    hi60 = max(d["high"] for d in r60); lo60 = min(d["low"] for d in r60)
    pos60 = (last["close"] - lo60) / (hi60 - lo60 + 1e-9)

    def eff_pts(dir_, min_vol=1.0):
        vol_ok = volRatio >= min_vol
        if dir_ == "bull":
            if pos60 < 0.35 and vol_ok: return 12
            if pos60 < 0.35:             return 7
            if pos60 > 0.65:             return 3
            return 7
        else:
            if pos60 > 0.65 and vol_ok: return 12
            if pos60 > 0.65:             return 7
            if pos60 < 0.35:             return 3
            return 7

    if range_ > 0 and downShadow >= body*2 and upShadow <= body*0.5 and body > 0:
        pat_bull += eff_pts("bull", 1.2)
    if isBull and body >= range_*0.7 and volRatio >= 1.2:
        pat_bull += eff_pts("bull", 1.2) + 2
    if isBear and body >= range_*0.7 and volRatio >= 1.2:
        pat_bear += eff_pts("bear", 1.2) + 2
    if isBull and prev["close"] < prev["open"] and last["open"] <= prev["close"] and last["close"] >= prev["open"]:
        pat_bull += eff_pts("bull", 1.3) + 3
    if isBear and prev["close"] > prev["open"] and last["open"] >= prev["close"] and last["close"] <= prev["open"]:
        pat_bear += eff_pts("bear", 1.3) + 3
    if isBear and volRatio > 2.5:
        pat_bear += 12
    if isBull and volRatio > 2.0 and n > 5 and last["close"] > max(d["high"] for d in data[-5:]):
        pat_bull += 13
    if n >= 3:
        b0, b1, b2 = data[n-3], data[n-2], data[n-1]
        if (b0["close"]>b0["open"] and b1["close"]>b1["open"] and b2["close"]>b2["open"]
                and b1["close"]>b0["close"] and b2["close"]>b1["close"]):
            pat_bull += eff_pts("bull", 1.0) + 5
        if (b0["close"]<b0["open"] and b1["close"]<b1["open"] and b2["close"]<b2["open"]
                and b1["close"]<b0["close"] and b2["close"]<b1["close"]):
            pat_bear += eff_pts("bear", 1.0) + 5
        if (b0["close"]<b0["open"] and abs(b1["close"]-b1["open"]) < (b1["high"]-b1["low"])*0.3
                and b2["close"]>b2["open"] and b2["close"] >= (b0["open"]+b0["close"])/2):
            pat_bull += eff_pts("bull", 1.2) + 5

    day_range = last["high"] - last["low"] or 0.01
    close_pos = (last["close"] - last["low"]) / day_range
    if close_pos >= 0.8:    extra_bull += 5
    elif close_pos >= 0.6:  extra_bull += 3
    elif close_pos <= 0.2:  extra_bear += 5
    elif close_pos <= 0.4:  extra_bear += 3

    upper_ratio = upShadow / day_range if day_range > 0 else 0
    lower_ratio = downShadow / day_range if day_range > 0 else 0
    if upper_ratio > 0.35 and isBull:    extra_bear += 3
    elif upper_ratio > 0.35:              extra_bear += 5
    elif lower_ratio > 0.35 and isBull:  extra_bull += 5
    elif lower_ratio > 0.35:              extra_bull += 3
    elif upper_ratio < 0.1 and isBull:   extra_bull += 4

    if week5 > week20: extra_bull += 6
    else:               extra_bear += 6
    if n >= 60:
        month60 = sum(d["close"] for d in data[-60:]) / 60
        if last["close"] > month60: extra_bull += 5
        else:                        extra_bear += 5

    if n >= 6:
        pv = [d["volume"] for d in data[-6:-1]]
        p5a = sum(pv)/5; p3a = sum(pv[-3:])/3
        if p3a < p5a * 0.85 and volRatio > 1.4:
            if isBull: extra_bull += 12
            else:       extra_bear += 8

    bull_pts = (ma_bull * w["ma"] + rsi_bull * w["rsi"] + kd_bull * w["kd"]
                + macd_bull * w["macd"] + vol_bull * w["vol"] + pat_bull * w["pattern"]
                + extra_bull)
    bear_pts = (ma_bear * w["ma"] + rsi_bear * w["rsi"] + kd_bear * w["kd"]
                + macd_bear * w["macd"] + vol_bear * w["vol"] + pat_bear * w["pattern"]
                + extra_bear)

    bonus = 0
    if auto_strat == "breakout":
        bonus = 8 if isBull else 0

    elif auto_strat == "pullback" and n >= 25:
        ma20_v = lma20 or 0
        ma60_v = lma60_as
        biasMA20 = (last["close"] - ma20_v) / ma20_v * 100 if ma20_v else 0
        biasMA60 = (last["close"] - ma60_v) / ma60_v * 100 if ma60_v else None
        recent_lows = [d["low"] for d in data[-8:]]
        touchedMA20 = any(abs(lo - ma20_v)/ma20_v < 0.03 for lo in recent_lows) if ma20_v else False
        touchedMA60 = any(abs(lo - ma60_v)/ma60_v < 0.04 for lo in recent_lows) if ma60_v else False
        if biasMA20 >= 0 and biasMA20 < 8 and touchedMA20:
            bonus += 18 if biasMA20 < 3 else 10
        if ma60_v and biasMA60 is not None and 0 <= biasMA60 < 10 and touchedMA60:
            bonus += 20 if biasMA60 < 4 else 12
        vol20a = sum(d["volume"] for d in data[-20:]) / 20
        vol5a  = sum(d["volume"] for d in data[-5:])  / 5
        if vol5a < vol20a * 0.85: bonus += 10
        today_ratio = last["volume"] / vol20a if vol20a > 0 else 1
        if isBull and 0.7 <= today_ratio < 1.8: bonus += 12
        elif isBull and 1.8 <= today_ratio < 2.5: bonus += 6
        if n >= 25:
            ma20_5ago = sum(d["close"] for d in data[-25:-5]) / 20
            if ma20_v > ma20_5ago: bonus += 6
        bonus = min(bonus, 40)

    elif auto_strat == "reversal" and n >= 15:
        rsi_window = rsiA[-45:]
        rsi_vals = [v for v in rsi_window if v is not None]
        if len(rsi_vals) >= 3:
            lrsi_b, prsi_b, p2rsi_b = rsi_vals[-1], rsi_vals[-2], rsi_vals[-3]
            if lrsi_b < 25:      bonus += 15
            elif lrsi_b < 35:    bonus += 8
            was_low = min(prsi_b, p2rsi_b) < 38
            if was_low and lrsi_b > prsi_b:            bonus += 18
            elif was_low and lrsi_b > p2rsi_b:         bonus += 8
        vol20a = sum(d["volume"] for d in data[-20:]) / 20
        vol3a  = sum(d["volume"] for d in data[-4:-1]) / 3
        end_shrink = vol3a < vol20a * 0.75
        today_ratio = last["volume"] / vol20a if vol20a > 0 else 1
        if end_shrink and isBull and today_ratio > 1.2:  bonus += 15
        elif isBull and today_ratio > 1.0:                bonus += 6
        if day_range > 0 and downShadow / day_range > 0.35: bonus += 8
        bonus = min(bonus, 45)

    net = bull_pts - bear_pts + bonus
    score = round(50 + (net / 200) * 50)
    return max(0, min(100, score)), auto_strat


# ──────────────────────────────────────────────────────────────
# 進場訊號
# ──────────────────────────────────────────────────────────────
def calc_entry_signal(ohlcv):
    n = len(ohlcv)
    if n < 10:
        return ""
    last   = ohlcv[-1]
    avg20  = sum(d["volume"] for d in ohlcv[-20:]) / min(20, n)
    if avg20 == 0:
        return ""
    vol_ratio  = last["volume"] / avg20
    is_bull    = last["close"] > last["open"]
    day_range  = (last["high"] - last["low"]) or 0.01
    close_pos  = (last["close"] - last["low"]) / day_range

    if is_bull and vol_ratio >= 2.0 and n >= 6:
        prev5_high = max(d["high"] for d in ohlcv[-6:-1])
        if last["close"] > prev5_high:
            return "💥突破放量"
    if is_bull and vol_ratio >= 1.5 and close_pos >= 0.7:
        return "🚀主力進場"
    if is_bull and vol_ratio >= 1.3 and n >= 6:
        prev3_shrink = sum(1 for d in ohlcv[-4:-1] if d["volume"] < avg20 * 0.85)
        if prev3_shrink >= 2:
            return "✅洗盤結束"
    if vol_ratio < 0.6:
        return "📉量縮整理"
    return ""


# ──────────────────────────────────────────────────────────────
# 綜合評分（台股版）
# ──────────────────────────────────────────────────────────────
def calc_composite_tw(r):
    score = 0.0

    # K線分 (40分)
    ks_raw = r.get("kline_score")
    if ks_raw is not None:
        score += min(ks_raw / 100 * 40, 40)

    # RSI (20分) — 55~65 蓄力偏強為最佳，極端值遞減
    rsi = r.get("rsi14")
    if rsi is not None:
        if   55 <= rsi <= 65: score += 20
        elif 66 <= rsi <= 75: score += 13
        elif 45 <= rsi <= 54: score += 13
        elif 36 <= rsi <= 44: score += 8
        elif 76 <= rsi <= 85: score += 8
        elif rsi >= 86:       score += 4
        elif rsi < 35:        score += 4

    # 爆量 (15分) — 1.0~2.0x 含爆量突破都給滿分，與K線分爆量加分區間對齊
    vr = r.get("volume_ratio")
    if vr is not None:
        if   1.0 <= vr <= 2.0: score += 15
        elif 0.6 <= vr <  1.0: score += 11
        elif 2.1 <= vr <= 2.5: score += 11
        elif 0.3 <= vr <  0.6: score += 6
        elif vr > 2.5:         score += 6
        elif vr < 0.3:         score += 3

    # 法人連買 (15分) — 剛啟動 2~3 天為黃金期，過長遞減
    inst_days = r.get("inst_buy_days", 0) or 0
    if   inst_days in (2, 3):  score += 15
    elif inst_days == 1:        score += 8
    elif 4 <= inst_days <= 6:   score += 5
    elif inst_days >= 7:        score += 2
    elif inst_days <= -3:       score -= 8

    # MA60 / MA20 各自算，取高分，避免錯殺 (15分)
    def _ma_score(pct):
        if pct is None:       return 0
        if 0 <= pct <= 8:     return 15
        if 8 < pct <= 15:     return 8
        if 15 < pct <= 20:    return 4
        return 0

    ma60_pct = r.get("price_vs_ma60_pct")
    ma20_pct = r.get("price_vs_ma20_pct")
    score += max(_ma_score(ma60_pct), _ma_score(ma20_pct))

    return round(min(score, 100), 1)


# ──────────────────────────────────────────────────────────────
# 型態偵測（台股版）
# ──────────────────────────────────────────────────────────────
def detect_patterns_tw(r):
    out = []

    # A 法人爆量：法人剛啟動（2~3天）+ 量比正常以上
    inst = (r.get("inst_buy_days", 0) or 0)
    if inst in (2, 3) and (r.get("volume_ratio") or 0) >= 1.0:
        out.append(("A法人啟動", "pat-a"))

    # B 回踩MA60：站上MA60 0~8%，RSI健康，MA20向上
    ma60_pct = r.get("price_vs_ma60_pct")
    rsi = r.get("rsi14")
    if (ma60_pct is not None and 0 <= ma60_pct <= 8
            and rsi is not None and 40 <= rsi <= 65
            and r.get("ma20_rising") is True):
        out.append(("B回踩MA60", "pat-b"))

    # B2 回踩MA20：站上MA20 0~8%，RSI健康，MA20向上（MA60資料不足時備用）
    ma20_pct = r.get("price_vs_ma20_pct")
    if (ma60_pct is None or ma60_pct < 0) and (
            ma20_pct is not None and 0 <= ma20_pct <= 8
            and rsi is not None and 40 <= rsi <= 65
            and r.get("ma20_rising") is True):
        out.append(("B2回踩MA20", "pat-b2"))

    # C 底背離
    if r.get("rsi_div") == "bull":
        out.append(("C底背離", "pat-c"))

    return out
