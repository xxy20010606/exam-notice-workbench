#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把云端探针的两份 JSON 合成一份人看的 markdown。

回答的问题只有一个：**电脑不开机时，手机上看板里能有多少是当天的。**
所以分组不按地区、不按 method，而按「云端到底抓不抓得到」：

  A 云端可抓     —— 海外 IP 实测有产出，关机也有
  B 云端零产出   —— 连上了但没解析出条目（栏目空窗 / 过滤太严 / 需要调容器）
  C 云端失败     —— 连不上（海外 IP 封锁），只能本地通道补，即「必须开机」
  D 需浏览器     —— method=browser：云端要能起 Chromium 才谈得上抓
  E 未测         —— 只跑了轻量源那次，浏览器源没进报告

用法：python tools/build_reachability_md.py <轻量源 json> <浏览器源 json> <输出 md>
"""
import json
import os
import sys
from collections import Counter, defaultdict


def _load(p):
    if not p or not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def main():
    light_p, browser_p, out_p = (sys.argv[1:4] + ["", "", ""])[:3]
    # 第 4 个参数是标题前缀：同一份脚本既要出「云端实测」，也要能出「本机对照」，
    # 否则两张表标题一模一样，贴进聊天窗口分不清哪张是海外出口的结果。
    label = sys.argv[4] if len(sys.argv) > 4 else "云端（海外 IP）"
    light = _load(light_p)
    brow = _load(browser_p)
    if not light and not brow:
        print("[FAIL] 两个输入都不存在，没有可汇总的数据")
        return 1

    rows = []          # (name, region, method, verdict, detail)
    if light:
        for s in light.get("sources", []):
            if s.get("skipped") and s.get("method") != "browser":
                rows.append((s["name"], s.get("region"), s.get("method"),
                             "未测", s.get("skip_reason") or "被跳过"))
                continue
            if s.get("method") == "browser":
                continue          # 浏览器源交给第二份报告，避免重复
            if s.get("error"):
                rows.append((s["name"], s.get("region"), s.get("method"),
                             "C 云端失败", s["error"]))
            elif (s.get("count") or 0) > 0:
                rows.append((s["name"], s.get("region"), s.get("method"),
                             "A 云端可抓", f"{s['count']} 条"))
            else:
                rows.append((s["name"], s.get("region"), s.get("method"),
                             "B 云端零产出", f"raw={s.get('raw')}"))
    if brow:
        for d in brow.get("detail", []):
            v = d.get("verdict")
            if v == "http_usable":
                # http 就能解析出内容 → 这个 browser 源其实可以降级成 http，云端秒抓
                rows.append((d["name"], d.get("region"), "browser",
                             "A 云端可抓", f"可降级 http，{d.get('count')} 条"))
            elif v == "http_reachable_but_0":
                rows.append((d["name"], d.get("region"), "browser",
                             "D 需浏览器", f"http 通了（{d.get('bytes')}B）但 0 条，"
                                            f"需真浏览器渲染"))
            else:
                rows.append((d["name"], d.get("region"), "browser",
                             "C 云端失败", (d.get("error") or "http 不可达")[:120]))

    groups = defaultdict(list)
    for r in rows:
        groups[r[3]].append(r)

    order = ["A 云端可抓", "B 云端零产出", "C 云端失败", "D 需浏览器", "未测"]
    md = [f"# {label}可达性实测", ""]
    for p, tag in ((light, "轻量源"), (brow, "浏览器源")):
        if p:
            md.append(f"- {tag}实测时间：{p.get('run_at')}")
    md += ["", "## 一句话结论", ""]
    a = len(groups.get("A 云端可抓", []))
    c = len(groups.get("C 云端失败", []))
    d = len(groups.get("D 需浏览器", []))
    md.append(f"- **关机也能抓**：{a} 个源")
    md.append(f"- **必须开机补**：{c} 个源（连不上）")
    if d:
        md.append(f"- **要看浏览器**：{d} 个源（网络通，但要真浏览器渲染；"
                  f"云端装了 Chromium 的话还有机会）")

    for k in order:
        items = groups.get(k)
        if not items:
            continue
        md += ["", f"## {k}（{len(items)} 个）", ""]
        if k.startswith("A"):
            md.append("| 源 | 地区 | 抓法 | 产出 |")
            md.append("|---|---|---|---|")
            for n, r, m, _, det in sorted(items, key=lambda x: x[0]):
                md.append(f"| {n} | {r} | {m} | {det} |")
        else:
            md.append("| 源 | 地区 | 抓法 | 说明 |")
            md.append("|---|---|---|---|")
            for n, r, m, _, det in sorted(items, key=lambda x: x[0]):
                md.append(f"| {n} | {r} | {m} | {str(det).replace('|', '/')} |")

    # 按省份给一张密度表：一眼看出哪些地区整体只能靠本地
    md += ["", "## 按地区分布", "", "| 地区 | 可抓 | 零产出 | 失败 | 需浏览器 |", "|---|---|---|---|---|"]
    per = defaultdict(Counter)
    for n, r, m, v, _ in rows:
        per[r or "（未标）"][v] += 1
    for r in sorted(per):
        c2 = per[r]
        md.append(f"| {r} | {c2.get('A 云端可抓',0)} | {c2.get('B 云端零产出',0)} | "
                  f"{c2.get('C 云端失败',0)} | {c2.get('D 需浏览器',0)} |")

    if out_p:
        os.makedirs(os.path.dirname(os.path.abspath(out_p)), exist_ok=True)
        with open(out_p, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(md) + "\n")
        print(f"[报告] 已写入 {out_p}（{len(rows)} 个源）")
    print("\n".join(md[:40]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
