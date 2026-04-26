# Autosign 第二期 — hifiti 日榜自动采集设计文档

- **日期**：2026-04-26
- **作者**：Claude（整理）
- **范围**：第二期（hifiti 日榜自动回帖 + 网盘信息采集，**暂不下载**）
- **承接**：[2026-04-19-autosign-design.md](2026-04-19-autosign-design.md)

---

## 1. 背景与目标

第一期已实现 hifiti 每日签到。第二期目标变更（相对一期 §11）：

- **不再人工排队** → 自动按日榜抓取
- **不下载** → 仅采集元数据（歌曲名/演唱者/网盘 URL/提取码）
- **多源记录** → SQLite 主库 + 飞书多维表格（Bitable）长期归档
- **自动回帖** → 解锁帖子里的网盘链接（hifiti 业务逻辑要求）

---

## 2. 核心决策（最终态）

| # | 决策 | 选定 |
|---|---|---|
| 1 | 数据源 | hifiti 日榜 `https://www.hifiti.com/index-0-5.htm` |
| 2 | 每次处理量 | Top 2（按合规音乐帖计数，跳过的不占配额） |
| 3 | 主存储 | SQLite (`scripts/records.db`) |
| 4 | 二级归档 | 飞书 Bitable（缺配置时自动跳过，不报错） |
| 5 | 回帖内容 | 帖子页歌词随机一行；无歌词则 fallback 到固定话术池（无 emoji） |
| 6 | tid 去重 | UNIQUE 约束 |
| 7 | (歌名+演唱者) 去重 | UNIQUE 约束（B1：同曲同人即跳过） |
| 8 | 通知 | 每次都发汇总卡片（复用青龙通知系统，stdout Markdown） |
| 9 | 无网盘链接帖 | 丢弃不记录，飞书消息标 NO_LINK |
| 10 | Bitable 写入失败 | 最终一致：SQLite `synced_to_bitable=0` 待下次补传 |
| 11 | 排除英文歌 | song_name 与 artist 都不含 CJK → 跳过 |
| 12 | 调度 | 21:00 主跑 + 21:30/22:00/22:30/23:00/23:30 重试，24:00 后停 |
| 13 | 完成判定 | 当天 SQLite 新增 = 2 条 |
| 14 | 重试粒度 | 每次重新抓日榜（去重表自动跳过已成功） |
| 15 | 代码组织 | 抽 `hifiti_common.py` 共享模块，Phase 1 同步改 import |

---

## 3. 系统架构

```
scripts/
├── hifiti_common.py             # 新增：共享 session/login/retry/snippet/mask
├── hifiti_sign.py               # 改：仅改 import，行为不变
├── hifiti_rank_collect.py       # 新增：第二期主脚本
├── records.db                   # 新增：SQLite 数据库（运行时数据，不入库）
├── .rank_collect.lock           # 新增：文件锁（防 21:00 双触发并发）
├── requirements.txt             # 加 1 行（无新依赖；requests 已在）
└── README.md                    # 加 Phase 2 章节

docs/superpowers/specs/
├── 2026-04-19-autosign-design.md
└── 2026-04-26-autosign-phase2-design.md   # 本文
```

`hifiti_common.py` 暴露的函数（来自 Phase 1 提取）：

```python
build_session() -> requests.Session
request_with_retry(session, method, url, **kwargs) -> requests.Response
login(session, account, password) -> tuple[bool, str]
snippet(text, max=500) -> str
mask(account) -> str
# 常量：BASE_URL, USER_AGENT, REQUEST_TIMEOUT, NETWORK_RETRIES, RETRY_DELAY
```

---

## 4. 单次运行流程

```
┌──────────────────────────────────────────────────────────────┐
│ hifiti_rank_collect.py main()                                │
└──────────────────────────────────────────────────────────────┘
        ↓
[1] 读环境变量
    HIFITI_ACCOUNT / HIFITI_PASSWORD（取首个账号）
    BITABLE_*（缺则禁用 Bitable 步骤）
        ↓
[2] 打开 SQLite, 确保 schema, 查 today_count
    if today_count >= 2:
        print("已完成"), exit(0)        # 任务 B 在主跑成功后静默退出
        ↓
[3] login(session, acct, pwd)            # 复用 hifiti_common
    失败 → exit(1)
        ↓
[4] GET https://www.hifiti.com/index-0-5.htm
    解析帖子卡片列表 → [(tid, title), ...]
    抓取失败 → exit(1)
        ↓
[5] for (tid, title) in candidates:
        if today_new_count >= 2: break

        a) 标题黑名单关键词命中 → SKIP_BLOCKED
        b) 正则解析 (artist, song_name)，失败 → SKIP_INVALID_TITLE
        c) 英文歌（无 CJK）→ SKIP_ENGLISH
        d) SQLite 查 tid → 命中 SKIP_DEDUP_TID
        e) SQLite 查 (song_name, artist) → 命中 SKIP_DEDUP_SONG
        f) GET thread-{tid}-1.htm → 取歌词 → 选回帖内容
        g) POST post-create-{tid}-1.htm 回帖
           失败 → FAIL_REPLY
        h) GET thread-{tid}-1.htm（回帖后有链接）→ 解析夸克/百度 + 提取码
        i) 任一网盘链接全无 → SKIP_NO_LINK
        j) INSERT records (synced_to_bitable=0)
           today_new_count += 1
        ↓
[6] Bitable 同步：扫 synced_to_bitable=0 行
    若 BITABLE_* 任一缺失 → 跳过（不算失败）
    取 tenant_access_token → 逐行 POST records
    成功 → UPDATE synced_to_bitable=1
    失败 → log，不更新（下次再补）
        ↓
[7] print 汇总卡片到 stdout（青龙转发飞书）
        ↓
[8] today_count = SELECT count WHERE date(created_at)=today
    exit(0) if today_count >= 2 else exit(1)
```

**关键不变量：**

- 跳过的帖（dedup/blocked/english/no_link）**不占用 Top 2 配额**
- 失败的帖（reply_fail）也**不占用 Top 2 配额**（下次重试任务会重新尝试）
- `today_count` 读的是当天**任何来源**新增的记录（包括之前主跑产生的）

---

## 5. SQLite Schema

数据库文件：`scripts/records.db`（运行时数据，不入版本库）

```sql
CREATE TABLE IF NOT EXISTS records (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    tid               INTEGER NOT NULL UNIQUE,        -- hifiti 帖子 ID
    title             TEXT    NOT NULL,                -- 原始标题
    song_name         TEXT    NOT NULL,                -- 解析出歌曲名
    artist            TEXT    NOT NULL,                -- 解析出演唱者
    post_url          TEXT    NOT NULL,                -- thread-{tid}-1.htm 全 URL
    quark_url         TEXT,                            -- 可空
    quark_password    TEXT,                            -- 可空
    baidu_url         TEXT,                            -- 可空
    baidu_password    TEXT,                            -- 可空
    reply_content     TEXT    NOT NULL,                -- 实发回帖内容（审计用）
    created_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    synced_to_bitable INTEGER NOT NULL DEFAULT 0       -- 0=未同步 1=已同步
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_song_artist ON records(song_name, artist);
CREATE INDEX IF NOT EXISTS idx_synced  ON records(synced_to_bitable);
CREATE INDEX IF NOT EXISTS idx_created ON records(created_at);
```

**约束语义：**

- 至少有一个网盘链接才会 INSERT（`SKIP_NO_LINK` 不入库）
- `quark_url` 与 `baidu_url` 至少一非空（业务约束，非 SQL 约束，由代码保证）
- `quark_password` 仅在 `quark_url` 非空时有意义；百度同理

---

## 6. 解析算法

### 6.1 日榜 → tid 列表

```
GET https://www.hifiti.com/index-0-5.htm
（实测校准：用 BeautifulSoup 或正则定位帖子卡片，提取 href="thread-{tid}-1.htm"）
保序返回 [(tid, title), ...]
```

### 6.2 标题 → (artist, song_name)

按优先级匹配：

| # | 正则 | 例 |
|---|---|---|
| 1 | `^\[.*?\]\s*(.+?)\s*[-–—]\s*(.+?)\s*\[.*?\]$` | `[流行] 周杰伦 - 七里香 [WAV]` |
| 2 | `^(.+?)\s*[-–—]\s*(.+?)$` | `周杰伦 - 七里香` |

匹配规则：第一捕获组 = artist，第二捕获组 = song_name。

**都不匹配** → `SKIP_INVALID_TITLE`，不进库。

**先过黑名单**：

```python
TITLE_BLOCKLIST = (
    "说明", "公告", "通知", "版规", "活动", "置顶",
    "积分", "VIP", "使用问题", "砥砺前行",
)
```

任意关键词出现在标题中 → `SKIP_BLOCKED`。

### 6.3 英文歌过滤

```python
import re
CJK_RE = re.compile(r'[\u4e00-\u9fff]')

def is_english_song(song_name: str, artist: str) -> bool:
    return not CJK_RE.search(song_name) and not CJK_RE.search(artist)
```

边界用例：

| 标题 | song_name | artist | 判定 |
|---|---|---|---|
| `周杰伦 - Hello` | Hello | 周杰伦 | 保留（artist 含 CJK） |
| `Adele - 转载` | 转载 | Adele | 保留（song 含 CJK） |
| `Adele - Hello` | Hello | Adele | 跳过（都不含） |
| `Beyond - 海阔天空` | 海阔天空 | Beyond | 保留 |

### 6.4 帖子页歌词提取

GET `thread-{tid}-1.htm?sort=asc`。

```
1. 拿到主楼正文 HTML
2. 优先找 <pre>/<blockquote>/<div> 内超过 5 行的纯文本块
3. 按 \n 切行，过滤空行和 ≤ 2 字符的行
4. 随机选一行作为回帖内容
5. 找不到符合的歌词块 → 用固定话术池
```

**固定话术池**（无 emoji，10 条）：

```python
FALLBACK_REPLIES = [
    "感谢分享",
    "好听，支持",
    "谢谢楼主",
    "经典好歌",
    "下载收藏",
    "怀念这首",
    "音质不错",
    "支持楼主",
    "感谢上传",
    "好歌一首",
]
```

### 6.5 网盘链接 + 提取码提取

回帖后 GET `thread-{tid}-1.htm?sort=asc` 拿带链接的正文。

正则（基于 Phase 1 §11.1 抓包）：

```python
QUARK_RE = re.compile(r'href="(https://pan\.quark\.cn/s/\w+)"')
BAIDU_RE = re.compile(r'href="(https://pan\.baidu\.com/s/\w+)"')
PWD_RE   = re.compile(r'<div class="alert alert-success">(\w+)</div>')
```

**提取码归属**（实施前实测确认）：

- 默认假设：夸克和百度共用同一个提取码 → 两边填同一个
- 若实测发现各有提取码 → 改为按链接位置上下文归属（紧邻链接的 alert）

---

## 7. 回帖请求规格

**接口**（Phase 1 §11.1 已抓包）：

```
POST https://www.hifiti.com/post-create-{tid}-1.htm
Content-Type: application/x-www-form-urlencoded
Body:
  doctype=1
  return_html=1
  quotepid=0
  message=<回帖内容>
Cookie: 复用 login 后的 bbs_token
```

**请求参数：**

| 项 | 值 |
|---|---|
| User-Agent | 同 Phase 1（Android Chrome UA） |
| Referer | `https://www.hifiti.com/thread-{tid}-1.htm` |
| Timeout | 20s（连接 + 读取） |
| 重试 | 网络错误 1 次（间隔 3s）；业务错误不重试 |
| 帖子间隔 | 随机 2.0~5.0 秒（比签到的 1.5~3 略宽，多账号场景不存在） |

**成功判定**（实施前实测校准关键字）：

- 响应 200 + 包含"成功"字样 → 成功
- 响应包含"间隔"/"过快"/"重复"等 → 业务失败
- 其它非 200 → 网络/业务失败

---

## 8. 飞书集成

### 8.1 通知（青龙转发）

脚本 print Markdown 到 stdout，青龙任务设置"成功+失败都通知"。

**输出模板**：

```
===== hifiti 日榜采集 {YYYY-MM-DD HH:MM:SS} =====
触发：{主跑 / 重试 attempt N/6}
完成进度：{today_count}/2 已采集

新增记录：
1. {artist} - {song_name}
   帖子: {post_url}
   夸克: {quark_url}  提取码: {quark_password}
   百度: {baidu_url}  提取码: {baidu_password}

跳过 (n)：
- tid={tid} [SKIP_DEDUP_TID]   ...
- tid={tid} [SKIP_DEDUP_SONG]  ...
- tid={tid} [SKIP_BLOCKED]     ...
- tid={tid} [SKIP_INVALID_TITLE] ...
- tid={tid} [SKIP_ENGLISH]     ...
- tid={tid} [SKIP_NO_LINK]     ...

失败 (n)：
- tid={tid} [FAIL_REPLY]   POST status={code}
- tid={tid} [FAIL_PARSE]   ...

Bitable 同步：{n} 行新写入成功 / {m} 行历史补传 / {k} 行失败
（或："Bitable 未配置，跳过"）
============================================
```

### 8.2 Bitable 写入

**鉴权**：每次跑现取 `tenant_access_token`（有效 2h，不缓存）

```
POST https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal
Body (JSON): {"app_id": "{BITABLE_APP_ID}", "app_secret": "{BITABLE_APP_SECRET}"}
→ 取 .tenant_access_token
```

**插入记录**：

```
POST https://open.feishu.cn/open-apis/bitable/v1/apps/{BITABLE_APP_TOKEN}/tables/{BITABLE_TABLE_ID}/records
Header: Authorization: Bearer {tenant_access_token}
Body (JSON):
{
  "fields": {
    "歌曲名": "{song_name}",
    "演唱者": "{artist}",
    "帖子链接": {"link": "{post_url}", "text": "{title}"},
    "夸克链接": {"link": "{quark_url}", "text": "{quark_url}"},
    "夸克提取码": "{quark_password}",
    "百度链接": {"link": "{baidu_url}", "text": "{baidu_url}"},
    "百度提取码": "{baidu_password}",
    "抓取时间": {epoch_ms}
  }
}
```

**Bitable 列定义**（你在飞书表格里建表时按此命名）：

| 列名 | 类型 | 备注 |
|---|---|---|
| 歌曲名 | 文本 | |
| 演唱者 | 文本 | |
| 帖子链接 | 超链接 | text=原始标题 |
| 夸克链接 | 超链接 | 可空 |
| 夸克提取码 | 文本 | 可空 |
| 百度链接 | 超链接 | 可空 |
| 百度提取码 | 文本 | 可空 |
| 抓取时间 | 日期时间 | epoch_ms |

### 8.3 同步策略（最终一致）

```python
def sync_pending_to_bitable():
    if not all([BITABLE_APP_ID, BITABLE_APP_SECRET, BITABLE_APP_TOKEN, BITABLE_TABLE_ID]):
        log("BITABLE 未配置，跳过")
        return SyncResult(skipped=True)

    token = get_tenant_access_token()
    rows = SELECT * FROM records WHERE synced_to_bitable=0
    ok, fail = 0, 0
    for row in rows:
        try:
            bitable_insert(token, row)
            UPDATE records SET synced_to_bitable=1 WHERE id=row.id
            ok += 1
        except Exception as e:
            log(f"BITABLE_FAIL tid={row.tid}: {e}")
            fail += 1
    return SyncResult(ok=ok, fail=fail)
```

**Bitable 失败不影响 exit code**——脚本最终 exit 由"今日 SQLite 是否到 2 条"决定。

---

## 9. 错误矩阵 + 退出码

### 9.1 错误分类

| 错误来源 | 行为 | 计入 |
|---|---|---|
| 环境变量缺失（HIFITI_*） | 立即退出 | — |
| 登录失败 | 立即退出 | — |
| 日榜抓取失败（网络/HTML 变更） | 立即退出 | — |
| 帖子页 GET 失败 | 跳过该帖 | 失败 |
| 回帖 POST 失败 | 跳过该帖 | 失败 |
| 回帖响应判定为业务失败（"过快"等） | 跳过该帖 | 失败 |
| 解析不到任一网盘链接 | 跳过该帖 | 跳过 NO_LINK |
| 标题黑名单 | 跳过该帖 | 跳过 BLOCKED |
| 标题正则不命中 | 跳过该帖 | 跳过 INVALID_TITLE |
| 英文歌 | 跳过该帖 | 跳过 ENGLISH |
| tid 已存在 | 跳过该帖 | 跳过 DEDUP_TID |
| (song,artist) 已存在 | 跳过该帖 | 跳过 DEDUP_SONG |
| Bitable 鉴权/写入失败 | 记日志、不更新 synced 标志 | — |
| 顶层未捕获异常 | print traceback, exit(1) | — |

### 9.2 退出码

```python
today_count = SELECT count(*) FROM records
              WHERE date(created_at, 'localtime') = date('now', 'localtime')
exit(0 if today_count >= 2 else 1)
```

**关键**：`created_at` 是 UTC（SQLite `CURRENT_TIMESTAMP` 默认），必须两边都套 `'localtime'` 转东八区再 `date(...)` 截日期，否则跨 UTC 0 点（北京 08:00）会判错。

| 场景 | exit |
|---|---|
| 当天已 ≥ 2 条记录（任何任务进入即退） | 0 |
| 主跑后 ≥ 2 条 | 0 |
| 主跑后 < 2 条（部分成功 / 全失败） | 1 |
| 配置/登录/日榜致命错误 | 1 |

退出码 1 时青龙触发飞书"失败"通知（这是预期行为：你能感知到当天进度未达成）。

### 9.3 时区一致性

容器 `TZ=Asia/Shanghai`，但 SQLite `CURRENT_TIMESTAMP` 不受 TZ env 影响，永远是 UTC。所有"今天"判断（`§4 [2]`、`§9.2`、§4 [8]）必须**两边都套 `'localtime'`** 后再 `date(...)`，统一到东八区。

```sql
-- 正确
WHERE date(created_at, 'localtime') = date('now', 'localtime')
-- 错误（混用 UTC 和本地）
WHERE date(created_at) = date('now', 'localtime')
```

---

## 10. 调度（青龙任务）

新增 **2 个青龙定时任务**：

| 任务 | cron | 命令 | 通知模式 |
|---|---|---|---|
| `hifiti 日榜采集 (主)` | `0 21 * * *` | `task autosign/hifiti_rank_collect.py` | 成功+失败都通知 |
| `hifiti 日榜采集 (重试)` | `*/30 21-23 * * *` | `task autosign/hifiti_rank_collect.py` | 仅失败通知 |

> cron `*/30 21-23` = 21:00, 21:30, 22:00, 22:30, 23:00, 23:30 各一次。

### 10.1 21:00 双触发竞态

主跑（`0 21 * * *`）和重试（`*/30 21-23`）在 21:00:00 同时被青龙调度，会并发跑两个进程，对同一账号同时登录/抓榜/回帖 → 风险。

**解决：进程级文件锁**

脚本启动时用 `fcntl.flock` 抢 `scripts/.rank_collect.lock`：

```python
import fcntl
lock_fd = open("/ql/data/scripts/autosign/.rank_collect.lock", "w")
try:
    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    print("已有同名进程在跑，本次跳过")
    sys.exit(0)
```

后启动者立即 exit(0) 静默退出（不当失败处理，避免 21:00 重试任务发出误报）。

### 10.2 重试任务"仅失败通知"

避免每次重试都刷屏；只有当 24:00 前一直没达成 2 条时，最后一次重试的失败会触发通知。

---

## 11. 环境变量总览

| 名称 | 必须 | 说明 |
|---|---|---|
| `HIFITI_ACCOUNT` | ✅ | 复用 Phase 1，取首个账号 |
| `HIFITI_PASSWORD` | ✅ | 同上 |
| `BITABLE_APP_ID` | ❌ | 飞书自建应用 ID |
| `BITABLE_APP_SECRET` | ❌ | 飞书自建应用 Secret |
| `BITABLE_APP_TOKEN` | ❌ | 多维表格 app_token |
| `BITABLE_TABLE_ID` | ❌ | 表格 ID |

四个 BITABLE_* 任一缺失 → 跳过 Bitable 同步步骤，不报错。

---

## 12. 实施前实测校准清单

实施期间按此顺序抓真实数据校准代码常量：

1. **日榜页结构** — `curl https://www.hifiti.com/index-0-5.htm`，确认帖子列表选择器/正则
2. **标题样本** — 抓 5 条真实标题，验证 §6.2 两条正则的命中率
3. **帖子正文 + 歌词块结构** — 抓 5 个真实音乐帖，定位歌词所在 HTML 标签
4. **网盘链接 + 提取码位置** — 抓 5 帖确认夸克/百度是否共用提取码
5. **回帖响应判定关键字** — POST `/post-create-{tid}-1.htm` 看响应，定位"成功"/"过快"/"重复"等关键词

每项校准后写入代码常量并加针对性测试。

---

## 13. 测试策略

### 13.1 单元测试

- `parse_title()` — 准备 ~5 条真实标题作 fixture，验证正则匹配
- `is_english_song()` — 边界用例覆盖（§6.3 表）
- `extract_lyrics()` — 给定 HTML fragment，验证歌词行提取
- `parse_pan_links()` — 给定 HTML，验证 quark/baidu URL + 提取码
- `pick_reply_content()` — 有歌词 → 返回歌词行；无歌词 → 返回话术池中某条

### 13.2 集成测试（手动）

运行环境：青龙容器内，使用真实 hifiti 账号

1. 清空 `records.db`，手动跑一次 → 应有 2 条新记录 + 飞书消息
2. 立即再跑一次 → today_count=2，立即 exit(0)，飞书消息显示"已完成"
3. 故意改坏密码再跑 → 登录失败 exit(1) + 飞书报警
4. 改回密码，但 Bitable 故意填错 token → Bitable 失败但脚本 exit(0) + 飞书消息显示"Bitable 同步 0/2"
5. 修复 Bitable，再跑 → 之前未同步的行被补传

### 13.3 验收清单

- [ ] `hifiti_common.py` 抽取后，Phase 1 签到任务仍然成功
- [ ] 主跑产生 2 条新记录写入 SQLite
- [ ] 飞书收到汇总卡片，链接可点击
- [ ] Bitable 看到对应行
- [ ] 故意填错密码 → 飞书收到失败通知
- [ ] 第二天 21:00 自动触发，跑出新的 2 条
- [ ] 日榜上某帖出现两次（连续两天）→ 第二次被去重跳过
- [ ] 日榜出现一首英文歌 → SKIP_ENGLISH，不占配额

---

## 14. 非目标（明确不做）

- 不下载任何文件（夸克/百度均不下载）
- 不爬非日榜（不做月榜/总榜/最新发布）
- 不做多账号回帖（仅用首账号）
- 不做歌词外部 API 查询（不接网易云/QQ 音乐）
- 不实现帖子内容差异比对（同 tid 不会被重复处理）
- 不暴露任何 Web UI（沿用青龙）

---

## 15. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| hifiti 反爬升级（验证码/封号） | 任务全失败 | 单账号、随机间隔、保持 Android UA；每次失败青龙告警 |
| 同账号回帖过快被禁言 | 24 小时无法回帖 | 帖子间隔 2~5 秒；若回帖响应含"过快"立即 break |
| 飞书 Bitable Schema 漂移 | 写入 400 错误 | 最终一致策略下次自动补；列名硬编码，文档明示 |
| SQLite 文件损坏 | 失去去重能力 | `scripts/records.db` 通过 podman volume 持久化；可加每日 .bak |
| 标题正则覆盖率低 | 大量 SKIP_INVALID_TITLE | 实测校准期收集 fixture，命中率 < 90% 加规则 |
| 回帖响应关键字不准 | 误判成功/失败 | 实测校准；保留 stdout 日志便于事后查证 |

---

## 16. 后续（Phase 3 候选，本次不做）

- 接入网盘下载（夸克 quark-auto-save 转存 / 百度 BaiduPCS-Go）
- 多日榜来源（月榜补充）
- Web 仪表盘（看采集历史趋势）
- LLM 辅助回帖内容生成
