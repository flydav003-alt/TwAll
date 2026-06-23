"""
rebuild_stats.py
=================
一次性工具：在不重新抓資料的情況下，用新版 refresh_summary_stats()
重建 data/stats.db 裡的 summary_stats 彙總表。

用途：剛部署完新版 stats_db.py（group_name 改為 single_*/cross_* 之後），
舊資料庫裡的 summary_stats 還是舊的 group_name（如 score_matrix），
跑一次這個腳本就能立刻補上新版分組，不用等下一次 GitHub Actions。

執行方式（在專案根目錄，跟 data/ 同層）：
    python rebuild_stats.py
"""
import stats_db as db

conn = db.connect(db.DB_PATH)
db.init_db(conn)          # 確保 breakout/swing 等新欄位已存在
db.refresh_summary_stats(conn)
conn.commit()

rows = conn.execute(
    "SELECT group_name, COUNT(*) AS c FROM summary_stats GROUP BY group_name ORDER BY group_name"
).fetchall()
print("重建完成，summary_stats 各群組筆數：")
for r in rows:
    print(f"  {r['group_name']:<28} {r['c']}")

conn.close()
