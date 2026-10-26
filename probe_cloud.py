#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""云端可达性探针

只做一件事：在 GitHub Actions（**海外 IP**）环境里跑一遍全量抓取，
把每个源的结果打出来，回答「云端到底能抓多少」。

为什么需要单独一个脚本：本地全量跑 fail_streak 全 0，但那走的是国内 IP。
云端是海外 IP，国内 gov.cn 对海外 IP 的封锁程度只能实测，不能靠推测。
（历史上有 8 个源因海外 IP 被格挡。）

安全性：不写 sources_status、不提交任何文件（对应 workflow 里没有 commit 步骤）。
跑完只打印报告，库改动随 runner 销毁。
"""
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scraper  # noqa: E402


def expected_names(only_region=None):
    """按 sources.json 算出「本轮应该出现在的报告里」的源名，用于完整性自检。"""
    import json
    here = os.path.dirname(os.path.abspath(__file__))
    d = json.load(open(os.path.join(here, "sources.json"), encoding="utf-8"))
    srcs = d["sources"] if isinstance(d, dict) else d
    if only_region:
        srcs = [s for s in srcs if only_region in (s.get("region") or "")]
    return [s["name"] for s in srcs if s.get("enabled", True) is not False]


def check_integrity(all_src, only_region=None):
    """校验报告条目与源配置是否一一对应。返回 (问题描述列表, 去重后的条目)。

    为什么需要这一步：2026-10-26 run #8 的「零产出 2 个」里出现了两行**一模一样**
    的源名（江苏镇江-人社招聘搜索）。当时的直接后果是把它误读成「还有两个源在云端
    掉成 0」，白查一轮。这类重复既虚增计数，也往往意味着有源没被报告出来
    （真故障被藏起来）。靠人眼对 15 行表格数不出这种事，只能让脚本自己喊。
    """
    problems = []
    names = [x.get("name") for x in all_src]
    cnt = collections.Counter(names)
    dup = [n for n, c in cnt.items() if c > 1 and n]
    exp = expected_names(only_region)
    missing = [n for n in exp if n not in cnt]
    if len(all_src) != len(exp):
        problems.append(f"报告 {len(all_src)} 条 ≠ 配置 {len(exp)} 个源")
    if dup:
        problems.append("重名源：" + "、".join(dup))
    if missing:
        problems.append("配置里有但报告缺失：" + "、".join(missing))
    if dup:
        # 同名只留一条：优先保留带 error 的（失败比 0 产出更严重），否则保留第一条
        keep = {}
        for x in all_src:
            n = x.get("name")
            prev = keep.get(n)
            if prev is None or (x.get("error") and not prev.get("error")):
                keep[n] = x
        all_src = list(keep.values())
    return problems, all_src


def http_on_browser(only_region=None):
    """对 method=browser 的源，改用**纯 http** 试一次并解析。

    回答一个卡住判断的问题：这些站在云端是「彻底不可达」，还是「只有浏览器不通」？

    · 如果 http 能拿到并解析出条目 → 说明站是服务端渲染，把源从 browser 改成 http
      就能在云端工作，13 个浙江源、乃至 34 个 browser 源里的一大批都能救回来。
    · 如果 http 也超时 → 就是海外 IP 在 TCP 层被封锁，代码解决不了，
      只能配 SCRAPE_PROXY 走国内代理，或者接受「这些源只在本地通道补」。

    为什么值得单独测：浏览器会加载 CSS/JS/图片，主文档能通、子资源被封也会表现为
    整体 Timeout；纯 http 只取一个 HTML，能更干净地判断网络可达性。
    """
    import json
    import time as _t

    here = os.path.dirname(os.path.abspath(__file__))
    d = json.load(open(os.path.join(here, "sources.json"), encoding="utf-8"))
    srcs = [s for s in (d["sources"] if isinstance(d, dict) else d)
            if s.get("method", "http") == "browser" and s.get("enabled", True)]
    if only_region:
        srcs = [s for s in srcs if only_region in (s.get("region") or "")]

    print()
    print("=" * 64)
    print(f"纯 http 直取测试：{len(srcs)} 个 browser 源" +
          (f"（地区含「{only_region}」）" if only_region else "（全部）"))
    print("=" * 64)

    usable, empty, dead = [], [], []
    for s in srcs:
        t0 = _t.time()
        try:
            html = scraper.fetch_http(s["url"], timeout=20, retries=0)
            st = {}
            items = scraper.parse_source(s, html, st)
            dt = _t.time() - t0
            raw = st.get("raw", 0)
            if items:
                print(f"  ✅ {s['name']}: HTML {len(html)}B / 候选 {raw} / 命中 {len(items)} 条  ({dt:.1f}s)")
                usable.append((s["name"], len(items)))
            else:
                print(f"  ⚠️ {s['name']}: HTML {len(html)}B / 候选 {raw} / 命中 0  ({dt:.1f}s)")
                empty.append(s["name"])
        except Exception as e:
            msg = str(e).strip().splitlines()
            print(f"  ✗ {s['name']}: {(msg[0] if msg else '')[:90]}")
            dead.append(s["name"])

    print()
    print("-" * 64)
    print(f"可改 http 直接可用：{len(usable)} 个")
    for n, c in usable:
        print(f"    · {n}（{c} 条）")
    print(f"http 通了但解析 0 条：{len(empty)} 个（需调 container/正则）")
    for n in empty:
        print(f"    · {n}")
    print(f"http 都不可达：{len(dead)} 个（海外 IP 封锁，只能代理或本地通道）")
    for n in dead:
        print(f"    · {n}")

    # 也写一份到 workflow summary。第一版漏了这个，跑完页面是空的，
    # 只能点进 probe 步骤翻日志才看得到结论。
    md = ["## 纯 http 直取（对 browser 源）", "",
          f"- 测试 **{len(srcs)}** 个源" +
          (f"（地区含「{only_region}」）" if only_region else ""),
          f"- 可改 http 直接可用 **{len(usable)}** 个",
          f"- http 通了但解析 0 条 **{len(empty)}** 个",
          f"- http 都不可达 **{len(dead)}** 个（海外 IP 封锁）"]
    if usable:
        md += ["", "### 可改 http 直接可用", "", "| 源 | 命中条数 |", "|---|---|"]
        md += [f"| {n} | {c} |" for n, c in usable]
    if empty:
        md += ["", "### http 通了但解析 0 条（需调 container / 正则）", ""]
        md += [f"- {n}" for n in empty]
    if dead:
        md += ["", "### http 都不可达（只能代理或本地通道）", ""]
        md += [f"- {n}" for n in dead]
    sf = os.environ.get("GITHUB_STEP_SUMMARY")
    if sf:
        try:
            with open(sf, "a", encoding="utf-8") as f:
                f.write("\n".join(md) + "\n")
            print("\n[探针] 已写入 GITHUB_STEP_SUMMARY")
        except Exception as e:
            print(f"[探针] 写 summary 失败：{e}")
    return 0


def main():
    import argparse
    ap = argparse.ArgumentParser(description="云端可达性探针")
    ap.add_argument("--only-region", default=None,
                    help="只测 region 含该关键字的源（如 浙江）。"
                         "全量跑 34 个 browser 源串行会超过 Actions 单 job 上限，"
                         "分区跑是更快拿到结论的办法。")
    ap.add_argument("--skip-browser", action="store_true",
                    help="跳过 browser 源，只测 http/api/jsonapi 等轻量源（1-2 分钟出结果）")
    ap.add_argument("--http-on-browser", action="store_true",
                    help="对 browser 源改用纯 http 直取并解析，判断是「网站不可达」"
                         "还是「只有浏览器不通」")
    args = ap.parse_args()

    if args.http_on_browser:
        return http_on_browser(args.only_region)

    scope = []
    if args.only_region:
        scope.append(f"地区含「{args.only_region}」")
    if args.skip_browser:
        scope.append("跳过 browser 源")
    print("=" * 64)
    print("云端可达性探针（海外 IP / 不写状态 / 不提交）")
    print("范围：" + ("、".join(scope) if scope else "全量启用源"))
    print("=" * 64)

    report = scraper.run_all(write_status=False,
                             only_region=args.only_region,
                             skip_browser=args.skip_browser)
    all_src = report.get("sources", [])
    problems, all_src = check_integrity(all_src, args.only_region)
    skipped = [s for s in all_src if s.get("skipped")]
    ran = [s for s in all_src if not s.get("skipped")]
    failed = [s for s in ran if s.get("error")]
    ok = [s for s in ran if not s.get("error")]
    zero = [s for s in ok if (s.get("count") or 0) == 0]
    has_data = [s for s in ok if (s.get("count") or 0) > 0]

    print()
    print("-" * 64)
    print(f"实际尝试 {len(ran)} 个源 | 跳过 {len(skipped)} 个")
    print(f"  成功 {len(ok)} 个（其中有产出 {len(has_data)}，零产出 {len(zero)}）")
    print(f"  失败 {len(failed)} 个")
    print("-" * 64)
    if problems:
        print()
        print("### ⚠️ 报告完整性异常 —— 下面的数字可能不可信")
        for p in problems:
            print(f"  ! {p}")

    if failed:
        print()
        print(f"### 失败源（{len(failed)} 个）—— 云端抓不到，只能靠本地通道补")
        for s in sorted(failed, key=lambda x: x.get("name") or ""):
            err = (s.get("error") or "").strip().splitlines()
            err = err[0][:110] if err else "（无错误信息）"
            print(f"  ✗ {s.get('name')} [{s.get('region')}/{s.get('category')}]")
            print(f"      {err}")

    if zero:
        print()
        print(f"### 零产出（{len(zero)} 个）—— 抓通了但没解析出条目")
        print("    可能是栏目当前真没内容，也可能是被识别成海外流量后返回空壳页")
        for s in sorted(zero, key=lambda x: x.get("name") or ""):
            print(f"  · {s.get('name')} [{s.get('region')}/{s.get('category')}]")

    if skipped:
        print()
        print(f"### 跳过（{len(skipped)} 个，fail_streak 未到重试窗口）")
        for s in sorted(skipped, key=lambda x: x.get("name") or ""):
            print(f"  - {s.get('name')}: {s.get('skip_reason')}")

    # 给 workflow summary 用的 markdown
    md = []
    md.append("## 云端（海外 IP）抓取可达性")
    md.append("")
    md.append(f"- 范围：`{' '.join(sys.argv[1:]) or '全量'}`")
    md.append(f"- 实际尝试 **{len(ran)}** 个源，跳过 {len(skipped)} 个")
    md.append(f"- 成功 **{len(ok)}**（有产出 {len(has_data)} / 零产出 {len(zero)}）")
    md.append(f"- 失败 **{len(failed)}**")
    if problems:
        md.append("")
        md.append("> ⚠️ **报告完整性异常，以下数字可能不可信**")
        for p in problems:
            md.append(f"> - {p}")
    # 全量明细：以前 summary 只列失败源，看不出「哪个源抓到了几条」——
    # 每次都要回头翻原始日志才知道新修的源到底有没有产出。直接铺一张全量表。
    # 放在失败详情表**前面**：这张表才是「一眼看完谁通谁不通」的总览，
    # 放后面会掉到首屏之外，每次都得往下滚才看得到。
    if ran:
        md.append("")
        md.append(f"### 全部 {len(ran)} 个源明细（按产出倒序，表格最下面就是要处理的）")
        md.append("")
        md.append("| 源 | 地区 | 产出 | 状态 |")
        md.append("|---|---|---|---|")
        # 排序：产出降序；同为 0 产出时把「失败」排在「零产出」前面（更严重）；
        # 再按名字稳定排序。这样表格底部天然聚齐了所有该看的行。
        for s in sorted(ran, key=lambda x: (-(x.get("count") or 0),
                                            0 if x.get("error") else 1,
                                            x.get("name") or "")):
            if s.get("error"):
                state = "✗ 失败"
            elif (s.get("count") or 0) > 0:
                state = "✓"
            else:
                state = "零产出"
            md.append(f"| {s.get('name')} | {s.get('region')} | "
                      f"{s.get('count') or 0} | {state} |")
    # 失败源的具体报错放在最后：明细表已经标出「✗ 失败」，这里补的是错误原因
    if failed:
        md.append("")
        md.append(f"### 失败源错误详情（{len(failed)} 个）")
        md.append("")
        md.append("| 失败源 | 地区 | 错误 |")
        md.append("|---|---|---|")
        for s in sorted(failed, key=lambda x: x.get("name") or ""):
            err = (s.get("error") or "").strip().splitlines()
            err = (err[0][:80] if err else "—").replace("|", "\\|")
            md.append(f"| {s.get('name')} | {s.get('region')} | {err} |")
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_file:
        try:
            with open(summary_file, "a", encoding="utf-8") as f:
                f.write("\n".join(md) + "\n")
            print("\n[探针] 已写入 GITHUB_STEP_SUMMARY")
        except Exception as e:
            print(f"[探针] 写 summary 失败：{e}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
