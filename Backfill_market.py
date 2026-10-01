"""
backfill_market.py
獨立回補腳本：只補 ^TWII 大盤歷史並重建月度趨勢，約幾十秒，不抓個股、不受交易日檢查限制。
用法：python backfill_market.py
成功/失敗都會明確印出；回補失敗時以非 0 結束碼結束，讓 Actions 顯示紅燈。
"""
import sys
from stats_db import (connect, init_db, backfill_market_history,
                      refresh_monthly_market_regime)

def main():
    conn = connect()
    init_db(conn)
    n0 = conn.execute("SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM market_daily_history").fetchone()
    print(f"[回補前] market_daily_history 筆數={n0[0]} 範圍={n0[1]} ~ {n0[2]}")

    res = backfill_market_history(conn)
    print(f"[回補結果] {res}")
    if res.get("reason"):
        print("[ERROR] 回補失敗，請把上面的訊息貼給開發者")
        sys.exit(1)

    n1 = conn.execute("SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM market_daily_history").fetchone()
    print(f"[回補後] market_daily_history 筆數={n1[0]} 範圍={n1[1]} ~ {n1[2]}")

    refresh_monthly_market_regime(conn)
    print("[月度大盤趨勢]")
    for r in conn.execute("SELECT * FROM monthly_market_regime ORDER BY year_month"):
        print(f"  {r['year_month']}  {r['regime']:<12} 月報酬={r['month_return_pct']:>6}%  "
              f"回撤={r['max_drawdown_pct']}%  大波動日={r['big_move_days']}  旗標={r['flags'] or '-'}  "
              f"基準={r['base_mode']}")
    conn.commit()
    conn.close()

if __name__ == "__main__":
    main()
