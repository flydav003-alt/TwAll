"""
streamlit_app.py — 台股半導體 Screener
架構：components.html() + JS 排序/篩選，scrolling=False + ResizeObserver
配色：對標圖1深藍黑風格
"""
import json, os, requests, streamlit as st
import streamlit.components.v1 as components
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from tw_screener_core import calc_composite_tw, detect_patterns_tw, yahoo_tw_url

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

# ── FinMind 2h 快取 ───────────────────────────────────────────
def _get_token():
    try: return st.secrets["FINMIND_TOKEN"]
    except: return ""

@st.cache_data(ttl=7200, show_spinner=False)
def _finmind_inst(stock_id, token, days=10):
    if not token: return 0
    end   = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now()-timedelta(days=days+10)).strftime("%Y-%m-%d")
    try:
        r = requests.get(
            "https://api.finmindtrade.com/api/v4/data",
            params=dict(dataset="TaiwanStockInstitutionalInvestorsBuySell",
                        data_id=stock_id, start_date=start, end_date=end, token=token),
            timeout=10)
        d = r.json()
        if d.get("status")!=200: return 0
        daily={}
        for rec in d.get("data",[]):
            nm=rec.get("name","")
            if nm in ("外資","外資自營商","Foreign_Investor","投信","Investment_Trust"):
                daily.setdefault(rec["date"],0)
                daily[rec["date"]]+=rec.get("buy",0)-rec.get("sell",0)
        dates=sorted(daily,reverse=True)[:days]
        if not dates: return 0
        sign=1 if daily[dates[0]]>=0 else -1
        cnt=0
        for dt in dates:
            if (1 if daily[dt]>=0 else -1)==sign: cnt+=1
            else: break
        return sign*cnt
    except: return 0

def _fetch_all_finmind(tickers, token):
    res={}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs={ex.submit(_finmind_inst,sid,token):sid for sid in tickers}
        for f in as_completed(futs):
            sid=futs[f]
            try: res[sid]=f.result()
            except: res[sid]=0
    return res

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

def main():
    stocks, generated_at = _load_stocks()
    mkt = _load_market()
    if stocks is None:
        st.error("⚠️ 找不到 data/screener_data.json，請先執行 GitHub Actions。")
        st.stop()

    token = _get_token()
    if token:
        with st.spinner("📡 抓取法人資料（2h 快取）…"):
            inst_map = _fetch_all_finmind([s["ticker"] for s in stocks], token)
    else:
        inst_map = {}

    SIG_RANK = {"💥突破放量":4,"🚀主力進場":3,"✅洗盤結束":2,"📉量縮整理":1,"":0}
    PAT_RANK = {"pat-a":3,"pat-b":2,"pat-c":1}

    rows = []
    for s in stocks:
        s["inst_buy_days"] = inst_map.get(s["ticker"], s.get("inst_buy_days",0))
        s["composite"]     = calc_composite_tw(s)
        s["patterns"]      = detect_patterns_tw(s)
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
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Noto+Sans+TC:wght@400;500;700&display=swap" rel="stylesheet">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
:root{{
  --bg:      #F2F0E8;
  --bg2:     #EAE7DC;
  --bg3:     #E2DED2;
  --row-odd: #F0EDE4;
  --row-evn: #EAE7DC;
  --hov:     #E4DFCF;
  --bdr:     rgba(26,24,16,0.10);
  --bdr2:    rgba(255,46,106,0.30);
  --txt:     #1A1810;
  --dim:     #9A9888;
  --mid:     #6A6858;
  --hd:      #4A4840;
  --acc:     #FF2E6A;
  --red:     #FF2E6A;
  --grn:     #1A8A3A;
  --orn:     #FF6B1A;
  --yel:     #6B3FFF;
  --sans:    'Inter','Noto Sans TC',sans-serif;
}}
body{{
  background:var(--bg);
  background-image:
    radial-gradient(ellipse 70% 45% at 50% -5%, rgba(255,208,0,0.13) 0%, transparent 65%),
    radial-gradient(ellipse 35% 50% at 95% 40%, rgba(107,63,255,0.07) 0%, transparent 55%),
    radial-gradient(ellipse 28px 28px at 0 0, transparent 1px, transparent 1px);
  color:var(--txt);font-family:var(--sans);font-size:13px;
}}

/* ── HEADER ── */
.hd{{
  display:flex;align-items:baseline;gap:14px;
  padding:16px 28px 12px;
  border-bottom:3px solid var(--acc);
  background:var(--bg);
  position:relative;
}}
.hd::after{{
  content:'';position:absolute;bottom:-3px;left:0;
  width:80px;height:3px;background:#6B3FFF;
}}
.hd-t{{font-size:18px;font-weight:700;color:var(--txt);letter-spacing:-.02em;}}
.hd-t span{{color:var(--acc);}}
.hd-m{{font-size:11px;color:var(--dim);}}

/* ── MARKET BAR ── */
.mkt{{
  display:flex;border-bottom:1px solid var(--bdr);
  background:var(--bg2);
}}
.mi{{
  padding:10px 24px;border-right:1px solid var(--bdr);
  display:flex;flex-direction:column;gap:3px;
}}
.mi:last-child{{border-right:none;}}
.mi-l{{font-size:9.5px;color:var(--dim);letter-spacing:.10em;text-transform:uppercase;font-weight:600;}}
.mi-v{{font-size:17px;font-weight:700;color:var(--txt);}}
.pos{{color:var(--red)}} .neg{{color:var(--grn)}} .neu{{color:var(--mid)}}

/* ── FILTER BAR ── */
.fb{{
  display:flex;align-items:center;gap:20px;flex-wrap:nowrap;
  padding:12px 28px;background:var(--bg2);border-bottom:1px solid var(--bdr);
}}
/* 搜尋框 */
.srch{{
  position:relative;display:flex;align-items:center;
}}
.srch-ico{{position:absolute;left:11px;color:var(--dim);font-size:14px;pointer-events:none;}}
.srch input{{
  background:var(--bg);border:1px solid rgba(26,24,16,0.20);color:var(--txt);
  border-radius:7px;padding:8px 12px 8px 32px;
  font-size:13px;font-family:var(--sans);width:210px;outline:none;
  transition:border .15s,box-shadow .15s;
}}
.srch input:focus{{border-color:var(--acc);box-shadow:0 0 0 3px rgba(255,46,106,0.12);}}
.srch input::placeholder{{color:var(--dim);}}

/* 滑桿群組 */
.sl-grp{{display:flex;align-items:center;gap:10px;}}
.sl-lbl{{font-size:12px;color:var(--mid);white-space:nowrap;font-weight:600;}}
/* 粗滑桿 */
input[type=range]{{
  -webkit-appearance:none;width:140px;height:7px;
  background:linear-gradient(to right, var(--acc) 0%, var(--acc) var(--pct,0%), rgba(26,24,16,0.12) var(--pct,0%), rgba(26,24,16,0.12) 100%);
  border-radius:4px;outline:none;cursor:pointer;border:none;
}}
input[type=range]::-webkit-slider-thumb{{
  -webkit-appearance:none;width:18px;height:18px;
  border-radius:50%;background:var(--acc);
  border:2px solid #fff;cursor:pointer;
  box-shadow:0 2px 8px rgba(255,46,106,0.40);
  transition:box-shadow .15s;
}}
input[type=range]::-webkit-slider-thumb:hover{{box-shadow:0 2px 14px rgba(255,46,106,0.65);}}
.sl-val{{
  font-size:14px;font-weight:700;color:var(--acc);
  min-width:26px;text-align:center;
  background:var(--bg);border:1px solid rgba(255,46,106,0.25);
  border-radius:5px;padding:3px 7px;
}}

/* ── STAT LINE ── */
.stat{{padding:7px 28px;font-size:11px;color:var(--dim);border-bottom:1px solid var(--bdr);background:var(--bg);}}
.stat b{{color:var(--acc);font-weight:700;}}

/* ── TABLE ── */
.tbl-wrap{{padding:0 28px 28px;}}
table{{width:100%;border-collapse:collapse;margin-top:4px;}}
thead th{{
  background:var(--bg2);color:var(--hd);
  font-size:10px;font-weight:700;letter-spacing:.10em;text-transform:uppercase;
  padding:9px 14px;text-align:left;
  border-bottom:2px solid rgba(255,46,106,0.35);
  white-space:nowrap;user-select:none;
  position:sticky;top:0;z-index:5;
}}
thead th.sortable{{cursor:pointer;}}
thead th.sortable:hover{{color:var(--acc);background:var(--hov);}}
.arr{{margin-left:4px;font-size:9px;opacity:.35;}}
th.asc  .arr{{opacity:1;color:var(--acc);}}
th.desc .arr{{opacity:1;color:var(--acc);}}
th.asc  .arr::after{{content:'▲'}}
th.desc .arr::after{{content:'▼'}}
th:not(.asc):not(.desc) .arr::after{{content:'⇅'}}

tbody tr:nth-child(odd) {{background:var(--row-odd);}}
tbody tr:nth-child(even){{background:var(--row-evn);}}
tbody tr:hover{{background:var(--hov)!important;transition:background .1s;}}
tbody td{{padding:8px 14px;vertical-align:middle;white-space:nowrap;border-bottom:1px solid rgba(26,24,16,0.05);}}

/* cells */
.tk{{color:var(--acc);font-weight:700;font-size:13px;text-decoration:none;letter-spacing:-.01em;}}
.tk:hover{{color:#CC1A50;text-decoration:underline;}}
.nm{{color:var(--mid);font-size:12px;}}
.pv{{font-weight:700;color:var(--txt);font-size:13px;}}

/* mini bar */
.bc{{display:flex;align-items:center;gap:8px;}}
.bb{{width:50px;height:4px;background:rgba(26,24,16,0.10);border-radius:2px;flex-shrink:0;}}
.bf{{height:4px;border-radius:2px;}}
.bn{{font-size:12.5px;font-weight:700;min-width:24px;}}

/* vol badge */
.vb{{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:700;}}
.vf{{background:rgba(255,46,106,.12);color:#CC1A50;border:1px solid rgba(255,46,106,.30);}}
.vw{{background:rgba(255,107,26,.12);color:#CC4A00;border:1px solid rgba(255,107,26,.28);}}
.vd{{color:var(--dim);font-size:12px;}}

/* rsi */
.rh{{color:var(--acc);font-weight:700;}}
.rm{{color:#6B3FFF;font-weight:600;}}
.rl{{color:var(--grn);font-weight:700;}}
.rn{{color:var(--mid);}}

/* inst */
.isb{{color:#CC1A50;font-weight:800;}}
.ib {{color:var(--acc);font-weight:600;}}
.iss{{color:#0A6B28;font-weight:800;}}
.is {{color:var(--grn);font-weight:600;}}
.iz {{color:var(--dim);}}

/* signal */
.sg{{display:inline-block;padding:2px 9px;border-radius:4px;font-size:11px;white-space:nowrap;font-weight:600;}}
.s1{{background:rgba(255,46,106,.10);color:#CC1A50;border:1px solid rgba(255,46,106,.28);}}
.s2{{background:rgba(255,107,26,.10);color:#AA4000;border:1px solid rgba(255,107,26,.28);}}
.s3{{background:rgba(26,138,58,.10);color:#0A6B28;border:1px solid rgba(26,138,58,.28);}}
.s4{{background:rgba(107,63,255,.09);color:#4A20CC;border:1px solid rgba(107,63,255,.22);}}

/* pattern */
.pt{{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;margin-right:3px;font-weight:600;}}
.pa{{background:rgba(255,208,0,.18);color:#7A6000;border:1px solid rgba(255,208,0,.40);}}
.pb{{background:rgba(107,63,255,.10);color:#4A20CC;border:1px solid rgba(107,63,255,.25);}}
.pc{{background:rgba(0,153,204,.10);color:#005A88;border:1px solid rgba(0,153,204,.25);}}

/* ── 說明欄 ── */
.legend{{
  margin:0 28px 28px;
  border:1px solid var(--bdr);border-radius:8px;
  overflow:hidden;
}}
.legend-hd{{
  padding:10px 16px;background:var(--bg2);
  font-size:11px;font-weight:700;color:var(--hd);
  letter-spacing:.08em;cursor:pointer;
  display:flex;justify-content:space-between;align-items:center;
  border-left:3px solid var(--acc);
}}
.legend-hd:hover{{background:var(--hov);color:var(--acc);}}
.legend-body{{display:none;padding:14px 16px;background:var(--bg);}}
.legend-body.open{{display:block;}}
.legend-body table{{margin-top:0;}}
.legend-body thead th{{
  background:transparent;padding:6px 10px;font-size:10px;
  border-bottom:1px solid var(--bdr);position:static;color:var(--hd);
}}
.legend-body tbody td{{padding:6px 10px;font-size:12px;border-bottom:1px solid var(--bdr);color:var(--txt);}}
.legend-body tbody tr:last-child td{{border-bottom:none;}}
.note{{color:var(--dim);font-size:11px;margin-top:10px;}}
</style>
</head><body>

<div class="hd">
  <span class="hd-t">🇹🇼 <span>台股</span>半導體 Screener</span>
  <span class="hd-m">更新：{gen_at} &nbsp;·&nbsp; {total} 檔</span>
</div>

<div class="mkt">
  <div class="mi"><span class="mi-l">加權指數</span><span class="mi-v">{mkt_px:,.0f}</span></div>
  <div class="mi"><span class="mi-l">近5日</span><span class="mi-v {r5cls}">{mkt_r5:+.1f}%</span></div>
  <div class="mi"><span class="mi-l">RSI(14)</span><span class="mi-v neu">{rsi_txt}</span></div>
  <div class="mi"><span class="mi-l">MA20</span><span class="mi-v {ma_cls}">{ma_txt}</span></div>
</div>

<div class="fb">
  <div class="srch">
    <span class="srch-ico">🔍</span>
    <input type="text" id="searchQ" placeholder="代號 / 名稱" oninput="applyFilter()">
  </div>
  <div class="sl-grp">
    <span class="sl-lbl">K線分 ≥</span>
    <input type="range" id="slK" min="0" max="100" step="5" value="0"
      oninput="updSlider(this,'kvK');applyFilter()">
    <span class="sl-val" id="kvK">0</span>
  </div>
  <div class="sl-grp">
    <span class="sl-lbl">綜合分 ≥</span>
    <input type="range" id="slC" min="0" max="100" step="5" value="0"
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

<!-- 說明 -->
<div class="legend">
  <div class="legend-hd" onclick="toggleLegend()">
    <span>📖 指標說明</span><span id="legArr">▶</span>
  </div>
  <div class="legend-body" id="legBody">
    <table>
      <thead><tr><th>指標</th><th>說明</th></tr></thead>
      <tbody>
        <tr><td>K線分 ≥ 75</td><td>技術面強勢；62~74 偏多；50~61 中性；&lt; 50 偏弱</td></tr>
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

<script>
const RAW = {rows_json};
let sortKey='kline', sortAsc=false;
const SC={{"💥突破放量":"s1","🚀主力進場":"s2","✅洗盤結束":"s3","📉量縮整理":"s4"}};
const PC={{"pat-a":"pa","pat-b":"pb","pat-c":"pc"}};

function kc(v){{
  if(v==null)return'rgba(26,24,16,0.12)';
  if(v>=75)return'#FF2E6A';if(v>=62)return'#FF6B1A';
  if(v>=50)return'#6B3FFF';return'#1A8A3A';
}}
function cc(v){{
  if(v==null)return'rgba(26,24,16,0.12)';
  if(v>=60)return'#FF2E6A';if(v>=40)return'#FF6B1A';
  if(v>=25)return'#6B3FFF';return'rgba(26,24,16,0.12)';
}}
function bar(val,cfn,link){{
  if(val==null)return'<span style="color:#9A9888">—</span>';
  const pct=Math.min(Math.max(val,0),100),c=cfn(val);
  const inner=`<div class="bc"><div class="bb"><div class="bf" style="width:${{pct}}%;background:${{c}}"></div></div><span class="bn" style="color:${{c}}">${{Math.round(val)}}</span></div>`;
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
  if(v==null)return'<span style="color:#9A9888">—</span>';
  if(v>0)return`<span class="pos">+${{v.toFixed(2)}}%</span>`;
  if(v<0)return`<span class="neg">${{v.toFixed(2)}}%</span>`;
  return'<span class="iz">0.00%</span>';
}}

function updSlider(el,vidId){{
  const pct=(el.value/el.max)*100;
  el.style.setProperty('--pct',pct+'%');
  document.getElementById(vidId).textContent=el.value;
}}

function renderRows(data){{
  const tb=document.getElementById('tBody');
  if(!data.length){{
    tb.innerHTML='<tr><td colspan="12" style="text-align:center;padding:48px;color:#9A9888;font-size:13px">沒有符合條件的股票</td></tr>';
    return;
  }}
  tb.innerHTML=data.map(r=>`<tr>
    <td><a class="tk" href="${{r.yahoo_url}}" target="_blank">${{r.ticker}}</a></td>
    <td><span class="nm">${{r.name}}</span></td>
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

// 初始化滑桿漸層
['slK','slC'].forEach(id=>{{
  const el=document.getElementById(id);
  el.style.setProperty('--pct','0%');
}});

// 預設 K線分降序
document.querySelector('th[data-k="kline"]').classList.add('desc');
applyFilter();

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
setTimeout(schedResize,150);setTimeout(schedResize,700);
</script>
</body></html>"""

    est = 520 + len(rows)*40
    components.html(page, height=est, scrolling=False)

if __name__=="__main__":
    main()
