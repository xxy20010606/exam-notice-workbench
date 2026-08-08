#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日公告汇总邮件：从 notices.db 筛选「当天新增」公告，发一封带日期的汇总邮件。

独立于抓取流程，由 GitHub Actions 每日定时（北京时间约 23:55）运行。
「当天新增」判定与看板一致：公告 date==今天 或 first_seen(入库时间)==今天。

环境变量：
  SMTP_PASSWORD  163 邮箱「授权码」（非登录密码），必填才发邮件
  RECIPIENT      收件人，默认与发件人相同（发给自己）
  PAGES_URL      看板链接，写入邮件正文（可选）
  DAILY_ALWAYS   设为 1 时，即使当天无新增也发一封「无新增」提示邮件
"""
import os, sqlite3, smtplib, ssl
from email.message import EmailMessage
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(ROOT, "notices.db")
SMTP_HOST = "smtp.163.com"
SMTP_PORT = 465
SENDER = "xxy1037550012@163.com"


def beijing_today():
    """返回北京时间（UTC+8）的日期字符串 YYYY-MM-DD。"""
    bj = timezone(timedelta(hours=8))
    return datetime.now(bj).strftime("%Y-%m-%d")


def fetch_today_notices(today):
    conn = sqlite3.connect(DB)
    rows = conn.execute(
        "SELECT category, region, title, url, date FROM notices "
        "WHERE date=? OR first_seen LIKE ?",
        (today, today + "%"),
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        out.append({"category": r[0] or "", "region": r[1] or "",
                    "title": r[2] or "", "url": r[3] or "", "date": r[4] or ""})
    # 按 (title, url) 去重（date 与 first_seen 同时命中会重复）
    seen, uniq = set(), []
    for it in out:
        key = (it["title"], it["url"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(it)
    return uniq


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
    today = beijing_today()
    notices = fetch_today_notices(today)
    recipient = (os.environ.get("RECIPIENT", SENDER) or SENDER).strip() or SENDER
    pages = (os.environ.get("PAGES_URL", "") or "").strip()
    always = os.environ.get("DAILY_ALWAYS", "").strip() == "1"

    if not notices:
        if always:
            body = f"{today} 公考/事考公告日报\n\n今日暂无新增公告。\n"
            if pages:
                body += f"\n查看完整看板：{pages}"
            send_email(recipient, f"公考事考公告日报 {today}（无新增）", body)
        else:
            print(f"[日报] {today} 无新增公告，跳过发送（设 DAILY_ALWAYS=1 可强制发送提示邮件）")
        return

    # 按地区分组，地区内按标题排序
    by_region = {}
    for it in notices:
        by_region.setdefault(it["region"] or "其他", []).append(it)
    for k in by_region:
        by_region[k].sort(key=lambda x: x["title"])

    lines = [f"{today} 公考/事考公告日报", f"今日新增 {len(notices)} 条：", ""]
    for region in sorted(by_region):
        lines.append(f"【{region}】")
        for it in by_region[region]:
            lines.append(f"  · [{it['category']}] {it['title']}")
            if it["url"]:
                lines.append(f"    {it['url']}")
        lines.append("")
    if pages:
        lines.append(f"查看完整看板：{pages}")

    send_email(recipient, f"公考事考公告日报 {today}（新增 {len(notices)} 条）", "\n".join(lines))


if __name__ == "__main__":
    main()
