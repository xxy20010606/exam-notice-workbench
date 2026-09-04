#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
公告聚合爬虫：读取 sources.json，按源抓取并解析公告，存入 SQLite，返回本次新增。
- method="http"    : 直接 requests 式抓取（适用于服务端渲染、未封 IP 的站点）
- method="browser" : 用 Playwright 无头浏览器执行 JS（适用于反爬/JS 渲染站点，如国考）
- method="manda"   : 上海站群站内搜索(POST JSON 到 ss.shanghai.gov.cn/manda-app)。
                    源需含 manda_token + manda_queries；返回带 title/url/date 的 items。
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
    r"留言|举报|督查|信访|繁體|无障碍版|纠错|收藏|分享|要闻公告|最新公告|仲裁公告|公告查询|事业单位进人公告|通知公告")

# 全局噪声（非招聘公告）：与 daily_digest 保持一致，在「入库」环节即过滤，
# 防止采购/中标/询价/导航文字/泛化栏目名等 junk 进入 notices.db 与看板。
GLOBAL_NOISE = re.compile(
    r"采购|中标|询价|成交|招标|竞价|政府采购|单一来源|资格预审.*采购|磋商|比价|验收|合同公告|选聘|审计"
    r"|^/\s"                  # 导航栏文字（以 / 开头）
    r"|^事业单位公开招聘$"     # 泛化栏目名（非具体公告）
    r"|^公开招聘$"            # 泛化栏目名
    r"|公开招聘服务平台"       # 统一平台导航名（非真实公告）
)

# 日期提取正则（按优先级排序）：
#   1) URL_DATE_RE: 从 URL 路径提取（覆盖中国政务站常见格式）
#      匹配 /2026-08-14/ 、 /2026/0814/ 、 t20260814_ 、 _20260814. 等
#   2) DATE_RE: 从页面文本提取（标准分隔符 + 紧凑8位数字）
DATE_RE = [
    re.compile(r"(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})"),     # 2026-08-14 / 2026年8月14日 / 2026.08.14
    re.compile(r"(?<![\d])(\d{4})(\d{2})(\d{2})(?![\d])"),        # 紧凑 20260814（文本中连续8位数字）
]
URL_DATE_RE = re.compile(
    r"(?:[/_\-\.t]|^)"                          # 分隔符或 t 前缀
    r"(\d{4})"                                  # 年 YYYY
    r"(?:"
    r"(?:[-/._](\d{1,2})[-/._](\d{1,2}))"         # 分隔格式 YYYY-MM-DD / YYYY/MM/DD
    r"|(\d{2})(\d{2})"                           # 连续格式 YYYYMMDD
    r")"
    r"(?:[_/\.\-]|$)"                           # 后缀分隔符或结束
)


def now_iso():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _norm_title(t):
    """规范化公告标题，用于「同源标题去重」与 manda 内去重。
    规则（只去装饰/载体噪声，保留实质信息）：
      - 去【区教育局】等机构方括号前缀
      - 去 "就业招聘 | " / "招聘 | " 前缀
      - 去全半角空白与常见装饰标点（空格/全角空格/·/、/，/，/！/！/→）
    例：
      【区教育局】 2026年闵行区第二批教师招聘公告  -> 2026年闵行区第二批教师招聘公告
      就业招聘 | 2026年奉贤区区属国有企业招聘公告 -> 2026年奉贤区区属国有企业招聘公告
    """
    t = re.sub(r"^【[^】]*】", "", t or "")
    t = re.sub(r"^(就业招聘|招聘)\s*[|｜]\s*", "", t)
    t = re.sub(r"[\s\u3000·、，,。．.！!！→]", "", t)
    return t


# ---------------- 抓取 ----------------
def _decode_response(r, encoding):
    """读取 urllib 响应并解码（处理 gzip / 推断 charset）。"""
    raw = r.read()
    if r.headers.get("Content-Encoding") == "gzip":
        import gzip
        raw = gzip.decompress(raw)
    cs = encoding if encoding else None
    if not cs:
        m = re.search(r"charset=([\w-]+)", r.headers.get("Content-Type", ""))
        cs = (m.group(1).lower() if m else "utf-8")
    return raw.decode(cs, errors="ignore")


def fetch_http(url, timeout=30, retries=2, encoding=None):
    import urllib.request, ssl, socket as _socket_mod
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    # 兼容老旧 TLS 重新协商（部分 gov 站如江西人事考试网使用旧协议）
    if hasattr(ssl, "OP_LEGACY_SERVER_CONNECT"):
        try:
            ctx.options |= ssl.OP_LEGACY_SERVER_CONNECT
        except Exception:
            pass
    proxy = os.environ.get("SCRAPE_PROXY", "").strip()
    headers = {
        "User-Agent": DEFAULT_UA,
        "Accept-Encoding": "gzip",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": url,
    }
    # 强制 IPv4：GitHub Actions ubuntu 默认优先 IPv6，国内 gov.cn 仅 IPv4 可达
    # 通过临时 monkey-patch socket.getaddrinfo 实现（仅影响本函数内 urllib 调用）
    _orig_gai = _socket_mod.getaddrinfo
    def _ipv4_only(host, port, family=0, *a, **kw):
        return _orig_gai(host, port, _socket_mod.AF_INET if family == 0 else family, *a, **kw)

    last = None
    for _ in range(retries + 1):
        try:
            _socket_mod.getaddrinfo = _ipv4_only
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                _socket_mod.getaddrinfo = _orig_gai
                return _decode_response(r, encoding)
        except Exception as e:
            _socket_mod.getaddrinfo = _orig_gai
            last = e
            time.sleep(1.5)
    # 第二轮：直连全失败且配置了代理 → 回退走国内代理（同样强制 IPv4）
    if proxy:
        try:
            _socket_mod.getaddrinfo = _ipv4_only
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
            req = urllib.request.Request(url, headers=headers)
            with opener.open(req, timeout=timeout, context=ctx) as r:
                _socket_mod.getaddrinfo = _orig_gai
                return _decode_response(r, encoding)
        except Exception as e:
            _socket_mod.getaddrinfo = _orig_gai
            last = e
    raise last


def _http_post_json(url, payload, timeout=30, retries=2):
    """POST JSON(body 以 text/plain 发送，兼容 manda 搜索接口)，强制 IPv4 + 代理回退。
    返回解析后的 dict（JSON）。失败抛异常。
    """
    import urllib.request, ssl, socket as _socket_mod, json as _json
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    proxy = os.environ.get("SCRAPE_PROXY", "").strip()
    data = _json.dumps(payload).encode("utf-8")
    headers = {
        "User-Agent": DEFAULT_UA,
        "Content-Type": "text/plain",
        "Accept": "application/json",
        "Referer": "https://www.jingan.gov.cn/",
    }
    _orig_gai = _socket_mod.getaddrinfo
    def _ipv4_only(host, port, family=0, *a, **kw):
        return _orig_gai(host, port, _socket_mod.AF_INET if family == 0 else family, *a, **kw)
    last = None
    for _ in range(retries + 1):
        try:
            _socket_mod.getaddrinfo = _ipv4_only
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                _socket_mod.getaddrinfo = _orig_gai
                return _json.loads(r.read().decode("utf-8", errors="ignore"))
        except Exception as e:
            _socket_mod.getaddrinfo = _orig_gai
            last = e
            time.sleep(1.0)
    if proxy:
        try:
            _socket_mod.getaddrinfo = _ipv4_only
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            with opener.open(req, timeout=timeout, context=ctx) as r:
                _socket_mod.getaddrinfo = _orig_gai
                return _json.loads(r.read().decode("utf-8", errors="ignore"))
        except Exception as e:
            _socket_mod.getaddrinfo = _orig_gai
            last = e
    raise last


def _manda_items(source):
    """上海站群 manda 站内搜索抓取。
    源需含：
      method: "manda"
      manda_token: 站点 token（如 17q2lm8）
      manda_queries: 关键词列表（如 ["招聘", "事业单位招聘"]）
      manda_razor: 可选频道 alias（如 jamh_search / fxss_razor），缺省自动探测 ui
    返回 items 列表（title/url/date）。
    """
    token = source["manda_token"]
    base = "https://ss.shanghai.gov.cn/manda-app/api/app/search/v1"
    queries = source.get("manda_queries") or ["招聘"]
    razor = source.get("manda_razor")
    size = int(source.get("manda_size", 40))
    # 若未指定 razor，先用 ui 接口探测可用搜索频道（取第一个 *_search / *_razor 非 suggest/imgsearch）
    # ui 探测属可选优化：超时短、不重试，失败则走默认索引，绝不阻塞主体抓取。
    if not razor:
        try:
            ui = _http_post_json(f"{base}/{token}/ui", {}, timeout=8, retries=0)
            for rz in (ui.get("razors") or []):
                alias = rz.get("alias", "")
                if alias and alias not in ("suggest", "imgsearch", "service_search", "leader_search", "wsbs", "ask_razor"):
                    razor = alias
                    break
        except Exception:
            razor = None
    items, seen = [], set()
    for q in queries:
        body = {"query": q, "current": 1, "size": size, "cid": token}
        if razor:
            body["razor"] = razor
        try:
            # 超时 12s、仅 1 次重试：GHA 海外 IP 访问 ss.shanghai.gov.cn 偶发慢，
            # 快速失败跳过该 query 即可，避免 25s×3 空等把整轮 run 从 15min 拖到 30+min。
            out = _http_post_json(f"{base}/{token}/search", body, timeout=12, retries=1)
        except Exception as e:
            print(f"[manda] {source['name']} query[{q}] 请求失败: {e}")
            continue
        if not out.get("success"):
            print(f"[manda] {source['name']} query[{q}] 失败: {out.get('reason') or (out.get('_meta') or {}).get('reason','')}")
            continue
        for it in (out.get("result") or {}).get("items") or []:
            title = ((it.get("title") or {}).get("raw") or "").strip()
            u = ((it.get("url") or {}).get("raw") or "").strip()
            if not title or not u:
                continue
            date = ""
            if it.get("date") and isinstance(it.get("date"), dict):
                date = (it["date"].get("raw") or "").strip()
            if date and not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
                date = extract_date(date, u)
            if not date:
                date = extract_date(title + " " + u, u)
            key = u.split("#")[0]
            if key in seen:
                continue
            seen.add(key)
            items.append({"title": title[:200], "url": u, "date": date})
    return items


def _filter_manda_items(source, items):
    """对 manda 搜索返回的 items 应用源的 include/exclude + 标题去重 + 日期窗口。

    保留规则：
      - 必须命中 title_include（白名单：公开招聘/招聘公告/招录/选调等）
      - 剔除命中 title_exclude 的噪声（公示/名单/成绩/新闻/问答等）
      - 同标题仅留一条（manda 会把同一公告镜像到多个局子站/人社局站，URL 不同但内容同）
      - 有明确日期且超过 manda_days 天的丢弃（默认 400 天，避免灌入上古历史）
    """
    inc = re.compile(source["title_include"]) if source.get("title_include") else None
    exc = re.compile(source["title_exclude"]) if source.get("title_exclude") else None
    days = int(source.get("manda_days", 400))
    now = datetime.date.today()
    seen = set()
    out = []
    for it in items:
        t = it["title"] or ""
        nt = _norm_title(t)
        if inc and not inc.search(nt):
            continue
        if exc and exc.search(nt):
            continue
        if GLOBAL_NOISE.search(nt):
            continue
        key = nt[:40]
        if key in seen:
            continue
        seen.add(key)
        # 日期窗口：有明确日期但超过窗口丢弃；
        # date 为空的历史公告兜底——从标题年份判断（如"2021年度储备人才招录公告"），
        # 年份早于窗口下限即剔除，避免 manda 把上古历史灌进库。
        d = it.get("date") or ""
        if re.match(r"^\d{4}-\d{2}-\d{2}$", d):
            try:
                y, mo, dd = map(int, d.split("-"))
                age = (now - datetime.date(y, mo, dd)).days
                if age > days:
                    continue
            except Exception:
                pass
        elif not d:
            m_year = re.search(r"(20\d{2})(?:年|年度)", nt)
            if m_year:
                try:
                    y = int(m_year.group(1))
                    # 用该年「年末」兜底估算最晚可能发布日期：若年末距今已超窗口，
                    # 说明必是历史公告（如"2021年度储备人才"），剔除。
                    # "2026学年"不会误匹配（"学"在"年"前）；当年公告自然不超窗口。
                    age = (now - datetime.date(y, 12, 31)).days
                    if age > days:
                        continue
                except Exception:
                    pass
        out.append(it)
    return out


_browser = None


def _get_browser():
    global _browser
    if _browser is None:
        from playwright.sync_api import sync_playwright
        p = sync_playwright().start()
        # Chromium 网络参数：强制IPv4、忽略SSL、规避反爬检测
        # 仅在「未配置代理」时强制直连(--no-proxy-server)；
        # 配置了 SCRAPE_PROXY 时交给 context 级 proxy 控制（见 _new_context）
        args = [
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-ipv6",
            "--ignore-certificate-errors",
        ]
        if not os.environ.get("SCRAPE_PROXY", "").strip():
            args.append("--no-proxy-server")
        _browser = p.chromium.launch(headless=True, args=args)
    return _browser


def _new_context(browser, proxy=None):
    """创建浏览器上下文；proxy 非空时走国内代理（直连失败回退用）。"""
    if proxy:
        return browser.new_context(user_agent=DEFAULT_UA, ignore_https_errors=True,
                                   proxy={"server": proxy})
    return browser.new_context(user_agent=DEFAULT_UA, ignore_https_errors=True)


def _browse(ctx, url, timeout, wait, wait_until):
    """在上下文里打开页面、等待、抽取 HTML（含 iframe 内容）。返回 html 字符串。"""
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


def _make_route_handler(block_external=False, base_url=None):
    """生成路由拦截器。block_external=True 时屏蔽所有第三方域名请求（解决 SPA 外部脚本卡死问题）。"""
    from urllib.parse import urlparse

    allowed_host = None
    if block_external and base_url:
        try:
            allowed_host = urlparse(base_url).hostname or urlparse(base_url).netloc.split(":")[0]
        except Exception:
            pass

    def handler(route, request):
        try:
            if request.resource_type in ("image", "media", "font", "stylesheet"):
                route.abort()
                return
        except Exception:
            pass
        if allowed_host:
            try:
                req_host = (urlparse(request.url).hostname or "").split(":")[0]
                if req_host and req_host != allowed_host and req_host != "localhost" and req_host != "127.0.0.1":
                    route.abort()
                    return
            except Exception:
                pass
        try:
            route.continue_()
        except Exception:
            pass

    return handler


def _block_heavy(route, request):
    _make_route_handler()(route, request)


def fetch_browser(url, timeout=90000, wait=3000, wait_until="domcontentloaded", retries=2, block_external=False):
    proxy = os.environ.get("SCRAPE_PROXY", "").strip()
    last = None
    route_handler = _make_route_handler(block_external=block_external, base_url=url)
    for attempt in range(retries + 1):
        try:
            browser = _get_browser()
            ctx = _new_context(browser)
            try:
                ctx.route("**/*", route_handler)
            except Exception:
                pass
            return _browse(ctx, url, timeout, wait, wait_until)
        except Exception as e:
            last = e
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
    if proxy:
        try:
            browser = _get_browser()
            ctx = _new_context(browser, proxy=proxy)
            try:
                ctx.route("**/*", route_handler)
            except Exception:
                pass
            return _browse(ctx, url, timeout, wait, wait_until)
        except Exception as e:
            last = e
    raise last



def _raw_fetch(url, method, encoding=None, browser_wait=3000, browser_wait_until="domcontentloaded", browser_timeout=90000, http_timeout=15, block_external=False):
    if method == "browser":
        try:
            return fetch_browser(url, timeout=browser_timeout, wait=browser_wait, wait_until=browser_wait_until, block_external=block_external)
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
    block_external = source.get("block_external", False)
    html = _raw_fetch(source["url"], method, encoding=encoding,
                      browser_wait=browser_wait, browser_wait_until=browser_wait_until,
                      browser_timeout=browser_timeout, http_timeout=http_timeout,
                      block_external=block_external)
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
                    col = _raw_fetch(col_url, method, encoding=encoding, browser_wait=browser_wait, block_external=block_external)
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
                                   browser_wait=browser_wait, browser_wait_until=browser_wait_until,
                                   block_external=block_external)
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
    """从 URL 或文本中提取公告日期，返回 YYYY-MM-DD 或空串。

    覆盖格式：
      URL:  /2026-08-14/ | /2026/0814/ | t20260814_ | _20260814.htm
      文本: 2026-08-14 | 2026年8月14日 | 2026.08.14 | 20260814
    """
    text = re.sub(r"\s+", "", text or "")  # 归一化（如嘉兴日期 span 内 "2026- 08- 13" 带空格）

    # 优先从 URL 提取（最可靠）
    m = URL_DATE_RE.search(url)
    if m:
        y = int(m.group(1))
        mo = int(m.group(2)) if m.group(2) else int(m.group(4))
        d = int(m.group(3)) if m.group(3) else int(m.group(5))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{y}-{mo:02d}-{d:02d}"

    # 再从文本提取
    for r in DATE_RE:
        m = r.search(text or "")
        if m:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if 1 <= mo <= 12 and 1 <= d <= 31:
                return f"{y}-{mo:02d}-{d:02d}"
            continue

    # 兜底：URL 中任意位置出现的 8 位纯数字日期（宽松匹配，误判风险低）
    m = re.search(r"(?<![\d])(20[2-9]\d)(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?![\d])", url)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

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
        # 日期提取：从多层上下文搜索（SPA 源的日期常在兄弟/远祖元素里）
        parent = a.find_parent(["li", "div", "tr", "td", "p"]) or a
        ptext = parent.get_text(" ", strip=True) if parent else title
        # 如果父元素文本没提取到日期，扩大搜索：兄弟元素 + 祖先 + 近邻 date 类节点
        if not extract_date(ptext, absurl):
            candidates = []
            # 兄弟元素（前3个后3个）
            for sib in list(parent.previous_siblings)[-3:] + list(parent.next_siblings)[:3]:
                if sib.name: candidates.append(sib.get_text(" ", strip=True))
            # 祖先链（向上2层）
            gp = parent.parent
            if gp and gp.name: candidates.append(gp.get_text(" ", strip=True))
            ggp = gp.parent if gp else None
            if ggp and ggp.name: candidates.append(ggp.get_text(" ", strip=True)[:200])
            # 近邻含 date/time 类名的元素
            for el in parent.find_all(class_=re.compile(r'date|time|publish', re.I), recursive=False):
                candidates.append(el.get_text(" ", strip=True)[:200])
            # 用第一个能提取到日期的候选文本
            for cand in candidates:
                d = extract_date(cand, absurl)
                if d:
                    ptext = ptext + " " + cand  # 追加到 ptext 让 extract_date 能匹配
                    break
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
    # 载入该源现有记录的规范化标题索引：规范化标题 -> (id, url)。
    # 目的：合并「同源同标题、URL 不同」的镜像公告（manda 会同一公告索引到多个局子站 /
    # 人社局站；山西/江苏部分站同一公告也有两种 URL 路径；长宁"正式版+宣传版"等）。
    # 仅在同一 source 内合并，避免跨源误并不同渠道公告。
    title_index = {}
    for rid, rt, rurl in conn.execute(
            "SELECT id,title,url FROM notices WHERE source=?", (source["name"],)).fetchall():
        if rt:
            title_index.setdefault(_norm_title(rt), []).append([rid, rurl])
    for it in items:
        # 入库即过滤全局噪声，避免 junk 污染 notices.db 与看板
        if GLOBAL_NOISE.search(it["title"] or ""):
            dropped += 1
            continue
        uid = url_id(it["url"])
        cur = conn.execute("SELECT id FROM notices WHERE id=?", (uid,)).fetchone()
        if cur:
            # 同 URL 已存在 -> 仅刷新（保留原 first_seen）
            conn.execute("UPDATE notices SET last_seen=?, title=?, date=? WHERE id=?",
                         (ts, it["title"], it["date"], uid))
            continue
        # 同源已有「规范化标题相同」的记录（URL 不同）-> 视为镜像，合并到已有那条
        nt = _norm_title(it["title"]) if it.get("title") else ""
        if nt and title_index.get(nt):
            # 合并到最早记录的 id，用最新抓到的 url/date/title 刷新，避免镜像堆积
            old_id = title_index[nt][0][0]
            conn.execute("UPDATE notices SET last_seen=?, url=?, title=?, date=? WHERE id=?",
                         (ts, it["url"], it["title"], it["date"], old_id))
            title_index[nt][0][1] = it["url"]   # 记录已刷新的最新 url
            continue
        # 真正新增
        conn.execute("""INSERT INTO notices(id,source,category,region,title,url,date,first_seen,last_seen)
                        VALUES(?,?,?,?,?,?,?,?,?)""",
                     (uid, source["name"], source.get("category", ""), source.get("region", ""),
                      it["title"], it["url"], it["date"], ts, ts))
        if nt:
            title_index.setdefault(nt, []).append([uid, it["url"]])
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
    # 上海源反向白名单：政府门户公告/通知栏目混杂大量市政/行政/采购文，
    # 仅保留含招聘专属词的条目，其余（磋商/验收/决算/任免/规划/闭馆等）一律清理。
    RECRUIT_KEEP = re.compile(r"招聘|招考|招录|引进|遴选|选调|竞聘|交流|招聘会|人员招录")
    for (rid, title) in conn.execute("SELECT id,title FROM notices WHERE region='上海'").fetchall():
        if title and not RECRUIT_KEEP.search(title):
            conn.execute("DELETE FROM notices WHERE id=?", (rid,))
            n += 1
    # 全局占位/栏目名噪声（导航栏目被当公告标题，如"要闻公告""通知公告"）：精确删除
    PLACEHOLDER = ["要闻公告", "最新公告", "仲裁公告", "公告查询",
                   "事业单位进人公告", "通知公告", "公告", "首页", "栏目"]
    for p in PLACEHOLDER:
        for (rid,) in conn.execute("SELECT id FROM notices WHERE title = ?", (p,)).fetchall():
            conn.execute("DELETE FROM notices WHERE id=?", (rid,))
            n += 1
    # 统一平台入口噪声：导航菜单链接的"福建省事业单位公开招聘服务平台"（非真实公告）
    for (rid,) in conn.execute("SELECT id FROM notices WHERE title LIKE ?", ("%公开招聘服务平台%",)).fetchall():
        conn.execute("DELETE FROM notices WHERE id=?", (rid,))
        n += 1
    # 省考/公务员导航噪声：专题页、index页、系统首页、泛化栏目名（标题短或含"专题/系统/管理/报名管理"且无具体招聘信息）
    nav_patterns = [
        "%专题%", "%考试录用公务员专题%", "%招录考试%",
        "%网上报名管理系统%", "%报名管理系统%",
    ]
    for pat in nav_patterns:
        for (rid,) in conn.execute("SELECT id FROM notices WHERE title LIKE ?", (pat,)).fetchall():
            conn.execute("DELETE FROM notices WHERE id=?", (rid,))
            n += 1
    # URL 含 /col/.../index.html 或以 index.html 结尾的导航页（非具体公告详情）
    for (rid,) in conn.execute(
        "SELECT id FROM notices WHERE url LIKE '%/col/%/index.html' OR url LIKE '%/index.html'"
    ).fetchall():
        conn.execute("DELETE FROM notices WHERE id=?", (rid,))
        n += 1

    conn.commit()
    conn.close()
    if n:
        print(f"[清理] 删除 {n} 条历史噪声记录")
    return n


def date_backfill():
    """对库内 date 为空的记录，用增强后的 extract_date 重新提取日期并 UPDATE。

    幂等：只更新能成功提取的；已回填的再次运行不会重复处理。
    返回更新的条数（供 CI 判断是否需推送，避免数据质量改动被锁死）。
    """
    conn = init_db()
    rows = conn.execute(
        "SELECT id, title, url, date FROM notices WHERE date IS NULL OR date=''"
    ).fetchall()
    updated = 0
    for rid, title, url, _ in rows:
        d = extract_date(title or "", url or "")
        if d:
            conn.execute("UPDATE notices SET date=? WHERE id=?", (d, rid))
            updated += 1
    conn.commit()
    conn.close()
    if updated:
        print(f"[回填] 修正 {updated} 条历史公告的 date 字段")
    return updated


# ---------------- 主流程 ----------------
def _work(s, limit_per_source=40):
    """单源抓取+解析（线程安全：不碰 DB）。返回 (source, items, error)。"""
    try:
        if s.get("method") == "manda":
            # 站内搜索 API 源：直接返回 items（title/url/date），不走 HTML 解析
            # 注意：先过滤再去重截断（manda 相关性排序会把近期公告排到靠后，
            # 若先截断再过滤会丢失排位靠后的近期真公告）
            s2 = dict(s)
            items = _filter_manda_items(s2, _manda_items(s2))
            return s2, items[:limit_per_source], None
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
    # 显式 enabled:false 的源一律跳过（不再被误抓）。历史遗留/被替代源用此字段关闭。
    sources = [s for s in sources if s.get("enabled", True) is not False]
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
