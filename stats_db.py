import json
import os
import sqlite3
from datetime import datetime, timedelta

import yfinance as yf

from tw_screener_core import is_otc


DB_PATH = os.path.join("data", "stats.db")
SCORE_VERSION = "2026-06-23-v2"
HORIZONS = (1, 3, 5, 7, 10)


def connect(db_path=DB_PATH):
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _ensure_columns(conn, table, col_defs):
    """col_defs: list of (col_name, sql_type). Adds any missing columns
    via ALTER TABLE so existing databases / historical rows are preserved
    (new columns simply start out NULL for old rows)."""
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, sql_type in col_defs:
        if name not in existing:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")
            except sqlite3.OperationalError:
                pass


def init_db(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS daily_stock_snapshot (
            trade_date TEXT NOT NULL,
            ticker TEXT NOT NULL,
            name TEXT,
            market TEXT,
            close_price REAL,
            prev_close REAL,
            change_pct REAL,
            volume_today INTEGER,
            volume_avg20 INTEGER,
            volume_ratio REAL,
            kline_score REAL,
            kline_strategy TEXT,
            kline_bucket TEXT,
            composite_score REAL,
            composite_bucket TEXT,
            rsi14 REAL,
            rs5d REAL,
            ma5 REAL,
            ma20 REAL,
            ma60 REAL,
            price_vs_ma20_pct REAL,
            price_vs_ma60_pct REAL,
            ma20_rising INTEGER,
            week52_pct REAL,
            inst_buy_days INTEGER,
            entry_signal TEXT,
            signal_rank INTEGER,
            patterns TEXT,
            signal_group TEXT,
            score_version TEXT,
            generated_at TEXT,
            raw_json TEXT,
            PRIMARY KEY (trade_date, ticker)
        );

        CREATE TABLE IF NOT EXISTS signal_events (
            event_id TEXT PRIMARY KEY,
            trade_date TEXT NOT NULL,
            ticker TEXT NOT NULL,
            name TEXT,
            event_type TEXT NOT NULL,
            trigger_source TEXT,
            kline_score REAL,
            composite_score REAL,
            kline_bucket TEXT,
            composite_bucket TEXT,
            entry_reference_close REAL,
            entry_price_mode TEXT,
            status TEXT DEFAULT 'open',
            score_version TEXT,
            created_at TEXT,
            UNIQUE (trade_date, ticker, event_type)
        );

        CREATE TABLE IF NOT EXISTS event_outcomes (
            event_id TEXT NOT NULL,
            ticker TEXT NOT NULL,
            signal_date TEXT NOT NULL,
            horizon INTEGER NOT NULL,
            target_date TEXT,
            target_close REAL,
            target_high REAL,
            target_low REAL,
            return_close_pct REAL,
            max_gain_pct REAL,
            max_drawdown_pct REAL,
            is_win INTEGER,
            is_big_win INTEGER,
            is_big_loss INTEGER,
            filled_at TEXT,
            PRIMARY KEY (event_id, horizon)
        );

        CREATE TABLE IF NOT EXISTS watch_transitions (
            watch_id TEXT PRIMARY KEY,
            watch_date TEXT NOT NULL,
            ticker TEXT NOT NULL,
            name TEXT,
            watch_kline_score REAL,
            watch_composite_score REAL,
            watch_close REAL,
            confirm_date TEXT,
            confirm_kline_score REAL,
            confirm_composite_score REAL,
            confirm_close REAL,
            days_to_confirm INTEGER,
            confirmed INTEGER DEFAULT 0,
            confirm_type TEXT,
            entry_event_id TEXT,
            status TEXT DEFAULT 'open',
            created_at TEXT,
            UNIQUE (watch_date, ticker)
        );

        CREATE TABLE IF NOT EXISTS summary_stats (
            stat_key TEXT PRIMARY KEY,
            group_name TEXT,
            event_type TEXT,
            kline_bucket TEXT,
            composite_bucket TEXT,
            horizon INTEGER,
            sample_count INTEGER,
            win_rate REAL,
            avg_return REAL,
            median_return REAL,
            avg_win REAL,
            avg_loss REAL,
            profit_factor REAL,
            max_return REAL,
            min_return REAL,
            avg_max_gain REAL,
            avg_max_drawdown REAL,
            updated_at TEXT
        );
        """
    )

    # ── 新增欄位：突破分（breakout / vcp）與波段分（swing） ──
    # 用 ALTER TABLE 補欄位，舊資料列維持 NULL，不影響既有統計。
    _ensure_columns(conn, "daily_stock_snapshot", [
        ("breakout_score", "REAL"), ("breakout_bucket", "TEXT"),
        ("swing_score", "REAL"), ("swing_bucket", "TEXT"),
    ])
    _ensure_columns(conn, "signal_events", [
        ("breakout_score", "REAL"), ("breakout_bucket", "TEXT"),
        ("swing_score", "REAL"), ("swing_bucket", "TEXT"),
    ])
    _ensure_columns(conn, "watch_transitions", [
        ("watch_breakout_score", "REAL"), ("watch_swing_score", "REAL"),
        ("confirm_breakout_score", "REAL"), ("confirm_swing_score", "REAL"),
    ])
    _ensure_columns(conn, "summary_stats", [
        ("breakout_bucket", "TEXT"), ("swing_bucket", "TEXT"),
    ])
    conn.commit()


def bucket_kline(score):
    if score is None:
        return "NA"
    if score >= 78:
        return "A_78UP"
    if score >= 70:
        return "B_70_77"
    if score >= 60:
        return "C_60_69"
    return "D_LT60"


def bucket_composite(score):
    if score is None:
        return "NA"
    if score >= 88:
        return "A_88UP"
    if score >= 75:
        return "B_75_87"
    if score >= 60:
        return "C_60_74"
    return "D_LT60"


def bucket_breakout(score):
    """突破分（breakout / VCP score）分級，與前端 vcpCol() 對齊。"""
    if score is None:
        return "NA"
    if score >= 70:
        return "A_70UP"
    if score >= 50:
        return "B_50_69"
    if score >= 30:
        return "C_30_49"
    return "D_LT30"


def bucket_swing(score):
    """波段分（swing score）分級，沿用與突破分相同的級距。"""
    if score is None:
        return "NA"
    if score >= 70:
        return "A_70UP"
    if score >= 50:
        return "B_50_69"
    if score >= 30:
        return "C_30_49"
    return "D_LT30"


# 四項分數維度的通用定義，供 summary_stats 的單維 / 交叉維度統計共用
SCORE_DIMS = {
    "kline":     {"bucket_col": "kline_bucket",     "label": "K線分"},
    "composite": {"bucket_col": "composite_bucket", "label": "綜合分"},
    "breakout":  {"bucket_col": "breakout_bucket",  "label": "突破分"},
    "swing":     {"bucket_col": "swing_bucket",      "label": "波段分"},
}
CROSS_PAIRS = [
    ("kline", "composite"), ("kline", "breakout"), ("kline", "swing"),
    ("composite", "breakout"), ("composite", "swing"), ("breakout", "swing"),
]


def classify_signal(kline_score, composite_score):
    k = kline_score if kline_score is not None else -1
    c = composite_score if composite_score is not None else -1
    if k >= 78 and c >= 88:
        return "BOTH_STRONG", "both"
    if k >= 70 and c >= 75:
        return "ENTRY", "both"
    if c >= 88 and k < 70:
        return "COMP_STRONG_K_LOW", "composite"
    if c >= 75 and k < 70:
        return "COMP_HIGH_K_LOW", "composite"
    if k >= 78 and c < 75:
        return "K_STRONG_COMP_LOW", "kline"
    if k >= 70 and c < 75:
        return "K_HIGH_COMP_LOW", "kline"
    return "NEUTRAL", "none"


def _num(v):
    return None if v is None else float(v)


def _change_pct(price, prev):
    if not price or not prev:
        return None
    return round((price - prev) / prev * 100, 2)


def save_daily_run(results, generated_at=None, db_path=DB_PATH):
    generated_at = generated_at or datetime.now().strftime("%Y-%m-%d %H:%M")
    trade_date = generated_at[:10]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = connect(db_path)
    init_db(conn)

    for s in results:
        ticker = str(s.get("ticker", "")).strip()
        price = s.get("price")
        if not ticker or price is None:
            continue

        kline = s.get("kline_score")
        comp = s.get("composite")
        breakout = s.get("vcp_score")          # 突破分（SEPA+VCP 突破評分）
        swing = s.get("swing_score")            # 波段分（尚未有計分函式時為 None）
        k_bucket = bucket_kline(kline)
        c_bucket = bucket_composite(comp)
        b_bucket = bucket_breakout(breakout)
        sw_bucket = bucket_swing(swing)
        event_type, trigger_source = classify_signal(kline, comp)
        patterns = json.dumps(s.get("patterns", []), ensure_ascii=False, default=str)

        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO daily_stock_snapshot (
                    trade_date, ticker, name, market, close_price, prev_close, change_pct,
                    volume_today, volume_avg20, volume_ratio, kline_score, kline_strategy,
                    kline_bucket, composite_score, composite_bucket, breakout_score, breakout_bucket,
                    swing_score, swing_bucket, rsi14, rs5d, ma5,
                    ma20, ma60, price_vs_ma20_pct, price_vs_ma60_pct, ma20_rising,
                    week52_pct, inst_buy_days, entry_signal, signal_rank, patterns,
                    signal_group, score_version, generated_at, raw_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    trade_date, ticker, s.get("name"), s.get("market"), _num(price),
                    _num(s.get("prev_close")), _change_pct(price, s.get("prev_close")),
                    s.get("volume_today"), s.get("volume_avg20"), _num(s.get("volume_ratio")),
                    _num(kline), s.get("kline_strat"), k_bucket, _num(comp), c_bucket,
                    _num(breakout), b_bucket, _num(swing), sw_bucket,
                    _num(s.get("rsi14")), _num(s.get("rs5d")), _num(s.get("ma5")),
                    _num(s.get("ma20")), _num(s.get("ma60")), _num(s.get("price_vs_ma20_pct")),
                    _num(s.get("price_vs_ma60_pct")), 1 if s.get("ma20_rising") else 0,
                    _num(s.get("week52_pct")), int(s.get("inst_buy_days") or 0),
                    s.get("entry_signal", ""), int(s.get("signal_rank") or 0), patterns,
                    event_type, SCORE_VERSION, generated_at,
                    json.dumps(s, ensure_ascii=False, default=str),
                ),
            )

            if event_type != "NEUTRAL":
                event_id = f"{trade_date}:{ticker}:{event_type}"
                conn.execute(
                    """
                    INSERT OR IGNORE INTO signal_events (
                        event_id, trade_date, ticker, name, event_type, trigger_source,
                        kline_score, composite_score, kline_bucket, composite_bucket,
                        breakout_score, breakout_bucket, swing_score, swing_bucket,
                        entry_reference_close, entry_price_mode, status, score_version, created_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        event_id, trade_date, ticker, s.get("name"), event_type, trigger_source,
                        _num(kline), _num(comp), k_bucket, c_bucket,
                        _num(breakout), b_bucket, _num(swing), sw_bucket,
                        _num(price), "close_after_signal", "open", SCORE_VERSION, now,
                    ),
                )

            if event_type in ("COMP_HIGH_K_LOW", "COMP_STRONG_K_LOW"):
                watch_id = f"{trade_date}:{ticker}:WATCH"
                conn.execute(
                    """
                    INSERT OR IGNORE INTO watch_transitions (
                        watch_id, watch_date, ticker, name, watch_kline_score,
                        watch_composite_score, watch_breakout_score, watch_swing_score,
                        watch_close, status, created_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (watch_id, trade_date, ticker, s.get("name"), _num(kline), _num(comp),
                     _num(breakout), _num(swing), _num(price), "open", now),
                )
        except Exception as e:
            print(f"[WARN] 寫入 {ticker} 失敗，略過：{e}")

    update_watch_transitions(conn, trade_date)
    update_event_outcomes(conn)
    refresh_summary_stats(conn)
    conn.commit()
    conn.close()


def update_watch_transitions(conn, trade_date):
    rows = conn.execute(
        """
        SELECT w.*, d.kline_score AS today_kline, d.composite_score AS today_comp,
               d.breakout_score AS today_breakout, d.swing_score AS today_swing,
               d.close_price AS today_close
        FROM watch_transitions w
        JOIN daily_stock_snapshot d ON d.ticker = w.ticker
        WHERE w.status = 'open' AND d.trade_date = ? AND w.watch_date < ?
        """,
        (trade_date, trade_date),
    ).fetchall()
    for r in rows:
        age = (datetime.fromisoformat(trade_date) - datetime.fromisoformat(r["watch_date"])).days
        if r["today_kline"] is not None and r["today_kline"] >= 70:
            event_type = "WATCH_CONFIRMED"
            event_id = f"{trade_date}:{r['ticker']}:{event_type}"
            confirm_type = "kline_78up" if r["today_kline"] >= 78 else "kline_70up"
            conn.execute(
                """
                INSERT OR IGNORE INTO signal_events (
                    event_id, trade_date, ticker, name, event_type, trigger_source,
                    kline_score, composite_score, kline_bucket, composite_bucket,
                    breakout_score, breakout_bucket, swing_score, swing_bucket,
                    entry_reference_close, entry_price_mode, status, score_version, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    event_id, trade_date, r["ticker"], r["name"], event_type, "watch_confirm",
                    r["today_kline"], r["today_comp"], bucket_kline(r["today_kline"]),
                    bucket_composite(r["today_comp"]),
                    r["today_breakout"], bucket_breakout(r["today_breakout"]),
                    r["today_swing"], bucket_swing(r["today_swing"]),
                    r["today_close"], "close_after_signal",
                    "open", SCORE_VERSION, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            conn.execute(
                """
                UPDATE watch_transitions
                SET confirm_date=?, confirm_kline_score=?, confirm_composite_score=?,
                    confirm_breakout_score=?, confirm_swing_score=?,
                    confirm_close=?, days_to_confirm=?, confirmed=1, confirm_type=?,
                    entry_event_id=?, status='confirmed'
                WHERE watch_id=?
                """,
                (
                    trade_date, r["today_kline"], r["today_comp"],
                    r["today_breakout"], r["today_swing"], r["today_close"],
                    age, confirm_type, event_id, r["watch_id"],
                ),
            )
        elif age >= 7:
            conn.execute(
                "UPDATE watch_transitions SET days_to_confirm=?, confirmed=0, confirm_type='expired', status='expired' WHERE watch_id=?",
                (age, r["watch_id"]),
            )


def _ticker_symbol(ticker, market=None):
    if market == "TWO" or (not market and is_otc(ticker)):
        return f"{ticker}.TWO"
    return f"{ticker}.TW"


def _history_after_signal(ticker, signal_date, market=None):
    start = (datetime.fromisoformat(signal_date) - timedelta(days=1)).strftime("%Y-%m-%d")
    end = (datetime.fromisoformat(signal_date) + timedelta(days=35)).strftime("%Y-%m-%d")
    for symbol in (_ticker_symbol(ticker, market), f"{ticker}.TW", f"{ticker}.TWO"):
        try:
            hist = yf.Ticker(symbol).history(start=start, end=end)
            if hist is not None and len(hist) >= 2:
                hist = hist.reset_index()
                hist["date_str"] = hist["Date"].dt.strftime("%Y-%m-%d")
                return hist
        except Exception:
            continue
    return None


def update_event_outcomes(conn):
    events = conn.execute(
        """
        SELECT e.*, d.market
        FROM signal_events e
        LEFT JOIN daily_stock_snapshot d
          ON d.trade_date = e.trade_date AND d.ticker = e.ticker
        WHERE e.status IN ('open', 'partial')
        ORDER BY e.trade_date
        """
    ).fetchall()
    filled_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for e in events:
        entry = e["entry_reference_close"]
        if not entry:
            continue
        hist = _history_after_signal(e["ticker"], e["trade_date"], e["market"])
        if hist is None:
            continue
        future = hist[hist["date_str"] > e["trade_date"]].copy()
        if future.empty:
            continue
        filled = 0
        for horizon in HORIZONS:
            exists = conn.execute(
                "SELECT 1 FROM event_outcomes WHERE event_id=? AND horizon=?",
                (e["event_id"], horizon),
            ).fetchone()
            if exists or len(future) < horizon:
                continue
            window = future.iloc[:horizon]
            target = future.iloc[horizon - 1]
            target_close = float(target["Close"])
            ret = round((target_close / entry - 1) * 100, 2)
            max_gain = round((float(window["High"].max()) / entry - 1) * 100, 2)
            max_drawdown = round((float(window["Low"].min()) / entry - 1) * 100, 2)
            conn.execute(
                """
                INSERT OR REPLACE INTO event_outcomes (
                    event_id, ticker, signal_date, horizon, target_date, target_close,
                    target_high, target_low, return_close_pct, max_gain_pct,
                    max_drawdown_pct, is_win, is_big_win, is_big_loss, filled_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    e["event_id"], e["ticker"], e["trade_date"], horizon,
                    target["date_str"], target_close, float(target["High"]), float(target["Low"]),
                    ret, max_gain, max_drawdown, 1 if ret > 0 else 0,
                    1 if ret >= 3 else 0, 1 if ret <= -3 else 0, filled_at,
                ),
            )
            filled += 1
        done = conn.execute(
            "SELECT COUNT(*) AS c FROM event_outcomes WHERE event_id=?",
            (e["event_id"],),
        ).fetchone()["c"]
        if done >= len(HORIZONS):
            conn.execute("UPDATE signal_events SET status='matured' WHERE event_id=?", (e["event_id"],))
        elif done > 0 or filled > 0:
            conn.execute("UPDATE signal_events SET status='partial' WHERE event_id=?", (e["event_id"],))


def _median(vals):
    vals = sorted(vals)
    if not vals:
        return None
    mid = len(vals) // 2
    if len(vals) % 2:
        return vals[mid]
    return round((vals[mid - 1] + vals[mid]) / 2, 2)


def refresh_summary_stats(conn):
    """
    重建 summary_stats，產生：
      - event_type            各訊號類型 × horizon
      - single_<dim>          K線分/綜合分/突破分/波段分 各自的單一分數區間 × horizon
                               （供「各分數區間 T+1~T+10 勝率走勢」四張圖使用）
      - cross_<a>_<b>         四項分數兩兩交叉（共 6 組）× horizon
                               （供「分數熱圖」四向交叉比對使用，含原本的 K線×綜合）
    """
    conn.execute("DELETE FROM summary_stats")
    updated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    rows = conn.execute(
        """
        SELECT o.horizon, o.return_close_pct, o.max_gain_pct, o.max_drawdown_pct,
               e.event_type, e.kline_bucket, e.composite_bucket, e.breakout_bucket, e.swing_bucket
        FROM event_outcomes o
        JOIN signal_events e ON e.event_id = o.event_id
        """
    ).fetchall()

    groups = {}

    def add(key, r):
        groups.setdefault(key, []).append(r)

    for r in rows:
        h = r["horizon"]
        kb, cb, bb, sb = r["kline_bucket"], r["composite_bucket"], r["breakout_bucket"], r["swing_bucket"]
        add(("event_type", r["event_type"], None, None, None, None, h), r)
        add(("single_kline", None, kb, None, None, None, h), r)
        add(("single_composite", None, None, cb, None, None, h), r)
        add(("single_breakout", None, None, None, bb, None, h), r)
        add(("single_swing", None, None, None, None, sb, h), r)
        add(("cross_kline_composite", None, kb, cb, None, None, h), r)
        add(("cross_kline_breakout", None, kb, None, bb, None, h), r)
        add(("cross_kline_swing", None, kb, None, None, sb, h), r)
        add(("cross_composite_breakout", None, None, cb, bb, None, h), r)
        add(("cross_composite_swing", None, None, cb, None, sb, h), r)
        add(("cross_breakout_swing", None, None, None, bb, sb, h), r)

    for key, items in groups.items():
        group_name, event_type, kb, cb, bb, sb, horizon = key
        vals = [float(x["return_close_pct"]) for x in items if x["return_close_pct"] is not None]
        if not vals:
            continue
        wins = [v for v in vals if v > 0]
        losses = [v for v in vals if v <= 0]
        gross_win = sum(wins)
        gross_loss = abs(sum(losses))
        stat_key = f"{group_name}:{event_type}:{kb}:{cb}:{bb}:{sb}:T{horizon}"
        conn.execute(
            """
            INSERT INTO summary_stats (
                stat_key, group_name, event_type, kline_bucket, composite_bucket,
                breakout_bucket, swing_bucket,
                horizon, sample_count, win_rate, avg_return, median_return,
                avg_win, avg_loss, profit_factor, max_return, min_return,
                avg_max_gain, avg_max_drawdown, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                stat_key, group_name, event_type, kb, cb, bb, sb, horizon,
                len(vals), round(len(wins) / len(vals) * 100, 1),
                round(sum(vals) / len(vals), 2), _median(vals),
                round(sum(wins) / len(wins), 2) if wins else None,
                round(sum(losses) / len(losses), 2) if losses else None,
                round(gross_win / gross_loss, 2) if gross_loss else None,
                round(max(vals), 2), round(min(vals), 2),
                round(sum(float(x["max_gain_pct"]) for x in items if x["max_gain_pct"] is not None) / len(items), 2),
                round(sum(float(x["max_drawdown_pct"]) for x in items if x["max_drawdown_pct"] is not None) / len(items), 2),
                updated,
            ),
        )


def export_stats_payload(db_path=DB_PATH):
    if not os.path.exists(db_path):
        return {"ready": False}
    conn = connect(db_path)
    init_db(conn)
    summary = [dict(r) for r in conn.execute("SELECT * FROM summary_stats ORDER BY group_name, horizon, sample_count DESC").fetchall()]
    counts = dict(conn.execute(
        """
        SELECT
          (SELECT COUNT(*) FROM daily_stock_snapshot) AS snapshots,
          (SELECT COUNT(*) FROM signal_events) AS events,
          (SELECT COUNT(*) FROM event_outcomes) AS outcomes,
          (SELECT COUNT(*) FROM watch_transitions) AS watches
        """
    ).fetchone())
    recent = [dict(r) for r in conn.execute(
        """
        SELECT e.trade_date, e.ticker, e.name, e.event_type, e.kline_score,
               e.composite_score, e.breakout_score, e.swing_score,
               e.entry_reference_close, e.status,
               MAX(CASE WHEN o.horizon=1 THEN o.return_close_pct END) AS t1_return,
               MAX(CASE WHEN o.horizon=3 THEN o.return_close_pct END) AS t3_return,
               MAX(CASE WHEN o.horizon=5 THEN o.return_close_pct END) AS t5_return,
               MAX(CASE WHEN o.horizon=7 THEN o.return_close_pct END) AS t7_return,
               MAX(CASE WHEN o.horizon=10 THEN o.return_close_pct END) AS t10_return
        FROM signal_events e
        LEFT JOIN event_outcomes o ON o.event_id=e.event_id
        GROUP BY e.event_id
        ORDER BY e.trade_date DESC, e.event_type
        LIMIT 120
        """
    ).fetchall()]
    threshold_stats = []
    threshold_defs = [
        ("K線 >= 70", "e.kline_score >= 70"),
        ("K線 >= 75", "e.kline_score >= 75"),
        ("K線 >= 78", "e.kline_score >= 78"),
        ("K線 >= 80", "e.kline_score >= 80"),
        ("綜合分 >= 75", "e.composite_score >= 75"),
        ("綜合分 >= 80", "e.composite_score >= 80"),
        ("綜合分 >= 85", "e.composite_score >= 85"),
        ("綜合分 >= 88", "e.composite_score >= 88"),
        ("突破分 >= 30", "e.breakout_score >= 30"),
        ("突破分 >= 50", "e.breakout_score >= 50"),
        ("突破分 >= 70", "e.breakout_score >= 70"),
        ("波段分 >= 30", "e.swing_score >= 30"),
        ("波段分 >= 50", "e.swing_score >= 50"),
        ("波段分 >= 70", "e.swing_score >= 70"),
        ("K線 >= 75 且綜合分 >= 80", "e.kline_score >= 75 AND e.composite_score >= 80"),
        ("K線 >= 78 且綜合分 >= 88", "e.kline_score >= 78 AND e.composite_score >= 88"),
        ("K線 >= 70 且突破分 >= 50", "e.kline_score >= 70 AND e.breakout_score >= 50"),
        ("綜合分 >= 75 且突破分 >= 50", "e.composite_score >= 75 AND e.breakout_score >= 50"),
        ("K線 >= 70 且波段分 >= 50", "e.kline_score >= 70 AND e.swing_score >= 50"),
        ("綜合分 >= 75 且波段分 >= 50", "e.composite_score >= 75 AND e.swing_score >= 50"),
        ("突破分 >= 50 且波段分 >= 50", "e.breakout_score >= 50 AND e.swing_score >= 50"),
    ]
    for label, where_sql in threshold_defs:
        rows = conn.execute(
            f"""
            SELECT o.horizon, COUNT(*) AS sample_count,
                   ROUND(AVG(CASE WHEN o.return_close_pct > 0 THEN 1.0 ELSE 0.0 END) * 100, 1) AS win_rate,
                   ROUND(AVG(o.return_close_pct), 2) AS avg_return,
                   ROUND(AVG(o.max_gain_pct), 2) AS avg_max_gain,
                   ROUND(AVG(o.max_drawdown_pct), 2) AS avg_max_drawdown
            FROM event_outcomes o
            JOIN signal_events e ON e.event_id = o.event_id
            WHERE {where_sql}
            GROUP BY o.horizon
            ORDER BY o.horizon
            """
        ).fetchall()
        for r in rows:
            item = dict(r)
            item["rule"] = label
            threshold_stats.append(item)
    watch = [dict(r) for r in conn.execute(
        """
        SELECT status, confirm_type, COUNT(*) AS count
        FROM watch_transitions
        GROUP BY status, confirm_type
        ORDER BY count DESC
        """
    ).fetchall()]
    conn.close()
    return {
        "ready": True,
        "counts": counts,
        "summary": summary,
        "recent": recent,
        "threshold_stats": threshold_stats,
        "watch": watch,
    }
