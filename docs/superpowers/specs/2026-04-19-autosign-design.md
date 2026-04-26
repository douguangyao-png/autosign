# Autosign 定时签到平台 — 设计文档

- **日期**：2026-04-19
- **作者**：Claude（整理）
- **范围**：第一期（本次实施）+ 第二期（需求留档）

---

## 1. 背景与目标

个人自动化需求：每天定时对 `hifiti.com`（Hi-Fi 音乐论坛）等网站执行签到、监控、下载等任务。预期未来会持续追加新任务（例如机票/车票监控、网盘自动下载等）。

**核心诉求：**

1. 一个**统一的定时任务平台**，而非散装的 cron 脚本
2. 有**前台 Web UI** 可查看任务、日志、执行历史
3. **支持多账号**（同一任务跑多个账号）
4. **失败时飞书通知**，成功时静默
5. 第一期只做 hifiti 登录 + 签到；第二期做自动回帖 + 网盘下载

---

## 2. 技术选型

### 平台：青龙面板（whyour/qinglong）

**选型理由：**

- GitHub 30k+ star，国内"定时签到/脚本调度"事实标准
- 原生支持 Python / JavaScript / Shell / TypeScript 任意脚本
- 自带 Web UI：任务列表、cron 编辑、环境变量、日志查看、通知中心
- 内置飞书/Server 酱/Telegram/钉钉 等 10+ 通知渠道
- 配套脚本生态（dailycheckin、checkinpanel、ql-script-hub）
- Docker 一键部署，单容器

**被淘汰的备选：**

| 方案 | 淘汰原因 |
|---|---|
| 单文件 Python + crontab | 无 UI、无法统一管理未来多个任务 |
| Plombery / ndscheduler | 比青龙轻，但无签到生态、无内置飞书 |
| FastAPI + APScheduler + 自写前端 | 造青龙已有的轮子，数周工作量 |
| XXL-JOB | 企业级分布式调度，Java+MySQL 全家桶，对 Python 脚本是二等公民，无签到生态 |

### 脚本语言：Python + requests

- 生态最成熟；`requests.Session()` 自动管理 cookie
- 依赖仅 `requests`，无额外运行时负担

### 凭据管理：青龙环境变量

不使用 `.env` 文件。通过青龙 Web UI 维护 `HIFITI_ACCOUNT` / `HIFITI_PASSWORD`，脚本从 `os.environ` 读取。改凭据无需改代码、无需重启容器。

### 通知：青龙内置飞书机器人

不在脚本里写飞书 SDK。脚本通过 `exit(1)` + stdout 日志触发通知，青龙捕获并推送到飞书。策略为**仅失败时通知**。

---

## 3. 系统架构

```
┌─────────────────────────────────────────────────────────┐
│                    /root/autosign/                      │
│                                                         │
│  ┌──────────────────────────────────────────────┐       │
│  │  青龙面板容器 (Docker)                        │       │
│  │  ─ Web UI on :5700                           │       │
│  │  ─ 定时调度器 (cron)                          │       │
│  │  ─ 脚本执行器 (python3/node/bash)             │       │
│  │  ─ 日志查看 / 环境变量管理 / 通知中心          │       │
│  │                                              │       │
│  │  持久化挂载:                                  │       │
│  │    ./ql/data   → /ql/data                    │       │
│  │    ./scripts   → /ql/data/scripts/autosign   │       │
│  └──────────────────────────────────────────────┘       │
│                                                         │
│  scripts/                                               │
│  ├── hifiti_sign.py    ← 第一期主脚本                   │
│  ├── requirements.txt  ← requests                       │
│  └── README.md                                          │
│                                                         │
│  docker-compose.yml                                     │
│  .gitignore                                             │
│  README.md                                              │
└─────────────────────────────────────────────────────────┘
```

---

## 4. 目录结构

```
/root/autosign/
├── docker-compose.yml          # 青龙服务定义
├── .gitignore                  # 忽略 ql/data（运行数据含敏感信息）
├── README.md                   # 部署 + 使用说明
├── docs/
│   └── superpowers/
│       └── specs/
│           └── 2026-04-19-autosign-design.md   # 本文
├── ql/                         # 青龙运行数据（自动生成，不入库）
│   └── data/
└── scripts/                    # 脚本（入库）
    ├── hifiti_sign.py          # 第一期
    ├── requirements.txt
    └── README.md
```

---

## 5. docker-compose 定义

```yaml
services:
  qinglong:
    image: whyour/qinglong:latest
    container_name: qinglong
    restart: unless-stopped
    ports:
      - "127.0.0.1:5700:5700"  # 仅本机监听（第一期）
    volumes:
      - ./ql/data:/ql/data
      - ./scripts:/ql/data/scripts/autosign
    environment:
      - TZ=Asia/Shanghai
```

**关键点：**

- **时区 `Asia/Shanghai`** 必须显式设置，否则 cron `0 1 * * *` 会按 UTC 触发（北京时间 09:00）
- **脚本挂载路径** 使用子目录 `autosign`，避免与青龙拉库脚本混杂
- **端口 5700** 默认，第一期仅本机访问；第二期加 nginx 反代暴露公网

---

## 6. hifiti_sign.py 脚本规格

### 6.1 环境变量

| 变量名 | 格式 | 示例 | 说明 |
|---|---|---|---|
| `HIFITI_ACCOUNT` | 多账号用 `&` 分隔 | `a@x.com&b@y.com` | 邮箱或用户名 |
| `HIFITI_PASSWORD` | 多密码用 `&` 分隔 | `pw1&pw2` | 与账号一一对应 |

**校验规则：**

- `HIFITI_ACCOUNT` 和 `HIFITI_PASSWORD` 都必须存在
- 账号数与密码数必须一致
- 账号数 `1 ≤ N ≤ 10`，超出则拒绝执行

> **密码中的 `&` 字符限制**：由于使用 `&` 作为多账号分隔符，若密码本身含 `&` 会被错误切分。README 需明确提示：如密码含 `&`，请改密码或改用其他分隔符机制。

### 6.2 接口信息（抓包实证）

**登录接口：**

```
POST https://www.hifiti.com/user-login.htm
Content-Type: application/x-www-form-urlencoded
Body: email={email}&password={password}
成功标识: Set-Cookie 中出现 bbs_token
```

**签到接口：**

```
POST https://www.hifiti.com/sg_sign.htm
X-Requested-With: XMLHttpRequest
Content-Length: 0 (空 body)
Cookie: 需携带登录后的 bbs_token
```

> **实现时需要实测校准**：登录/签到的"成功判定关键字"（例如签到响应中是"成功"、"已签到"、"+N 金币"还是 JSON `code=1`）目前基于通用经验推断，实施前必须用真实账号跑一次完整请求，根据实际响应体锁定准确的关键字或 JSON 字段，再写入脚本。

### 6.3 执行流程

```
读取环境变量
  HIFITI_ACCOUNT   "a@x.com&b@y.com&c@z.com"  → [a, b, c]
  HIFITI_PASSWORD  "pw1&pw2&pw3"              → [p1, p2, p3]
校验：数量一致且 1..10 之间，否则 exit(1)
     ↓
逐账号循环（每账号间隔 1.5~3.0 秒，随机）
     ↓
  ┌────────────────────────────────────────┐
  │ 单账号流程（独立 requests.Session）      │
  │                                        │
  │  1. GET /user-login.htm                │  ← 获取 bbs_sid
  │     （统一 User-Agent + Referer）       │
  │                                        │
  │  2. POST /user-login.htm               │  ← 登录
  │     body: email=X&password=Y           │
  │     成功判定: cookie 中有 bbs_token      │
  │     失败 → 记录 LOGIN_FAIL，跳下一个     │
  │                                        │
  │  3. POST /sg_sign.htm                  │  ← 签到
  │     header: X-Requested-With=XHR       │
  │     body: 空                           │
  │     成功判定: 响应含"成功"/"已签到"      │
  │     失败 → 记录 SIGN_FAIL                │
  │     成功 → 记录 OK                       │
  └────────────────────────────────────────┘
     ↓
汇总 + 打印报告到 stdout（青龙捕获为日志）
     ↓
任一账号失败 → sys.exit(1)
全部成功     → sys.exit(0)
```

### 6.4 关键实现细节

| 项目 | 决定 |
|---|---|
| Session 隔离 | 每账号独立 `requests.Session()`，避免 cookie 串号 |
| User-Agent | 使用抓包时的 Android Chrome UA，保持特征一致 |
| 超时 | `timeout=20`（连接 + 读取） |
| 重试 | 网络错误 1 次重试（间隔 3 秒）；业务错误不重试 |
| "已签到" | 视为成功（允许重复执行） |
| 账号间隔 | 随机 1.5~3.0 秒（避免固定节奏） |
| 日志截断 | `response.text` 只取前 500 字符用于判定/记录 |
| 敏感数据 | 密码永不打印；日志不输出完整响应体 |
| 异常兜底 | 顶层 `try/except` 捕获全部未预期异常，打印 traceback，`exit(1)` |

### 6.5 日志输出格式（stdout）

```
===== hifiti 签到报告 2026-04-19 01:00:00 =====
✅ a@x.com  已签到 (+5 金币)
✅ b@y.com  已签到
❌ c@z.com  登录失败: 响应无 bbs_token (status=200)
============================================
```

---

## 7. 错误处理矩阵

| 错误类型 | 行为 | 退出码 | 飞书通知 |
|---|---|---|---|
| 环境变量缺失 | 打印错误 | 1 | ✅ |
| 账号/密码数量不一致 | 打印错误 | 1 | ✅ |
| 账号数超过 10 | 打印错误 | 1 | ✅ |
| 网络错误（某账号） | 重试 1 次，仍失败记录该账号失败 | 视总体而定 | 若导致总体失败则 ✅ |
| 登录失败（某账号） | 记录 `LOGIN_FAIL` | 视总体而定 | 若导致总体失败则 ✅ |
| 签到失败（某账号） | 记录 `SIGN_FAIL` | 视总体而定 | 若导致总体失败则 ✅ |
| 签到返回"已签到" | 记录 OK | 视总体而定 | ❌ |
| 全部成功 | | 0 | ❌ |
| 任一失败 | | 1 | ✅ |

**部分成功部分失败的策略**：按 **A 策略**——任一账号失败即整体 `exit(1)`，便于及时发现问题账号。

---

## 8. 部署流程

```
1. 前置检查
   ├─ Docker + Docker Compose 已安装
   └─ 端口 5700 未被占用

2. 生成项目骨架
   └─ 创建 docker-compose.yml / .gitignore / scripts/ 等

3. 启动青龙
   └─ docker compose up -d

4. 首次配置（浏览器 http://<host>:5700）
   ├─ 设置管理员账号/密码
   ├─ 通知设置 → 飞书机器人 → 填 webhook URL + 勾选"仅失败时通知"
   ├─ 环境变量 →
   │    HIFITI_ACCOUNT  = your@email.com
   │    HIFITI_PASSWORD = yourpassword
   └─ 定时任务 → 新建：
        名称:   hifiti 签到
        命令:   task autosign/hifiti_sign.py
        定时:   0 1 * * *   (每天 01:00)

5. 手动触发一次验证
   └─ 点"运行"，确认日志显示签到成功、飞书无误报

6. 完成
```

---

## 9. 运维

| 场景 | 操作 |
|---|---|
| 改账号/密码 | 青龙 UI → 环境变量页面，无需改代码、无需重启 |
| 查看历史日志 | 青龙 UI → 定时任务 → 点任务名 → 日志页 |
| 失败排查 | 飞书收到通知 → 打开青龙对应日志 → 看 `LOGIN_FAIL` / `SIGN_FAIL` |
| 停止服务 | `docker compose down`（数据保留在 `./ql/data`） |
| 升级青龙 | `docker compose pull && docker compose up -d` |

**.gitignore：**

```
ql/
__pycache__/
*.pyc
```

---

## 10. 健康自检清单

实现完毕后逐项验证：

- [ ] `docker ps` 看到 qinglong 容器 Running
- [ ] 浏览器能打开 `http://<host>:5700` 并登录
- [ ] 环境变量和定时任务在 Web UI 中正确显示
- [ ] 手动触发 `hifiti_sign.py`，日志显示"签到成功"
- [ ] 故意填错密码再触发一次，飞书能收到失败通知
- [ ] 成功时飞书**不**发通知（确认通知策略正确）
- [ ] 等真实 01:00 自动触发一次，次日确认执行日志存在
- [ ] 重启容器 (`docker compose restart`)，环境变量与定时任务仍在

---

## 11. 第二期需求（本次不实现，留档）

**目标：** 每天从"人工队列"取一个 hifiti 帖子 URL → 自动回帖 → 解析网盘链接 → 自动下载到指定目录。规模：**每天 1 账号 / 1 帖 / 1 次下载**。

### 11.1 接口（已抓包）

**回帖接口：**

```
POST https://www.hifiti.com/post-create-{tid}-{page}.htm
（示例：post-create-69745-1.htm）
Content-Type: application/x-www-form-urlencoded
Body 字段:
  doctype=1
  return_html=1
  quotepid=0
  message=<回帖内容>
认证: 复用登录 Session (bbs_token cookie)
```

**帖子正文（回帖后获取）：**

```
GET https://www.hifiti.com/thread-{tid}-1.htm?sort=asc
夸克链接正则: <a href="https://pan.quark.cn/s/\w+" target="_blank">
百度链接正则: <a href="https://pan.baidu.com/s/\w+" target="_blank">
提取码位置: <div class="alert alert-success">\w+</div>
```

### 11.2 网盘下载工具

| 网盘 | 推荐工具 | 说明 |
|---|---|---|
| 夸克 | [Cp0204/quark-auto-save](https://github.com/Cp0204/quark-auto-save) | 转存到自己夸克账号；或直接下载 |
| 百度 | [qjfoidnh/BaiduPCS-Go](https://github.com/qjfoidnh/BaiduPCS-Go) | CLI，需用户自己的百度 cookie；非会员限速严重 |

### 11.3 待定决策（第二期开工前再讨论）

- **队列存储方式**：纯文本文件 / 青龙环境变量 / 独立 Web 表单
- **回复内容策略**：固定候选池随机 / 按帖子标题模板化
- **下载目录结构**：按艺术家/专辑归档，具体规则待定
- **下载完成后的通知策略**
- **公网访问**：nginx 反代 + Let's Encrypt + basic auth（届时与下载功能一起上线）

### 11.4 第二期脚本预留命名

`scripts/hifiti_reply_download.py`（或拆分为 `hifiti_reply.py` + `downloader.py`）

---

## 12. 非目标（明确不做）

- 不实现 cookie 持久化（每次重新登录，简单可靠）
- 不实现多账号并发（顺序 + 间隔足够快）
- 不在脚本内写飞书 SDK（用青龙内置）
- 第一期不暴露公网访问（仅 `localhost:5700`）
- 第一期不支持账号数 > 10（防止滥用风险）
- 不自动扫描论坛帖子（第二期也只从人工队列取）
