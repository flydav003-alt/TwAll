"""
streamlit_app.py
================
台股半導體 Screener — Streamlit 網頁版（重設計版）
- 讀取 GitHub Actions 預先抓好的 data/screener_data.json
- FinMind 三大法人資料：從 Streamlit Secrets 取得 Token，快取 2 小時
- 漲跌% = (現價 - 昨收) / 昨收，由 JSON 存的 prev_close 計算
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
    page_title="台股半導體 Screener",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ──────────────────────────────────────────────────────────────
# 全域 CSS
# ──────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=Noto+Sans+TC:wght@400;500;700&display=swap');

:root {
  --bg:        #090e1a;
  --bg2:       #0d1525;
  --bg3:       #111d30;
  --border:    #1e2d45;
  --text:      #c8d8f0;
  --text-dim:  #4a6080;
  --text-mid:  #7a9bbf;
  --accent:    #1e6fff;
  --red:       #ff3d5a;
  --green:     #00c97a;
  --orange:    #ff8c00;
  --yellow:    #f5c518;
  --mono:      'IBM Plex Mono', monospace;
  --sans:      'Noto Sans TC', sans-serif;
}

html, body, [data-testid="stAppViewContainer"] {
  background: var(--bg) !important;
  color: var(--text);
  font-family: var(--sans);
}

[data-testid="stHeader"],
[data-testid="stToolbar"],
footer { display: none !important; }

[data-testid="stSidebar"] { background: var(--bg2) !important; }

/* 隱藏 streamlit 預設間距 */
.block-container { padding-top: 1.5rem !important; padding-bottom: 2rem !important; }

/* ── Header ── */
.screener-header {
  display: flex;
  align-items: baseline;
  gap: 16px;
  margin-bottom: 4px;
}
.screener-title {
  font-family: var(--mono);
  font-size: 22px;
  font-weight: 600;
  color: #e8f0ff;
  letter-spacing: 0.04em;
}
.screener-meta {
  font-family: var(--mono);
  font-size: 11px;
  color: var(--text-dim);
  letter-spacing: 0.06em;
}

/* ── 大盤 bar ── */
.market-bar {
  display: flex;
  align-items: center;
  gap: 28px;
  background: var(--bg2);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 20px;
  margin-bottom: 16px;
  font-family: var(--mono);
  font-size: 13px;
}
.market-bar .label { color: var(--text-dim); font-size: 11px; margin-right: 6px; }
.market-bar .val   { color: var(--text); font-weight: 600; }
.market-bar .pos   { color: var(--red); }
.market-bar .neg   { color: var(--green); }
.market-bar .neutral { color: var(--text-mid); }
.market-sep { color: var(--border); }

/* ── 篩選列 ── */
.filter-row {
  display: flex;
  gap: 12px;
  align-items: center;
  flex-wrap: wrap;
  margin-bottom: 14px;
}

/* ── 結果統計 ── */
.result-stat {
  font-family: var(--mono);
  font-size: 11px;
  color: var(--text-dim);
  margin-bottom: 10px;
  letter-spacing: 0.05em;
}
.result-stat span { color: var(--accent); }

/* ── 主表格 ── */
.screener-table {
  width: 100%;
  border-collapse: collapse;
  font-family: var(--mono);
  font-size: 12.5px;
}
.screener-table thead th {
  background: var(--bg2);
  color: var(--text-dim);
  font-size: 10.5px;
  font-weight: 500;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  padding: 8px 10px;
  text-align: left;
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
  position: sticky;
  top: 0;
  z-index: 10;
}
.screener-table tbody tr {
  border-bottom: 1px solid rgba(30,45,69,0.6);
  transition: background 0.1s;
}
.screener-table tbody tr:hover { background: var(--bg3); }
.screener-table tbody td {
  padding: 7px 10px;
  vertical-align: middle;
  white-space: nowrap;
  color: var(--text);
}

/* ── 欄位樣式 ── */
.ticker-link {
  color: #5ba3ff;
  text-decoration: none;
  font-weight: 600;
  font-size: 13px;
  letter-spacing: 0.05em;
}
.ticker-link:hover { color: #a0c4ff; text-decoration: underline; }

.stock-name {
  color: var(--text-mid);
  font-family: var(--sans);
  font-size: 12px;
}

.price-val {
  font-weight: 600;
  font-size: 13px;
  color: var(--text);
}

.chg-pos { color: var(--red); font-weight: 600; }
.chg-neg { color: var(--green); font-weight: 600; }
.chg-zero { color: var(--text-dim); }

/* K線 / 綜合 mini bar */
.score-cell {
  display: flex;
  align-items: center;
  gap: 7px;
}
.score-bar-wrap {
  width: 52px;
  height: 5px;
  background: rgba(255,255,255,0.07);
  border-radius: 3px;
  overflow: hidden;
  flex-shrink: 0;
}
.score-bar-fill {
  height: 100%;
  border-radius: 3px;
}
.score-num {
  font-size: 12px;
  font-weight: 600;
  min-width: 22px;
  text-align: right;
}

/* 爆量 */
.vol-badge {
  display: inline-block;
  padding: 1px 7px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 600;
}
.vol-hot2  { background: rgba(255,61,90,0.2);  color: #ff3d5a; border: 1px solid rgba(255,61,90,0.35); }
.vol-hot15 { background: rgba(255,140,0,0.18); color: #ff8c00; border: 1px solid rgba(255,140,0,0.35); }
.vol-norm  { color: var(--text-dim); }

/* RSI */
.rsi-high   { color: var(--red); }
.rsi-mid    { color: var(--yellow); }
.rsi-low    { color: var(--green); }
.rsi-normal { color: var(--text-mid); }

/* RS5日 */
.rs-pos { color: var(--red); }
.rs-neg { color: var(--green); }
.rs-zero { color: var(--text-dim); }

/* 法人 */
.inst-strong-buy  { color: var(--red); font-weight: 700; }
.inst-buy         { color: #ff8060; }
.inst-strong-sell { color: var(--green); font-weight: 700; }
.inst-sell        { color: #60c090; }
.inst-zero        { color: var(--text-dim); }

/* 今日訊號 */
.signal-badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 11px;
  font-family: var(--sans);
  white-space: nowrap;
}
.sig-breakthrough { background: rgba(255,61,90,0.15);  color: #ff6070; border: 1px solid rgba(255,61,90,0.3); }
.sig-main         { background: rgba(255,140,0,0.15);  color: #ffaa30; border: 1px solid rgba(255,140,0,0.3); }
.sig-wash         { background: rgba(0,201,122,0.12);  color: #30d898; border: 1px solid rgba(0,201,122,0.3); }
.sig-shrink       { background: rgba(74,96,128,0.2);   color: #7090b0; border: 1px solid rgba(74,96,128,0.4); }

/* 型態標籤 */
.pat-tag {
  display: inline-block;
  padding: 1px 7px;
  border-radius: 3px;
  font-size: 10.5px;
  font-family: var(--sans);
  margin-right: 3px;
}
.pat-a { background: rgba(255,140,0,0.18); color: #ffaa30; border: 1px solid rgba(255,140,0,0.3); }
.pat-b { background: rgba(91,163,255,0.15); color: #80b8ff; border: 1px solid rgba(91,163,255,0.3); }
.pat-c { background: rgba(160,100,255,0.15); color: #c090ff; border: 1px solid rgba(160,100,255,0.3); }

/* 表格容器 */
.table-wrap {
  overflow-x: auto;
  overflow-y: auto;
  max-height: 72vh;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg);
}

/* Streamlit widget 覆蓋 */
[data-baseweb="input"] input,
[data-baseweb="select"] * {
  background: var(--bg2) !important;
  color: var(--text) !important;
  border-color: var(--border) !important;
  font-family: var(--mono) !important;
}
label[data-testid="stWidgetLabel"] p { color: var(--text-dim) !important; font-size: 11px !important; }
</style>
""", unsafe_allow_html=True)


# ──────────────────────────────────────────────────────────────
# FinMind（快取 2 小時）
# ──────────────────────────────────────────────────────────────
def _get_finmind_token() -> str:
    try:
        return st.secrets["FINMIND_TOKEN"]
    except Exception:
        return ""


@st.cache_data(ttl=7200, show_spinner=False)   # ← 改為 2 小時快取
def fetch_finmind_inst_cached(stock_id: str, token: str, days: int = 10):
    if not token:
        return 0
    end_date   = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days + 10)).strftime("%Y-%m-%d")
    url = "https://api.finmindtrade.com/api/v4/data"
    params = dict(
        dataset="TaiwanStockInstitutionalInvestorsBuySell",
        data_id=stock_id, start_date=start_date,
        end_date=end_date, token=token,
    )
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
            if name in ("外資", "外資自營商", "Foreign_Investor", "投信", "Investment_Trust"):
                daily.setdefault(date, 0)
                daily[date] += net
        sorted_dates = sorted(daily.keys(), reverse=True)[:days]
        if not sorted_dates:
            return 0
        latest_sign = 1 if daily[sorted_dates[0]] >= 0 else -1
        count = 0
        for date in sorted_dates:
            if (1 if daily[date] >= 0 else -1) == latest_sign:
                count += 1
            else:
                break
        return latest_sign * count
    except Exception:
        return 0


def fetch_all_finmind(tickers: list, token: str, max_workers: int = 8):
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
@st.cache_data(ttl=300, show_spinner=False)
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
# 渲染函式
# ──────────────────────────────────────────────────────────────
def _kline_color(v):
    if v is None: return "#4a6080"
    if v >= 75:   return "#ff3d5a"
    if v >= 62:   return "#ff8c00"
    if v >= 50:   return "#f5c518"
    return "#22c55e"

def _comp_color(v):
    if v is None: return "#4a6080"
    if v >= 60:   return "#ff3d5a"
    if v >= 40:   return "#ff8c00"
    if v >= 25:   return "#f5c518"
    return "#4a6080"

def render_score_bar(val, color_fn):
    if val is None:
        return '<span style="color:#4a6080">—</span>'
    pct = min(max(val, 0), 100)
    color = color_fn(val)
    return f'''<div class="score-cell">
  <div class="score-bar-wrap">
    <div class="score-bar-fill" style="width:{pct}%;background:{color}"></div>
  </div>
  <span class="score-num" style="color:{color}">{int(val)}</span>
</div>'''

def render_vol(v):
    if v is None:
        return '<span class="vol-norm">—</span>'
    if v >= 2.0:
        return f'<span class="vol-badge vol-hot2">{v:.1f}x</span>'
    if v >= 1.5:
        return f'<span class="vol-badge vol-hot15">{v:.1f}x</span>'
    return f'<span class="vol-norm">{v:.1f}x</span>'

def render_rsi(v):
    if v is None:
        return '<span class="rsi-normal">—</span>'
    if v >= 70:
        return f'<span class="rsi-high">{v:.0f}</span>'
    if v >= 50:
        return f'<span class="rsi-mid">{v:.0f}</span>'
    if v <= 30:
        return f'<span class="rsi-low">{v:.0f}</span>'
    return f'<span class="rsi-normal">{v:.0f}</span>'

def render_rs(v):
    if v is None:
        return '<span class="rs-zero">—</span>'
    if v > 0:
        return f'<span class="rs-pos">+{v:.1f}%</span>'
    if v < 0:
        return f'<span class="rs-neg">{v:.1f}%</span>'
    return f'<span class="rs-zero">0.0%</span>'

def render_inst(v):
    v = v or 0
    if v >= 3:
        return f'<span class="inst-strong-buy">+{v}</span>'
    if v >= 1:
        return f'<span class="inst-buy">+{v}</span>'
    if v <= -3:
        return f'<span class="inst-strong-sell">{v}</span>'
    if v < 0:
        return f'<span class="inst-sell">{v}</span>'
    return '<span class="inst-zero">0</span>'

def render_signal(s):
    if not s:
        return ''
    cls_map = {
        "💥突破放量": "sig-breakthrough",
        "🚀主力進場": "sig-main",
        "✅洗盤結束": "sig-wash",
        "📉量縮整理": "sig-shrink",
    }
    cls = cls_map.get(s, "sig-shrink")
    return f'<span class="signal-badge {cls}">{s}</span>'

def render_patterns(pats):
    if not pats:
        return ''
    html = ''
    for name, cls in pats:
        html += f'<span class="pat-tag {cls}">{name}</span>'
    return html

def render_chg(price, prev_close):
    if price is None or prev_close is None or prev_close == 0:
        return '<span class="chg-zero">—</span>'
    pct = (price - prev_close) / prev_close * 100
    if pct > 0:
        return f'<span class="chg-pos">+{pct:.2f}%</span>'
    if pct < 0:
        return f'<span class="chg-neg">{pct:.2f}%</span>'
    return f'<span class="chg-zero">0.00%</span>'


# ──────────────────────────────────────────────────────────────
# 主程式
# ──────────────────────────────────────────────────────────────
def main():
    stocks, generated_at = load_screener_data()
    market_info = load_market_data()

    if stocks is None:
        st.error("⚠️ 找不到 `data/screener_data.json`，請確認 GitHub Actions 已執行過至少一次。")
        st.stop()

    # ── Header ──
    count_total = len(stocks)
    st.markdown(f"""
    <div class="screener-header">
      <span class="screener-title">🇹🇼 台股半導體 SCREENER</span>
      <span class="screener-meta">更新：{generated_at or 'N/A'} &nbsp;|&nbsp; 共 {count_total} 檔</span>
    </div>
    """, unsafe_allow_html=True)

    # ── 大盤 bar ──
    if market_info:
        price = market_info.get('price', 0) or 0
        ret5d = market_info.get('ret5d', 0) or 0
        rsi   = market_info.get('rsi')
        ma20_ok = not market_info.get('below_ma20', False)
        ret5d_cls  = "pos" if ret5d > 0 else ("neg" if ret5d < 0 else "neutral")
        rsi_str    = f"{rsi:.0f}" if rsi else "N/A"
        ma20_str   = "站上 MA20 ✓" if ma20_ok else "跌破 MA20 ✗"
        ma20_cls   = "pos" if ma20_ok else "neg"
        st.markdown(f"""
        <div class="market-bar">
          <div><span class="label">加權指數</span><span class="val">{price:,.0f}</span></div>
          <span class="market-sep">|</span>
          <div><span class="label">近5日</span><span class="{ret5d_cls}">{ret5d:+.1f}%</span></div>
          <span class="market-sep">|</span>
          <div><span class="label">RSI</span><span class="neutral">{rsi_str}</span></div>
          <span class="market-sep">|</span>
          <div><span class="{ma20_cls}">{ma20_str}</span></div>
        </div>
        """, unsafe_allow_html=True)

    # ── FinMind ──
    token = _get_finmind_token()
    if token:
        with st.spinner("📡 抓取法人資料（2小時快取）..."):
            tickers  = [s["ticker"] for s in stocks]
            inst_map = fetch_all_finmind(tickers, token)
    else:
        inst_map = {}

    # 更新法人 & 重算
    for s in stocks:
        s["inst_buy_days"] = inst_map.get(s["ticker"], s.get("inst_buy_days", 0))
        s["composite"]     = calc_composite_tw(s)
        s["patterns"]      = detect_patterns_tw(s)

    # ── 篩選列（上方一排）──
    col1, col2, col3, _pad = st.columns([2, 1.2, 1.2, 3])
    with col1:
        search_q  = st.text_input("🔍 代號 / 名稱", placeholder="輸入代號或名稱…", label_visibility="collapsed")
    with col2:
        kline_min = st.number_input("K線分 ≥", min_value=0, max_value=100, value=0, step=5)
    with col3:
        comp_min  = st.number_input("綜合分 ≥", min_value=0, max_value=100, value=0, step=5)

    # ── 套用篩選 ──
    filtered = [
        s for s in stocks
        if (s.get("kline_score") is not None or s.get("volume_ratio") is not None)
        and (s.get("kline_score") or 0) >= kline_min
        and (s.get("composite")  or 0) >= comp_min
        and (not search_q or
             search_q.lower() in s["ticker"].lower() or
             search_q.lower() in (s.get("name") or "").lower())
    ]

    # 預設按 kline_score 降序
    filtered.sort(key=lambda x: (x.get("kline_score") is None, -(x.get("kline_score") or 0)))

    st.markdown(f'<div class="result-stat">篩選結果 <span>{len(filtered)}</span> / {count_total} 檔</div>',
                unsafe_allow_html=True)

    # ── 建構 HTML 表格 ──
    rows_html = ""
    for s in filtered:
        ticker  = s["ticker"]
        name    = s.get("name", ticker)
        market  = s.get("market", "TW")
        url     = yahoo_tw_url(ticker, market)
        kline_url = f"https://flydav003-alt.github.io/k-line/?stock={ticker}"

        price      = s.get("price")
        prev_close = s.get("prev_close")          # fetch_data.py 需存此欄（見下方說明）
        price_str  = f"{price:.1f}" if price is not None else "—"

        chg_html   = render_chg(price, prev_close)
        kline_html = f'<a href="{kline_url}" target="_blank" style="text-decoration:none">{render_score_bar(s.get("kline_score"), _kline_color)}</a>'
        comp_html  = render_score_bar(s.get("composite"), _comp_color)
        vol_html   = render_vol(s.get("volume_ratio"))
        rsi_html   = render_rsi(s.get("rsi14"))
        rs_html    = render_rs(s.get("rs5d"))
        inst_html  = render_inst(s.get("inst_buy_days"))
        sig_html   = render_signal(s.get("entry_signal", ""))
        pat_html   = render_patterns(s.get("patterns", []))

        rows_html += f"""
<tr>
  <td><a class="ticker-link" href="{url}" target="_blank">{ticker}</a></td>
  <td><span class="stock-name">{name}</span></td>
  <td><span class="price-val">{price_str}</span></td>
  <td>{chg_html}</td>
  <td>{kline_html}</td>
  <td>{comp_html}</td>
  <td>{vol_html}</td>
  <td>{rsi_html}</td>
  <td>{rs_html}</td>
  <td>{inst_html}</td>
  <td>{sig_html}</td>
  <td>{pat_html}</td>
</tr>"""

    table_html = f"""
<div class="table-wrap">
<table class="screener-table">
<thead>
<tr>
  <th>代號</th>
  <th>名稱</th>
  <th>現價</th>
  <th>漲跌%</th>
  <th>K線分 ↗</th>
  <th>綜合分</th>
  <th>爆量</th>
  <th>RSI</th>
  <th>RS(5日)</th>
  <th>法人</th>
  <th>今日訊號</th>
  <th>型態</th>
</tr>
</thead>
<tbody>
{rows_html if rows_html else '<tr><td colspan="12" style="text-align:center;padding:40px;color:#4a6080">沒有符合篩選條件的股票</td></tr>'}
</tbody>
</table>
</div>"""

    st.markdown(table_html, unsafe_allow_html=True)

    # ── 說明 & 下載 ──
    st.markdown("<br>", unsafe_allow_html=True)
    with st.expander("📖 指標說明", expanded=False):
        st.markdown("""
| 指標 | 說明 |
|------|------|
| **K線分 ≥ 75** | 技術面強勢；62~74 偏多；50~61 中性；< 50 偏弱 |
| **💥突破放量** | 爆量創5日新高 — 最強進場訊號 |
| **🚀主力進場** | 大量紅K收高位 |
| **✅洗盤結束** | 量縮後放量紅K |
| **📉量縮整理** | 量縮蓄勢中 |
| **爆量** | 1.5x 橘色警示；2.0x+ 紅色強訊號 |
| **RSI** | ≥70 紅（過熱）；50-70 黃（偏強）；≤30 綠（超賣） |
| **法人** | +3以上深紅強買；+1至+2橘買；-1至-2淺綠賣；-3以下深綠強賣 |
| **RS(5日)** | 個股5日漲跌 - 加權指數5日漲跌（超額報酬）|
| **A法人爆量** | 法人連買≥3天 且 爆量≥1.5倍 |
| **B回踩MA60** | 股價在MA60附近、RSI 32~55、MA20上升中 |
| **C底背離** | RSI 底背離（技術反轉訊號）|
> ⚠️ 本工具僅供技術面參考，不構成投資建議。
        """)

    # ── 漲跌% 欄位說明 ──
    with st.expander("⚠️ 關於漲跌% 欄位", expanded=False):
        st.markdown("""
漲跌% 需要 `prev_close`（昨日收盤價）欄位。
請在 `fetch_data.py` 的 `fetch_tw_ticker` 函式中加入以下程式碼：

```python
# 在 OHLCV 區塊，hist 取得後加入：
if len(closes) >= 2:
    base["prev_close"] = round(float(closes.iloc[-2]), 2)
```

加入後重新執行 GitHub Actions，漲跌% 即可正確顯示。
        """)


if __name__ == "__main__":
    main()
