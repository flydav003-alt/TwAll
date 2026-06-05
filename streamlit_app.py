"""
streamlit_app.py — 台股半導體 Screener
架構：components.html() + JS 排序，scrolling=False + ResizeObserver 撐高
配色：深海藍黑（參考圖片風格）
"""
import json, os, requests, streamlit as st
import streamlit.components.v1 as components
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
st.markdown("""<style>
  [data-testid="stHeader"],[data-testid="stToolbar"],
  [data-testid="stDecoration"],#MainMenu,footer { display:none!important; }
  [data-testid="stAppViewContainer"]{padding:0!important;}
  [data-testid="stVerticalBlock"]{gap:0!important;padding:0!important;}
  [data-testid="element-container"]{padding:0!important;margin:0!important;}
  .block-container{padding:0!important;max-width:100%!important;}
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


# ── 讀資料 ───────────────────────────────────────────────────
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


# ── 主程式 ───────────────────────────────────────────────────
def main():
    stocks, generated_at = _load_stocks()
    mkt = _load_market()

    if stocks is None:
        st.error("⚠️ 找不到 data/screener_data.json，請先執行 GitHub Actions。")
        st.stop()

    # FinMind
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
        chg   = round((price-prev)/prev*100, 2) if price and prev and prev!=0 else None
        pat_r = max((PAT_RANK.get(c,0) for _,c in s.get("patterns",[])), default=0)
        rows.append({
            "ticker":    s["ticker"],
            "name":      s.get("name", s["ticker"]),
            "market":    s.get("market","TW"),
            "price":     price,
            "prev":      prev,
            "chg":       chg,
            "kline":     s.get("kline_score"),
            "comp":      s.get("composite"),
            "vol":       s.get("volume_ratio"),
            "rsi":       s.get("rsi14"),
            "rs5d":      s.get("rs5d"),
            "inst":      s.get("inst_buy_days",0) or 0,
            "signal":    s.get("entry_signal",""),
            "sig_rank":  SIG_RANK.get(s.get("entry_signal",""),0),
            "patterns":  [[n,c] for n,c in s.get("patterns",[])],
            "pat_rank":  pat_r,
            "yahoo_url": yahoo_tw_url(s["ticker"], s.get("market","TW")),
            "kline_url": f"https://flydav003-alt.github.io/k-line/?stock={s['ticker']}",
        })

    # 大盤資料
    mkt_px  = mkt.get("price",0) or 0
    mkt_r5  = mkt.get("ret5d",0) or 0
    mkt_rsi = mkt.get("rsi")
    mkt_ma  = not mkt.get("below_ma20", False)

    rows_json = json.dumps(rows, ensure_ascii=False, default=str)
    total     = len(rows)
    gen_at    = generated_at or "N/A"

    # ── 組 HTML ──────────────────────────────────────────────
    html_page = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Noto+Sans+TC:wght@400;500;700&display=swap" rel="stylesheet">
<style>
*{{box-sizing:border-box;margin:0;padding:0;}}
:root{{
  --bg:         #070e1c;
  --bg2:        #0a1628;
  --bg3:        #0d1e35;
  --bg-row:     #0b1a2e;
  --bg-row-alt: #091525;
  --bg-hov:     #112040;
  --border:     rgba(40,90,160,0.28);
  --border2:    rgba(50,110,190,0.45);
  --text:       #c8dcf4;
  --text-dim:   #3a5878;
  --text-mid:   #6888aa;
  --text-hd:    #4a78a8;
  --accent:     #3a7fff;
  --red:        #f03a52;
  --green:      #00c070;
  --orange:     #e87820;
  --yellow:     #d8b830;
  --sans:       'Inter','Noto Sans TC',sans-serif;
}}
body{{
  background:var(--bg);color:var(--text);
  font-family:var(--sans);font-size:13px;
  min-height:100vh;
}}

/* ── Header ── */
.hd{{
  display:flex;align-items:baseline;gap:12px;
  padding:14px 20px 10px;
  border-bottom:1px solid var(--border);
}}
.hd-title{{font-size:17px;font-weight:700;color:#ddeeff;letter-spacing:.04em;}}
.hd-meta{{font-size:11px;color:var(--text-dim);letter-spacing:.04em;}}

/* ── 大盤 ── */
.mkt{{
  display:flex;align-items:stretch;
  background:var(--bg2);border-bottom:1px solid var(--border);
  padding:0 20px;
}}
.mkt-item{{
  display:flex;flex-direction:column;justify-content:center;
  padding:10px 20px 10px 0;margin-right:20px;
  border-right:1px solid var(--border);
}}
.mkt-item:last-child{{border-right:none;}}
.mkt-lbl{{font-size:9.5px;color:var(--text-dim);letter-spacing:.1em;text-transform:uppercase;margin-bottom:2px;}}
.mkt-val{{font-size:16px;font-weight:700;}}
.pos{{color:var(--red);}} .neg{{color:var(--green);}} .neu{{color:var(--text-mid);}}

/* ── 篩選列 ── */
.filter-bar{{
  display:flex;align-items:center;gap:16px;
  padding:10px 20px;
  background:var(--bg2);
  border-bottom:1px solid var(--border);
  flex-wrap:nowrap;
}}
.filter-bar input[type=text]{{
  background:rgba(10,25,55,0.9);
  border:1px solid var(--border2);
  color:var(--text);border-radius:5px;
  padding:6px 12px;font-size:13px;
  font-family:var(--sans);width:200px;
  outline:none;transition:border .15s;
}}
.filter-bar input[type=text]:focus{{border-color:var(--accent);}}
.filter-bar input[type=text]::placeholder{{color:var(--text-dim);}}
.sl-wrap{{display:flex;align-items:center;gap:8px;}}
.sl-lbl{{font-size:11px;color:var(--text-mid);white-space:nowrap;}}
.sl-val{{font-size:12px;font-weight:600;color:var(--accent);min-width:22px;}}
input[type=range]{{
  -webkit-appearance:none;width:110px;height:3px;
  background:var(--border2);border-radius:2px;outline:none;cursor:pointer;
}}
input[type=range]::-webkit-slider-thumb{{
  -webkit-appearance:none;width:14px;height:14px;
  border-radius:50%;background:var(--accent);
  border:2px solid #ddeeff;cursor:pointer;
  box-shadow:0 0 6px rgba(58,127,255,.5);
}}
.stat-line{{
  padding:6px 20px;font-size:11px;color:var(--text-dim);
  border-bottom:1px solid var(--border);
}}
.stat-line b{{color:var(--accent);}}

/* ── 表格 ── */
table{{width:100%;border-collapse:collapse;}}
thead th{{
  background:var(--bg2);color:var(--text-hd);
  font-size:10px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;
  padding:8px 12px;text-align:left;
  border-bottom:1px solid var(--border2);
  white-space:nowrap;cursor:pointer;user-select:none;
  position:sticky;top:0;z-index:5;
}}
thead th:hover{{color:var(--text);background:var(--bg3);}}
thead th .arr{{margin-left:4px;font-size:9px;opacity:.4;}}
thead th.asc .arr{{opacity:1;color:var(--accent);}}
thead th.asc .arr::after{{content:'▲';}}
thead th.desc .arr{{opacity:1;color:var(--accent);}}
thead th.desc .arr::after{{content:'▼';}}
thead th:not(.asc):not(.desc) .arr::after{{content:'⇅';}}

tbody tr:nth-child(odd)  {{background:var(--bg-row);}}
tbody tr:nth-child(even) {{background:var(--bg-row-alt);}}
tbody tr:hover{{background:var(--bg-hov)!important;}}
tbody td{{padding:7px 12px;vertical-align:middle;white-space:nowrap;}}

/* ── 欄位樣式 ── */
.tk{{color:#4a9fff;font-weight:700;font-size:13px;text-decoration:none;letter-spacing:.03em;}}
.tk:hover{{color:#90c8ff;}}
.nm{{color:var(--text-mid);font-size:12px;}}
.pv{{font-weight:600;color:#ddeeff;}}

/* bar */
.bar-cell{{display:flex;align-items:center;gap:7px;}}
.bar-bg{{width:44px;height:3px;background:rgba(255,255,255,.07);border-radius:2px;flex-shrink:0;}}
.bar-fg{{height:3px;border-radius:2px;}}
.bar-num{{font-size:12px;font-weight:700;min-width:22px;}}

/* vol */
.vb{{display:inline-block;padding:1px 7px;border-radius:3px;font-size:11px;font-weight:700;}}
.v-fire{{background:rgba(240,58,82,.14);color:#f03a52;border:1px solid rgba(240,58,82,.3);}}
.v-warn{{background:rgba(232,120,32,.14);color:#e87820;border:1px solid rgba(232,120,32,.3);}}
.v-dim{{color:var(--text-dim);font-size:12px;}}

/* rsi */
.rh{{color:var(--red);font-weight:600;}} .rm{{color:var(--yellow);}} .rl{{color:var(--green);font-weight:600;}} .rn{{color:var(--text-mid);}}

/* inst */
.isb{{color:var(--red);font-weight:800;}} .ib{{color:#f07060;font-weight:600;}}
.iss{{color:var(--green);font-weight:800;}} .is{{color:#50b888;font-weight:600;}}
.iz{{color:var(--text-dim);}}

/* signal */
.sig{{display:inline-block;padding:2px 8px;border-radius:3px;font-size:11px;white-space:nowrap;}}
.s1{{background:rgba(240,58,82,.12);color:#f06070;border:1px solid rgba(240,58,82,.25);}}
.s2{{background:rgba(232,120,32,.12);color:#f0a040;border:1px solid rgba(232,120,32,.25);}}
.s3{{background:rgba(0,192,112,.10);color:#30d890;border:1px solid rgba(0,192,112,.25);}}
.s4{{background:rgba(50,80,120,.20);color:#6090b8;border:1px solid rgba(50,80,120,.4);}}

/* pattern */
.pt{{display:inline-block;padding:1px 7px;border-radius:3px;font-size:10.5px;margin-right:3px;}}
.pa{{background:rgba(232,120,32,.14);color:#f0a040;border:1px solid rgba(232,120,32,.3);}}
.pb{{background:rgba(42,127,255,.12);color:#70b0ff;border:1px solid rgba(42,127,255,.3);}}
.pc{{background:rgba(160,100,240,.12);color:#c090f8;border:1px solid rgba(160,100,240,.3);}}
</style>
</head>
<body>

<!-- Header -->
<div class="hd">
  <span class="hd-title">🇹🇼 台股半導體 Screener</span>
  <span class="hd-meta">更新：{gen_at} &nbsp;·&nbsp; {total} 檔</span>
</div>

<!-- 大盤 -->
<div class="mkt" id="mktBar">
  <div class="mkt-item">
    <div class="mkt-lbl">加權指數</div>
    <div class="mkt-val">{mkt_px:,.0f}</div>
  </div>
  <div class="mkt-item">
    <div class="mkt-lbl">近5日</div>
    <div class="mkt-val {'pos' if mkt_r5>0 else 'neg' if mkt_r5<0 else 'neu'}">{mkt_r5:+.1f}%</div>
  </div>
  <div class="mkt-item">
    <div class="mkt-lbl">RSI(14)</div>
    <div class="mkt-val neu">{f'{mkt_rsi:.0f}' if mkt_rsi else 'N/A'}</div>
  </div>
  <div class="mkt-item">
    <div class="mkt-lbl">MA20</div>
    <div class="mkt-val {'pos' if mkt_ma else 'neg'}">{'站上 ✓' if mkt_ma else '跌破 ✗'}</div>
  </div>
</div>

<!-- 篩選列 -->
<div class="filter-bar">
  <input type="text" id="searchQ" placeholder="🔍  代號 / 名稱" oninput="applyFilter()">
  <div class="sl-wrap">
    <span class="sl-lbl">K線分 ≥</span>
    <input type="range" id="slKline" min="0" max="100" step="5" value="0" oninput="applyFilter();document.getElementById('kvKline').textContent=this.value">
    <span class="sl-val" id="kvKline">0</span>
  </div>
  <div class="sl-wrap">
    <span class="sl-lbl">綜合分 ≥</span>
    <input type="range" id="slComp" min="0" max="100" step="5" value="0" oninput="applyFilter();document.getElementById('kvComp').textContent=this.value">
    <span class="sl-val" id="kvComp">0</span>
  </div>
</div>

<div class="stat-line" id="statLine">載入中…</div>

<!-- 表格 -->
<div id="tableWrap">
<table id="mainTbl">
<thead>
<tr>
  <th data-key="ticker" class="no-sort">代號</th>
  <th data-key="name"   class="no-sort">名稱</th>
  <th data-key="price"  class="no-sort">現價</th>
  <th data-key="chg">漲跌%<span class="arr"></span></th>
  <th data-key="kline">K線分<span class="arr"></span></th>
  <th data-key="comp">綜合分<span class="arr"></span></th>
  <th data-key="vol">爆量<span class="arr"></span></th>
  <th data-key="rsi">RSI<span class="arr"></span></th>
  <th data-key="rs5d">RS(5日)<span class="arr"></span></th>
  <th data-key="inst">法人<span class="arr"></span></th>
  <th data-key="sig_rank">今日訊號<span class="arr"></span></th>
  <th data-key="pat_rank">型態<span class="arr"></span></th>
</tr>
</thead>
<tbody id="tBody"></tbody>
</table>
</div>

<script>
const RAW = {rows_json};
let sortKey = 'kline', sortAsc = false;

const SIG_CSS = {{"💥突破放量":"s1","🚀主力進場":"s2","✅洗盤結束":"s3","📉量縮整理":"s4"}};
const PAT_CSS = {{"pat-a":"pa","pat-b":"pb","pat-c":"pc"}};

function kc(v){{
  if(v===null||v===undefined) return '#3a5878';
  if(v>=75) return '#f03a52'; if(v>=62) return '#e87820';
  if(v>=50) return '#d8b830'; return '#00c070';
}}
function cc(v){{
  if(v===null||v===undefined) return '#3a5878';
  if(v>=60) return '#f03a52'; if(v>=40) return '#e87820';
  if(v>=25) return '#d8b830'; return '#3a5878';
}}
function bar(val,cfn,link){{
  if(val===null||val===undefined) return '<span style="color:#3a5878">—</span>';
  const pct=Math.min(Math.max(val,0),100), c=cfn(val);
  const inner=`<div class="bar-cell"><div class="bar-bg"><div class="bar-fg" style="width:${{pct}}%;background:${{c}}"></div></div><span class="bar-num" style="color:${{c}}">${{Math.round(val)}}</span></div>`;
  return link ? `<a href="${{link}}" target="_blank" style="text-decoration:none">${{inner}}</a>` : inner;
}}
function vol(v){{
  if(v===null||v===undefined) return '<span class="v-dim">—</span>';
  if(v>=2.0) return `<span class="vb v-fire">${{v.toFixed(1)}}x</span>`;
  if(v>=1.5) return `<span class="vb v-warn">${{v.toFixed(1)}}x</span>`;
  return `<span class="v-dim">${{v.toFixed(1)}}x</span>`;
}}
function rsi(v){{
  if(v===null||v===undefined) return '<span class="rn">—</span>';
  if(v>=70) return `<span class="rh">${{Math.round(v)}}</span>`;
  if(v>=50) return `<span class="rm">${{Math.round(v)}}</span>`;
  if(v<=30) return `<span class="rl">${{Math.round(v)}}</span>`;
  return `<span class="rn">${{Math.round(v)}}</span>`;
}}
function rs(v){{
  if(v===null||v===undefined) return '<span class="iz">—</span>';
  if(v>0) return `<span class="pos">+${{v.toFixed(1)}}%</span>`;
  if(v<0) return `<span class="neg">${{v.toFixed(1)}}%</span>`;
  return '<span class="iz">0.0%</span>';
}}
function inst(v){{
  v=v||0;
  if(v>=3) return `<span class="isb">+${{v}}</span>`;
  if(v>=1) return `<span class="ib">+${{v}}</span>`;
  if(v<=-3) return `<span class="iss">${{v}}</span>`;
  if(v<0)   return `<span class="is">${{v}}</span>`;
  return '<span class="iz">0</span>';
}}
function sig(s){{
  if(!s) return '';
  return `<span class="sig ${{SIG_CSS[s]||'s4'}}">${{s}}</span>`;
}}
function pats(arr){{
  if(!arr||!arr.length) return '';
  return arr.map(([n,c])=>`<span class="pt ${{PAT_CSS[c]||'pb'}}">${{n}}</span>`).join('');
}}
function chgHtml(v){{
  if(v===null||v===undefined) return '<span style="color:#3a5878">—</span>';
  if(v>0) return `<span class="pos">+${{v.toFixed(2)}}%</span>`;
  if(v<0) return `<span class="neg">${{v.toFixed(2)}}%</span>`;
  return '<span class="iz">0.00%</span>';
}}

function renderRows(data){{
  const tb=document.getElementById('tBody');
  if(!data.length){{
    tb.innerHTML='<tr><td colspan="12" style="text-align:center;padding:40px;color:#3a5878">沒有符合條件的股票</td></tr>';
    return;
  }}
  tb.innerHTML=data.map(r=>`<tr>
    <td><a class="tk" href="${{r.yahoo_url}}" target="_blank">${{r.ticker}}</a></td>
    <td><span class="nm">${{r.name}}</span></td>
    <td><span class="pv">${{r.price!==null?r.price.toFixed(1):'—'}}</span></td>
    <td>${{chgHtml(r.chg)}}</td>
    <td>${{bar(r.kline,kc,r.kline_url)}}</td>
    <td>${{bar(r.comp,cc)}}</td>
    <td>${{vol(r.vol)}}</td>
    <td>${{rsi(r.rsi)}}</td>
    <td>${{rs(r.rs5d)}}</td>
    <td>${{inst(r.inst)}}</td>
    <td>${{sig(r.signal)}}</td>
    <td>${{pats(r.patterns)}}</td>
  </tr>`).join('');
}}

function applyFilter(){{
  const q=(document.getElementById('searchQ').value||'').toLowerCase();
  const kMin=+document.getElementById('slKline').value;
  const cMin=+document.getElementById('slComp').value;
  let data=RAW.filter(r=>{{
    if((r.kline===null&&r.vol===null)) return false;
    if((r.kline||0)<kMin) return false;
    if((r.comp||0)<cMin)  return false;
    if(q && !r.ticker.toLowerCase().includes(q) && !r.name.toLowerCase().includes(q)) return false;
    return true;
  }});
  data=sortData(data);
  document.getElementById('statLine').innerHTML=`顯示 <b>${{data.length}}</b> / {total} 檔`;
  renderRows(data);
  scheduleResize();
}}

function sortData(data){{
  return [...data].sort((a,b)=>{{
    let av=a[sortKey], bv=b[sortKey];
    const na=av===null||av===undefined;
    const nb=bv===null||bv===undefined;
    if(na&&nb) return 0;
    if(na) return 1; if(nb) return -1;
    return sortAsc ? av-bv : bv-av;
  }});
}}

// 標頭排序
document.querySelectorAll('thead th[data-key]').forEach(th=>{{
  if(th.classList.contains('no-sort')) return;
  th.addEventListener('click',()=>{{
    const k=th.dataset.key;
    if(sortKey===k) sortAsc=!sortAsc;
    else{{ sortKey=k; sortAsc=false; }}
    document.querySelectorAll('thead th').forEach(t=>t.classList.remove('asc','desc'));
    th.classList.add(sortAsc?'asc':'desc');
    applyFilter();
  }});
}});

// 預設 K線分降序
document.querySelector('th[data-key="kline"]').classList.add('desc');
applyFilter();

// ResizeObserver 撐高 iframe
let raf=0;
function contentH(){{
  return Math.ceil(Math.max(document.body.scrollHeight, document.documentElement.scrollHeight))+4;
}}
function reportH(){{
  window.parent.postMessage({{isStreamlitMessage:true,type:'streamlit:setFrameHeight',height:contentH()}},'*');
}}
function scheduleResize(){{
  if(raf) cancelAnimationFrame(raf);
  raf=requestAnimationFrame(reportH);
}}
if(window.ResizeObserver){{
  new ResizeObserver(scheduleResize).observe(document.body);
}}
window.addEventListener('load',scheduleResize);
setTimeout(scheduleResize,100);
setTimeout(scheduleResize,600);
</script>
</body>
</html>"""

    est_height = 300 + len(rows) * 38
    components.html(html_page, height=est_height, scrolling=False)


if __name__ == "__main__":
    main()
