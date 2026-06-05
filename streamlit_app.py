"""
streamlit_app.py
================
台股半導體 Screener — Streamlit 網頁版
- 讀取 GitHub Actions 預先抓好的 data/screener_data.json
- FinMind 三大法人資料：從 Streamlit Secrets 取得 Token，即時查詢並快取 24 小時
- 自動在每日下午5點（台灣時間）由 GitHub Actions 更新資料後重新部署
"""

import streamlit as st
import json
import os
import requests
import pandas as pd
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

from tw_screener_core import (
    calc_composite_tw, detect_patterns_tw,
    yahoo_tw_url, SORT_MODES,
)

# ──────────────────────────────────────────────────────────────
# 頁面設定
# ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="🇹🇼 台股半導體 Screener",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ──────────────────────────────────────────────────────────────
# 自訂 CSS
# ──────────────────────────────────────────────────────────────
st.markdown("""
<style>
  .metric-card {
    background: #1e293b;
    border: 1px solid #334155;
    border-radius: 10px;
    padding: 14px 20px;
    text-align: center;
  }
  .metric-label { color: #94a3b8; font-size: 12px; margin-bottom: 4px; }
  .metric-value { color: #e2e8f0; font-size: 22px; font-weight: 700; }
  .pos { color: #ef4444; }
  .neg { color: #22c55e; }

  /* 調整 Streamlit dataframe 字型 */
  .stDataFrame { font-size: 13px; }

  /* 隱藏 Streamlit 預設 footer */
  footer { visibility: hidden; }
</style>
""", unsafe_allow_html=True)


# ──────────────────────────────────────────────────────────────
# FinMind 資料（用 Streamlit Secrets 的 Token）
# ──────────────────────────────────────────────────────────────
def _get_finmind_token() -> str:
    """從 Streamlit Secrets 安全讀取 FinMind Token"""
    try:
        return st.secrets["FINMIND_TOKEN"]
    except Exception:
        return ""


@st.cache_data(ttl=86400, show_spinner=False)   # 快取 24 小時
def fetch_finmind_inst_cached(stock_id: str, token: str, days: int = 10):
    """
    呼叫 FinMind API 取得三大法人（外資+投信）連買/連賣天數
    回傳 int：正 = 連買天數，負 = 連賣天數
    """
    if not token:
        return 0

    end_date   = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days + 10)).strftime("%Y-%m-%d")

    url = "https://api.finmindtrade.com/api/v4/data"
    params = {
        "dataset":   "TaiwanStockInstitutionalInvestorsBuySell",
        "data_id":   stock_id,
        "start_date": start_date,
        "end_date":   end_date,
        "token":      token,
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        if data.get("status") != 200:
            return 0

        records = data.get("data", [])
        if not records:
            return 0

        daily = {}
        for rec in records:
            date = rec["date"]
            name = rec.get("name", "")
            net  = rec.get("buy", 0) - rec.get("sell", 0)
            if name in ("外資", "外資自營商", "Foreign_Investor",
                        "投信", "Investment_Trust"):
                daily.setdefault(date, 0)
                daily[date] += net

        sorted_dates = sorted(daily.keys(), reverse=True)[:days]
        if not sorted_dates:
            return 0

        latest_sign = 1 if daily[sorted_dates[0]] >= 0 else -1
        count = 0
        for date in sorted_dates:
            sign = 1 if daily[date] >= 0 else -1
            if sign == latest_sign:
                count += 1
            else:
                break
        return latest_sign * count

    except Exception:
        return 0


def fetch_all_finmind(tickers: list[str], token: str, max_workers: int = 8):
    """
    並行抓取所有股票的 FinMind 法人資料
    回傳 dict: {stock_id: inst_days}
    """
    result = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(fetch_finmind_inst_cached, sid, token): sid for sid in tickers}
        for fut in as_completed(futures):
            sid = futures[fut]
            try:
                result[sid] = fut.result()
            except Exception:
                result[sid] = 0
    return result


# ──────────────────────────────────────────────────────────────
# 讀取資料
# ──────────────────────────────────────────────────────────────
@st.cache_data(ttl=300, show_spinner=False)   # 快取 5 分鐘（會被 GitHub Actions 部署刷新）
def load_screener_data():
    path = os.path.join("data", "screener_data.json")
    if not os.path.exists(path):
        return None, None
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return raw.get("stocks", []), raw.get("generated_at", "")


@st.cache_data(ttl=300, show_spinner=False)
def load_market_data():
    path = os.path.join("data", "market_data.json")
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ──────────────────────────────────────────────────────────────
# 工具函式
# ──────────────────────────────────────────────────────────────
def fmt_val(v, decimals=1, suffix="", na="N/A"):
    if v is None:
        return na
    try:
        return f"{float(v):.{decimals}f}{suffix}"
    except Exception:
        return na


def kline_color(v):
    if v is None: return "gray"
    if v >= 75:   return "#ef4444"
    if v >= 62:   return "#f97316"
    if v >= 50:   return "#eab308"
    return "#22c55e"


def inst_display(v):
    v = v or 0
    if v >= 3:   return f"🔴 +{v}天"
    if v >= 1:   return f"🟠 +{v}天"
    if v <= -3:  return f"🟢 -{abs(v)}天"
    if v < 0:    return f"🔵 -{abs(v)}天"
    return "—"


# ──────────────────────────────────────────────────────────────
# 主頁面
# ──────────────────────────────────────────────────────────────
def main():
    st.title("🇹🇼 台股半導體 Screener")

    # ── 讀取資料 ──
    stocks, generated_at = load_screener_data()
    market_info = load_market_data()

    if stocks is None:
        st.error("⚠️ 找不到 `data/screener_data.json`，請確認 GitHub Actions 已執行過至少一次。")
        st.info("你可以在 GitHub → Actions → Daily Data Update → Run workflow 手動觸發一次。")
        st.stop()

    # ── 大盤概況 ──
    st.subheader("📊 大盤概況 — 台灣加權指數")
    if market_info:
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.metric("加權指數", f"{market_info.get('price', 0):,.0f}")
        with c2:
            ret5d = market_info.get("ret5d", 0) or 0
            st.metric("近5日漲跌", f"{ret5d:+.1f}%",
                      delta_color="inverse" if ret5d < 0 else "normal")
        with c3:
            rsi = market_info.get("rsi")
            st.metric("RSI(14)", f"{rsi:.0f}" if rsi else "N/A")
        with c4:
            bm = market_info.get("below_ma20")
            st.metric("MA20 狀態",
                      "⚠️ 跌破 MA20" if bm else "✅ 站上 MA20",
                      delta_color="off")
    else:
        st.info("大盤資料暫無法取得")

    if generated_at:
        st.caption(f"📅 資料更新時間：{generated_at}（每日下午5點自動更新）")

    st.divider()

    # ── FinMind 法人資料 ──
    token = _get_finmind_token()

    if token:
        with st.spinner("📡 正在從 FinMind 抓取三大法人資料（已快取24小時）..."):
            tickers = [s["ticker"] for s in stocks]
            inst_map = fetch_all_finmind(tickers, token)
    else:
        inst_map = {}
        st.warning("⚠️ 未設定 FinMind Token（請至 Streamlit Cloud → Settings → Secrets 新增 `FINMIND_TOKEN`），法人欄位將顯示為 0。")

    # ── 更新法人資料 & 重算綜合評分 ──
    for s in stocks:
        s["inst_buy_days"] = inst_map.get(s["ticker"], s.get("inst_buy_days", 0))
        s["composite"]     = calc_composite_tw(s)
        s["patterns"]      = detect_patterns_tw(s)

    # ── 側邊欄篩選 ──
    with st.sidebar:
        st.header("🔍 篩選條件")

        sort_choice = st.selectbox(
            "排序方式",
            options=list(SORT_MODES.keys()),
            format_func=lambda k: SORT_MODES[k][2],
            index=0,
        )

        kline_min = st.slider("K線技術分 ≥", 0, 100, 0, 5)
        comp_min  = st.slider("綜合評分 ≥",  0, 100, 0, 5)
        rsi_max   = st.slider("RSI(14) ≤",   0, 100, 100, 5)

        vol_min   = st.number_input("爆量倍數 ≥", min_value=0.0, max_value=10.0,
                                    value=0.0, step=0.5)

        inst_min  = st.number_input("法人連買天數 ≥", min_value=-10, max_value=10,
                                    value=0, step=1)

        signals   = st.multiselect(
            "今日訊號",
            options=["💥突破放量", "🚀主力進場", "✅洗盤結束", "📉量縮整理"],
            default=[],
        )

        search_q  = st.text_input("🔍 搜尋代號 / 名稱")

        st.divider()
        top_n = st.number_input("顯示前 N 檔（0 = 全部）", 0, 500, 0, 10)

    # ── 套用篩選 ──
    sort_key, ascending, _ = SORT_MODES[sort_choice]

    filtered = [
        s for s in stocks
        if (s.get("kline_score") is not None or s.get("volume_ratio") is not None)
        and (s.get("kline_score") or 0) >= kline_min
        and (s.get("composite")  or 0) >= comp_min
        and (s.get("rsi14")      or 100) <= rsi_max
        and (s.get("volume_ratio") or 0) >= vol_min
        and (s.get("inst_buy_days") or 0) >= inst_min
        and (not signals or s.get("entry_signal") in signals)
        and (not search_q or
             search_q.lower() in s["ticker"].lower() or
             search_q.lower() in (s.get("name") or "").lower())
    ]

    filtered.sort(
        key=lambda x: (
            x.get(sort_key) is None,
            x.get(sort_key) if ascending else -(x.get(sort_key) or 0)
        )
    )

    if top_n > 0:
        filtered = filtered[:top_n]

    st.subheader(f"📋 篩選結果：{len(filtered)} 檔")

    # ── 建立 DataFrame ──
    rows = []
    for rank, s in enumerate(filtered, 1):
        pat_str = " ".join(p[0] for p in s.get("patterns", []))
        upside  = fmt_val(s.get("upside_pct"), 1, "%") if s.get("upside_pct") is not None else "N/A"
        rs5d    = fmt_val(s.get("rs5d"), 1, "%") if s.get("rs5d") is not None else "N/A"
        url     = yahoo_tw_url(s["ticker"], s.get("market", "TW"))
        rows.append({
            "#":       rank,
            "代號":    s["ticker"],
            "名稱":    s.get("name", ""),
            "現價":    s.get("price"),
            "K線分":   s.get("kline_score"),
            "策略":    s.get("kline_strat", "-") or "-",
            "RSI":     s.get("rsi14"),
            "爆量":    s.get("volume_ratio"),
            "距MA20%": s.get("price_vs_ma20_pct"),
            "距MA60%": s.get("price_vs_ma60_pct"),
            "法人":    inst_display(s.get("inst_buy_days")),
            "今日訊號": s.get("entry_signal") or "-",
            "RS(5日)%": s.get("rs5d"),
            "型態":    pat_str or "-",
            "綜合分":  s.get("composite"),
            "目標空間": upside,
            "Yahoo":   f"[🔗 查看]({url})",
        })

    df = pd.DataFrame(rows)

    # ── 以 st.dataframe 顯示（支援排序/複製）──
    if df.empty:
        st.info("沒有符合篩選條件的股票。")
    else:
        # 數值欄位加色條
        styled = st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "#":        st.column_config.NumberColumn("#", width=40),
                "代號":     st.column_config.TextColumn("代號", width=70),
                "名稱":     st.column_config.TextColumn("名稱", width=90),
                "現價":     st.column_config.NumberColumn("現價", format="%.1f", width=70),
                "K線分":    st.column_config.ProgressColumn(
                                "K線分", min_value=0, max_value=100,
                                format="%d", width=100),
                "策略":     st.column_config.TextColumn("策略", width=80),
                "RSI":      st.column_config.NumberColumn("RSI", format="%.1f", width=60),
                "爆量":     st.column_config.NumberColumn("爆量倍數", format="%.2f x", width=80),
                "距MA20%":  st.column_config.NumberColumn("距MA20%", format="%.1f%%", width=80),
                "距MA60%":  st.column_config.NumberColumn("距MA60%", format="%.1f%%", width=80),
                "法人":     st.column_config.TextColumn("法人連買", width=80),
                "今日訊號": st.column_config.TextColumn("今日訊號", width=100),
                "RS(5日)%": st.column_config.NumberColumn("RS(5日)%", format="%.1f%%", width=80),
                "型態":     st.column_config.TextColumn("型態", width=100),
                "綜合分":   st.column_config.ProgressColumn(
                                "綜合分", min_value=0, max_value=100,
                                format="%g", width=100),
                "目標空間": st.column_config.TextColumn("目標空間", width=75),
                "Yahoo":    st.column_config.LinkColumn("Yahoo", width=65, display_text="🔗"),
            },
            height=min(40 + len(df) * 36, 700),
        )

    # ── 說明卡片 ──
    st.divider()
    with st.expander("📖 指標說明", expanded=False):
        st.markdown("""
| 指標 | 說明 |
|------|------|
| **K線分 ≥ 75** | 技術面強勢；62~74 偏多；50~61 中性；< 50 偏弱 |
| **策略標籤** | breakout 突破 / pullback 回踩 / reversal 反轉 / balanced 均衡 |
| **💥突破放量** | 爆量創5日新高 — 最強進場訊號 |
| **🚀主力進場** | 大量紅K收高位 |
| **✅洗盤結束** | 量縮後放量紅K |
| **📉量縮整理** | 量縮蓄勢中 |
| **法人連買（紅）** | 外資+投信連續買超天數（FinMind 資料） |
| **法人連賣（綠）** | 外資+投信連續賣超天數 |
| **RS(5日)%** | 個股5日漲跌 - 加權指數5日漲跌 = 超額報酬 |
| **A法人爆量** | 法人連買≥3天 且 爆量≥1.5倍 |
| **B回踩MA60** | 股價在MA60附近、RSI 32~55、MA20上升中 |
| **C底背離** | RSI 底背離（技術反轉訊號） |

> ⚠️ 本工具僅供技術面參考，不構成投資建議。股票投資有風險，請自行判斷。
        """)

    # ── 下載 CSV ──
    st.divider()
    if not df.empty:
        csv_data = df.drop(columns=["Yahoo"]).to_csv(index=False, encoding="utf-8-sig")
        st.download_button(
            label="💾 下載 CSV",
            data=csv_data.encode("utf-8-sig"),
            file_name=f"tw_screener_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
            mime="text/csv",
        )


if __name__ == "__main__":
    main()
