#!/usr/bin/env bash
# 国内 CI 抓取脚本（阿里云效 / 腾讯 Coding / Gitee Go 通用，平台无关）
#
# 前置：CI 把以下变量作为「环境变量 / Secret」注入到运行环境
#   CI_DEPLOY_KEY   (必填) 专用 SSH 私钥（多行，含 -----BEGIN/END-----）
#   SMTP_PASSWORD   (可选) 163 邮箱授权码 —— 发邮件用
#   RECIPIENT       (可选) 邮件收件人，默认发件人自己
#   PAGES_URL       (可选) 看板链接，写入正文
#   SERVERCHAN_KEY  (可选) 微信推送 Key
#   SCRAPE_PROXY    (可选) 一般国内 CI 直连即可，不需要；仅特殊源才用
#
# 设计要点：
#   · 国内 CI runner 本身就是国内 IP，直连 gov.cn 通畅，无需代理（直连优先路径直接生效）
#   · 通过专用 SSH deploy key 推回 GitHub main（与沙箱那把密钥分开，可独立撤销）
#   · 无新增时不推送，避免自循环
set -euo pipefail

# 1. 把 CI 私钥写入临时文件（用后即删）
KEY_DIR="$(mktemp -d)"
KEY_FILE="$KEY_DIR/id_ci"
printf '%s\n' "${CI_DEPLOY_KEY:?缺少 CI_DEPLOY_KEY 环境变量}" > "$KEY_FILE"
chmod 600 "$KEY_FILE"
export GIT_SSH_COMMAND="ssh -i $KEY_FILE -o StrictHostKeyChecking=no -o IdentitiesOnly=yes"

REPO_SSH="git@github.com:xxy20010606/exam-notice-workbench.git"

# 2. 拉取仓库（每次全新 clone，状态干净、避免残留）
rm -rf work
git clone "$REPO_SSH" work
cd work

# 3. 安装依赖 + Playwright Chromium
pip install -r requirements.txt
# --with-deps 需要 root + apt；阿里云效 Ubuntu runner 通常为 root，失败则退回不带 deps
python3 -m playwright install --with-deps chromium || python3 -m playwright install chromium

# 4. 抓取全部源 + 重建看板（国内 IP 直连，被封源自动恢复）
python3 cloud_run.py

# 5. 仅在有新增时才提交/推送（防自循环）
NEW_COUNT=$(cat .new_count 2>/dev/null || echo 0)
if [ "$NEW_COUNT" -le 0 ]; then
  echo "[跳过] 本次无新增公告，不提交不推送"
else
  git config user.name "ci-bot"
  git config user.email "ci@exam-notice.local"
  git add -A
  git commit -m "chore: 定时更新公告(国内CI) $(date +'%Y-%m-%d %H:%M')"
  git push "$REPO_SSH" HEAD:main
fi

# 6. 清理私钥，不留痕
rm -f "$KEY_FILE"
echo "[完成] 国内 CI 抓取结束"
