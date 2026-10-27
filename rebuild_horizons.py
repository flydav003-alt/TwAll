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

import stats_db
from stats_db import HORIZONS, backfill_entry_price_to_next_open, connect, write_stats_payload

COMPARE_HORIZONS = (1, 5, 10)   # 新舊兩套 horizon 都有的天期，用來對帳


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


def main(argv=None):
    ap = argparse.ArgumentParser(description="一次性重算 event_outcomes 為新的 HORIZONS")
    ap.add_argument("--db", default=stats_db.DB_PATH)
    ap.add_argument("--max-fail-rate", type=float, default=0.03, help="下載失敗事件占比上限")
    ap.add_argument("--max-mismatch-rate", type=float, default=0.02, help="新舊 T+1/5/10 不一致占比上限")
    ap.add_argument("--force", action="store_true", help="驗證未通過也覆蓋原檔")
    args = ap.parse_args(argv)

    db = args.db
    if not os.path.exists(db):
        print(f"[ERROR] 找不到資料庫：{db}")
        return 2

    work = db + ".rebuild.tmp"
    _cleanup(work, work + "-wal", work + "-shm")
    shutil.copyfile(db, work)
    old = _load_outcomes(db)
    print(f"[INFO] 目前 HORIZONS = {HORIZONS}；舊資料 event_outcomes = {len(old)} 筆")

    conn = connect(work)
    try:
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

    new = _load_outcomes(work)
    new_horizons = sorted({h for (_, h) in new})
    per_h = {h: sum(1 for (_, hh) in new if hh == h) for h in new_horizons}

    common = [k for k in old if k[1] in COMPARE_HORIZONS and k in new]
    old_cmp = [k for k in old if k[1] in COMPARE_HORIZONS]
    mism = [
        k for k in common
        if old[k] is None or new[k] is None or abs(float(old[k]) - float(new[k])) > 0.05
    ]
    lost = [k for k in old_cmp if k not in new]
    fail_rate = real_fail / total_events if total_events else 1.0
    mismatch_rate = len(mism) / len(common) if common else 1.0
    lost_rate = len(lost) / len(old_cmp) if old_cmp else 0.0

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
