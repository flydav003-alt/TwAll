"""
rebuild_stats.py
=================
手動重建/補齊 stats.db 的統計資料，不需要重新抓股價，只重算資料庫裡已有的資料。

用途：
  1. 強制清空並重算全部 excess_return_pct(超額報酬) —— 因為大盤對齊基準
     (entry_date用開盤價、target_date用收盤價，才能跟個股報酬的計算基準完全對齊)
     修正過，舊資料是用「entry_date收盤價」當基準算出來的，兩者存在系統性偏移，
     不能讓新舊算法的數字混在同一個資料庫欄位裡，所以這裡改成「先清空、全部用
     新算法重算」，而不是 backfill_excess_return() 原本「只補NULL」的增量邏輯。
     這只需要在「大盤對齊算法本身有修正」時執行一次；平常的每日排程
     (fetch_data.py -> save_daily_run -> backfill_excess_return) 用增量補值即可，
     不需要每天都全部重算。
  2. 重新整理 summary_stats / monthly / yearly 策略統計表

執行方式：python rebuild_stats.py
"""

import stats_db

conn = stats_db.connect()   # 預設讀 data/stats.db
stats_db.init_db(conn)      # 確保 schema 已升級到最新（含 excess_return_pct 等新欄位）

print("[0/2] 清空舊算法(entry_date收盤價基準)算出的超額報酬，準備用新算法(entry_date開盤價基準)全部重算 ...")
before = conn.execute(
    "SELECT COUNT(*) c FROM event_outcomes WHERE excess_return_pct IS NOT NULL"
).fetchone()["c"]
conn.execute(
    "UPDATE event_outcomes SET excess_return_pct=NULL, twii_entry_price=NULL, twii_target_price=NULL"
)
conn.commit()
print(f"      已清空 {before} 筆舊超額報酬資料。")

print("[1/2] 用新算法重新計算全部超額報酬 (backfill_excess_return) ...")
result = stats_db.backfill_excess_return(conn)
print("      結果：", {k: v for k, v in result.items() if k != "skip_samples"})
if result.get("skip_samples"):
    print("      略過樣本(供排查原因)：")
    for s in result["skip_samples"]:
        print("       ", s)

print("[2/2] 重新整理統計彙總表 ...")
stats_db.refresh_summary_stats(conn)
stats_db.refresh_monthly_strategy_stats(conn)
stats_db.refresh_yearly_strategy_stats(conn)
stats_db.refresh_monthly_market_regime(conn)
conn.commit()
conn.close()

print("[完成] 已用新算法重建全部超額報酬統計資料。")
