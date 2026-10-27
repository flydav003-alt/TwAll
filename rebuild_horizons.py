"""
rebuild_horizons.py — 一次性：把整個統計資料庫依新的 HORIZONS（T+1/5/10/20/40）重算。

安全設計
  1. 在「工作副本」上重算，原始 stats.db 全程不動。
  2. 驗證通過才用工作副本取代原始檔；沒通過就保留原檔、以非 0 狀態結束（可用 --force 強制）。
  3. 驗證項目：
       - 重算後只有新的 horizon，且 T+20 / T+40 確實有產出
       - 下載失敗的事件比例不超過上限
       - 新舊資料重疊的 T+1 / T+5 / T+10 報酬必須一致（容許 0.05 個百分點；
         不一致代表股價來源或回補邏輯有問題，這是最可靠的對帳）
       - 原本有 T+1/5/10 結果的事件，重算後不能大量消失
  4. 「只是還沒有下一個交易日」的事件會改回 open，之後每日更新會自動補上，
     不會像舊回補那樣被永久標成 missing_next_open。

用法（在專案根目錄，需要能連 Yahoo Finance）：
    python rebuild_horizons.py
    python rebuild_horizons.py --db data/stats.db --max-fail-rate 0.03 --max-mismatch-rate 0.02
    python rebuild_horizons.py --force      # 驗證未通過也強制覆蓋（一般不要用）
"""
import argparse
import glob
import os
import shutil
import sqlite3
import sys
from datetime import date, datetime, timedelta

import stats_db
from stats_db import HORIZONS, backfill_entry_price_to_next_open, connect, write_stats_payload

COMPARE_HORIZONS = (1, 5, 10)   # 新舊兩套 horizon 都有的天期，用來對帳


def _load_detail(path):
    """回傳 (outcomes, events)：outcomes[(event_id,h)] = (報酬, 結算日)；events[event_id] = (代號, 訊號日, 進場日)。"""
    conn = sqlite3.connect(path)
    try:
        outs = {
            (r[0], r[1]): (r[2], r[3])
            for r in conn.execute("SELECT event_id, horizon, return_close_pct, target_date FROM event_outcomes")
        }
        evs = {r[0]: (r[1], r[2], r[3]) for r in conn.execute("SELECT event_id, ticker, trade_date, entry_date FROM signal_events")}
        return outs, evs
    finally:
        conn.close()


def _diagnose(old_o, old_e, new_o, new_e, mism, csv_path):
    """把不一致的筆數依成因分類，並輸出 CSV，方便判斷是「進場日變了」「結算日位移」還是「收盤價被修正」。"""
    import collections
    import csv
    cats = collections.Counter()
    by_h = collections.Counter()
    by_date = collections.Counter()
    shifts = collections.Counter()
    diffs = []
    rows = []
    for k in mism:
        eid, h = k
        tk, sd, oe = old_e.get(eid, (None, None, None))
        ne = new_e.get(eid, (None, None, None))[2]
        o_ret, o_td = old_o[k]
        n_ret, n_td = new_o.get(k, (None, None))
        if oe != ne:
            cat = "進場日改變"
        elif o_td != n_td:
            cat = "結算日位移"
            shifts[(o_td, n_td)] += 1
        else:
            cat = "同日收盤價不同"
            if o_ret is not None and n_ret is not None:
                diffs.append(abs(float(o_ret) - float(n_ret)))
        cats[cat] += 1
        by_h[h] += 1
        by_date[sd] += 1
        rows.append([eid, tk, sd, h, o_ret, n_ret, o_td, n_td, oe, ne, cat])
    try:
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["event_id", "ticker", "signal_date", "horizon", "old_ret", "new_ret",
                        "old_target", "new_target", "old_entry", "new_entry", "category"])
            w.writerows(rows)
        print(f"[INFO] 不一致明細已輸出：{csv_path}")
    except OSError as exc:
        print(f"[WARN] 無法輸出不一致明細：{exc}")
    print("\n---------- 不一致成因分析 ----------")
    print("成因分類：", dict(cats))
    print("依 horizon：", dict(sorted(by_h.items())))
    print("訊號日最多的前 8 天：", by_date.most_common(8))
    if shifts:
        print("結算日位移（舊→新）最多的前 8 組：", shifts.most_common(8))
    if diffs:
        diffs.sort()
        print(f"同日收盤價不同：中位數差 {diffs[len(diffs)//2]:.2f} 個百分點，最大 {diffs[-1]:.2f}")


def _load_outcomes(path):
    conn = sqlite3.connect(path)
    try:
        return {
            (r[0], r[1]): r[2]
            for r in conn.execute("SELECT event_id, horizon, return_close_pct FROM event_outcomes")
        }
    finally:
        conn.close()


def _cleanup(*paths):
    for p in paths:
        try:
            os.remove(p)
        except OSError:
            pass


def _find_closed_days(conn, max_days=8):
    """找出「訊號日當天大盤其實休市」的日期（例如颱風假、國定假日卻仍有排程跑出訊號）。
    判斷依據：該日期是平日、落在大盤歷史資料範圍內，但 ^TWII 沒有那天的 K 棒。
    回傳 (closed_days, 說明)；資料不足或疑似誤判（日數過多）時回傳 (None, 原因)，呼叫端就不會刪除任何東西。"""
    dates = [r[0] for r in conn.execute("SELECT DISTINCT trade_date FROM signal_events ORDER BY 1")]
    if not dates:
        return [], "無事件"
    start = (datetime.fromisoformat(dates[0]) - timedelta(days=5)).strftime("%Y-%m-%d")
    end = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    twii = stats_db._twii_history(start, end)
    if twii is None or len(twii) < 20:
        return None, "大盤歷史資料取得失敗或太短，無法判斷休市日"
    bars = set(twii["date_str"])
    lo, hi = min(bars), max(bars)
    closed = [d for d in dates if lo <= d <= hi and d not in bars and date.fromisoformat(d).weekday() < 5]
    if len(closed) > max_days:
        return None, f"偵測到 {len(closed)} 個休市日（超過上限 {max_days}），疑似大盤資料缺漏，為安全起見不刪除"
    return closed, "ok"


def _drop_closed_day_events(conn, closed):
    marks = ",".join("?" * len(closed))
    ids = [r[0] for r in conn.execute(f"SELECT event_id FROM signal_events WHERE trade_date IN ({marks})", closed)]
    n_snap = conn.execute(f"SELECT COUNT(*) FROM daily_stock_snapshot WHERE trade_date IN ({marks})", closed).fetchone()[0]
    conn.execute(f"DELETE FROM event_outcomes WHERE event_id IN (SELECT event_id FROM signal_events WHERE trade_date IN ({marks}))", closed)
    conn.execute(f"DELETE FROM signal_events WHERE trade_date IN ({marks})", closed)
    conn.execute(f"DELETE FROM daily_stock_snapshot WHERE trade_date IN ({marks})", closed)
    conn.commit()
    return set(ids), n_snap


def main(argv=None):
    ap = argparse.ArgumentParser(description="一次性重算 event_outcomes 為新的 HORIZONS")
    ap.add_argument("--db", default=stats_db.DB_PATH)
    ap.add_argument("--max-fail-rate", type=float, default=0.03, help="下載失敗事件占比上限")
    ap.add_argument("--max-mismatch-rate", type=float, default=0.02, help="新舊 T+1/5/10 不一致占比上限")
    ap.add_argument("--drop-closed-day-events", action="store_true",
                    help="刪除「訊號日當天大盤休市」（颱風假/國定假日仍跑出訊號）的事件與快照，避免與前一交易日重複計算")
    ap.add_argument("--force", action="store_true", help="驗證未通過也覆蓋原檔")
    args = ap.parse_args(argv)

    db = args.db
    if not os.path.exists(db):
        print(f"[ERROR] 找不到資料庫：{db}")
        return 2

    work = db + ".rebuild.tmp"
    _cleanup(work, work + "-wal", work + "-shm")
    shutil.copyfile(db, work)
    old_o, old_e = _load_detail(db)
    old = {k: v[0] for k, v in old_o.items()}
    print(f"[INFO] 目前 HORIZONS = {HORIZONS}；舊資料 event_outcomes = {len(old)} 筆")

    conn = connect(work)
    removed_ids = set()
    try:
        if args.drop_closed_day_events:
            closed, why = _find_closed_days(conn)
            if closed is None:
                print(f"[WARN] 略過休市日清理：{why}")
            elif not closed:
                print("[INFO] 未發現休市日的訊號事件，無需清理")
            else:
                removed_ids, n_snap = _drop_closed_day_events(conn, closed)
                print(f"[INFO] 已清除休市日 {closed} 的事件 {len(removed_ids)} 筆、每日快照 {n_snap} 筆")
        result = backfill_entry_price_to_next_open(conn)
        # 只是「還沒有下一個交易日」的事件 → 改回 open，之後每日更新自動補上
        conn.execute(
            "UPDATE signal_events SET status='open' WHERE status='missing_next_open' AND event_id IN "
            "(SELECT event_id FROM backfill_failures WHERE reason='no_next_trading_day')"
        )
        reopened = conn.execute("SELECT changes()").fetchone()[0]
        conn.execute("DELETE FROM backfill_failures WHERE reason='no_next_trading_day'")
        conn.commit()

        total_events = conn.execute("SELECT COUNT(*) FROM signal_events").fetchone()[0]
        real_fail = conn.execute("SELECT COUNT(*) FROM backfill_failures").fetchone()[0]
        fail_reasons = dict(conn.execute("SELECT reason, COUNT(*) FROM backfill_failures GROUP BY reason").fetchall())
        status_counts = dict(conn.execute("SELECT status, COUNT(*) FROM signal_events GROUP BY status").fetchall())
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()

    new_o, new_e = _load_detail(work)
    new = {k: v[0] for k, v in new_o.items()}
    new_horizons = sorted({h for (_, h) in new})
    per_h = {h: sum(1 for (_, hh) in new if hh == h) for h in new_horizons}

    common = [k for k in old if k[1] in COMPARE_HORIZONS and k in new and k[0] not in removed_ids]
    old_cmp = [k for k in old if k[1] in COMPARE_HORIZONS and k[0] not in removed_ids]
    mism = [
        k for k in common
        if old[k] is None or new[k] is None or abs(float(old[k]) - float(new[k])) > 0.05
    ]
    lost = [k for k in old_cmp if k not in new]
    fail_rate = real_fail / total_events if total_events else 1.0
    mismatch_rate = len(mism) / len(common) if common else 1.0
    lost_rate = len(lost) / len(old_cmp) if old_cmp else 0.0

    report_csv = os.path.join(os.path.dirname(db) or ".", "rebuild_mismatches.csv")
    checks = [
        ("只含新 horizon", set(new_horizons) <= set(HORIZONS) and bool(new_horizons)),
        ("T+20 有產出", per_h.get(20, 0) > 0),
        ("T+40 有產出", per_h.get(40, 0) > 0),
        (f"下載失敗占比 {fail_rate:.2%} ≤ {args.max_fail_rate:.0%}", fail_rate <= args.max_fail_rate),
        (f"新舊 T+1/5/10 不一致占比 {mismatch_rate:.2%} ≤ {args.max_mismatch_rate:.0%}", mismatch_rate <= args.max_mismatch_rate),
        (f"原有 T+1/5/10 消失占比 {lost_rate:.2%} ≤ {args.max_fail_rate:.0%}", lost_rate <= args.max_fail_rate),
    ]

    print("\n========== 重算報告 ==========")
    print(f"事件總數：{total_events}；狀態：{status_counts}")
    print(f"各 horizon 結果筆數：{per_h}")
    print(f"下載失敗（不含尚無下一交易日）：{real_fail} {fail_reasons}；改回 open 待日更補上：{reopened}")
    print(f"新舊對帳：重疊 {len(common)} 筆，不一致 {len(mism)} 筆；舊有但新缺 {len(lost)} 筆")
    ok = True
    for label, passed in checks:
        print(("  ✔ " if passed else "  ✘ ") + label)
        ok = ok and passed

    if mism:
        _diagnose(old_o, old_e, new_o, new_e, mism, report_csv)

    if not ok and not args.force:
        print("\n[ABORT] 驗證未通過，原始資料庫未更動。")
        if mism:
            print("  不一致樣本（event_id, horizon, 舊, 新）：")
            for k in mism[:8]:
                print("   ", k[0], k[1], old[k], new[k])
        _cleanup(work, work + "-wal", work + "-shm", *glob.glob(work + ".before_next_open_*.bak"))
        return 1
    if not ok:
        print("\n[WARN] 驗證未通過，但指定了 --force，仍會覆蓋原檔。")

    for f in (work + "-wal", work + "-shm"):
        if os.path.exists(f):
            os.remove(f)
    for f in (db + "-wal", db + "-shm"):
        if os.path.exists(f):
            os.remove(f)
    os.replace(work, db)
    _cleanup(*glob.glob(work + ".before_next_open_*.bak"))
    print(f"\n[OK] 已更新 {db}")

    out = os.path.join(os.path.dirname(db) or ".", "stats_payload.json")
    write_stats_payload(db_path=db, out_path=out)
    print(f"[OK] 已重新產生 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
