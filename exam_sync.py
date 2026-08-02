#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
官方公告发布后自动校准倒计时日期。

触发条件：仅当本次 *新增* 公告里包含「国考」或「福建省考」的招录公告时，
才去抓取该公告详情页，用正则提取报名区间 + 笔试日期，写回 exam_dates.json。
平时（无新招录公告）零开销、不碰 exam_dates.json。

提取规则（宽松多模式，避免误匹配成绩/面试等日期）：
  - 报名区间：要求以「报名」开头
  - 笔试日期：要求以「笔试 / 公共科目笔试」开头
验证：报名开始 <= 报名结束 <= 笔试；年份与 exam_dates.json 的 year 基本吻合。
"""
import os, sys, json, re

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import scraper

DB = os.path.join(ROOT, "notices.db")
EXAM_DATES = os.path.join(ROOT, "exam_dates.json")

# 仅认「招录公告」，排除公示/体检/面试/补录等噪音
RECRUIT_RE = re.compile(r"考试录用公务员|公务员招录|公务员招考|录用公务员公告")

# 报名区间：报名(时间)...2026年10月15日[8:00] 至/到 10月24日[18:00]
# 允许「日」后跟具体时间点（如 8:00），再接「至/到/—」分隔
REG_RE = re.compile(
    r"报名[^0-9]{0,20}?(\d{4})[年./\-](\d{1,2})[月./\-](\d{1,2})[日号]?[0-9:：]*"
    r"\s*(?:至|到|[-~—])\s*(\d{1,2})[月./\-](\d{1,2})[日号]?[0-9:：]*"
)
# 笔试：公共科目笔试 / 笔试 ...2026年11月29日
EXAM_RE = re.compile(
    r"(?:公共科目笔试|笔试)[^0-9]{0,20}?(\d{4})[年./\-](\d{1,2})[月./\-](\d{1,2})[日号]?"
)


def extract_dates(html):
    """从公告详情页 HTML 提取报名区间 + 笔试日期，返回 dict（可能为空）。"""
    out = {}
    reg = REG_RE.search(html or "")
    if reg:
        y, m1, d1, m2, d2 = reg.groups()
        out["registration_start"] = f"{y}-{int(m1):02d}-{int(d1):02d}"
        out["registration_end"] = f"{y}-{int(m2):02d}-{int(d2):02d}"
    exam = EXAM_RE.search(html or "")
    if exam:
        y, m, d = exam.groups()
        out["written_exam"] = f"{y}-{int(m):02d}-{int(d):02d}"
    return out


def _valid(dates, key, expect_year):
    """基础合理性校验：必须含笔试；报名区间内部及相对笔试的顺序正确；年份吻合。"""
    if "written_exam" not in dates:
        return False
    we = dates["written_exam"]
    yint = int(we[:4])
    if key == "国考":
        # 国考笔试通常在报名同年年底（year-1），接受 year-1 / year
        if yint not in (expect_year - 1, expect_year):
            return False
    else:  # 福建省考：笔试在报名同年（year）
        if yint != expect_year:
            return False
    if "registration_start" in dates and "registration_end" in dates:
        if dates["registration_start"] > dates["registration_end"]:
            return False
        if dates["registration_end"] > dates["written_exam"]:
            return False
    return True


def sync(new_items):
    """
    入参 new_items: run_all 返回的本次新增公告列表（含 category/region/title/url）。
    仅当其中含国考 / 福建省考的招录公告时才触发抓取+校准。
    返回被更新的考试 key 列表（如 ["国考"]），无更新返回 None。
    """
    targets = [it for it in (new_items or [])
               if (it.get("category") == "国考"
                   or (it.get("category") == "省考" and it.get("region") == "福建"))]
    if not targets:
        return None

    try:
        with open(EXAM_DATES, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None

    updated = []
    for it in targets:
        if not RECRUIT_RE.search(it.get("title", "")):
            continue
        try:
            html = scraper.fetch_http(it["url"], timeout=20)
        except Exception as e:
            print(f"[校准] 抓取详情失败 {it.get('url')}: {e}")
            continue
        dates = extract_dates(html)
        if not dates:
            continue
        key = "国考" if it.get("category") == "国考" else "福建省考"
        if not _valid(dates, key, data.get(key, {}).get("year", 2027)):
            print(f"[校准] {key} 提取到的日期未通过校验，跳过：{dates}")
            continue
        data[key].update(dates)
        data[key]["last_verified"] = scraper.now_iso()
        data[key]["verified_source"] = it["url"]
        data[key]["note"] = (f"已由官方公告自动校准（{it.get('title','')}）。"
                             f"来源：{it['url']}（{scraper.now_iso()}）")
        updated.append(key)
        print(f"[校准] {key} 倒计时已更新为：{dates}")

    if updated:
        with open(EXAM_DATES, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return updated
    return None


if __name__ == "__main__":
    # 本地调试：用一条模拟公告标题+HTML 验证提取逻辑
    html = ("本次考试报名时间：2026年10月15日8:00至10月24日18:00，"
            "公共科目笔试时间为2026年11月29日。专业科目笔试为11月28日。")
    print("提取结果:", extract_dates(html))
