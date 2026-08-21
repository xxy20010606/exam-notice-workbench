# 国内 CI 抓取部署指南（治本修复 gov.cn 封海外 IP）

GitHub Actions 的 runner 是海外 IP，被国内 gov.cn 封（Network unreachable / 403 / 超时）。
本方案把抓取迁到**腾讯云 CODING**（runner 是国内 IP），直连通畅。

## 一、GitHub 侧（一次性）

1. **加 CI 公钥为 Deploy key**
   - 打开 https://github.com/xxy20010606/exam-notice-workbench/settings/keys
   - Add deploy key → Title 填 `ci-domestic`，**勾选 Allow write access**
   - Key 粘贴：
     ```
     ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAID7N97GoE5vJdkcs//nu/2mJSL5R2JOiqsmVYxqINTvA exam-notice-ci
     ```
   - 指纹核对：`SHA256:hh+wWh9mGWICnPraeoT3S9ox4RjeVM+TRwONw8x+LbA`

## 二、腾讯 CODING 配置（5 步搞定）

### 第 1 步：注册/登录 CODING
- 打开 **https://dev.tencent.com** （腾讯云账号直接登录）
- 如果没有项目：点「创建项目」→ 填 `exam-notice` → 选「私有」→ 创建

### 第 2 步：绑定 GitHub 仓库
- 进项目后，左边菜单点 **「代码仓库」** → **「导入代码库」**
- 选 **GitHub** → 授权绑定你的 GitHub 账号
- 搜索并选中 `exam-notice-workbench` 仓库
- 导入完成

### 第 3 步：开启持续集成
- 左边菜单点 **「持续集成」** → **「新建构建计划」**
- 选择 **「自定义构建过程」**（或「空白模板」）
- 代码源选刚导入的 `exam-notice-workbench` 仓库，分支 `main`
- 构建脚本填：
  ```bash
  bash ci_scrape.sh
  ```
- 或使用仓库里的 `.coding-ci.yml`（CODING 原生流水线格式）

### 第 4 步：添加保密变量
- 在构建计划设置里找 **「环境变量」** 或 **「变量与缓存」** → 新建变量
- 变量名：`CI_DEPLOY_KEY`
- 变量值：**专用私钥全文**（OpenSSH ed25519 私钥，含 BEGIN/END 标记行的完整多行内容）
- ✅ 勾选「设为保密变量」（加密存储，日志中不显示）

> 私钥获取方式：在 WorkBuddy 对话里跟我说"给我私钥"，我会读取文件给你。**不要粘贴到任何公开场合。**

### 第 5 步：设置定时触发
- 构建计划设置 → **「触发规则」** → **「定时触发」** / **Cron**
- 表达式：`0 */6 * * *`（每 6 小时）
- 再加一条补抓：`0 10 * * *`（北京 18:00，赶在 19:00 日报前）
- 保存

### 验证
- 点 **「立即构建」** 手动跑一次
- 看日志：出现 `[代理] 未配置，纯直连`（正常，CODING 本身就是国内 IP 不需要代理）
- 那 8 个源（福建4市+宁德+河北/山西/辽宁）应不再报 Network unreachable

## 三、切换 GitHub Pages 来源（重要）

现在 Pages 由 GitHub Actions 的 `deploy-pages` 部署。迁移后需改来源：
- 仓库 Settings → Pages → Build and deployment → Source 改为 **Deploy from a branch**
- Branch 选 **main** ，目录 **/ (root)**
- 这样 CODING 把 `index.html` + `notices.db` 推上 main，Pages 自动更新

## 四、停用旧的 GitHub Actions 抓取（验证后再做）

CODING 跑稳一两天、确认那 8 个源恢复后告诉我，我来停用旧 `scrape.yml`。
- `daily_digest.yml`（日报推送）保留，独立运行不受影响

## 五、回滚

- 旧 GitHub Actions `scrape.yml` 仍在线兜底，先别删
- CI 密钥想撤销：GitHub Settings→Deploy keys 删 `ci-domestic` 即可
