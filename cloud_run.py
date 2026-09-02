#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
云端版编排入口（用于 GitHub Actions / 任意能跑 Python 的云环境）：
  1. 抓取全部数据源 -> 更新 SQLite(notices.db)
  2. 重建静态看板 index.html
  3. 若本次有新增公告，且设置了 SMTP_PASSWORD，则用 163 邮箱直发摘要邮件

与本地 run.py 的区别：邮件走 SMTP(163) 而非 agent-mail（云端无客户端）。
环境变量：
  SMTP_PASSWORD  163 邮箱“授权码”（非登录密码），必填才发邮件
  RECIPIENT      收件人，默认与发件人相同（发给自己）
  PAGES_URL      看板链接，写入邮件正文（可选）
"""
import os, sys, json, smtplib, ssl
from email.message import EmailMessage
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import scraper, build_dashboard, exam_sync

# 代理状态提示：配置了 SCRAPE_PROXY 则抓取走国内代理（直连失败自动回退），否则纯直连
_PROXY = os.environ.get("SCRAPE_PROXY", "").strip()
print(f"[代理] {'已启用国内代理（直连失败自动回退）' if _PROXY else '未配置，纯直连（当前行为）'}")

# === 临时探测：福建统一平台 API 在 GitHub 云端连通性 ===
# （非业务代码，仅打印日志观察。结果不影响抓取流程，跑完即弃。）
import urllib.request
try:
    req = urllib.request.Request(
        "http://220.160.53.33:8903/ksbm/student/home/newsList?userId=&year=&orderBy=1&pageNum=1&pageSize=3&flag=2&isProject=1",
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
    )
    r = urllib.request.urlopen(req, timeout=20)
    body = r.read().decode("utf-8", "ignore")
    import json as _json
    j = _json.loads(body)
    data = j.get("data", {})
    rows = data.get("rows", []) if isinstance(data, dict) else []
    total = data.get("total") if isinstance(data, dict) else len(rows)
    print(f"[PROBE-福建API] ✅ 通！HTTP {r.status} 本页{len(rows)}条 total={total}")
    for row in rows[:2]:
        print(f"   - {str(row.get('proTitle') or row.get('title'))[:50]}")
except Exception as _e:
    print(f"[PROBE-福建API] ❌ 不通：{type(_e).__name__}: {str(_e)[:200]}")

SMTP_HOST = "smtp.163.com"
SMTP_PORT = 465
SENDER = "xxy1037550012@163.com"


def send_email(recipient, subject, body):
    pwd = os.environ.get("SMTP_PASSWORD")
    if not pwd:
        print("[邮件] 未设置 SMTP_PASSWORD，跳过发送")
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = SENDER
    msg["To"] = recipient
    msg.set_content(body)
    ctx = ssl.create_default_context()
    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx) as s:
            s.login(SENDER, pwd)
            s.send_message(msg)
        print(f"[邮件] 已发送至 {recipient}")
        return True
    except Exception as e:
        print(f"[邮件] 发送失败：{e}")
        return False


def main():
    import argparse
    ap = argparse.ArgumentParser(description="云端编排入口")
    ap.add_argument("--only-region", help="只抓取 region 包含该关键字的源（如 浙江）")
    ap.add_argument("--exclude-region", help="跳过 region 包含该关键字的源（如 浙江）")
    args = ap.parse_args()
    # 各步骤独立 try-catch：单步异常不致命，确保数据质量改动（清噪声/回填）无论抓取到否都能落库，
    # 避免「抓取偶发失败 → 整个 job 崩 → .has_change 不写 → 改动被锁死」的死循环。
    try:
        report = scraper.run_all(only_region=args.only_region, exclude_region=args.exclude_region)
    except Exception as _e:
        print(f"[warn] run_all 异常（仍继续 cleanup/回填）: {_e}")
        report = {"new": [], "run_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "sources": []}
    try:
        n_clean = scraper.cleanup_noise()
    except Exception as _e:
        print(f"[warn] cleanup_noise 异常: {_e}"); n_clean = 0
    try:
        n_back = scraper.date_backfill()
    except Exception as _e:
        print(f"[warn] date_backfill 异常: {_e}"); n_back = 0
    try:
        exam_sync.sync(report.get("new", []))
    except Exception as _e:
        print(f"[warn] exam_sync 异常: {_e}")
    try:
        n = build_dashboard.build()
        report["dashboard_notices"] = n
    except Exception as _e:
        print(f"[warn] build_dashboard 异常: {_e}"); n = 0
    new_count = len(report.get("new", []))
    report["new_count"] = new_count
    # 精简摘要打印
    print(json.dumps({
        "run_at": report["run_at"],
        "dashboard_notices": n,
        "new_count": new_count,
        "cleaned": n_clean,
        "date_backfilled": n_back,
        "new": report["new"],
    }, ensure_ascii=False, indent=2))

    # 写 .new_count：本次新增公告数（兼容旧逻辑 / 日志查看）
    try:
        with open(os.path.join(ROOT, ".new_count"), "w", encoding="utf-8") as _f:
            _f.write(str(new_count))
    except Exception as _e:
        print(f"[warn] 写入 .new_count 失败: {_e}")

    # 写 .has_change：任何数据变化都需推送（新增 / 清噪声 / 回填日期）。
    # 不再以「仅新增」为唯一推送条件——否则 cleanup / date 修复等数据质量改动
    # 会被「无新公告不推送」锁死、永远无法落库。
    # 自循环防护仍由 commit message 含「定时更新公告」+ workflow 防重入保证。
    has_change = 1 if (new_count > 0 or n_clean > 0 or n_back > 0) else 0
    try:
        with open(os.path.join(ROOT, ".has_change"), "w", encoding="utf-8") as _f:
            _f.write(str(has_change))
    except Exception as _e:
        print(f"[warn] 写入 .has_change 失败: {_e}")

    # 即时邮件已移除：改由每日汇总（daily_digest.py / daily_digest.yml）统一发送，
    # 保证「每天一封」而非每次抓取一封。send_email 函数保留供 daily_digest 复用。


if __name__ == "__main__":
    main()
