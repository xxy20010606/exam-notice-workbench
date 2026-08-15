#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日公告汇总：从 notices.db 筛选「当天新增」公告，发一封带日期的汇总（邮件 + 微信）。

独立于抓取流程，由 GitHub Actions 每日定时（北京时间 19:00）运行。
「当天新增」判定与看板一致：公告 date==今天 或 first_seen(入库时间)==今天。

环境变量：
  SMTP_PASSWORD   163 邮箱「授权码」（非登录密码），配置后才发邮件
  RECIPIENT       收件人，默认与发件人相同（发给自己）
  PAGES_URL       看板链接，写入正文（可选）
  SERVERCHAN_KEY  Server酱 SendKey，配置后才推微信（sctp… 为 Server酱³，SCT… 为旧版 Turbo）

邮件与微信互相独立：配了哪个就发哪个，都配就都发，都不配则只打印日志。
默认每天发送：有新增发汇总，无新增发「今日暂无新增」提示。
覆盖 sources.json 中全部地区（国考 / 各省省考 / 浙江11市事业编 / 福建·江苏·上海等事业编）。
"""
import os, re, json, sqlite3, smtplib, ssl
import urllib.request, urllib.parse
from email.message import EmailMessage
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(ROOT, "notices.db")
SMTP_HOST = "smtp.163.com"
SMTP_PORT = 465
SENDER = "xxy1037550012@163.com"
# 微信单条消息里最多列多少条公告，超出只给数量与看板链接（避免消息被平台截断）
WX_MAX_ITEMS = 40

# 全局噪声关键词：标题含这些词的条目不是真正的招聘公告（政府采购/中标/询价/导航栏文字/泛化栏目名）
NOISE_KEYWORDS = re.compile(
    r"采购|中标|询价|成交|招标|竞价|政府采购|单一来源|资格预审.*采购"
    r"|^/\s"  # 导航栏文字（以 / 开头）
    r"|^事业单位公开招聘$"  # 泛化栏目名（非具体公告）
    r"|^公开招聘$"  # 泛化栏目名
)

# 白名单：标题必须含以下招聘类关键词才视为有效公告。
# 与上方黑名单叠加，从根本上挡住「未知类别」噪声（黑名单只拦已知类别）。
RECRUIT_KEYWORDS = re.compile(
    r"公务员|省考|选调|遴选|事业(单位|编)?|招聘|招考|招录|公考|引进"
)


def fetch_source_health():
    """读取 sources_status，返回今日抓取异常（失败/被跳过）的源。"""
    if not os.path.exists(DB):
        return []
    conn = sqlite3.connect(DB)
    try:
        rows = conn.execute(
            "SELECT name, fail_streak, last_error, last_run FROM sources_status "
            "WHERE fail_streak > 0 OR (last_error IS NOT NULL AND last_error != '')"
        ).fetchall()
    except sqlite3.OperationalError:
        # 表尚未创建（如云端首次抓取前）：跳过健康提醒，不影响日报发送
        return []
    finally:
        conn.close()
    out = []
    for r in rows:
        out.append({"name": r[0] or "", "fail_streak": r[1] or 0,
                    "last_error": (r[2] or "")[:120], "last_run": r[3] or ""})
    return out


def format_health(health):
    """返回 (text_lines, md_lines)，供邮件/微信正文追加源健康提醒。"""
    if not health:
        return [], []
    text = ["【源健康提醒】以下源今日抓取异常，可能漏收公告："]
    md = ["**源健康提醒**：以下源今日抓取异常，可能漏收公告："]
    for h in health:
        reason = h["last_error"] or (f"连续失败 {h['fail_streak']} 次" if h["fail_streak"] else "未知错误")
        text.append(f"  · {h['name']}：{reason}")
        md.append(f"- {h['name']}：{reason}")
    text.append("")
    md.append("")
    return text, md


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


def send_wechat(title, desp):
    """通过 Server酱推送到微信。

    Server酱³（SendKey 形如 sctp1234t****）：POST https://<uid>.push.ft07.com/send/<key>.send
    旧版 Turbo（SendKey 形如 SCT****）：      POST https://sctapi.ftqq.com/<key>.send
    desp 支持 Markdown。
    """
    key = (os.environ.get("SERVERCHAN_KEY", "") or "").strip()
    if not key:
        print("[微信] 未设置 SERVERCHAN_KEY，跳过推送")
        return False

    m = re.match(r"^sctp(\d+)t", key)
    if m:                                   # Server酱³
        url = f"https://{m.group(1)}.push.ft07.com/send/{key}.send"
    else:                                   # 旧版 Turbo
        url = f"https://sctapi.ftqq.com/{key}.send"

    # 微信标题过长会显示不全，截断保护
    payload = urllib.parse.urlencode(
        {"title": title[:60], "desp": desp}
    ).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8", "ignore")
        try:
            data = json.loads(raw)
        except Exception:
            data = {}
        code = data.get("code", data.get("data", {}).get("code"))
        if code in (0, None) and '"code":4' not in raw:
            print("[微信] 已推送")
            return True
        print(f"[微信] 推送返回异常：{raw[:200]}")
        return False
    except Exception as e:
        print(f"[微信] 推送失败：{e}")
        return False


def build_wechat_markdown(today, notices, by_region, pages, health_md=None):
    """微信正文（Markdown），条数过多时只列前 WX_MAX_ITEMS 条。"""
    lines = [f"**{today} 新增 {len(notices)} 条**", ""]
    shown = 0
    truncated = False
    for region in sorted(by_region):
        if shown >= WX_MAX_ITEMS:
            truncated = True
            break
        lines.append(f"**{region}**")
        for it in by_region[region]:
            if shown >= WX_MAX_ITEMS:
                truncated = True
                break
            if it["url"]:
                lines.append(f"- [{it['title']}]({it['url']})")
            else:
                lines.append(f"- {it['title']}")
            shown += 1
        lines.append("")
    if truncated:
        lines.append(f"> 还有 {len(notices) - shown} 条未列出，请查看完整看板。")
        lines.append("")
    if pages:
        lines.append(f"[查看完整看板]({pages})")
    if health_md:
        lines.extend(health_md)
    return "\n".join(lines)


def main():
    today = beijing_today()
    notices = fetch_today_notices(today)
    recipient = (os.environ.get("RECIPIENT", SENDER) or SENDER).strip() or SENDER
    pages = (os.environ.get("PAGES_URL", "") or "").strip()
    health = fetch_source_health()
    htext, hmd = format_health(health)

    if not notices:
        print(f"[日报] {today} 无新增公告，发送提示")
        body = f"{today} 公考/事考公告日报\n\n今日暂无新增公告。\n"
        if pages:
            body += f"\n查看完整看板：{pages}"
        if htext:
            body += "\n" + "\n".join(htext)
        send_email(recipient, f"公考事考公告日报 {today}（无新增）", body)
        wx = f"**{today}**\n\n今日暂无新增公告。\n"
        if pages:
            wx += f"\n[查看完整看板]({pages})"
        if hmd:
            wx += "\n" + "\n".join(hmd)
        send_wechat(f"公考事考日报 {today}（无新增）", wx)
        return

    # 全局噪声过滤：剔除采购/中标/询价等非招聘公告
    real = []
    noise = []
    for it in notices:
        if NOISE_KEYWORDS.search(it["title"]):
            noise.append(it)
        else:
            real.append(it)
    if noise:
        print(f"[日报] 过滤掉 {len(noise)} 条噪声（采购/中标/询价等）")
    notices = real

    # 再白名单把关：只保留含招聘类关键词的标题，挡住未知噪声
    kept = []
    for it in real:
        if RECRUIT_KEYWORDS.search(it["title"]):
            kept.append(it)
        else:
            noise.append(it)
    if noise:
        print(f"[日报] 过滤掉 {len(noise)} 条噪声（采购/中标/询价/非招聘等）")
    notices = kept

    if not notices:
        print(f"[日报] {today} 新增 {len(real) + len(noise)} 条但全部为噪声，发送提示")
        body = f"{today} 公考/事考公告日报\n\n今日新增 {len(noise)} 条信息，但均为政府采购/中标/询价等非招聘公告，已自动过滤。\n"
        if pages:
            body += f"\n查看完整看板：{pages}"
        if htext:
            body += "\n" + "\n".join(htext)
        send_email(recipient, f"公考事考日报 {today}（无有效招聘）", body)
        wx = f"**{today}**\n\n今日新增 {len(noise)} 条，但均为采购/中标等非招聘信息，已过滤。\n"
        if pages:
            wx += f"\n[查看完整看板]({pages})"
        if hmd:
            wx += "\n" + "\n".join(hmd)
        send_wechat(f"公考事考日报 {today}（无有效招聘）", wx)
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
    if htext:
        lines.extend(htext)
    if pages:
        lines.append(f"查看完整看板：{pages}")

    send_email(recipient, f"公考事考公告日报 {today}（新增 {len(notices)} 条）", "\n".join(lines))
    send_wechat(
        f"公考事考日报 {today}｜新增 {len(notices)} 条",
        build_wechat_markdown(today, notices, by_region, pages, hmd),
    )


if __name__ == "__main__":
    main()
