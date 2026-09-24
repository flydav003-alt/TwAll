"""
rebuild_stats.py
=================
手動重建/補齊 stats.db 的統計資料，不需要重新抓股價，只重算資料庫裡已有的資料。
用途：
  1. 補齊尚未有超額報酬(excess_return_pct)的舊資料（backfill_excess_return）
  2. 重新整理 summary_stats / monthly / yearly 策略統計表
執行方式：python rebuild_stats.py
"""

import stats_db

conn = stats_db.connect()   # 預設讀 data/stats.db
stats_db.init_db(conn)      # 確保 schema 已升級到最新（含 excess_return_pct 等新欄位）

print("[1/2] 回補超額報酬 (backfill_excess_return) ...")
result = stats_db.backfill_excess_return(conn)
print("      結果：", result)

print("[2/2] 重新整理統計彙總表 ...")
stats_db.refresh_summary_stats(conn)
stats_db.refresh_monthly_strategy_stats(conn)
stats_db.refresh_yearly_strategy_stats(conn)
stats_db.refresh_monthly_market_regime(conn)
conn.commit()
conn.close()

print("[完成] 已重建統計資料。")
