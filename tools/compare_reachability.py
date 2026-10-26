#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把「云端普查」和「本机对照」并起来，回答一句话问题：
哪些源电脑不开机也有，哪些必须开机。

两张表的口径要对齐才可比：
  · 轻量源：两份都是「真抓 + 真解析」，直接比 count；
  · 浏览器源：两边都只做了「纯 http 直取」，比的是**网络可达性**，
    不涉及 Chromium 能不能渲染。所以浏览器源单独归一组，不混进主结论。
"""
import json
import os
import sys
from collections import defaultdict


def _load(p):
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def main():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    q = lambda *a: os.path.join(here, *a)

    cl = _load(q("_probe", "cloud", "cloud_reachability.json"))
    cb = _load(q("_probe", "cloud", "cloud_reachability_browser.json"))
    ll = _load(q("_probe", "local_light.json"))
    lb = _load(q("_probe", "local_brow.json"))
    if not (cl and ll):
        print("[FAIL] 缺少云端或本地 JSON")
        return 1

    def light_verdict(rep):
        """名字 → (是否成功, 条数, 错误)"""
        out = {}
        for s in rep.get("sources", []):
            if s.get("method") == "browser":
                continue
            if s.get("error"):
                out[s["name"]] = ("fail", 0, s["error"])
            elif (s.get("count") or 0) > 0:
                out[s["name"]] = ("ok", s["count"], "")
            else:
                out[s["name"]] = ("zero", 0, f"raw={s.get('raw')}")
        return out

    def brow_verdict(rep):
        out = {}
        for d in rep.get("detail", []):
            out[d["name"]] = (d.get("verdict"), d.get("count") or 0,
                              (d.get("error") or "")[:90])
        return out

    C, L = light_verdict(cl), light_verdict(ll)
    CBv, LBv = brow_verdict(cb or {}), brow_verdict(lb or {})

    only_cloud, only_local, both_ok, both_bad = [], [], [], []
    for name in sorted(set(C) & set(L)):
        cs, cc, ce = C[name]
        ls, lc, le = L[name]
        if cs == "ok" and ls == "ok":
            both_ok.append((name, cc, lc))
        elif ls == "ok":
            only_local.append((name, lc, ce or cs))
        elif cs == "ok":
            only_cloud.append((name, cc, le or ls))
        else:
            both_bad.append((name, ce or ls, le or cs))

    # 浏览器源：比 http 可达性
    b_local_only, b_both, b_none = [], [], []
    for name in sorted(set(CBv) | set(LBv)):
        cv = CBv.get(name, ("(云端未测)", 0, ""))[0]
        lv = LBv.get(name, ("(本地未测)", 0, ""))[0]
        if cv == "http_unreachable" and lv != "http_unreachable":
            b_local_only.append((name, lv, CBv.get(name, ("", 0, ""))[2]))
        elif cv != "http_unreachable":
            b_both.append((name, cv, lv))
        elif lv == "http_unreachable":
            b_none.append((name, CBv.get(name, ("", 0, ""))[2]))

    md = []
    md.append("# 哪些源「关机也有」、哪些「必须开机」")
    md.append("")
    md.append(f"- 对照基础：云端（GitHub Actions 海外出口）{cl.get('run_at')} 实测 "
              f"{len(C)} 个轻量源；本机（中国大陆出口）{ll.get('run_at')} 实测 {len(L)} 个。")
    md.append(f"- 结论汇总：轻量源中 **两边都抓得到 {len(both_ok)} 个**、"
              f"**只有本机能抓 {len(only_local)} 个**、"
              f"两边都抓不到 {len(both_bad)} 个。")
    md.append("")
    md.append("## A. 关机也有（云端实测有产出）")
    md.append("")
    md.append("| 源 | 云端条数 | 本机条数 |")
    md.append("|---|---|---|")
    for n, c, l in both_ok:
        md.append(f"| {n} | {c} | {l} |")
    for n, c, _ in only_cloud:
        md.append(f"| {n} | {c} | 本机 0（站点对国内反向异常）|")

    md.append("")
    md.append(f"## B. 必须开机（云端抓不到，本机正常）—— {len(only_local)} 个")
    md.append("")
    md.append("| 源 | 本机条数 | 云端报错 |")
    md.append("|---|---|---|")
    for n, l, e in only_local:
        md.append(f"| {n} | {l} | {str(e).replace('|','/')[:70]} |")

    md.append("")
    md.append(f"## C. 两边都抓不到（源本身的问题，不是 IP）—— {len(both_bad)} 个")
    md.append("")
    md.append("| 源 | 云端 | 本机 |")
    md.append("|---|---|---|")
    for n, ce, le in both_bad:
        md.append(f"| {n} | {str(ce).replace('|','/')[:50]} | {str(le).replace('|','/')[:50]} |")

    md.append("")
    md.append("## D. 浏览器源（比的是网络可达性，不是渲染能力）")
    md.append("")
    md.append(f"- 云端 http 不通、本机 http 通 → **必须开机**：{len(b_local_only)} 个")
    for n, lv, e in b_local_only:
        md.append(f"    - {n}（本机 {lv}；云端 {str(e)[:50]}）")
    md.append(f"- 两边 http 都通（云端装了 Chromium 才有机会）：{len(b_both)} 个")
    for n, cv, lv in b_both:
        md.append(f"    - {n}（云端 {cv} / 本机 {lv}）")
    if b_none:
        md.append(f"- 两边 http 都不通：{len(b_none)} 个")
        for n, e in b_none:
            md.append(f"    - {n}（{str(e)[:50]}）")

    out = q("reports", "关机也有-vs-必须开机.md")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(md) + "\n")
    print("\n".join(md))
    print(f"\n[报告] 已写入 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
