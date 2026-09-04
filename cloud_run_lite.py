#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
国内轻量抓取入口（WorkBuddy 云端自动化专用，出口为国内 IP）：
  1. 抓取全部「非 browser」源（http / jsptsearch / api / row_table 等，无需 playwright）
     —— browser 源自动跳过（标记 skipped，不计失败），由 GHA 海外通道继续负责
  2. cleanup_noise + date_backfill 数据质量维护
  3. build_dashboard 重建静态看板
  4. 写 .has_change / .new_count（与 cloud_run.py 同约定，供推送判断）

与 cloud_run.py 的区别：不装 playwright 也能跑；不发邮件（日报仍由 GHA daily_digest 统一发）。
"""
import os, sys, json
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import scraper, build_dashboard


def _playwright_available():
    """自动检测：playwright 可用则 browser 源也走国内通道（实测国内 IP 对
    rsj.lyg/hrss.zhenjiang 等可直接访问），不可用则自动跳过（仅跑轻量源）。"""
    try:
        import playwright  # noqa: F401
        return True
    except Exception:
        return False


def main():
    skip_browser = not _playwright_available()
    print(f"[lite] playwright {'可用，browser 源纳入国内抓取' if not skip_browser else '不可用，跳过 browser 源'}")
    try:
        report = scraper.run_all(skip_browser=skip_browser)
    except Exception as _e:
        print(f"[warn] run_all 异常（仍继续 cleanup/回填）: {_e}")
        report = {"new": [], "run_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "sources": []}
    try:
        n_clean = scraper.cleanup_noise()
    except Exception as _e:
        print(f"[warn] cleanup_noise 异常: {_e}"); n_clean = 0
    try:
        n_back = scraper.date_backfill()
    except Exception as _e:
        print(f"[warn] date_backfill 异常: {_e}"); n_back = 0
    try:
        n = build_dashboard.build()
    except Exception as _e:
        print(f"[warn] build_dashboard 异常: {_e}"); n = 0

    new_count = len(report.get("new", []))
    has_change = 1 if (new_count > 0 or n_clean > 0 or n_back > 0) else 0
    n_skip = sum(1 for s in report.get("sources", []) if s.get("skipped"))
    n_err = sum(1 for s in report.get("sources", []) if s.get("error"))
    print(json.dumps({
        "run_at": report["run_at"],
        "dashboard_notices": n,
        "new_count": new_count,
        "cleaned": n_clean,
        "date_backfilled": n_back,
        "browser_skipped": n_skip,
        "source_errors": n_err,
        "has_change": has_change,
        "new": report.get("new", []),
    }, ensure_ascii=False, indent=2))

    for fname, val in ((".new_count", str(new_count)), (".has_change", str(has_change))):
        try:
            with open(os.path.join(ROOT, fname), "w", encoding="utf-8") as f:
                f.write(val)
        except Exception as _e:
            print(f"[warn] 写入 {fname} 失败: {_e}")


if __name__ == "__main__":
    main()
