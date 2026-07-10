"""
fetch_data.py
=============
由 GitHub Actions 於每日下午 5 點（台灣時間）自動執行
只使用 yfinance（免費，無需 Token）
結果存到 data/screener_data.json 與 data/market_data.json
FinMind 三大法人資料由 Streamlit app 自行取得（Token 存於 Streamlit Secrets）
"""

import yfinance as yf
import pandas as pd
import numpy as np
import json
import time
import os
import io
import contextlib
import logging
from datetime import datetime
import requests

from tw_screener_core import (
    calc_kline_score, calc_entry_signal, calc_rsi,
    calc_composite_tw, detect_patterns_tw,
    is_otc, SIGNAL_RANK, _detect_rsi_divergence,
    calc_ret_n, calc_rs_score, RS_PERIODS,
    calc_ma120, calc_max_drawdown_pct, calc_vcp_contraction_strict,
    calc_consolidation_vol_ratio, calc_breakout_60d, calc_vcp_score_strict,
    calc_vcp_status, calc_swing_score,
    calc_bb_data, calc_bb_score,
)
from stats_db import save_daily_run

logging.getLogger("yfinance").setLevel(logging.CRITICAL)
logging.getLogger("peewee").setLevel(logging.CRITICAL)

SLEEP_BETWEEN = 0.2
STOCK_FILE    = "371檔股票.XLSX"
DATA_DIR      = "data"


def fetch_finmind_inst_days(stock_id: str, token: str, days: int = 10) -> int:
    if not token:
        return 0
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - pd.Timedelta(days=days + 10)).strftime("%Y-%m-%d")
    try:
        r = requests.get(
            "https://api.finmindtrade.com/api/v4/data",
            params=dict(
                dataset="TaiwanStockInstitutionalInvestorsBuySell",
                data_id=stock_id,
                start_date=start,
                end_date=end,
                token=token,
            ),
            timeout=12,
        )
        d = r.json()
        if d.get("status") != 200:
            return 0
        daily = {}
        for rec in d.get("data", []):
            name = rec.get("name", "")
            if name in ("外資", "外資自營商", "Foreign_Investor", "投信", "Investment_Trust"):
                daily.setdefault(rec["date"], 0)
                daily[rec["date"]] += rec.get("buy", 0) - rec.get("sell", 0)
        dates = sorted(daily, reverse=True)[:days]
        if not dates:
            return 0
        sign = 1 if daily[dates[0]] >= 0 else -1
        count = 0
        for dt in dates:
            if (1 if daily[dt] >= 0 else -1) == sign:
                count += 1
            else:
                break
        return sign * count
    except Exception:
        return 0


# ──────────────────────────────────────────────────────────────
# 讀取股票清單
# ──────────────────────────────────────────────────────────────
def load_stock_list(filepath: str):
    df = pd.read_excel(filepath)
    id_col   = next((c for c in df.columns if "id" in c.lower() or "代號" in c or "stock" in c.lower()), df.columns[0])
    name_col = next((c for c in df.columns if "name" in c.lower() or "名稱" in c or "名字" in c), None)
    stocks = []
    seen   = set()
    for _, row in df.iterrows():
        try:
            sid  = str(int(row[id_col]))
            if sid in seen:
                continue
            seen.add(sid)
            name = str(row[name_col]) if name_col else sid
            stocks.append((sid, name))
        except Exception:
            continue
    return stocks


# ──────────────────────────────────────────────────────────────
# 大盤資料
# ──────────────────────────────────────────────────────────────
def fetch_twii_data():
    try:
        t = yf.Ticker("^TWII")
        h = t.history(period="3mo")
        if h is None or len(h) < 22:
            return None
        closes = h["Close"]
        ret5d  = round((float(closes.iloc[-1]) / float(closes.iloc[-6]) - 1) * 100, 2)
        rsi    = calc_rsi(closes)
        ma20   = float(closes.iloc[-20:].mean())
        price  = float(closes.iloc[-1])
        return {
            "ret5d":    ret5d,
            "rsi":      rsi,
            "price":    price,
            "below_ma20": price < ma20,
            "ma20":     round(ma20, 2),
        }
    except Exception:
        return None


# ──────────────────────────────────────────────────────────────
# 單檔資料抓取（yfinance only）
# ──────────────────────────────────────────────────────────────
def fetch_tw_ticker(stock_id: str, name: str = ""):
    sid = str(stock_id)
    base = dict(
        ticker=sid, name=name or sid,
        market="TW",
        price=None, targetMean=None, upside_pct=None,
        recMean=None, recKey="N/A", analysts=None,
        volume_today=None, volume_avg20=None, volume_ratio=None,
        ma5=None, ma20=None, ma60=None,
        price_vs_ma20_pct=None,
        price_vs_ma60_pct=None,
        week52_high=None, week52_pct=None, rsi14=None,
        inst_buy_days=0,   # GitHub Actions with FINMIND_TOKEN fills this before saving.
        kline_score=None, kline_strat=None,
        ret5d=None, ma20_rising=None,
        entry_signal="", signal_rank=0,
        rsi_div=None,
        rs5d=None,
        ret21d=None, ret63d=None, ret126d=None, ret252d=None,
        rs_score=None,
        ma120=None, dd60=None, vcp_contracting=None, vcp_higher_high=None,
        vcp_vol_shrink_quality=None, vcp_detail=None, leg1_pct=None, leg2_pct=None,
        vcp_score_breakdown=None, cons_vol_ratio=None,
        vcp_breakout=False, vcp_score=None, vcp_status=None,
        swing_score=None, swing_score_breakdown=None,  # 波段分：目前尚無計分函式，預留欄位供未來擴充
        bb_score=None, bb_setup=None, bb_score_breakdown=None,
        bb_upper=None, bb_lower=None, bb_mid=None, bb_width_pct=None,
        bb_percent_b=None, bb_mid_rising=None, bb_lower_touch_days_10=None,
        composite=0,
        patterns=[],
    )

    hist = None
    info = {}
    detected_market = "TW"
    suffixes = [".TWO", ".TW"] if is_otc(sid) else [".TW", ".TWO"]
    for suffix in suffixes:
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                t    = yf.Ticker(f"{sid}{suffix}")
                info = t.info or {}
                h = None
                for _ in range(3):
                    h = t.history(period="1y")
                    if h is not None and len(h) >= 20:
                        break
                    time.sleep(0.8)
            if h is not None and len(h) >= 20:
                hist = h
                detected_market    = suffix[1:]
                base["market"]     = detected_market
                base["name"]       = name or info.get("shortName") or info.get("longName") or sid
                break
        except Exception:
            continue

    if hist is None:
        return base

    # 基本資訊
    base["price"]      = info.get("currentPrice") or info.get("regularMarketPrice")
    base["targetMean"] = info.get("targetMeanPrice")
    base["recMean"]    = info.get("recommendationMean")
    base["recKey"]     = info.get("recommendationKey", "N/A")
    base["analysts"]   = info.get("numberOfAnalystOpinions")

    if base["price"] and base["targetMean"] and base["price"] > 0:
        base["upside_pct"] = round((base["targetMean"] - base["price"]) / base["price"] * 100, 1)

    # OHLCV
    closes  = hist["Close"]
    volumes = hist["Volume"]
    base["volume_today"] = int(volumes.iloc[-1])
    base["volume_avg20"] = int(volumes.iloc[-20:].mean())
    if base["volume_avg20"] > 0:
        base["volume_ratio"] = round(base["volume_today"] / base["volume_avg20"], 2)
    base["ma5"]  = round(float(closes.iloc[-5:].mean()), 2)
    base["ma20"] = round(float(closes.iloc[-20:].mean()), 2)
    if len(closes) >= 60:
        base["ma60"] = round(float(closes.iloc[-60:].mean()), 2)
    elif len(closes) >= 20:
        base["ma60"] = round(float(closes.mean()), 2)
    base["ma120"] = calc_ma120(closes)

    if base["ma20"] and base["price"]:
        base["price_vs_ma20_pct"] = round((base["price"] - base["ma20"]) / base["ma20"] * 100, 1)
    if base["ma60"] and base["price"]:
        base["price_vs_ma60_pct"] = round((base["price"] - base["ma60"]) / base["ma60"] * 100, 1)

    wh = float(closes.iloc[-252:].max()) if len(closes) >= 252 else float(closes.max())
    base["week52_high"] = round(wh, 2)
    if base["price"]:
        base["week52_pct"] = round((base["price"] - wh) / wh * 100, 1)
    base["rsi14"] = calc_rsi(closes)
    if len(closes) >= 2:
        base["prev_close"] = round(float(closes.iloc[-2]), 2)
    if len(closes) >= 6:
        base["ret5d"] = round((float(closes.iloc[-1]) / float(closes.iloc[-6]) - 1) * 100, 2)
    for key, n, _ in RS_PERIODS:
        base[key] = calc_ret_n(closes, n)

    base["dd60"] = calc_max_drawdown_pct(closes, n=60)
    vcp = calc_vcp_contraction_strict(
        closes=closes,
        highs=hist["High"],
        lows=hist["Low"],
        volumes=volumes,
        lookback=90,
    )
    base["vcp_contracting"] = vcp.get("contracting")
    base["vcp_higher_high"] = vcp.get("higher_high")
    base["vcp_vol_shrink_quality"] = vcp.get("vol_shrink_quality")
    base["vcp_detail"] = vcp.get("detail")
    base["leg1_pct"] = vcp.get("leg1_pct")
    base["leg2_pct"] = vcp.get("leg2_pct")
    base["cons_vol_ratio"]  = calc_consolidation_vol_ratio(volumes, recent_n=10, base_n=60)
    base["vcp_breakout"]    = calc_breakout_60d(closes, volumes, n=60, vol_mult=1.5)
    if len(closes) >= 25:
        ma20_now  = float(closes.iloc[-20:].mean())
        ma20_5ago = float(closes.iloc[-25:-5].mean())
        base["ma20_rising"] = bool(ma20_now > ma20_5ago)

    # OHLCV list → K線計分
    ohlcv = [
        {"open": float(row.Open), "high": float(row.High),
         "low":  float(row.Low),  "close": float(row.Close), "volume": float(row.Volume)}
        for _, row in hist.iterrows()
    ]

    if len(ohlcv) >= 20:
        result = calc_kline_score(ohlcv)
        if result:
            base["kline_score"], base["kline_strat"] = result
        base["entry_signal"] = calc_entry_signal(ohlcv)
        base["signal_rank"]  = SIGNAL_RANK.get(base["entry_signal"], 0)

        # RSI 背離
        rsi_vals = [None]
        for j in range(1, len(ohlcv)):
            g_sum = sum(max(ohlcv[k]["close"] - ohlcv[k-1]["close"], 0) for k in range(max(1, j-13), j+1))
            l_sum = sum(max(ohlcv[k-1]["close"] - ohlcv[k]["close"], 0) for k in range(max(1, j-13), j+1))
            cnt = min(j, 14)
            if cnt < 14:
                rsi_vals.append(None)
            elif l_sum == 0:
                rsi_vals.append(100.0)
            else:
                rsi_vals.append(100 - 100 / (1 + (g_sum/cnt) / (l_sum/cnt)))
        base["rsi_div"] = _detect_rsi_divergence(ohlcv, rsi_vals)

    # 布林通道分（BB分）：需在 rsi_div / volume_ratio / ma120 / price 都備妥後才計算
    bb_data = calc_bb_data(closes)
    if bb_data:
        base.update(bb_data)
        base["bb_score"], base["bb_setup"] = calc_bb_score(base)

    # Initial score before FinMind. GitHub Actions recalculates after inst_buy_days is filled.
    base["composite"] = calc_composite_tw(base)
    base["patterns"]  = detect_patterns_tw(base)

    return base


# ──────────────────────────────────────────────────────────────
# 主程式
# ──────────────────────────────────────────────────────────────
def is_trading_day() -> bool:
    """
    用大盤今日是否有成交來判定是否為交易日。
    yfinance period='1d' 在非交易日（國定假日、週末）會回傳空 DataFrame。
    """
    try:
        h = yf.Ticker("^TWII").history(period="1d")
        return h is not None and len(h) > 0
    except Exception:
        return True   # 抓取失敗時保守地繼續執行，避免漏抓


def main():
    os.makedirs(DATA_DIR, exist_ok=True)

    # ── 交易日檢查 ──────────────────────────────────────────────
    print("[INFO] 確認今日是否為交易日 ...")
    if not is_trading_day():
        print("[INFO] 今日大盤無成交資料，判定為非交易日，跳過執行。")
        return
    print("[INFO] 交易日確認，開始執行。")
    # ────────────────────────────────────────────────────────────

    if not os.path.exists(STOCK_FILE):
        print(f"[ERROR] 找不到股票清單：{STOCK_FILE}")
        raise SystemExit(1)

    stocks = load_stock_list(STOCK_FILE)
    total  = len(stocks)
    print(f"[INFO] 共 {total} 檔，開始抓取（每檔間隔 {SLEEP_BETWEEN}s）")

    # 大盤
    print("[INFO] 抓取大盤 ^TWII ...")
    market_info = fetch_twii_data()
    if market_info:
        print(f"[INFO] 加權 {market_info['price']:,.0f}  近5日 {market_info['ret5d']:+.1f}%")
    else:
        print("[WARN] 大盤資料無法取得")

    twii_ret5d = market_info["ret5d"] if market_info else None
    finmind_token = os.environ.get("FINMIND_TOKEN", "").strip()
    if finmind_token:
        print("[INFO] FINMIND_TOKEN detected; institutional days will be stored in DB snapshots.")
    else:
        print("[WARN] FINMIND_TOKEN not set; inst_buy_days will remain 0 in DB snapshots.")

    results = []
    for i, (sid, name) in enumerate(stocks, 1):
        print(f"\r[{i:03d}/{total}] {sid} {name[:8]:<8}...", end="", flush=True)
        data = fetch_tw_ticker(sid, name)

        if twii_ret5d is not None and data.get("ret5d") is not None:
            data["rs5d"] = round(data["ret5d"] - twii_ret5d, 1)

        if finmind_token and data.get("price") is not None:
            data["inst_buy_days"] = fetch_finmind_inst_days(sid, finmind_token)
            data["composite"] = calc_composite_tw(data)
            data["patterns"] = detect_patterns_tw(data)

        results.append(data)
        time.sleep(SLEEP_BETWEEN)

    print("\n[INFO] 抓取完成！")

    print("[INFO] 計算橫向排名 RS 分數 ...")
    calc_rs_score(results)

    print("[INFO] 計算 SEPA+VCP 回檔波段分 ...")
    for r in results:
        r["vcp_score"] = calc_vcp_score_strict(r)
        r["vcp_status"] = calc_vcp_status(r)
        r["swing_score"] = calc_swing_score(r)

    # 只保留最終分數，中間用的各期漲幅/排名/VCP原始輸入不需要存進 JSON / 顯示
    _drop_keys = (
        [k for k, _, _ in RS_PERIODS]
        + [f"{k}_rank" for k, _, _ in RS_PERIODS]
        + ["ma120", "dd60", "cons_vol_ratio"]
    )
    for r in results:
        for k in _drop_keys:
            r.pop(k, None)

    # 儲存
    from datetime import date, timedelta
    def last_trading_day():
        d = date.today()
        while d.weekday() >= 5:
          d -= timedelta(days=1)
        return d.strftime("%Y-%m-%d")

    ts = f"{last_trading_day()} {datetime.now().strftime('%H:%M')}"
    output = {"generated_at": ts, "stocks": results}

    data_path = os.path.join(DATA_DIR, "screener_data.json")
    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, default=str)
    print(f"[INFO] 已儲存 → {data_path}")

    market_path = os.path.join(DATA_DIR, "market_data.json")
    with open(market_path, "w", encoding="utf-8") as f:
        json.dump(market_info or {}, f, ensure_ascii=False)
    print(f"[INFO] 已儲存 → {market_path}")

    # 只將 K線≥75、綜合分≥75、突破分≥60、波段分≥60、BB分≥60 的股票存入統計資料庫
    # 低分股票不具備進場條件，不應計入績效統計，也節省資料庫空間
    # ── 門檻對齊 stats_db.py 的策略組合回測 ──
    #   策略A 要抓 突破分≥60，策略B 要抓 波段分≥60，策略D 要抓 綜合分≥75，
    #   策略E 要抓 BB分≥60，如果這裡的門檻比策略門檻還嚴格，會讓對應分數區間的
    #   股票根本進不了資料庫，統計出來的樣本永遠是空的或被悄悄墊高標準。
    DB_KLINE_MIN    = 75
    DB_COMP_MIN     = 75
    DB_BREAKOUT_MIN = 60
    DB_SWING_MIN    = 60
    DB_BB_MIN       = 60
    qualified = [
        s for s in results
        if (s.get("kline_score") or 0) >= DB_KLINE_MIN
        or (s.get("composite") or 0) >= DB_COMP_MIN
        or (s.get("vcp_score") or 0) >= DB_BREAKOUT_MIN
        or (s.get("swing_score") or 0) >= DB_SWING_MIN
        or (s.get("bb_score") or 0) >= DB_BB_MIN
    ]
    print(f"[INFO] 符合資料庫下線（K線≥{DB_KLINE_MIN} 或 綜合分≥{DB_COMP_MIN} "
          f"或 突破分≥{DB_BREAKOUT_MIN} 或 波段分≥{DB_SWING_MIN} 或 BB分≥{DB_BB_MIN}）：{len(qualified)} 檔")
    save_daily_run(qualified, generated_at=ts)
    print(f"[INFO] 已更新統計資料庫 → {os.path.join(DATA_DIR, 'stats.db')}")


if __name__ == "__main__":
    main()
