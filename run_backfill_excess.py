import stats_db

conn = stats_db.connect()   # 預設讀 data/stats.db
stats_db.init_db(conn)      # 確保schema先升級好
result = stats_db.backfill_excess_return(conn)
print(result)                # {'updated': ..., 'skipped': ...}
conn.close()
