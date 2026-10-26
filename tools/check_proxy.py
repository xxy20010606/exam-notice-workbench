#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一条命令验证 SCRAPE_PROXY 到底通不通、能救哪些源。

背景（2026-10-26）：`SCRAPE_PROXY` 这个 Secret 在 workflow 里挂了很久，但四个请求
函数都写成 `opener.open(req, timeout=..., context=ctx)` —— `OpenerDirector.open()`
不接受 `context`，直接 TypeError，又被外层 `except` 吞掉，于是**配了等于没配**，
一次都没成功走过代理。修好之后需要一把能立刻验收的尺子，而不是等下一次定时任务
跑完再去日志里翻。

用法：
    # 本地：先把代理写进环境变量
    export SCRAPE_PROXY=http://user:pass@1.2.3.4:8080
    python tools/check_proxy.py

    # GitHub Actions：手动触发 proxy_check.yml，Secret 会自动注入

退出码：0 = 代理可用（至少救回一个直连失败的源）；1 = 代理不可用或未配置。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import scraper  # noqa: E402

# 挑的都是「海外 IP 被整域封死、国内 IP 正常」的站：这类站是代理唯一能救的对象。
# 加一个上海市人社局做对照：它在云端本来就能抓，用来确认「配了代理之后
# 原本能通的源没被代理拖坏」。
TARGETS = [
    ("江苏镇江-事业单位", "https://hrss.zhenjiang.gov.cn/hrss/tzgg/list.shtml"),
    ("江苏宿迁-事业单位", "https://sqhrss.suqian.gov.cn/rlzyj/rsxx/xxgk_list.shtml"),
    ("对照·上海人社局", "https://rsj.sh.gov.cn/tsydwgkzp_17406/index.html"),
]


def _mask(p):
    """只露协议和主机，账号密码打掉——日志会被贴到聊天窗口。"""
    if not p:
        return "（未配置）"
    if "@" not in p:
        return p
    head, tail = p.rsplit("@", 1)
    scheme = head.split("://", 1)[0] if "://" in head else ""
    return f"{scheme}://***:***@{tail}" if scheme else f"***@{tail}"


def _direct(url, timeout=12):
    """直连一次：临时摘掉 SCRAPE_PROXY，否则 fetch_http 会自己回退到代理，
    测出来的「直连」其实是代理的结果。"""
    saved = os.environ.pop("SCRAPE_PROXY", None)
    t0 = time.time()
    try:
        body = scraper.fetch_http(url, timeout=timeout, retries=0)
        return True, time.time() - t0, len(body), ""
    except Exception as e:
        return False, time.time() - t0, 0, f"{type(e).__name__}: {str(e)[:90]}"
    finally:
        if saved is not None:
            os.environ["SCRAPE_PROXY"] = saved


def _via_proxy(url, timeout=15):
    t0 = time.time()
    try:
        body = scraper._proxy_fetch_text(url, timeout=timeout, tries=1)
        return True, time.time() - t0, len(body), ""
    except Exception as e:
        return False, time.time() - t0, 0, f"{type(e).__name__}: {str(e)[:90]}"


def main():
    proxy = os.environ.get("SCRAPE_PROXY", "").strip()
    print("=" * 70)
    print("SCRAPE_PROXY 验收")
    print(f"代理：{_mask(proxy)}")
    print("=" * 70)
    if not proxy:
        print("\n[FAIL] 没有配置 SCRAPE_PROXY，无从验证。")
        print("  本地：export SCRAPE_PROXY=http://user:pass@host:port")
        print("  Actions：仓库 Settings → Secrets → SCRAPE_PROXY")
        return 1

    rows, rescued = [], 0
    for name, url in TARGETS:
        d_ok, d_t, d_n, d_err = _direct(url)
        p_ok, p_t, p_n, p_err = _via_proxy(url)
        if p_ok and not d_ok:
            verdict, rescued = "代理可救 ✓", rescued + 1
        elif p_ok and d_ok:
            verdict = "两条路都通（代理没拖慢）"
        elif not p_ok and d_ok:
            verdict = "代理反而不通（直连够用，先别开）"
        else:
            verdict = "两条路都不通（该站可能真挂了/需登录）"
        rows.append((name, d_ok, d_t, d_n, d_err, p_ok, p_t, p_n, p_err, verdict))

    print()
    print(f"{'源':<20}{'直连':<28}{'代理':<28}判定")
    print("-" * 100)
    for name, d_ok, d_t, d_n, d_err, p_ok, p_t, p_n, p_err, verdict in rows:
        d = f"{'OK' if d_ok else 'FAIL'} {d_t:5.1f}s {d_n}B" if d_ok else f"FAIL {d_t:5.1f}s"
        p = f"{'OK' if p_ok else 'FAIL'} {p_t:5.1f}s {p_n}B" if p_ok else f"FAIL {p_t:5.1f}s"
        print(f"{name:<20}{d:<28}{p:<28}{verdict}")

    errs = [(n, d_err, p_err) for n, d_ok, _, _, d_err, p_ok, _, _, p_err, _ in rows
            if not d_ok or not p_ok]
    if errs:
        print("\n--- 报错详情 ---")
        for n, d_err, p_err in errs:
            print(f"· {n}")
            if d_err:
                print(f"    直连：{d_err}")
            if p_err:
                print(f"    代理：{p_err}")

    proxy_ok_n = sum(1 for r in rows if r[5])
    direct_ok_n = sum(1 for r in rows if r[1])
    print()
    if rescued:
        print(f"[OK] 代理可用，已在 {rescued}/{len(TARGETS)} 个直连失败的源上验证成功。")
        print("     下一步：确认 Actions 里的 SCRAPE_PROXY Secret 是同一个值，")
        print("     然后手动跑一次「云端可达性探针」，镇江/宿迁应能在报告里出现产出。")
        return 0
    if proxy_ok_n and direct_ok_n == len(TARGETS):
        # 本机在中国大陆出口时必然走这一支：直连全通，没有「可救」的样本。
        # 这不代表代理没用，只代表尺子量不到——真正需要代理的是 Actions 的海外出口。
        print(f"[OK] 代理链路可用（{proxy_ok_n}/{len(TARGETS)} 个请求经代理成功）。")
        print("     本机直连全通是正常的（国内 IP），所以没有「代理救回」的样本。")
        print("     要验证对海外出口有没有用，请在 Actions 上手动跑这个 workflow。")
        return 0
    if proxy_ok_n:
        print(f"[WARN] 代理只通了 {proxy_ok_n}/{len(TARGETS)} 个请求，且没救回任何一个源。")
        print("     看上面的报错：403/407 是代理认证或目标站风控，超时是代理本身不通，")
        print("     SSL 错误多半是代理不支持 CONNECT 隧道（需要支持 https 的代理）。")
        return 1
    print("[FAIL] 代理完全不可用：所有经代理的请求都失败了。")
    print("     先确认地址格式 http://user:pass@host:port，再确认代理支持 CONNECT。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
