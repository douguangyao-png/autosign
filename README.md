# Autosign — 定时签到平台

基于**青龙面板**（Docker/Podman 部署）的个人定时签到/自动化脚本平台。第一期实现 hifiti 论坛每日签到；后续任务在 `scripts/` 目录追加即可。

完整设计见 [`docs/superpowers/specs/2026-04-19-autosign-design.md`](docs/superpowers/specs/2026-04-19-autosign-design.md)。

---

## 目录结构

```
autosign/
├── docker-compose.yml          # 青龙服务定义
├── scripts/                    # 脚本（挂载进容器）
│   ├── hifiti_sign.py          # 第一期：hifiti 签到
│   ├── requirements.txt
│   └── README.md
├── ql/data/                    # 青龙运行数据（自动生成，已 gitignore）
└── docs/                       # 设计文档
```

---

## 部署

### 前置

- Linux 主机（已验证 Rocky Linux 9）
- Podman（推荐，RHEL 家族默认）或 Docker
- 端口 5700 可用

### 启动

```bash
# Rocky/RHEL 系（Podman）
cd /root/autosign
podman-compose up -d

# 或使用 Docker
docker compose up -d
```

### 访问 Web UI

**公网 HTTPS 入口**：<https://task.aipnm.net>

架构：
```
浏览器 ─HTTPS──► nginx (443, Let's Encrypt) ─HTTP──► 127.0.0.1:5700 (qinglong)
```

- 青龙绑定 `127.0.0.1:5700`，公网 5700 不可直连
- 证书自动续期（certbot systemd timer）
- nginx 配置：`/etc/nginx/conf.d/task.conf`

### 首次配置

浏览器打开 <https://task.aipnm.net>：

1. **设置管理员账号/密码**（首次访问自动进入引导页）

2. **通知设置**（系统设置 → 通知设置 → 飞书机器人）
   - 填入飞书自定义机器人 webhook URL
   - 勾选"仅失败时通知"

3. **环境变量**（配置 → 环境变量 → 新建）

   | 名称 | 值示例 | 说明 |
   |---|---|---|
   | `HIFITI_ACCOUNT` | `you@example.com` | hifiti 账号/邮箱（多账号用 `&` 分隔） |
   | `HIFITI_PASSWORD` | `yourpassword` | 密码（多密码用 `&` 分隔） |

   > **密码不能含 `&` 字符**（会被当作分隔符）。如含 `&`，请先改密码。

4. **定时任务**（配置 → 定时任务 → 新建）

   | 字段 | 值 |
   |---|---|
   | 名称 | `hifiti 签到` |
   | 命令 | `task autosign/hifiti_sign.py` |
   | 定时 | `0 1 * * *` |

5. **手动跑一次验证** — 点任务右侧"运行"按钮，查看日志应显示签到成功。

---

## 日常运维

| 场景 | 操作 |
|---|---|
| 改账号/密码 | Web UI → 环境变量页面 |
| 查看历史日志 | Web UI → 定时任务 → 点任务名 |
| 失败排查 | 飞书收到通知后，打开对应任务日志 |
| 停止服务 | `podman-compose down`（数据保留） |
| 升级青龙 | `podman-compose pull && podman-compose up -d` |

---

## 规划

- **第一期** ✅ 本次：hifiti 登录 + 签到 + 飞书失败通知
- **第二期** 📅 后续：hifiti 自动回帖 + 夸克/百度网盘下载 + 公网 nginx 反代

详见设计文档第 11 节。
