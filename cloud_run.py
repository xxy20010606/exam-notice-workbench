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
    report = scraper.run_all(only_region=args.only_region, exclude_region=args.exclude_region)
    # 清理历史噪声记录（入库过滤上线前的 junk），保证看板与数据库干净
    scraper.cleanup_noise()
    # 若本次新增了国考/福建省考招录公告，自动从详情页校准倒计时日期
    exam_sync.sync(report["new"])
    n = build_dashboard.build()
    report["dashboard_notices"] = n
    report["new_count"] = len(report["new"])
    # 精简摘要打印
    print(json.dumps({
        "run_at": report["run_at"],
        "dashboard_notices": n,
        "new_count": len(report["new"]),
        "new": report["new"],
    }, ensure_ascii=False, indent=2))

    # 把本次新增数量写入 .new_count，供 CI 判断是否提交
    # （无新数据时不提交/不推送，从根本上避免 Runner 自循环）
    try:
        with open(os.path.join(ROOT, ".new_count"), "w", encoding="utf-8") as _f:
            _f.write(str(len(report["new"])))
    except Exception as _e:
        print(f"[warn] 写入 .new_count 失败: {_e}")

    # 即时邮件已移除：改由每日汇总（daily_digest.py / daily_digest.yml）统一发送，
    # 保证「每天一封」而非每次抓取一封。send_email 函数保留供 daily_digest 复用。


if __name__ == "__main__":
    main()
