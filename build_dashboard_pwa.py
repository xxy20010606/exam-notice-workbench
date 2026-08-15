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
        '<a data-view="home" class="active"><span class="nav-dot"></span>倒计时</a>\n'
        '<a data-view="notices"><span class="nav-dot"></span>公告汇总<span class="badge" data-badge="notices"></span></a>'
    )





TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <meta name="theme-color" content="#F7F6F3">
  <link rel="manifest" href="manifest.webmanifest">
  <link rel="icon" href="icon.svg" type="image/svg+xml">
  <link rel="apple-touch-icon" href="icon.svg">
  <title>公考/事考 工作台</title>
<style>
  :root{
    /* minimalist-ui：暖白底 + 炭灰字 + 发丝边框 + 极淡彩标签 */
    --bg:#F7F6F3; --surface:#FFFFFF; --surface-2:#FBFBFA;
    --ink:#2F3437; --muted:#787774; --faint:#9B9A97;
    --line:#EAEAEA; --line-2:rgba(0,0,0,.06);
    --gk-bg:#FDEBEC; --gk-tx:#9F2F2D;
    --sk-bg:#EDF3EC; --sk-tx:#346538;
    --sy-bg:#E1F3FE; --sy-tx:#1F6C9F;
    --new-bg:#E1F3FE; --new-tx:#1F6C9F;
    --badge-bg:#9F2F2D;
    --pin-bg:#FBF3DB; --pin-fg:#956400; --pin-grp-bg:#FBF3DB;
    --cd-surface:#FFFFFF; --cd-cell:#FBFBFA;
    --cd-line:rgba(0,0,0,.06); --cd-node:rgba(0,0,0,.04);
    --r:12px; --r-sm:6px; --r-pill:999px;
    --sans:'SF Pro Display','Helvetica Neue',-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;
    --serif:'Newsreader','Playfair Display','Songti SC',serif;
    --mono:'SF Mono','JetBrains Mono',ui-monospace,monospace;
  }
  *{box-sizing:border-box}
  a{color:inherit}
  :focus-visible{outline:2px solid var(--ink);outline-offset:2px;border-radius:4px}
  body{margin:0;font-family:var(--sans);
       background:var(--bg);color:var(--ink);font-size:14px;line-height:1.6;-webkit-font-smoothing:antialiased}
  .app{display:flex;min-height:100vh}
  aside{width:230px;background:var(--surface);border-right:1px solid var(--line);padding:30px 18px;position:sticky;top:0;height:100vh;overflow:auto}
  .logo{font-family:var(--serif);font-size:21px;letter-spacing:-.02em;line-height:1.15;margin-bottom:6px;padding:0 4px;display:flex;flex-direction:column;gap:4px}
  .logo-mark{display:flex;align-items:center;white-space:nowrap}
  .logo-dot{width:7px;height:7px;border-radius:50%;background:var(--ink);margin-right:8px;flex:none}
  .logo small{color:var(--faint);font-weight:400;font-size:11px;letter-spacing:.04em;white-space:nowrap}
  .nav-dot{width:6px;height:6px;border-radius:50%;background:var(--faint);margin-right:9px;flex:none;transition:background .15s}
  nav a{display:flex;align-items:center;gap:8px;padding:11px 14px;border-radius:var(--r-sm);color:var(--muted);text-decoration:none;margin-bottom:4px;cursor:pointer;font-size:14px;line-height:1.3;transition:background .18s,color .18s}
  nav a:hover{background:var(--surface-2);color:var(--ink)}
  nav a.active{color:var(--ink);font-weight:600;background:var(--surface-2);position:relative}
  nav a.active .nav-dot{background:var(--ink)}
  main{flex:1;padding:34px 40px 60px;overflow:auto;max-width:1000px}
  h1{font-family:var(--serif);font-size:27px;font-weight:500;letter-spacing:-.02em;margin:0 0 16px}
  .grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px}

  /* 手机端底部导航（窄屏显示，隐藏左侧栏） */
  .mobile-tab{display:none}
  @media(max-width:880px){
    .app{flex-direction:column}
    aside{display:none}
    main{padding:24px 18px calc(76px + env(safe-area-inset-bottom));max-width:none}
    .mobile-tab{display:flex;position:fixed;bottom:0;left:0;right:0;
      background:var(--surface);border-top:1px solid var(--line);
      box-shadow:0 -2px 10px rgba(0,0,0,.05);z-index:30;
      padding-bottom:env(safe-area-inset-bottom)}
    .mobile-tab a{flex:1;display:flex;align-items:center;justify-content:center;gap:6px;
      padding:14px 0;color:var(--muted);text-decoration:none;
      font-size:15px;font-weight:600;line-height:1.3;cursor:pointer}
    .mobile-tab a.active{color:var(--ink);position:relative}
  .mobile-tab a.active::before{content:"";position:absolute;top:0;left:50%;transform:translateX(-50%);
    width:26px;height:3px;background:var(--ink);border-radius:0 0 3px 3px}
    .grid2{grid-template-columns:1fr}
    h1{font-size:21px}
    .cd-title{font-size:16px}
    .card a{font-size:13.5px}
  }

  /* countdown */
  .cd-card{background:var(--cd-surface);color:var(--ink);border:1px solid var(--line);border-radius:var(--r);padding:24px;position:relative;overflow:hidden;transition:box-shadow .2s}
  .cd-card:hover{box-shadow:0 2px 10px rgba(0,0,0,.04)}
  .cd-title{font-size:18px;font-weight:600;display:flex;justify-content:space-between;align-items:center;letter-spacing:-.01em}
  .cd-year{font-size:11px;font-family:var(--mono);background:var(--surface-2);color:var(--muted);padding:3px 9px;border-radius:var(--r-pill);letter-spacing:.03em}
  .cd-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:16px}
  .cd-cell{background:var(--cd-cell);border-radius:var(--r-sm);padding:16px}
  .cd-cell h4{margin:0 0 4px;font-size:11px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}
  .cd-cell .date{font-size:13px;color:var(--muted)}
  .cd-cell .days{font-family:var(--serif);font-size:44px;line-height:1;font-weight:500;margin-top:10px;letter-spacing:-.03em;font-variant-numeric:tabular-nums}
  .cd-cell .days small{font-size:14px;font-weight:400;color:var(--muted);font-family:var(--sans);margin-left:6px;letter-spacing:0}
  .cd-cell.expired .days{font-size:16px;font-weight:600;color:var(--muted)}
  .cd-note{font-size:11px;color:var(--muted);margin-top:14px;line-height:1.5}
  .timeline{display:flex;margin-top:16px;background:var(--cd-line);border-radius:var(--r-sm);padding:12px 14px;font-size:11px}
  .tl-node{flex:1;text-align:center;position:relative;color:var(--muted);opacity:.7}
  .tl-node.active{opacity:1;font-weight:700;color:var(--ink)}
  .tl-node::after{content:"";position:absolute;top:50%;right:-50%;width:100%;height:1px;background:var(--cd-line);z-index:0}
  .tl-node:last-child::after{display:none}
  .tl-node span{display:block;position:relative;z-index:1;background:var(--cd-node);border-radius:6px;padding:3px 2px;margin:0 2px}

  /* lists */
  .latest{margin-top:34px}
  .latest h2{font-size:14px;margin:0 0 12px;font-weight:600;letter-spacing:.02em;color:var(--ink)}
  .latest .row{display:flex;gap:12px;padding:13px 4px;align-items:flex-start;border-top:1px solid var(--line-2);font-size:13px}
  .latest .row:first-child{border-top:none}
  .tag{font-size:10px;letter-spacing:.05em;text-transform:uppercase;font-weight:700;padding:3px 9px;border-radius:var(--r-pill);white-space:nowrap;display:inline-flex;align-items:center;justify-content:center;text-align:center;min-width:46px;line-height:1.4;font-family:var(--sans)}
  .tag.国考{background:var(--gk-bg);color:var(--gk-tx)}
  .tag.省考{background:var(--sk-bg);color:var(--sk-tx)}
  .tag.事业编{background:var(--sy-bg);color:var(--sy-tx)}
  .card.read .tag{opacity:1}
  .bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:10px 0 16px;position:sticky;top:0;background:var(--bg);padding:8px 0;z-index:5}
  .chip{padding:6px 14px;border:1px solid var(--line);border-radius:var(--r-pill);background:var(--surface);cursor:pointer;font-size:13px;color:var(--muted);transition:background .15s,color .15s,border-color .15s}
  .chip.active{background:var(--ink);color:#fff;border-color:var(--ink)}
  select,input{padding:7px 11px;border:1px solid var(--line);border-radius:var(--r-sm);background:var(--surface);font-size:13px;color:var(--ink);font-family:var(--sans);transition:border-color .15s,box-shadow .15s}
  select:focus,input:focus{border-color:var(--ink);box-shadow:0 0 0 3px rgba(47,52,55,.08)}
  input{min-width:180px}
  .card{background:var(--surface);border:1px solid var(--line);border-radius:var(--r);padding:12px 14px;margin-bottom:10px;display:flex;gap:12px;align-items:flex-start;transition:border-color .15s,box-shadow .2s}
  .card:hover{box-shadow:0 2px 8px rgba(0,0,0,.04)}
  .card .body{flex:1;min-width:0}
  .card a{color:var(--ink);text-decoration:none;font-weight:600;font-size:14px}
  .card a:hover{color:var(--ink);text-decoration:underline;text-underline-offset:3px}
  .meta{color:var(--muted);font-size:11px;margin-top:3px;font-family:var(--mono)}
  .new{background:var(--new-bg);color:var(--new-tx);font-size:10px;padding:1px 7px;border-radius:var(--r-pill);margin-left:6px;font-weight:600;font-family:var(--sans)}
  .empty{color:var(--muted);text-align:center;padding:36px 20px;line-height:1.6}
  .sub{color:var(--muted);font-size:12px}
  /* 已读 / 置顶 切换按钮（SVG 图标，无 emoji） */
  .rd-toggle,.pin-toggle{width:28px;height:28px;border-radius:var(--r-sm);border:1px solid var(--line);background:var(--surface);color:var(--muted);cursor:pointer;flex:none;display:flex;align-items:center;justify-content:center;padding:0;transition:background .15s,color .15s,border-color .15s}
  .rd-toggle svg,.pin-toggle svg{width:14px;height:14px;display:block}
  .rd-toggle svg{opacity:0;transition:opacity .15s}
  .rd-toggle:hover{border-color:var(--ink);color:var(--ink)}
  .rd-toggle.read{background:var(--ink);border-color:var(--ink);color:#fff}
  .rd-toggle.read svg{opacity:1}
  /* 已读不降低透明度/字重，保持清晰 */
  .pin-toggle svg{opacity:.5;transition:opacity .15s}
  .pin-toggle:hover{border-color:var(--pin-fg);color:var(--pin-fg)}
  .pin-toggle.pinned{background:var(--pin-bg);border-color:var(--pin-fg);color:var(--pin-fg)}
  .pin-toggle.pinned svg{opacity:1}
  .grp.pin-grp>summary{background:var(--pin-grp-bg)}
  .badge{display:none;align-items:center;justify-content:center;min-width:18px;height:18px;padding:0 5px;margin-left:6px;border-radius:var(--r-pill);background:var(--badge-bg);color:#fff;font-size:11px;font-weight:700;line-height:1;font-variant-numeric:tabular-nums}
  .badge.show{display:inline-flex}
  .btn{padding:6px 14px;border:1px solid var(--line);border-radius:var(--r-pill);background:var(--surface);cursor:pointer;font-size:13px;color:var(--ink);transition:background .15s,border-color .15s}
  .btn:hover{border-color:var(--ink)}
  .btn:active{background:var(--surface-2)}
  .search-wrap{position:relative;display:inline-flex;align-items:center}
  .search-wrap input{padding-right:28px;min-width:200px}
  .qclear{position:absolute;right:6px;border:none;background:transparent;color:var(--muted);font-size:16px;cursor:pointer;line-height:1;padding:4px}
  .qclear:hover{color:var(--ink)}
  /* 公告汇总：按日期分组折叠（发丝线分隔，无整框） */
  .grp{border-top:1px solid var(--line);margin-top:14px}
  .grp:last-child{border-bottom:1px solid var(--line)}
  .grp>summary{list-style:none;cursor:pointer;display:flex;align-items:center;gap:12px;padding:16px 4px;font-weight:600;font-size:15px;user-select:none;color:var(--ink)}
  .grp>summary::-webkit-details-marker{display:none}
  .grp .grp-chev{color:var(--faint);font-family:var(--mono);font-size:15px;width:14px;text-align:center}
  .pin-ico{color:var(--pin-fg);display:inline-flex}
  .pin-ico svg{width:15px;height:15px}
  .grp .grpt{flex:1}
  .grp .grpc{font-family:var(--mono);font-size:12px;color:var(--muted);font-weight:600;padding:1px 9px;border-radius:var(--r-pill);background:var(--surface-2)}
  .grp .card,.grp .empty{margin-bottom:0;border-radius:0;border-left:none;border-right:none;border-bottom:none;border-top:1px solid var(--line-2)}
  .grp .card:first-of-type{border-top:none}
  .home-search{margin:4px 0 6px}

  /* 深色模式对等（手机 PWA 自动跟随系统） */
  @media (prefers-color-scheme: dark){
    :root{
      --bg:#1A1A18; --surface:#212120; --surface-2:#28271F;
      --ink:#ECECE8; --muted:#9A9A93; --faint:#6E6E66;
      --line:#2E2E2B; --line-2:rgba(255,255,255,.08);
      --gk-bg:#3A2A2A; --gk-tx:#E89A96;
      --sk-bg:#26332A; --sk-tx:#9FC4A0;
      --sy-bg:#26323C; --sy-tx:#9CC0E0;
      --new-bg:#26323C; --new-tx:#9CC0E0;
      --badge-bg:#E0837F;
      --pin-bg:#3A3320; --pin-fg:#E6C453; --pin-grp-bg:#332E1C;
      --cd-surface:#212120; --cd-cell:#28271F;
      --cd-line:rgba(255,255,255,.08); --cd-node:rgba(255,255,255,.06);
    }
    .chip.active{background:var(--ink);color:#1A1A18;border-color:var(--ink)}
    .rd-toggle.read{background:var(--ink);border-color:var(--ink);color:#1A1A18}
    .rd-toggle.read svg{opacity:1}
    .pin-toggle.pinned{background:var(--pin-bg);border-color:var(--pin-fg);color:var(--pin-fg)}
    .card:hover{box-shadow:0 2px 8px rgba(0,0,0,.3)}
    .grp .grpc{background:var(--surface-2)}
    select,input{background:var(--surface)}
    select:focus,input:focus{box-shadow:0 0 0 3px rgba(236,236,232,.12)}
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
      <h1>备考倒计时</h1>
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
      <div class="empty" id="empty" style="display:none">没有匹配的公告</div>
    </div>
  </main>
</div>
<nav class="mobile-tab">
  <a data-view="home" class="active"><span class="txt">倒计时</span></a>
  <a data-view="notices"><span class="txt">公告</span><span class="badge" data-badge="notices"></span></a>
</nav>
<script>
const DATA = /*__DATA__*/;
const EXAM = /*__EXAM__*/;
const ICON_CHECK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>';
const ICON_PIN  = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M14 2l8 8-3 3-2-2-4 4v4l-2 2-3-3 2-2H6l-4-4 2-2h4l4-4-2-2 3-3z"/></svg>';

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
    <button class="pin-toggle${pinned?' pinned':''}" data-url="${enc(n.url)}" type="button" title="${pinned?'取消置顶':'置顶到顶部'}">${ICON_PIN}</button>
    <button class="rd-toggle${read?' read':''}" data-url="${enc(n.url)}" type="button" title="${read?'标记为未读':'标记为已读'}">${ICON_CHECK}</button>
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
    <button class="rd-toggle${read?' read':''}" data-url="${enc(n.url)}" type="button" title="${read?'标记为未读':'标记为已读'}">${ICON_CHECK}</button>
    <span class="tag ${catClass(n.category)}">${n.category||""}</span>
    <a href="${n.url}" data-url="${enc(n.url)}" target="_blank" rel="noopener" style="color:var(--ink);text-decoration:none;flex:1">${n.title}</a>
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

// 折叠符号 +/− 同步（置顶组用 SVG 图标，跳过）
document.addEventListener('toggle', function(e){
  const d = e.target;
  if(d.tagName === 'DETAILS'){
    const s = d.querySelector(':scope > summary .grp-chev');
    if(s && !s.querySelector('svg')) s.textContent = d.open ? '−' : '+';
  }
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
  document.getElementById("latest-list").innerHTML = list.map(rowHtml).join("") || `<div class="empty">${q?"没有匹配的公告":"暂无公告"}</div>`;
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
    ? `<details class="grp pin-grp" open><summary><span class="pin-ico">${ICON_PIN}</span><span class="grpt">置顶公告</span><span class="grpc">${pinned.length}</span></summary>${pinned.map(cardHtml).join("")}</details>`
    : "";
  const groupsHtml = keys.length
    ? keys.map(k=>{
        const open = (k==="日期未知") || (daysAgo(k)!==null && daysAgo(k)<=6);
        return `<details class="grp"${open?' open':''}><summary><span class="grp-chev">${open?'−':'+'}</span><span class="grpt">${k}</span><span class="grpc">${groups[k].length}</span></summary>${groups[k].map(cardHtml).join("")}</details>`;
      }).join("")
    : "";
  if(list.length===0){
    document.getElementById("list").innerHTML = `<div class="empty">没有匹配的公告</div>`;
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
