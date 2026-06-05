import json
import os
import sqlite3
from datetime import datetime, timedelta

import yfinance as yf

from tw_screener_core import is_otc


DB_PATH = os.path.join("data", "stats.db")
SCORE_VERSION = "2026-06-05-v1"
HORIZONS = (1, 3, 5, 7, 10)


def connect(db_path=DB_PATH):
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


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
        k_bucket = bucket_kline(kline)
        c_bucket = bucket_composite(comp)
        event_type, trigger_source = classify_signal(kline, comp)
        patterns = json.dumps(s.get("patterns", []), ensure_ascii=False, default=str)

        conn.execute(
            """
            INSERT OR REPLACE INTO daily_stock_snapshot (
                trade_date, ticker, name, market, close_price, prev_close, change_pct,
                volume_today, volume_avg20, volume_ratio, kline_score, kline_strategy,
                kline_bucket, composite_score, composite_bucket, rsi14, rs5d, ma5,
                ma20, ma60, price_vs_ma20_pct, price_vs_ma60_pct, ma20_rising,
                week52_pct, inst_buy_days, entry_signal, signal_rank, patterns,
                signal_group, score_version, generated_at, raw_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                trade_date, ticker, s.get("name"), s.get("market"), _num(price),
                _num(s.get("prev_close")), _change_pct(price, s.get("prev_close")),
                s.get("volume_today"), s.get("volume_avg20"), _num(s.get("volume_ratio")),
                _num(kline), s.get("kline_strat"), k_bucket, _num(comp), c_bucket,
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
                    entry_reference_close, entry_price_mode, status, score_version, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    event_id, trade_date, ticker, s.get("name"), event_type, trigger_source,
                    _num(kline), _num(comp), k_bucket, c_bucket, _num(price),
                    "close_after_signal", "open", SCORE_VERSION, now,
                ),
            )

        if event_type in ("COMP_HIGH_K_LOW", "COMP_STRONG_K_LOW"):
            watch_id = f"{trade_date}:{ticker}:WATCH"
            conn.execute(
                """
                INSERT OR IGNORE INTO watch_transitions (
                    watch_id, watch_date, ticker, name, watch_kline_score,
                    watch_composite_score, watch_close, status, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (watch_id, trade_date, ticker, s.get("name"), _num(kline), _num(comp), _num(price), "open", now),
            )

    update_watch_transitions(conn, trade_date)
    update_event_outcomes(conn)
    refresh_summary_stats(conn)
    conn.commit()
    conn.close()


def update_watch_transitions(conn, trade_date):
    rows = conn.execute(
        """
        SELECT w.*, d.kline_score AS today_kline, d.composite_score AS today_comp,
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
                    entry_reference_close, entry_price_mode, status, score_version, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    event_id, trade_date, r["ticker"], r["name"], event_type, "watch_confirm",
                    r["today_kline"], r["today_comp"], bucket_kline(r["today_kline"]),
                    bucket_composite(r["today_comp"]), r["today_close"], "close_after_signal",
                    "open", SCORE_VERSION, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            conn.execute(
                """
                UPDATE watch_transitions
                SET confirm_date=?, confirm_kline_score=?, confirm_composite_score=?,
                    confirm_close=?, days_to_confirm=?, confirmed=1, confirm_type=?,
                    entry_event_id=?, status='confirmed'
                WHERE watch_id=?
                """,
                (
                    trade_date, r["today_kline"], r["today_comp"], r["today_close"],
                    age, confirm_type, event_id, r["watch_id"],
                ),
            )
        elif age >= 3:
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
    end = (datetime.fromisoformat(signal_date) + timedelta(days=25)).strftime("%Y-%m-%d")
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
    conn.execute("DELETE FROM summary_stats")
    updated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    dimensions = [
        ("event_type", "e.event_type", "e.event_type"),
        ("score_matrix", "e.kline_bucket || '|' || e.composite_bucket", "e.kline_bucket, e.composite_bucket"),
    ]
    for group_name, key_expr, select_expr in dimensions:
        rows = conn.execute(
            f"""
            SELECT {select_expr} AS stat_group, o.horizon, o.return_close_pct,
                   o.max_gain_pct, o.max_drawdown_pct, o.is_win,
                   e.event_type, e.kline_bucket, e.composite_bucket
            FROM event_outcomes o
            JOIN signal_events e ON e.event_id = o.event_id
            ORDER BY o.horizon
            """
        ).fetchall()
        grouped = {}
        for r in rows:
            if group_name == "score_matrix":
                key = (r["kline_bucket"], r["composite_bucket"], r["horizon"])
            else:
                key = (r["event_type"], None, None, r["horizon"])
            grouped.setdefault(key, []).append(r)

        for key, items in grouped.items():
            vals = [float(x["return_close_pct"]) for x in items if x["return_close_pct"] is not None]
            if not vals:
                continue
            wins = [v for v in vals if v > 0]
            losses = [v for v in vals if v <= 0]
            gross_win = sum(wins)
            gross_loss = abs(sum(losses))
            if group_name == "score_matrix":
                k_bucket, c_bucket, horizon = key
                event_type = None
                stat_key = f"{group_name}:{k_bucket}:{c_bucket}:T{horizon}"
            else:
                event_type, _, _, horizon = key
                k_bucket = c_bucket = None
                stat_key = f"{group_name}:{event_type}:T{horizon}"
            conn.execute(
                """
                INSERT INTO summary_stats (
                    stat_key, group_name, event_type, kline_bucket, composite_bucket,
                    horizon, sample_count, win_rate, avg_return, median_return,
                    avg_win, avg_loss, profit_factor, max_return, min_return,
                    avg_max_gain, avg_max_drawdown, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    stat_key, group_name, event_type, k_bucket, c_bucket, horizon,
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
               e.composite_score, o.horizon, o.return_close_pct
        FROM signal_events e
        LEFT JOIN event_outcomes o ON o.event_id=e.event_id AND o.horizon=5
        ORDER BY e.trade_date DESC, e.event_type
        LIMIT 80
        """
    ).fetchall()]
    watch = [dict(r) for r in conn.execute(
        """
        SELECT status, confirm_type, COUNT(*) AS count
        FROM watch_transitions
        GROUP BY status, confirm_type
        ORDER BY count DESC
        """
    ).fetchall()]
    conn.close()
    return {"ready": True, "counts": counts, "summary": summary, "recent": recent, "watch": watch}
