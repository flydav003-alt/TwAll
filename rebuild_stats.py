update_watch_transitions(conn, trade_date)
update_event_outcomes(conn)
# 補齊尚未有超額報酬(excess_return_pct)的舊資料。只需要一次 ^TWII 歷史抓取，
# 沒有缺漏時(rows為空)會直接跳過，不會在每天正常執行時額外造成負擔。
backfill_excess_return(conn)
refresh_summary_stats(conn)
refresh_monthly_strategy_stats(conn)
refresh_yearly_strategy_stats(conn)
refresh_monthly_market_regime(conn)
conn.commit()
conn.close()
