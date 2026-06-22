"""
stats_db_v2.py - 升級版
========================
新增功能：
1. 所有 tab 分頁都加上突破分和波段分的篩選條件
2. T+5勝率對比加上四個維度（K線分、突破分、波段分、綜合分）
3. 門檻分析加上四個分數區間
4. 勝率和報酬熱圖加上四向交叉對比
5. 各分數區間的T+1~T+10勝率走勢
6. 黃金出場邏輯加上四個維度篩選
"""

import json
import os
import sqlite3
from datetime import datetime, timedelta
from collections import defaultdict
import numpy as np
import pandas as pd

DB_PATH = os.path.join("data", "stats.db")
HORIZONS = (1, 3, 5, 7, 10)

# ══════════════════════════════════════════════════════════════════════════════
# 分數區間定義
# ══════════════════════════════════════════════════════════════════════════════

SCORE_BUCKETS = {
    "极优": (80, 100),
    "优秀": (65, 79),
    "良好": (50, 64),
    "及格": (40, 49),
    "不达标": (0, 39),
}

SCORE_BUCKET_ORDER = ["极优", "优秀", "良好", "及格", "不达标"]


def get_bucket_for_score(score):
    """根據分數判斷所屬區間"""
    if score is None:
        return None
    for bucket_name, (min_val, max_val) in SCORE_BUCKETS.items():
        if min_val <= score <= max_val:
            return bucket_name
    return None


# ══════════════════════════════════════════════════════════════════════════════
# 推薦清單的拉棒條篩選
# ══════════════════════════════════════════════════════════════════════════════

def generate_recommended_with_filters(conn, limit=50):
    """
    生成每日推薦清單，帶上拉棒條篩選
    支持按以下維度篩選：
    - K線分區間
    - 突破分區間
    - 波段分區間
    - 綜合分區間
    """
    query = """
    SELECT
        ticker, name, market,
        close_price, change_pct,
        kline_score, kline_bucket,
        composite_score, composite_bucket,
        breakout_score, breakout_bucket,
        swing_score, swing_bucket,
        composite_v2, composite_v2_bucket,
        rsi14, volume_ratio,
        inst_buy_days, entry_signal,
        patterns, generated_at
    FROM daily_stock_snapshot
    WHERE trade_date = (
        SELECT MAX(trade_date) FROM daily_stock_snapshot
    )
    ORDER BY
        CASE 
            WHEN composite_v2 IS NOT NULL THEN composite_v2
            ELSE composite_score
        END DESC,
        kline_score DESC
    LIMIT ?
    """
    
    cursor = conn.execute(query, (limit,))
    rows = cursor.fetchall()
    
    return [dict(row) for row in rows]


# ══════════════════════════════════════════════════════════════════════════════
# 近期訊號統計（含拉棒條篩選）
# ══════════════════════════════════════════════════════════════════════════════

def analyze_recent_signals_with_filters(conn, days=20):
    """
    分析最近N天內的訊號，按四個維度分組統計
    
    返回結構：
    {
        "by_signal_type": {
            "💥突破放量": [signals...],
            "🚀主力進場": [signals...],
            ...
        },
        "by_kline_bucket": {...},
        "by_breakout_bucket": {...},
        "by_swing_bucket": {...},
        "by_composite_bucket": {...},
    }
    """
    cutoff_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    
    query = """
    SELECT
        ticker, name, trade_date,
        kline_score, kline_bucket,
        breakout_score, breakout_bucket,
        swing_score, swing_bucket,
        composite_v2, composite_v2_bucket,
        entry_signal, signal_rank,
        patterns
    FROM daily_stock_snapshot
    WHERE trade_date >= ?
    AND entry_signal IS NOT NULL AND entry_signal != ''
    ORDER BY trade_date DESC
    """
    
    cursor = conn.execute(query, (cutoff_date,))
    rows = cursor.fetchall()
    
    result = {
        "by_signal_type": defaultdict(list),
        "by_kline_bucket": defaultdict(list),
        "by_breakout_bucket": defaultdict(list),
        "by_swing_bucket": defaultdict(list),
        "by_composite_bucket": defaultdict(list),
    }
    
    for row in rows:
        data = dict(row)
        signal_type = data["entry_signal"]
        result["by_signal_type"][signal_type].append(data)
        
        if data.get("kline_bucket"):
            result["by_kline_bucket"][data["kline_bucket"]].append(data)
        if data.get("breakout_bucket"):
            result["by_breakout_bucket"][data["breakout_bucket"]].append(data)
        if data.get("swing_bucket"):
            result["by_swing_bucket"][data["swing_bucket"]].append(data)
        if data.get("composite_v2_bucket"):
            result["by_composite_bucket"][data["composite_v2_bucket"]].append(data)
    
    return result


# ══════════════════════════════════════════════════════════════════════════════
# 各訊號 T+5 勝率對比（加上四個維度）
# ══════════════════════════════════════════════════════════════════════════════

def signal_winrate_comparison_4d(conn, horizon=5):
    """
    比較各訊號類型的 T+horizon 勝率，按四個維度分別統計
    
    返回結構：
    {
        "by_signal_and_kline": {...},
        "by_signal_and_breakout": {...},
        "by_signal_and_swing": {...},
        "by_signal_and_composite": {...},
    }
    """
    query = """
    SELECT DISTINCT
        se.event_type,
        dss.kline_bucket,
        dss.breakout_bucket,
        dss.swing_bucket,
        dss.composite_v2_bucket,
        eo.is_win,
        eo.return_close_pct
    FROM signal_events se
    JOIN daily_stock_snapshot dss ON se.ticker = dss.ticker AND se.trade_date = dss.trade_date
    LEFT JOIN event_outcomes eo ON se.event_id = eo.event_id AND eo.horizon = ?
    WHERE eo.target_date IS NOT NULL
    """
    
    cursor = conn.execute(query, (horizon,))
    rows = cursor.fetchall()
    
    result = {
        "by_signal_and_kline": {},
        "by_signal_and_breakout": {},
        "by_signal_and_swing": {},
        "by_signal_and_composite": {},
    }
    
    # 按訊號 + K線分組
    signal_kline_stats = defaultdict(lambda: {"win": 0, "total": 0, "returns": []})
    signal_breakout_stats = defaultdict(lambda: {"win": 0, "total": 0, "returns": []})
    signal_swing_stats = defaultdict(lambda: {"win": 0, "total": 0, "returns": []})
    signal_composite_stats = defaultdict(lambda: {"win": 0, "total": 0, "returns": []})
    
    for row in rows:
        data = dict(row)
        event_type = data["event_type"]
        is_win = data["is_win"]
        ret = data["return_close_pct"] or 0
        
        # K線維度
        kline_bucket = data.get("kline_bucket")
        if kline_bucket:
            key = f"{event_type}|{kline_bucket}"
            signal_kline_stats[key]["total"] += 1
            if is_win:
                signal_kline_stats[key]["win"] += 1
            signal_kline_stats[key]["returns"].append(ret)
        
        # 突破分維度
        breakout_bucket = data.get("breakout_bucket")
        if breakout_bucket:
            key = f"{event_type}|{breakout_bucket}"
            signal_breakout_stats[key]["total"] += 1
            if is_win:
                signal_breakout_stats[key]["win"] += 1
            signal_breakout_stats[key]["returns"].append(ret)
        
        # 波段分維度
        swing_bucket = data.get("swing_bucket")
        if swing_bucket:
            key = f"{event_type}|{swing_bucket}"
            signal_swing_stats[key]["total"] += 1
            if is_win:
                signal_swing_stats[key]["win"] += 1
            signal_swing_stats[key]["returns"].append(ret)
        
        # 綜合分維度
        composite_bucket = data.get("composite_v2_bucket")
        if composite_bucket:
            key = f"{event_type}|{composite_bucket}"
            signal_composite_stats[key]["total"] += 1
            if is_win:
                signal_composite_stats[key]["win"] += 1
            signal_composite_stats[key]["returns"].append(ret)
    
    # 計算勝率和平均報酬
    for key, stats in signal_kline_stats.items():
        if stats["total"] > 0:
            wr = round(100 * stats["win"] / stats["total"], 1)
            avg_ret = round(np.mean(stats["returns"]), 2)
            result["by_signal_and_kline"][key] = {"winrate": wr, "avg_return": avg_ret, "count": stats["total"]}
    
    for key, stats in signal_breakout_stats.items():
        if stats["total"] > 0:
            wr = round(100 * stats["win"] / stats["total"], 1)
            avg_ret = round(np.mean(stats["returns"]), 2)
            result["by_signal_and_breakout"][key] = {"winrate": wr, "avg_return": avg_ret, "count": stats["total"]}
    
    for key, stats in signal_swing_stats.items():
        if stats["total"] > 0:
            wr = round(100 * stats["win"] / stats["total"], 1)
            avg_ret = round(np.mean(stats["returns"]), 2)
            result["by_signal_and_swing"][key] = {"winrate": wr, "avg_return": avg_ret, "count": stats["total"]}
    
    for key, stats in signal_composite_stats.items():
        if stats["total"] > 0:
            wr = round(100 * stats["win"] / stats["total"], 1)
            avg_ret = round(np.mean(stats["returns"]), 2)
            result["by_signal_and_composite"][key] = {"winrate": wr, "avg_return": avg_ret, "count": stats["total"]}
    
    return result


# ══════════════════════════════════════════════════════════════════════════════
# 門檻分析（四維度）
# ══════════════════════════════════════════════════════════════════════════════

def threshold_analysis_4d(conn, horizon=5):
    """
    分別針對 K線分、突破分、波段分、綜合分 進行門檻分析
    
    返回：按區間的勝率統計
    """
    query = """
    SELECT
        dss.kline_bucket, dss.kline_score,
        dss.breakout_bucket, dss.breakout_score,
        dss.swing_bucket, dss.swing_score,
        dss.composite_v2_bucket, dss.composite_v2,
        eo.is_win, eo.return_close_pct
    FROM daily_stock_snapshot dss
    LEFT JOIN signal_events se ON dss.ticker = se.ticker AND dss.trade_date = se.trade_date
    LEFT JOIN event_outcomes eo ON se.event_id = eo.event_id AND eo.horizon = ?
    WHERE eo.target_date IS NOT NULL
    """
    
    cursor = conn.execute(query, (horizon,))
    rows = cursor.fetchall()
    
    result = {
        "kline_by_bucket": {},
        "breakout_by_bucket": {},
        "swing_by_bucket": {},
        "composite_by_bucket": {},
    }
    
    kline_stats = defaultdict(lambda: {"win": 0, "total": 0, "returns": []})
    breakout_stats = defaultdict(lambda: {"win": 0, "total": 0, "returns": []})
    swing_stats = defaultdict(lambda: {"win": 0, "total": 0, "returns": []})
    composite_stats = defaultdict(lambda: {"win": 0, "total": 0, "returns": []})
    
    for row in rows:
        data = dict(row)
        is_win = data.get("is_win", 0)
        ret = data.get("return_close_pct", 0) or 0
        
        # K線分統計
        if data.get("kline_bucket"):
            bucket = data["kline_bucket"]
            kline_stats[bucket]["total"] += 1
            if is_win:
                kline_stats[bucket]["win"] += 1
            kline_stats[bucket]["returns"].append(ret)
        
        # 突破分統計
        if data.get("breakout_bucket"):
            bucket = data["breakout_bucket"]
            breakout_stats[bucket]["total"] += 1
            if is_win:
                breakout_stats[bucket]["win"] += 1
            breakout_stats[bucket]["returns"].append(ret)
        
        # 波段分統計
        if data.get("swing_bucket"):
            bucket = data["swing_bucket"]
            swing_stats[bucket]["total"] += 1
            if is_win:
                swing_stats[bucket]["win"] += 1
            swing_stats[bucket]["returns"].append(ret)
        
        # 綜合分統計
        if data.get("composite_v2_bucket"):
            bucket = data["composite_v2_bucket"]
            composite_stats[bucket]["total"] += 1
            if is_win:
                composite_stats[bucket]["win"] += 1
            composite_stats[bucket]["returns"].append(ret)
    
    # 計算勝率
    for bucket in SCORE_BUCKET_ORDER:
        if kline_stats[bucket]["total"] > 0:
            stats = kline_stats[bucket]
            result["kline_by_bucket"][bucket] = {
                "winrate": round(100 * stats["win"] / stats["total"], 1),
                "avg_return": round(np.mean(stats["returns"]), 2),
                "count": stats["total"]
            }
        
        if breakout_stats[bucket]["total"] > 0:
            stats = breakout_stats[bucket]
            result["breakout_by_bucket"][bucket] = {
                "winrate": round(100 * stats["win"] / stats["total"], 1),
                "avg_return": round(np.mean(stats["returns"]), 2),
                "count": stats["total"]
            }
        
        if swing_stats[bucket]["total"] > 0:
            stats = swing_stats[bucket]
            result["swing_by_bucket"][bucket] = {
                "winrate": round(100 * stats["win"] / stats["total"], 1),
                "avg_return": round(np.mean(stats["returns"]), 2),
                "count": stats["total"]
            }
        
        if composite_stats[bucket]["total"] > 0:
            stats = composite_stats[bucket]
            result["composite_by_bucket"][bucket] = {
                "winrate": round(100 * stats["win"] / stats["total"], 1),
                "avg_return": round(np.mean(stats["returns"]), 2),
                "count": stats["total"]
            }
    
    return result


# ══════════════════════════════════════════════════════════════════════════════
# 勝率和報酬熱圖（四向交叉）
# ══════════════════════════════════════════════════════════════════════════════

def generate_heatmap_4d_cross(conn, horizon=5):
    """
    生成四向交叉對比的勝率和報酬熱圖
    
    交叉維度：
    1. K線分 × 突破分
    2. K線分 × 波段分
    3. 突破分 × 波段分
    4. 綜合分 × 訊號類型
    """
    query = """
    SELECT
        dss.kline_bucket,
        dss.breakout_bucket,
        dss.swing_bucket,
        dss.composite_v2_bucket,
        se.event_type,
        eo.is_win,
        eo.return_close_pct
    FROM daily_stock_snapshot dss
    LEFT JOIN signal_events se ON dss.ticker = se.ticker AND dss.trade_date = se.trade_date
    LEFT JOIN event_outcomes eo ON se.event_id = eo.event_id AND eo.horizon = ?
    WHERE eo.target_date IS NOT NULL
    """
    
    cursor = conn.execute(query, (horizon,))
    rows = cursor.fetchall()
    
    # 四種交叉統計
    kline_x_breakout = defaultdict(lambda: {"win": 0, "total": 0, "returns": []})
    kline_x_swing = defaultdict(lambda: {"win": 0, "total": 0, "returns": []})
    breakout_x_swing = defaultdict(lambda: {"win": 0, "total": 0, "returns": []})
    composite_x_signal = defaultdict(lambda: {"win": 0, "total": 0, "returns": []})
    
    for row in rows:
        data = dict(row)
        is_win = data.get("is_win", 0)
        ret = data.get("return_close_pct", 0) or 0
        
        kb = data.get("kline_bucket")
        bb = data.get("breakout_bucket")
        sb = data.get("swing_bucket")
        cb = data.get("composite_v2_bucket")
        et = data.get("event_type")
        
        # 交叉1: K線 × 突破
        if kb and bb:
            key = f"{kb}|{bb}"
            kline_x_breakout[key]["total"] += 1
            if is_win:
                kline_x_breakout[key]["win"] += 1
            kline_x_breakout[key]["returns"].append(ret)
        
        # 交叉2: K線 × 波段
        if kb and sb:
            key = f"{kb}|{sb}"
            kline_x_swing[key]["total"] += 1
            if is_win:
                kline_x_swing[key]["win"] += 1
            kline_x_swing[key]["returns"].append(ret)
        
        # 交叉3: 突破 × 波段
        if bb and sb:
            key = f"{bb}|{sb}"
            breakout_x_swing[key]["total"] += 1
            if is_win:
                breakout_x_swing[key]["win"] += 1
            breakout_x_swing[key]["returns"].append(ret)
        
        # 交叉4: 綜合 × 訊號
        if cb and et:
            key = f"{cb}|{et}"
            composite_x_signal[key]["total"] += 1
            if is_win:
                composite_x_signal[key]["win"] += 1
            composite_x_signal[key]["returns"].append(ret)
    
    # 轉換為 Heatmap 格式
    heatmaps = {
        "kline_x_breakout": {},
        "kline_x_swing": {},
        "breakout_x_swing": {},
        "composite_x_signal": {},
    }
    
    for key, stats in kline_x_breakout.items():
        if stats["total"] >= 5:  # 樣本數最少5筆
            heatmaps["kline_x_breakout"][key] = {
                "winrate": round(100 * stats["win"] / stats["total"], 1),
                "avg_return": round(np.mean(stats["returns"]), 2),
                "count": stats["total"]
            }
    
    for key, stats in kline_x_swing.items():
        if stats["total"] >= 5:
            heatmaps["kline_x_swing"][key] = {
                "winrate": round(100 * stats["win"] / stats["total"], 1),
                "avg_return": round(np.mean(stats["returns"]), 2),
                "count": stats["total"]
            }
    
    for key, stats in breakout_x_swing.items():
        if stats["total"] >= 5:
            heatmaps["breakout_x_swing"][key] = {
                "winrate": round(100 * stats["win"] / stats["total"], 1),
                "avg_return": round(np.mean(stats["returns"]), 2),
                "count": stats["total"]
            }
    
    for key, stats in composite_x_signal.items():
        if stats["total"] >= 5:
            heatmaps["composite_x_signal"][key] = {
                "winrate": round(100 * stats["win"] / stats["total"], 1),
                "avg_return": round(np.mean(stats["returns"]), 2),
                "count": stats["total"]
            }
    
    return heatmaps


# ══════════════════════════════════════════════════════════════════════════════
# 各分數區間的 T+1~T+10 勝率走勢
# ══════════════════════════════════════════════════════════════════════════════

def winrate_trend_by_score_dimension(conn):
    """
    生成四個維度分別的 T+1 至 T+10 勝率走勢曲線
    
    返回結構：
    {
        "kline_trends": {
            "极优": [wr_t1, wr_t2, ..., wr_t10],
            ...
        },
        "breakout_trends": {...},
        "swing_trends": {...},
        "composite_trends": {...},
    }
    """
    result = {
        "kline_trends": {},
        "breakout_trends": {},
        "swing_trends": {},
        "composite_trends": {},
    }
    
    for dimension, target_dict in [
        ("kline", result["kline_trends"]),
        ("breakout", result["breakout_trends"]),
        ("swing", result["swing_trends"]),
        ("composite", result["composite_trends"]),
    ]:
        # 選擇適當的 bucket 欄位名
        bucket_col = f"{dimension}_bucket"
        
        # 為每個區間計算 T+1~T+10 的勝率
        for bucket_name in SCORE_BUCKET_ORDER:
            trends = []
            
            for horizon in HORIZONS:
                query = f"""
                SELECT eo.is_win
                FROM daily_stock_snapshot dss
                LEFT JOIN signal_events se ON dss.ticker = se.ticker AND dss.trade_date = se.trade_date
                LEFT JOIN event_outcomes eo ON se.event_id = eo.event_id AND eo.horizon = ?
                WHERE dss.{bucket_col} = ? AND eo.target_date IS NOT NULL
                """
                
                cursor = conn.execute(query, (horizon, bucket_name))
                rows = cursor.fetchall()
                
                if len(rows) > 0:
                    wins = sum(1 for r in rows if r[0])
                    wr = round(100 * wins / len(rows), 1)
                else:
                    wr = None
                
                trends.append(wr)
            
            target_dict[bucket_name] = trends
    
    return result


# ══════════════════════════════════════════════════════════════════════════════
# 黃金出場（加上四維度篩選）
# ══════════════════════════════════════════════════════════════════════════════

def golden_exit_analysis_4d(conn, min_days_held=3, max_days_held=15):
    """
    分析黃金出場時機，按四個維度分別統計最佳出場日期
    
    邏輯：在 min_days_held 到 max_days_held 之間找到最高報酬率
    """
    query = """
    SELECT
        se.ticker, se.trade_date,
        dss.kline_bucket, dss.breakout_bucket, dss.swing_bucket, dss.composite_v2_bucket,
        eo.horizon, eo.return_close_pct, eo.max_gain_pct
    FROM signal_events se
    JOIN daily_stock_snapshot dss ON se.ticker = dss.ticker AND se.trade_date = dss.trade_date
    JOIN event_outcomes eo ON se.event_id = eo.event_id
    WHERE eo.horizon BETWEEN ? AND ?
    """
    
    cursor = conn.execute(query, (min_days_held, max_days_held))
    rows = cursor.fetchall()
    
    result = {
        "kline_exit_days": {},
        "breakout_exit_days": {},
        "swing_exit_days": {},
        "composite_exit_days": {},
    }
    
    kline_exits = defaultdict(lambda: {"best_day": None, "max_ret": -999})
    breakout_exits = defaultdict(lambda: {"best_day": None, "max_ret": -999})
    swing_exits = defaultdict(lambda: {"best_day": None, "max_ret": -999})
    composite_exits = defaultdict(lambda: {"best_day": None, "max_ret": -999})
    
    for row in rows:
        data = dict(row)
        horizon = data["horizon"]
        ret = data["max_gain_pct"] or 0
        
        kb = data.get("kline_bucket")
        bb = data.get("breakout_bucket")
        sb = data.get("swing_bucket")
        cb = data.get("composite_v2_bucket")
        
        if kb:
            if ret > kline_exits[kb]["max_ret"]:
                kline_exits[kb]["max_ret"] = ret
                kline_exits[kb]["best_day"] = horizon
        
        if bb:
            if ret > breakout_exits[bb]["max_ret"]:
                breakout_exits[bb]["max_ret"] = ret
                breakout_exits[bb]["best_day"] = horizon
        
        if sb:
            if ret > swing_exits[sb]["max_ret"]:
                swing_exits[sb]["max_ret"] = ret
                swing_exits[sb]["best_day"] = horizon
        
        if cb:
            if ret > composite_exits[cb]["max_ret"]:
                composite_exits[cb]["max_ret"] = ret
                composite_exits[cb]["best_day"] = horizon
    
    # 轉換輸出格式
    for bucket in SCORE_BUCKET_ORDER:
        if kline_exits[bucket]["best_day"] is not None:
            result["kline_exit_days"][bucket] = kline_exits[bucket]
        if breakout_exits[bucket]["best_day"] is not None:
            result["breakout_exit_days"][bucket] = breakout_exits[bucket]
        if swing_exits[bucket]["best_day"] is not None:
            result["swing_exit_days"][bucket] = swing_exits[bucket]
        if composite_exits[bucket]["best_day"] is not None:
            result["composite_exit_days"][bucket] = composite_exits[bucket]
    
    return result
