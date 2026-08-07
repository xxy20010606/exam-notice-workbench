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

    new = report["new"]
    recipient = os.environ.get("RECIPIENT", SENDER).strip() or SENDER
    if new:
        pages = os.environ.get("PAGES_URL", "").strip()
        lines = [f"本次新增 {len(new)} 条招考公告（{report['run_at']}）：", ""]
        for it in new:
            lines.append(f"【{it.get('category','')}·{it.get('region','')}】{it.get('title','')}")
            lines.append(it.get("url", ""))
            lines.append("")
        if pages:
            lines.append(f"查看完整看板：{pages}")
        send_email(recipient, f"公考/事考公告更新：新增 {len(new)} 条", "\n".join(lines))
    else:
        print("[邮件] 无新增公告，不发送")


if __name__ == "__main__":
    main()
