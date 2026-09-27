"""
backfill_strategy_kl.py
========================
一次性回補腳本：掃描 daily_stock_snapshot 裡指定日期範圍(預設6/1~9/30)的歷史快照，
用「現在版本」的 STRAT_K_LOWSWING_SETUP / STRAT_L_GENTLE_TURN 判斷條件重新跑一次，
把符合條件但當初尚未上線這兩個策略、所以沒被寫進 signal_events 的（日期,股票）補寫進去。

不重抓 yfinance：daily_stock_snapshot 已經存了 kline_score/composite/swing_score/bb_score/
rs_score/rs5d/volume_ratio/inst_buy_days 等策略K、L需要的全部欄位，直接讀出來重新判斷即可。
唯一例外是 bb_consec_down_days 沒有獨立欄位，存在 raw_json 裡，用 json.loads 撈出來。

用法：
    python backfill_strategy_kl.py                     # 預設 2026-06-01 ~ 2026-09-30
    python backfill_strategy_kl.py 2026-06-01 2026-09-30
    python backfill_strategy_kl.py --dry-run            # 只印出會新增幾筆，不寫入

寫完新的 signal_events 之後，會自動：
    1. update_event_outcomes()      → 幫新事件補上 T+1~T+10 報酬（含超額報酬）
    2. backfill_excess_return()     → 補齊超額報酬缺漏
    3. refresh_summary_stats()      → 重建 summary_stats（近期訊號/門檻分析/分數熱圖用）
    4. refresh_monthly_strategy_stats() / refresh_yearly_strategy_stats() → 月度/年度策略表
跑完後，網站的「策略組合回測」「月度分析」「近期訊號」分頁就會立刻看到 K、L 的完整歷史表現，
不需要再手動點什麼。
"""

import json
import sys
from datetime import datetime

from stats_db import (
    connect, init_db, DB_PATH, SCORE_VERSION,
    bucket_kline, bucket_composite, bucket_breakout, bucket_swing, bucket_bb,
    bucket_rs, bucket_rs5d, bucket_volume_ratio,
    classify_strategy_events,
    update_event_outcomes, backfill_excess_return,
    refresh_summary_stats, refresh_monthly_strategy_stats, refresh_yearly_strategy_stats,
)

TARGET_STRATS = ("STRAT_K_LOWSWING_SETUP", "STRAT_L_GENTLE_TURN")


def _num(v):
    return None if v is None else float(v)


def backfill(start_date="2026-06-01", end_date="2026-09-30", dry_run=False, db_path=DB_PATH):
    conn = connect(db_path)
    init_db(conn)

    rows = conn.execute(
        """
        SELECT trade_date, ticker, name, kline_score, composite_score, breakout_score,
               swing_score, bb_score, bb_setup, rs_score, vcp_status, entry_signal,
               rsi14, volume_ratio, rs5d, inst_buy_days, raw_json
        FROM daily_stock_snapshot
        WHERE trade_date >= ? AND trade_date <= ?
        ORDER BY trade_date, ticker
        """,
        (start_date, end_date),
    ).fetchall()

    print(f"[INFO] 讀取 daily_stock_snapshot 區間 {start_date} ~ {end_date}，共 {len(rows)} 筆快照")

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    to_insert = []
    matched_count = {s: 0 for s in TARGET_STRATS}
    already_exists = 0

    for r in rows:
        # bb_consec_down_days 沒有獨立欄位，從 raw_json 撈
        bb_consec_down_days = None
        if r["raw_json"]:
            try:
                raw = json.loads(r["raw_json"])
                bb_consec_down_days = raw.get("bb_consec_down_days")
            except Exception:
                pass

        strat_events = classify_strategy_events(
            r["kline_score"], r["composite_score"], r["breakout_score"], r["swing_score"],
            r["rs_score"], r["vcp_status"], r["entry_signal"] or "",
            bb_score=r["bb_score"], bb_setup=r["bb_setup"], bb_consec_down_days=bb_consec_down_days,
            rsi14=r["rsi14"], volume_ratio=r["volume_ratio"],
            rs5d=r["rs5d"], inst_buy_days=r["inst_buy_days"],
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

        for strat_event_type in strat_events:
            event_id = f"{trade_date}:{ticker}:{strat_event_type}"
            exists = conn.execute(
                "SELECT 1 FROM signal_events WHERE event_id = ?", (event_id,)
            ).fetchone()
            if exists:
                already_exists += 1
                continue
            matched_count[strat_event_type] += 1
            to_insert.append((
                event_id, trade_date, ticker, r["name"], strat_event_type, "strategy_combo",
                _num(r["kline_score"]), _num(r["composite_score"]), k_bucket, c_bucket,
                _num(r["breakout_score"]), b_bucket, _num(r["swing_score"]), sw_bucket,
                None, "next_open", "open", SCORE_VERSION, now,
                _num(r["bb_score"]), bb_bucket, r["bb_setup"],
                _num(r["rs_score"]), rs_bucket, _num(r["rs5d"]), rs5d_bucket,
                _num(r["volume_ratio"]), vol_ratio_bucket,
                _num(r["rsi14"]), r["vcp_status"], r["entry_signal"] or "",
                int(r["inst_buy_days"] or 0),
            ))

    print(f"[INFO] 已存在（略過）：{already_exists} 筆")
    for s in TARGET_STRATS:
        print(f"[INFO] 新符合條件 {s}：{matched_count[s]} 筆")
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
            vcp_status, entry_signal, inst_buy_days
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
    print("[完成] 策略K、L歷史回補完成，網站統計頁面重新整理後即可看到完整表現。")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry_run = "--dry-run" in sys.argv
    start = args[0] if len(args) > 0 else "2026-06-01"
    end = args[1] if len(args) > 1 else "2026-09-30"
    backfill(start, end, dry_run=dry_run)
