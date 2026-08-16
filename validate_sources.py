#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sources.json 推送前 / 抓取前自动校验（防呆）：

  - 顶层必须是 list（源数组）
  - 每条必填 name（非空字符串，且全局唯一） / url（非空、合法 http(s) 地址）
  - method ∈ {http, browser}（若提供）
  - 所有 *_include / *_exclude / *_regex / *_re / *_pattern 字段必须是合法正则
  - browser_wait 必须是 >=0 的整数（若提供）
  - browser_wait_until ∈ {load, domcontentloaded, networkidle}（若提供）
  - page_urls 必须是 http(s) URL 字符串数组（若提供）
  - 缺失 category / region 仅警告、不致命（看板分组依据，建议补全）

退出码：0 = 通过；1 = 存在致命错误（CI 应中止，避免把坏配置推上云端 scraper）。
仅依赖 Python 标准库，可在本地或 GitHub Actions 直接 `python validate_sources.py`。
"""
import json
import os
import re
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.abspath(__file__))
SOURCES = os.path.join(ROOT, "sources.json")

REGEX_KEY_RE = re.compile(r"(include|exclude|regex|_re|_pattern)$", re.IGNORECASE)
VALID_METHODS = {"http", "browser"}
VALID_WAIT_UNTIL = {"load", "domcontentloaded", "networkidle"}
VALID_URL_RE = re.compile(r"^https?://[^\s/]+", re.IGNORECASE)


def main():
    errors = []
    warnings = []

    try:
        with open(SOURCES, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:  # noqa: BLE001
        print(f"[FAIL] sources.json 不是合法 JSON: {e}")
        return 1

    if not isinstance(data, list):
        print(f"[FAIL] 顶层结构必须是 list（源数组），当前是 {type(data).__name__}")
        return 1
    if not data:
        print("[FAIL] sources.json 为空数组，没有任何数据源")
        return 1

    names = []
    for idx, s in enumerate(data):
        tag = f"#{idx}"
        if not isinstance(s, dict):
            errors.append(f"{tag} 不是对象(dict)，而是 {type(s).__name__}")
            continue

        # name
        name = s.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"{tag} 缺少有效的 name（非空字符串）")
        else:
            names.append(name)
            tag = f"{tag} {name}"

        # url
        url = s.get("url")
        if not isinstance(url, str) or not url.strip():
            errors.append(f"{tag} 缺少有效的 url（非空字符串）")
        elif not VALID_URL_RE.match(url):
            errors.append(f"{tag} url 不是合法的 http/https 地址: {url!r}")

        # method
        method = s.get("method")
        if method is not None and method not in VALID_METHODS:
            errors.append(f"{tag} method 非法: {method!r}（应为 http/browser）")

        # 正则类字段（值为 null/None 表示禁用，合法；非空字符串必须能编译）
        for k, v in s.items():
            if REGEX_KEY_RE.search(k):
                if v is None:
                    continue  # 显式关闭，合法
                if not isinstance(v, str):
                    errors.append(f"{tag} 正则字段 {k} 必须是字符串或 null（当前: {type(v).__name__}）")
                elif not v.strip():
                    errors.append(f"{tag} 正则字段 {k} 为空字符串，请删除该字段或填入合法正则")
                else:
                    try:
                        re.compile(v)
                    except re.error as e:  # noqa: BLE001
                        errors.append(f"{tag} 正则字段 {k} 编译失败: {e}  (值: {v!r})")

        # browser_wait
        bw = s.get("browser_wait")
        if bw is not None:
            if isinstance(bw, bool) or not isinstance(bw, int) or bw < 0:
                errors.append(f"{tag} browser_wait 必须是 >=0 的整数，当前: {bw!r}")

        # browser_wait_until
        bwu = s.get("browser_wait_until")
        if bwu is not None and bwu not in VALID_WAIT_UNTIL:
            errors.append(
                f"{tag} browser_wait_until 非法: {bwu!r}（应为 load/domcontentloaded/networkidle）"
            )

        # page_urls
        pu = s.get("page_urls")
        if pu is not None:
            if not isinstance(pu, list) or not all(
                isinstance(x, str) and VALID_URL_RE.match(x) for x in pu
            ):
                errors.append(f"{tag} page_urls 必须是 http(s) URL 字符串数组")

        # category / region 仅警告
        if not s.get("category"):
            warnings.append(f"{tag} 缺少 category（看板分组依据，建议补全）")
        if not s.get("region"):
            warnings.append(f"{tag} 缺少 region（看板分组依据，建议补全）")

    # 重复 name
    dup = [n for n, c in Counter(names).items() if c > 1]
    if dup:
        errors.append(f"存在重复 name: {', '.join(dup)}")

    for w in warnings:
        print(f"[WARN] {w}")

    if errors:
        print(f"\n校验未通过，发现 {len(errors)} 个致命问题：")
        for e in errors:
            print(f"[FAIL] {e}")
        return 1

    extra = f"（{len(warnings)} 条警告）" if warnings else ""
    print(f"✅ sources.json 校验通过：共 {len(data)} 个源，无致命问题{extra}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
