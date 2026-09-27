"""
cleanup_bad_trading_day.py
===========================
用途：清除因 is_trading_day() 誤判而寫入 stats.db 的「假交易日」資料
      （例如 9/25 實際休市，卻誤判為交易日、重複寫入 9/24 的資料）。

用法：
    把這支檔案放到跟 data/stats.db 同一層（也就是專案根目錄），然後：
        python cleanup_bad_trading_day.py 2026-09-25

    可以一次傳多個日期：
        python cleanup_bad_trading_day.py 2026-09-25 2026-09-28

注意：
- 只清 data/stats.db，data/screener_data.json 與 market_data.json
  每天會被整批覆蓋、不會累積，不需要處理。
- 執行前會自動備份一份 stats.db（多一個 .bak_時間戳 檔案），
  萬一清錯了可以直接拿備份覆蓋回去。
- 清完之後會自動呼叫 refresh_summary_stats / refresh_monthly_strategy_stats /
  refresh_yearly_strategy_stats / refresh_monthly_market_regime 重建所有
  衍生出來的統計表，不用手動再跑一次 fetch_data.py。
"""

import shutil
import sqlite3
import sys
from datetime import datetime

import stats_db  # 直接沿用專案裡的 stats_db.py，共用 refresh_* 邏輯

DB_PATH = stats_db.DB_PATH


def cleanup_dates(bad_dates):
    # ── 備份 ──
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{DB_PATH}.bak_{stamp}"
    shutil.copy2(DB_PATH, backup_path)
    print(f"[備份完成] {backup_path}")

    conn = stats_db.connect(DB_PATH)
    stats_db.init_db(conn)

    for bad_date in bad_dates:
        print(f"\n=== 清理 {bad_date} ===")

        # 1) 找出當天產生的所有 signal_events（含策略組合標籤與 watch_confirm）
        event_ids = [
            r[0] for r in conn.execute(
                "SELECT event_id FROM signal_events WHERE trade_date = ?", (bad_date,)
            ).fetchall()
        ]
        print(f"  signal_events: {len(event_ids)} 筆")

        if event_ids:
            q = ",".join("?" * len(event_ids))
            n_out = conn.execute(
                f"DELETE FROM event_outcomes WHERE event_id IN ({q})", event_ids
            ).rowcount
            print(f"  event_outcomes: 刪除 {n_out} 筆")
            conn.execute(f"DELETE FROM signal_events WHERE event_id IN ({q})", event_ids)

        # 2) 每日快照
        n_snap = conn.execute(
            "DELETE FROM daily_stock_snapshot WHERE trade_date = ?", (bad_date,)
        ).rowcount
        print(f"  daily_stock_snapshot: 刪除 {n_snap} 筆")

        # 3) watch_transitions —
        #    a. 如果是「當天新建」的觀察名單 → 整列刪除
        n_watch_new = conn.execute(
            "DELETE FROM watch_transitions WHERE watch_date = ?", (bad_date,)
        ).rowcount
        #    b. 如果是「當天確認轉強」的舊觀察名單 → 只重置確認欄位，退回 open 狀態，
        #       不要整列刪掉（watch_date 是更早、正常的交易日，不該被牽連）
        n_watch_confirm = conn.execute(
            """
            UPDATE watch_transitions
            SET confirm_date=NULL, confirm_kline_score=NULL, confirm_composite_score=NULL,
                confirm_breakout_score=NULL, confirm_swing_score=NULL, confirm_bb_score=NULL,
                confirm_close=NULL, days_to_confirm=NULL, confirmed=0, confirm_type=NULL,
                entry_event_id=NULL, status='open'
            WHERE confirm_date = ?
            """,
            (bad_date,),
        ).rowcount
        print(f"  watch_transitions: 新建刪除 {n_watch_new} 筆, 確認重置 {n_watch_confirm} 筆")

        # 4) 大盤歷史
        n_mkt = conn.execute(
            "DELETE FROM market_daily_history WHERE trade_date = ?", (bad_date,)
        ).rowcount
        print(f"  market_daily_history: 刪除 {n_mkt} 筆")

        conn.commit()

    # ── 重建所有衍生統計（月度/年度策略勝率、summary_stats、大盤趨勢）──
    print("\n=== 重建衍生統計 ===")
    stats_db.refresh_summary_stats(conn)
    stats_db.refresh_monthly_strategy_stats(conn)
    stats_db.refresh_yearly_strategy_stats(conn)
    stats_db.refresh_monthly_market_regime(conn)
    conn.commit()
    conn.close()
    print("完成。")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python cleanup_bad_trading_day.py 2026-09-25 [其他日期...]")
        sys.exit(1)
    cleanup_dates(sys.argv[1:])
