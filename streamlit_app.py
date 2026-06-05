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
    wb = 5
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
            and rsi_highs[-1]["val"]    < rsi_highs[-2]["val"]):
        return "bear"
    if (len(price_lows) >= 2 and len(rsi_lows) >= 2
            and price_lows[-1]["price"] < price_lows[-2]["price"]
            and rsi_lows[-1]["val"]    > rsi_lows[-2]["val"]):
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

    # 爆量 (15分) — 1.0~1.5x 主力溫和推進為最佳買點
    vr = r.get("volume_ratio")
    if vr is not None:
        if   1.0 <= vr <= 1.5: score += 15
        elif 0.6 <= vr <  1.0: score += 11
        elif 1.6 <= vr <= 2.0: score += 11
        elif 0.3 <= vr <  0.6: score += 6
        elif 2.1 <= vr <= 2.5: score += 6
        elif vr > 2.5:         score += 3
        elif vr < 0.3:         score += 3

    # 法人連買 (15分) — 剛啟動 2~3 天為黃金期，過長遞減
    inst_days = r.get("inst_buy_days", 0) or 0
    if   inst_days in (2, 3):  score += 15
    elif inst_days == 1:        score += 8
    elif 4 <= inst_days <= 6:   score += 5
    elif inst_days >= 7:        score += 2
    elif inst_days <= -3:       score -= 8

    # MA60 / MA20 各自算，取高分，避免錯殺 (15分)
    # 站上均線 0~8% 為最佳買點，跌破或過熱均遞減
    def _ma_score(pct):
        if pct is None:       return 0
        if 0 <= pct <= 8:     return 15
        if 8 < pct <= 15:     return 8
        if 15 < pct <= 20:    return 4
        return 0  # 跌破(<0) 或 超過 20% 皆給 0

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
