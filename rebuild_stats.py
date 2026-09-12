"""將既有訊號績效統一回補為「訊號隔日開盤買進」口徑。

使用範例：
    python backfill_next_open.py --db data/stats.db
"""

import argparse
from pathlib import Path

import stats_db


def main():
    parser = argparse.ArgumentParser(
        description="以訊號隔日開盤價重算所有績效；舊收盤進場 outcome 不會保留。"
    )
    parser.add_argument(
        "--db",
        required=True,
        help="既有 stats.db 的路徑，例如 data/stats.db",
    )
    args = parser.parse_args()
    db_path = Path(args.db)

    if not db_path.is_file():
        raise SystemExit(f"[停止] 找不到既有資料庫：{db_path}")

    conn = stats_db.connect(str(db_path))
    try:
        stats_db.init_db(conn)
        result = stats_db.backfill_entry_price_to_next_open(conn)
        print("[完成結果]", result)

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
            print("[未回補原因] 無；所有事件均已統一為隔日開盤進場。")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
