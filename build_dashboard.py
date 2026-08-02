#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""读取 notices.db / exam_dates.json，生成 SPA 看板 index.html。

布局：
- 左侧导航：核心功能 → 考试信息（倒计时 / 公告汇总）
- 右侧主区：备考倒计时首页 / 公告汇总

纯静态单文件，无外部依赖、可离线打开。
"""
import sqlite3, os, json, datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(ROOT, "notices.db")
EXAM_FILE = os.path.join(ROOT, "exam_dates.json")
OUT = os.path.join(ROOT, "index.html")


def now_iso():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def render_sidebar():
    return (
        '<div class="sec-title">核心功能</div>\n'
        '<a class="parent" data-view="home">📝 考试信息</a>\n'
        '<div class="sub-nav">\n'
        '  <a data-view="home" class="active">⌛ 倒计时</a>\n'
        '  <a data-view="notices">📋 公告汇总</a>\n'
        '</div>'
    )


TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <meta name="theme-color" content="#22c55e">
  <link rel="manifest" href="manifest.webmanifest">
  <link rel="icon" href="icon.svg" type="image/svg+xml">
  <link rel="apple-touch-icon" href="icon.svg">
  <title>公考/事考 工作台</title>
<style>
  :root{
    --bg:#f0fdf4; --card:#ffffff; --text:#1f3a2e; --muted:#7c9b8a;
    --border:#d1fae5; --accent:#22c55e; --accent-soft:#dcfce7;
    --gk:#fb7185; --sk:#60a5fa; --sy:#34d399;
    --newbg:#fef9c3; --newtx:#a16207; --sidebg:#ffffff;
    --shadow:0 6px 20px rgba(34,197,94,.12);
  }
  *{box-sizing:border-box}
  body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
       background:var(--bg);color:var(--text);font-size:14px;position:relative}
  body::before{content:"";position:fixed;inset:0;z-index:-1;pointer-events:none;
    background-image:radial-gradient(var(--border) 2.5px, transparent 2.5px);
    background-size:28px 28px;opacity:.55}
  .app{display:flex;min-height:100vh}
  aside{width:235px;background:var(--sidebg);border-right:1px solid var(--border);padding:18px 10px;position:sticky;top:0;height:100vh;overflow:auto}
  .logo{font-size:17px;font-weight:700;margin-bottom:18px;padding:0 6px}
  .logo small{display:block;color:var(--muted);font-weight:400;font-size:11px;margin-top:2px}
  nav .sec-title{font-size:11px;color:var(--muted);margin:16px 6px 6px;letter-spacing:.5px;font-weight:600}
  nav a{display:flex;align-items:center;gap:8px;padding:9px 12px;border-radius:14px;color:var(--text);text-decoration:none;margin-bottom:4px;cursor:pointer;font-size:13.5px;line-height:1.2;transition:background .15s}
  nav a.active{background:var(--accent);color:#fff;font-weight:600}
  nav a:not(.active):hover{background:var(--accent-soft)}
  nav a.parent{font-weight:600}
  nav .sub-nav{padding:2px 0 4px 14px;margin:0 6px 6px 14px;border-left:1px solid var(--border)}
  nav .sub-nav a{padding:7px 12px;font-size:13px}
  main{flex:1;padding:20px 28px;overflow:auto}
  h1{font-size:20px;margin:0 0 14px;font-weight:800}
  .grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}

  /* 手机端底部导航（窄屏显示，隐藏左侧栏） */
  .mobile-tab{display:none}
  @media(max-width:880px){
    .app{flex-direction:column}
    aside{display:none}
    main{padding:16px 14px calc(76px + env(safe-area-inset-bottom))}
    .mobile-tab{display:flex;position:fixed;bottom:0;left:0;right:0;
      background:var(--card);border-top:1px solid var(--border);
      box-shadow:0 -2px 10px rgba(0,0,0,.06);z-index:30;
      padding-bottom:env(safe-area-inset-bottom)}
    .mobile-tab a{flex:1;text-align:center;padding:9px 0;color:var(--muted);
      text-decoration:none;font-size:12px;line-height:1.35;cursor:pointer}
    .mobile-tab a.active{color:var(--accent);font-weight:700}
    .grid2{grid-template-columns:1fr}
    .bar{position:static}
    h1{font-size:18px}
    .cd-title{font-size:16px}
    .card a{font-size:13.5px}
  }

  /* countdown */
  .cd-card{background:linear-gradient(135deg,#34d399,#16a34a);color:#fff;border-radius:22px;padding:22px;box-shadow:var(--shadow);position:relative;overflow:hidden}
  .cd-card.fj{background:linear-gradient(135deg,#6ee7b7,#22c55e)}
  .cd-card::after{content:"🌿";position:absolute;right:14px;top:6px;font-size:46px;opacity:.18;line-height:1}
  .cd-title{font-size:18px;font-weight:700;display:flex;justify-content:space-between;align-items:center}
  .cd-year{font-size:12px;background:rgba(255,255,255,.18);padding:2px 8px;border-radius:999px}
  .cd-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:14px}
  .cd-cell{background:rgba(255,255,255,.18);border-radius:14px;padding:14px}
  .cd-cell h4{margin:0 0 4px;font-size:12px;font-weight:500;opacity:.85}
  .cd-cell .date{font-size:13px;opacity:.95}
  .cd-cell .days{font-size:30px;font-weight:800;margin-top:4px}
  .cd-cell .days small{font-size:12px;font-weight:400;opacity:.8;margin-left:4px}
  .cd-cell.expired .days{font-size:16px;font-weight:600;opacity:.7}
  .cd-note{font-size:11px;opacity:.75;margin-top:12px;line-height:1.5}
  .timeline{display:flex;margin-top:14px;background:rgba(255,255,255,.12);border-radius:10px;padding:12px 14px;font-size:11px}
  .tl-node{flex:1;text-align:center;position:relative;opacity:.75}
  .tl-node.active{opacity:1;font-weight:700}
  .tl-node::after{content:"";position:absolute;top:50%;right:-50%;width:100%;height:2px;background:rgba(255,255,255,.25);z-index:0}
  .tl-node:last-child::after{display:none}
  .tl-node span{display:block;position:relative;z-index:1;background:rgba(255,255,255,.15);border-radius:6px;padding:3px 2px;margin:0 2px}

  /* lists */
  .latest{margin-top:22px}
  .latest h2{font-size:15px;margin:0 0 10px}
  .latest .row{display:flex;gap:10px;padding:9px 12px;border-bottom:1px solid var(--border);font-size:13px}
  .latest .row:last-child{border-bottom:none}
  .tag{font-size:11px;padding:2px 9px;border-radius:999px;color:#fff;white-space:nowrap;font-weight:600}
  .tag.国考{background:var(--gk)}.tag.省考{background:var(--sk)}.tag.事业编{background:var(--sy)}
  .bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:8px 0 14px;position:sticky;top:0;background:var(--bg);padding:6px 0;z-index:5}
  .chip{padding:5px 13px;border:1px solid var(--border);border-radius:999px;background:#fff;cursor:pointer;font-size:13px}
  .chip.active{background:var(--accent);color:#fff;border-color:var(--accent)}
  select,input{padding:6px 10px;border:1px solid var(--border);border-radius:8px;background:#fff;font-size:13px}
  input{min-width:180px}
  .card{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:12px 14px;margin-bottom:10px;display:flex;gap:10px;align-items:flex-start;box-shadow:0 2px 8px rgba(34,197,94,.06);transition:transform .15s ease}
  .card:hover{transform:translateY(-2px)}
  .card .body{flex:1;min-width:0}
  .card a{color:var(--text);text-decoration:none;font-weight:600;font-size:14px}
  .card a:hover{color:var(--accent);text-decoration:underline}
  .meta{color:var(--muted);font-size:11px;margin-top:3px}
  .new{background:var(--newbg);color:var(--newtx);font-size:10px;padding:1px 6px;border-radius:999px;margin-left:6px}
  .empty{color:var(--muted);text-align:center;padding:30px}
  .sub{color:var(--muted);font-size:12px}
</style>
</head>
<body>
<div class="app">
  <aside>
    <div class="logo">🌿 公考工作台<small>公告聚合 · 倒计时</small></div>
    <nav id="main-nav">
      /*__SIDEBAR__*/
    </nav>
  </aside>
  <main>
    <div id="view-home">
      <h1>🌱 备考倒计时</h1>
      <div class="sub">更新时间：<span id="gen"></span>　|　共 <span id="total"></span> 条公告，今日新增 <span id="newc"></span> 条</div>
      <div class="grid2" id="countdown-grid"></div>
      <div class="latest">
        <h2>最新公告</h2>
        <div id="latest-list"></div>
      </div>
    </div>
    <div id="view-notices" style="display:none">
      <h1>公告汇总</h1>
      <div class="sub">更新时间：<span id="gen2"></span></div>
      <div class="bar">
        <span class="chip active" data-cat="全部">全部</span>
        <span class="chip" data-cat="国考">国考</span>
        <span class="chip" data-cat="省考">省考</span>
        <span class="chip" data-cat="事业编">事业单位</span>
        <select id="region"><option value="">全部地区</option></select>
        <input id="q" placeholder="搜索标题关键字…">
      </div>
      <div id="list"></div>
      <div class="empty" id="empty" style="display:none">🍃 没有匹配的公告</div>
    </div>
  </main>
</div>
<nav class="mobile-tab">
  <a data-view="home" class="active">⌛ 倒计时</a>
  <a data-view="notices">📋 公告</a>
</nav>
<script>
const DATA = /*__DATA__*/;
const EXAM = /*__EXAM__*/;

function catClass(c){return (["国考","省考","事业编"].includes(c)?c:"")}
function fmt(d){return d||"日期未知"}
function days(dateStr){
  if(!dateStr) return null;
  const t = new Date(dateStr+" 00:00:00").getTime();
  const n = new Date(); n.setHours(0,0,0,0);
  return Math.round((t-n.getTime())/86400000);
}

function renderCountdown(){
  const grid=document.getElementById("countdown-grid");
  grid.innerHTML = Object.entries(EXAM).map(([k,e])=>{
    const d_reg_start = days(e.registration_start);
    const d_reg_end   = days(e.registration_end);
    const d_exam      = days(e.written_exam);
    const cls = k.includes("福建")?"fj":"";
    function cell(title, d){
      if(d===null) return `<div class="cd-cell"><h4>${title}</h4><div class="date">—</div><div class="days">—</div></div>`;
      const expired = d<0, cur = d===0;
      return `<div class="cd-cell${expired?" expired":""}"><h4>${title}</h4><div class="date">${expired?"已结束":"日期 "+(title.includes("报名")?e.registration_start+" 至 "+e.registration_end:e.written_exam)}</div><div class="days">${cur?"今天":(expired?(-d+" 天前"):d)}<small>${expired?"":" 天"}</small></div></div>`;
    }
    const tlNodes=[
      {label:"公告", cur: d_reg_start>15},
      {label:"报名", cur: d_reg_start!==null && d_reg_start<=15 && d_reg_end>=0, active: d_reg_start<=0 && d_reg_end>=0},
      {label:"笔试", cur: d_exam!==null && d_exam<=30 && d_exam>=0, active: d_exam===0},
      {label:"成绩", cur: d_exam!==null && d_exam<0 && d_exam>-45},
      {label:"录用", cur: d_exam!==null && d_exam<-45},
    ];
    const tl = tlNodes.map(n=>`<div class="tl-node${n.active?" active":""}"><span>${n.label}</span></div>`).join("");
    return `<div class="cd-card ${cls}">
      <div class="cd-title">${e.title}<span class="cd-year">${e.year}年度</span></div>
      <div class="cd-grid">
        ${cell("报名时间", d_reg_start)}
        ${cell("笔试时间", d_exam)}
      </div>
      <div class="timeline">${tl}</div>
      <div class="cd-note">${e.note||""}</div>
    </div>`;
  }).join("");
}

function renderLatest(){
  const list = [...DATA.notices].sort((a,b)=>(b.first_seen||"").localeCompare(a.first_seen||""));
  const top = list.slice(0,10);
  document.getElementById("latest-list").innerHTML = top.map(n=>`
    <div class="row">
      <span class="tag ${catClass(n.category)}">${n.category||""}</span>
      <a href="${n.url}" target="_blank" rel="noopener" style="color:var(--text);text-decoration:none;flex:1">${n.title}</a>
      <span class="sub">${n.region||""} · ${fmt(n.date)}</span>
    </div>`).join("") || `<div class="empty">🍃 暂无公告</div>`;
}

function renderNotices(){
  const cat=document.querySelector(".chip.active").dataset.cat;
  const region=document.getElementById("region").value;
  const q=document.getElementById("q").value.trim();
  let list=DATA.notices.filter(n=>{
    if(cat!=="全部"&&n.category!==cat) return false;
    if(region&&n.region!==region) return false;
    if(q&&!n.title.includes(q)) return false;
    return true;
  });
  list.sort((a,b)=>{
    if((b.is_new?1:0)!==(a.is_new?1:0)) return (b.is_new?1:0)-(a.is_new?1:0);
    return (b.date||"").localeCompare(a.date||"");
  });
  document.getElementById("list").innerHTML = list.map(n=>`
    <div class="card">
      <span class="tag ${catClass(n.category)}">${n.category||""}</span>
      <div class="body">
        <a href="${n.url}" target="_blank" rel="noopener">${n.title}${n.is_new?'<span class="new">今日新增</span>':''}</a>
        <div class="meta">${n.region||""} · ${fmt(n.date)} · 来源：${n.source||""}</div>
      </div>
    </div>`).join("");
  document.getElementById("empty").style.display = list.length?"none":"block";
}

function switchView(v){
  document.getElementById("view-home").style.display = v==="home"?"":"none";
  document.getElementById("view-notices").style.display = v==="notices"?"":"none";
  document.querySelectorAll("nav a[data-view]").forEach(a=>{
    if(a.classList.contains("parent")) a.classList.toggle("active", v==="home");
    else a.classList.toggle("active", a.dataset.view===v);
  });
  document.querySelectorAll(".mobile-tab a[data-view]").forEach(a=>a.classList.toggle("active", a.dataset.view===v));
  if(v==="notices") renderNotices();
}

function init(){
  document.getElementById("gen").textContent = DATA.generated;
  document.getElementById("gen2").textContent = DATA.generated;
  document.getElementById("total").textContent = DATA.notices.length;
  document.getElementById("newc").textContent = DATA.notices.filter(n=>n.is_new).length;
  renderCountdown();
  renderLatest();
  const sel=document.getElementById("region");
  [...new Set(DATA.notices.map(n=>n.region).filter(Boolean))].sort().forEach(r=>{
    const o=document.createElement("option");o.value=r;o.textContent=r;sel.appendChild(o);
  });
  document.querySelectorAll("nav a[data-view]").forEach(a=>a.onclick=()=>switchView(a.dataset.view));
  document.querySelectorAll(".mobile-tab a[data-view]").forEach(a=>a.onclick=()=>switchView(a.dataset.view));
  document.querySelectorAll(".chip").forEach(c=>c.onclick=()=>{document.querySelectorAll(".chip").forEach(x=>x.classList.remove("active"));c.classList.add("active");renderNotices();});
  sel.onchange=renderNotices;
  document.getElementById("q").oninput=renderNotices;
}
init();
</script>
</body></html>
"""

TEMPLATE = TEMPLATE  # noqa


def build():
    conn = sqlite3.connect(DB)
    rows = conn.execute(
        "SELECT source,category,region,title,url,date,first_seen,last_seen FROM notices").fetchall()
    conn.close()
    today = datetime.date.today().strftime("%Y-%m-%d")
    notices = []
    for r in rows:
        notices.append({
            "source": r[0], "category": r[1], "region": r[2], "title": r[3],
            "url": r[4], "date": r[5], "first_seen": r[6], "last_seen": r[7],
            "is_new": (r[5] == today) or (r[6] and r[6][:10] == today),
        })
    data = {"generated": now_iso(), "notices": notices}
    exam = {}
    if os.path.exists(EXAM_FILE):
        with open(EXAM_FILE, encoding="utf-8") as f:
            exam = json.load(f)
    html = (TEMPLATE
            .replace("/*__DATA__*/", json.dumps(data, ensure_ascii=False))
            .replace("/*__EXAM__*/", json.dumps(exam, ensure_ascii=False))
            .replace("/*__SIDEBAR__*/", render_sidebar()))
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html)
    return len(notices)


if __name__ == "__main__":
    n = build()
    print(f"生成看板：公告 {n} 条")
