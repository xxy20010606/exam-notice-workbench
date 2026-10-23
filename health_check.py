#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
抓取健康哨兵 —— 专治「静默失败」（silent failure）。

背景（为什么需要它）：
  旧口径 `daily_digest.fetch_source_health()` 只报 fail_streak>0 或 last_error 非空，
  并把 `last_count=0` 当作「今日无新公告」，一律放行。
  但 parse_source() 返回的是**列表页上解析到的全部公告**，不是「今日新增」。
  列表页上历史公告一直都在，正常情况下不可能解析出 0 条。
  所以 last_count=0 ≈ 解析器失效（改版 / 反爬 / 端口迁移），而不是「没公告」。
  2026-10 实测：92 个源里 23 个 last_count=0 且 last_error 为空 —— 全部在假装正常。

本脚本的判据（与旧口径互补，不替代）：
  ① 解析归零  : 该源历史上有公告记录，但当前 last_count=0        → CRITICAL
     但必须区分两种「0 条」（2026-10-23 修正）：
       · last_raw=0   → 列表页一条候选都读不到 = 解析器/URL 真失效 → CRITICAL
       · last_raw>0   → 读到了 N 条但被过滤干净（栏目当前全是拟聘公示/
                        采购公告）= 内容空窗，不是故障 → WARN(window_empty)
     修正前只看 last_count，把南通/苏州/泰州/镇江这类内容空窗全误报成解析器失效。
  ② 从未成功  : 历史 0 条 + last_count=0 + 无报错                → WARN（配置错/URL 失效）
  ③ 抓取报错  : last_error 非空 或 fail_streak>0                 → WARN/CRITICAL
  ④ 数据陈旧  : 该源最新公告日期距今超过阈值                      → WARN
  ⑤ 黄金源熔断: 福建统一平台产出低于基线 / 最新日期过旧            → CRITICAL
  ⑥ 日期缺失  : date 为空的公告占比过高（影响排序与推送）          → WARN

用法：
  python health_check.py                  # 打印体检报告
  python health_check.py --push           # 有 CRITICAL 时推送微信（同日同问题只推一次）
  python health_check.py --db other.db    # 指定数据库（用于验证/回放）
  python health_check.py --json           # 输出机器可读 JSON

退出码：0 = 健康或有 WARN；1 = 存在 CRITICAL（便于 CI 里卡住流程）
"""
import os
import sys
import json
import sqlite3
import hashlib
import argparse
from datetime import datetime, date, timedelta

ROOT = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(ROOT, "notices.db")
SOURCES = os.path.join(ROOT, "sources.json")
STATE = os.path.join(ROOT, "health_state.json")

# 黄金源：业务命脉，零容忍。做「福建事业单位不漏」全靠这几个。
GOLDEN = {
    # 2026-10-22 基线校准：修掉 limit 截断(200→600)与 70 条正则误杀后，
    # 单轮正常产出由 ~200 条升至 513 条。基线设 280（-45%），
    # 既容得下 API 侧存量自然波动，又能在解析/过滤链失效时立刻熔断。
    "福建-全省事业单位(统一平台)": {"min_count": 280, "max_stale_days": 3},
    "福建（省直）-事业单位招聘": {"min_count": 8, "max_stale_days": 21},
}

# 各类源的「多久没新公告算异常」。公告频率差异极大，不能一刀切。
STALE_LIMIT = {"事业编": 45, "省考": 200, "国考": 320}

# issue 展示优先级：数字越小越靠前。stalled 是全链路级别，永远排第一。
CODE_ORDER = {
    "stalled": 0, "golden_count": 1, "golden_stale": 2,
    "silent_zero": 3, "long_dead": 4, "fetch_error": 5,
    "stale": 6, "nodate": 7, "window_empty": 8, "never_ok": 9, "not_run": 10,
}

# 报错源升级为 CRITICAL 的连续失败次数
ERR_CRIT_STREAK = 5
# date 缺失率告警线
NODATE_WARN_PCT = 20.0
# 历史上有多少条才证明「这个源曾经能正常解析」
HISTORY_PROOF = 3


def today_str():
    return datetime.now().strftime("%Y-%m-%d")


def days_between(d1, d2):
    """d1 早于 d2 时返回正天数；解析失败返回 None。"""
    try:
        a = datetime.strptime(d1[:10], "%Y-%m-%d").date()
        b = datetime.strptime(d2[:10], "%Y-%m-%d").date()
        return (b - a).days
    except Exception:
        return None


def load_sources_meta():
    """返回 {name: {"category":..., "enabled": bool}}。

    enabled=False 的源是**主动禁用**的（历史遗留/被其他源替代），
    它们本来就不该抓，不能被判定为「长期失联」——那是我自己的误报。
    """
    try:
        with open(SOURCES, encoding="utf-8") as f:
            data = json.load(f)
        return {s.get("name"): {"category": s.get("category") or "",
                                "enabled": s.get("enabled", True) is not False}
                for s in data}
    except Exception:
        return {}


def load_categories():
    return {k: v["category"] for k, v in load_sources_meta().items()}


def load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(st):
    try:
        with open(STATE, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[state] 写入失败: {e}")


def collect(db_path):
    """从数据库收集体检原料。只读，不改动业务表。"""
    if not os.path.exists(db_path):
        raise SystemExit(f"数据库不存在: {db_path}")
    conn = sqlite3.connect(db_path)
    try:
        total = conn.execute("SELECT COUNT(*) FROM notices").fetchone()[0]
        nodate = conn.execute(
            "SELECT COUNT(*) FROM notices WHERE date IS NULL OR date=''"
        ).fetchone()[0]
        # 端到端新鲜度：最近 24h 全库新增。这是唯一不会被 sources_status 覆盖影响的指标，
        # 用来判断「两条抓取轨是否都还活着」。
        cutoff = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
        recent_24h = conn.execute(
            "SELECT COUNT(*) FROM notices WHERE first_seen >= ?", (cutoff,)
        ).fetchone()[0]
        last_ingest = conn.execute("SELECT MAX(first_seen) FROM notices").fetchone()[0] or ""
        # 每源历史条数 + 最新公告日期
        hist = {}
        for src, cnt, latest, seen in conn.execute(
            "SELECT source, COUNT(*), MAX(date), MAX(last_seen) FROM notices GROUP BY source"
        ):
            hist[src or ""] = {
                "hist": cnt,
                "latest_date": latest or "",
                "latest_seen": seen or "",
            }
        try:
            status = conn.execute(
                "SELECT name,last_run,last_count,last_error,fail_streak FROM sources_status"
            ).fetchall()
        except sqlite3.OperationalError:
            status = []
        # last_raw = 过滤前原始候选数。老库没有这一列 → 全部给 None，退回旧判据。
        try:
            raws = {r[0]: r[1] for r in conn.execute(
                "SELECT name,last_raw FROM sources_status").fetchall()}
        except sqlite3.OperationalError:
            raws = {}
    finally:
        conn.close()
    return {"total": total, "nodate": nodate, "hist": hist, "status": status,
            "raws": raws, "recent_24h": recent_24h, "last_ingest": last_ingest}


def diagnose(data, cats):
    """返回 issues 列表 + 汇总。每条 issue: level/code/source/detail"""
    issues = []
    hist = data["hist"]
    seen = set()
    meta = load_sources_meta()
    disabled = {k for k, v in meta.items() if not v["enabled"]}

    # ⓪ 端到端新鲜度：全库最近 24h 零新增 → 抓取链路整体停摆。
    # 排在所有检查最前面，因为它是唯一不受 sources_status 互相覆盖影响的硬指标。
    if data.get("total", 0) > 0 and data.get("recent_24h", 0) == 0:
        issues.append({
            "level": "CRITICAL", "code": "stalled", "source": "(全库)",
            "detail": (f"最近 24 小时零新增入库，最后一次入库 "
                       f"{data.get('last_ingest', '')[:16]} → 两条抓取轨可能全挂"),
        })

    for name, last_run, last_count, last_error, fail_streak in data["status"]:
        name = name or ""
        seen.add(name)
        if name in disabled:
            continue          # 主动禁用的源不参与体检（不算失联）
        last_count = last_count or 0
        fail_streak = fail_streak or 0
        last_error = (last_error or "").strip()
        h = hist.get(name, {"hist": 0, "latest_date": ""})
        cat = cats.get(name, "")

        # ⑤ 黄金源优先判，命中就不再重复报
        if name in GOLDEN:
            g = GOLDEN[name]
            if last_count < g["min_count"]:
                issues.append({
                    "level": "CRITICAL", "code": "golden_count", "source": name,
                    "detail": f"产出 {last_count} 条，低于基线 {g['min_count']}（疑似解析失效）",
                })
                continue
            stale = days_between(h["latest_date"], today_str()) if h["latest_date"] else None
            if stale is not None and stale > g["max_stale_days"]:
                issues.append({
                    "level": "CRITICAL", "code": "golden_stale", "source": name,
                    "detail": f"最新公告 {h['latest_date'][:10]}，已 {stale} 天无更新",
                })
                continue

        # ③ 抓取报错（显性失败）
        if last_error:
            lvl = "CRITICAL" if fail_streak >= ERR_CRIT_STREAK else "WARN"
            issues.append({
                "level": lvl, "code": "fetch_error", "source": name,
                "detail": f"连续失败 {fail_streak} 次：{last_error[:90]}",
            })
            continue

        # ① 解析归零。必须区分两种「0 条」：
        #    raw=0  → 列表页一条候选都读不到 = 解析器/URL 真失效（CRITICAL）
        #    raw>0  → 读到了 N 条，但当前栏目全是拟聘公示/采购公告，被 include/exclude
        #             全挡掉 = 内容空窗（WARN）。这类不是故障，2026-10-23 实测南通/苏州/
        #             泰州/镇江都属于这一类，之前全被误报成「解析器疑似失效」。
        raw = data.get("raws", {}).get(name)
        if last_count == 0 and h["hist"] >= HISTORY_PROOF:
            if raw is not None and raw > 0:
                issues.append({
                    "level": "WARN", "code": "window_empty", "source": name,
                    "detail": (f"本轮读到 {raw} 条候选，但全被过滤（栏目当前只有拟聘公示/"
                               f"采购公告等）→ 内容空窗，非解析器故障"),
                })
            else:
                issues.append({
                    "level": "CRITICAL", "code": "silent_zero", "source": name,
                    "detail": f"历史有 {h['hist']} 条，本轮解析 0 条且无报错 → 解析器疑似失效",
                })
            continue

        # ② 从未成功过
        if last_count == 0 and h["hist"] == 0:
            if raw is not None and raw > 0:
                issues.append({
                    "level": "WARN", "code": "window_empty", "source": name,
                    "detail": f"读到 {raw} 条候选但全被过滤，从未入库过真公告",
                })
            else:
                issues.append({
                    "level": "WARN", "code": "never_ok", "source": name,
                    "detail": "从未解析出任何公告（URL/选择器可能配置错误）",
                })
            continue

        # ④ 数据陈旧
        if h["latest_date"]:
            stale = days_between(h["latest_date"], today_str())
            limit = STALE_LIMIT.get(cat, 45)
            if stale is not None and stale > limit:
                issues.append({
                    "level": "WARN", "code": "stale", "source": name,
                    "detail": f"最新公告 {h['latest_date'][:10]}，已 {stale} 天（阈值 {limit}）",
                })

    # 配置里有、但状态表里没有记录的源（跳过主动禁用的）
    for name in cats:
        if name in disabled:
            continue
        if name not in seen:
            h = hist.get(name, {"hist": 0, "latest_date": ""})
            if h["hist"] > 0:
                # 库里有历史公告，却连抓取记录都没了 → 长期失联，属于「静默漏」的重症
                issues.append({
                    "level": "CRITICAL", "code": "long_dead", "source": name,
                    "detail": f"库内尚有 {h['hist']} 条历史公告，但已无任何抓取记录（长期失联）",
                })
            else:
                issues.append({
                    "level": "WARN", "code": "not_run", "source": name,
                    "detail": "配置中存在但从未产生抓取记录",
                })

    # ⑥ 日期缺失率（全局）
    if data["total"] > 0:
        pct = data["nodate"] * 100.0 / data["total"]
        if pct > NODATE_WARN_PCT:
            issues.append({
                "level": "WARN", "code": "nodate",
                "source": f"(全局 {data['nodate']}/{data['total']})",
                "detail": f"date 缺失率 {pct:.1f}%，影响排序与「今日新增」判定",
            })

    order = {"CRITICAL": 0, "WARN": 1}
    issues.sort(key=lambda x: (order.get(x["level"], 9),
                               CODE_ORDER.get(x["code"], 9),
                               x["source"]))
    # 统一清洗 detail：多行报错（如 Playwright 的 Call log）会破坏 markdown 列表与邮件排版
    for i in issues:
        i["detail"] = " ".join(str(i["detail"]).split())[:160]
    return issues


def summarize(issues, data):
    crit = [i for i in issues if i["level"] == "CRITICAL"]
    warn = [i for i in issues if i["level"] == "WARN"]
    return {
        "total_notices": data["total"],
        "nodate": data["nodate"],
        "critical": len(crit),
        "warn": len(warn),
        "ok_sources": max(0, len(data["status"]) - len({i["source"] for i in issues})),
    }


def render_text(issues, summ, kpi=None):
    L = []
    L.append("=" * 56)
    L.append(f"抓取健康体检  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    L.append(f"公告总数 {summ['total_notices']} | date缺失 {summ['nodate']} | "
             f"CRITICAL {summ['critical']} | WARN {summ['warn']}")
    L.append("=" * 56)
    if kpi:
        L.append("\n核心源 KPI")
        for k in kpi:
            sd = f"{k['stale_days']} 天前" if k["stale_days"] is not None else "无日期"
            L.append(f"  · {k['name']}")
            L.append(f"    解析 {k['parsed']} 条 | 最新 {k['latest']}（{sd}）")
    if not issues:
        L.append("\n全部正常。")
        return "\n".join(L)
    for level in ("CRITICAL", "WARN"):
        group = [i for i in issues if i["level"] == level]
        if not group:
            continue
        L.append(f"\n[{level}] {len(group)} 项")
        for i in group:
            L.append(f"  · {i['source']}")
            L.append(f"    {i['detail']}")
    return "\n".join(L)


def golden_status(data):
    """核心源的每日 KPI —— 做「福建事业单位不漏」全靠这几个，必须天天可见。"""
    out = []
    counts = {n: (lc or 0) for n, lr, lc, le, fs in data["status"]}
    for name in GOLDEN:
        h = data.get("hist", {}).get(name, {"hist": 0, "latest_date": ""})
        # 公告 date 可能为空（列表页不显示日期），此时退化为「最后一次抓到它的时间」
        latest = h.get("latest_date") or h.get("latest_seen") or ""
        stale = days_between(latest, today_str()) if latest else None
        out.append({
            "name": name,
            "parsed": counts.get(name, 0),
            "latest": latest[:10] or "—",
            "stale_days": stale,
        })
    return out


def render_markdown(issues, summ, kpi=None):
    """给 Server酱 / 日报正文用的精简版，只列 CRITICAL 与汇总。"""
    crit = [i for i in issues if i["level"] == "CRITICAL"]
    warn = [i for i in issues if i["level"] == "WARN"]
    L = [f"**CRITICAL {len(crit)} 项 / WARN {len(warn)} 项**",
         f"公告总数 {summ['total_notices']}，date 缺失 {summ['nodate']}", ""]
    if kpi:
        L.append("**核心源状态**")
        for k in kpi:
            sd = f"{k['stale_days']} 天前" if k["stale_days"] is not None else "无日期"
            L.append(f"- {k['name']}：解析 {k['parsed']} 条，最新 {k['latest']}（{sd}）")
        L.append("")
    if crit:
        L.append("**必须处理：**")
        for i in crit:
            L.append(f"- {i['source']}：{i['detail']}")
        L.append("")
    if warn:
        L.append(f"**留意（{len(warn)} 项）：**")
        for i in warn[:12]:
            L.append(f"- {i['source']}：{i['detail']}")
        if len(warn) > 12:
            L.append(f"- …另有 {len(warn) - 12} 项")
    return "\n".join(L)


# 只要出现就必须立刻推到手机的问题类型（其余交给 19:00 日报，避免每 6 小时刷屏）
PUSH_ALWAYS = {"stalled", "golden_count", "golden_stale"}


def push(title, md, st):
    """只在有 CRITICAL 时推；同日同问题集合只推一次，避免告警疲劳。"""
    try:
        from daily_digest import send_wechat
    except Exception as e:
        print(f"[push] 加载推送通道失败: {e}")
        return False
    ok = send_wechat(title, md)
    if ok:
        st["last_push"] = today_str()
    return ok


def main():
    ap = argparse.ArgumentParser(description="抓取健康哨兵")
    ap.add_argument("--db", default=DB, help="数据库路径")
    ap.add_argument("--push", action="store_true", help="有 CRITICAL 时推送微信")
    ap.add_argument("--push-mode", choices=["all", "critical"], default="all",
                    help="all=所有 CRITICAL 都推；critical=只推致命项(全库停摆/黄金源失效)")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--quiet", action="store_true", help="仅在有 CRITICAL 时输出")
    args = ap.parse_args()

    cats = load_categories()
    data = collect(args.db)
    issues = diagnose(data, cats)
    summ = summarize(issues, data)
    kpi = golden_status(data)

    if args.json:
        print(json.dumps({"summary": summ, "kpi": kpi, "issues": issues},
                         ensure_ascii=False, indent=2))
    elif not (args.quiet and summ["critical"] == 0):
        print(render_text(issues, summ, kpi))

    st = load_state()
    if args.push and summ["critical"] > 0:
        targets = [i for i in issues if i["level"] == "CRITICAL"
                   and (args.push_mode == "all" or i["code"] in PUSH_ALWAYS)]
        if not targets:
            # 非致命项不打扰手机，留给 19:00 日报统一汇报
            print(f"[push] {summ['critical']} 项 CRITICAL 均非致命类型，交由日报汇总")
            save_state(st)
            return 1
        sig = hashlib.sha1(
            "|".join(sorted(f"{i['code']}:{i['source']}" for i in targets)).encode("utf-8")
        ).hexdigest()[:12]
        # 同一签名 + 同一天 → 不重复推
        if st.get("sig") == sig and st.get("last_push") == today_str():
            print("[push] 今日已推送过相同问题，跳过")
        else:
            ok = push(f"公告抓取致命异常 · {len(targets)} 项", 
                      render_markdown(targets + [i for i in issues
                                                 if i["level"] == "WARN"], summ, kpi), st)
            if ok:
                st["sig"] = sig
            save_state(st)
    else:
        save_state(st)

    return 1 if summ["critical"] > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
