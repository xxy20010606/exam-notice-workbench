#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
公告聚合爬虫：读取 sources.json，按源抓取并解析公告，存入 SQLite，返回本次新增。
- method="http"    : 直接 requests 式抓取（适用于服务端渲染、未封 IP 的站点）
- method="browser" : 用 Playwright 无头浏览器执行 JS（适用于反爬/JS 渲染站点，如国考）
- method="manda"   : 上海站群站内搜索(POST JSON 到 ss.shanghai.gov.cn/manda-app)。
                    源需含 manda_token + manda_queries；返回带 title/url/date 的 items。
- method="api"     : 福建统一平台 JSON 数据源(GET newsList, flag=2&isProject=1)，
                    源需含 api_url(可用 {pageNum} 占位)；返回 title/url/date 的 items。
- method="jsonapi" : 通用后台 JSON 接口(POST 表单 + 可选签名头)，适用于列表页是
                    SPA 壳、数据靠 XHR 拉取的站（如宁波人才网 网上招考 10500+ 条）。
"""
import json, os, sqlite3, hashlib, re, time, datetime, threading
from urllib.parse import urljoin, quote
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
    r"|拟录用|拟聘用"          # 录用结果公示（非招聘公告）
    r"|招聘会"                 # 招聘会预告/排期（非招聘公告）
    r"|^/\s"                  # 导航栏文字（以 / 开头）
    r"|^事业单位公开招聘$"     # 泛化栏目名（非具体公告）
    r"|^事业单位招聘$"         # 泛化栏目名（连云港 sydwzp.html 列表页自身被当成公告）
    r"|^公开招聘$"            # 泛化栏目名
    r"|^招聘公告$"            # 泛化栏目名（上海 rsj 多页合并后每页都有一个「招聘公告」栏目名）
    r"|^公告公示$"            # 泛化栏目名
    r"|^通知公告$"            # 泛化栏目名
    r"|公开招聘服务平台"       # 统一平台导航名（非真实公告）
)

# 招聘关键词白名单：标题必须命中其一才入库（store() 第二道闸）。
# 拦住政务门户/人社局首页混入的行政通知、送达催告、目录发布、政策文件等
# （如「关于向李佳送达《依法支付工伤保险待遇催告通知书》的公告」）。
# 与 daily_digest.RECRUIT_KEYWORDS 同源，另加「招募」（三支一扶等）。
RECRUIT_WHITELIST = re.compile(
    r"公务员|省考|选调|遴选|事业|招聘|招考|招录|公考|引进|招募"
)


def whitelist_exempt():
    """需要在白名单上放行的源名集合（sources.json 里配 "skip_whitelist": true）。

    场景：部分站点服务端就把列表标题截断到十几个字（典型淮安
    「2026年淮安市第一人民医院公开…」），关键词「招聘/公告」正好被截掉，
    白名单会把这些真公告全判成 junk 删掉。这类源在 sources.json 显式声明豁免。
    """
    try:
        with open(SOURCES, encoding="utf-8") as f:
            return {s.get("name") for s in json.load(f) if s.get("skip_whitelist")}
    except Exception:
        return set()

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


def _http_get_json(url, referer=None, timeout=20, retries=2):
    """GET 返回 JSON(dict)。强制 IPv4 + 代理回退，兼容福建统一平台等 JSON 接口。
    返回解析后的 dict；失败抛异常。
    """
    import urllib.request, ssl, socket as _socket_mod, json as _json
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    proxy = os.environ.get("SCRAPE_PROXY", "").strip()
    headers = {
        "User-Agent": DEFAULT_UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    if referer:
        headers["Referer"] = referer
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
            req = urllib.request.Request(url, headers=headers)
            with opener.open(req, timeout=timeout, context=ctx) as r:
                _socket_mod.getaddrinfo = _orig_gai
                return _json.loads(r.read().decode("utf-8", errors="ignore"))
        except Exception as e:
            _socket_mod.getaddrinfo = _orig_gai
            last = e
    raise last


def _fujian_api_items(source):
    """福建省事业单位公开招聘统一平台（http://220.160.53.33:8903）JSON 数据源。

    平台首页 /home 是 SPA 壳（无公告链接），真正的公告列表由前端 XHR 拉取：
      GET /ksbm/student/home/newsList?userId=&year=&orderBy=1&pageNum=N&pageSize=100
            &flag=2&isProject=1
    flag=2&isProject=1 返回「公开招聘公告」feed（total≈1500，全部近期，含 clean
    title + 真实公告 URL + noticeTime 日期），覆盖全省 9 市 + 平潭，无需登录、
    无 GHA 海外 IP 对各地市 gov 子站的封锁问题（数据来自平台自身服务器）。

    源配置:
      method: "api"
      api_url:  newsList 请求 URL（内部以 {pageNum} 占位页码）
      api_pages: 抓取页数（默认 15，pageSize 固定 100）
      api_days:  只收近 N 天公告（默认 400）
    返回 items(title/url/date)。
    """
    base = source.get("api_url") or source.get("url")
    pages = int(source.get("api_pages", 15))
    size = 100
    referer = source.get("api_referer") or "http://220.160.53.33:8903/home"
    items, seen = [], set()
    total_ok = False
    for p in range(1, pages + 1):
        url = base.format(pageNum=p) if "{pageNum}" in base else base + \
              (("&" if "?" in base else "?") + f"pageNum={p}&pageSize={size}")
        try:
            out = _http_get_json(url, referer=referer, timeout=20, retries=1)
        except Exception as e:
            print(f"[api] {source['name']} 第{p}页请求失败: {e}")
            break
        data = out.get("data") or {}
        rows = data.get("rows") or []
        if not rows:
            break
        total_ok = True
        for r in rows:
            title = (r.get("title") or r.get("proTitle") or "").strip()
            u = (r.get("url") or "").strip()
            if not title or not u or not u.startswith("http"):
                continue
            # noticeTime 形如 2026-09-04T08:30:09.000+08:00
            date = ""
            nt = (r.get("noticeTime") or "")
            m = re.match(r"(\d{4})-(\d{2})-(\d{2})", nt)
            if m:
                date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
            key = u.split("#")[0]
            if key in seen:
                continue
            seen.add(key)
            items.append({"title": title[:200], "url": u, "date": date})
        if len(rows) < size:
            break
    if not total_ok:
        raise RuntimeError("统一平台 newsList 未返回有效 rows")
    return items


def _http_post_form(url, data, headers=None, timeout=25, retries=1):
    """POST application/x-www-form-urlencoded，返回解析后的 JSON dict。

    与 _http_post_json 的区别：body 是表单编码而非 JSON（不少政府/人才网
    后台接口只认 form），且允许调用方注入自定义头（部分站点带签名头才返回数据）。
    """
    import urllib.parse, urllib.request, ssl, socket as _socket_mod, json as _json
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    body = urllib.parse.urlencode(data).encode("utf-8")
    hdr = {
        "User-Agent": DEFAULT_UA,
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
    }
    if headers:
        hdr.update({k: v for k, v in headers.items() if v is not None})
    proxy = os.environ.get("SCRAPE_PROXY", "").strip()
    _orig_gai = _socket_mod.getaddrinfo
    def _ipv4_only(host, port, family=0, *a, **kw):
        return _orig_gai(host, port, _socket_mod.AF_INET if family == 0 else family, *a, **kw)
    last = None
    for _ in range(retries + 1):
        try:
            _socket_mod.getaddrinfo = _ipv4_only
            req = urllib.request.Request(url, data=body, headers=hdr, method="POST")
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
            req = urllib.request.Request(url, data=body, headers=hdr, method="POST")
            with opener.open(req, timeout=timeout, context=ctx) as r:
                _socket_mod.getaddrinfo = _orig_gai
                return _json.loads(r.read().decode("utf-8", errors="ignore"))
        except Exception:
            _socket_mod.getaddrinfo = _orig_gai
    raise last


def _nbrc_sign(key):
    """宁波人才网(nbrc.com.cn) XHR 签名头。

    站点前端 function.js 里 setData/request 统一注入：
        nbrcbefore = Date.now()            # 毫秒时间戳
        nbrcafter  = md5(key + md5(str(nbrcbefore)))
        keyid      = key
    缺这几个头接口返回 200 但 data.list 为空（静默失败，极易误判为「无数据」）。
    """
    d = str(int(time.time() * 1000))
    inner = hashlib.md5(d.encode()).hexdigest()
    after = hashlib.md5((key + inner).encode()).hexdigest()
    return {"jsonType": "jsonType", "nbrcbefore": d, "nbrcafter": after,
            "keyid": key, "pwdreset": "0", "nbrctoken": ""}


def _dig(obj, path):
    """按点号路径取嵌套字段，如 "data.list"。缺失返回 None。"""
    cur = obj
    for seg in (path or "").split("."):
        if not seg:
            continue
        if isinstance(cur, dict):
            cur = cur.get(seg)
        elif isinstance(cur, list) and seg.isdigit():
            cur = cur[int(seg)] if int(seg) < len(cur) else None
        else:
            return None
    return cur


def _jsonapi_items(source):
    """通用后台 JSON 接口抓取（分页 POST 表单 + 自定义签名头）。

    典型场景：列表页是 SPA 壳，数据由前端 XHR 拉取，接口需要站点自定义签名头
    （如宁波人才网 nbrc）。配置项：
      method:            "jsonapi"
      api_url:           接口地址
      api_body:          表单字段 dict，{page} / {size} 占位
      api_pages:         抓取页数（默认 10）
      api_size:          每页条数（默认 100）
      api_signer:        签名方案名，目前支持 "nbrc"（需 api_sign_key）
      api_sign_key:      签名密钥
      api_headers:       额外固定请求头 dict（可选）
      api_list_path:     列表所在点号路径，如 "data.list"
      api_title_key:     标题字段名
      api_date_key:      日期字段名（可含时间，取前 10 位）
      api_url_key:       直接给 URL 的字段名（与 api_url_template 二选一）
      api_url_template:  URL 模板，可用列表项任意字段 {field}
      api_max_id_key:    可选，用该字段做去重键（默认用解析出的 URL）
    返回 items(title/url/date)。
    """
    url = source["api_url"]
    pages = int(source.get("api_pages", 10))
    size = int(source.get("api_size", 100))
    body_tpl = source.get("api_body") or {"pageSize": "{size}", "pageNumber": "{page}"}
    list_path = source.get("api_list_path", "data.list")
    tkey = source.get("api_title_key", "title")
    dkey = source.get("api_date_key", "date")
    ukey = source.get("api_url_key")
    utpl = source.get("api_url_template")
    extra_hdr = source.get("api_headers") or {}

    items, seen = [], set()
    got_any = False
    for p in range(1, pages + 1):
        hdr = dict(extra_hdr)
        signer = source.get("api_signer")
        if signer == "nbrc":
            hdr.update(_nbrc_sign(source.get("api_sign_key", "")))
        body = {}
        for k, v in body_tpl.items():
            if isinstance(v, str):
                v = v.replace("{page}", str(p)).replace("{size}", str(size))
            body[k] = v
        try:
            out = _http_post_form(url, body, headers=hdr, timeout=25, retries=1)
        except Exception as e:
            print(f"[jsonapi] {source['name']} 第{p}页请求失败: {e}")
            break
        rows = _dig(out, list_path) or []
        if not rows:
            break
        got_any = True
        for r in rows:
            title = (r.get(tkey) or "").strip()
            if not title:
                continue
            if utpl:
                try:
                    u = utpl.format(**r)
                except Exception:
                    u = ""
            else:
                u = (r.get(ukey or "url") or "").strip()
            if not u or not u.startswith("http"):
                continue
            raw_date = str(r.get(dkey) or "")
            m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", raw_date)
            date = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else ""
            key = u.split("#")[0]
            if key in seen:
                continue
            seen.add(key)
            items.append({"title": title[:200], "url": u, "date": date})
        if len(rows) < size:
            break
    if not got_any:
        raise RuntimeError("JSON 接口未返回有效列表（检查签名头/接口路径）")
    return items


def _gwy_column_items(source):
    """上海市公务员局（shacs.gov.cn）专栏接口抓取。

    为什么不能用 HTML 解析：该站是纯 SPA，导航和列表全是 JS 路由，渲染后所有
    <a> 的 href 都是空字符串（实测首页 33 个 <a>，href 全为 null），所以
    parse_source 永远拿到 0 条候选 —— 这是「raw=0 型静默失败」的典型。

    真实数据走两个 JSON 接口（固定两步，不能合并）：
      1) gwy-column-section.json?listChild=true
         → result.child[]，每个是「上海市20XX年度…专题」，含 id / name / sort
      2) child-section-and-news.json?sectionId={id}
         → result.secNews[].list[]，每个是公告 {id,title,postDate,sectionId}
    详情页：https://shacs.gov.cn/recruits/{sectionId}/article/{newsId}

    专题按 sort 倒序取最近的几个（sort 是「20260908」这种日期数字），这样每年
    新专题上线后自动纳入，不需要改配置。辅警/文员/学员等非公务员考试专题排除。
    """
    base = source.get("gwy_base") or "https://shacs.gov.cn"
    referer = base + "/recruits"
    sec_api = source.get("gwy_section_api") or (base + "/gwyj/api/gwy-column-section.json?listChild=true")
    news_api = source.get("gwy_news_api") or (
        base + "/gwyj/api/child-section-and-news.json?sectionId={section}&pageSize=2000")
    s_inc = re.compile(source["gwy_section_include"]) if source.get("gwy_section_include") else None
    s_exc = re.compile(source["gwy_section_exclude"]) if source.get("gwy_section_exclude") else None
    max_sec = int(source.get("gwy_max_sections", 4))

    node = _http_get_json(sec_api, referer=referer, timeout=25)
    child = _dig(node, "result.child") or []
    if not child:
        raise RuntimeError("专栏接口未返回专题列表（result.child 为空）")

    picked = []
    for s in child:
        name = (s.get("name") or "").strip()
        if not name:
            continue
        if s_inc and not s_inc.search(name):
            continue
        if s_exc and s_exc.search(name):
            continue
        picked.append(s)
    picked.sort(key=lambda x: -(x.get("sort") or 0))
    picked = picked[:max_sec]
    if not picked:
        raise RuntimeError("专题列表里没有命中 gwy_section_include 的专题")

    items, seen = [], set()
    for s in picked:
        sid = s.get("id")
        try:
            nd = _http_get_json(news_api.replace("{section}", str(sid)), referer=referer, timeout=25)
        except Exception as e:
            print(f"[gwycolumn] 专题 {s.get('name')} 公告接口失败: {str(e)[:60]}")
            continue
        groups = _dig(nd, "result.secNews") or []
        for g in groups:
            for it in (g.get("list") or []):
                title = (it.get("title") or "").strip()
                if not title:
                    continue
                nid = it.get("id")
                if nid is None:
                    continue
                # 详情路由用「专题 id」(sid)，不是公告自己的 sectionId——后者是
                # 「公告」这种子栏目（如 435），拿去拼路由会 404。真实链接形如
                # /recruits/434/article/1351?n=公告
                u = f"{base}/recruits/{sid}/article/{nid}"
                sec_name = (g.get("sectionInfo") or {}).get("name") or ""
                if sec_name:
                    u += "?n=" + quote(sec_name)
                if u in seen:
                    continue
                seen.add(u)
                items.append({"title": title[:200], "url": u,
                              "date": extract_date(str(it.get("postDate") or ""), u)})
    return items


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


# ---------------- 江苏站群 search4（jsptsearch） ----------------
def _jsptsearch_items(source):
    """江苏站群 search4 站内搜索（GET SSR）。用于人社局子站招聘公告检索。
    源需含:
      method: "jsptsearch"
      jspt_url:  search4 URL 模板(含 {searchWord} 占位, 或直接含 searchWord=xxx)
      jspt_keywords: 关键词列表(如 ["招聘"])
    返回 items(title/url/date)。
    search4 结果页 SSR 渲染, 每条主公告为 <a class="..titleFont permitT.." href=.. title=标题>,
    日期在同结果块内 <b>YYYY-MM-DD</b>。解析后入库。
    """
    base = source.get("jspt_url") or source.get("url")
    keywords = source.get("jspt_keywords") or ["招聘"]
    items, seen = [], set()
    for kw in keywords:
        url = base.format(searchWord=quote(kw)) if "{searchWord}" in base else base
        try:
            html = fetch_http(url, timeout=25)
        except Exception as e:
            print(f"[jsptsearch] {source['name']} 关键词[{kw}] 请求失败: {e}")
            continue
        pat_a = re.compile(
            r'<a[^>]+href="(https?://[^"]+)"[^>]*class="[^"]*titleFont[^"]*permitT[^"]*"[^>]*title="([^"]+)"', re.S)
        matches = list(pat_a.finditer(html))
        for k, m in enumerate(matches):
            u, t = m.group(1), m.group(2).strip()
            if not u or not t:
                continue
            key = u.split("#")[0]
            if key in seen:
                continue
            seen.add(key)
            # 该结果块 = 到下一条标题 a 前，块内取日期 <b>YYYY-MM-DD</b>
            blk_end = matches[k+1].start() if k+1 < len(matches) else len(html)
            block = html[m.end():blk_end]
            dm = re.search(r'<b>\s*(20\d{2}-\d{1,2}-\d{1,2})\s*</b>', block)
            if not dm:
                dm = re.search(r'(20\d{2}-\d{1,2}-\d{1,2})', block)
            date = dm.group(1) if dm else ""
            items.append({"title": t[:200], "url": u, "date": date})
    return items


def _filter_jsptsearch(source, items):
    """对 search4 结果应用源的 include/exclude + 全局噪声 + 标题去重。
    江苏人社局搜索结果常混入 拟录用公示/名单公示/政府新闻, 用 exclude 剔除。
    """
    inc = re.compile(source["title_include"]) if source.get("title_include") else None
    exc = re.compile(source["title_exclude"]) if source.get("title_exclude") else None
    seen = set()
    out = []
    for it in items:
        t = it.get("title") or ""
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
        out.append(it)
    return out


_browser = None
_browser_tid = None


def _get_browser():
    """取（懒加载的）共享浏览器；**按线程隔离**。

    为什么必须按线程：Playwright 的 sync API 对象绑定在创建它的线程上，跨线程调用
    会直接抛 `cannot switch to a different thread (which happens to have exited)`。
    而 run_all 的执行顺序是「http 源先进线程池并发 → browser 源再在主线程串行」，
    只要有任意一个 method=http 的源在它的 extra_urls 里带了 {"method":"browser"}
    的子栏目（典型：江苏-省考 col92919 职位表），线程池里的 worker 就会先把
    _browser 创建出来。worker 退出后主线程拿到这个已死线程的 browser 对象，
    后面**所有** browser 源集体报错、count 全 0——症状极具迷惑性：像站点全挂了，
    实际是浏览器单例绑错了线程。
    """
    global _browser, _browser_tid
    tid = threading.get_ident()
    if _browser is None or _browser_tid != tid:
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
        _browser_tid = tid
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


NEXT_PAGE_TEXTS = ("下一页", "下页", "后一页", "Next", "next", "»", "›", ">")


def fetch_browser_pages(url, pages=5, timeout=90000, wait=3000, wait_until="domcontentloaded",
                        next_selector=None, page_wait=None, block_external=False):
    """在同一个浏览器上下文里逐页翻页，返回第 2~N 页合并后的 HTML（不含第 1 页）。

    解决「分页控件拿不到可请求 URL」的站：href 为空、href="javascript:;"、或压根
    没有 href 只有 JS 事件监听。这类站 page_url_template（需要 URL 规律）和
    follow_pages（需要静态 <a href>）都无能为力。
    典型：徐州 hrss.xz.gov.cn（页码 href 为空，第 2~8 页藏着 50+ 条真招聘公告，
    第 1 页却全是拟聘公示）、泰州 rsj.taizhou.gov.cn（href="javascript:;"，
    列表由 CMS 接口重建）。

    做法：加载第 1 页 → 定位「下一页」或「当前页+1」控件 → 点击 → 等渲染 →
    抓 page.content()；每轮用「前 30 个链接文本」做指纹，指纹没变即认定到底或
    点击无效，立即停，避免空转到上限。返回内容不含第 1 页，因为调用方
    （fetch）已经抓过第 1 页了。
    """
    def _fingerprint(p):
        texts = []
        try:
            for a in p.query_selector_all("a"):
                t = (a.inner_text() or "").strip()
                if t:
                    texts.append(t[:40])
                if len(texts) >= 30:
                    break
        except Exception:
            pass
        return tuple(texts)

    def _find_next(p, cur):
        if next_selector:
            try:
                el = p.query_selector(next_selector)
                if el:
                    return el
            except Exception:
                pass
        # 文本控件优先（下一页/下页/>），这类控件比页码稳，不受当前页码影响。
        # 关键：必须点最内层可点击元素。政务站的分页常写成 <li><a>&gt;</a></li>，
        # 若匹配到外层 li 再 click，DOM 事件不会冒泡到 a 的监听器，点击等于没点。
        for txt in NEXT_PAGE_TEXTS:
            el = None
            try:
                for e in p.query_selector_all("a,button"):
                    if (e.inner_text() or "").strip() == txt:
                        el = e
                        break
            except Exception:
                pass
            if el is None:
                try:
                    for e in p.query_selector_all("li,span,i,div"):
                        if (e.inner_text() or "").strip() == txt:
                            inner = None
                            try:
                                inner = e.query_selector("a") or e.query_selector("button")
                            except Exception:
                                pass
                            el = inner or e
                            break
                except Exception:
                    pass
            if el is not None:
                return el
        # 退化为「当前页 +1」的页码
        try:
            for el in p.query_selector_all("a"):
                if (el.inner_text() or "").strip() == str(cur + 1):
                    return el
        except Exception:
            pass
        return None

    out = []
    got = 0
    browser = _get_browser()
    ctx = _new_context(browser)
    page = ctx.new_page()
    try:
        try:
            ctx.route("**/*", _make_route_handler(block_external=block_external, base_url=url))
        except Exception:
            pass
        page.goto(url, timeout=timeout, wait_until=wait_until)
        page.wait_for_timeout(wait)
        cur_url = page.url
        prev = _fingerprint(page)
        dwell = page_wait or wait
        for i in range(1, max(1, int(pages))):
            el = _find_next(page, i)
            if not el:
                break
            try:
                el.click()
            except Exception:
                break
            page.wait_for_timeout(dwell)
            fp = _fingerprint(page)
            if fp and fp == prev:
                break          # 内容没变：到底了，或点击没触发加载
            prev = fp
            got += 1
            out.append(page.content())
            if page.url != cur_url:
                # 点击触发整页跳转，新页面的列表可能仍在 JS 渲染，再等一轮
                cur_url = page.url
                page.wait_for_timeout(dwell)
        if got:
            print(f"[翻页] {url[:60]} 点击翻页 {got} 页")
    finally:
        try:
            page.close()
        except Exception:
            pass
        try:
            ctx.close()
        except Exception:
            pass
    return "\n".join(out)


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
    # 分页模板（page_url_template + max_pages）：分页由 JS 动态生成、首页无静态链接，
    # 且页数很多的站。典型：上海 rsj.sh.gov.cn 招聘公告 550 页，翻页回调是
    # location.href='index_'+i+'.html'，页面上拿不到 <a href="index_2.html">。
    # 从第 2 页起按模板抓，任意一页请求失败即停（页数耗尽或越界自然终止），
    # 避免为不存在的页空耗，也避免 follow_pages 那种「找不到静态链接就永远抓第 1 页」。
    tpl = source.get("page_url_template")
    if tpl:
        max_pages = int(source.get("max_pages", 10))
        got_pages = 1
        for p in range(2, max_pages + 1):
            purl = tpl.replace("{page}", str(p))
            try:
                ph = _raw_fetch(purl, method, encoding=encoding,
                                browser_wait=browser_wait,
                                browser_wait_until=browser_wait_until,
                                browser_timeout=browser_timeout,
                                http_timeout=http_timeout,
                                block_external=block_external)
            except Exception:
                break
            html = html + "\n" + ph
            got_pages += 1
        if got_pages > 1:
            print(f"[分页] {source.get('name','')} 按模板抓取 {got_pages} 页")
    # 点击翻页（browser_click_pages）：分页控件 href 为空 / href="javascript:;" / 纯 JS
    # 事件监听，页面上拿不到任何可请求的分页 URL。上面两套机制（follow_pages 靠静态
    # <a href>、page_url_template 靠 URL 规律）都覆盖不到。典型：徐州（页码 href 为空的
    # JS 跳转，真公告全在第 2~8 页）、泰州（href="javascript:;"，列表由 CMS 接口重建）。
    # 会在同一浏览器上下文里连点 N 页，返回第 2~N 页的内容（第 1 页上面已抓）。
    bcp = source.get("browser_click_pages")
    if bcp:
        try:
            more = fetch_browser_pages(
                source["url"], pages=int(bcp), timeout=browser_timeout, wait=browser_wait,
                wait_until=browser_wait_until,
                next_selector=source.get("next_page_selector"),
                page_wait=source.get("page_wait"),
                block_external=block_external,
            )
            if more:
                html = html + "\n" + more
        except Exception as e:
            print(f"[翻页] {source.get('name','')} 点击翻页失败: {str(e)[:80]}")
    return html


# ---------------- 解析 ----------------
def _same_page(a, b):
    """判断两个 URL 是否指向同一页面（忽略协议、case、末尾斜杠、锚点）。"""
    def norm(u):
        u = (u or "").strip().split("#")[0]
        u = re.sub(r"^https?://", "", u, flags=re.I).rstrip("/")
        return u.lower()
    a, b = norm(a), norm(b)
    return bool(a) and a == b


def extract_date(text, url, hint=""):
    """从 URL 或文本中提取公告日期，返回 YYYY-MM-DD 或空串。

    覆盖格式：
      URL:  /2026-08-14/ | /2026/0814/ | t20260814_ | _20260814.htm
      文本: 2026-08-14 | 2026年8月14日 | 2026.08.14 | 20260814

    hint：标题文本，仅在「MM/DD 无年份」兜底路径下用于校正年份。
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

    # 两位年份 YY.MM.DD（如成都人事考试网 "26.09.04"）
    m = re.search(r"(?<!\d)(\d{2})\.(\d{1,2})\.(\d{1,2})(?!\d)", text or "")
    if m:
        y, mo, d = 2000 + int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 2020 <= y <= 2035 and 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{y}-{mo:02d}-{d:02d}"

    # 兜底：URL 中任意位置出现的 8 位纯数字日期（宽松匹配，误判风险低）
    m = re.search(r"(?<![\d])(20[2-9]\d)(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?![\d])", url)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    # 最后兜底：MM/DD 或 MM-DD 无年份（政府站常见，如淮安列表 <div class="time">08/18</div>）。
    # 年份需推断：默认取今年；若推算出的日期比今天晚 30 天以上，说明是去年的条目
    # （跨年时列表顶部仍显示 12/25），年份减 1。30 天余量覆盖「未来几天才发布」的正常情况。
    # 仅在短文本上启用：日期字段本身很短，长文本（整块导航/列表拼接）里出现 "1/2"
    # 这类数字片段的概率高，容易被误读成 1 月 2 日。
    # 年份校正：标题常自带年份（「2025年淮安市…公开招聘」），比 MM/DD 推断更准。
    # 只在标题年份早于推断年份（差距 ≤2 年）时覆盖；反方向不覆盖——12 月发布的
    # 「2026年招聘」公告，标题年份本就晚于发布年，拿来改会把日期推到未来。
    if text and len(text) <= 120:
        m = re.search(r"(?<!\d)(\d{1,2})[/](\d{1,2})(?!\d)", text)
        if m:
            mo, d = int(m.group(1)), int(m.group(2))
            if 1 <= mo <= 12 and 1 <= d <= 31:
                today = datetime.date.today()
                year = today.year
                hm = re.search(r"(20\d{2})\s*年", hint or "")
                if hm:
                    hy = int(hm.group(1))
                    if 0 < year - hy <= 2:
                        year = hy
                try:
                    cand = datetime.date(year, mo, d)
                except ValueError:
                    return ""
                if year == today.year and (cand - today).days > 30:
                    try:
                        cand = datetime.date(year - 1, mo, d)
                    except ValueError:
                        return ""
                return cand.strftime("%Y-%m-%d")

    return ""


def parse_source(source, html, stats=None):
    """解析列表页，返回命中 include/exclude 的条目。

    stats：可选 dict，会被填入原始候选数（未经 include/exclude 过滤）。
    用途：健康检查要区分「解析器失效」（列表页一条候选都读不到）和
    「内容空窗」（读到了 N 条，但当前栏目全是拟聘公示/采购公告，被过滤成 0）。
    只看过滤后的条数分不开这两种，会把内容空窗误报成解析器坏掉。
    """
    if stats is not None:
        stats.setdefault("raw", 0)
    # CDATA 剥离：部分政务站（典型江苏省人社厅 col92917/col92919）把列表项用
    # <record><![CDATA[ <li><a href=...> ... ]]></record> 包起来，html.parser 会
    # 把 CDATA 整段当文本节点，里面的 <a> 一个都解析不出来，表现为「页面肉眼看得见
    # 职位表、解析器只读到导航文字」。CDATA 在 HTML 里本就不合法，直接剥掉壳子
    # 保留内部标签，对正常站点无副作用。
    if "<![CDATA[" in html:
        html = html.replace("<![CDATA[", "").replace("]]>", "")
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
            if stats is not None:
                stats["raw"] += 1
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
        # SPA 自定义组件属性兜底：Vue 组件（如山东 <basic-line title=... time=...>）渲染文本
        # 会被拼成「日+年月+标题+描述」黏串，且祖先文本混入页面当前日期，导致标题污染、
        # 日期错成抓取当日。向上找自定义元素（标签名含 -），优先用其 title / time 属性。
        comp_title, comp_time = "", ""
        anc = a
        for _ in range(5):
            anc = anc.parent
            if anc is None or not getattr(anc, "name", None):
                break
            if "-" in anc.name:
                comp_title = (anc.get("title") or "").strip()
                comp_time = (anc.get("time") or "").strip()
                break
        if comp_title:
            title = comp_title
        # 黏连日期前缀剥离：SPA 渲染文本可能以「日+年月」开头（如 11202605 2026年度...），
        # 剥离前缀并恢复真实日期（11+202605 → 2026-05-11）
        glued_date = ""
        gm = re.match(r"^(\d{1,2})(20\d{2})(0[1-9]|1[0-2])(?=20\d{2})", title)
        if gm and len(title) > 12:
            _dd, _yy, _mo = int(gm.group(1)), int(gm.group(2)), int(gm.group(3))
            if 1 <= _dd <= 31:
                glued_date = f"{_yy}-{_mo:02d}-{_dd:02d}"
                title = title[gm.end():]
        if not title:
            continue
        # 列表项目符号剥离：部分站标题前带圆点（如淮安「· 2025年…公开招聘」）
        title = re.sub(r"^[·•∙‧⋅●○›»]+\s*", "", title).strip()
        # 尾部日期剥离：列表页常把日期放在 <i> 里（江苏人社厅
        # 「…省级机关职位表</span><i>2025-10-31</i>」），get_text 会把它拼进标题。
        # 日期已单独存入 date 字段，标题里重复既难看又影响标题去重。
        # 只剥结尾精确的 YYYY-MM-DD 且剥完不能为空，避免误伤标题正文里的日期。
        _t2 = re.sub(r"\s*\d{4}-\d{2}-\d{2}\s*$", "", title).strip()
        if _t2:
            title = _t2
        if not title:
            continue
        if stats is not None:
            stats["raw"] += 1
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
            onclick = a.get("onclick") or (href if href.startswith("javascript:") else "")
            m = o_re.search(onclick)
            if m:
                try:
                    href = o_tpl.format(*m.groups())
                except Exception:
                    href = ""
        if not href or href.startswith(("javascript:", "mailto:", "tel:")):
            continue
        absurl = urljoin(base, href).split("#")[0]
        # 自链接过滤：href 直接指回栏目列表页本身（如连云港「事业单位招聘」导航项），
        # 那不是公告，只是栏目名。比对时忽略协议/末尾斜杠差异。
        if _same_page(absurl, source.get("url", "")):
            continue
        if absurl in seen:
            continue
        seen.add(absurl)
        # 日期提取优先级：组件 time 属性 > 黏连前缀恢复 > 邻接节点文本 > 父容器全文
        # （SPA 源的日期常在兄弟/远祖元素里；祖先文本可能混入页面当前日期，
        #   所以组件属性/黏连前缀命中时不再走祖先扩展，避免拿到抓取当日）
        parent = a.find_parent(["li", "div", "tr", "td", "p"]) or a
        row_date = (extract_date(comp_time, absurl, title) if comp_time else "") or glued_date
        # 邻接节点文本：紧贴 <a> 的兄弟节点，**遇到下一个 <a> 就停**（本条目边界）。
        # 政务站常用「<a>标题</a><span class="bt_time">日期</span><br>」平铺，
        # 根本没有 <li> 包裹，find_parent 会一路找到整个列表容器；容器全文里第一个
        # 日期是最新那条的，于是整页所有条目被赋成同一天。
        # 丽水实测：列表页该行明明写着 2026-09-03（详情页正文也是 09-03），
        # 却全被算成 2026-10-23，看板日期分组因此整组错位。
        if not row_date:
            parts = []
            for sib in a.next_siblings:
                if getattr(sib, "name", None) == "a":
                    break
                try:
                    parts.append(sib.get_text(" ", strip=True)
                                 if hasattr(sib, "get_text") else str(sib))
                except Exception:
                    pass
                if len(parts) >= 4:
                    break
            for sib in list(a.previous_siblings)[-3:]:
                if getattr(sib, "name", None) == "a":
                    break
                try:
                    parts.append(sib.get_text(" ", strip=True)
                                 if hasattr(sib, "get_text") else str(sib))
                except Exception:
                    pass
            row_date = extract_date(" ".join(p for p in parts if p).strip(), absurl, title)
        ptext = parent.get_text(" ", strip=True) if parent else title
        # 父容器全文兜底：仅当该容器"只装着这一个条目"时才可信。
        # 容器里出现 2 个以上 <a>（相邻条目、分页、面包屑都在里面）说明它是列表
        # 容器而非条目容器，全文里的首个日期属于别人，不能用。
        if not row_date:
            _na = len(parent.find_all("a")) if hasattr(parent, "find_all") else 0
            if _na <= 2:
                row_date = extract_date(ptext, absurl, title)
        # 仍未拿到日期时，扩大搜索：
        if not row_date:
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
            # 用第一个能提取到日期的候选文本。
            # 注意：这里必须把 d 直接赋给 row_date。旧写法是「把 cand 追加到 ptext 尾部，
            # 最后再 extract_date(ptext) 从头扫一遍」——可 ptext 开头本来就带着那个错误
            # 日期，重扫必然又命中它，候选项等于白找。
            for cand in candidates:
                d = extract_date(cand, absurl, title)
                if d:
                    row_date = d
                    break
        items.append({"title": title[:200], "url": absurl, "date": row_date})
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
    # last_raw = 过滤前的原始候选条数，供 health_check 区分「解析器失效」与「内容空窗」
    if "last_raw" not in cols:
        conn.execute("ALTER TABLE sources_status ADD COLUMN last_raw INT")
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
    # 标题被站点截断的源（如淮安）豁免白名单，否则「…公开…」这类真公告会被当 junk
    exempt = source.get("name") in whitelist_exempt()
    for it in items:
        # 入库即过滤全局噪声，避免 junk 污染 notices.db 与看板
        if GLOBAL_NOISE.search(it["title"] or ""):
            dropped += 1
            continue
        # 白名单第二道闸：标题必须含招聘/考试专属词，拦住政务门户混入的
        # 行政通知/送达催告/目录发布等（与 daily_digest 口径一致）
        if not exempt and not RECRUIT_WHITELIST.search(it["title"] or ""):
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
                     "%政府采购%", "%单一来源%", "/%",
                     # 2026-09-06 扩充：录用结果公示/招聘会预告/选聘（与 GLOBAL_NOISE 对齐，
                     # 清理加入黑名单前已入库的历史行）
                     "%拟录用%", "%拟聘用%", "%招聘会%", "%选聘%"]
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
    # 全局招聘关键词白名单清理：政务门户/人社局首页混入的行政通知、送达催告、
    # 目录发布、政策文件等（标题不含任何招聘/考试专属词）一次性清扫，
    # 与 store() 的 RECRUIT_WHITELIST 同口径。region=上海沿用其自身白名单规则。
    _exempt_src = whitelist_exempt()
    for (rid, title, region, src) in conn.execute(
            "SELECT id,title,region,source FROM notices").fetchall():
        if region == "上海" or src in _exempt_src:
            continue
        if title and not RECRUIT_WHITELIST.search(title):
            conn.execute("DELETE FROM notices WHERE id=?", (rid,))
            n += 1
    # 上海源反向白名单：政府门户公告/通知栏目混杂大量市政/行政/采购文，
    # 仅保留含招聘专属词的条目，其余（磋商/验收/决算/任免/规划/闭馆等）一律清理。
    # 2026-10-24 补「公务员|考试录用」：上海市局的公告标题是「上海市2026年度考试录用
    # 公务员公告」，不含「招聘/招考/招录」，被这条反向白名单整批误删（上海-省考 27→17）。
    RECRUIT_KEEP = re.compile(
        r"招聘|招考|招录|引进|遴选|选调|竞聘|交流|招聘会|人员招录|公务员|考试录用")
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
    # 同源同规范化标题的历史镜像去重：manda 会把同一公告索引到多个局子站 URL、
    # 山西/江苏部分站同一公告也有两种 URL 路径、长宁"正式版+宣传版"等，
    # 在 store() 入库去重上线前已堆积的同标题不同 URL 重复，这里一次性清理。
    # 每组保留 first_seen 最早那条，其余删除（若被删行有更新的 url/date 则刷新保留行）。
    # 仅同 source 内合并，不跨源误并。
    rows = conn.execute(
        "SELECT id, source, title, url, date, first_seen FROM notices"
    ).fetchall()
    groups = {}
    for rid, rsrc, rtitle, rurl, rdate, rfs in rows:
        if not rtitle:
            continue
        nt = _norm_title(rtitle)
        groups.setdefault((rsrc, nt), []).append([rid, rurl, rdate, rfs])
    for (rsrc, _nt), members in groups.items():
        if len(members) < 2:
            continue
        # first_seen 最早为保留对象；时间字符串可直接字典序比较
        members.sort(key=lambda m: m[3] or "")
        keep = members[0]
        for rid, rurl, rdate, _rfs in members[1:]:
            # 被删行 url 或 date 与保留行不同且非空，则刷新保留行（保留最可点的 url）
            upd = []
            if rurl and rurl != keep[1]:
                keep[1] = rurl
                upd.append("url")
            if rdate and rdate != keep[2]:
                keep[2] = rdate
                upd.append("date")
            if upd:
                if "url" in upd and "date" in upd:
                    conn.execute("UPDATE notices SET url=?, date=? WHERE id=?", (keep[1], keep[2], keep[0]))
                elif "url" in upd:
                    conn.execute("UPDATE notices SET url=? WHERE id=?", (keep[1], keep[0]))
                else:
                    conn.execute("UPDATE notices SET date=? WHERE id=?", (keep[2], keep[0]))
            conn.execute("DELETE FROM notices WHERE id=?", (rid,))
            n += 1

    conn.commit()
    conn.close()
    if n:
        print(f"[清理] 删除 {n} 条历史噪声/重复记录")
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
    """单源抓取+解析（线程安全：不碰 DB）。返回 (source, items, error, raw)。

    raw = 过滤前的原始候选条数。健康检查用它区分：
      raw=0            → 列表页一条候选都读不到，解析器/URL 真失效（CRITICAL）
      raw>0 但 items=0 → 读到了，但当前栏目全是拟聘公示/采购公告（内容空窗，WARN）
    """
    try:
        # 每源 limit 覆盖：sources.json 里配 "limit": N 可单独调大该源入库条数
        limit_per_source = s.get("limit", limit_per_source)
        if s.get("method") == "api":
            # 福建统一平台 JSON API 源：抓取 newsList → items，复用 manda 的过滤链
            # (include/exclude + GLOBAL_NOISE + 规范化标题去重 + 日期窗口)。
            s2 = dict(s)
            raw = _fujian_api_items(s2)
            items = _filter_manda_items(s2, raw)
            return s2, items[:limit_per_source], None, len(raw)
        if s.get("method") == "jsonapi":
            # 通用后台 JSON 接口源（SPA 列表页 + XHR 分页）：抓取命中 include/exclude
            # 后再截断，避免相关性/时间序靠前的无关条目把近期真公告挤掉。
            s2 = dict(s)
            raw = _jsonapi_items(s2)
            items = _filter_manda_items(s2, raw)
            return s2, items[:limit_per_source], None, len(raw)
        if s.get("method") == "manda":
            # 站内搜索 API 源：直接返回 items（title/url/date），不走 HTML 解析
            # 注意：先过滤再去重截断（manda 相关性排序会把近期公告排到靠后，
            # 若先截断再过滤会丢失排位靠后的近期真公告）
            s2 = dict(s)
            raw = _manda_items(s2)
            items = _filter_manda_items(s2, raw)
            return s2, items[:limit_per_source], None, len(raw)
        if s.get("method") == "jsptsearch":
            # 江苏站群 search4 GET SSR 源：解析搜索结果公告(title/url/date)
            s2 = dict(s)
            raw = _jsptsearch_items(s2)
            items = _filter_jsptsearch(s2, raw)
            return s2, items[:limit_per_source], None, len(raw)
        if s.get("method") == "gwycolumn":
            # 上海公务员局专栏（SPA，页面无 href）：专题接口 + 专题公告接口两步
            s2 = dict(s)
            raw = _gwy_column_items(s2)
            items = _filter_manda_items(s2, raw)
            return s2, items[:limit_per_source], None, len(raw)
        html = fetch(s)
        stats = {}
        items = parse_source(s, html, stats)
        # extra_urls：同一专题的信息常分散在多个栏目。典型是江苏省考——
        # col92917「招考公告」只放公告+大纲两条，职位表在 col92919「岗位信息」，
        # 各种简章/专业目录/技能测试大纲在 col92920「相关文件」。只抓第一个栏目
        # 就永远只有 2~3 条。配 extra_urls 后依次抓取、把候选并进来，共用本源的
        # include/exclude，最后按 url+title 去重（同一公告可能同时挂在多个栏目）。
        extra = s.get("extra_urls") or []
        if extra:
            for eu in extra:
                # 支持两种写法：纯字符串（沿用本源 method）或
                # {"url": ..., "method": "browser"}（该子栏目是 JS 动态加载时必须指定）
                if isinstance(eu, dict):
                    eu_url, eu_method = eu.get("url"), eu.get("method")
                else:
                    eu_url, eu_method = eu, None
                if not eu_url:
                    continue
                try:
                    s_eu = dict(s)
                    s_eu["url"] = eu_url
                    if eu_method:
                        s_eu["method"] = eu_method
                    s_eu.pop("extra_urls", None)
                    items.extend(parse_source(s_eu, fetch(s_eu), stats))
                except Exception as e:
                    print(f"[extra_urls] {s['name']} 子栏目失败 {eu_url[:60]}: {str(e)[:60]}")
            seen_k, uniq = set(), []
            for it in items:
                k = ((it.get("url") or "") + "|" + (it.get("title") or ""))
                if k in seen_k:
                    continue
                seen_k.add(k)
                uniq.append(it)
            items = uniq
        return s, items[:limit_per_source], None, stats.get("raw", 0)
    except Exception as e:
        return s, [], str(e), 0


def run_all(limit_per_source=40, max_workers=8,
            skip_fail_threshold=10, retry_after_hours=12,
            only_region=None, exclude_region=None,
            skip_browser=False, force_http_retry=False,
            write_status=True):
    """
    自适应跳过失败源：
    - 连续失败 ≥ skip_fail_threshold 次的源，本轮跳过 fetch（不耗超时）
    - 距 last_attempt_at ≥ retry_after_hours 小时后强制重试一次
    - 重试成功 → fail_streak 归零；仍失败 → fail_streak+1，重新进入跳过窗口
    目的：云端 IP 对部分中国 gov 站点系统性不可达，跳过这些源可省 1-2min。
    - force_http_retry=True（国内通道用）：非 browser 源无视 fail_streak 每轮都试，
      修复海外失败把国内可达源顶进 skip 窗口的跨通道污染。

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

    # 清理孤儿状态行（已删除/改名源的状态残留），避免健康检查误报"长期未尝试"
    # 仅在全量运行时执行：only_region/exclude_region 会把 sources 缩成子集，
    # 若按子集比对，未参与本轮的其他源会被误判为「孤儿」而删掉状态行
    # （2026-10-22 踩坑：--only-region 福建 一次跑完，其余 90 个源状态全被清空，
    #   健康检查随后把它们全报成 not_run，历史 fail_streak 也一并丢失）。
    if not only_region and not exclude_region:
        _names = {s["name"] for s in sources}
        _orphans = [r[0] for r in conn.execute("SELECT name FROM sources_status").fetchall() if r[0] not in _names]
        for _n in _orphans:
            conn.execute("DELETE FROM sources_status WHERE name=?", (_n,))
        if _orphans:
            conn.commit()
            print(f"[状态] 清理孤儿状态行 {len(_orphans)} 个: {_orphans}")

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
        # 跨通道 fail_streak 污染修复（2026-09-06）：fail_streak 由海外/国内共享，
        # GHA 海外 IP 被站方封锁会把 http 源 streak 顶到阈值 → 国内通道跟着 skip，
        # 变成「国内明明可达却从不抓」。国内通道（force_http_retry=True）对非 browser
        # 源不 skip（http 类单发 0.3-3s 代价可忽略），成功即清零；browser 源仍走自适应窗口。
        if skip and force_http_retry and s.get("method", "http") != "browser":
            skip = False
        if skip:
            skipped_sources.append((s, reason))
            report["sources"].append({
                "name": s["name"], "category": s.get("category"),
                "region": s.get("region"), "count": 0, "new": 0,
                "error": None, "skipped": True, "skip_reason": reason,
            })
        else:
            active_sources.append(s)

    # lite 模式（国内自动化无 playwright）：跳过 browser 源，只跑 http/jsptsearch/api 等
    # 轻量源。被跳过的源标记 skipped，不计失败、不污染 fail_streak。
    if skip_browser:
        for s in [x for x in active_sources if x.get("method", "http") == "browser"]:
            report["sources"].append({
                "name": s["name"], "category": s.get("category"),
                "region": s.get("region"), "count": 0, "new": 0,
                "error": None, "skipped": True, "skip_reason": "lite模式跳过browser源",
            })
        active_sources = [x for x in active_sources if x.get("method", "http") != "browser"]

    http_sources = [s for s in active_sources if s.get("method", "http") != "browser"]
    browser_sources = [s for s in active_sources if s.get("method", "http") == "browser"]

    # http 源并发抓取（每个线程只 fetch+parse，不写库，避免 SQLite 跨线程）
    results = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_work, s, limit_per_source): s["name"] for s in http_sources}
        for fut in as_completed(futures):
            name = futures[fut]
            s, items, err, raw = fut.result()
            results[name] = (s, items, err, raw)

    # 浏览器源（反爬、共享单例 browser）顺序抓取，避免并发冲突
    for s in browser_sources:
        s2, items, err, raw = _work(s, limit_per_source)
        results[s2["name"]] = (s2, items, err, raw)

    # 主线程顺序写库（SQLite 线程安全）
    now_str = now_iso()
    for s in active_sources:
        s2, items, err, raw = results[s["name"]]
        if err:
            old = fail_info.get(s["name"], {}).get("streak", 0)
            new_streak = old + 1
            if write_status:
                conn.execute("""INSERT OR REPLACE INTO sources_status
                                (name,last_run,last_count,last_error,fail_streak,last_attempt_at,last_raw)
                                VALUES(?,?,?,?,?,?,?)""",
                             (s["name"], now_str, 0, err[:300], new_streak, now_str, raw))
            report["sources"].append({"name": s["name"], "category": s.get("category"),
                                       "region": s.get("region"), "count": 0, "new": 0,
                                       "error": err[:200]})
        else:
            new = store(conn, s, items)
            if write_status:
                conn.execute("""INSERT OR REPLACE INTO sources_status
                                (name,last_run,last_count,last_error,fail_streak,last_attempt_at,last_raw)
                                VALUES(?,?,?,?,?,?,?)""",
                             (s["name"], now_str, len(items), "", 0, now_str, raw))
            report["sources"].append({"name": s["name"], "category": s.get("category"),
                                       "region": s.get("region"), "count": len(items),
                                       "new": len(new), "raw": raw, "error": None})
            for n in new:
                report["new"].append({"source": s["name"], "category": s.get("category"),
                                       "region": s.get("region"), **n})

    # 更新被跳过源的 last_run（让 UI 显示"上次跳过时间"）；fail_streak/last_attempt_at 不变
    if write_status:
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
    ap.add_argument("--no-status", action="store_true",
                    help="只入库公告，不写 sources_status（海外 IP 轨道用，避免覆盖国内通道健康度）")
    args = ap.parse_args()
    print(json.dumps(run_all(only_region=args.only_region,
                             exclude_region=args.exclude_region,
                             write_status=not args.no_status),
                     ensure_ascii=False, indent=2))
