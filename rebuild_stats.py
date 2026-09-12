import stats_db
conn = stats_db.connect('data/stats.db')  # 換成你的實際路徑
stats_db.backfill_entry_price_to_next_open(conn)
stats_db.refresh_summary_stats(conn)
stats_db.refresh_monthly_strategy_stats(conn)
stats_db.refresh_yearly_strategy_stats(conn)
conn.close()
