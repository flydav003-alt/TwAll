"""
streamlit_app.py  ── 台股半導體 Screener（深海藍黑重設計版）
- 無內嵌捲軸，全部交給瀏覽器
- 欄位標頭可點擊排序
- 篩選列：搜尋框 + K線分滑桿 + 綜合分滑桿，單橫排
- 法人快取 2 小時
"""

import streamlit as st
import json, os, requests
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from tw_screener_core import calc_composite_tw, detect_patterns_tw, yahoo_tw_url

# ── 頁面設定 ─────────────────────────────────────────────────
st.set_page_config(
    page_title="台股半導體 Screener",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── CSS ───────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Noto+Sans+TC:wght@400;500;700&display=swap');

:root {
  --bg:         #050d1a;
  --bg-card:    #071326;
  --bg-row-alt: #081628;
  --bg-hover:   #0c1f3a;
  --border:     rgba(30,80,140,0.35);
  --border-hd:  rgba(40,100,180,0.5);
  --text:       #ccdcf4;
  --text-dim:   #3d5a7a;
  --text-mid:   #6a8fb0;
  --text-hd:    #4a7aaa;
  --accent:     #2a7fff;
  --red:        #f23a54;
  --red-soft:   #c0304a;
  --green:      #00bf72;
  --green-soft: #008f55;
  --orange:     #f08020;
  --yellow:     #e8c040;
  --purple:     #a070f0;
  --sans:       'Inter', 'Noto Sans TC', sans-serif;
}

/* ── 全域 ── */
html, body,
[data-testid="stAppViewContainer"],
[data-testid="stApp"] {
  background: var(--bg) !important;
  color: var(--text);
  font-family: var(--sans);
}
[data-testid="stHeader"],
[data-testid="stToolbar"],
[data-testid="stDecoration"],
footer { display: none !important; }
[data-testid="stSidebar"] { background: var(--bg-card) !important; }

/* 去掉 Streamlit 預設 padding，讓內容填滿 */
.block-container {
  padding: 1.2rem 2rem 3rem 2rem !important;
  max-width: 100% !important;
}

/* ── Header ── */
.sc-header {
  display: flex;
  align-items: baseline;
  gap: 14px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--border);
  margin-bottom: 12px;
}
.sc-title {
  font-size: 18px;
  font-weight: 700;
  color: #ddeeff;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}
.sc-meta {
  font-size: 11px;
  color: var(--text-dim);
  letter-spacing: 0.05em;
}

/* ── 大盤 bar ── */
.mkt-bar {
  display: flex;
  align-items: center;
  gap: 0;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 0;
  margin-bottom: 14px;
  overflow: hidden;
}
.mkt-item {
  padding: 9px 20px;
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.mkt-item:last-child { border-right: none; }
.mkt-label { font-size: 9.5px; color: var(--text-dim); letter-spacing: 0.1em; text-transform: uppercase; }
.mkt-val   { font-size: 16px; font-weight: 700; color: var(--text); letter-spacing: 0.02em; }
.mkt-pos   { color: var(--red); }
.mkt-neg   { color: var(--green); }
.mkt-neu   { color: var(--text-mid); }

/* ── 篩選區 ── */
.filter-bar {
  display: flex;
  align-items: center;
  gap: 18px;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 10px 16px;
  margin-bottom: 10px;
  flex-wrap: nowrap;
}
.filter-label {
  font-size: 10px;
  color: var(--text-dim);
  letter-spacing: 0.1em;
  text-transform: uppercase;
  white-space: nowrap;
}
/* Streamlit widget 美化 */
[data-baseweb="input"] input {
  background: rgba(10,30,60,0.8) !important;
  border: 1px solid var(--border) !important;
  color: var(--text) !important;
  border-radius: 4px !important;
  font-size: 13px !important;
  font-family: var(--sans) !important;
  padding: 5px 10px !important;
  height: 34px !important;
}
[data-baseweb="input"] input:focus {
  border-color: var(--accent) !important;
  box-shadow: 0 0 0 2px rgba(42,127,255,0.15) !important;
}
/* slider 顏色 */
[data-testid="stSlider"] [data-baseweb="slider"] [role="slider"] {
  background: var(--accent) !important;
  border-color: var(--accent) !important;
}
div[data-testid="stSlider"] > div > div > div { background: var(--accent) !important; }
label[data-testid="stWidgetLabel"] p {
  color: var(--text-mid) !important;
  font-size: 11px !important;
  letter-spacing: 0.04em;
}

/* ── 結果統計 ── */
.result-stat {
  font-size: 11px;
  color: var(--text-dim);
  margin-bottom: 8px;
  letter-spacing: 0.04em;
}
.result-stat b { color: var(--accent); font-weight: 600; }

/* ── 主表格 ── */
.sc-table {
  width: 100%;
  border-collapse: collapse;
  font-family: var(--sans);
  font-size: 13px;
  table-layout: auto;
}
.sc-table thead th {
  background: var(--bg-card);
  color: var(--text-hd);
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  padding: 9px 12px;
  text-align: left;
  border-bottom: 1px solid var(--border-hd);
  white-space: nowrap;
  cursor: pointer;
  user-select: none;
  position: sticky;
  top: 0;
  z-index: 5;
}
.sc-table thead th:hover { color: var(--text); background: var(--bg-hover); }
.sc-table thead th .sort-arrow { margin-left: 4px; opacity: 0.4; font-size: 9px; }
.sc-table thead th.sorted-asc .sort-arrow::after  { content: ' ▲'; opacity: 1; color: var(--accent); }
.sc-table thead th.sorted-desc .sort-arrow::after { content: ' ▼'; opacity: 1; color: var(--accent); }
.sc-table thead th:not(.sorted-asc):not(.sorted-desc) .sort-arrow::after { content: ' ⇅'; }

.sc-table tbody tr { border-bottom: 1px solid var(--border); }
.sc-table tbody tr:nth-child(even) { background: var(--bg-row-alt); }
.sc-table tbody tr:nth-child(odd)  { background: transparent; }
.sc-table tbody tr:hover { background: var(--bg-hover) !important; }

.sc-table tbody td {
  padding: 7px 12px;
  vertical-align: middle;
  white-space: nowrap;
  color: var(--text);
}

/* ── 欄位 ── */
.tk-link {
  color: #4a9fff;
  text-decoration: none;
  font-weight: 700;
  font-size: 13px;
  letter-spacing: 0.04em;
}
.tk-link:hover { color: #90c8ff; }
.stk-name { color: var(--text-mid); font-size: 12px; }
.price-v  { font-weight: 600; color: #ddeeff; }

.chg-pos  { color: var(--red);   font-weight: 600; }
.chg-neg  { color: var(--green); font-weight: 600; }
.chg-zero { color: var(--text-dim); }

/* mini score bar */
.sc-bar-wrap { display:flex; align-items:center; gap:8px; }
.sc-bar-bg {
  width: 48px; height: 4px;
  background: rgba(255,255,255,0.06);
  border-radius: 2px; overflow: hidden; flex-shrink: 0;
}
.sc-bar-fill { height:100%; border-radius:2px; }
.sc-bar-num  { font-size:12.5px; font-weight:700; min-width:24px; }

/* 爆量 badge */
.vol-b {
  display:inline-block; padding:2px 8px;
  border-radius:3px; font-size:11px; font-weight:700;
}
.vol-fire { background:rgba(242,58,84,0.15); color:#f23a54; border:1px solid rgba(242,58,84,0.3); }
.vol-warn { background:rgba(240,128,32,0.15); color:#f08020; border:1px solid rgba(240,128,32,0.3); }
.vol-dim  { color: var(--text-dim); font-size:12px; }

/* RSI */
.rsi-h { color: var(--red);    font-weight: 600; }
.rsi-m { color: var(--yellow); }
.rsi-l { color: var(--green);  font-weight: 600; }
.rsi-n { color: var(--text-mid); }

/* RS */
.rs-p { color: var(--red);   font-weight:600; }
.rs-n { color: var(--green); font-weight:600; }
.rs-z { color: var(--text-dim); }

/* 法人 */
.inst-sb { color: var(--red);        font-weight:800; }
.inst-b  { color: #f07060; font-weight:600; }
.inst-ss { color: var(--green);      font-weight:800; }
.inst-s  { color: #50b888; font-weight:600; }
.inst-z  { color: var(--text-dim); }

/* 訊號 badge */
.sig-b {
  display:inline-block; padding:2px 8px;
  border-radius:3px; font-size:11px; white-space:nowrap;
}
.sig-1 { background:rgba(242,58,84,0.12);  color:#f26070; border:1px solid rgba(242,58,84,0.25); }
.sig-2 { background:rgba(240,128,32,0.12); color:#f0a040; border:1px solid rgba(240,128,32,0.25); }
.sig-3 { background:rgba(0,191,114,0.10);  color:#30d890; border:1px solid rgba(0,191,114,0.25); }
.sig-4 { background:rgba(60,90,130,0.2);   color:#6090b8; border:1px solid rgba(60,90,130,0.4); }

/* 型態 badge */
.pat-t {
  display:inline-block; padding:2px 7px;
  border-radius:3px; font-size:10.5px; margin-right:3px;
}
.pat-a { background:rgba(240,128,32,0.14); color:#f0a040; border:1px solid rgba(240,128,32,0.3); }
.pat-b { background:rgba(42,127,255,0.12); color:#70b0ff; border:1px solid rgba(42,127,255,0.3); }
.pat-c { background:rgba(160,112,240,0.12);color:#c090f8; border:1px solid rgba(160,112,240,0.3); }

</style>
""", unsafe_allow_html=True)


# ── FinMind（2 小時快取）────────────────────────────────────
def _get_token():
    try: return st.secrets["FINMIND_TOKEN"]
    except: return ""

@st.cache_data(ttl=7200, show_spinner=False)
def _finmind_inst(stock_id, token, days=10):
    if not token: return 0
    end   = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=days+10)).strftime("%Y-%m-%d")
    try:
        r = requests.get(
            "https://api.finmindtrade.com/api/v4/data",
            params=dict(dataset="TaiwanStockInstitutionalInvestorsBuySell",
                        data_id=stock_id, start_date=start, end_date=end, token=token),
            timeout=10)
        d = r.json()
        if d.get("status") != 200: return 0
        daily = {}
        for rec in d.get("data", []):
            nm = rec.get("name","")
            if nm in ("外資","外資自營商","Foreign_Investor","投信","Investment_Trust"):
                daily.setdefault(rec["date"], 0)
                daily[rec["date"]] += rec.get("buy",0) - rec.get("sell",0)
        dates = sorted(daily, reverse=True)[:days]
        if not dates: return 0
        sign = 1 if daily[dates[0]] >= 0 else -1
        cnt = sum(1 for _ in (d for d in dates if (1 if daily[d]>=0 else -1)==sign)
                  if True) # walk
        # proper walk
        cnt = 0
        for dt in dates:
            if (1 if daily[dt]>=0 else -1) == sign: cnt += 1
            else: break
        return sign * cnt
    except: return 0

def _fetch_all_finmind(tickers, token):
    res = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(_finmind_inst, sid, token): sid for sid in tickers}
        for f in as_completed(futs):
            sid = futs[f]
            try: res[sid] = f.result()
            except: res[sid] = 0
    return res


# ── 讀資料 ───────────────────────────────────────────────────
@st.cache_data(ttl=300, show_spinner=False)
def _load_stocks():
    p = os.path.join("data","screener_data.json")
    if not os.path.exists(p): return None, None
    with open(p, encoding="utf-8") as f: raw = json.load(f)
    return raw.get("stocks",[]), raw.get("generated_at","")

@st.cache_data(ttl=300, show_spinner=False)
def _load_market():
    p = os.path.join("data","market_data.json")
    if not os.path.exists(p): return {}
    with open(p, encoding="utf-8") as f: return json.load(f)


# ── 渲染輔助 ─────────────────────────────────────────────────
def _kc(v):
    if v is None: return "#3d5a7a"
    if v>=75: return "#f23a54"
    if v>=62: return "#f08020"
    if v>=50: return "#e8c040"
    return "#00bf72"

def _cc(v):
    if v is None: return "#3d5a7a"
    if v>=60: return "#f23a54"
    if v>=40: return "#f08020"
    if v>=25: return "#e8c040"
    return "#3d5a7a"

def _bar(val, cfn, link=None):
    if val is None: return '<span style="color:#3d5a7a">—</span>'
    pct = min(max(val,0),100); c = cfn(val)
    inner = f'''<div class="sc-bar-wrap">
  <div class="sc-bar-bg"><div class="sc-bar-fill" style="width:{pct}%;background:{c}"></div></div>
  <span class="sc-bar-num" style="color:{c}">{int(val)}</span>
</div>'''
    if link: return f'<a href="{link}" target="_blank" style="text-decoration:none">{inner}</a>'
    return inner

def _vol(v):
    if v is None: return '<span class="vol-dim">—</span>'
    if v>=2.0: return f'<span class="vol-b vol-fire">{v:.1f}x</span>'
    if v>=1.5: return f'<span class="vol-b vol-warn">{v:.1f}x</span>'
    return f'<span class="vol-dim">{v:.1f}x</span>'

def _rsi(v):
    if v is None: return '<span class="rsi-n">—</span>'
    if v>=70: return f'<span class="rsi-h">{v:.0f}</span>'
    if v>=50: return f'<span class="rsi-m">{v:.0f}</span>'
    if v<=30: return f'<span class="rsi-l">{v:.0f}</span>'
    return f'<span class="rsi-n">{v:.0f}</span>'

def _rs(v):
    if v is None: return '<span class="rs-z">—</span>'
    if v>0:  return f'<span class="rs-p">+{v:.1f}%</span>'
    if v<0:  return f'<span class="rs-n">{v:.1f}%</span>'
    return '<span class="rs-z">0.0%</span>'

def _inst(v):
    v = v or 0
    if v>=3:  return f'<span class="inst-sb">+{v}</span>'
    if v>=1:  return f'<span class="inst-b">+{v}</span>'
    if v<=-3: return f'<span class="inst-ss">{v}</span>'
    if v<0:   return f'<span class="inst-s">{v}</span>'
    return '<span class="inst-z">0</span>'

def _sig(s):
    if not s: return ''
    m = {"💥突破放量":"sig-1","🚀主力進場":"sig-2","✅洗盤結束":"sig-3","📉量縮整理":"sig-4"}
    return f'<span class="sig-b {m.get(s,"sig-4")}">{s}</span>'

def _pat(pats):
    if not pats: return ''
    return ''.join(f'<span class="pat-t {c}">{n}</span>' for n,c in pats)

def _chg(price, prev):
    if price is None or not prev or prev==0:
        return '<span class="chg-zero">—</span>', 0.0
    p = (price-prev)/prev*100
    if p>0: return f'<span class="chg-pos">+{p:.2f}%</span>', p
    if p<0: return f'<span class="chg-neg">{p:.2f}%</span>', p
    return '<span class="chg-zero">0.00%</span>', 0.0

# 訊號排序權重
SIG_RANK = {"💥突破放量":4,"🚀主力進場":3,"✅洗盤結束":2,"📉量縮整理":1,"":0}
PAT_RANK_MAP = {"pat-a":3,"pat-b":2,"pat-c":1}

def _pat_rank(pats):
    if not pats: return 0
    return max(PAT_RANK_MAP.get(c,0) for _,c in pats)


# ── 主程式 ───────────────────────────────────────────────────
def main():
    stocks, generated_at = _load_stocks()
    mkt = _load_market()

    if stocks is None:
        st.error("⚠️ 找不到 data/screener_data.json，請先執行 GitHub Actions。")
        st.stop()

    total = len(stocks)

    # Header
    st.markdown(f"""
<div class="sc-header">
  <span class="sc-title">🇹🇼 台股半導體 Screener</span>
  <span class="sc-meta">更新：{generated_at or 'N/A'} &nbsp;·&nbsp; {total} 檔</span>
</div>""", unsafe_allow_html=True)

    # 大盤
    if mkt:
        px   = mkt.get('price',0) or 0
        r5   = mkt.get('ret5d',0) or 0
        rsi  = mkt.get('rsi')
        ma_ok= not mkt.get('below_ma20', False)
        r5c  = "mkt-pos" if r5>0 else ("mkt-neg" if r5<0 else "mkt-neu")
        st.markdown(f"""
<div class="mkt-bar">
  <div class="mkt-item"><span class="mkt-label">加權指數</span><span class="mkt-val">{px:,.0f}</span></div>
  <div class="mkt-item"><span class="mkt-label">近5日</span><span class="mkt-val {r5c}">{r5:+.1f}%</span></div>
  <div class="mkt-item"><span class="mkt-label">RSI(14)</span><span class="mkt-val mkt-neu">{f'{rsi:.0f}' if rsi else 'N/A'}</span></div>
  <div class="mkt-item"><span class="mkt-label">MA20</span><span class="mkt-val {'mkt-pos' if ma_ok else 'mkt-neg'}">{'站上 ✓' if ma_ok else '跌破 ✗'}</span></div>
</div>""", unsafe_allow_html=True)

    # FinMind
    token = _get_token()
    if token:
        with st.spinner("📡 抓取法人資料（2h 快取）…"):
            inst_map = _fetch_all_finmind([s["ticker"] for s in stocks], token)
    else:
        inst_map = {}

    for s in stocks:
        s["inst_buy_days"] = inst_map.get(s["ticker"], s.get("inst_buy_days",0))
        s["composite"]     = calc_composite_tw(s)
        s["patterns"]      = detect_patterns_tw(s)
        prev = s.get("prev_close")
        p    = s.get("price")
        s["_chg_pct"] = (p-prev)/prev*100 if p and prev and prev!=0 else None

    # ── 篩選列（單橫排）──
    c1, c2, c3 = st.columns([2.2, 3.5, 3.5])
    with c1:
        search_q = st.text_input("", placeholder="🔍  代號 / 名稱", label_visibility="collapsed")
    with c2:
        kline_min = st.slider("K線分 ≥", 0, 100, 0, 5)
    with c3:
        comp_min  = st.slider("綜合分 ≥", 0, 100, 0, 5)

    # 篩選
    filtered = [
        s for s in stocks
        if (s.get("kline_score") is not None or s.get("volume_ratio") is not None)
        and (s.get("kline_score") or 0) >= kline_min
        and (s.get("composite")  or 0) >= comp_min
        and (not search_q or
             search_q.lower() in s["ticker"].lower() or
             search_q.lower() in (s.get("name") or "").lower())
    ]

    st.markdown(
        f'<div class="result-stat">顯示 <b>{len(filtered)}</b> / {total} 檔 &nbsp;·&nbsp; 點欄位標頭排序</div>',
        unsafe_allow_html=True)

    # ── 建表格資料列 ──
    COLS = ["代號","名稱","現價","漲跌%","K線分","綜合分","爆量","RSI","RS(5日)","法人","今日訊號","型態"]
    # sort_key：對應到 s dict 的數值欄位（None = 不排序）
    SORT_KEYS = {
        "漲跌%":  "_chg_pct",
        "K線分":  "kline_score",
        "綜合分": "composite",
        "爆量":   "volume_ratio",
        "RSI":    "rsi14",
        "RS(5日)":"rs5d",
        "法人":   "inst_buy_days",
        "今日訊號":"signal_rank",
        "型態":   "_pat_rank",
    }

    # 排序 state
    if "sort_col" not in st.session_state:
        st.session_state.sort_col = "K線分"
        st.session_state.sort_asc = False

    # 預先計算 _pat_rank / signal_rank
    for s in filtered:
        s["_pat_rank"]   = _pat_rank(s.get("patterns",[]))
        s["signal_rank"] = SIG_RANK.get(s.get("entry_signal",""), 0)

    sk = SORT_KEYS.get(st.session_state.sort_col)
    if sk:
        asc = st.session_state.sort_asc
        filtered.sort(key=lambda x: (x.get(sk) is None, x.get(sk,0) if asc else -(x.get(sk) or 0)))

    # 建 HTML 列
    rows_html = ""
    for s in filtered:
        ticker = s["ticker"]
        mkt_s  = s.get("market","TW")
        url    = yahoo_tw_url(ticker, mkt_s)
        kurl   = f"https://flydav003-alt.github.io/k-line/?stock={ticker}"
        price  = s.get("price")
        chg_h, _ = _chg(price, s.get("prev_close"))
        rows_html += f"""<tr>
  <td><a class="tk-link" href="{url}" target="_blank">{ticker}</a></td>
  <td><span class="stk-name">{s.get('name',ticker)}</span></td>
  <td><span class="price-v">{f'{price:.1f}' if price is not None else '—'}</span></td>
  <td>{chg_h}</td>
  <td>{_bar(s.get('kline_score'), _kc, kurl)}</td>
  <td>{_bar(s.get('composite'),   _cc)}</td>
  <td>{_vol(s.get('volume_ratio'))}</td>
  <td>{_rsi(s.get('rsi14'))}</td>
  <td>{_rs(s.get('rs5d'))}</td>
  <td>{_inst(s.get('inst_buy_days'))}</td>
  <td>{_sig(s.get('entry_signal',''))}</td>
  <td>{_pat(s.get('patterns',[]))}</td>
</tr>"""

    # 表頭（含排序箭頭）
    sc = st.session_state.sort_col
    asc_flag = st.session_state.sort_asc
    ths = ""
    for col in COLS:
        cls = ""
        if col in SORT_KEYS:
            if col == sc:
                cls = "sorted-asc" if asc_flag else "sorted-desc"
            # 用 JS onclick 寄送
            ths += f'<th class="{cls}" onclick="sortCol(\'{col}\')">{col}<span class="sort-arrow"></span></th>'
        else:
            ths += f'<th>{col}</th>'

    table_html = f"""
<script>
function sortCol(col) {{
  // 透過隱藏按鈕觸發 streamlit rerun
  const btn = document.getElementById('sort_btn_' + col);
  if (btn) btn.click();
}}
</script>
<table class="sc-table">
<thead><tr>{ths}</tr></thead>
<tbody>
{rows_html or '<tr><td colspan="12" style="text-align:center;padding:40px;color:#3d5a7a">沒有符合條件的股票</td></tr>'}
</tbody>
</table>"""

    st.markdown(table_html, unsafe_allow_html=True)

    # 隱藏的排序按鈕（Streamlit 無法從 JS 直接改 session_state，用 st.button 代替）
    # 改用下拉選單排序取代 JS（Streamlit 限制）
    st.markdown("<br>", unsafe_allow_html=True)
    sort_cols_opts = ["K線分","綜合分","漲跌%","爆量","RSI","RS(5日)","法人","今日訊號","型態"]
    col_s1, col_s2, col_s3 = st.columns([2, 2, 6])
    with col_s1:
        new_sort = st.selectbox("排序欄位", sort_cols_opts,
                                index=sort_cols_opts.index(st.session_state.sort_col)
                                if st.session_state.sort_col in sort_cols_opts else 0,
                                label_visibility="visible")
    with col_s2:
        new_asc = st.radio("方向", ["↓ 高→低", "↑ 低→高"],
                           index=0 if not st.session_state.sort_asc else 1,
                           horizontal=True, label_visibility="visible")

    if new_sort != st.session_state.sort_col or (new_asc=="↑ 低→高") != st.session_state.sort_asc:
        st.session_state.sort_col = new_sort
        st.session_state.sort_asc = (new_asc == "↑ 低→高")
        st.rerun()

    # 說明
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
| **RSI** | ≥70 紅（過熱）；50~70 黃（偏強）；≤30 綠（超賣） |
| **法人** | +3以上深紅強買；-3以下深綠強賣 |
| **RS(5日)** | 個股5日漲跌 − 加權指數5日漲跌 |
| **A法人爆量** | 法人連買≥3天 且 爆量≥1.5倍 |
| **B回踩MA60** | 股價在MA60附近、RSI 32~55、MA20上升中 |
| **C底背離** | RSI 底背離（技術反轉訊號）|
> ⚠️ 本工具僅供技術面參考，不構成投資建議。
        """)

if __name__ == "__main__":
    main()
