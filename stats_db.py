import json
import math
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

        -- excess_return_pct / twii_entry_price / twii_target_price 用 ALTER TABLE 於下方補上，
        -- 讓舊資料庫升級時不用重建表。

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
        ("rs_score", "REAL"), ("vcp_status", "TEXT"),
    ])
    _ensure_columns(conn, "signal_events", [
        ("breakout_score", "REAL"), ("breakout_bucket", "TEXT"),
        ("swing_score", "REAL"), ("swing_bucket", "TEXT"),
        ("rsi14", "REAL"), ("vcp_status", "TEXT"), ("entry_signal", "TEXT"),
        ("inst_buy_days", "INTEGER"),
    ])
    # 舊事件尚未保存今日訊號時，從同日的每日快照安全回填；新事件則在寫入時直接保存。
    conn.execute(
        """
        UPDATE signal_events
        SET entry_signal = (
            SELECT d.entry_signal
            FROM daily_stock_snapshot d
            WHERE d.trade_date = signal_events.trade_date
              AND d.ticker = signal_events.ticker
        )
        WHERE (entry_signal IS NULL OR entry_signal = '')
          AND EXISTS (
            SELECT 1
            FROM daily_stock_snapshot d
            WHERE d.trade_date = signal_events.trade_date
              AND d.ticker = signal_events.ticker
              AND d.entry_signal IS NOT NULL
              AND d.entry_signal <> ''
          )
        """
    )
    conn.execute(
        """
        UPDATE signal_events
        SET inst_buy_days = (
            SELECT d.inst_buy_days
            FROM daily_stock_snapshot d
            WHERE d.trade_date = signal_events.trade_date
              AND d.ticker = signal_events.ticker
        )
        WHERE inst_buy_days IS NULL
          AND EXISTS (
            SELECT 1
            FROM daily_stock_snapshot d
            WHERE d.trade_date = signal_events.trade_date
              AND d.ticker = signal_events.ticker
          )
        """
    )
    _ensure_columns(conn, "watch_transitions", [
        ("watch_breakout_score", "REAL"), ("watch_swing_score", "REAL"),
        ("confirm_breakout_score", "REAL"), ("confirm_swing_score", "REAL"),
    ])
    _ensure_columns(conn, "summary_stats", [
        ("breakout_bucket", "TEXT"), ("swing_bucket", "TEXT"),
    ])

    # ── 新增欄位：BB分（布林通道分）──
    # setup 額外存起來（lower_reversal / squeeze_consolidation / upper_breakout / neutral），
    # 方便回測時區分「哪種型態」的BB分表現最好，不會被三種型態的分數混在一起稀釋。
    _ensure_columns(conn, "daily_stock_snapshot", [
        ("bb_score", "REAL"), ("bb_bucket", "TEXT"), ("bb_setup", "TEXT"),
    ])
    _ensure_columns(conn, "signal_events", [
        ("bb_score", "REAL"), ("bb_bucket", "TEXT"), ("bb_setup", "TEXT"),
    ])
    _ensure_columns(conn, "watch_transitions", [
        ("watch_bb_score", "REAL"), ("confirm_bb_score", "REAL"),
    ])
    _ensure_columns(conn, "summary_stats", [
        ("bb_bucket", "TEXT"),
    ])

    # ── 新增欄位：RS分 / RS5日分 / 量比 ──
    # 這三項原本只存在 daily_stock_snapshot（rs_score 更早已經有了，rs5d、volume_ratio
    # 一直都在但沒有 bucket 化），signal_events 完全沒有記錄，等於資料存在但被鎖住、
    # 每次要分析都得手動 JOIN daily_stock_snapshot。現在補上原始值+bucket，讓
    # single_rs / single_rs5d / single_volume_ratio 以及對應的 cross_* 可以比照
    # 既有五項分數自動產生。
    _ensure_columns(conn, "signal_events", [
        ("rs_score", "REAL"), ("rs_bucket", "TEXT"),
        ("rs5d", "REAL"), ("rs5d_bucket", "TEXT"),
        ("volume_ratio", "REAL"), ("volume_ratio_bucket", "TEXT"),
        # 訊號日收盤價與實際回測進場價分開保存。前者僅供回看訊號，後者一律是隔日開盤。
        ("entry_price", "REAL"), ("entry_date", "TEXT"),
    ])
    _ensure_columns(conn, "summary_stats", [
        ("rs_bucket", "TEXT"), ("rs5d_bucket", "TEXT"), ("volume_ratio_bucket", "TEXT"),
    ])

    # ── 新增欄位：月度策略統計要用的年月 + 熟成狀態 ──
    # 不塞進event_type複合字串，用獨立欄位查詢/排序才不容易出錯。
    _ensure_columns(conn, "summary_stats", [
        ("year_month", "TEXT"), ("is_matured", "INTEGER"),
    ])

    # ── 新增資料表：大盤每日歷史 + 月度趨勢彙總 ──
    # market_data.json 每天只存「當天這一筆」，隔天就被覆蓋，過去完全沒有留下歷史，
    # 導致沒辦法回頭判斷「6月是漲是跌」。fetch_twii_data() 其實每天都有算出
    # ret5d/rsi/price/below_ma20 這些值，只是原本只寫進會被覆蓋的json，從今天起
    # 額外存進這張表，之後月份才有真正的大盤歷史可以拿來標記趨勢。
    # 過去(這張表還沒開始記錄前)的月份沒有資料可回溯，月度報表會顯示「資料不足」，
    # 不會用個股平均漲跌幅這種有偏誤的代理值硬湊，那樣算出來的數字不可靠。
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS market_daily_history (
            trade_date TEXT PRIMARY KEY,
            twii_price REAL,
            twii_ret5d REAL,
            twii_rsi REAL,
            twii_ma20 REAL,
            below_ma20 INTEGER,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS monthly_market_regime (
            year_month TEXT PRIMARY KEY,
            trading_days INTEGER,
            month_return_pct REAL,
            avg_rsi REAL,
            pct_days_below_ma20 REAL,
            regime TEXT,
            updated_at TEXT
        );
        """
    )
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS backfill_failures (
            event_id TEXT PRIMARY KEY,
            ticker TEXT NOT NULL,
            signal_date TEXT NOT NULL,
            reason TEXT NOT NULL,
            recorded_at TEXT NOT NULL
        );
        """
    )

    # ── 新增欄位：超額報酬（扣除大盤同期報酬後的個股報酬）──
    # 個股報酬(return_close_pct)本身混雜了「大盤系統性風險(beta)」跟「選股是否真的挑對」
    # 兩個來源，光看絕對報酬/絕對勝率，遇到大盤急漲急跌時會被regime效果完全淹沒
    # （實測驗證：同一段大盤急拉期間，連最原始的純K線分/純綜合分基準線策略勝率都會
    # 從30幾%暴衝到70幾%，代表策略間的高低差距有很大一部分只是共同暴露在同一個大盤方向上，
    # 不是策略本身的選股能力）。excess_return_pct = 個股報酬 − 大盤(TWII)同期間報酬，
    # 把共同的大盤方向效果先扣掉，才能看出策略真正的相對選股能力（alpha）。
    # twii_entry_price / twii_target_price 保留原始值方便除錯核對，不是必要欄位但成本很低。
    _ensure_columns(conn, "event_outcomes", [
        ("excess_return_pct", "REAL"), ("twii_entry_price", "REAL"), ("twii_target_price", "REAL"),
    ])
    _ensure_columns(conn, "summary_stats", [
        ("avg_excess_return", "REAL"), ("median_excess_return", "REAL"),
        ("excess_win_rate", "REAL"),
    ])

    conn.commit()


def save_market_daily(conn, trade_date, market_info):
    """把當天的大盤快照(fetch_twii_data()的回傳值)存進歷史表，可重複執行(當天資料會被覆蓋更新，
    不會重複累積)。market_info為None時(抓取失敗)略過不寫，避免用空值污染歷史。"""
    if not market_info:
        return
    conn.execute(
        """
        INSERT INTO market_daily_history (trade_date, twii_price, twii_ret5d, twii_rsi, twii_ma20, below_ma20, created_at)
        VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(trade_date) DO UPDATE SET
            twii_price=excluded.twii_price, twii_ret5d=excluded.twii_ret5d,
            twii_rsi=excluded.twii_rsi, twii_ma20=excluded.twii_ma20,
            below_ma20=excluded.below_ma20, created_at=excluded.created_at
        """,
        (
            trade_date, _num(market_info.get("price")), _num(market_info.get("ret5d")),
            _num(market_info.get("rsi")), _num(market_info.get("ma20")),
            1 if market_info.get("below_ma20") else 0,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )
    conn.commit()


def classify_regime(month_return_pct, pct_days_below_ma20):
    """月度大盤趨勢判定：用「當月累計報酬」定方向，「站上/跌破MA20的天數比例」定是否為
    有方向性的趨勢還是來回震盪。門檻是常見的技術分析經驗值(月漲跌5%作為多空分界、
    MA20天數比例30%/70%作為震盪/趨勢分界)，不是從這份資料庫回測驗證出來的最適門檻——
    等累積更多月份資料後，應該回頭比對「不同門檻切出的規則(月)組，策略勝率差異大不大」
    來調整，而不是把這幾個數字當成已驗證的定論。"""
    if month_return_pct is None or pct_days_below_ma20 is None:
        return "NA"
    if month_return_pct >= 5:
        return "UP" if pct_days_below_ma20 <= 30 else "UP_CHOPPY"
    if month_return_pct <= -5:
        return "DOWN" if pct_days_below_ma20 >= 70 else "DOWN_CHOPPY"
    return "RANGE"


def refresh_monthly_market_regime(conn):
    """從 market_daily_history 重建每月的趨勢標記。只有這張表有資料的月份才會產生列，
    在這張表開始記錄之前的月份(6~9月)不會出現，前端要顯示「資料不足」而不是留白或亂猜。"""
    conn.execute("DELETE FROM monthly_market_regime")
    rows = conn.execute(
        """
        SELECT substr(trade_date,1,7) ym, trade_date, twii_price, twii_rsi, below_ma20
        FROM market_daily_history ORDER BY trade_date
        """
    ).fetchall()
    from collections import defaultdict
    by_month = defaultdict(list)
    for ym, trade_date, price, rsi, below in rows:
        by_month[ym].append((trade_date, price, rsi, below))
    updated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for ym, items in by_month.items():
        prices = [p for _, p, _, _ in items if p is not None]
        rsis = [r for _, _, r, _ in items if r is not None]
        belows = [b for _, _, _, b in items if b is not None]
        if len(prices) < 2:
            continue
        month_ret = round((prices[-1] / prices[0] - 1) * 100, 2)
        avg_rsi = round(sum(rsis) / len(rsis), 1) if rsis else None
        pct_below = round(sum(belows) / len(belows) * 100, 1) if belows else None
        regime = classify_regime(month_ret, pct_below)
        conn.execute(
            """
            INSERT INTO monthly_market_regime
                (year_month, trading_days, month_return_pct, avg_rsi, pct_days_below_ma20, regime, updated_at)
            VALUES (?,?,?,?,?,?,?)
            """,
            (ym, len(items), month_ret, avg_rsi, pct_below, regime, updated),
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


def bucket_bb(score):
    """BB分（布林通道分）分級，沿用與突破分/波段分相同的級距，方便熱圖/組合表直接複用。"""
    if score is None:
        return "NA"
    if score >= 70:
        return "A_70UP"
    if score >= 50:
        return "B_50_69"
    if score >= 30:
        return "C_30_49"
    return "D_LT30"


def bucket_rs(score):
    """RS分（橫向排名相對強度）分級。實測發現不是越高越好，85+反而在T+10表現最差
    （已經強過頭、後段風險最高），50~85是甜蜜點，門檻依此設定。"""
    if score is None:
        return "NA"
    if score >= 85:
        return "A_85UP"
    if score >= 70:
        return "B_70_84"
    if score >= 50:
        return "C_50_69"
    return "D_LT50"


def bucket_rs5d(score):
    """RS5日分（短期相對強度加速度）分級。跟RS分邏輯不同：這裡是越高越好，
    20+代表「正在加速轉強」，實測是少數幾個獨立於其他維度、仍有正報酬訊號的欄位。"""
    if score is None:
        return "NA"
    if score >= 20:
        return "A_20UP"
    if score >= 10:
        return "B_10_19"
    if score >= 0:
        return "C_0_9"
    return "D_LT0"


def bucket_volume_ratio(score):
    """量比（今日量/20日均量）分級。實測發現2.5倍以上（真爆量）表現反而最差，
    1~1.5倍溫和放量最穩，跟「爆量=強訊號」的直覺相反，門檻依此設定。"""
    if score is None:
        return "NA"
    if score >= 2.5:
        return "A_2P5UP"
    if score >= 1.5:
        return "B_1P5_2P4"
    if score >= 1.0:
        return "C_1_1P4"
    return "D_LT1"


# 八項分數維度的通用定義，供 summary_stats 的單維 / 交叉維度統計共用
SCORE_DIMS = {
    "kline":         {"bucket_col": "kline_bucket",         "label": "K線分"},
    "composite":     {"bucket_col": "composite_bucket",     "label": "綜合分"},
    "breakout":      {"bucket_col": "breakout_bucket",      "label": "突破分"},
    "swing":         {"bucket_col": "swing_bucket",          "label": "波段分"},
    "bb":            {"bucket_col": "bb_bucket",             "label": "BB分"},
    "rs":            {"bucket_col": "rs_bucket",             "label": "RS分"},
    "rs5d":          {"bucket_col": "rs5d_bucket",           "label": "RS5日分"},
    "volume_ratio":  {"bucket_col": "volume_ratio_bucket",   "label": "量比"},
}
CROSS_PAIRS = [
    ("kline", "composite"), ("kline", "breakout"), ("kline", "swing"), ("kline", "bb"),
    ("composite", "breakout"), ("composite", "swing"), ("composite", "bb"),
    ("breakout", "swing"), ("breakout", "bb"), ("swing", "bb"),
    # 新增：只挑實測有互補資訊量的兩組，不把8個維度做滿28組排列組合
    ("rs5d", "volume_ratio"),   # 加速度 x 量能，對應「怎麼搭配看」的實測發現
    ("rs", "kline"),            # 驗證RS分獨立於K線分之外還有沒有邊際貢獻
]


def classify_signal(kline_score, composite_score, breakout_score=None, swing_score=None, bb_score=None):
    k = kline_score if kline_score is not None else -1
    c = composite_score if composite_score is not None else -1
    b = breakout_score if breakout_score is not None else -1
    sw = swing_score if swing_score is not None else -1
    bbv = bb_score if bb_score is not None else -1
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
    # ── 突破分 / 波段分 ≥75 的獨立觸發條件 ──
    # 即使 K線分／綜合分未達標，只要突破分或波段分夠高，也保留在 signal_events。
    if b >= 75 and sw >= 75:
        return "BREAKOUT_SWING_STRONG", "both_alt"
    if b >= 75:
        return "BREAKOUT_STRONG", "breakout"
    if sw >= 75:
        return "SWING_STRONG", "swing"
    # ── BB分 ≥75 的獨立觸發條件（跟突破分/波段分同層級，抓「時機濾網」訊號）──
    if bbv >= 75 and (b >= 75 or sw >= 75):
        return "BB_CONFIRMED_STRONG", "bb_confirmed"
    if bbv >= 75:
        return "BB_STRONG", "bb"
    return "NEUTRAL", "none"


# ── 策略組合回測用的獨立標籤（不影響上面 classify_signal 的既有分類）──
# 這五個策略彼此不互斥，同一檔股票同一天可以同時符合多個，各自獨立記錄一筆
# signal_events，才能公平比較每個策略各自的 T+1~T+10 勝率，不會互相稀釋樣本。
#   A：突破族 — 已通過 RS≥85 門檻的 VCP 高品質整理／接近突破股，當日出現突破放量才進場
#   B：波段族 — 已通過 RS≥85 門檻、拉回夠深後止跌轉強的股票，當日出現洗盤結束才進場
#   C：純K線分對照組 — 完全不看RS/結構，單純K線分排序，驗證「單一雜訊分數」有沒有用
#   D：純綜合分基準線 — 你原本習慣的做法，用來當A、B有沒有真的比較好的比較基準
#   E：BB時機濾網族 — 不看RS門檻，純粹用BB分本身的三選一setup(下軌反轉/擠壓/突破)
#      當進場條件，驗證「BB分單獨使用」的時機濾網有沒有比D這個基準線更早/更準
STRAT_VCP_OK_STATUS = ("高品質整理", "接近突破", "已突破")
STRAT_BB_OK_SETUP = ("lower_reversal", "squeeze_consolidation", "upper_breakout")


def classify_strategy_events(kline_score, composite_score, breakout_score, swing_score,
                              rs_score, vcp_status, entry_signal,
                              bb_score=None, bb_setup=None, bb_consec_down_days=None,
                              rsi14=None, volume_ratio=None):
    events = []
    if (rs_score is not None and rs_score >= 85
            and breakout_score is not None and breakout_score >= 60
            and vcp_status in STRAT_VCP_OK_STATUS
            and entry_signal == "💥突破放量"):
        events.append("STRAT_A_BREAKOUT")
    if (rs_score is not None and rs_score >= 85
            and swing_score is not None and swing_score >= 60
            and entry_signal == "✅洗盤結束"):
        events.append("STRAT_B_SWING")
    if kline_score is not None and kline_score >= 78:
        events.append("STRAT_C_KLINE")
    if composite_score is not None and composite_score >= 75:
        events.append("STRAT_D_COMPOSITE")
    if (bb_score is not None and bb_score >= 60
            and bb_setup in STRAT_BB_OK_SETUP):
        events.append("STRAT_E_BB")
    # 策略F：均值回歸，故意不要求RS≥85或高分結構條件（那是動能邏輯），
    # 只要求bb_setup是lower_reversal、bb_score本身夠高（代表已經通過
    # bb_gate_multiplier的連跌天數防呆，不然還在崩的股票bb_score會被砍到接近0）、
    # 且連跌天數不到危險門檻。這條線刻意跟A/B用完全相反的篩選邏輯。
    if (bb_score is not None and bb_score >= 40
            and bb_setup == "lower_reversal"
            and (bb_consec_down_days is None or bb_consec_down_days < 4)):
        events.append("STRAT_F_MEANREV")
    # 策略G：RS85+順勢回檔 — 長期強勢股(RS≥85)短線K線降溫(<70)、RSI落在健康區間(45~70)，
    # 抓「強勢股短線拉回、賣壓釋放後」的進場點。樣本仍在累積中，實際勝率請看資料庫統計即時數字，
    # 不在註解寫死具體數字——RS相關欄位2026/08/19才上線，樣本尚未涵蓋完整市場週期，數字會持續變動。
    if (rs_score is not None and rs_score >= 85
            and kline_score is not None and kline_score < 70
            and rsi14 is not None and 45 <= rsi14 <= 70):
        events.append("STRAT_G_RS_PULLBACK")
    # 策略H：RS85+量縮拉回 — 長期強勢股，當日成交量比均量還低(<1.0倍)，
    # 代表賣壓萎縮、短線在健康整理。樣本仍在累積中，實際勝率請看資料庫統計即時數字。
    if (rs_score is not None and rs_score >= 85
            and volume_ratio is not None and volume_ratio < 1.0):
        events.append("STRAT_H_RS_VOLDRY")
    # 策略I：RS50-85動能發動 — 中期相對強度落在甜蜜點(50~85)，K線分已經轉強(≥80)，
    # 跟G/H邏輯相反：抓的是「尚未到極端強勢、但短線動能剛要噴出」的股票。樣本仍在累積中，
    # 實際勝率請看資料庫統計即時數字。
    if (rs_score is not None and 50 <= rs_score < 85
            and kline_score is not None and kline_score >= 80):
        events.append("STRAT_I_RS_MOMENTUM")
    # 策略J：RS70-84+K線降溫 — 中期相對強度偏強(70-84，比G/H的85+門檻略寬)、短線K線分<60
    # (比G的<70更嚴，要求短線降溫更明顯)。2026/09實測初步樣本(n=86~173，僅涵蓋8/19~9/10這段
    # 市場轉強期)T+1~T+7勝率48~61%、報酬轉正，數字亮眼，但跟G/H/I一樣完全沒經過震盪期考驗，
    # 不能排除只是搭上這波多頭順風車——這點務必看資料庫統計的即時數字判斷，不能只看這段註解。
    if (rs_score is not None and 70 <= rs_score < 85
            and kline_score is not None and kline_score < 60):
        events.append("STRAT_J_RS_COOLDOWN")
    return events


def _num(v):
    return None if v is None else float(v)


def _change_pct(price, prev):
    if not price or not prev:
        return None
    return round((price - prev) / prev * 100, 2)


def save_daily_run(results, generated_at=None, db_path=DB_PATH, market_info=None):
    generated_at = generated_at or datetime.now().strftime("%Y-%m-%d %H:%M")
    trade_date = generated_at[:10]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = connect(db_path)
    init_db(conn)
    save_market_daily(conn, trade_date, market_info)

    for s in results:
        ticker = str(s.get("ticker", "")).strip()
        price = s.get("price")
        if not ticker or price is None:
            continue

        kline = s.get("kline_score")
        comp = s.get("composite")
        breakout = s.get("vcp_score")          # 突破分（SEPA+VCP 突破評分）
        swing = s.get("swing_score")            # 波段分（尚未有計分函式時為 None）
        bb = s.get("bb_score")                  # BB分（布林通道分）
        bb_setup = s.get("bb_setup")             # BB setup：lower_reversal/squeeze_consolidation/upper_breakout/neutral
        k_bucket = bucket_kline(kline)
        c_bucket = bucket_composite(comp)
        b_bucket = bucket_breakout(breakout)
        sw_bucket = bucket_swing(swing)
        bb_bucket = bucket_bb(bb)
        rs = s.get("rs_score")            # 橫向排名 RS 分數（突破分/波段分的資格門檻）
        vcp_status = s.get("vcp_status")  # VCP 狀態文字（高品質整理／接近突破…）
        rs5d = s.get("rs5d")               # RS5日分（短期相對強度加速度）
        vol_ratio = s.get("volume_ratio")  # 量比（今日量/20日均量）
        rs_bucket = bucket_rs(rs)
        rs5d_bucket = bucket_rs5d(rs5d)
        vol_ratio_bucket = bucket_volume_ratio(vol_ratio)
        event_type, trigger_source = classify_signal(kline, comp, breakout, swing, bb)
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
                    signal_group, score_version, generated_at, raw_json,
                    rs_score, vcp_status, bb_score, bb_bucket, bb_setup
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                    _num(rs), vcp_status, _num(bb), bb_bucket, bb_setup,
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
                        entry_reference_close, entry_price_mode, status, score_version, created_at,
                        bb_score, bb_bucket, bb_setup,
                        rs_score, rs_bucket, rs5d, rs5d_bucket, volume_ratio, volume_ratio_bucket, rsi14,
                        vcp_status, entry_signal, inst_buy_days
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        event_id, trade_date, ticker, s.get("name"), event_type, trigger_source,
                        _num(kline), _num(comp), k_bucket, c_bucket,
                        _num(breakout), b_bucket, _num(swing), sw_bucket,
                        _num(price), "next_open", "open", SCORE_VERSION, now,
                        _num(bb), bb_bucket, bb_setup,
                        _num(rs), rs_bucket, _num(rs5d), rs5d_bucket, _num(vol_ratio), vol_ratio_bucket,
                        _num(s.get("rsi14")), vcp_status, s.get("entry_signal", ""), int(s.get("inst_buy_days") or 0),
                    ),
                )

            # ── 策略組合回測標籤（獨立於上面的 event_type，彼此不互斥）──
            strat_events = classify_strategy_events(
                kline, comp, breakout, swing, rs, vcp_status, s.get("entry_signal", ""),
                bb, bb_setup, s.get("bb_consec_down_days"),
                rsi14=s.get("rsi14"), volume_ratio=vol_ratio,
            )
            for strat_event_type in strat_events:
                strat_event_id = f"{trade_date}:{ticker}:{strat_event_type}"
                conn.execute(
                    """
                    INSERT OR IGNORE INTO signal_events (
                        event_id, trade_date, ticker, name, event_type, trigger_source,
                        kline_score, composite_score, kline_bucket, composite_bucket,
                        breakout_score, breakout_bucket, swing_score, swing_bucket,
                        entry_reference_close, entry_price_mode, status, score_version, created_at,
                        bb_score, bb_bucket, bb_setup,
                        rs_score, rs_bucket, rs5d, rs5d_bucket, volume_ratio, volume_ratio_bucket, rsi14,
                        vcp_status, entry_signal, inst_buy_days
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        strat_event_id, trade_date, ticker, s.get("name"), strat_event_type, "strategy_combo",
                        _num(kline), _num(comp), k_bucket, c_bucket,
                        _num(breakout), b_bucket, _num(swing), sw_bucket,
                        _num(price), "next_open", "open", SCORE_VERSION, now,
                        _num(bb), bb_bucket, bb_setup,
                        _num(rs), rs_bucket, _num(rs5d), rs5d_bucket, _num(vol_ratio), vol_ratio_bucket,
                        _num(s.get("rsi14")), vcp_status, s.get("entry_signal", ""), int(s.get("inst_buy_days") or 0),
                    ),
                )

            if event_type in ("COMP_HIGH_K_LOW", "COMP_STRONG_K_LOW"):
                watch_id = f"{trade_date}:{ticker}:WATCH"
                conn.execute(
                    """
                    INSERT OR IGNORE INTO watch_transitions (
                        watch_id, watch_date, ticker, name, watch_kline_score,
                        watch_composite_score, watch_breakout_score, watch_swing_score,
                        watch_close, status, created_at, watch_bb_score
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (watch_id, trade_date, ticker, s.get("name"), _num(kline), _num(comp),
                     _num(breakout), _num(swing), _num(price), "open", now, _num(bb)),
                )
        except Exception as e:
            print(f"[WARN] 寫入 {ticker} 失敗，略過：{e}")

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


def update_watch_transitions(conn, trade_date):
    rows = conn.execute(
        """
        SELECT w.*, d.kline_score AS today_kline, d.composite_score AS today_comp,
               d.breakout_score AS today_breakout, d.swing_score AS today_swing,
               d.bb_score AS today_bb, d.bb_setup AS today_bb_setup,
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
                    entry_reference_close, entry_price_mode, status, score_version, created_at,
                    bb_score, bb_bucket, bb_setup
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    event_id, trade_date, r["ticker"], r["name"], event_type, "watch_confirm",
                    r["today_kline"], r["today_comp"], bucket_kline(r["today_kline"]),
                    bucket_composite(r["today_comp"]),
                    r["today_breakout"], bucket_breakout(r["today_breakout"]),
                    r["today_swing"], bucket_swing(r["today_swing"]),
                    r["today_close"], "next_open",
                    "open", SCORE_VERSION, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    r["today_bb"], bucket_bb(r["today_bb"]), r["today_bb_setup"],
                ),
            )
            conn.execute(
                """
                UPDATE watch_transitions
                SET confirm_date=?, confirm_kline_score=?, confirm_composite_score=?,
                    confirm_breakout_score=?, confirm_swing_score=?, confirm_bb_score=?,
                    confirm_close=?, days_to_confirm=?, confirmed=1, confirm_type=?,
                    entry_event_id=?, status='confirmed'
                WHERE watch_id=?
                """,
                (
                    trade_date, r["today_kline"], r["today_comp"],
                    r["today_breakout"], r["today_swing"], r["today_bb"], r["today_close"],
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


_TWII_HIST_CACHE = {}


def _twii_history(start_date, end_date):
    """抓取 ^TWII 大盤歷史，用 (start,end) 當 key 做簡易快取，避免同一次
    save_daily_run/backfill 流程裡對每一筆事件都重複打 yfinance API。
    一次抓完整個回測需要的期間範圍即可，呼叫端只需要傳最寬的區間。"""
    key = (start_date, end_date)
    if key in _TWII_HIST_CACHE:
        return _TWII_HIST_CACHE[key]
    try:
        hist = yf.Ticker("^TWII").history(start=start_date, end=end_date, auto_adjust=False)
        if hist is not None and not hist.empty:
            hist = hist.reset_index()
            hist["date_str"] = hist["Date"].dt.strftime("%Y-%m-%d")
        else:
            hist = None
    except Exception:
        hist = None
    _TWII_HIST_CACHE[key] = hist
    return hist


def _twii_close_on_or_before(twii_hist, date_str):
    """取 twii_hist 中 <= date_str 的最後一筆收盤價，用來對齊個股的進場日/結算日
    （大盤跟個股的交易日應該一致，這裡容錯一下避免因為抓取時間差1天而整筆對不上）。"""
    if twii_hist is None or twii_hist.empty:
        return None
    sub = twii_hist[twii_hist["date_str"] <= date_str]
    if sub.empty:
        return None
    row = sub.iloc[-1]
    return float(row["Close"]) if _valid_price(row["Close"]) else None


def _calc_excess_return(twii_hist, entry_date, target_date, stock_ret_pct):
    """算超額報酬：個股報酬 − 大盤同期間報酬。任一邊大盤資料缺失就回傳 None，
    不用0或個股報酬本身頂替，避免把「沒有大盤資料」誤標成「超額報酬為0」。"""
    if stock_ret_pct is None:
        return None, None, None
    twii_entry = _twii_close_on_or_before(twii_hist, entry_date)
    twii_target = _twii_close_on_or_before(twii_hist, target_date)
    if not _valid_price(twii_entry) or not _valid_price(twii_target):
        return None, twii_entry, twii_target
    twii_ret = (twii_target / twii_entry - 1) * 100
    return round(stock_ret_pct - twii_ret, 2), twii_entry, twii_target


def _history_after_signal(ticker, signal_date, market=None):
    start = (datetime.fromisoformat(signal_date) - timedelta(days=1)).strftime("%Y-%m-%d")
    end = (datetime.fromisoformat(signal_date) + timedelta(days=35)).strftime("%Y-%m-%d")
    for symbol in (_ticker_symbol(ticker, market), f"{ticker}.TW", f"{ticker}.TWO"):
        try:
            # 明確不復權，避免用未復權 Open 搭配復權 Close，造成報酬失真。
            hist = yf.Ticker(symbol).history(start=start, end=end, auto_adjust=False)
            if hist is not None and len(hist) >= 2:
                hist = hist.reset_index()
                hist["date_str"] = hist["Date"].dt.strftime("%Y-%m-%d")
                return hist
        except Exception:
            continue
    return None


def _history_for_range(ticker, market, start_date, end_date):
    """同一標的一次抓完整回補期間；失敗時才切換 TW/TWO 後綴。"""
    symbols = tuple(dict.fromkeys((_ticker_symbol(ticker, market), f"{ticker}.TW", f"{ticker}.TWO")))
    for symbol in symbols:
        try:
            hist = yf.Ticker(symbol).history(
                start=start_date, end=end_date, auto_adjust=False,
            )
            if hist is not None and not hist.empty:
                hist = hist.reset_index()
                hist["date_str"] = hist["Date"].dt.strftime("%Y-%m-%d")
                return hist, None
        except Exception as exc:
            last_error = type(exc).__name__
    return None, locals().get("last_error", "no_history")


def _valid_price(value):
    try:
        return value is not None and math.isfinite(float(value)) and float(value) > 0
    except (TypeError, ValueError):
        return False


def _outcome_rows(event, future, entry, filled_at, twii_hist=None, entry_date=None):
    """隔日開盤進場：future[0] 當日收盤就是 T+1。
    twii_hist 有提供時，額外算 excess_return_pct(超額報酬) = 個股報酬 − 大盤同期報酬，
    用個股實際的進場日(entry_date，next_open那天)對齊大盤進場日的收盤價當基準，
    不是用訊號當天，因為個股報酬本身也是用隔日開盤價當進場基準。"""
    rows = []
    entry_date = entry_date or event["trade_date"]
    for horizon in HORIZONS:
        if len(future) < horizon:
            continue
        window = future.iloc[:horizon]
        target = future.iloc[horizon - 1]
        if not all(_valid_price(target[k]) for k in ("Close", "High", "Low")):
            continue
        target_close = float(target["Close"])
        max_gain = round((float(window["High"].max()) / entry - 1) * 100, 2)
        max_drawdown = round((float(window["Low"].min()) / entry - 1) * 100, 2)
        ret = round((target_close / entry - 1) * 100, 2)
        excess, twii_entry_px, twii_target_px = _calc_excess_return(
            twii_hist, entry_date, target["date_str"], ret
        ) if twii_hist is not None else (None, None, None)
        rows.append((
            event["event_id"], event["ticker"], event["trade_date"], horizon,
            target["date_str"], target_close, float(target["High"]), float(target["Low"]),
            ret, max_gain, max_drawdown, 1 if ret > 0 else 0,
            1 if ret >= 3 else 0, 1 if ret <= -3 else 0, filled_at,
            excess, twii_entry_px, twii_target_px,
        ))
    return rows


OUTCOME_INSERT_SQL = """
    INSERT OR REPLACE INTO event_outcomes (
        event_id, ticker, signal_date, horizon, target_date, target_close,
        target_high, target_low, return_close_pct, max_gain_pct,
        max_drawdown_pct, is_win, is_big_win, is_big_loss, filled_at,
        excess_return_pct, twii_entry_price, twii_target_price
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
"""


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

    # 一次抓涵蓋全部待結算事件的大盤區間，供底下逐筆事件共用，避免每筆事件各打一次API。
    twii_hist = None
    if events:
        earliest = min(e["trade_date"] for e in events)
        start = (datetime.fromisoformat(earliest) - timedelta(days=1)).strftime("%Y-%m-%d")
        end = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        twii_hist = _twii_history(start, end)

    for e in events:
        hist = _history_after_signal(e["ticker"], e["trade_date"], e["market"])
        if hist is None:
            continue
        future = hist[hist["date_str"] > e["trade_date"]].copy()
        if future.empty:
            continue
        # 進場價改成「隔日開盤價」，不再用訊號當天收盤價(entry_reference_close)。
        # 原本用訊號當天收盤價當進場價，等於假設訊號出來當下就能成交，但訊號其實是收盤後
        # 才跑批次算出來的，實際上最快只能在隔天開盤才進場，用收盤價回測會系統性高估績效。
        # entry_reference_close欄位保留供顯示「當時觸發訊號時的價位」參考，不再用於報酬計算。
        if not _valid_price(future.iloc[0]["Open"]):
            continue
        entry = float(future.iloc[0]["Open"])
        entry_date = future.iloc[0]["date_str"]
        conn.execute(
            "UPDATE signal_events SET entry_price=?, entry_date=?, entry_price_mode='next_open' WHERE event_id=?",
            (entry, entry_date, e["event_id"]),
        )
        filled = 0
        for row in _outcome_rows(e, future, entry, filled_at, twii_hist=twii_hist, entry_date=entry_date):
            exists = conn.execute("SELECT 1 FROM event_outcomes WHERE event_id=? AND horizon=?", (e["event_id"], row[3])).fetchone()
            if not exists:
                conn.execute(OUTCOME_INSERT_SQL, row)
                filled += 1
        done = conn.execute(
            "SELECT COUNT(*) AS c FROM event_outcomes WHERE event_id=?",
            (e["event_id"],),
        ).fetchone()["c"]
        if done >= len(HORIZONS):
            conn.execute("UPDATE signal_events SET status='matured' WHERE event_id=?", (e["event_id"],))
        elif done > 0 or filled > 0:
            conn.execute("UPDATE signal_events SET status='partial' WHERE event_id=?", (e["event_id"],))


def backfill_entry_price_to_next_open(conn):
    """
    一次性回補：把現有全部event_outcomes(不論狀態是matured/partial/open)，用「隔日開盤價」
    重新計算進場價與所有報酬指標，取代原本「訊號當天收盤價」的算法。這是應user要求的全面改版
    ——不保留舊算法的對照數字，全部統一用新定義重算，所以是直接覆蓋(INSERT OR REPLACE)而非新增
    欄位。跑完這個函式後，summary_stats/monthly_event_type/yearly_event_type都需要重新refresh，
    因為底層的event_outcomes數字全部變了。

    只需要執行一次(部署這版程式碼後手動呼叫一次)，之後update_event_outcomes()日常運作時
    就會自動用新邏輯(隔日開盤價)處理新產生的事件，不需要重複呼叫這個回補函式。
    """
    events = conn.execute(
        """
        SELECT e.*, d.market
        FROM signal_events e
        LEFT JOIN daily_stock_snapshot d
          ON d.trade_date = e.trade_date AND d.ticker = e.ticker
        ORDER BY e.trade_date
        """
    ).fetchall()
    if not events:
        return {"events": 0, "success": 0, "failed": 0, "backup": None}

    # 先抓完所有資料，確認後才動資料庫；中途斷線不會留下半套新舊混合結果。
    groups = {}
    for event in events:
        groups.setdefault((event["ticker"], event["market"]), []).append(event)
    total_groups = len(groups)
    history_by_group, fetch_failures = {}, {}
    for index, (key, group_events) in enumerate(groups.items(), 1):
        ticker, market = key
        first_date = min(e["trade_date"] for e in group_events)
        start = (datetime.fromisoformat(first_date) - timedelta(days=1)).strftime("%Y-%m-%d")
        end = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        hist, reason = _history_for_range(ticker, market, start, end)
        if hist is None:
            fetch_failures[key] = f"history_unavailable:{reason}"
        else:
            history_by_group[key] = hist
        if index % 50 == 0 or index == total_groups:
            print(f"[回補下載] 股票 {index}/{total_groups}")

    # SQLite 的 backup API 會建立一致性備份；需要還原時直接以此檔案覆蓋資料庫即可。
    conn.commit()
    db_path = next((row[2] for row in conn.execute("PRAGMA database_list") if row[1] == "main"), "")
    backup_path = None
    if db_path:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = f"{db_path}.before_next_open_{stamp}.bak"
        backup_conn = sqlite3.connect(backup_path)
        try:
            conn.backup(backup_conn)
        finally:
            backup_conn.close()

    filled_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    earliest = min(e["trade_date"] for e in events)
    twii_start = (datetime.fromisoformat(earliest) - timedelta(days=1)).strftime("%Y-%m-%d")
    twii_end = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    twii_hist = _twii_history(twii_start, twii_end)

    outcome_rows, event_updates, failures = [], [], []
    for event in events:
        key = (event["ticker"], event["market"])
        hist = history_by_group.get(key)
        reason = fetch_failures.get(key)
        future = hist[hist["date_str"] > event["trade_date"]].copy() if hist is not None else None
        if future is None or future.empty:
            failures.append((event["event_id"], event["ticker"], event["trade_date"], reason or "no_next_trading_day", filled_at))
            event_updates.append(("missing_next_open", None, None, event["event_id"]))
            continue
        if not _valid_price(future.iloc[0]["Open"]):
            failures.append((event["event_id"], event["ticker"], event["trade_date"], "invalid_next_open", filled_at))
            event_updates.append(("missing_next_open", None, None, event["event_id"]))
            continue
        entry = float(future.iloc[0]["Open"])
        entry_date = future.iloc[0]["date_str"]
        rows = _outcome_rows(event, future, entry, filled_at, twii_hist=twii_hist, entry_date=entry_date)
        status = "matured" if len(rows) == len(HORIZONS) else "partial"
        outcome_rows.extend(rows)
        event_updates.append((status, entry, future.iloc[0]["date_str"], event["event_id"]))

    # 唯一寫入階段：先完全清除舊口徑，再一次寫入新口徑，絕不混用收盤與隔日開盤結果。
    with conn:
        conn.execute("DELETE FROM event_outcomes")
        conn.execute("DELETE FROM backfill_failures")
        conn.execute("UPDATE signal_events SET entry_price_mode='next_open', entry_price=NULL, entry_date=NULL, status='open'")
        conn.executemany(OUTCOME_INSERT_SQL, outcome_rows)
        conn.executemany(
            "UPDATE signal_events SET status=?, entry_price=?, entry_date=? WHERE event_id=?",
            event_updates,
        )
        conn.executemany(
            "INSERT INTO backfill_failures (event_id, ticker, signal_date, reason, recorded_at) VALUES (?,?,?,?,?)",
            failures,
        )

    refresh_summary_stats(conn)
    refresh_monthly_strategy_stats(conn)
    refresh_yearly_strategy_stats(conn)
    conn.commit()
    result = {"events": len(events), "success": len(events) - len(failures), "failed": len(failures), "backup": backup_path}
    print(f"[回補完成] 總事件數={result['events']}, 成功={result['success']}, 失敗={result['failed']}")
    print(f"[回補備份] {backup_path or '未建立（記憶體資料庫）'}")
    return result


def backfill_excess_return(conn):
    """
    一次性回補：幫既有 event_outcomes 補上 excess_return_pct(超額報酬，扣除大盤同期報酬)。
    只需要大盤(^TWII)一次歷史資料，不用重抓每檔個股，比 backfill_entry_price_to_next_open
    快很多，可獨立執行。只補目前 excess_return_pct 還是 NULL 的列，可重複執行，
    不會動到已經有值的列(包含之後 update_event_outcomes 正常寫入的新資料)。

    entry_date 用 event_outcomes 對應的 signal_events.entry_date(隔日開盤進場日)當大盤基準，
    沒有 entry_date 的舊資料(理論上不該發生，entry_price_mode都已經是next_open)才退回用
    signal_events.trade_date 當基準，避免漏掉任何一筆。
    """
    rows = conn.execute(
        """
        SELECT o.event_id, o.horizon, o.target_date, o.return_close_pct,
               COALESCE(e.entry_date, e.trade_date) AS entry_date
        FROM event_outcomes o
        JOIN signal_events e ON e.event_id = o.event_id
        WHERE o.excess_return_pct IS NULL AND o.return_close_pct IS NOT NULL
        """
    ).fetchall()
    if not rows:
        return {"updated": 0, "skipped": 0}

    earliest = min(r["entry_date"] for r in rows if r["entry_date"])
    latest = max(r["target_date"] for r in rows if r["target_date"])
    start = (datetime.fromisoformat(earliest) - timedelta(days=1)).strftime("%Y-%m-%d")
    end = (datetime.fromisoformat(latest) + timedelta(days=2)).strftime("%Y-%m-%d")
    twii_hist = _twii_history(start, end)
    if twii_hist is None:
        return {"updated": 0, "skipped": len(rows), "reason": "twii_history_unavailable"}

    updated, skipped = 0, 0
    for r in rows:
        if not r["entry_date"] or not r["target_date"]:
            skipped += 1
            continue
        excess, twii_entry_px, twii_target_px = _calc_excess_return(
            twii_hist, r["entry_date"], r["target_date"], r["return_close_pct"]
        )
        if excess is None:
            skipped += 1
            continue
        conn.execute(
            "UPDATE event_outcomes SET excess_return_pct=?, twii_entry_price=?, twii_target_price=? "
            "WHERE event_id=? AND horizon=?",
            (excess, twii_entry_px, twii_target_px, r["event_id"], r["horizon"]),
        )
        updated += 1
    conn.commit()
    if updated:
        refresh_summary_stats(conn)
        refresh_monthly_strategy_stats(conn)
        refresh_yearly_strategy_stats(conn)
        conn.commit()
    print(f"[超額報酬回補完成] 更新={updated}, 略過(缺大盤或日期資料)={skipped}")
    return {"updated": updated, "skipped": skipped}


def _median(vals):
    vals = sorted(vals)
    if not vals:
        return None
    mid = len(vals) // 2
    if len(vals) % 2:
        return vals[mid]
    return round((vals[mid - 1] + vals[mid]) / 2, 2)


def backfill_rs_fields(conn):
    """一次性回填：daily_stock_snapshot 裡本來就有 rs_score / rs5d / volume_ratio 的原始值
    （rs_score 更早就有，rs5d、volume_ratio 則是從一開始就存在但沒被 signal_events 引用），
    只是 signal_events 是新加的欄位、既有歷史列還是 NULL。用 trade_date+ticker 對回去，
    把既有訊號也一起補上，不用等新資料跑出來才能分析 RS / RS5日 / 量比。
    可重複執行，只補目前還是 NULL 的列，不會覆蓋掉未來新流程自己寫入的值。"""
    rows = conn.execute(
        """
        SELECT e.event_id, d.rs_score, d.rs5d, d.volume_ratio
        FROM signal_events e
        JOIN daily_stock_snapshot d ON d.trade_date = e.trade_date AND d.ticker = e.ticker
        WHERE e.rs_score IS NULL
        """
    ).fetchall()
    updated = 0
    for r in rows:
        rs_b = bucket_rs(r["rs_score"])
        rs5d_b = bucket_rs5d(r["rs5d"])
        vr_b = bucket_volume_ratio(r["volume_ratio"])
        conn.execute(
            """
            UPDATE signal_events
            SET rs_score=?, rs_bucket=?, rs5d=?, rs5d_bucket=?, volume_ratio=?, volume_ratio_bucket=?
            WHERE event_id=?
            """,
            (_num(r["rs_score"]), rs_b, _num(r["rs5d"]), rs5d_b, _num(r["volume_ratio"]), vr_b, r["event_id"]),
        )
        updated += 1
    conn.commit()
    return updated


def refresh_monthly_strategy_stats(conn):
    """
    月度策略勝率彙總：group_name='monthly_event_type'，多一個 year_month 欄位。
    只統計 STRAT_A~I 這幾個「策略組合回測」用的分類(不含ENTRY/BOTH_STRONG等舊版雙分訊號)，
    月度視角本來就是要比較策略優劣，混入其他分類只會讓表格失焦。

    「這個月能不能顯示」的規則：不是看今天日期有沒有過完這個月，是看「這個月最後一筆訊號的
    T+10結果是不是已經存在」——例如9月要等到9/30那天(若為交易日)發出的訊號也跑完10個交易日
    的event_outcomes才算「熟了」，不然9月的T+10欄位會因為月底那幾筆還沒到期而被拖累失真，
    跟你要求的「9/30資料跑完到+10再一起顯示9月」是同一件事。
    """
    conn.execute("DELETE FROM summary_stats WHERE group_name='monthly_event_type'")
    updated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    STRAT_TYPES = (
        "STRAT_A_BREAKOUT", "STRAT_B_SWING", "STRAT_C_KLINE", "STRAT_D_COMPOSITE",
        "STRAT_E_BB", "STRAT_F_MEANREV", "STRAT_G_RS_PULLBACK", "STRAT_H_RS_VOLDRY",
        "STRAT_I_RS_MOMENTUM", "STRAT_J_RS_COOLDOWN",
    )

    # 每個月「最後一筆訊號」的日期，用來判斷該月是否已經熟成
    last_signal_per_month = dict(conn.execute(
        """
        SELECT substr(trade_date,1,7) ym, MAX(trade_date)
        FROM signal_events WHERE event_type IN ({})
        GROUP BY ym
        """.format(",".join("?" * len(STRAT_TYPES))),
        STRAT_TYPES,
    ).fetchall())

    rows = conn.execute(
        """
        SELECT substr(e.trade_date,1,7) ym, e.event_type, o.horizon,
               MAX(o.return_close_pct) return_close_pct
        FROM signal_events e JOIN event_outcomes o ON o.event_id = e.event_id
        WHERE e.event_type IN ({})
        GROUP BY e.trade_date, e.ticker, e.event_type, o.horizon
        """.format(",".join("?" * len(STRAT_TYPES))),
        STRAT_TYPES,
    ).fetchall()

    from collections import defaultdict
    groups = defaultdict(list)
    for ym, et, h, ret in rows:
        groups[(ym, et, h)].append(ret)

    for (ym, et, h), vals in groups.items():
        last_date = last_signal_per_month.get(ym)
        if last_date is None:
            continue
        # 該月「最晚一筆訊號」是否已經有 horizon=10 的結果 → 代表這個月已經熟成，可以顯示
        matured = conn.execute(
            "SELECT COUNT(*) FROM event_outcomes o JOIN signal_events e ON e.event_id=o.event_id "
            "WHERE e.trade_date=? AND o.horizon=10", (last_date,)
        ).fetchone()[0] > 0
        vals = [v for v in vals if v is not None]
        if not vals:
            continue
        wins = [v for v in vals if v > 0]
        stat_key = f"monthly_event_type:{ym}:{et}:T{h}"
        conn.execute(
            """
            INSERT INTO summary_stats (
                stat_key, group_name, event_type, horizon, sample_count, win_rate,
                avg_return, median_return, avg_win, avg_loss, profit_factor,
                max_return, min_return, year_month, is_matured, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                stat_key, "monthly_event_type", et, h,
                len(vals), round(len(wins) / len(vals) * 100, 1),
                round(sum(vals) / len(vals), 2), _median(vals),
                round(sum(wins) / len(wins), 2) if wins else None,
                round(sum(v for v in vals if v <= 0) / max(1, len(vals) - len(wins)), 2) if len(vals) > len(wins) else None,
                None, round(max(vals), 2), round(min(vals), 2),
                ym, 1 if matured else 0, updated,
            ),
        )
    conn.commit()


def refresh_yearly_strategy_stats(conn):
    """
    年度策略勝率彙總：group_name='yearly_event_type'，多一個 year 欄位。
    跟月度不同：月度是「整月熟不熟成」二選一，年度改成「逐筆事件各自看有沒有T+10結果，
    有的就納入」——這樣今年一開始就能持續顯示已經熟成的部分，不用等到12/31才有數字，
    是你要的「當年度直接顯示已有T+10的統計」。也因此年度數字會逐日增加樣本、逐漸逼近
    真正的全年結果，不是一次性定案的數字，每天都可能因為新增熟成事件而微調。
    """
    conn.execute("DELETE FROM summary_stats WHERE group_name='yearly_event_type'")
    updated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    STRAT_TYPES = (
        "STRAT_A_BREAKOUT", "STRAT_B_SWING", "STRAT_C_KLINE", "STRAT_D_COMPOSITE",
        "STRAT_E_BB", "STRAT_F_MEANREV", "STRAT_G_RS_PULLBACK", "STRAT_H_RS_VOLDRY",
        "STRAT_I_RS_MOMENTUM", "STRAT_J_RS_COOLDOWN",
    )
    rows = conn.execute(
        """
        SELECT substr(e.trade_date,1,4) yr, e.event_type, o.horizon,
               MAX(o.return_close_pct) return_close_pct
        FROM signal_events e JOIN event_outcomes o ON o.event_id = e.event_id
        WHERE e.event_type IN ({})
        GROUP BY e.trade_date, e.ticker, e.event_type, o.horizon
        """.format(",".join("?" * len(STRAT_TYPES))),
        STRAT_TYPES,
    ).fetchall()

    from collections import defaultdict
    groups = defaultdict(list)
    for yr, et, h, ret in rows:
        if ret is not None:
            groups[(yr, et, h)].append(ret)

    for (yr, et, h), vals in groups.items():
        wins = [v for v in vals if v > 0]
        stat_key = f"yearly_event_type:{yr}:{et}:T{h}"
        conn.execute(
            """
            INSERT INTO summary_stats (
                stat_key, group_name, event_type, horizon, sample_count, win_rate,
                avg_return, median_return, avg_win, avg_loss, profit_factor,
                max_return, min_return, year_month, is_matured, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                stat_key, "yearly_event_type", et, h,
                len(vals), round(len(wins) / len(vals) * 100, 1),
                round(sum(vals) / len(vals), 2), _median(vals),
                round(sum(wins) / len(wins), 2) if wins else None,
                round(sum(v for v in vals if v <= 0) / max(1, len(vals) - len(wins)), 2) if len(vals) > len(wins) else None,
                None, round(max(vals), 2), round(min(vals), 2),
                yr, 1, updated,  # 年度沒有「未熟成整批隱藏」的概念，is_matured固定存1，前端改用樣本數自然反映成熟度
            ),
        )
    conn.commit()


def refresh_summary_stats(conn):
    """
    重建 summary_stats，產生：
      - event_type            各訊號類型 × horizon
      - single_<dim>          K線分/綜合分/突破分/波段分/BB分 各自的單一分數區間 × horizon
                               （供「各分數區間 T+1~T+10 勝率走勢」五張圖使用）
      - cross_<a>_<b>         五項分數兩兩交叉（共 10 組，來自 CROSS_PAIRS）× horizon
                               （供「分數熱圖」與「十大組合總覽」使用）
    改用 SCORE_DIMS / CROSS_PAIRS 通用產生，之後再加新分數維度只需要改那兩個常數，
    不用再回來改這個函式的分組邏輯。
    """
    conn.execute("DELETE FROM summary_stats")
    updated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    rows_by_event_type = conn.execute(
        """
        SELECT o.horizon, o.return_close_pct, o.max_gain_pct, o.max_drawdown_pct, o.excess_return_pct,
               e.event_type, e.kline_bucket, e.composite_bucket, e.breakout_bucket,
               e.swing_bucket, e.bb_bucket
        FROM event_outcomes o
        JOIN signal_events e ON e.event_id = o.event_id
        """
    ).fetchall()
    rows_unique_signal = conn.execute(
        """
        SELECT o.horizon,
               MAX(o.return_close_pct) AS return_close_pct,
               MAX(o.max_gain_pct) AS max_gain_pct,
               MAX(o.max_drawdown_pct) AS max_drawdown_pct,
               MAX(o.excess_return_pct) AS excess_return_pct,
               MAX(e.kline_bucket) AS kline_bucket, MAX(e.composite_bucket) AS composite_bucket,
               MAX(e.breakout_bucket) AS breakout_bucket, MAX(e.swing_bucket) AS swing_bucket,
               MAX(e.bb_bucket) AS bb_bucket, MAX(e.rs_bucket) AS rs_bucket,
               MAX(e.rs5d_bucket) AS rs5d_bucket, MAX(e.volume_ratio_bucket) AS volume_ratio_bucket
        FROM event_outcomes o
        JOIN signal_events e ON e.event_id = o.event_id
        GROUP BY e.trade_date, e.ticker, o.horizon
        """
    ).fetchall()

    # bucket 欄位在 signal_events 資料表裡的實際 column 名稱（依 SCORE_DIMS 順序）
    ROW_BUCKET_COL = {
        "kline": "kline_bucket", "composite": "composite_bucket",
        "breakout": "breakout_bucket", "swing": "swing_bucket", "bb": "bb_bucket",
        "rs": "rs_bucket", "rs5d": "rs5d_bucket", "volume_ratio": "volume_ratio_bucket",
    }
    DIM_ORDER = ["kline", "composite", "breakout", "swing", "bb", "rs", "rs5d", "volume_ratio"]
    NA8 = {d: "NA" for d in DIM_ORDER}

    groups = {}

    def add(key, r):
        groups.setdefault(key, []).append(r)

    def key_tuple(kb_dict):
        return tuple(kb_dict[d] for d in DIM_ORDER)

    for r in rows_by_event_type:
        h = r["horizon"]

        # event_type 分組（不分維度）
        add(("event_type", r["event_type"]) + key_tuple(NA8) + (h,), r)

    for r in rows_unique_signal:
        h = r["horizon"]
        buckets = {dim: r[ROW_BUCKET_COL[dim]] for dim in DIM_ORDER}

        # single_<dim> 分組
        for dim in DIM_ORDER:
            key_buckets = dict(NA8)
            key_buckets[dim] = buckets[dim]
            add((f"single_{dim}", None) + key_tuple(key_buckets) + (h,), r)

        # cross_<a>_<b> 分組（來自 CROSS_PAIRS）
        for a, b in CROSS_PAIRS:
            key_buckets = dict(NA8)
            key_buckets[a] = buckets[a]
            key_buckets[b] = buckets[b]
            add((f"cross_{a}_{b}", None) + key_tuple(key_buckets) + (h,), r)

    for key, items in groups.items():
        group_name, event_type, kb, cb, brk_b, sw_b, bb_b, rs_b, rs5d_b, vr_b, horizon = key
        vals = [float(x["return_close_pct"]) for x in items if x["return_close_pct"] is not None]
        if not vals:
            continue
        wins = [v for v in vals if v > 0]
        losses = [v for v in vals if v <= 0]
        gross_win = sum(wins)
        gross_loss = abs(sum(losses))
        # 超額報酬（扣除大盤同期報酬）：舊資料還沒回補時 excess_return_pct 會是 None，
        # 這裡只用已經有值的樣本算，欄位本身允許比 sample_count 更少（不強制對齊）。
        excess_vals = [float(x["excess_return_pct"]) for x in items if x["excess_return_pct"] is not None]
        excess_wins = [v for v in excess_vals if v > 0]
        stat_key = f"{group_name}:{event_type}:{kb}:{cb}:{brk_b}:{sw_b}:{bb_b}:{rs_b}:{rs5d_b}:{vr_b}:T{horizon}"
        conn.execute(
            """
            INSERT INTO summary_stats (
                stat_key, group_name, event_type, kline_bucket, composite_bucket,
                breakout_bucket, swing_bucket, bb_bucket,
                rs_bucket, rs5d_bucket, volume_ratio_bucket,
                horizon, sample_count, win_rate, avg_return, median_return,
                avg_win, avg_loss, profit_factor, max_return, min_return,
                avg_max_gain, avg_max_drawdown,
                avg_excess_return, median_excess_return, excess_win_rate, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                stat_key, group_name, event_type, kb, cb, brk_b, sw_b, bb_b,
                rs_b, rs5d_b, vr_b, horizon,
                len(vals), round(len(wins) / len(vals) * 100, 1),
                round(sum(vals) / len(vals), 2), _median(vals),
                round(sum(wins) / len(wins), 2) if wins else None,
                round(sum(losses) / len(losses), 2) if losses else None,
                round(gross_win / gross_loss, 2) if gross_loss else None,
                round(max(vals), 2), round(min(vals), 2),
                round(sum(float(x["max_gain_pct"]) for x in items if x["max_gain_pct"] is not None) / len(items), 2),
                round(sum(float(x["max_drawdown_pct"]) for x in items if x["max_drawdown_pct"] is not None) / len(items), 2),
                round(sum(excess_vals) / len(excess_vals), 2) if excess_vals else None,
                _median(excess_vals) if excess_vals else None,
                round(len(excess_wins) / len(excess_vals) * 100, 1) if excess_vals else None,
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
        SELECT e.trade_date, e.ticker, MAX(e.name) AS name,
               MIN(CASE WHEN e.trigger_source = 'strategy_combo' THEN NULL ELSE e.event_type END) AS event_type,
               (SELECT GROUP_CONCAT(DISTINCT e2.event_type)
                FROM signal_events e2
                WHERE e2.trade_date = e.trade_date AND e2.ticker = e.ticker) AS event_types,
               MAX(e.kline_score) AS kline_score,
               MAX(e.composite_score) AS composite_score,
               MAX(e.breakout_score) AS breakout_score,
               MAX(e.vcp_status) AS vcp_status,
               MAX(e.entry_signal) AS entry_signal,
               MAX(e.inst_buy_days) AS inst_buy_days,
               MAX(e.swing_score) AS swing_score,
               MAX(e.bb_score) AS bb_score,
               MAX(e.bb_setup) AS bb_setup,
               MAX(e.rs_score) AS rs_score,
               MAX(e.rs5d) AS rs5d,
               MAX(e.rsi14) AS rsi14,
               MAX(e.volume_ratio) AS volume_ratio,
               MAX(e.entry_reference_close) AS entry_reference_close,
               MAX(e.entry_price) AS entry_price,
               MAX(e.entry_date) AS entry_date,
               CASE
                 WHEN SUM(CASE WHEN e.status = 'open' THEN 1 ELSE 0 END) > 0 THEN 'open'
                 WHEN SUM(CASE WHEN e.status = 'partial' THEN 1 ELSE 0 END) > 0 THEN 'partial'
                 ELSE MAX(e.status)
               END AS status,
               MAX(CASE WHEN o.horizon=1 THEN o.return_close_pct END) AS t1_return,
               MAX(CASE WHEN o.horizon=3 THEN o.return_close_pct END) AS t3_return,
               MAX(CASE WHEN o.horizon=5 THEN o.return_close_pct END) AS t5_return,
               MAX(CASE WHEN o.horizon=7 THEN o.return_close_pct END) AS t7_return,
               MAX(CASE WHEN o.horizon=10 THEN o.return_close_pct END) AS t10_return,
               MAX(CASE WHEN o.horizon=1 THEN o.excess_return_pct END) AS t1_excess,
               MAX(CASE WHEN o.horizon=3 THEN o.excess_return_pct END) AS t3_excess,
               MAX(CASE WHEN o.horizon=5 THEN o.excess_return_pct END) AS t5_excess,
               MAX(CASE WHEN o.horizon=7 THEN o.excess_return_pct END) AS t7_excess,
               MAX(CASE WHEN o.horizon=10 THEN o.excess_return_pct END) AS t10_excess
        FROM signal_events e
        LEFT JOIN event_outcomes o ON o.event_id=e.event_id
        WHERE e.trade_date >= date('now', '-90 days')
        GROUP BY e.trade_date, e.ticker
        ORDER BY e.trade_date DESC, e.ticker
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
        ("BB分 >= 30", "e.bb_score >= 30"),
        ("BB分 >= 50", "e.bb_score >= 50"),
        ("BB分 >= 70", "e.bb_score >= 70"),
        ("K線 >= 70 且BB分 >= 50", "e.kline_score >= 70 AND e.bb_score >= 50"),
        ("綜合分 >= 75 且BB分 >= 50", "e.composite_score >= 75 AND e.bb_score >= 50"),
        ("突破分 >= 50 且BB分 >= 50", "e.breakout_score >= 50 AND e.bb_score >= 50"),
        ("波段分 >= 50 且BB分 >= 50", "e.swing_score >= 50 AND e.bb_score >= 50"),
        ("BB分下軌反轉(bb_setup) >= 60", "e.bb_score >= 60 AND e.bb_setup = 'lower_reversal'"),
        ("BB分擠壓蓄勢(bb_setup) >= 50", "e.bb_score >= 50 AND e.bb_setup = 'squeeze_consolidation'"),
        ("BB分上軌突破(bb_setup) >= 50", "e.bb_score >= 50 AND e.bb_setup = 'upper_breakout'"),
        ("RS分 50~85(甜蜜點)", "e.rs_score >= 50 AND e.rs_score < 85"),
        ("RS分 >= 85", "e.rs_score >= 85"),
        ("RS5日分 >= 10", "e.rs5d >= 10"),
        ("RS5日分 >= 20(加速轉強)", "e.rs5d >= 20"),
        ("量比 1~1.5倍(溫和放量)", "e.volume_ratio >= 1.0 AND e.volume_ratio < 1.5"),
        ("量比 >= 2.5倍(真爆量)", "e.volume_ratio >= 2.5"),
        ("波段分<30 且BB分<30", "e.swing_score < 30 AND e.bb_score < 30"),
        ("波段分<30 且BB分<30 且K線<78(排除過熱)", "e.swing_score < 30 AND e.bb_score < 30 AND e.kline_score < 78"),
        ("波段分<30 且BB分<30 且K線>=78(過熱對照組)", "e.swing_score < 30 AND e.bb_score < 30 AND e.kline_score >= 78"),
        ("波段分<30 且BB分<30 且RS5日>=20", "e.swing_score < 30 AND e.bb_score < 30 AND e.rs5d >= 20"),
        ("RS5日>=20 且量比1~1.5倍", "e.rs5d >= 20 AND e.volume_ratio >= 1.0 AND e.volume_ratio < 1.5"),
    ]
    for label, where_sql in threshold_defs:
        rows = conn.execute(
            f"""
            SELECT horizon, COUNT(*) AS sample_count,
                   ROUND(AVG(CASE WHEN return_close_pct > 0 THEN 1.0 ELSE 0.0 END) * 100, 1) AS win_rate,
                   ROUND(AVG(return_close_pct), 2) AS avg_return,
                   ROUND(AVG(max_gain_pct), 2) AS avg_max_gain,
                   ROUND(AVG(max_drawdown_pct), 2) AS avg_max_drawdown,
                   ROUND(AVG(excess_return_pct), 2) AS avg_excess_return,
                   ROUND(AVG(CASE WHEN excess_return_pct > 0 THEN 1.0 ELSE 0.0 END) * 100, 1) AS excess_win_rate
            FROM (
                SELECT e.trade_date, e.ticker, o.horizon,
                       MAX(o.return_close_pct) AS return_close_pct,
                       MAX(o.max_gain_pct) AS max_gain_pct,
                       MAX(o.max_drawdown_pct) AS max_drawdown_pct,
                       MAX(o.excess_return_pct) AS excess_return_pct
                FROM event_outcomes o
                JOIN signal_events e ON e.event_id = o.event_id
                WHERE {where_sql}
                GROUP BY e.trade_date, e.ticker, o.horizon
            )
            GROUP BY horizon
            ORDER BY horizon
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
    monthly_regime = [dict(r) for r in conn.execute(
        "SELECT * FROM monthly_market_regime ORDER BY year_month"
    ).fetchall()]
    conn.close()
    return {
        "ready": True,
        "counts": counts,
        "summary": summary,
        "recent": recent,
        "threshold_stats": threshold_stats,
        "watch": watch,
        "monthly_regime": monthly_regime,
    }
