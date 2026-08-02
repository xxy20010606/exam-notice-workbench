#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
编排入口：抓取全部数据源 -> 更新 SQLite -> 生成看板。
输出本次新增公告的 JSON（供自动化推送使用），并写入 last_run.json。
"""
import json, os, sys
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import scraper, build_dashboard


def main():
    report = scraper.run_all()
    n = build_dashboard.build()
    report["dashboard_notices"] = n
    report["new_count"] = len(report["new"])
    with open(os.path.join(ROOT, "last_run.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    # 给自动化推送看的精简摘要
    summary = {
        "run_at": report["run_at"],
        "dashboard_notices": n,
        "new_count": len(report["new"]),
        "new": report["new"],
        "source_status": report["sources"],
    }
    print(json.dumps(summary, ensure_ascii=False))
    return report


if __name__ == "__main__":
    main()
