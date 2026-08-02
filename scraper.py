#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
公告聚合爬虫：读取 sources.json，按源抓取并解析公告，存入 SQLite，返回本次新增。
- method="http"    : 直接 requests 式抓取（适用于服务端渲染、未封 IP 的站点）
- method="browser" : 用 Playwright 无头浏览器执行 JS（适用于反爬/JS 渲染站点，如国考）
"""
import json, os, sqlite3, hashlib, re, time, datetime
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(ROOT, "notices.db")
SOURCES = os.path.join(ROOT, "sources.json")

DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# 全局过滤：去掉导航/页脚等噪音链接
GLOBAL_EXCLUDE = (r"首页|网站地图|联系我们|无障碍|English|EN$|登录|注册|后台|投稿|订阅|"
                  r"邮箱|微信|微博|客户端|下载|帮助|指南|隐私|版权|备案|关于我们|站点|"
                  r"留言|举报|督查|信访|繁體|无障碍版|纠错|收藏|分享")

DATE_RE = [re.compile(r"(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})"),]
URL_DATE_RE = re.compile(r"(\d{4})[-/_](\d{2})[-/_](\d{2})")


def now_iso():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------- 抓取 ----------------
def fetch_http(url, timeout=15, retries=2):
    import urllib.request, ssl, gzip
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    last = None
    for _ in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": DEFAULT_UA,
                "Accept-Encoding": "gzip",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Referer": url,
            })
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                m = re.search(r"charset=([\w-]+)", r.headers.get("Content-Type", ""))
                cs = (m.group(1).lower() if m else "utf-8")
                return raw.decode(cs, errors="ignore")
        except Exception as e:
            last = e
            time.sleep(1.5)
    raise last


_browser = None


def _get_browser():
    global _browser
    if _browser is None:
        from playwright.sync_api import sync_playwright
        p = sync_playwright().start()
        _browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
    return _browser


def fetch_browser(url, timeout=30000, wait=3000):
    browser = _get_browser()
    page = browser.new_page(user_agent=DEFAULT_UA)
    try:
        page.goto(url, timeout=timeout, wait_until="domcontentloaded")
        page.wait_for_timeout(wait)
        html = page.content()
        # 国考等反爬：JS 设 cookie 后 location 跳转，若仍是挑战页则重载再等
        if "EO_Bot" in html or "tads" in html:
            page.reload(wait_until="domcontentloaded")
            page.wait_for_timeout(wait + 1500)
            html = page.content()
        return html
    finally:
        page.close()


def _raw_fetch(url, method):
    if method == "browser":
        try:
            return fetch_browser(url)
        except Exception as e:
            msg = str(e)
            if "playwright" in msg.lower() or "no module" in msg.lower():
                raise RuntimeError("Playwright 未安装，无法抓取浏览器型源（" + url + "）")
            raise
    return fetch_http(url)


def fetch(source):
    """抓取入口：支持先抓首页、再自动跟进“公务员/省考专栏”仅一层。"""
    method = source.get("method", "http")
    html = _raw_fetch(source["url"], method)
    clr = source.get("column_link_regex")
    if clr:
        soup = BeautifulSoup(html, "html.parser")
        pat = re.compile(clr)
        for a in soup.find_all("a", href=True):
            t = a.get("title") or a.get_text(strip=True)
            href = a["href"].strip()
            if t and pat.search(t) and href and not href.startswith(("#", "javascript:")):
                col_url = urljoin(source["url"], href)
                try:
                    col = _raw_fetch(col_url, method)
                    html = html + "\n" + col   # 合并首页与专栏，避免错过任一处公告
                except Exception:
                    pass
                break
    return html


# ---------------- 解析 ----------------
def extract_date(text, url):
    m = URL_DATE_RE.search(url)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    for r in DATE_RE:
        m = r.search(text or "")
        if m:
            return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return ""


def parse_source(source, html):
    soup = BeautifulSoup(html, "html.parser")
    base = source.get("base") or source["url"]
    scope = soup
    if source.get("container"):
        node = soup.select_one(source["container"])
        if node:
            scope = node
    inc = re.compile(source["title_include"]) if source.get("title_include") else None
    exc_src = re.compile(source["title_exclude"]) if source.get("title_exclude") else None
    exc_glob = re.compile(GLOBAL_EXCLUDE)
    items, seen = [], set()
    for a in scope.find_all("a", href=True):
        title = (a.get("title") or a.get_text(strip=True) or "")
        if not title:
            continue
        if inc and not inc.search(title):
            continue
        if (exc_src and exc_src.search(title)) or exc_glob.search(title):
            continue
        href = a["href"].strip()
        if not href or href.startswith(("javascript:", "#", "mailto:", "tel:")):
            continue
        absurl = urljoin(base, href).split("?")[0].split("#")[0]
        if absurl in seen:
            continue
        seen.add(absurl)
        parent = a.find_parent(["li", "div", "tr", "td", "p"]) or a
        ptext = parent.get_text(" ", strip=True) if parent else title
        items.append({"title": title[:200], "url": absurl, "date": extract_date(ptext, absurl)})
    return items


# ---------------- 存储 ----------------
def init_db():
    conn = sqlite3.connect(DB)
    conn.execute("""CREATE TABLE IF NOT EXISTS notices(
        id TEXT PRIMARY KEY, source TEXT, category TEXT, region TEXT,
        title TEXT, url TEXT, date TEXT, first_seen TEXT, last_seen TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS sources_status(
        name TEXT PRIMARY KEY, last_run TEXT, last_count INT, last_error TEXT)""")
    conn.commit()
    return conn


def url_id(u):
    return hashlib.sha1(u.encode()).hexdigest()[:24]


def store(conn, source, items):
    new = []
    ts = now_iso()
    for it in items:
        uid = url_id(it["url"])
        cur = conn.execute("SELECT id FROM notices WHERE id=?", (uid,)).fetchone()
        if cur:
            conn.execute("UPDATE notices SET last_seen=?, title=?, date=? WHERE id=?",
                         (ts, it["title"], it["date"], uid))
        else:
            conn.execute("""INSERT INTO notices(id,source,category,region,title,url,date,first_seen,last_seen)
                            VALUES(?,?,?,?,?,?,?,?,?)""",
                         (uid, source["name"], source.get("category", ""), source.get("region", ""),
                          it["title"], it["url"], it["date"], ts, ts))
            new.append(it)
    conn.commit()
    return new


# ---------------- 主流程 ----------------
def _work(s, limit_per_source=40):
    """单源抓取+解析（线程安全：不碰 DB）。返回 (source, items, error)。"""
    try:
        html = fetch(s)
        items = parse_source(s, html)[:limit_per_source]
        return s, items, None
    except Exception as e:
        return s, [], str(e)


def run_all(limit_per_source=40, max_workers=8):
    with open(SOURCES, encoding="utf-8") as f:
        sources = json.load(f)
    conn = init_db()
    report = {"run_at": now_iso(), "sources": [], "new": []}

    http_sources = [s for s in sources if s.get("method", "http") != "browser"]
    browser_sources = [s for s in sources if s.get("method", "http") == "browser"]

    # http 源并发抓取（每个线程只 fetch+parse，不写库，避免 SQLite 跨线程）
    results = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_work, s, limit_per_source): s["name"] for s in http_sources}
        for fut in as_completed(futures):
            name = futures[fut]
            s, items, err = fut.result()
            results[name] = (s, items, err)

    # 浏览器源（反爬、共享单例 browser）顺序抓取，避免并发冲突
    for s in browser_sources:
        s2, items, err = _work(s, limit_per_source)
        results[s2["name"]] = (s2, items, err)

    # 主线程顺序写库（SQLite 线程安全）
    for s in sources:
        s2, items, err = results[s["name"]]
        if err:
            conn.execute("INSERT OR REPLACE INTO sources_status(name,last_run,last_count,last_error) VALUES(?,?,?,?)",
                         (s["name"], now_iso(), 0, err[:300]))
            report["sources"].append({"name": s["name"], "category": s.get("category"),
                                       "region": s.get("region"), "count": 0, "new": 0,
                                       "error": err[:200]})
        else:
            new = store(conn, s, items)
            conn.execute("INSERT OR REPLACE INTO sources_status(name,last_run,last_count,last_error) VALUES(?,?,?,?)",
                         (s["name"], now_iso(), len(items), ""))
            report["sources"].append({"name": s["name"], "category": s.get("category"),
                                       "region": s.get("region"), "count": len(items),
                                       "new": len(new), "error": None})
            for n in new:
                report["new"].append({"source": s["name"], "category": s.get("category"),
                                       "region": s.get("region"), **n})
    conn.commit()
    conn.close()
    return report


if __name__ == "__main__":
    print(json.dumps(run_all(), ensure_ascii=False, indent=2))
