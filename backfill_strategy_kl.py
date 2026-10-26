"""
backfill_strategy_kl.py  （檔名沿用，GitHub Actions 的 YML 不用改；內容已改為回補「策略O」）
=====================================================================================
一次性回補腳本：掃描 daily_stock_snapshot 裡指定日期範圍(預設2026-06-01 ~ 今天)的歷史快照，
用「現在版本」的 STRAT_O_BB_MA60 判斷條件重新跑一次(BB分<30 + RS70~84 + MA60上方0~5%)，
把符合條件、但當初尚未上線這個策略所以沒被寫進 signal_events 的（日期,股票）補寫進去。

不重抓 yfinance 的個股資料：daily_stock_snapshot 已經存了策略O需要的全部欄位
(bb_score / rs_score / price_vs_ma60_pct)，直接讀出來重新判斷即可。
補完事件後，T+1~T+10 報酬與超額報酬才會用 update_event_outcomes() 去 yfinance 補算（這一步需要網路）。

⚠ 樣本內 / 樣本外：
    STRAT_O_LIVE_DATE(見 stats_db.py，目前 2026-10-26) 之前的事件是用快照「回補」出來的，
    而O的門檻本身就是從這批歷史資料挑出來的 → 這段是樣本內，績效天生偏樂觀。
    STRAT_O_LIVE_DATE(含)之後由每日 save_daily_run 即時產生的才是樣本外。
    看統計時請把兩段分開判讀，不要混成一個勝率。

用法：
    python backfill_strategy_kl.py                     # 預設 2026-06-01 ~ 今天
    python backfill_strategy_kl.py 2026-06-01 2026-09-30
    python backfill_strategy_kl.py --dry-run            # 只印出會新增幾筆，不寫入

可重複執行：已存在的 event_id 會略過(INSERT OR IGNORE)。

寫完新的 signal_events 之後，會自動：
    1. update_event_outcomes()      → 幫新事件補上 T+1~T+10 報酬（含超額報酬）
    2. backfill_excess_return()     → 補齊超額報酬缺漏
    3. refresh_summary_stats()      → 重建 summary_stats（近期訊號/門檻分析/分數熱圖用）
    4. refresh_monthly_strategy_stats() / refresh_yearly_strategy_stats() → 月度/年度策略表
跑完後，網站的「策略組合回測」「月度分析」「近期訊號」「門檻分析」分頁就會看到策略O的完整歷史。

注意：快照只含「當時通過 fetch_data.py 入庫門檻」的股票，門檻外的樣本無法回補。
"""

import sys
from collections import Counter
from datetime import datetime

from stats_db import (
    connect, init_db, DB_PATH, SCORE_VERSION, STRAT_O_LIVE_DATE,
    bucket_kline, bucket_composite, bucket_breakout, bucket_swing, bucket_bb,
    bucket_rs, bucket_rs5d, bucket_volume_ratio, bucket_ma60,
    classify_strategy_events,
    update_event_outcomes, backfill_excess_return,
    refresh_summary_stats, refresh_monthly_strategy_stats, refresh_yearly_strategy_stats,
)

TARGET_STRATS = ("STRAT_O_BB_MA60",)


def _num(v):
    return None if v is None else float(v)


def backfill(start_date="2026-06-01", end_date=None, dry_run=False, db_path=DB_PATH):
    end_date = end_date or datetime.now().strftime("%Y-%m-%d")
    conn = connect(db_path)
    init_db(conn)   # 會先補欄位(price_vs_ma60_pct/ma60_bucket)並回填既有事件的MA60

    rows = conn.execute(
        """
        SELECT trade_date, ticker, name, close_price, kline_score, composite_score, breakout_score,
               swing_score, bb_score, bb_setup, rs_score, vcp_status, entry_signal,
               rsi14, volume_ratio, rs5d, inst_buy_days, price_vs_ma60_pct
        FROM daily_stock_snapshot
        WHERE trade_date >= ? AND trade_date <= ?
        ORDER BY trade_date, ticker
        """,
        (start_date, end_date),
    ).fetchall()

    print(f"[INFO] 讀取 daily_stock_snapshot 區間 {start_date} ~ {end_date}，共 {len(rows)} 筆快照")
    no_ma60 = sum(1 for r in rows if r["price_vs_ma60_pct"] is None)
    if no_ma60:
        print(f"[INFO] 其中 {no_ma60} 筆沒有 MA60 乖離資料（資料不足），無法判斷策略O，已略過")

    existing = {
        r[0] for r in conn.execute(
            "SELECT event_id FROM signal_events WHERE event_type IN (%s)"
            % ",".join("?" * len(TARGET_STRATS)),
            TARGET_STRATS,
        )
    }

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    to_insert = []
    matched_count = {s: 0 for s in TARGET_STRATS}
    by_month = Counter()
    already_exists = 0

    for r in rows:
        strat_events = classify_strategy_events(
            r["kline_score"], r["composite_score"], r["breakout_score"], r["swing_score"],
            r["rs_score"], r["vcp_status"], r["entry_signal"] or "",
            bb_score=r["bb_score"], bb_setup=r["bb_setup"], bb_consec_down_days=None,
            rsi14=r["rsi14"], volume_ratio=r["volume_ratio"],
            rs5d=r["rs5d"], inst_buy_days=r["inst_buy_days"],
            price_vs_ma60_pct=r["price_vs_ma60_pct"],
        )
        strat_events = [s for s in strat_events if s in TARGET_STRATS]
        if not strat_events:
            continue

        ticker = r["ticker"]
        trade_date = r["trade_date"]
        k_bucket = bucket_kline(r["kline_score"])
        c_bucket = bucket_composite(r["composite_score"])
        b_bucket = bucket_breakout(r["breakout_score"])
        sw_bucket = bucket_swing(r["swing_score"])
        bb_bucket = bucket_bb(r["bb_score"])
        rs_bucket = bucket_rs(r["rs_score"])
        rs5d_bucket = bucket_rs5d(r["rs5d"])
        vol_ratio_bucket = bucket_volume_ratio(r["volume_ratio"])
        ma60_bucket = bucket_ma60(r["price_vs_ma60_pct"])

        for strat_event_type in strat_events:
            event_id = f"{trade_date}:{ticker}:{strat_event_type}"
            if event_id in existing:
                already_exists += 1
                continue
            existing.add(event_id)
            matched_count[strat_event_type] += 1
            by_month[trade_date[:7]] += 1
            to_insert.append((
                event_id, trade_date, ticker, r["name"], strat_event_type, "strategy_combo",
                _num(r["kline_score"]), _num(r["composite_score"]), k_bucket, c_bucket,
                _num(r["breakout_score"]), b_bucket, _num(r["swing_score"]), sw_bucket,
                _num(r["close_price"]), "next_open", "open", SCORE_VERSION, now,
                _num(r["bb_score"]), bb_bucket, r["bb_setup"],
                _num(r["rs_score"]), rs_bucket, _num(r["rs5d"]), rs5d_bucket,
                _num(r["volume_ratio"]), vol_ratio_bucket,
                _num(r["rsi14"]), r["vcp_status"], r["entry_signal"] or "",
                int(r["inst_buy_days"] or 0),
                _num(r["price_vs_ma60_pct"]), ma60_bucket,
            ))

    print(f"[INFO] 已存在（略過）：{already_exists} 筆")
    for s in TARGET_STRATS:
        print(f"[INFO] 新符合條件 {s}：{matched_count[s]} 筆")
    for ym in sorted(by_month):
        print(f"       {ym}：{by_month[ym]} 筆")
    n_live = sum(1 for t in to_insert if t[1] >= STRAT_O_LIVE_DATE)
    print(f"[INFO] 其中 {STRAT_O_LIVE_DATE} 以後(樣本外) {n_live} 筆，之前(回補/樣本內) {len(to_insert) - n_live} 筆")
    print(f"[INFO] 總計待新增：{len(to_insert)} 筆")

    if dry_run:
        print("[DRY-RUN] 未寫入資料庫，結束。")
        conn.close()
        return

    if not to_insert:
        print("[INFO] 沒有新資料需要寫入。")
        conn.close()
        return

    conn.executemany(
        """
        INSERT OR IGNORE INTO signal_events (
            event_id, trade_date, ticker, name, event_type, trigger_source,
            kline_score, composite_score, kline_bucket, composite_bucket,
            breakout_score, breakout_bucket, swing_score, swing_bucket,
            entry_reference_close, entry_price_mode, status, score_version, created_at,
            bb_score, bb_bucket, bb_setup,
            rs_score, rs_bucket, rs5d, rs5d_bucket, volume_ratio, volume_ratio_bucket, rsi14,
            vcp_status, entry_signal, inst_buy_days,
            price_vs_ma60_pct, ma60_bucket
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        to_insert,
    )
    conn.commit()
    print(f"[INFO] 已寫入 {len(to_insert)} 筆新 signal_events")

    print("[INFO] 補算 T+1~T+10 報酬（含超額報酬）...")
    update_event_outcomes(conn)
    conn.commit()

    print("[INFO] 回補超額報酬缺漏...")
    backfill_excess_return(conn)
    conn.commit()

    print("[INFO] 重建 summary_stats / 月度 / 年度統計...")
    refresh_summary_stats(conn)
    refresh_monthly_strategy_stats(conn)
    refresh_yearly_strategy_stats(conn)
    conn.commit()
    conn.close()
    print("[完成] 策略O歷史回補完成，網站統計頁面重新整理後即可看到完整表現。")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry_run = "--dry-run" in sys.argv
    start = args[0] if len(args) > 0 else "2026-06-01"
    end = args[1] if len(args) > 1 else None
    backfill(start, end, dry_run=dry_run)
