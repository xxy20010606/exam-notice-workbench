#!/usr/bin/env bash
# 安全推送封装（防呆 #2 / #3 / #4 / #5）
#
# 用法:  bash tools/push.sh [repo_dir=.]
#
# 它替你完成：
#   #5 铁律   —— 只允许推 main 分支；绝不带 --force（用 --no-force-with-lease）
#   #4 防推错  —— remote 必须指向本仓库（github-exam / xxy20010606/exam-notice-workbench）
#   #3 扫泄漏  —— 暂存区 + 工作区扫描疑似 token / 私钥，发现即中止
#   #2 先校验  —— 若 sources.json 有改动/存在，推送前先跑 validate_sources.py
#
# 退出码非 0 即被拦下，绝不带病推送。
set -uo pipefail

REPO_DIR="${1:-.}"
cd "$REPO_DIR" || { echo "[FAIL] 无法进入目录: $REPO_DIR" >&2; exit 1; }

REMOTE="${REMOTE:-origin}"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"

# ---------- #5 铁律：只推 main ----------
if [ "$BRANCH" != "main" ]; then
  echo "[FAIL#5] 当前分支是 '$BRANCH'，只允许推 main（防呆 #5）" >&2
  exit 1
fi

# ---------- #4 防推错仓：remote 必须指向本仓库 ----------
RURL="$(git remote get-url "$REMOTE")"
if ! printf '%s' "$RURL" | grep -q "github-exam\|xxy20010606/exam-notice-workbench"; then
  echo "[FAIL#4] remote '$REMOTE' 不是 exam-notice-workbench 仓库: $RURL" >&2
  exit 1
fi

# ---------- #3 扫泄漏：token / 私钥 ----------
# 正则字面量用相邻字符串拼接（PR""IVATE）避免脚本自身被自己匹配。
TOK='(ghp_|gho_|ghu_|ghs_|ghr_|github_pat_)[A-Za-z0-9]{20,}|sk-ssh-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|'"-----BEGIN [A-Z ]*PR""IVATE KEY-----"

if git diff --cached -U0 | grep -Eq "$TOK"; then
  echo "[FAIL#3] 暂存区检测到疑似 token / 私钥，已中止推送" >&2
  git diff --cached -U0 | grep -Eo "$TOK" | sed -E 's/(.{6}).*/\1****/' >&2
  exit 1
fi
if git diff -U0 | grep -Eq "$TOK"; then
  echo "[FAIL#3] 工作区（未暂存）检测到疑似 token / 私钥，已中止推送" >&2
  git diff -U0 | grep -Eo "$TOK" | sed -E 's/(.{6}).*/\1****/' >&2
  exit 1
fi
if grep -rIqE "$TOK" --exclude-dir=.git --exclude=tools/push.sh --exclude-dir=.githooks . 2>/dev/null; then
  echo "[FAIL#3] 工作区文件检测到疑似 token / 私钥，已中止推送" >&2
  exit 1
fi
echo "[防呆#3] 未检测到 token / 私钥 ✅"

# ---------- #2 先校验 sources.json ----------
if git diff --cached --name-only | grep -qx 'sources.json' || [ -f sources.json ]; then
  if [ -f validate_sources.py ]; then
    echo "[info] 运行 validate_sources.py ..."
    python3 validate_sources.py || exit 1
  fi
fi

# ---------- 推送（禁用 --force） ----------
echo "[info] 推送 $REMOTE/$BRANCH（禁用 --force）..."
git push "$REMOTE" "$BRANCH" --no-force-with-lease
echo "[OK] 推送完成 ✅"
