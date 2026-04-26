# scripts/

放置所有定时任务脚本。通过 `docker-compose.yml` 挂载到容器内 `/ql/data/scripts/autosign/`。

## 当前脚本

| 文件 | 说明 | 调用命令（青龙内） |
|---|---|---|
| `hifiti_sign.py` | hifiti 论坛每日签到（支持多账号） | `task autosign/hifiti_sign.py` |

## 添加新脚本

1. 在本目录新建 `.py` / `.js` / `.sh` 文件
2. 若需要 Python 依赖，加入 `requirements.txt` 并在青龙"依赖管理"中安装
3. 在青龙 Web UI 新建定时任务，命令写 `task autosign/<filename>`
