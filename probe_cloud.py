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
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scraper  # noqa: E402


def main():
    print("=" * 64)
    print("云端可达性探针（海外 IP / 全量源 / 不写状态 / 不提交）")
    print("=" * 64)

    report = scraper.run_all(write_status=False)
    all_src = report.get("sources", [])
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
    md.append(f"- 实际尝试 **{len(ran)}** 个源，跳过 {len(skipped)} 个")
    md.append(f"- 成功 **{len(ok)}**（有产出 {len(has_data)} / 零产出 {len(zero)}）")
    md.append(f"- 失败 **{len(failed)}**")
    if failed:
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
