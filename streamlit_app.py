"""
streamlit_app.py — 台股半導體 Screener
架構：components.html() + JS 排序/篩選，scrolling=False + ResizeObserver
配色：對標圖1深藍黑風格
"""
import json, os, streamlit as st
import streamlit.components.v1 as components
from tw_screener_core import yahoo_tw_url
from stats_db import export_stats_payload

st.set_page_config(page_title="台股半導體 Screener", page_icon="📈",
                   layout="wide", initial_sidebar_state="collapsed")
st.markdown("""<style>
  [data-testid="stHeader"],[data-testid="stToolbar"],
  [data-testid="stDecoration"],#MainMenu,footer{display:none!important;}
  .block-container{padding:0!important;max-width:100%!important;}
  [data-testid="stVerticalBlock"]{gap:0!important;padding:0!important;}
  [data-testid="element-container"]{padding:0!important;margin:0!important;}
  html,body{overflow:auto!important;height:auto!important;}
  .stApp{overflow:visible!important;height:auto!important;}
  iframe{display:block!important;border:none!important;margin:0!important;}
</style>""", unsafe_allow_html=True)

@st.cache_data(ttl=300, show_spinner=False)
def _load_stocks():
    p=os.path.join("data","screener_data.json")
    if not os.path.exists(p): return None,None
    with open(p,encoding="utf-8") as f: raw=json.load(f)
    return raw.get("stocks",[]), raw.get("generated_at","")

@st.cache_data(ttl=300, show_spinner=False)
def _load_market():
    p=os.path.join("data","market_data.json")
    if not os.path.exists(p): return {}
    with open(p,encoding="utf-8") as f: return json.load(f)

@st.cache_data(ttl=600, show_spinner=False)
def _load_stats():
    return export_stats_payload()

def main():
    stocks, generated_at = _load_stocks()
    mkt = _load_market()
    if stocks is None:
        st.error("⚠️ 找不到 data/screener_data.json，請先執行 GitHub Actions。")
        st.stop()

    SIG_RANK = {"💥突破放量":4,"🚀主力進場":3,"✅洗盤結束":2,"📉量縮整理":1,"":0}
    PAT_RANK = {"pat-a":3,"pat-b":2,"pat-c":1}

    rows = []
    for s in stocks:
        price = s.get("price")
        prev  = s.get("prev_close")
        chg   = round((price-prev)/prev*100,2) if price and prev and prev!=0 else None
        pat_r = max((PAT_RANK.get(c,0) for _,c in s.get("patterns",[])), default=0)
        rows.append({
            "ticker":   s["ticker"],
            "name":     s.get("name", s["ticker"]),
            "price":    price,
            "chg":      chg,
            "kline":    s.get("kline_score"),
            "comp":     s.get("composite"),
            "vol":      s.get("volume_ratio"),
            "rsi":      s.get("rsi14"),
            "rs5d":     s.get("rs5d"),
            "inst":     s.get("inst_buy_days",0) or 0,
            "signal":   s.get("entry_signal",""),
            "sig_rank": SIG_RANK.get(s.get("entry_signal",""),0),
            "patterns": [[n,c] for n,c in s.get("patterns",[])],
            "pat_rank": pat_r,
            "yahoo_url":yahoo_tw_url(s["ticker"], s.get("market","TW")),
            "kline_url":f"https://flydav003-alt.github.io/k-line/?stock={s['ticker']}",
        })

    mkt_px  = mkt.get("price",0) or 0
    mkt_r5  = mkt.get("ret5d",0) or 0
    mkt_rsi = mkt.get("rsi")
    mkt_ma  = not mkt.get("below_ma20", False)
    rows_json = json.dumps(rows, ensure_ascii=False, default=str)
    stats_json = json.dumps(_load_stats(), ensure_ascii=False, default=str)
    total     = len(rows)
    gen_at    = generated_at or "N/A"
    r5cls     = "pos" if mkt_r5>0 else ("neg" if mkt_r5<0 else "neu")
    ma_cls    = "pos" if mkt_ma else "neg"
    ma_txt    = "站上 ✓" if mkt_ma else "跌破 ✗"
    rsi_txt   = f"{mkt_rsi:.0f}" if mkt_rsi else "N/A"

    page = f"""<!DOCTYPE html>
<html lang="zh-TW"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@400;500;700&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
:root{{
  --bg:      #0f172a;
  --bg2:     #1e293b;
  --bg3:     #1e293b;
  --row-odd: #0f172a;
  --row-evn: #0f172a;
  --hov:     rgba(99,102,241,0.06);
  --bdr:     #334155;
  --bdr2:    #6366f1;
  --txt:     #e2e8f0;
  --dim:     #64748b;
  --mid:     #94a3b8;
  --hd:      #94a3b8;
  --acc:     #6366f1;
  --red:     #ef4444;
  --grn:     #22c55e;
  --orn:     #f59e0b;
  --yel:     #fde68a;
  --sans:    'Noto Sans TC',sans-serif;
}}
body{{background:var(--bg);color:var(--txt);font-family:var(--sans);font-size:13px;}}

/* ── HEADER ── */
.hd{{
  display:flex;align-items:baseline;gap:14px;
  padding:20px 24px 6px;
  border-bottom:none;
}}
.hd-t{{font-size:20px;font-weight:700;color:var(--acc);letter-spacing:0;}}
.hd-m{{font-size:12px;color:var(--mid);}}

/* ── MARKET BAR ── */
.mkt{{
  display:flex;border:1px solid var(--bdr);
  background:var(--bg2);border-radius:8px;
  margin:0 24px 16px;padding:10px 18px;gap:24px;align-items:center;
}}
.mi{{
  padding:0;border-right:none;
  display:flex;flex-direction:row;align-items:center;gap:8px;
}}
.mi:last-child{{border-right:none;}}
.mi-l{{font-size:13px;color:var(--txt);letter-spacing:0;text-transform:none;font-weight:400;}}
.mi-v{{font-size:20px;font-weight:700;color:#fff;}}
.pos{{color:var(--red)}} .neg{{color:var(--grn)}} .neu{{color:var(--mid)}}

/* ── FILTER BAR ── */
.fb{{
  display:flex;align-items:center;gap:12px;flex-wrap:wrap;
  padding:0 24px 12px;background:transparent;border-bottom:none;
}}
.srch{{
  position:relative;display:flex;align-items:center;
}}
.srch-ico{{position:absolute;left:11px;color:var(--mid);font-size:14px;pointer-events:none;}}
.srch input{{
  background:var(--bg2);border:1px solid var(--bdr);color:var(--txt);
  border-radius:6px;padding:6px 12px 6px 32px;
  font-size:13px;font-family:var(--sans);width:200px;outline:none;
}}
.srch input:focus{{border-color:var(--acc);}}
.srch input::placeholder{{color:var(--mid);}}

/* 滑桿群組 */
.sl-grp{{display:flex;align-items:center;gap:8px;}}
.sl-lbl{{font-size:13px;color:var(--mid);white-space:nowrap;font-weight:400;}}
input[type=range]{{
  -webkit-appearance:none;width:140px;height:6px;
  background:linear-gradient(to right, var(--acc) 0%, var(--acc) var(--pct,0%), var(--bdr) var(--pct,0%), var(--bdr) 100%);
  border-radius:3px;outline:none;cursor:pointer;border:none;
  padding:0;margin:0;
}}
input[type=range]::-webkit-slider-thumb{{
  -webkit-appearance:none;width:16px;height:16px;
  border-radius:50%;background:var(--acc);
  border:2px solid #c7d2fe;cursor:pointer;
  box-shadow:none;transition:box-shadow .15s;
}}
input[type=range]::-webkit-slider-thumb:hover{{box-shadow:0 0 0 4px rgba(99,102,241,.25);}}
.sl-val{{
  font-size:13px;font-weight:500;color:var(--txt);
  min-width:28px;text-align:center;
  background:var(--bg2);border:1px solid var(--bdr);
  border-radius:6px;padding:4px 8px;
}}

/* ── STAT LINE ── */
.stat{{padding:4px 24px 10px;font-size:12px;color:var(--mid);border-bottom:none;}}
.stat b{{color:var(--txt);font-weight:500;}}

.tabs{{display:flex;gap:8px;padding:0 24px 14px;}}
.tab-btn{{
  border:1px solid var(--bdr);background:transparent;color:var(--mid);
  border-radius:6px;padding:8px 14px;font-family:var(--sans);font-size:13px;
  cursor:pointer;font-weight:500;
}}
.tab-btn:hover{{color:var(--txt);border-color:var(--acc);}}
.tab-btn.active{{background:var(--acc);border-color:var(--acc);color:#fff;}}
.tab-panel{{display:none;}}
.tab-panel.active{{display:block;}}

/* ── STATS ── */
.stats-wrap{{margin:0 24px 16px;border:1px solid #2d4060;border-radius:10px;background:#0d1a2e;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,.4);}}
.stats-head{{display:flex;align-items:center;justify-content:space-between;padding:14px 18px;border-bottom:1px solid #2d4060;gap:12px;background:linear-gradient(90deg,#0f2040,#0d1a30);}}
.stats-title{{font-size:15px;font-weight:700;color:#e2e8f0;letter-spacing:.5px;}}
.stats-note{{font-size:12px;color:#64748b;}}
/* KPI cards */
.stats-kpi-row{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:1px;background:#1a2e4a;border-bottom:1px solid #1a2e4a;}}
.stat-card{{background:#0d1a2e;padding:16px 18px;position:relative;overflow:hidden;}}
.stat-card::before{{content:'';position:absolute;top:0;left:0;right:0;height:2px;}}
.stat-card.sk1::before{{background:linear-gradient(90deg,#3b82f6,#6366f1);}}
.stat-card.sk2::before{{background:linear-gradient(90deg,#6366f1,#8b5cf6);}}
.stat-card.sk3::before{{background:linear-gradient(90deg,#06b6d4,#0891b2);}}
.stat-card.sk4::before{{background:linear-gradient(90deg,#22c55e,#059669);}}
.stat-k{{font-size:10px;color:#475569;margin-bottom:8px;font-weight:600;text-transform:uppercase;letter-spacing:.8px;}}
.stat-v{{font-size:28px;font-weight:700;color:#e8f4ff;line-height:1;}}
.stat-sub{{font-size:11px;color:#475569;margin-top:5px;}}
/* inner tabs */
.stats-inner-tabs{{display:flex;gap:0;border-bottom:1px solid #1a2e4a;background:#080f1e;}}
.sit{{padding:10px 16px;font-size:12px;font-weight:600;color:#475569;cursor:pointer;border:none;background:none;font-family:var(--sans);border-bottom:2px solid transparent;transition:color .15s;letter-spacing:.3px;}}
.sit:hover{{color:#7eb8e0;}}
.sit.sa{{color:#60a5fa;border-bottom-color:#6366f1;}}
.sp{{display:none;padding:0;}}.sp.sa{{display:block;}}
/* stats tables */
.stats-table{{width:100%;border-collapse:collapse;}}
.stats-table th,.stats-table td{{padding:9px 12px;border-bottom:1px solid #0f2040;font-size:12px;white-space:nowrap;}}
.stats-table th{{color:#475569;font-weight:600;background:#080f1e;cursor:pointer;user-select:none;}}
.stats-table th:hover{{color:#7eb8e0;}}
.stats-table td{{color:#94a3b8;}}
.stats-table tbody tr:hover td{{background:rgba(59,130,246,.06)!important;}}
.stats-empty{{padding:20px;color:#475569;font-size:13px;}}
.stats-section-title{{padding:12px 16px 8px;font-size:11px;font-weight:700;color:#475569;letter-spacing:.8px;text-transform:uppercase;border-top:1px solid #0f2040;}}
.stats-section-title:first-of-type{{border-top:none;}}
.stats-scroll{{overflow-x:auto;}}
/* tools bar */
.stats-tools{{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:10px 14px;border-bottom:1px solid #0f2040;background:#080f1e;}}
.stats-tools input{{background:#0d1a2e;border:1px solid #1a2e4a;color:#94a3b8;border-radius:6px;padding:6px 10px;font-size:12px;font-family:var(--sans);outline:none;}}
.stats-tools input[type=text]{{width:200px;}}
.stats-tools input:focus{{border-color:#3b82f6;}}
.stats-tools select{{background:#0d1a2e;border:1px solid #1a2e4a;color:#94a3b8;border-radius:6px;padding:6px 8px;font-size:12px;font-family:var(--sans);outline:none;}}
.stats-sl-grp{{display:flex;align-items:center;gap:6px;}}
.stats-sl-lbl{{font-size:11px;color:#475569;white-space:nowrap;font-weight:600;}}
.sl-val2{{font-size:12px;font-weight:600;color:#e2e8f0;min-width:26px;text-align:center;background:#0d1a2e;border:1px solid #1a2e4a;border-radius:4px;padding:3px 6px;}}
.stats-table th.stats-sort{{cursor:pointer;}}
.stats-table th.stats-sort:hover{{color:#7eb8e0;}}
/* chart boxes */
.sc-grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px;padding:14px;}}
.sc-grid.sc-wide{{grid-template-columns:1fr;}}
.sc-box{{background:#080f1e;border:1px solid #0f2040;border-radius:8px;padding:14px;}}
.sc-title{{font-size:11px;font-weight:700;color:#475569;letter-spacing:.6px;text-transform:uppercase;margin-bottom:10px;}}
/* heatmap */
.hm-grid{{display:grid;grid-template-columns:80px repeat(4,1fr);gap:3px;padding:14px;}}
.hm-hdr{{font-size:10px;color:#475569;text-align:center;padding:4px 2px;}}
.hm-row-lbl{{display:flex;align-items:center;font-size:10px;color:#475569;}}
.hm-cell{{border-radius:5px;padding:10px 4px;text-align:center;font-size:11px;font-weight:700;transition:transform .1s;cursor:default;}}
.hm-cell:hover{{transform:scale(1.08);z-index:2;position:relative;}}
/* badges */
.wr-badge{{display:inline-block;padding:2px 7px;border-radius:4px;font-size:11px;font-weight:700;}}
.wr-s{{background:rgba(239,68,68,.15);color:#f87171;border:1px solid rgba(239,68,68,.25);}}
.wr-a{{background:rgba(245,158,11,.12);color:#fbbf24;border:1px solid rgba(245,158,11,.22);}}
.wr-b{{background:rgba(34,197,94,.1);color:#4ade80;border:1px solid rgba(34,197,94,.2);}}
.wr-c{{background:rgba(100,116,139,.1);color:#64748b;border:1px solid rgba(100,116,139,.2);}}
.peak-badge{{display:inline-block;padding:2px 7px;border-radius:4px;font-size:11px;font-weight:700;background:rgba(99,102,241,.15);color:#818cf8;border:1px solid rgba(99,102,241,.3);}}
/* score bar in table */
.sc-bar{{display:flex;align-items:center;gap:6px;}}
.sc-track{{flex:1;height:5px;border-radius:3px;background:#0f2040;overflow:hidden;max-width:70px;}}
.sc-fill{{height:5px;border-radius:3px;}}
/* hot strat bar */
.strat-bar{{display:flex;align-items:center;gap:8px;}}
.strat-bg{{flex:1;height:5px;border-radius:3px;background:#0f2040;overflow:hidden;max-width:90px;}}
.strat-fill{{height:5px;border-radius:3px;background:#6366f1;}}
/* status */
.st-open{{background:#0f2040;color:#475569;padding:2px 6px;border-radius:3px;font-size:10px;}}
.st-partial{{background:rgba(245,158,11,.1);color:#fbbf24;border:1px solid rgba(245,158,11,.2);padding:2px 6px;border-radius:3px;font-size:10px;}}
.st-matured{{background:rgba(34,197,94,.1);color:#4ade80;border:1px solid rgba(34,197,94,.2);padding:2px 6px;border-radius:3px;font-size:10px;}}
@media(max-width:900px){{.stats-kpi-row{{grid-template-columns:repeat(2,minmax(0,1fr));}}.sc-grid{{grid-template-columns:1fr;}}}}

/* ── TABLE ── */
.tbl-wrap{{padding:0 24px 40px;overflow-x:auto;}}
table{{width:100%;border-collapse:collapse;}}
thead th{{
  background:var(--bg2);color:var(--mid);
  font-size:13px;font-weight:500;letter-spacing:0;text-transform:none;
  padding:10px 8px;text-align:left;
  border-bottom:2px solid var(--bdr);
  white-space:nowrap;user-select:none;
  position:sticky;top:0;z-index:5;
}}
thead th.sortable{{cursor:pointer;}}
thead th.sortable:hover{{color:var(--acc);}}
.arr{{margin-left:4px;font-size:9px;opacity:.35;}}
th.asc  .arr{{opacity:1;color:var(--acc);}}
th.desc .arr{{opacity:1;color:var(--acc);}}
th.asc  .arr::after{{content:'▲'}}
th.desc .arr::after{{content:'▼'}}
th:not(.asc):not(.desc) .arr::after{{content:'↕'}}

tbody tr:nth-child(odd) {{background:var(--bg);}}
tbody tr:nth-child(even){{background:var(--bg);}}
tbody tr:hover td{{background:var(--hov)!important;}}
tbody td{{padding:9px 8px;vertical-align:middle;white-space:nowrap;border-bottom:1px solid var(--bdr);}}

/* cells */
.tk{{color:var(--acc);font-weight:600;font-size:13px;text-decoration:none;}}
.tk:hover{{text-decoration:underline;}}
.nm{{color:var(--txt);font-size:13px;text-decoration:none;}}
.nm:hover{{text-decoration:underline;color:var(--acc);}}
.pv{{font-weight:400;color:var(--txt);font-size:13px;}}

/* mini bar — 對標原版 score-bar-wrap */
.bc{{display:flex;align-items:center;gap:6px;min-width:90px;}}
.bb{{height:6px;border-radius:3px;background:var(--bdr);flex:1;max-width:80px;overflow:hidden;}}
.bf{{height:6px;border-radius:3px;}}
.bn{{font-size:12px;font-weight:600;color:var(--txt);white-space:nowrap;min-width:24px;}}

/* vol badge — 對標原版 badge */
.vb{{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;white-space:nowrap;}}
.vf{{background:rgba(239,68,68,0.15);color:#ef4444;border:1px solid rgba(239,68,68,0.35);}}
.vw{{background:rgba(245,158,11,0.12);color:#f59e0b;border:1px solid rgba(245,158,11,0.30);}}
.vd{{color:var(--mid);font-size:13px;}}

/* rsi */
.rh{{color:#ff6b6b;font-weight:600;}}
.rm{{color:#fde68a;}}
.rl{{color:#86efac;font-weight:600;}}
.rn{{color:var(--mid);}}

/* inst */
.isb{{color:#ef4444;font-weight:700;font-size:13px;}}
.ib {{color:#ef4444;font-weight:700;font-size:13px;}}
.iss{{color:#22c55e;font-weight:700;font-size:13px;}}
.is {{color:#22c55e;font-weight:700;font-size:13px;}}
.iz {{color:var(--dim);font-size:12px;}}

/* signal — 對標原版 badge 實色 */
.sg{{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;white-space:nowrap;font-weight:600;}}
.s1{{background:#7f1d1d;color:#fca5a5;}}
.s2{{background:#422006;color:#fde68a;}}
.s3{{background:#14532d;color:#86efac;}}
.s4{{background:#1e293b;color:#94a3b8;border:1px solid #334155;}}

/* pattern */
.pt{{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;margin-right:3px;font-weight:600;}}
.pa{{background:#7f1d1d;color:#fca5a5;}}
.pb{{background:#1e3a5f;color:#93c5fd;}}
.pc{{background:#4a1d96;color:#c4b5fd;}}

/* ── 說明欄 ── */
.legend{{
  margin:0 24px 24px;
  border:1px solid var(--bdr);border-radius:6px;
  overflow:hidden;
}}
.legend-hd{{
  padding:8px 14px;background:var(--bg2);
  font-size:12px;font-weight:500;color:var(--mid);
  cursor:pointer;
  display:flex;justify-content:space-between;align-items:center;
}}
.legend-hd:hover{{color:var(--acc);}}
.legend-body{{display:none;padding:14px;background:var(--bg2);}}
.legend-body.open{{display:block;}}
.legend-body table{{margin-top:0;}}
.legend-body thead th{{
  background:transparent;padding:6px 8px;font-size:12px;font-weight:500;
  border-bottom:1px solid var(--bdr);position:static;color:var(--mid);
}}
.legend-body tbody td{{padding:6px 8px;font-size:12px;border-bottom:1px solid var(--bdr);color:var(--txt);}}
.legend-body tbody tr:last-child td{{border-bottom:none;}}
.note{{color:var(--mid);font-size:11px;margin-top:10px;}}
</style>
</head><body>

<div class="hd">
  <span class="hd-t">🇹🇼 台股半導體 Screener</span>
  <span class="hd-m">更新：{gen_at} &nbsp;·&nbsp; {total} 檔</span>
</div>

<div class="mkt">
  <div class="mi"><span class="mi-l">加權指數</span><span class="mi-v">{mkt_px:,.0f}</span></div>
  <div class="mi"><span class="mi-l">近5日</span><span class="mi-v {r5cls}">{mkt_r5:+.1f}%</span></div>
  <div class="mi"><span class="mi-l">RSI(14)</span><span class="mi-v neu">{rsi_txt}</span></div>
  <div class="mi"><span class="mi-l">MA20</span><span class="mi-v {ma_cls}">{ma_txt}</span></div>
</div>

<div class="tabs">
  <button class="tab-btn active" type="button" data-tab="dailyPanel" onclick="switchTab('dailyPanel')">每日推薦</button>
  <button class="tab-btn" type="button" data-tab="statsPanel" onclick="switchTab('statsPanel')">資料庫統計</button>
</div>

<div class="tab-panel" id="statsPanel">
<div class="stats-wrap" id="statsBox">
  <div class="stats-head">
    <div class="stats-title">資料庫統計</div>
    <div class="stats-note" id="statsHeadNote">每日真實訊號 + T+1 / T+3 / T+5 / T+7 / T+10 事後績效</div>
  </div>
  <div id="statsContent" class="stats-empty">統計資料載入中...</div>
</div>
</div>

<div class="tab-panel active" id="dailyPanel">
<div class="fb">
  <div class="srch">
    <span class="srch-ico">🔍</span>
    <input type="text" id="searchQ" placeholder="代號 / 名稱" oninput="applyFilter()">
  </div>
  <div class="sl-grp">
    <span class="sl-lbl">K線分 ≥</span>
    <input type="range" id="slK" min="0" max="100" step="1" value="0"
      oninput="updSlider(this,'kvK');applyFilter()">
    <span class="sl-val" id="kvK">0</span>
  </div>
  <div class="sl-grp">
    <span class="sl-lbl">綜合分 ≥</span>
    <input type="range" id="slC" min="0" max="100" step="1" value="0"
      oninput="updSlider(this,'kvC');applyFilter()">
    <span class="sl-val" id="kvC">0</span>
  </div>
</div>

<div class="stat" id="statLine">載入中…</div>

<div class="tbl-wrap">
<table>
<thead><tr>
  <th>代號</th>
  <th>名稱</th>
  <th>現價</th>
  <th class="sortable" data-k="chg">漲跌%<span class="arr"></span></th>
  <th class="sortable" data-k="kline">K線分<span class="arr"></span></th>
  <th class="sortable" data-k="comp">綜合分<span class="arr"></span></th>
  <th class="sortable" data-k="vol">爆量<span class="arr"></span></th>
  <th class="sortable" data-k="rsi">RSI<span class="arr"></span></th>
  <th class="sortable" data-k="rs5d">RS(5日)<span class="arr"></span></th>
  <th class="sortable" data-k="inst">法人<span class="arr"></span></th>
  <th class="sortable" data-k="sig_rank">今日訊號<span class="arr"></span></th>
  <th class="sortable" data-k="pat_rank">型態<span class="arr"></span></th>
</tr></thead>
<tbody id="tBody"></tbody>
</table>
</div>

<div class="legend">
  <div class="legend-hd" onclick="toggleLegend()">
    <span>📖 指標說明</span><span id="legArr">▶</span>
  </div>
  <div class="legend-body" id="legBody">
    <table>
      <thead><tr><th>指標</th><th>說明</th></tr></thead>
      <tbody>
        <tr><td>K線分 ≥ 78</td><td>頂級強勢🔴；70~77 強勢🟠；60~69 偏多🟡；&lt; 60 偏弱</td></tr>
        <tr><td>綜合分 ≥ 88</td><td>極致狙擊🔴；75~87 高勝率🟠；60~74 觀察🟡；&lt; 60 偏弱</td></tr>
        <tr><td>💥 突破放量</td><td>爆量創5日新高 — 最強進場訊號</td></tr>
        <tr><td>🚀 主力進場</td><td>大量紅K收高位</td></tr>
        <tr><td>✅ 洗盤結束</td><td>量縮後放量紅K</td></tr>
        <tr><td>📉 量縮整理</td><td>量縮蓄勢中</td></tr>
        <tr><td>爆量</td><td>1.5x 橘色警示；2.0x+ 紅色強訊號</td></tr>
        <tr><td>RSI</td><td>≥70 紅（過熱）；50~70 黃（偏強）；≤30 綠（超賣）</td></tr>
        <tr><td>法人</td><td>+3以上深紅強買；-3以下深綠強賣</td></tr>
        <tr><td>RS(5日)</td><td>個股5日漲跌 − 加權指數5日漲跌（超額報酬）</td></tr>
        <tr><td>A 法人爆量</td><td>法人連買≥3天 且 爆量≥1.5倍</td></tr>
        <tr><td>B 回踩MA60</td><td>股價在MA60附近、RSI 32~55、MA20上升中</td></tr>
        <tr><td>C 底背離</td><td>RSI 底背離（技術反轉訊號）</td></tr>
      </tbody>
    </table>
    <p class="note">⚠️ 本工具僅供技術面參考，不構成投資建議。</p>
  </div>
</div>

</div>

<script>
const RAW = {rows_json};
const STATS = {stats_json};
let sortKey='kline', sortAsc=false;
let statsSortKey='trade_date', statsSortAsc=false;
const SC={{"💥突破放量":"s1","🚀主力進場":"s2","✅洗盤結束":"s3","📉量縮整理":"s4"}};
const PC={{"pat-a":"pa","pat-b":"pb","pat-c":"pc"}};

// K線分階級色：78+ 鮮紅 / 70~77 橙 / 60~69 金黃 / 59- 灰
function kc(v){{
  if(v==null)return'#334155';
  if(v>=78)return'#FF3B3B';
  if(v>=70)return'#FF8C42';
  if(v>=60)return'#F5C518';
  return'#6b7280';
}}
// 綜合分階級色：88+ 鮮紅 / 75~87 橙 / 60~74 金黃 / 59- 灰
function cc(v){{
  if(v==null)return'#334155';
  if(v>=88)return'#FF3B3B';
  if(v>=75)return'#FF8C42';
  if(v>=60)return'#F5C518';
  return'#6b7280';
}}
function bar(val,cfn,link){{
  if(val==null)return'<span style="color:#64748b">—</span>';
  const pct=Math.min(Math.max(val,0),100),c=cfn(val);
  const fw=val>=78||val>=88?700:600;
  const inner=`<div class="bc"><div class="bb"><div class="bf" style="width:${{pct}}%;background:${{c}}"></div></div><span class="bn" style="color:${{c}};font-weight:${{fw}}">${{Math.round(val)}}</span></div>`;
  return link?`<a href="${{link}}" target="_blank" style="text-decoration:none">${{inner}}</a>`:inner;
}}
function fVol(v){{
  if(v==null)return'<span class="vd">—</span>';
  if(v>=2.0)return`<span class="vb vf">${{v.toFixed(1)}}x</span>`;
  if(v>=1.5)return`<span class="vb vw">${{v.toFixed(1)}}x</span>`;
  return`<span class="vd">${{v.toFixed(1)}}x</span>`;
}}
function fRsi(v){{
  if(v==null)return'<span class="rn">—</span>';
  if(v>=70)return`<span class="rh">${{Math.round(v)}}</span>`;
  if(v>=50)return`<span class="rm">${{Math.round(v)}}</span>`;
  if(v<=30)return`<span class="rl">${{Math.round(v)}}</span>`;
  return`<span class="rn">${{Math.round(v)}}</span>`;
}}
function fRs(v){{
  if(v==null)return'<span class="iz">—</span>';
  if(v>0)return`<span class="pos">+${{v.toFixed(1)}}%</span>`;
  if(v<0)return`<span class="neg">${{v.toFixed(1)}}%</span>`;
  return'<span class="iz">0.0%</span>';
}}
function fInst(v){{
  v=v||0;
  if(v>=3)return`<span class="isb">+${{v}}</span>`;
  if(v>=1)return`<span class="ib">+${{v}}</span>`;
  if(v<=-3)return`<span class="iss">${{v}}</span>`;
  if(v<0)return`<span class="is">${{v}}</span>`;
  return'<span class="iz">0</span>';
}}
function fSig(s){{
  if(!s)return'';
  return`<span class="sg ${{SC[s]||'s4'}}">${{s}}</span>`;
}}
function fPat(arr){{
  if(!arr||!arr.length)return'';
  return arr.map(([n,c])=>`<span class="pt ${{PC[c]||'pb'}}">${{n}}</span>`).join('');
}}
function fChg(v){{
  if(v==null)return'<span style="color:#64748b">—</span>';
  if(v>0)return`<span class="pos">+${{v.toFixed(2)}}%</span>`;
  if(v<0)return`<span class="neg">${{v.toFixed(2)}}%</span>`;
  return'<span class="iz">0.00%</span>';
}}

function pct(v){{
  if(v==null || Number.isNaN(Number(v)))return'-';
  const n=Number(v);
  return `${{n>0?'+':''}}${{n.toFixed(2)}}%`;
}}
function outcomePct(v){{
  if(v==null || Number.isNaN(Number(v)))return'<span class="iz">pending</span>';
  const n=Number(v), cls=n>=0?'pos':'neg';
  return `<span class="${{cls}}">${{n>0?'+':''}}${{n.toFixed(2)}}%</span>`;
}}
function rate(v){{
  if(v==null || Number.isNaN(Number(v)))return'-';
  return `${{Number(v).toFixed(1)}}%`;
}}
function statCell(v){{
  if(v==null || Number.isNaN(Number(v)))return'<span class="iz">pending</span>';
  const n=Number(v), cls=n>=0?'pos':'neg';
  return `<span class="${{cls}}">${{n>0?'+':''}}${{n.toFixed(2)}}%</span>`;
}}
function statNum(v){{
  if(v==null || Number.isNaN(Number(v)))return null;
  return Number(v);
}}
function labelEvent(v){{
  const m={{
    BOTH_STRONG:'雙強',
    ENTRY:'雙分進場',
    COMP_STRONG_K_LOW:'綜強K低',
    COMP_HIGH_K_LOW:'綜高K低',
    K_STRONG_COMP_LOW:'K強綜低',
    K_HIGH_COMP_LOW:'K高綜低',
    WATCH_CONFIRMED:'觀察確認'
  }};
  return m[v]||v||'-';
}}
function switchTab(id){{
  document.querySelectorAll('.tab-panel').forEach(p=>p.classList.toggle('active',p.id===id));
  document.querySelectorAll('.tab-btn').forEach(b=>b.classList.toggle('active',b.dataset.tab===id));
  schedResize();
}}
// ── STATS RENDERING ──────────────────────────────────────────────────────────
// Chart.js global defaults
function _cjsDefaults(){{
  if(!window.Chart)return;
  Chart.defaults.color='#475569';
  Chart.defaults.font.family="'Noto Sans TC',sans-serif";
  Chart.defaults.font.size=11;
}}

const _charts={{}};
function _mkChart(id,cfg){{
  if(!window.Chart)return;
  if(_charts[id]){{_charts[id].destroy();delete _charts[id];}}
  const el=document.getElementById(id);if(!el)return;
  _charts[id]=new Chart(el,cfg);
}}
const _GRID={{color:'rgba(15,32,64,.8)'}};
const _TICK={{color:'#334155'}};
const _BASE_SCALE={{x:{{grid:_GRID,ticks:_TICK}},y:{{grid:_GRID,ticks:_TICK}}}};
const _NO_LEGEND={{legend:{{display:false}}}};

const ETYPE_LBL={{BOTH_STRONG:'雙強',ENTRY:'雙分進場',COMP_STRONG_K_LOW:'綜強K低',COMP_HIGH_K_LOW:'綜高K低',K_STRONG_COMP_LOW:'K強綜低',K_HIGH_COMP_LOW:'K高綜低',WATCH_CONFIRMED:'觀察確認'}};
const ET_COLORS=['#3b82f6','#6366f1','#06b6d4','#f59e0b','#f87171','#4ade80','#a78bfa'];

function labelEvent(v){{return ETYPE_LBL[v]||v||'-';}}
function pct(v){{if(v==null||Number.isNaN(Number(v)))return'-';const n=Number(v);return`${{n>0?'+':''}}${{n.toFixed(2)}}%`;}}
function rate(v){{if(v==null||Number.isNaN(Number(v)))return'-';return`${{Number(v).toFixed(1)}}%`;}}
function statCell(v){{
  if(v==null||Number.isNaN(Number(v)))return'<span class="iz">—</span>';
  const n=Number(v),cls=n>=0?'pos':'neg';
  return`<span class="${{cls}}">${{n>0?'+':''}}${{n.toFixed(2)}}%</span>`;
}}
function statNum(v){{if(v==null||Number.isNaN(Number(v)))return null;return Number(v);}}
function wrBadge(wr,n){{
  if(n==null||n<5||wr==null)return`<span style="color:#1e3a5f;font-size:10px">樣本不足</span>`;
  const cls=wr>=65?'wr-s':wr>=55?'wr-a':wr>=45?'wr-b':'wr-c';
  return`<span class="wr-badge ${{cls}}">${{Number(wr).toFixed(1)}}%</span>`;
}}
function kCol(v){{return v>=78?'#f87171':v>=70?'#fbbf24':v>=60?'#4ade80':'#475569';}}
function cCol(v){{return v>=88?'#f87171':v>=75?'#fbbf24':v>=60?'#4ade80':'#475569';}}

// ── Tab: 門檻分析 ──────────────────────────────────────────────
function buildTabThreshold(){{
  const ts=STATS.threshold_stats||[];
  const rules=[...new Set(ts.map(x=>x.rule))];
  if(!rules.length)return'<div style="padding:20px;color:#334155">尚無資料。</div>';
  const hs=[1,3,5,7,10];

  // Build horizontal bar chart data (T+5 win rate per rule)
  const t5data=rules.map(r=>{{const x=ts.find(t=>t.rule===r&&t.horizon===5);return x?Number(x.win_rate):0;}});
  const t5colors=t5data.map(v=>v>=65?'rgba(248,113,113,.7)':v>=55?'rgba(251,191,36,.7)':v>=45?'rgba(74,222,128,.7)':'rgba(100,116,139,.4)');

  hdr = '<tr><th style="min-width:160px">條件</th><th>樣本</th>' + ''.join([f'<th>T+{h} 勝率</th><th>T+{h} 報酬</th>' for h in hs]) + '</tr>' ;
  const rows=rules.map(r=>{{
    const rd=ts.filter(x=>x.rule===r);
    const n=rd[0]?.sample_count||0;
    const cells=hs.map(h=>{{
      const x=rd.find(d=>d.horizon===h);
      if(!x)return'<td>—</td><td>—</td>';
      return`<td>${{wrBadge(x.win_rate,x.sample_count)}}</td><td class="${{(x.avg_return||0)>=0?'pos':'neg'}}">${{pct(x.avg_return)}}</td>`;
    }});
    return`<tr><td style="color:#93c5fd;font-weight:600">${{r}}</td><td style="color:#334155">${{n}}</td>${{cells.join('')}}</tr>`;
  }});

  return`
  <div class="sc-grid sc-wide" style="padding-bottom:0">
    <div class="sc-box">
      <div class="sc-title">T+5 勝率比較（各門檻）</div>
      <div style="position:relative;height:${{Math.max(rules.length*32,140)}}px"><canvas id="chartThresh"></canvas></div>
    </div>
  </div>
  <div class="stats-scroll" style="padding:0 14px 14px">
    <table class="stats-table"><thead>${{hdr}}</thead><tbody>${{rows.join('')}}</tbody></table>
    <div style="padding:8px 0 4px;font-size:11px;color:#1e3a5f">樣本 &lt; 5 顯示「樣本不足」</div>
  </div>`;
}}
function afterThreshold(){{
  const ts=STATS.threshold_stats||[];
  const rules=[...new Set(ts.map(x=>x.rule))];
  const t5data=rules.map(r=>{{const x=ts.find(t=>t.rule===r&&t.horizon===5);return x?Number(x.win_rate):0;}});
  const t5colors=t5data.map(v=>v>=65?'rgba(248,113,113,.7)':v>=55?'rgba(251,191,36,.7)':v>=45?'rgba(74,222,128,.7)':'rgba(100,116,139,.4)');
  _mkChart('chartThresh',{{type:'bar',data:{{labels:rules,datasets:[{{label:'T+5 勝率',data:t5data,backgroundColor:t5colors,borderRadius:4,borderSkipped:false}}]}},
    options:{{indexAxis:'y',responsive:true,maintainAspectRatio:false,plugins:{{..._NO_LEGEND,tooltip:{{callbacks:{{label:ctx=>`${{ctx.parsed.x.toFixed(1)}}%`}}}}}},
    scales:{{x:{{..._BASE_SCALE.x,min:0,max:100,ticks:{{color:'#334155',callback:v=>v+'%'}}}},y:{{..._BASE_SCALE.y,ticks:{{color:'#64748b',font:{{size:10}}}}}}}}}}}});
}}

// ── Tab: 分數熱圖 ──────────────────────────────────────────────
const K_BUCKETS=[{{k:'A_78UP',lbl:'K ≥78'}},{{k:'B_70_77',lbl:'K 70~77'}},{{k:'C_60_69',lbl:'K 60~69'}},{{k:'D_LT60',lbl:'K <60'}}];
const C_BUCKETS=[{{k:'A_88UP',lbl:'綜 ≥88'}},{{k:'B_75_87',lbl:'綜 75~87'}},{{k:'C_60_74',lbl:'綜 60~74'}},{{k:'D_LT60',lbl:'綜 <60'}}];
function hmColor(v,mode){{
  if(v==null)return{{bg:'#06111e',txt:'#1e3a5f'}};
  if(mode==='wr'){{
    if(v>=60)return{{bg:'rgba(127,29,29,.9)',txt:'#fca5a5'}};
    if(v>=50)return{{bg:'rgba(120,53,15,.85)',txt:'#fde68a'}};
    if(v>=40)return{{bg:'rgba(20,83,45,.8)',txt:'#86efac'}};
    return{{bg:'rgba(15,32,64,.7)',txt:'#334155'}};
  }}else{{
    if(v>=3)return{{bg:'rgba(127,29,29,.8)',txt:'#fca5a5'}};
    if(v>=0)return{{bg:'rgba(20,83,45,.7)',txt:'#86efac'}};
    return{{bg:'rgba(90,10,10,.7)',txt:'#f87171'}};
  }}
}}
function buildTabMatrix(){{
  const mat=(STATS.summary||[]).filter(x=>x.group_name==='score_matrix'&&x.horizon===5);
  function mkHM(mode){{
    let html=`<div class="hm-grid"><div class="hm-hdr" style="text-align:left;font-size:10px;color:#334155">K ↓ / 綜 →</div>${{C_BUCKETS.map(c=>`<div class="hm-hdr">${{c.lbl}}</div>`).join('')}}`;
    K_BUCKETS.forEach(kb=>{{
      html+=`<div class="hm-row-lbl">${{kb.lbl}}</div>`;
      C_BUCKETS.forEach(cb=>{{
        const cell=mat.find(m=>m.kline_bucket===kb.k&&m.composite_bucket===cb.k);
        const val=cell?(mode==='wr'?cell.win_rate:cell.avg_return):null;
        const {{bg,txt}}=hmColor(val,mode);
        const disp=val!=null?(mode==='wr'?Number(val).toFixed(0)+'%':(val>=0?'+':'')+Number(val).toFixed(1)+'%'):'—';
        const sub=cell?`n=${{cell.sample_count}}`:'';
        html+=`<div class="hm-cell" style="background:${{bg}};color:${{txt}}" title="${{sub}}">${{disp}}<div style="font-size:9px;opacity:.5;margin-top:2px">${{sub}}</div></div>`;
      }});
    }});
    return html+'</div>';
  }}
  return`
  <div class="sc-grid">
    <div class="sc-box">
      <div class="sc-title">T+5 勝率熱圖 &nbsp;紅≥60% 橘≥50% 綠≥40%</div>
      ${{mkHM('wr')}}
    </div>
    <div class="sc-box">
      <div class="sc-title">T+5 平均報酬熱圖</div>
      ${{mkHM('ar')}}
    </div>
  </div>
  <div class="sc-grid sc-wide" style="padding-top:0">
    <div class="sc-box">
      <div class="sc-title">各 K線分區間 T+1~T+10 勝率走勢</div>
      <div style="position:relative;height:180px"><canvas id="chartMatLine"></canvas></div>
    </div>
  </div>`;
}}
function afterMatrix(){{
  const summary=STATS.summary||[];
  const kLabels=K_BUCKETS.map(b=>b.lbl);
  const kColors=['#f87171','#fbbf24','#4ade80','#475569'];
  const datasets=K_BUCKETS.map((kb,i)=>{{
    const pts=[1,3,5,7,10].map(h=>{{
      const cell=(STATS.summary||[]).find(m=>m.group_name==='score_matrix'&&m.kline_bucket===kb.k&&m.composite_bucket==='A_88UP'&&m.horizon===h);
      return cell?Number(cell.win_rate):null;
    }});
    return{{label:kb.lbl,data:pts,borderColor:kColors[i],backgroundColor:'transparent',tension:.3,pointRadius:4,pointBackgroundColor:kColors[i]}};
  }});
  _mkChart('chartMatLine',{{type:'line',data:{{labels:['T+1','T+3','T+5','T+7','T+10'],datasets}},
    options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{labels:{{color:'#64748b',boxWidth:10,font:{{size:10}}}}}}}},
    scales:{{x:{{..._BASE_SCALE.x}},y:{{..._BASE_SCALE.y,ticks:{{color:'#334155',callback:v=>v+'%'}},title:{{display:true,text:'勝率 %',color:'#334155',font:{{size:10}}}}}}}}}}}});
}}

// ── Tab: 近期訊號 ──────────────────────────────────────────────
let _sigSort='trade_date',_sigAsc=false;
function buildTabRecent(){{
  return`
  <div class="sc-grid" style="padding-bottom:0">
    <div class="sc-box">
      <div class="sc-title">T+5 報酬分布</div>
      <div style="position:relative;height:160px"><canvas id="chartRetDist"></canvas></div>
    </div>
    <div class="sc-box">
      <div class="sc-title">各訊號 T+5 勝率對比</div>
      <div style="position:relative;height:160px"><canvas id="chartEvWR"></canvas></div>
    </div>
  </div>
  <div class="stats-tools">
    <input type="text" id="statsQ" placeholder="代號 / 名稱" oninput="renderRecentStats()">
    <select id="statsEt" onchange="renderRecentStats()">
      <option value="">全部訊號</option>
      <option value="BOTH_STRONG">雙強</option><option value="ENTRY">雙分進場</option>
      <option value="K_STRONG_COMP_LOW">K強綜低</option><option value="COMP_STRONG_K_LOW">綜強K低</option>
      <option value="COMP_HIGH_K_LOW">綜高K低</option>
    </select>
    <div class="stats-sl-grp"><span class="stats-sl-lbl">K線≥</span><input type="range" id="statsK" min="0" max="100" step="1" value="0" oninput="updSlider2(this,'statsKv');renderRecentStats()" style="width:90px;"><span class="sl-val2" id="statsKv">0</span></div>
    <div class="stats-sl-grp"><span class="stats-sl-lbl">綜合≥</span><input type="range" id="statsC" min="0" max="100" step="1" value="0" oninput="updSlider2(this,'statsCv');renderRecentStats()" style="width:90px;"><span class="sl-val2" id="statsCv">0</span></div>
    <span style="font-size:11px;color:#334155">顯示 <b id="recentStatsCount" style="color:#64748b">0</b> 筆</span>
  </div>
  <div class="stats-scroll">
    <table class="stats-table">
      <thead><tr>
        <th class="stats-sort" onclick="statsSortBy('trade_date')">日期</th>
        <th class="stats-sort" onclick="statsSortBy('ticker')">代號</th><th>名稱</th>
        <th class="stats-sort" onclick="statsSortBy('event_type')">訊號</th>
        <th class="stats-sort" onclick="statsSortBy('kline_score')">K線分</th>
        <th class="stats-sort" onclick="statsSortBy('composite_score')">綜合分</th>
        <th>買進收盤</th>
        <th class="stats-sort" onclick="statsSortBy('t1_return')">T+1</th>
        <th class="stats-sort" onclick="statsSortBy('t3_return')">T+3</th>
        <th class="stats-sort" onclick="statsSortBy('t5_return')">T+5</th>
        <th class="stats-sort" onclick="statsSortBy('t7_return')">T+7</th>
        <th class="stats-sort" onclick="statsSortBy('t10_return')">T+10</th>
        <th>狀態</th>
      </tr></thead>
      <tbody id="recentStatsBody"><tr><td colspan="13" style="padding:16px;color:#334155">載入中...</td></tr></tbody>
    </table>
  </div>`;
}}
function afterRecent(){{
  // Dist histogram
  const all=(STATS.recent||[]).filter(r=>r.t5_return!=null).map(r=>Number(r.t5_return));
  const bins=[-8,-6,-4,-2,0,2,4,6,8,10];
  const bLabels=bins.slice(0,-1).map((b,i)=>`${{b}}~${{bins[i+1]}}%`);
  const bCounts=bins.slice(0,-1).map((b,i)=>all.filter(v=>v>=b&&v<bins[i+1]).length);
  const bColors=bins.slice(0,-1).map(b=>b>=0?'rgba(74,222,128,.7)':'rgba(248,113,113,.7)');
  _mkChart('chartRetDist',{{type:'bar',data:{{labels:bLabels,datasets:[{{data:bCounts,backgroundColor:bColors,borderRadius:4,borderSkipped:false}}]}},
    options:{{responsive:true,maintainAspectRatio:false,plugins:{{..._NO_LEGEND,tooltip:{{callbacks:{{label:ctx=>`${{ctx.parsed.y}} 筆`}}}}}},scales:{{x:{{..._BASE_SCALE.x,ticks:{{color:'#334155',font:{{size:9}}}}}},y:{{..._BASE_SCALE.y,ticks:{{color:'#334155'}}}}}}}}}});

  // Event T+5 WR bar
  const evTypes=[...new Set((STATS.summary||[]).filter(x=>x.group_name==='event_type').map(x=>x.event_type))];
  const evWR=evTypes.map(et=>{{const x=(STATS.summary||[]).find(s=>s.group_name==='event_type'&&s.event_type===et&&s.horizon===5);return x?Number(x.win_rate):null;}});
  const evColors=evWR.map((v,i)=>ET_COLORS[i%ET_COLORS.length]);
  _mkChart('chartEvWR',{{type:'bar',data:{{labels:evTypes.map(e=>labelEvent(e)),datasets:[{{data:evWR,backgroundColor:evColors,borderRadius:4,borderSkipped:false}}]}},
    options:{{responsive:true,maintainAspectRatio:false,plugins:{{..._NO_LEGEND,tooltip:{{callbacks:{{label:ctx=>`${{ctx.parsed.y?.toFixed(1)}}%`}}}}}},
    scales:{{x:{{..._BASE_SCALE.x,ticks:{{color:'#334155',font:{{size:9}}}}}},y:{{..._BASE_SCALE.y,min:0,max:100,ticks:{{color:'#334155',callback:v=>v+'%'}}}}}}}}}});

  renderRecentStats();
}}
function statsSortBy(k){{if(_sigSort===k)_sigAsc=!_sigAsc;else{{_sigSort=k;_sigAsc=false;}}renderRecentStats();}}
function renderRecentStats(){{
  const host=document.getElementById('recentStatsBody');if(!host)return;
  const q=((document.getElementById('statsQ')||{{}}).value||'').toLowerCase();
  const kMin=Number((document.getElementById('statsK')||{{value:0}}).value||0);
  const cMin=Number((document.getElementById('statsC')||{{value:0}}).value||0);
  const etFilt=(document.getElementById('statsEt')||{{}}).value||'';
  let data=(STATS.recent||[]).filter(r=>{{
    if(q&&!(String(r.ticker||'').toLowerCase().includes(q)||String(r.name||'').toLowerCase().includes(q)))return false;
    if((r.kline_score||0)<kMin)return false;
    if((r.composite_score||0)<cMin)return false;
    if(etFilt&&r.event_type!==etFilt)return false;
    return true;
  }});
  data=[...data].sort((a,b)=>{{
    const av=statNum(a[_sigSort])??a[_sigSort]??'';
    const bv=statNum(b[_sigSort])??b[_sigSort]??'';
    if(av===bv)return 0;if(av==='')return 1;if(bv==='')return -1;
    if(typeof av==='number'&&typeof bv==='number')return _sigAsc?av-bv:bv-av;
    return _sigAsc?String(av).localeCompare(String(bv)):String(bv).localeCompare(String(av));
  }});
  const stBadge=s=>{{
    const cls=s==='matured'?'st-matured':s==='partial'?'st-partial':'st-open';
    return`<span class="${{cls}}">${{s||'-'}}</span>`;
  }};
  host.innerHTML=data.map(r=>`<tr>
    <td style="color:#334155">${{r.trade_date||'-'}}</td>
    <td style="color:#6366f1;font-weight:700">${{r.ticker||'-'}}</td>
    <td>${{r.name||'-'}}</td>
    <td style="color:#93c5fd;font-size:11px">${{labelEvent(r.event_type)}}</td>
    <td><div class="sc-bar"><div class="sc-track"><div class="sc-fill" style="width:${{Math.min(r.kline_score||0,100)}}%;background:${{kCol(r.kline_score||0)}}"></div></div><span style="color:${{kCol(r.kline_score||0)}};font-weight:700">${{r.kline_score!=null?Math.round(r.kline_score):'-'}}</span></div></td>
    <td><div class="sc-bar"><div class="sc-track"><div class="sc-fill" style="width:${{Math.min(r.composite_score||0,100)}}%;background:${{cCol(r.composite_score||0)}}"></div></div><span style="color:${{cCol(r.composite_score||0)}};font-weight:700">${{r.composite_score!=null?Math.round(r.composite_score):'-'}}</span></div></td>
    <td style="color:#64748b">${{r.entry_reference_close!=null?Number(r.entry_reference_close).toFixed(1):'-'}}</td>
    <td>${{statCell(r.t1_return)}}</td><td>${{statCell(r.t3_return)}}</td>
    <td>${{statCell(r.t5_return)}}</td><td>${{statCell(r.t7_return)}}</td><td>${{statCell(r.t10_return)}}</td>
    <td>${{stBadge(r.status)}}</td>
  </tr>`).join('')||'<tr><td colspan="13" style="text-align:center;padding:16px;color:#1e3a5f">沒有符合篩選的訊號</td></tr>';
  const cnt=document.getElementById('recentStatsCount');if(cnt)cnt.textContent=data.length;
  schedResize();
}}

// ── Tab: 黃金出場 ──────────────────────────────────────────────
function buildTabPeak(){{
  const sum=STATS.summary||[];
  const evTypes=[...new Set(sum.filter(x=>x.group_name==='event_type').map(x=>x.event_type))];
  if(!evTypes.length)return'<div style="padding:20px;color:#334155">尚無資料。</div>';
  const hs=[1,3,5,7,10];
  const rows=evTypes.map((et,ei)=>{{
    const pts=hs.map(h=>{{const x=sum.find(s=>s.group_name==='event_type'&&s.event_type===et&&s.horizon===h);return x?{{h,wr:Number(x.win_rate),ar:Number(x.avg_return),n:Number(x.sample_count)}}:null;}}).filter(Boolean);
    if(!pts.length)return'';
    const best=pts.reduce((a,b)=>b.wr>a.wr?b:a);
    const bestAr=pts.reduce((a,b)=>b.ar>a.ar?b:a);
    const wrCells=hs.map(h=>{{
      const p=pts.find(x=>x.h===h);
      if(!p)return`<td style="text-align:center;color:#1e3a5f">—</td>`;
      const hi=p.h===best.h;
      return`<td style="text-align:center;color:${{hi?'#fbbf24':'#475569'}};font-weight:${{hi?700:400}}">${{p.wr.toFixed(1)}}%${{hi?' 🏆':''}}</td>`;
    }});
    return`<tr><td style="color:#93c5fd;font-weight:600">${{labelEvent(et)}}</td><td style="text-align:center;color:#334155">${{pts[0]?.n||0}}</td>${{wrCells.join('')}}<td><span class="peak-badge">T+${{best.h}}</span></td><td class="pos" style="text-align:center">${{bestAr.ar>=0?'+':''}}${{bestAr.ar.toFixed(2)}}%</td></tr>`;
  }}).filter(Boolean);

  return`
  <div class="sc-grid sc-wide" style="padding-bottom:0">
    <div class="sc-box">
      <div class="sc-title">各訊號類型 T+1~T+10 勝率對比（Grouped Bar）</div>
      <div style="position:relative;height:200px"><canvas id="chartPeak"></canvas></div>
    </div>
  </div>
  <div style="padding:8px 14px 4px;font-size:11px;color:#334155">🏆 = 該訊號歷史上勝率最高的持有天數</div>
  <div class="stats-scroll" style="padding:0 0 14px">
    <table class="stats-table"><thead><tr><th>訊號類型</th><th style="text-align:center">樣本</th>
      ${{hs.map(h=>`<th style="text-align:center">T+${{h}}</th>`).join('')}}
      <th>黃金出場</th><th style="text-align:center">最佳報酬</th></tr></thead>
    <tbody>${{rows.join('')}}</tbody></table>
  </div>`;
}}
function afterPeak(){{
  const sum=STATS.summary||[];
  const evTypes=[...new Set(sum.filter(x=>x.group_name==='event_type').map(x=>x.event_type))];
  const hs=[1,3,5,7,10];
  const hLabels=hs.map(h=>`T+${{h}}`);
  const datasets=evTypes.map((et,i)=>{{
    const data=hs.map(h=>{{const x=sum.find(s=>s.group_name==='event_type'&&s.event_type===et&&s.horizon===h);return x?Number(x.win_rate):null;}});
    return{{label:labelEvent(et),data,backgroundColor:ET_COLORS[i%ET_COLORS.length]+'bb',borderRadius:3,borderSkipped:false}};
  }});
  _mkChart('chartPeak',{{type:'bar',data:{{labels:hLabels,datasets}},
    options:{{responsive:true,maintainAspectRatio:false,
    plugins:{{legend:{{labels:{{color:'#475569',boxWidth:10,font:{{size:10}}}},position:'bottom'}},
      tooltip:{{callbacks:{{label:ctx=>`${{ctx.dataset.label}}: ${{ctx.parsed.y?.toFixed(1)}}%`}}}}}},
    scales:{{x:{{..._BASE_SCALE.x,ticks:{{color:'#334155'}}}},y:{{..._BASE_SCALE.y,min:0,max:100,ticks:{{color:'#334155',callback:v=>v+'%'}}}}}}}}}});
}}

// ── Tab: 常勝標的 ──────────────────────────────────────────────
function buildTabHot(){{
  const recent=STATS.recent||[];if(!recent.length)return'<div style="padding:20px;color:#334155">尚無資料。</div>';
  const freq={{}};
  recent.forEach(r=>{{
    const key=r.ticker+'|'+(r.name||r.ticker);
    if(!freq[key])freq[key]={{ticker:r.ticker,name:r.name||r.ticker,cnt:0,wins:0,total:0,retSum:0,lastDate:r.trade_date,types:new Set()}};
    freq[key].cnt++;freq[key].types.add(r.event_type);
    if(r.t5_return!=null){{freq[key].total++;freq[key].retSum+=Number(r.t5_return);if(Number(r.t5_return)>0)freq[key].wins++;}}
    if((r.trade_date||'')>(freq[key].lastDate||''))freq[key].lastDate=r.trade_date;
  }});
  const sorted=Object.values(freq).sort((a,b)=>b.cnt-a.cnt).slice(0,15);
  const maxCnt=sorted[0]?.cnt||1;
  const rows=sorted.map((x,i)=>{{
    const wr=x.total>=3?(x.wins/x.total*100):null;
    const ar=x.total>=3?(x.retSum/x.total):null;
    const wrCls=wr!=null&&wr>=50?'pos':'neg';
    const arCls=ar!=null&&ar>=0?'pos':'neg';
    return`<tr>
      <td style="text-align:center;color:#334155">${{i+1}}</td>
      <td style="color:#6366f1;font-weight:700">${{x.ticker}}</td><td style="color:#94a3b8">${{x.name}}</td>
      <td><div class="strat-bar"><div class="strat-bg"><div class="strat-fill" style="width:${{Math.round(x.cnt/maxCnt*100)}}%"></div></div><span style="font-size:12px;color:#818cf8;font-weight:700">${{x.cnt}}</span></div></td>
      <td style="color:#334155;font-size:11px">${{[...x.types].map(t=>labelEvent(t)).join(', ')}}</td>
      <td class="${{wrCls}}" style="text-align:center">${{wr!=null?wr.toFixed(0)+'%':'—'}}</td>
      <td class="${{arCls}}" style="text-align:center">${{ar!=null?(ar>=0?'+':'')+ar.toFixed(2)+'%':'—'}}</td>
      <td style="color:#334155;font-size:11px">${{x.lastDate||''}}</td>
    </tr>`;
  }});
  return`<div style="padding:10px 14px 6px;font-size:11px;color:#334155">近期被系統選中最多次的標的（T+5 樣本≥3筆才顯示勝率）</div>
  <div class="stats-scroll">
  <table class="stats-table"><thead><tr><th style="text-align:center">#</th><th>代號</th><th>名稱</th><th>出現次數</th><th>訊號類型</th><th style="text-align:center">T+5 勝率</th><th style="text-align:center">T+5 平均報酬</th><th>最後出現</th></tr></thead>
  <tbody>${{rows.join('')}}</tbody></table></div>`;
}}

// ── Main renderStats ──────────────────────────────────────────
function renderStats(){{
  _cjsDefaults();
  const box=document.getElementById('statsContent');
  if(!STATS||!STATS.ready){{
    box.innerHTML='<div class="stats-empty">尚未建立 data/stats.db。GitHub Actions 跑完一次後會開始累積統計。</div>';
    return;
  }}
  const c=STATS.counts||{{}};
  const matured=(STATS.recent||[]).filter(r=>r.t5_return!=null);
  const wr5=matured.length?((matured.filter(r=>r.t5_return>0).length/matured.length)*100).toFixed(1):null;

  document.getElementById('statsHeadNote').textContent=`訊號 ${{c.events||0}} 筆 · 績效 ${{c.outcomes||0}} 筆 · 觀察 ${{c.watches||0}} 筆`;

  box.className='';
  box.innerHTML=`
    <div class="stats-kpi-row">
      <div class="stat-card sk1"><div class="stat-k">每日快照</div><div class="stat-v">${{(c.snapshots||0).toLocaleString()}}</div><div class="stat-sub">累積筆數</div></div>
      <div class="stat-card sk2"><div class="stat-k">訊號事件</div><div class="stat-v">${{(c.events||0).toLocaleString()}}</div><div class="stat-sub">已記錄</div></div>
      <div class="stat-card sk3"><div class="stat-k">T+5 整體勝率</div><div class="stat-v" style="color:${{wr5>=50?'#f87171':'#4ade80'}}">${{wr5!=null?wr5+'%':'—'}}</div><div class="stat-sub">樣本 ${{matured.length}} 筆</div></div>
      <div class="stat-card sk4"><div class="stat-k">觀察追蹤</div><div class="stat-v">${{(c.watches||0).toLocaleString()}}</div><div class="stat-sub">待確認</div></div>
    </div>
    <div class="stats-inner-tabs">
      <button class="sit sa" onclick="switchStatsTab('sth',this)">🎯 門檻分析</button>
      <button class="sit" onclick="switchStatsTab('smat',this)">🔥 分數熱圖</button>
      <button class="sit" onclick="switchStatsTab('srec',this)">📋 近期訊號</button>
      <button class="sit" onclick="switchStatsTab('speak',this)">🏆 黃金出場</button>
      <button class="sit" onclick="switchStatsTab('shot',this)">⚡ 常勝標的</button>
    </div>
    <div class="sp sa" id="sth">${{buildTabThreshold()}}</div>
    <div class="sp" id="smat">${{buildTabMatrix()}}</div>
    <div class="sp" id="srec">${{buildTabRecent()}}</div>
    <div class="sp" id="speak">${{buildTabPeak()}}</div>
    <div class="sp" id="shot">${{buildTabHot()}}</div>`;

  // Init visible tab charts
  setTimeout(()=>{{afterThreshold();schedResize();}},50);
}}

function switchStatsTab(id,btn){{
  document.querySelectorAll('.sp').forEach(p=>p.classList.toggle('sa',p.id===id));
  document.querySelectorAll('.sit').forEach(b=>b.classList.toggle('sa',b===btn));
  setTimeout(()=>{{
    if(id==='sth')afterThreshold();
    if(id==='smat')afterMatrix();
    if(id==='srec'){{afterRecent();}}
    if(id==='speak')afterPeak();
    schedResize();
  }},30);
}}

function updSlider2(el,vidId){{
  const p=(el.value/el.max)*100;el.style.setProperty('--pct',p+'%');
  document.getElementById(vidId).textContent=el.value;
}}

function updSlider(el,vidId){{
  const pct=(el.value/el.max)*100;
  el.style.setProperty('--pct',pct+'%');
  document.getElementById(vidId).textContent=el.value;
}}

function renderRows(data){{
  const tb=document.getElementById('tBody');
  if(!data.length){{
    tb.innerHTML='<tr><td colspan="12" style="text-align:center;padding:48px;color:#64748b;font-size:13px">沒有符合條件的股票</td></tr>';
    return;
  }}
  // 🎯 已套用超連結對調：代號連到 kline_url，名稱連到 yahoo_url
  tb.innerHTML=data.map(r=>`<tr>
    <td><a class="tk" href="${{r.kline_url}}" target="_blank">${{r.ticker}}</a></td>
    <td><a class="nm" href="${{r.yahoo_url}}" target="_blank">${{r.name}}</a></td>
    <td><span class="pv">${{r.price!=null?r.price.toFixed(1):'—'}}</span></td>
    <td>${{fChg(r.chg)}}</td>
    <td>${{bar(r.kline,kc,r.kline_url)}}</td>
    <td>${{bar(r.comp,cc)}}</td>
    <td>${{fVol(r.vol)}}</td>
    <td>${{fRsi(r.rsi)}}</td>
    <td>${{fRs(r.rs5d)}}</td>
    <td>${{fInst(r.inst)}}</td>
    <td>${{fSig(r.signal)}}</td>
    <td>${{fPat(r.patterns)}}</td>
  </tr>`).join('');
}}

function applyFilter(){{
  const q=(document.getElementById('searchQ').value||'').toLowerCase();
  const kMin=+document.getElementById('slK').value;
  const cMin=+document.getElementById('slC').value;
  let data=RAW.filter(r=>{{
    if(r.kline==null&&r.vol==null)return false;
    if((r.kline||0)<kMin)return false;
    if((r.comp||0)<cMin)return false;
    if(q&&!r.ticker.toLowerCase().includes(q)&&!r.name.toLowerCase().includes(q))return false;
    return true;
  }});
  data=[...data].sort((a,b)=>{{
    const av=a[sortKey],bv=b[sortKey];
    const na=av==null,nb=bv==null;
    if(na&&nb)return 0;if(na)return 1;if(nb)return-1;
    return sortAsc?av-bv:bv-av;
  }});
  document.getElementById('statLine').innerHTML=`顯示 <b>${{data.length}}</b> / {total} 檔`;
  renderRows(data);
  schedResize();
}}

// 表頭排序
document.querySelectorAll('thead th.sortable').forEach(th=>{{
  th.addEventListener('click',()=>{{
    const k=th.dataset.k;
    if(sortKey===k)sortAsc=!sortAsc;
    else{{sortKey=k;sortAsc=false;}}
    document.querySelectorAll('thead th').forEach(t=>t.classList.remove('asc','desc'));
    th.classList.add(sortAsc?'asc':'desc');
    applyFilter();
  }});
}});

function toggleLegend(){{
  const b=document.getElementById('legBody');
  const a=document.getElementById('legArr');
  if(b.classList.contains('open')){{b.classList.remove('open');a.textContent='▶';}}
  else{{b.classList.add('open');a.textContent='▼';schedResize();}}
}}

// ── 強化版應用程式初始化模組 ──
function initApp() {{
  try {{
    // 1. 強制重置拉條數值，清除瀏覽器的 Form Auto-restore 快取
    ['slK', 'slC'].forEach(id => {{
      const el = document.getElementById(id);
      if (el) {{
        el.value = 0; // 強制將真實數值歸零
        el.style.setProperty('--pct', '0%');
      }}
    }});
    // 強制重置旁邊顯示的數字
    document.getElementById('kvK').textContent = '0';
    document.getElementById('kvC').textContent = '0';

    // 2. 設定預設排序
    const thKline = document.querySelector('th[data-k="kline"]');
    if (thKline) thKline.classList.add('desc');

    // 3. 獨立執行統計面板
    try {{ 
      renderStats(); 
    }} catch(e) {{ 
      console.error("統計面板初始化略過:", e); 
    }}

    // 4. 初始化統計面板的滑桿漸層百分比
    ['statsK', 'statsC'].forEach(id => {{
      const el = document.getElementById(id);
      if (el) el.style.setProperty('--pct', '0%');
    }});

    // 5. 執行最終過濾與渲染
    applyFilter();
    
  }} catch (err) {{
    console.error("首頁初始化錯誤:", err);
    const statLine = document.getElementById('statLine');
    if (statLine) statLine.innerHTML = '載入完成，若無資料請重新調整拉條。';
  }}
}}

// 確保 DOM 與 Streamlit iframe 完全就緒後再啟動
if (document.readyState === 'loading') {{
  document.addEventListener('DOMContentLoaded', initApp);
}} else {{
  initApp();
}}

// ResizeObserver
let raf=0;
function schedResize(){{
  if(raf)cancelAnimationFrame(raf);
  raf=requestAnimationFrame(()=>{{
    const h=Math.ceil(Math.max(document.body.scrollHeight,document.documentElement.scrollHeight))+4;
    window.parent.postMessage({{isStreamlitMessage:true,type:'streamlit:setFrameHeight',height:h}},'*');
  }});
}}
if(window.ResizeObserver)new ResizeObserver(schedResize).observe(document.body);
window.addEventListener('load',schedResize);
setTimeout(schedResize,150);setTimeout(schedResize,700);setTimeout(schedResize,1500);setTimeout(schedResize,3000);
</script>
</body></html>"""

    est = 520 + len(rows)*40
    components.html(page, height=est, scrolling=False)

if __name__=="__main__":
    main()
