"""GitHub Actions 一次性回補入口：統一以訊號隔日開盤價進場。

此檔案刻意固定使用 data/stats.db，供 workflow 直接執行：
    python rebuild_stats.py
"""

from pathlib import Path

import stats_db


DB_PATH = Path("data") / "stats.db"


def main():
    if not DB_PATH.is_file():
        raise SystemExit(f"[停止] 找不到既有資料庫：{DB_PATH}")

    conn = stats_db.connect(str(DB_PATH))
    try:
        stats_db.init_db(conn)
        result = stats_db.backfill_entry_price_to_next_open(conn)
        print(f"[回補結果] {result}")

        failures = conn.execute(
            """
            SELECT reason, COUNT(*) AS count
            FROM backfill_failures
            GROUP BY reason
            ORDER BY count DESC
            """
        ).fetchall()
        if failures:
            print("[未回補原因]")
            for row in failures:
                print(f"  {row['reason']}: {row['count']}")
        else:
            print("[未回補原因] 無")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
