# 国内 CI 抓取部署指南（治本修复 gov.cn 封海外 IP）

GitHub Actions 的 runner 是海外 IP，被国内 gov.cn 封（Network unreachable / 403 / 超时）。
本方案把抓取迁到 **Gitee Go**（Gitee 持续集成，国内 IP 直连通畅）。

## 一、GitHub 侧（已完成 ✅）

GitHub Deploy keys 已加 `ci-domestic`（read/write，指纹 `SHA256:hh+wWh9m...`）。
若需重新添加：https://github.com/xxy20010606/exam-notice-workbench/settings/keys
公钥：
```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAID7N97GoE5vJdkcs//nu/2mJSL5R2JOiqsmVYxqINTvA exam-notice-ci
```

## 二、Gitee 侧配置（5 步）

### 第 1 步：注册/登录 Gitee
- 打开 **https://gitee.com** → 用 GitHub/微信/手机号注册登录

### 第 2 步：导入仓库到 Gitee
- 右上角 **「+」** → **「从 GitHub/GitLab 导入仓库」**
- 授权 GitHub → 选中 `exam-notice-workbench` → 导入（设为私有仓库）
- 导入后 Gitee 上的仓库有了代码（含 `.workflow/scrape.yml` 和 `ci_scrape.sh`）

### 第 3 步：开启 Gitee Go
- 进入 Gitee 仓库 → 左边菜单 **「DevOps」** → **「Gitee Go」**
- 首次需开通（免费）→ 同意协议
- 点击 **「新建流水线」** → 选仓库里的 `.workflow/scrape.yml` 作为配置
- 流水线会自动识别 `on.schedule` 的定时触发

### 第 4 步：添加保密变量
- 流水线设置 → **「变量」** → 新建
- 变量名：`CI_DEPLOY_KEY`
- 值：**专用私钥全文**（OpenSSH ed25519 私钥，含 BEGIN/END 标记行的完整多行内容）
- ✅ 设为保密变量
- 可选：`SMTP_PASSWORD` / `RECIPIENT` / `PAGES_URL` / `SERVERCHAN_KEY`（与日报一致）

> 私钥获取：在 WorkBuddy 跟我说"给我 CI 私钥"，我读取文件给你。不要粘贴到公开场合。

### 第 5 步：手动运行验证
- 点 **「运行」** 手动跑一次
- 看日志：出现 `[代理] 未配置，纯直连`（正常，Gitee 是国内 IP）
- 那 8 个源（福建4市+宁德+河北/山西/辽宁）应不再报 Network unreachable

### 保持 GitHub 仓库同步
Gitee Go 抓取后通过 `ci-scrape.sh` 推回 **GitHub main**（用 `ci-domestic` 公钥）。
GitHub Pages 仍从 GitHub main 读取，无需改设置。

## 三、停用旧 GitHub Actions 抓取（验证后）

Gitee Go 跑稳一两天、确认那 8 个源恢复后告诉我，我来停用旧 `scrape.yml`。
- `daily_digest.yml`（日报推送）保留，独立运行不受影响

## 四、回滚

- 旧 GitHub Actions `scrape.yml` 仍在线兜底，先别删
- CI 密钥撤销：GitHub Settings→Deploy keys 删 `ci-domestic` 即可
