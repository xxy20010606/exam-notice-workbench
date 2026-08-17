# 国内 CI 抓取部署指南（治本修复 gov.cn 封海外 IP）

GitHub Actions 的 runner 是海外 IP，被国内 gov.cn 封（Network unreachable / 403 / 超时）。
本方案把抓取迁到**国内 CI**（runner 是国内 IP），直连通畅，代码无需改动。

## 一、GitHub 侧（一次性）

1. **加 CI 公钥为 Deploy key**
   - 打开 https://github.com/xxy20010606/exam-notice-workbench/settings/keys
   - Add deploy key → Title 填 `ci-domestic`，**勾选 Allow write access**
   - Key 粘贴仓库里 `ci_scrape.sh` 说明对应的公钥（指纹 `SHA256:hh+wWh9mGWICnPraeoT3S9ox4RjeVM+TRwONw8x+LbA`）
   - 这把是**专用 CI 密钥**，和沙箱那把独立，可单独撤销

## 二、阿里云效（推荐，免费个人版）

1. 登录 https://devops.aliyun.com/ → 云效 Flow → 我的流水线 → 新建流水线
2. 选择「空白模板」
3. **添加流水线源**：代码源选 GitHub → 授权绑定你的 GitHub 账号 → 选中 `exam-notice-workbench` 仓库（默认分支 main）
4. **添加任务**：拖一个「运行脚本 / Shell」步骤，内容为：
   ```bash
   bash ci_scrape.sh
   ```
5. **添加环境变量（保密）**：在流水线「变量 / 环境变量」里加
   - `CI_DEPLOY_KEY` = 专用私钥全文（含 `-----BEGIN/END-----` 多行，设为保密变量）
   - 可选：`SMTP_PASSWORD` / `RECIPIENT` / `PAGES_URL` / `SERVERCHAN_KEY`（与现有 daily_digest 一致）
6. **定时触发**：流水线设置 → 触发器 → 定时（Cron）
   - 每 6 小时：`0 0 */6 * * ?`（云效 cron 是 7 位，秒在前；或用界面「周期」选每6h）
   - 补一次赶在 19:00 日报前：`0 0 10 * * ?`（北京 18:00 左右）
7. **保存并运行一次**验证：看日志是否出现 `[代理]` 打印（应为"未配置，纯直连"）且那 8 个源不再报错

> 注意：阿里云效的具体 UI 文案可能随版本变化，核心是「源=GitHub + 一步 Shell 跑 ci_scrape.sh + 定时触发」。

## 三、其他平台（脚本通用，仅编排不同）

- **腾讯云 CODING**：项目 → 持续集成 → 新建构建计划 → 代码源绑定 GitHub → 构建脚本 `bash ci_scrape.sh` → 定时触发。
- **Gitee Go**：需先把仓库镜像到 Gitee，再用 Gitee Go 跑同一脚本。
- 脚本 `ci_scrape.sh` 平台无关，上述平台只差「怎么填一步 Shell + 怎么存 Secret」。

## 四、切换 GitHub Pages 来源（重要）

现在 Pages 由 GitHub Actions 的 `deploy-pages` 部署。迁移后抓取改由国内 CI 推文件，需改 Pages 来源：
- 仓库 Settings → Pages → Build and deployment → Source 改为 **Deploy from a branch**
- Branch 选 **main** ，目录 **/root**
- 这样国内 CI 把 `index.html` + `notices.db` 推上 main，Pages 自动更新，无需 GitHub Actions 再部署

## 五、停用旧的 GitHub Actions 抓取（验证后再做）

国内 CI 跑稳一两天、确认那 8 个源恢复后，再停用旧流水线，避免两套系统抢同一 DB：
- 在仓库 `.github/workflows/scrape.yml` 顶部加 `if: false` 或删除该文件（由我执行，你确认后说一声）
- `daily_digest.yml`（日报推送）保留，它独立运行、不受影响

## 六、回滚

- 任何一步卡住：旧 GitHub Actions 抓取（scrape.yml）仍在线兜底，先别急着删。
- CI 密钥想撤销：GitHub Settings→Deploy keys 删 `ci-domestic` 即可，不影响沙箱密钥与其他功能。
