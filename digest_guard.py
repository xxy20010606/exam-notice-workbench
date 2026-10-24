#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""日报缺席哨兵（digest guard）

## 为什么需要它

日报靠 `workflow_run` 链式触发——抓取 workflow 跑完，才发日报。
这条链有个结构性盲区：**抓取 workflow 自己挂了 → 日报永远不会被触发 →
而没有任何人知道**（GitHub 默认的失败邮件尤其容易被埋，且一天 6 次抓取里
只挂几次不会发邮件，因为整个 run 还是 success）。

它要覆盖的两个盲区：
  ① 日报自己挂了（SMTP / Server酱 认证失效、脚本报错）
  ② 抓取完全没跑成功 → 日报连触发机会都没有

## 为什么用 tag 判断，而不是查 workflow run

`digest-YYYY-MM-DD` tag 只在「日报发送成功 **且** workflow success」之后才
push（见 daily_digest.yml 最后一步），是「用户真的收到了」的唯一可信凭据。

查 run 的 `conclusion=success` 只能说明脚本没崩，不说明邮件/微信真发出去了
（send_email / send_wechat 内部遇到未配置的凭据是 print 后 return False，
不会让 run 失败）。所以 tag 缺失才是真信号。

## 幂等

同一天只告警一次，用 `guard-alert-YYYY-MM-DD` tag 做标记。
guard tag 超过 14 天自动清理。

用法：
    python digest_guard.py            # 正常检查
    python digest_guard.py --dry-run  # 只诊断不推送
"""
import os
import sys
import json
import sqlite3
import subprocess
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(ROOT, "notices.db")

BJ = timezone(timedelta(hours=8))

# 需要盯的 workflow（文件名 → 中文名）
WATCH = [("scrape.yml", "抓取"), ("daily_digest.yml", "日报")]


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------
def beijing_today():
    return datetime.now(BJ).date()


def _token():
    return (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or "").strip()


def _repo():
    return (os.environ.get("GITHUB_REPOSITORY") or "").strip()


def _gh(method, path, timeout=20):
    """调 GitHub REST API。

    返回 (status_code, data)。status==0 表示配置缺失或网络异常，**无法判断**，
    调用方必须把它和「真的查到了」区分开，否则会误报或漏报。
    """
    tok, repo = _token(), _repo()
    if not tok or not repo:
        return 0, None
    url = f"https://api.github.com/repos/{repo}{path}"
    req = urllib.request.Request(
        url,
        method=method,
        headers={
            "Authorization": f"Bearer {tok}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "digest-guard",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "ignore")
            return r.status, (json.loads(raw) if raw.strip() else None)
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception:
        return 0, None


def tag_exists(tag):
    """True / False / None（None = 无法判断）。

    先走 API（稳），再退回本地 git ls-remote（Actions checkout 后带凭据）。
    两者都不可用时返回 None —— 哨兵宁可不动，也不能凭猜报假警。
    """
    if _token() and _repo():
        code, _ = _gh("GET", f"/git/ref/tags/{tag}")
        if code == 200:
            return True
        if code == 404:
            return False
        # 401/403/网络错误 → 落到 git 兜底
    try:
        p = subprocess.run(
            ["git", "ls-remote", "--tags", "origin", f"refs/tags/{tag}"],
            capture_output=True, text=True, timeout=40, cwd=ROOT,
        )
        if p.returncode == 0:
            return tag in (p.stdout or "")
    except Exception:
        pass
    return None


def _bj(iso):
    """ISO(UTC) → 北京 'MM-DD HH:MM'。"""
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return dt.astimezone(BJ).strftime("%m-%d %H:%M")
    except Exception:
        return str(iso)[:16]


# --------------------------------------------------------------------------
# 诊断信息
# --------------------------------------------------------------------------
def collect_db_diagnosis():
    """从 notices.db 挖：库规模、最近一轮抓取时间、连续失败源。"""
    lines = []
    if not os.path.exists(DB):
        return ["· **notices.db 不存在** —— 仓库 checkout 有问题"]
    try:
        c = sqlite3.connect(DB)
        n = c.execute("SELECT COUNT(*) FROM notices").fetchone()[0]
        lines.append(f"· 库内公告 **{n}** 条")

        row = c.execute("SELECT MAX(last_run) FROM sources_status").fetchone()
        if row and row[0]:
            lines.append(f"· 最近一轮抓取 **{row[0]}**（北京时间）")
        else:
            lines.append("· **没有任何抓取记录** —— sources_status 是空的")

        bad = c.execute(
            "SELECT name, fail_streak, IFNULL(last_error,'') FROM sources_status "
            "WHERE fail_streak >= 2 ORDER BY fail_streak DESC, name LIMIT 8"
        ).fetchall()
        if bad:
            lines.append(f"· 连续失败源 **{len(bad)}** 个：")
            for nm, fs, er in bad:
                er = (er or "").strip().splitlines()[0][:70] if er else "（无错误信息）"
                lines.append(f"    - {nm}（连续 {fs} 次）{er}")
        else:
            lines.append("· 无连续失败源")
        c.close()
    except Exception as e:
        lines.append(f"· 读库失败：{e}")
    return lines


def collect_run_diagnosis():
    """查两个 workflow 最近几次 run 的结论，定位是哪一环断的。"""
    if not (_token() and _repo()):
        return ["· （未配置 token，跳过 Actions 运行记录查询）"]
    lines = []
    for wf, label in WATCH:
        code, d = _gh("GET", f"/actions/workflows/{wf}/runs?per_page=5")
        if code != 200 or not isinstance(d, dict):
            lines.append(f"· {label}：查不到运行记录（HTTP {code}）")
            continue
        runs = d.get("workflow_runs") or []
        if not runs:
            lines.append(f"· {label}：**没有任何运行记录**")
            continue
        parts = []
        for r in runs[:3]:
            t = _bj(r.get("created_at") or "")
            st = r.get("conclusion") or r.get("status") or "?"
            parts.append(f"{t} `{st}`")
        lines.append(f"· {label} 最近 3 次：" + " ／ ".join(parts))
    return lines


# --------------------------------------------------------------------------
# tag 维护
# --------------------------------------------------------------------------
def mark_alert(tag):
    """打一个 guard tag 做幂等标记。失败不影响主流程（最多明天多推一次）。"""
    # 用 git 打 tag 而不是 API 建 ref（API 建 ref 还得拼 body 和 commit sha，没好处）
    try:
        env = dict(os.environ)
        env.setdefault("GIT_AUTHOR_NAME", "digest-guard")
        env.setdefault("GIT_AUTHOR_EMAIL", "digest-guard@users.noreply.github.com")
        env.setdefault("GIT_COMMITTER_NAME", "digest-guard")
        env.setdefault("GIT_COMMITTER_EMAIL", "digest-guard@users.noreply.github.com")
        subprocess.run(["git", "tag", "-f", tag], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=30)
        p = subprocess.run(["git", "push", "origin", f"refs/tags/{tag}"],
                           cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
        if p.returncode == 0:
            print(f"[哨兵] 已打告警标记 {tag}")
            return True
        print(f"[哨兵] 打标记失败：{(p.stderr or '').strip()[:200]}")
    except Exception as e:
        print(f"[哨兵] 打标记异常：{e}")
    return False


def cleanup_old_alert_tags(days=14):
    """清掉过期的 guard-alert tag，避免无限堆积。"""
    if not (_token() and _repo()):
        return
    code, d = _gh("GET", "/git/matching-refs/tags/guard-alert-")
    if code != 200 or not isinstance(d, list):
        return
    cutoff = beijing_today() - timedelta(days=days)
    removed = 0
    for ref in d:
        name = (ref.get("ref") or "").replace("refs/tags/", "")
        try:
            dt = datetime.strptime(name.replace("guard-alert-", ""), "%Y-%m-%d").date()
        except Exception:
            continue
        if dt < cutoff:
            c2, _ = _gh("DELETE", f"/git/refs/tags/{name}")
            if c2 in (204, 200):
                removed += 1
    if removed:
        print(f"[哨兵] 清理旧告警标记 {removed} 个")


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def build_message(today, tag):
    action_url = f"https://github.com/{_repo()}/actions" if _repo() else "（仓库地址未知）"
    lines = [
        f"**{today.isoformat()} 的日报没有发出去。**",
        "",
        f"判据：`{tag}` 这个 tag 不存在。日报只在「发送成功且 workflow success」之后",
        "才打 tag，所以缺失就等于：你今天没收到简报，而且系统没吱声。",
        "",
        "### 诊断",
        "",
    ]
    lines += collect_db_diagnosis()
    lines.append("")
    lines += collect_run_diagnosis()
    lines += [
        "",
        "### 可能原因（按概率排）",
        "",
        "1. **抓取 workflow 今天没跑成功** → 日报的 `workflow_run` 触发不了。",
        "   这是整条链最脆的一环：抓取挂了，日报连启动机会都没有。",
        "2. 日报 workflow 跑了，但 SMTP / Server酱 凭据失效（脚本内部 return False，",
        "   不会让 run 失败，所以 GitHub 那边看着是绿的）。",
        "3. cron 被 GitHub 排队延迟，北京 19:00 之后才跑 → 当天发送窗口已过。",
        "",
        f"查看运行记录：{action_url}",
        "",
        "---",
        "",
        "补发：Actions 里手动跑一次「每日公告汇总邮件」（workflow_dispatch）。",
    ]
    return "\n".join(lines)


def target_date(now=None):
    """该检查哪一天的日报。

    不能直接用"今天"——两个坑：
      · 哨兵排在北京 20:00，但 GitHub 的 schedule 经常排队延迟。
        如果延迟跨到次日凌晨才跑，`today` 已经翻页，会去查"明天"的 tag，
        而明天的日报根本还没到时间 → **必然误报**。
      · 有人在白天手动跑一次，也不该拿今天去报警。

    规则：北京时间 20:00 及以后查今天；之前（含跨天延迟）查昨天。
    20:00 是安全线——日报最晚 19:00 就该发出，留 1 小时给慢队列。
    """
    now = now or datetime.now(BJ)
    if now.hour >= 20:
        return now.date()
    return (now - timedelta(days=1)).date()


def main():
    dry = "--dry-run" in sys.argv
    today = target_date()
    # --date YYYY-MM-DD：手动指定要查哪天（排查历史 / 演练告警用）
    if "--date" in sys.argv:
        try:
            today = datetime.strptime(
                sys.argv[sys.argv.index("--date") + 1], "%Y-%m-%d"
            ).date()
        except Exception:
            print("[哨兵] --date 格式应为 YYYY-MM-DD", file=sys.stderr)
            return 2
    tag = f"digest-{today.isoformat()}"
    alert_tag = f"guard-alert-{today.isoformat()}"

    print(f"[哨兵] 北京时间 {datetime.now(BJ):%Y-%m-%d %H:%M}，检查 {tag}")

    ex = tag_exists(tag)
    if ex is None:
        # 既查不到 API 也跑不了 git —— 这时候报警必然是猜测，宁可不动
        print("[哨兵] 无法判断 tag 是否存在（无 token 且 git 不可用），为避免误报直接退出")
        return 0
    if ex:
        print(f"[哨兵] OK：今日日报已发出（{tag} 存在）")
        cleanup_old_alert_tags()
        return 0

    print(f"[哨兵] 告警：今日日报未发出（{tag} 不存在）")

    if tag_exists(alert_tag):
        print(f"[哨兵] 今日已告警过（{alert_tag} 存在），跳过避免刷屏")
        return 0

    body = build_message(today, tag)
    print("[哨兵] 诊断内容：")
    print(body)

    if dry:
        print("[哨兵] --dry-run，不推送")
        return 0

    import daily_digest  # 延迟导入，避免顶层副作用影响 --dry-run

    subject = f"⚠️ {today.isoformat()} 日报未发出"
    wx = daily_digest.send_wechat(subject, body)
    if wx is True:
        print("[哨兵] 微信告警已发出")
    else:
        # 微信推不出去 → 走邮件兜底。
        # 这不是多余：Server酱 的 key 会过期（本次就实测到一个已失效的，
        # 返回 40001 错误的Key），如果哨兵只有微信一条腿，key 一过期哨兵就哑了，
        # 而且哑得毫无声息——比没有监控更糟。邮件和微信用完全不同的凭据，互为备份。
        print(f"[哨兵] 微信通道不可用（返回值 {wx}），改用邮件兜底")
        recipient = (os.environ.get("RECIPIENT") or daily_digest.SENDER or "").strip()
        mail = daily_digest.send_email(recipient, subject, body)
        if mail is not True:
            # 两条腿都断了，只能靠 workflow 变红触发 GitHub 自己的通知
            print("[哨兵] 微信和邮件都发不出去 → 让 workflow 失败（GitHub 会发通知）",
                  file=sys.stderr)
            return 1
        print("[哨兵] 邮件告警已发出")

    mark_alert(alert_tag)
    return 0


if __name__ == "__main__":
    sys.exit(main())
