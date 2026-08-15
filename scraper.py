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

# 全局噪声（非招聘公告）：与 daily_digest 保持一致，在「入库」环节即过滤，
# 防止采购/中标/询价/导航文字/泛化栏目名等 junk 进入 notices.db 与看板。
GLOBAL_NOISE = re.compile(
    r"采购|中标|询价|成交|招标|竞价|政府采购|单一来源|资格预审.*采购"
    r"|^/\s"                  # 导航栏文字（以 / 开头）
    r"|^事业单位公开招聘$"     # 泛化栏目名（非具体公告）
    r"|^公开招聘$"            # 泛化栏目名
)

DATE_RE = [re.compile(r"(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})"),]
URL_DATE_RE = re.compile(r"(\d{4})[-/_](\d{2})[-/_](\d{2})")


def now_iso():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------- 抓取 ----------------
def fetch_http(url, timeout=30, retries=2, encoding=None):
    import urllib.request, ssl, gzip
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    # 兼容老旧 TLS 重新协商（部分 gov 站如江西人事考试网使用旧协议）
    if hasattr(ssl, "OP_LEGACY_SERVER_CONNECT"):
        try:
            ctx.options |= ssl.OP_LEGACY_SERVER_CONNECT
        except Exception:
            pass
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
                cs = encoding if encoding else None
                if not cs:
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
        # Chromium 网络参数：禁用代理(直连gov.cn)、强制IPv4、忽略SSL、规避反爬检测
        _browser = p.chromium.launch(headless=True, args=[
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--no-proxy-server",
            "--disable-ipv6",
            "--ignore-certificate-errors",
        ])
    return _browser


def _block_heavy(route, request):
    """拦截图片/媒体/字体/样式表等重资源：gov 站这些常拖到 90s 超时。
    只保留文档与脚本，大幅加快页面可达，避免网络超时（iframe 主文档为
    document 类型不会被拦截，列表内容照常抓取）。"""
    try:
        if request.resource_type in ("image", "media", "font", "stylesheet"):
            route.abort()
            return
    except Exception:
        pass
    try:
        route.continue_()
    except Exception:
        pass


def fetch_browser(url, timeout=90000, wait=3000, wait_until="domcontentloaded", retries=2):
    last = None
    for attempt in range(retries + 1):
        try:
            browser = _get_browser()
            ctx = browser.new_context(user_agent=DEFAULT_UA, ignore_https_errors=True)
            try:
                ctx.route("**/*", _block_heavy)
            except Exception:
                pass
            page = ctx.new_page()
            try:
                page.goto(url, timeout=timeout, wait_until=wait_until)
                page.wait_for_timeout(wait)
                html = page.content()
                # 浙江 JCMS 等政府站群把文章列表放在 <iframe> 内，主文档无 <a> 链接；
                # 必须把所有 iframe 的渲染内容也拼进来，否则抓不到任何公告
                try:
                    for frame in page.frames:
                        if frame is page.main_frame:
                            continue
                        try:
                            fhtml = frame.content()
                            if fhtml:
                                html += "\n" + fhtml
                        except Exception:
                            pass
                except Exception:
                    pass
                # 国考等反爬：JS 设 cookie 后 location 跳转，若仍是挑战页则重载再等
                if "EO_Bot" in html or "tads" in html:
                    page.reload(wait_until=wait_until)
                    page.wait_for_timeout(wait + 1500)
                    html = page.content()
                return html
            finally:
                page.close()
                ctx.close()
        except Exception as e:
            last = e
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
    raise last


def _raw_fetch(url, method, encoding=None, browser_wait=3000, browser_wait_until="domcontentloaded", browser_timeout=90000, http_timeout=15):
    if method == "browser":
        try:
            return fetch_browser(url, timeout=browser_timeout, wait=browser_wait, wait_until=browser_wait_until)
        except Exception as e:
            msg = str(e)
            if "playwright" in msg.lower() or "no module" in msg.lower():
                raise RuntimeError("Playwright 未安装，无法抓取浏览器型源（" + url + "）")
            raise
    return fetch_http(url, encoding=encoding, timeout=http_timeout)


def fetch(source):
    """抓取入口：支持先抓首页、再自动跟进“公务员/省考专栏”仅一层。"""
    method = source.get("method", "http")
    encoding = source.get("encoding")
    browser_wait = source.get("browser_wait", 3000)
    browser_wait_until = source.get("browser_wait_until", "domcontentloaded")
    browser_timeout = source.get("browser_timeout", 90000)
    http_timeout = source.get("http_timeout", 15)
    html = _raw_fetch(source["url"], method, encoding=encoding,
                      browser_wait=browser_wait, browser_wait_until=browser_wait_until,
                      browser_timeout=browser_timeout, http_timeout=http_timeout)
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
                    col = _raw_fetch(col_url, method, encoding=encoding, browser_wait=browser_wait)
                    html = html + "\n" + col   # 合并首页与专栏，避免错过任一处公告
                except Exception:
                    pass
                break
    # 分页跟进（如西藏通知公告 index_2.html / index_3.html ...）
    fp = source.get("follow_pages")
    if fp:
        fpat = re.compile(fp)
        visited = {source["url"].rstrip("/")}
        max_pages = source.get("max_pages", 5)
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if fpat.search(href):
                purl = urljoin(source["url"], href).rstrip("/")
                if purl in visited:
                    continue
                visited.add(purl)
                if len(visited) > max_pages + 1:
                    break
                try:
                    ph = _raw_fetch(purl, method, encoding=encoding,
                                   browser_wait=browser_wait, browser_wait_until=browser_wait_until)
                    html = html + "\n" + ph
                except Exception:
                    pass
    # 显式多页（page_urls）：用于分页靠 JS、首页无静态分页链接的站点（如西藏）
    for purl in source.get("page_urls", []):
        try:
            ph = _raw_fetch(purl, method, encoding=encoding,
                           browser_wait=browser_wait, browser_wait_until=browser_wait_until)
            html = html + "\n" + ph
        except Exception:
            pass
    return html


# ---------------- 解析 ----------------
def extract_date(text, url):
    text = re.sub(r"\s+", "", text or "")  # 归一化（如嘉兴日期 span 内 "2026- 08- 13" 带空格）
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
    href_inc = re.compile(source["href_include"]) if source.get("href_include") else None
    exc_glob = re.compile(GLOBAL_EXCLUDE)

    # 表格行模式：Vue / Ant-Design 等 SPA 列表，条目为 <tr> 且不含 <a> 链接
    if source.get("row_table"):
        node = soup
        if source.get("container"):
            c = soup.select_one(source["container"])
            if c:
                node = c
        items, seen = [], set()
        t_idx = int(source.get("row_title_idx", 0))
        d_idx = int(source.get("row_date_idx", 1))
        for tr in node.select(source.get("row_selector", "tr")):
            tds = tr.find_all("td")
            if len(tds) <= max(t_idx, d_idx if d_idx >= 0 else -1):
                continue
            title = tds[t_idx].get_text(" ", strip=True)
            if not title:
                continue
            if inc and not inc.search(title):
                continue
            # row_table 模式只套用该源的 include/exclude，不套 GLOBAL_EXCLUDE：
            # 结构化列表标题已精确，全局排除（如“下载”）会误杀“面试通知单下载入口”等
            if exc_src and exc_src.search(title):
                continue
            date = tds[d_idx].get_text(" ", strip=True) if (d_idx >= 0 and len(tds) > d_idx) else ""
            if source.get("row_url_template") and tr.get("data-row-key"):
                try:
                    url = source["row_url_template"].format(tr.get("data-row-key"))
                except Exception:
                    url = source.get("base") or source["url"]
            else:
                url = source.get("base") or source["url"]
            # row_table 行内多为同一源 URL，store() 以 url_id(url) 作主键去重会丢行；
            # 用「标题+日期」造唯一 fragment 作为主键，保证每行独立入库
            url = url.split("#")[0] + "#" + hashlib.md5((title + "|" + date).encode("utf-8")).hexdigest()[:12]
            # 去重键用 标题+日期（行内 url 多为同一源 URL，不能用 url 去重）
            uid = title + "|" + date
            if uid in seen:
                continue
            seen.add(uid)
            items.append({"title": title[:200], "url": url, "date": extract_date(date, url)})
        return items

    items, seen = [], set()
    o_re = re.compile(source["onclick_regex"]) if source.get("onclick_regex") else None
    o_tpl = source.get("onclick_url_template")
    for a in scope.find_all("a", href=True):
        title = (a.get("title") or a.get_text(strip=True) or "")
        if not title:
            continue
        if inc and not inc.search(title):
            continue
        if (exc_src and exc_src.search(title)) or exc_glob.search(title):
            continue
        href = (a.get("href") or "").strip()
        # href_include：仅保留链接命中该正则的 <a>（如 JCMS 文章详情页 /art/，
        # 排除 /col/.../index.html 等栏目导航链接），从源头杜绝导航噪声漏入
        if href_inc and href and not href_inc.search(href):
            continue
        # 处理 onclick 拼接的真实链接（如黑龙江公务员考试网 queryDetail('mkxh','tzid')）
        if (not href or href.startswith(("#", "javascript:", "mailto:", "tel:"))) and o_re and o_tpl:
            onclick = a.get("onclick") or ""
            m = o_re.search(onclick)
            if m:
                try:
                    href = o_tpl.format(*m.groups())
                except Exception:
                    href = ""
        if not href or href.startswith(("javascript:", "mailto:", "tel:")):
            continue
        absurl = urljoin(base, href).split("#")[0]
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
    # 兼容老库：渐进加列（fail_streak=连续失败次数；last_attempt_at=上次实际尝试时间）
    cols = {r[1] for r in conn.execute("PRAGMA table_info(sources_status)").fetchall()}
    if "fail_streak" not in cols:
        conn.execute("ALTER TABLE sources_status ADD COLUMN fail_streak INT DEFAULT 0")
    if "last_attempt_at" not in cols:
        conn.execute("ALTER TABLE sources_status ADD COLUMN last_attempt_at TEXT")
    conn.commit()
    return conn


def url_id(u):
    return hashlib.sha1(u.encode()).hexdigest()[:24]


def store(conn, source, items):
    new = []
    ts = now_iso()
    dropped = 0
    for it in items:
        # 入库即过滤全局噪声，避免 junk 污染 notices.db 与看板
        if GLOBAL_NOISE.search(it["title"] or ""):
            dropped += 1
            continue
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
    if dropped:
        print(f"[噪声] {source['name']} 入库时过滤 {dropped} 条 junk")
    conn.commit()
    return new


def cleanup_noise():
    """定期/幂等清理：删除库中遗留的全局噪声记录（入库过滤上线前的历史 junk）。

    注意：采购/中标等用 LIKE 模糊匹配（真实招聘公告不会含这些词）；
    而「事业单位公开招聘」「公开招聘」这类泛化栏目名必须用精确匹配，
    否则会误删「XX事业单位公开招聘工作人员公告」等真实公告。
    """
    conn = init_db()
    like_patterns = ["%采购%", "%中标%", "%询价%", "%成交%", "%招标%", "%竞价%",
                     "%政府采购%", "%单一来源%", "/%"]
    exact_patterns = ["事业单位公开招聘", "公开招聘"]
    n = 0
    for p in like_patterns:
        for (rid,) in conn.execute("SELECT id FROM notices WHERE title LIKE ?", (p,)).fetchall():
            conn.execute("DELETE FROM notices WHERE id=?", (rid,))
            n += 1
    for p in exact_patterns:
        for (rid,) in conn.execute("SELECT id FROM notices WHERE title = ?", (p,)).fetchall():
            conn.execute("DELETE FROM notices WHERE id=?", (rid,))
            n += 1
    conn.commit()
    conn.close()
    if n:
        print(f"[清理] 删除 {n} 条历史噪声记录")
    return n


# ---------------- 主流程 ----------------
def _work(s, limit_per_source=40):
    """单源抓取+解析（线程安全：不碰 DB）。返回 (source, items, error)。"""
    try:
        html = fetch(s)
        items = parse_source(s, html)[:limit_per_source]
        return s, items, None
    except Exception as e:
        return s, [], str(e)


def run_all(limit_per_source=40, max_workers=8,
            skip_fail_threshold=10, retry_after_hours=12,
            only_region=None, exclude_region=None):
    """
    自适应跳过失败源：
    - 连续失败 ≥ skip_fail_threshold 次的源，本轮跳过 fetch（不耗超时）
    - 距 last_attempt_at ≥ retry_after_hours 小时后强制重试一次
    - 重试成功 → fail_streak 归零；仍失败 → fail_streak+1，重新进入跳过窗口
    目的：云端 IP 对部分中国 gov 站点系统性不可达，跳过这些源可省 1-2min。

    区域过滤（混合架构用）：
    - only_region:   只抓取 region 包含该关键字的源（自托管 Runner 只跑浙江）
    - exclude_region: 跳过 region 包含该关键字的源（云端只跑非浙江）
    """
    with open(SOURCES, encoding="utf-8") as f:
        sources = json.load(f)
    # 混合架构：云端跑非浙江、自托管只跑浙江，互不污染 fail_streak
    if only_region:
        sources = [s for s in sources if only_region in (s.get("region", "") or "")]
    if exclude_region:
        sources = [s for s in sources if exclude_region not in (s.get("region", "") or "")]
    conn = init_db()
    report = {"run_at": now_iso(), "sources": [], "new": []}

    # 读取 sources_status 计算本轮要跳过的源
    rows = conn.execute(
        "SELECT name, fail_streak, last_attempt_at FROM sources_status"
    ).fetchall()
    fail_info = {r[0]: {"streak": r[1] or 0, "last_attempt_at": r[2] or ""} for r in rows}
    now_dt = datetime.datetime.now()

    def _should_skip(name):
        st = fail_info.get(name)
        if not st or st["streak"] < skip_fail_threshold:
            return False, ""
        last_str = st["last_attempt_at"]
        if not last_str:
            return False, ""
        try:
            last_dt = datetime.datetime.strptime(last_str, "%Y-%m-%d %H:%M:%S")
            elapsed_h = (now_dt - last_dt).total_seconds() / 3600
            if elapsed_h >= retry_after_hours:
                return False, ""  # 超过重试窗口，本轮重试
            return True, f"已连续失败 {st['streak']} 次，下次重试约 {retry_after_hours - elapsed_h:.1f}h 后"
        except Exception:
            return False, ""

    active_sources, skipped_sources = [], []
    for s in sources:
        skip, reason = _should_skip(s["name"])
        if skip:
            skipped_sources.append((s, reason))
            report["sources"].append({
                "name": s["name"], "category": s.get("category"),
                "region": s.get("region"), "count": 0, "new": 0,
                "error": None, "skipped": True, "skip_reason": reason,
            })
        else:
            active_sources.append(s)

    http_sources = [s for s in active_sources if s.get("method", "http") != "browser"]
    browser_sources = [s for s in active_sources if s.get("method", "http") == "browser"]

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
    now_str = now_iso()
    for s in active_sources:
        s2, items, err = results[s["name"]]
        if err:
            old = fail_info.get(s["name"], {}).get("streak", 0)
            new_streak = old + 1
            conn.execute("""INSERT OR REPLACE INTO sources_status
                            (name,last_run,last_count,last_error,fail_streak,last_attempt_at)
                            VALUES(?,?,?,?,?,?)""",
                         (s["name"], now_str, 0, err[:300], new_streak, now_str))
            report["sources"].append({"name": s["name"], "category": s.get("category"),
                                       "region": s.get("region"), "count": 0, "new": 0,
                                       "error": err[:200]})
        else:
            new = store(conn, s, items)
            conn.execute("""INSERT OR REPLACE INTO sources_status
                            (name,last_run,last_count,last_error,fail_streak,last_attempt_at)
                            VALUES(?,?,?,?,?,?)""",
                         (s["name"], now_str, len(items), "", 0, now_str))
            report["sources"].append({"name": s["name"], "category": s.get("category"),
                                       "region": s.get("region"), "count": len(items),
                                       "new": len(new), "error": None})
            for n in new:
                report["new"].append({"source": s["name"], "category": s.get("category"),
                                       "region": s.get("region"), **n})

    # 更新被跳过源的 last_run（让 UI 显示"上次跳过时间"）；fail_streak/last_attempt_at 不变
    for s, _reason in skipped_sources:
        conn.execute("""UPDATE sources_status SET last_run=? WHERE name=?""",
                     (now_str, s["name"]))

    conn.commit()
    conn.close()
    return report


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="公考事考公告抓取")
    ap.add_argument("--only-region", help="只抓取 region 包含该关键字的源（如 浙江）")
    ap.add_argument("--exclude-region", help="跳过 region 包含该关键字的源（如 浙江）")
    args = ap.parse_args()
    print(json.dumps(run_all(only_region=args.only_region, exclude_region=args.exclude_region), ensure_ascii=False, indent=2))
