#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""诊断「云端抓不到」的源里，哪些其实是**请求头/WAF**问题，不是 IP 封锁。

为什么要这个脚本：403 Forbidden 有两种完全不同的成因，
  ① 站方按 IP 段封（海外 IP 一律拒绝）→ 只能代理，代码无解；
  ② WAF 按 UA / Referer / TLS 指纹 / 请求头组合判定 → 换一套头就通了。
只看一个 403 分不出是哪一种，而这两种的处置代价差一个量级。

做法：在**云端（海外 IP）**跑同一个 URL 的多种请求头变体，
如果某个变体拿到 200 而且内容像正常列表 → 那就是 ②，可修。
如果所有变体都是 403 → 那就是 ①，别浪费时间。

用法（云端，由 blocked_diagnose.yml 调用）：
    python tools/diagnose_blocked.py --json-out reports/blocked.json \
        --md-out reports/换头诊断.md
本地跑也有用：可以确认「国内 IP 下全 200」，从而证明 URL 本身没写错。
"""
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import scraper  # noqa: E402

# 这一批是 2026-10-26 云端普查里「不是超时」的失败源：
# 403 / 493 / 接口返回异常。超时那批属于 IP 层封锁，不在这里浪费请求。
TARGETS = [
    "云南-省考",
    "安徽-省考",
    "河南-省考",
    "江苏扬州-事业单位",
    "山东-省考",
    "江苏南通-事业单位",
    "江苏宿迁-事业单位",
    "河北-省考",
    "河北-省直事业单位",
    "龙岩-事业单位招聘",
]

CHROME = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
ANDROID = ("Mozilla/5.0 (Linux; Android 14; PGT-AN20) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36")
WECHAT = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
          "(KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.49")

# 变体顺序有意为之：越靠前越接近「现有行为」，靠后的才是「换一套身份」。
def variants(url):
    host = urllib.parse.urlsplit(url).netloc
    return [
        ("v0 现状(scraper UA)", {"User-Agent": scraper.DEFAULT_UA,
                                 "Accept-Encoding": "gzip",
                                 "Accept-Language": "zh-CN,zh;q=0.9"}),
        ("v1 Chrome 全头", {"User-Agent": CHROME,
                            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                            "Accept-Encoding": "gzip, deflate",
                            "Upgrade-Insecure-Requests": "1"}),
        ("v2 Chrome + Referer 自身", {"User-Agent": CHROME,
                                      "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                                      "Accept-Language": "zh-CN,zh;q=0.9",
                                      "Referer": f"https://{host}/"}),
        ("v3 Chrome + Referer 百度", {"User-Agent": CHROME,
                                      "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                                      "Accept-Language": "zh-CN,zh;q=0.9",
                                      "Referer": "https://www.baidu.com/"}),
        ("v4 移动 Chrome", {"User-Agent": ANDROID,
                            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                            "Accept-Language": "zh-CN,zh;q=0.9"}),
        ("v5 微信内置", {"User-Agent": WECHAT,
                         "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                         "Accept-Language": "zh-CN,zh;q=0.9"}),
        ("v6 裸 curl", {"User-Agent": "curl/8.5.0", "Accept": "*/*"}),
        ("v7 不带 UA", {"Accept": "*/*"}),
    ]


def _ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    if hasattr(ssl, "OP_LEGACY_SERVER_CONNECT"):
        try:
            ctx.options |= ssl.OP_LEGACY_SERVER_CONNECT
        except Exception:
            pass
    return ctx


def probe(url, headers, timeout=12):
    """返回 (status, 字节数, 正文前 160 字的可读片段, 耗时)。不抛异常。

    ⚠️ 这里必须用**带 cookie jar 的 opener**，不能图省事写 urlopen。
    用裸 urlopen 的后果实测过：河南-省考（302 + Set-Cookie，带同一 cookie
    再请求才 200）在报告里显示成「302 非 403 类失败」，看起来像站点改版，
    实际只是缺 Cookie 支持 —— 而 scraper.fetch_http 早就修好了。
    诊断工具和抓取器用两套逻辑，结论就会互相打脸，这个工具也就没用了。
    带 jar 的 opener 默认跟随 3xx（最多 10 跳）并在跳转时带上 cookie，
    正好等于浏览器的行为，也就等于我们要判定的「换头能不能过」。
    """
    import http.cookiejar
    t0 = time.time()
    ctx = _ctx()
    req = urllib.request.Request(url, headers=headers)
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ctx),
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
    )
    try:
        with opener.open(req, timeout=timeout) as r:
            raw = _drain(r)
            return r.status, len(raw), _snip(raw, r.headers, r.status), time.time() - t0
    except urllib.error.HTTPError as e:
        try:
            raw = _drain(e)
        except Exception:
            raw = b""
        return e.code, len(raw), _snip(raw, e.headers, e.code), time.time() - t0
    except Exception as e:
        return 0, 0, f"{type(e).__name__}: {str(e)[:110]}", time.time() - t0


def _drain(resp):
    """读 body 并解压。

    必须解压：默认头里带 Accept-Encoding: gzip，不解的话「正文片段」全是乱码，
    判断「200 且内容正常」就会误判成异常 —— 第一版就是这样把河北两个源全判成坏的。
    """
    raw = resp.read()
    enc = (resp.headers.get("Content-Encoding") or "").lower() if resp.headers else ""
    try:
        if "gzip" in enc:
            import gzip
            raw = gzip.decompress(raw)
        elif "deflate" in enc:
            import zlib
            try:
                raw = zlib.decompress(raw)
            except Exception:
                raw = zlib.decompress(raw, -zlib.MAX_WBITS)
    except Exception:
        pass
    return raw


def _snip(raw, headers, status=0):
    # 3xx 一定要把 Location 打出来：不然只看到「302 Found」，不知道它想把你送去哪，
    # 也就判断不了「跟着跳能不能过」。河南-省考本地就卡在这里。
    if status in (301, 302, 303, 307, 308):
        loc = headers.get("Location", "") if headers else ""
        return f"→ Location: {loc[:120]}"
    try:
        enc = "utf-8"
        m = None
        ctype = headers.get("Content-Type", "") if headers else ""
        import re
        m = re.search(r"charset=([\w-]+)", ctype or "")
        if m:
            enc = m.group(1).lower()
        txt = raw[:400].decode(enc, errors="ignore")
    except Exception:
        txt = str(raw[:200])
    return " ".join(txt.split())[:160]


def looks_like_content(status, nbytes, snip):
    """200 且有实内容才算真通。有的 WAF 拦截页也是 200，所以要排除空壳。"""
    if status != 200 or nbytes < 800:
        return False
    low = snip.lower()
    bad = ("访问受限", "禁止访问", "forbidden", "blocked", "人机验证", "captcha",
           "请开启javascript", "<title>403")
    return not any(b in low for b in bad)


def _write_md(report, path):
    """同一份结果再出一份人看的 markdown：JSON 只给脚本用，讨论时需要能直接读。"""
    L = []
    L.append("# 换请求头诊断（云端出口实测）\n")
    L.append(f"跑于 {report['run_at']}。目的：分清「WAF 按请求头拦」和「按 IP 段封」——"
             "前者换一套头就能救，后者只能代理。\n")
    fixable = [x for x in report["sources"] if x["usable_variants"] or x["usable_api_variants"]]
    ipblocked = [x for x in report["sources"] if "按 IP 段封" in x["verdict"]]
    other = [x for x in report["sources"] if x not in fixable and x not in ipblocked]

    L.append("## 总览\n")
    L.append(f"- **换头可救：{len(fixable)} 个** —— {'、'.join(x['name'] for x in fixable) or '无'}")
    L.append(f"- **确认按 IP 段封：{len(ipblocked)} 个** —— {'、'.join(x['name'] for x in ipblocked) or '无'}")
    L.append(f"- 其他（非 403 类，需看具体报错）：{len(other)} 个 —— "
             f"{'、'.join(x['name'] for x in other) or '无'}\n")

    for x in report["sources"]:
        L.append(f"## {x['name']}  [{x['method']}]\n")
        L.append(f"`{x['url']}`\n")
        L.append(f"**结论：{x['verdict']}**；可用变体：{', '.join(x['usable_variants']) or '无'}\n")
        L.append("| 变体 | 状态 | 字节 | 耗时 | 通 | 摘要 |")
        L.append("|---|---|---|---|---|---|")
        for r in x["variants"]:
            snip = (r["snip"] or "").replace("|", "\\|")[:70]
            L.append(f"| {r['variant']} | {r['status']} | {r['bytes']} | {r['seconds']}s "
                     f"| {'✅' if r['ok'] else '—'} | {snip} |")
        if x["api_variants"]:
            L.append("\n数据接口：\n")
            L.append("| 变体 | 状态 | 字节 | 耗时 | 通 | 摘要 |")
            L.append("|---|---|---|---|---|---|")
            for r in x["api_variants"]:
                snip = (r["snip"] or "").replace("|", "\\|")[:70]
                L.append(f"| {r['variant']} | {r['status']} | {r['bytes']} | {r['seconds']}s "
                         f"| {'✅' if r['ok'] else '—'} | {snip} |")
        L.append("")

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L))


def main():
    ap_out = None
    md_out = None
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--json-out" and i + 1 < len(args):
            ap_out = args[i + 1]
        elif a == "--md-out" and i + 1 < len(args):
            md_out = args[i + 1]

    srcs = {}
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, "sources.json"), encoding="utf-8") as f:
        for s in json.load(f):
            srcs[s["name"]] = s

    print("=" * 96)
    print("请求头/WAF 诊断（要在云端跑才有意义：本地国内 IP 一律 200，看不出问题）")
    print("=" * 96)

    report = {"mode": "blocked_diagnose",
              "run_at": time.strftime("%Y-%m-%d %H:%M:%S"),
              "sources": []}

    for name in TARGETS:
        s = srcs.get(name)
        if not s:
            print(f"\n### {name} —— 配置里找不到，跳过")
            continue
        url = s["url"]
        method = s.get("method", "http")
        print(f"\n### {name}  [{method}]  {url}")
        rows = []
        for vname, hdrs in variants(url):
            st, n, snip, dt = probe(url, hdrs)
            ok = looks_like_content(st, n, snip)
            mark = "✅" if ok else ("·" if st == 200 else "✗")
            print(f"  {mark} {vname:<22} {st:>4}  {n:>7}B  {dt:4.1f}s  {snip[:70]}")
            rows.append({"variant": vname, "status": st, "bytes": n,
                         "seconds": round(dt, 1), "ok": ok, "snip": snip})

        # 接口类源：栏目页通了不代表数据接口通，单独再打一次接口
        api_rows = []
        if method in ("jsonapi", "jpaas"):
            api_url, headers_extra = _api_target(s)
            if api_url:
                print(f"  -- 数据接口：{api_url[:100]}")
                for vname, hdrs in variants(url):
                    hh = dict(hdrs)
                    hh.update(headers_extra)
                    st, n, snip, dt = probe(api_url, hh)
                    ok = st == 200 and n > 200
                    mark = "✅" if ok else ("·" if st == 200 else "✗")
                    print(f"  {mark} {vname:<22} {st:>4}  {n:>7}B  {dt:4.1f}s  {snip[:70]}")
                    api_rows.append({"variant": vname, "status": st, "bytes": n,
                                     "seconds": round(dt, 1), "ok": ok, "snip": snip})

        good = [r["variant"] for r in rows if r["ok"]]
        good_api = [r["variant"] for r in api_rows if r["ok"]]
        if good or good_api:
            verdict = "换请求头可通（不是 IP 封锁）"
        elif all(r["status"] in (403, 493) for r in rows):
            verdict = "所有变体都被拦（403/493）→ 大概率按 IP 段封"
        else:
            verdict = "非 403 类失败，需要看具体报错"
        print(f"  → 结论：{verdict}；可用变体 {good or good_api or '无'}")
        report["sources"].append({
            "name": name, "url": url, "method": method,
            "verdict": verdict, "usable_variants": good, "usable_api_variants": good_api,
            "variants": rows, "api_variants": api_rows,
        })

    if ap_out:
        os.makedirs(os.path.dirname(os.path.abspath(ap_out)), exist_ok=True)
        with open(ap_out, "w", encoding="utf-8", newline="\n") as f:
            json.dump(report, f, ensure_ascii=False, indent=1)
        print(f"\n[诊断] 已写入 {ap_out}")
    if md_out:
        _write_md(report, md_out)
        print(f"[诊断] 已写入 {md_out}")

    fixable = [x["name"] for x in report["sources"] if x["usable_variants"] or x["usable_api_variants"]]
    ipblocked = [x["name"] for x in report["sources"] if "按 IP 段封" in x["verdict"]]
    print("\n" + "=" * 96)
    print(f"换头可救 {len(fixable)} 个：{'、'.join(fixable) or '无'}")
    print(f"确认 IP 封 {len(ipblocked)} 个：{'、'.join(ipblocked) or '无'}")
    return 0


def _api_target(s):
    """给接口类源拼出「数据接口 URL + 额外头」。栏目页通了也不能说明接口通。"""
    m = s.get("method")
    if m == "jsonapi":
        api = s.get("api_url")
        if not api:
            return None, {}
        if (s.get("api_method") or "get").lower() == "get":
            body = dict(s.get("api_body") or {})
            params = {k: str(v).replace("{page}", "1").replace("{size}",
                      str(s.get("api_size", 100))) for k, v in body.items()}
            sep = "&" if "?" in api else "?"
            return api + (sep + urllib.parse.urlencode(params) if params else ""), {}
        return api, {"Content-Type": "application/json"}
    if m == "jpaas":
        # jpaas 的接口要靠栏目页里的 queryData 现场扒，栏目页 403 时扒不到，
        # 这里只把「接口路径」猜出来供参考，不做真实请求。
        return None, {}
    return None, {}


if __name__ == "__main__":
    sys.exit(main())
