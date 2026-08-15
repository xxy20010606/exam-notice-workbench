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
        '<a data-view="home" class="active">⌛ 倒计时</a>\n'
        '<a data-view="notices">📋 公告汇总<span class="badge" data-badge="notices"></span></a>'
    )





TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <meta name="theme-color" content="#4F7D3F">
  <link rel="manifest" href="manifest.webmanifest">
  <link rel="icon" href="icon.svg" type="image/svg+xml">
  <link rel="apple-touch-icon" href="icon.svg">
  <title>公考/事考 工作台</title>
<style>
  :root{
    /* 单一功能强调色：深鼠尾草绿，用于选中/已读/主操作 */
    --bg:#F4F7F1; --card:#FFFFFF; --text:#243024; --muted:#5E6B59;
    --border:#E3EAE0; --sidebg:#FFFFFF;
    --accent:#4F7D3F; --accent-soft:#E9F0E3; --accent-text:#3C6B2E;
    --gk:#C0764A; --sk:#4F7D3F; --sy:#5F7CA0;
    --newbg:#1F5FD0; --newtx:#FFFFFF;
    --badge-bg:#E5484D;
    --pin-bg:#FBF1D4; --pin-fg:#B8860B; --pin-grp-bg:#FCF6E3;
    --cd-text:#243024; --cd1:#DCE7D2; --cd2:#A9C19A;
    --cd-cell:rgba(255,255,255,.16); --cd-line:rgba(255,255,255,.28); --cd-node:rgba(255,255,255,.18);
    --r:12px; --r-sm:10px; --r-pill:999px;
  }
  *{box-sizing:border-box}
  a{color:inherit}
  :focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:4px}
  body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
       background:var(--bg);color:var(--text);font-size:14px;line-height:1.55}
  .app{display:flex;min-height:100vh}
  aside{width:215px;background:var(--sidebg);border-right:1px solid var(--border);padding:22px 14px;position:sticky;top:0;height:100vh;overflow:auto}
  .logo{font-size:18px;font-weight:700;margin-bottom:22px;padding:0 4px;display:flex;flex-direction:column;align-items:flex-start;gap:4px;line-height:1.2}
  .logo-mark{display:flex;align-items:center;white-space:nowrap}
  .logo-dot{width:9px;height:9px;border-radius:50%;background:var(--accent);margin-right:8px;flex:none}
  .logo small{color:var(--muted);font-weight:400;font-size:11px;white-space:nowrap}
  nav a{display:flex;align-items:center;gap:8px;padding:12px 14px;border-radius:var(--r);color:var(--text);text-decoration:none;margin-bottom:6px;cursor:pointer;font-size:15px;line-height:1.2;transition:background .15s,color .15s}
  nav a.active{color:var(--accent);font-weight:700;background:var(--accent-soft);position:relative}
  nav a.active::before{content:"";position:absolute;left:0;top:8px;bottom:8px;width:3px;background:var(--accent);border-radius:0 3px 3px 0}
  nav a:not(.active):hover{background:var(--accent-soft)}
  main{flex:1;padding:22px 28px;overflow:auto}
  h1{font-size:21px;margin:0 0 16px;font-weight:800;letter-spacing:-.01em}
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
    .mobile-tab a{flex:1;display:flex;align-items:center;justify-content:center;gap:6px;
      padding:14px 0;color:var(--muted);text-decoration:none;
      font-size:16px;font-weight:600;line-height:1.3;cursor:pointer}
    .mobile-tab a.active{color:var(--accent);position:relative}
  .mobile-tab a.active::before{content:"";position:absolute;top:0;left:50%;transform:translateX(-50%);
    width:28px;height:3px;background:var(--accent);border-radius:0 0 3px 3px}
    .mobile-tab a .ico{font-size:20px;line-height:1}
    .grid2{grid-template-columns:1fr}
    .bar{position:static}
    h1{font-size:18px}
    .cd-title{font-size:16px}
    .card a{font-size:13.5px}
  }

  /* countdown */
  .cd-card{background:linear-gradient(135deg,var(--cd1),var(--cd2));color:var(--cd-text);border-radius:var(--r);padding:24px;position:relative;overflow:hidden}
  .cd-title{font-size:18px;font-weight:700;display:flex;justify-content:space-between;align-items:center}
  .cd-year{font-size:12px;background:var(--cd-cell);padding:2px 8px;border-radius:var(--r-pill)}
  .cd-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:14px}
  .cd-cell{background:var(--cd-cell);border-radius:var(--r-sm);padding:14px}
  .cd-cell h4{margin:0 0 4px;font-size:12px;font-weight:500;opacity:.85}
  .cd-cell .date{font-size:13px;opacity:.95}
  .cd-cell .days{font-size:30px;font-weight:800;margin-top:4px;font-variant-numeric:tabular-nums;letter-spacing:-.02em}
  .cd-cell .days small{font-size:12px;font-weight:400;opacity:.8;margin-left:4px;letter-spacing:0}
  .cd-cell.expired .days{font-size:16px;font-weight:600;opacity:.7}
  .cd-note{font-size:11px;opacity:.75;margin-top:12px;line-height:1.5}
  .timeline{display:flex;margin-top:14px;background:var(--cd-line);border-radius:var(--r-sm);padding:12px 14px;font-size:11px}
  .tl-node{flex:1;text-align:center;position:relative;opacity:.75}
  .tl-node.active{opacity:1;font-weight:700}
  .tl-node::after{content:"";position:absolute;top:50%;right:-50%;width:100%;height:2px;background:var(--cd-line);z-index:0}
  .tl-node:last-child::after{display:none}
  .tl-node span{display:block;position:relative;z-index:1;background:var(--cd-node);border-radius:6px;padding:3px 2px;margin:0 2px}

  /* lists */
  .latest{margin-top:24px}
  .latest h2{font-size:15px;margin:0 0 10px;font-weight:700}
  .latest .row{display:flex;gap:10px;padding:9px 12px;border-bottom:1px solid var(--border);font-size:13px}
  .latest .row:last-child{border-bottom:none}
  .tag{font-size:11px;padding:2px 9px;border-radius:var(--r-pill);color:#fff;white-space:nowrap;font-weight:600;font-variant-numeric:tabular-nums}
  .tag.国考{background:var(--gk)}.tag.省考{background:var(--sk)}.tag.事业编{background:var(--sy)}
  .bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:8px 0 14px;position:sticky;top:0;background:var(--bg);padding:6px 0;z-index:5}
  .chip{padding:6px 14px;border:1px solid var(--border);border-radius:var(--r-pill);background:var(--card);cursor:pointer;font-size:13px;transition:background .15s,color .15s,border-color .15s}
  .chip.active{background:var(--accent);color:#fff;border-color:var(--accent)}
  select,input{padding:7px 11px;border:1px solid var(--border);border-radius:var(--r-sm);background:var(--card);font-size:13px;color:var(--text);transition:border-color .15s,box-shadow .15s}
  select:focus,input:focus{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
  input{min-width:180px}
  .card{background:var(--card);border:1px solid var(--border);border-radius:var(--r);padding:11px 14px;margin-bottom:8px;display:flex;gap:10px;align-items:flex-start;transition:border-color .15s}
  .card:hover{border-color:#CFDAC6}
  .card .body{flex:1;min-width:0}
  .card a{color:var(--text);text-decoration:none;font-weight:600;font-size:14px}
  .card a:hover{color:var(--accent);text-decoration:underline}
  .meta{color:var(--muted);font-size:11px;margin-top:3px}
  .new{background:var(--newbg);color:var(--newtx);font-size:10px;padding:1px 6px;border-radius:var(--r-pill);margin-left:6px;font-weight:600}
  .empty{color:var(--muted);text-align:center;padding:36px 20px;line-height:1.6}
  .sub{color:var(--muted);font-size:12px}
  /* 已读状态 + 搜索增强 */
  .rd-toggle{width:26px;height:26px;border-radius:50%;border:1.5px solid var(--border);background:var(--card);color:var(--muted);cursor:pointer;font-size:13px;flex:none;display:flex;align-items:center;justify-content:center;line-height:1;padding:0;transition:background .15s,color .15s,border-color .15s}
  .rd-toggle:hover{border-color:var(--accent);color:var(--accent)}
  .rd-toggle.read{background:var(--accent);color:#fff;border-color:var(--accent)}
  /* 已读不降低透明度/字重，保持清晰 */
  /* 置顶按钮 */
  .pin-toggle{width:26px;height:26px;border-radius:50%;border:1.5px solid var(--border);background:var(--card);color:var(--muted);cursor:pointer;font-size:13px;flex:none;display:flex;align-items:center;justify-content:center;line-height:1;padding:0;transition:background .15s,color .15s,border-color .15s}
  .pin-toggle:hover{border-color:var(--pin-fg);color:var(--pin-fg)}
  .pin-toggle.pinned{background:var(--pin-bg);border-color:var(--pin-fg);color:var(--pin-fg)}
  .grp.pin-grp>summary{background:var(--pin-grp-bg)}
  .badge{display:none;align-items:center;justify-content:center;min-width:18px;height:18px;padding:0 5px;margin-left:6px;border-radius:var(--r-pill);background:var(--badge-bg);color:#fff;font-size:11px;font-weight:700;line-height:1;font-variant-numeric:tabular-nums}
  .badge.show{display:inline-flex}
  .btn{padding:6px 14px;border:1px solid var(--border);border-radius:var(--r-pill);background:var(--card);cursor:pointer;font-size:13px;color:var(--text);transition:background .15s,border-color .15s}
  .btn:hover{border-color:var(--accent);color:var(--accent)}
  .btn:active{background:var(--accent-soft)}
  .search-wrap{position:relative;display:inline-flex;align-items:center}
  .search-wrap input{padding-right:28px;min-width:200px}
  .qclear{position:absolute;right:6px;border:none;background:transparent;color:var(--muted);font-size:16px;cursor:pointer;line-height:1;padding:4px}
  .qclear:hover{color:var(--text)}
  /* 公告汇总：按日期分组折叠 */
  .grp{border:1px solid var(--border);border-radius:var(--r);margin-bottom:10px;background:var(--card);overflow:hidden}
  .grp>summary{list-style:none;cursor:pointer;display:flex;align-items:center;gap:8px;padding:12px 14px;font-weight:700;font-size:14px;user-select:none;transition:background .15s}
  .grp>summary:hover{background:var(--accent-soft)}
  .grp>summary::-webkit-details-marker{display:none}
  .grp .grp-chev{color:var(--muted);font-size:11px;transition:transform .15s}
  .grp[open] .grp-chev{transform:rotate(90deg)}
  .grp .grpt{flex:1}
  .grp .grpc{background:var(--accent-soft);color:var(--accent-text);font-size:12px;padding:1px 9px;border-radius:var(--r-pill);font-weight:700;font-variant-numeric:tabular-nums}
  .grp .card,.grp .empty{margin-bottom:0;border-radius:0;border-left:none;border-right:none;border-bottom:none}
  .grp .card:first-of-type{border-top:none}
  .home-search{margin:4px 0 10px}

  /* 深色模式对等（手机 PWA 自动跟随系统） */
  @media (prefers-color-scheme: dark){
    :root{
      --bg:#151A14; --card:#1E241D; --text:#E6ECE2; --muted:#9AAA93;
      --border:#2C352A; --sidebg:#1A201A;
      --accent:#7FB069; --accent-soft:#243019; --accent-text:#A7D393;
      --gk:#D98A5E; --sk:#7FB069; --sy:#8AA6C6;
      --newbg:#4F8CFF; --badge-bg:#FF6B6F;
      --pin-bg:#3A3320; --pin-fg:#E6C453; --pin-grp-bg:#2C2817;
      --cd-text:#EAF0E4;
      --cd1:#223022; --cd2:#33472E;
      --cd-cell:rgba(255,255,255,.07); --cd-line:rgba(255,255,255,.16); --cd-node:rgba(255,255,255,.1);
    }
    .chip.active{background:var(--accent-soft);color:var(--accent-text);border-color:var(--accent)}
    .rd-toggle.read{background:var(--accent-soft);color:var(--accent-text);border-color:var(--accent)}
    .pin-toggle.pinned{background:var(--pin-bg);border-color:var(--pin-fg);color:var(--pin-fg)}
    .card:hover{border-color:#3A4636}
    .grp>summary:hover{background:var(--accent-soft)}
    select,input{background:var(--card)}
    select:focus,input:focus{box-shadow:0 0 0 3px var(--accent-soft)}
  }

  /* 尊重「减少动态效果」系统设置 */
  @media (prefers-reduced-motion: reduce){
    *{transition:none !important;animation:none !important;scroll-behavior:auto !important}
  }
  </style>
</head>
<body>
<div class="app">
  <aside>
    <div class="logo"><span class="logo-mark"><span class="logo-dot"></span>公考工作台</span><small>公告聚合 · 倒计时</small></div>
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
        <div class="search-wrap home-search"><input id="q-home" placeholder="搜索标题/地区/来源…"><button id="qclear-home" class="qclear" type="button" aria-label="清除">×</button></div>
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
        <span class="chip" data-cat="未读">未读</span>
        <select id="region"><option value="">全部地区</option></select>
        <div class="search-wrap"><input id="q" placeholder="搜索标题/地区/来源…"><button id="qclear" class="qclear" type="button" aria-label="清除">×</button></div>
        <button id="markall" class="btn" type="button">全部已读</button>
      </div>
      <div id="list"></div>
      <div class="empty" id="empty" style="display:none">🍃 没有匹配的公告</div>
    </div>
  </main>
</div>
<nav class="mobile-tab">
  <a data-view="home" class="active"><span class="ico">⌛</span><span class="txt">倒计时</span></a>
  <a data-view="notices"><span class="ico">📋</span><span class="txt">公告</span><span class="badge" data-badge="notices"></span></a>
</nav>
<script>
const DATA = /*__DATA__*/;
const EXAM = /*__EXAM__*/;

// ===== 已读状态（localStorage，按 url 标识） =====
const READ_KEY = "enw_read_v1";
function loadRead(){ try{ return new Set(JSON.parse(localStorage.getItem(READ_KEY)||"[]")); }catch(e){ return new Set(); } }
function saveRead(s){ try{ localStorage.setItem(READ_KEY, JSON.stringify([...s])); }catch(e){} }
let readSet = loadRead();
function isRead(n){ return readSet.has(n.url); }
function markRead(url){ if(!readSet.has(url)){ readSet.add(url); saveRead(readSet); afterReadChange(); } }
function toggleRead(url){ if(readSet.has(url)) readSet.delete(url); else readSet.add(url); saveRead(readSet); afterReadChange(); }
function afterReadChange(){ renderNotices(); renderLatest(); updateBadge(); }
function updateBadge(){
  const unread = DATA.notices.filter(n=>!isRead(n)).length;
  document.querySelectorAll('[data-badge="notices"]').forEach(b=>{
    b.textContent = unread>0 ? unread : "";
    b.classList.toggle("show", unread>0);
  });
}
function enc(u){ return encodeURIComponent(u); }
// ===== 置顶状态（localStorage，按 url 标识） =====
const PIN_KEY = "enw_pin_v1";
function loadPin(){ try{ return new Set(JSON.parse(localStorage.getItem(PIN_KEY)||"[]")); }catch(e){ return new Set(); } }
function savePin(s){ try{ localStorage.setItem(PIN_KEY, JSON.stringify([...s])); }catch(e){} }
let pinSet = loadPin();
function isPinned(n){ return pinSet.has(n.url); }
function togglePin(url){ if(pinSet.has(url)) pinSet.delete(url); else pinSet.add(url); savePin(pinSet); renderNotices(); }
// 距今天数（用于分组默认展开最近 7 天）
function daysAgo(dateStr){
  if(!dateStr || dateStr==="日期未知") return null;
  const t = new Date(dateStr+" 00:00:00").getTime();
  const n = new Date(); n.setHours(0,0,0,0);
  return Math.round((n.getTime()-t)/86400000);
}
function cardHtml(n){
  const read = isRead(n);
  const pinned = isPinned(n);
  return `<div class="card${read?' read':''}">
    <button class="pin-toggle${pinned?' pinned':''}" data-url="${enc(n.url)}" type="button" title="${pinned?'取消置顶':'置顶到顶部'}">${pinned?'📌':'📍'}</button>
    <button class="rd-toggle${read?' read':''}" data-url="${enc(n.url)}" type="button" title="${read?'标记为未读':'标记为已读'}">${read?'✓':'○'}</button>
    <span class="tag ${catClass(n.category)}">${n.category||""}</span>
    <div class="body">
      <a href="${n.url}" data-url="${enc(n.url)}" target="_blank" rel="noopener">${n.title}${n.is_new?'<span class="new">今日新增</span>':''}</a>
      <div class="meta">${n.region||""} · ${fmt(n.date)} · 来源：${n.source||""}</div>
    </div>
  </div>`;
}
function rowHtml(n){
  const read = isRead(n);
  return `<div class="row${read?' read':''}">
    <button class="rd-toggle${read?' read':''}" data-url="${enc(n.url)}" type="button" title="${read?'标记为未读':'标记为已读'}">${read?'✓':'○'}</button>
    <span class="tag ${catClass(n.category)}">${n.category||""}</span>
    <a href="${n.url}" data-url="${enc(n.url)}" target="_blank" rel="noopener" style="color:var(--text);text-decoration:none;flex:1">${n.title}</a>
    <span class="sub">${n.region||""} · ${fmt(n.date)}</span>
  </div>`;
}
document.addEventListener('click', function(e){
  const pbtn = e.target.closest('.pin-toggle');
  if(pbtn){ e.preventDefault(); togglePin(decodeURIComponent(pbtn.dataset.url)); return; }
  const btn = e.target.closest('.rd-toggle');
  if(btn){ e.preventDefault(); toggleRead(decodeURIComponent(btn.dataset.url)); return; }
  const a = e.target.closest('a[data-url]');
  if(a && a.dataset.url){ markRead(decodeURIComponent(a.dataset.url)); }
});

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

function renderLatest(qArg){
  const q=(qArg||"").trim().toLowerCase();
  let list=[...DATA.notices].sort((a,b)=>{
    const ha=!!a.date, hb=!!b.date;
    if(ha!==hb) return hb-ha;  // 有 date 的排前
    if(ha) return b.date.localeCompare(a.date);
    return (b.first_seen||"").localeCompare(a.first_seen||"");
  });
  if(q){
    list=list.filter(n=>(n.title+" "+n.region+" "+n.source).toLowerCase().includes(q));
    list=list.slice(0,50);
  } else {
    list=list.slice(0,10);
  }
  document.getElementById("latest-list").innerHTML = list.map(rowHtml).join("") || `<div class="empty">🍃 ${q?"没有匹配的公告":"暂无公告"}</div>`;
}

function renderNotices(){
  const cat=document.querySelector(".chip.active").dataset.cat;
  const region=document.getElementById("region").value;
  const q=document.getElementById("q").value.trim().toLowerCase();
  let list=DATA.notices.filter(n=>{
    if(cat!=="全部"&&cat!=="未读"&&n.category!==cat) return false;
    if(cat==="未读"&&isRead(n)) return false;
    if(region&&n.region!==region) return false;
    if(q){
      const hay=(n.title+" "+n.region+" "+n.source).toLowerCase();
      if(!hay.includes(q)) return false;
    }
    return true;
  });
  list.sort((a,b)=>{
    const ha=!!a.date, hb=!!b.date;
    if(ha!==hb) return hb-ha;
    if(ha) return b.date.localeCompare(a.date);
    return (b.first_seen||"").localeCompare(a.first_seen||"");
  });
  // 置顶公告（始终展示，不参与日期折叠）；其余按日期分组
  const pinned = list.filter(n=>isPinned(n));
  const normal  = list.filter(n=>!isPinned(n));
  const groups={};
  normal.forEach(n=>{
    const k = n.date ? n.date : "日期未知";
    (groups[k]||(groups[k]=[])).push(n);
  });
  const keys=Object.keys(groups).sort((a,b)=>{
    if(a==="日期未知") return 1;
    if(b==="日期未知") return -1;
    return b.localeCompare(a);
  });
  const pinHtml = pinned.length
    ? `<details class="grp pin-grp" open><summary><span class="grp-chev">▶</span><span class="grpt">📌 置顶公告</span><span class="grpc">${pinned.length}</span></summary>${pinned.map(cardHtml).join("")}</details>`
    : "";
  const groupsHtml = keys.length
    ? keys.map(k=>{
        const open = (k==="日期未知") || (daysAgo(k)!==null && daysAgo(k)<=6);
        return `<details class="grp"${open?' open':''}><summary><span class="grp-chev">▶</span><span class="grpt">${k}</span><span class="grpc">${groups[k].length}</span></summary>${groups[k].map(cardHtml).join("")}</details>`;
      }).join("")
    : "";
  if(list.length===0){
    document.getElementById("list").innerHTML = `<div class="empty">🍃 没有匹配的公告</div>`;
  } else {
    document.getElementById("list").innerHTML = pinHtml + groupsHtml;
  }
  document.getElementById("empty").style.display = "none";
}

function markAllRead(){
  const cat=document.querySelector(".chip.active").dataset.cat;
  const region=document.getElementById("region").value;
  const q=document.getElementById("q").value.trim().toLowerCase();
  DATA.notices.forEach(n=>{
    if(cat!=="全部"&&cat!=="未读"&&n.category!==cat) return;
    if(cat==="未读"&&isRead(n)) return;
    if(region&&n.region!==region) return;
    if(q && !((n.title+" "+n.region+" "+n.source).toLowerCase().includes(q))) return;
    readSet.add(n.url);
  });
  saveRead(readSet); afterReadChange();
}

function switchView(v){
  document.getElementById("view-home").style.display = v==="home"?"":"none";
  document.getElementById("view-notices").style.display = v==="notices"?"":"none";
  document.querySelectorAll("nav a[data-view]").forEach(a=>a.classList.toggle("active", a.dataset.view===v));
  document.querySelectorAll(".mobile-tab a[data-view]").forEach(a=>a.classList.toggle("active", a.dataset.view===v));
  if(v==="notices") renderNotices();
  if(v==="home") renderLatest(document.getElementById("q-home").value);
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
  document.getElementById("qclear").onclick=()=>{ document.getElementById("q").value=""; renderNotices(); };
  document.getElementById("markall").onclick=markAllRead;
  document.getElementById("q-home").oninput=()=>renderLatest(document.getElementById("q-home").value);
  document.getElementById("qclear-home").onclick=()=>{ document.getElementById("q-home").value=""; renderLatest(""); };
  updateBadge();
}
init();
</script>
</body></html>
"""

TEMPLATE = TEMPLATE  # noqa


def build():
    conn = sqlite3.connect(DB)
    # 动态近 6 个月过滤（约 180 天），保留近期公告；空 date 的专题页始终保留
    cutoff = (datetime.date.today() - datetime.timedelta(days=180)).strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT source,category,region,title,url,date,first_seen,last_seen FROM notices "
        "WHERE date>=? OR date='' OR date IS NULL",
        (cutoff,)).fetchall()
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
