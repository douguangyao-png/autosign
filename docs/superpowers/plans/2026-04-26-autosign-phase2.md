# Autosign Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build hifiti 日榜自动采集脚本 — 每天 21:00 起每 30 分钟跑一次，对日榜 Top 2 合规音乐帖自动回帖、解析夸克/百度网盘链接 + 提取码、写入 SQLite + 飞书 Bitable，直到当天采到 2 条为止。

**Architecture:** 单脚本入口 (`hifiti_rank_collect.py`) + 内部模块化（`hifiti_common`/`hifiti_parsing`/`hifiti_storage`/`hifiti_bitable`/`hifiti_reporting`）。Phase 1 的 `hifiti_sign.py` 改为复用 `hifiti_common` 的登录/会话工具。SQLite 文件锁兜底 21:00 双触发。

**Tech Stack:** Python 3.10+ • requests • sqlite3（标准库）• pytest（开发依赖）• unittest.mock（HTTP 桩）• 青龙面板 cron

**Spec:** `docs/superpowers/specs/2026-04-26-autosign-phase2-design.md`

---

## File Structure

```
scripts/
├── hifiti_common.py              # NEW: session/login/retry/snippet/mask
├── hifiti_parsing.py             # NEW: title/english/lyrics/pan_links/ranking
├── hifiti_storage.py             # NEW: SQLite DAO（schema, insert, dedup, today_count）
├── hifiti_bitable.py             # NEW: tenant_access_token + insert + sync
├── hifiti_reporting.py           # NEW: stdout markdown 汇总卡片
├── hifiti_rank_collect.py        # NEW: 主脚本（orchestrator + file lock + env）
├── hifiti_sign.py                # MODIFY: 改 import 用 hifiti_common
├── requirements.txt              # MODIFY: 无新增运行时依赖（已有 requests）
├── requirements-dev.txt          # NEW: pytest
└── README.md                     # MODIFY: 加 Phase 2 章节

tests/
├── __init__.py                   # NEW (空)
├── conftest.py                   # NEW: pytest 配置
├── test_hifiti_common.py         # NEW
├── test_hifiti_parsing.py        # NEW
├── test_hifiti_storage.py        # NEW
├── test_hifiti_bitable.py        # NEW
├── test_hifiti_reporting.py      # NEW
└── fixtures/
    ├── ranking.html              # NEW: 合成日榜 HTML 片段
    ├── thread_with_lyrics.html   # NEW
    ├── thread_no_lyrics.html     # NEW
    └── thread_no_pan.html        # NEW

pytest.ini                        # NEW: 测试配置
.gitignore                        # MODIFY: 加 tests/.pytest_cache/
```

---

## Task 1: 项目脚手架（pytest + 测试目录）

**Files:**
- Create: `scripts/requirements-dev.txt`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `tests/fixtures/.gitkeep`
- Create: `pytest.ini`
- Modify: `.gitignore`

- [ ] **Step 1: Create `scripts/requirements-dev.txt`**

```txt
pytest>=7.0
```

- [ ] **Step 2: Create `pytest.ini`**

```ini
[pytest]
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*
addopts = -ra --strict-markers
filterwarnings = ignore::DeprecationWarning
```

- [ ] **Step 3: Create `tests/__init__.py` (empty)**

```python
```

- [ ] **Step 4: Create `tests/conftest.py`**

```python
import sys
from pathlib import Path

# 让测试能 import scripts/ 下的模块
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
```

- [ ] **Step 5: Create `tests/fixtures/.gitkeep` (empty placeholder)**

```
```

- [ ] **Step 6: Append to `.gitignore`**

```
# pytest
.pytest_cache/
tests/__pycache__/
```

- [ ] **Step 7: Install pytest and verify discovery**

```bash
pip install -r scripts/requirements-dev.txt
cd /root/autosign && pytest --collect-only
```

Expected: `no tests ran` exit code 5 (no tests yet — that's fine).

- [ ] **Step 8: Commit**

```bash
git -C /root/autosign add scripts/requirements-dev.txt tests/ pytest.ini .gitignore
git -C /root/autosign commit -m "scaffold: pytest infrastructure for Phase 2"
```

---

## Task 2: 抽取 hifiti_common.py（共享会话/登录工具）

**Files:**
- Create: `scripts/hifiti_common.py`
- Create: `tests/test_hifiti_common.py`

- [ ] **Step 1: Write failing tests for `mask` and `snippet`**

Create `tests/test_hifiti_common.py`:

```python
from hifiti_common import mask, snippet


def test_mask_email_short_local_part():
    assert mask("ab@x.com") == "a***@x.com"


def test_mask_email_normal():
    assert mask("collin@example.com") == "col***@example.com"


def test_mask_username_short():
    assert mask("xy") == "x***"


def test_mask_username_normal():
    assert mask("collin") == "col***"


def test_snippet_truncates_to_500_chars():
    text = "a" * 800
    result = snippet(text)
    assert len(result) == 500


def test_snippet_strips_newlines():
    assert snippet("hello\nworld\n") == "hello world"


def test_snippet_handles_empty_or_none():
    assert snippet("") == ""
    assert snippet(None) == ""
```

- [ ] **Step 2: Run tests — expect ImportError**

```bash
cd /root/autosign && pytest tests/test_hifiti_common.py -v
```

Expected: ERRORS with `ModuleNotFoundError: No module named 'hifiti_common'`.

- [ ] **Step 3: Create `scripts/hifiti_common.py` with all shared helpers**

```python
"""共享 hifiti 客户端工具：session/login/retry/snippet/mask。

Phase 1 (hifiti_sign.py) 和 Phase 2 (hifiti_rank_collect.py) 都从这里导入。
"""
from __future__ import annotations

import time
from typing import Tuple

import requests

BASE_URL = "https://www.hifiti.com"
LOGIN_URL = f"{BASE_URL}/user-login.htm"

USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/147.0.0.0 Mobile Safari/537.36"
)

REQUEST_TIMEOUT = 20
NETWORK_RETRIES = 1
RETRY_DELAY = 3
MAX_LOG_SNIPPET = 500

LOGIN_ERROR_KEYWORDS = (
    "密码", "错误", "不存在", "不正确", "失败", "不匹配", "验证码",
)


def build_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
    )
    return s


def request_with_retry(
    session: requests.Session, method: str, url: str, **kwargs
) -> requests.Response:
    last_exc: Exception | None = None
    for attempt in range(NETWORK_RETRIES + 1):
        try:
            return session.request(method, url, timeout=REQUEST_TIMEOUT, **kwargs)
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < NETWORK_RETRIES:
                time.sleep(RETRY_DELAY)
    assert last_exc is not None
    raise last_exc


def snippet(text: str | None) -> str:
    if not text:
        return ""
    return text[:MAX_LOG_SNIPPET].replace("\n", " ").strip()


def mask(account: str) -> str:
    if "@" in account:
        name, _, domain = account.partition("@")
        if len(name) <= 2:
            return f"{name[:1]}***@{domain}"
        return f"{name[:3]}***@{domain}"
    if len(account) <= 3:
        return f"{account[:1]}***"
    return f"{account[:3]}***"


def login(session: requests.Session, account: str, password: str) -> Tuple[bool, str]:
    """登录 hifiti。返回 (是否成功, 状态描述)。成功后 session.cookies 含 bbs_token。"""
    try:
        session.get(
            LOGIN_URL,
            headers={"Referer": BASE_URL + "/"},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException:
        pass

    resp = request_with_retry(
        session,
        "POST",
        LOGIN_URL,
        data={"email": account, "password": password},
        headers={
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": BASE_URL,
            "Referer": LOGIN_URL,
            "X-Requested-With": "XMLHttpRequest",
        },
        allow_redirects=False,
    )

    if "bbs_token" in session.cookies:
        return True, f"status={resp.status_code}"

    body = snippet(resp.text)
    for kw in LOGIN_ERROR_KEYWORDS:
        if kw in body:
            return False, f"status={resp.status_code} msg='{body}'"
    return False, f"status={resp.status_code} 无 bbs_token cookie; body='{body}'"
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
cd /root/autosign && pytest tests/test_hifiti_common.py -v
```

Expected: 7 passed.

> ⚠️ Note: spec original `mask("collin@example.com")` returned `"co***@..."` in old hifiti_sign.py (only first 2 chars kept). Tests above use 3 chars (`col***`). Pick one and apply to both new helper and tests consistently. The implementation above uses 3 chars for emails ≥3 char local. **Verify against existing Phase 1 behavior before committing**: `grep -n "name\[:" scripts/hifiti_sign.py`.

If Phase 1 used 2-char prefix, change `mask` to:
```python
return f"{name[:2]}***@{domain}"
```
and update test:
```python
assert mask("collin@example.com") == "co***@example.com"
```

- [ ] **Step 5: Commit**

```bash
git -C /root/autosign add scripts/hifiti_common.py tests/test_hifiti_common.py
git -C /root/autosign commit -m "feat: extract hifiti_common.py with session/login/retry helpers"
```

---

## Task 3: hifiti_sign.py 迁移到 hifiti_common

**Files:**
- Modify: `scripts/hifiti_sign.py`

- [ ] **Step 1: Replace duplicated helpers with imports**

Replace the top of `scripts/hifiti_sign.py` (the constants + `build_session`/`request_with_retry`/`snippet`/`mask`/`login` functions) with imports from `hifiti_common`. Keep the sign-specific logic (`SIGN_URL`, `SIGN_SUCCESS_KEYWORDS`, `sign()`, `process_account()`, `parse_accounts()`, `print_report()`, `main()`) as-is.

Final structure of `hifiti_sign.py`:

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hifiti 论坛每日签到（Phase 1）

环境变量:
  HIFITI_ACCOUNT   账号/邮箱，多账号用 & 分隔 (最多 10 个)
  HIFITI_PASSWORD  密码，多密码用 & 分隔，数量与账号严格一致

退出码: 0=全部成功; 1=任一失败或配置错误
"""
from __future__ import annotations

import os
import random
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from typing import List, Tuple

import requests

from hifiti_common import (
    BASE_URL,
    REQUEST_TIMEOUT,
    build_session,
    login,
    mask,
    request_with_retry,
    snippet,
)

SIGN_URL = f"{BASE_URL}/sg_sign.htm"
SIGN_SUCCESS_KEYWORDS = ("成功", "已签", "已经", "奖励", "获得", "+")
SIGN_ERROR_KEYWORDS = ("未登录", "请登录", "请先登录", "登录失效", "登录已过期")

ACCOUNT_DELAY_MIN = 1.5
ACCOUNT_DELAY_MAX = 3.0
MAX_ACCOUNTS = 10
ACCOUNT_SEPARATOR = "&"


@dataclass
class AccountResult:
    account: str
    status: str
    detail: str


def sign(session: requests.Session) -> Tuple[bool, str]:
    resp = request_with_retry(
        session,
        "POST",
        SIGN_URL,
        headers={
            "Origin": BASE_URL,
            "Referer": BASE_URL + "/",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "text/plain, */*; q=0.01",
        },
        data=b"",
    )

    body = snippet(resp.text)

    if resp.status_code != 200:
        return False, f"status={resp.status_code} body='{body}'"

    for kw in SIGN_ERROR_KEYWORDS:
        if kw in body:
            return False, f"status={resp.status_code} msg='{body}'"

    for kw in SIGN_SUCCESS_KEYWORDS:
        if kw in body:
            return True, f"status={resp.status_code} msg='{body}'"

    return True, f"status={resp.status_code} (assumed success; body='{body}')"


def process_account(account: str, password: str) -> AccountResult:
    session = build_session()
    try:
        ok, detail = login(session, account, password)
        if not ok:
            return AccountResult(account, "LOGIN_FAIL", detail)

        ok, detail = sign(session)
        if not ok:
            return AccountResult(account, "SIGN_FAIL", detail)

        return AccountResult(account, "OK", detail)
    except requests.RequestException as exc:
        return AccountResult(account, "NETWORK_ERROR", f"{type(exc).__name__}: {exc}")


def parse_accounts() -> List[Tuple[str, str]]:
    accounts_env = os.environ.get("HIFITI_ACCOUNT", "").strip().strip(ACCOUNT_SEPARATOR)
    passwords_env = (
        os.environ.get("HIFITI_PASSWORD", "").strip().strip(ACCOUNT_SEPARATOR)
    )

    if not accounts_env or not passwords_env:
        raise SystemExit("[FATAL] 环境变量 HIFITI_ACCOUNT / HIFITI_PASSWORD 未设置")

    accounts = [a.strip() for a in accounts_env.split(ACCOUNT_SEPARATOR)]
    passwords = passwords_env.split(ACCOUNT_SEPARATOR)

    if len(accounts) != len(passwords):
        raise SystemExit(
            f"[FATAL] 账号数 ({len(accounts)}) 与密码数 ({len(passwords)}) 不一致"
        )
    if any(not a for a in accounts) or any(not p for p in passwords):
        raise SystemExit("[FATAL] 账号或密码存在空值")
    if not (1 <= len(accounts) <= MAX_ACCOUNTS):
        raise SystemExit(
            f"[FATAL] 账号数 {len(accounts)} 超出允许范围 1..{MAX_ACCOUNTS}"
        )

    return list(zip(accounts, passwords))


STATUS_ICON = {"OK": "✅", "LOGIN_FAIL": "❌", "SIGN_FAIL": "❌", "NETWORK_ERROR": "⚠️"}


def print_report(results: List[AccountResult]) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header = f"===== hifiti 签到报告 {now} ====="
    print(header)
    for r in results:
        icon = STATUS_ICON.get(r.status, "?")
        print(f"{icon} {mask(r.account)}  [{r.status}]  {r.detail}")
    print("=" * len(header))


def main() -> int:
    creds = parse_accounts()
    results: List[AccountResult] = []
    for i, (account, password) in enumerate(creds):
        if i > 0:
            time.sleep(random.uniform(ACCOUNT_DELAY_MIN, ACCOUNT_DELAY_MAX))
        results.append(process_account(account, password))
    print_report(results)
    return 0 if all(r.status == "OK" for r in results) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        print("[FATAL] 未捕获异常:")
        traceback.print_exc()
        sys.exit(1)
```

- [ ] **Step 2: Run regression — Phase 1 syntax + imports OK**

```bash
cd /root/autosign && python3 -c "import sys; sys.path.insert(0, 'scripts'); import hifiti_sign; print('import OK')"
```

Expected: `import OK`.

- [ ] **Step 3: Run pytest (all existing tests still green)**

```bash
cd /root/autosign && pytest -v
```

Expected: 7 passed.

- [ ] **Step 4: (Manual) Smoke test on container**

```bash
podman exec qinglong sh -c "cd /ql/data/scripts/autosign && python3 hifiti_sign.py"
```

Expected: Phase 1 sign-in still works (assuming HIFITI_ACCOUNT/PASSWORD env vars set in qinglong).

- [ ] **Step 5: Commit**

```bash
git -C /root/autosign add scripts/hifiti_sign.py
git -C /root/autosign commit -m "refactor: hifiti_sign.py imports shared helpers from hifiti_common"
```

---

## Task 4: hifiti_parsing.parse_title — 标题解析

**Files:**
- Create: `scripts/hifiti_parsing.py`
- Create: `tests/test_hifiti_parsing.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_hifiti_parsing.py`:

```python
import pytest
from hifiti_parsing import parse_title, ParsedTitle


def test_parse_title_with_brackets():
    r = parse_title("[流行] 周杰伦 - 七里香 [WAV]")
    assert r == ParsedTitle(artist="周杰伦", song="七里香", blocked=False, valid=True)


def test_parse_title_simple_dash():
    r = parse_title("周杰伦 - 七里香")
    assert r.artist == "周杰伦"
    assert r.song == "七里香"
    assert r.valid is True
    assert r.blocked is False


def test_parse_title_em_dash():
    r = parse_title("Adele — Hello")
    assert r.artist == "Adele"
    assert r.song == "Hello"
    assert r.valid is True


def test_parse_title_blocked_keyword():
    r = parse_title("[公告] 积分使用问题说明")
    assert r.blocked is True
    assert r.valid is False


def test_parse_title_blocked_song_post():
    r = parse_title("砥砺前行,不忘初心")
    assert r.blocked is True


def test_parse_title_no_separator_invalid():
    r = parse_title("某个不带破折号的标题")
    assert r.valid is False
    assert r.blocked is False
```

- [ ] **Step 2: Run tests — expect ImportError**

```bash
cd /root/autosign && pytest tests/test_hifiti_parsing.py::test_parse_title_simple_dash -v
```

Expected: ERRORS with `ModuleNotFoundError`.

- [ ] **Step 3: Create `scripts/hifiti_parsing.py` with parse_title**

```python
"""hifiti 页面解析工具：标题/英文歌/歌词/网盘链接/日榜列表。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

# ----- 标题黑名单 -----
TITLE_BLOCKLIST = (
    "说明", "公告", "通知", "版规", "活动", "置顶",
    "积分", "VIP", "使用问题", "砥砺前行",
)

# 正则按优先级匹配标题
_TITLE_RE_BRACKETED = re.compile(
    r"^\s*\[.*?\]\s*(?P<artist>.+?)\s*[-–—]\s*(?P<song>.+?)\s*\[.*?\]\s*$"
)
_TITLE_RE_SIMPLE = re.compile(r"^\s*(?P<artist>.+?)\s*[-–—]\s*(?P<song>.+?)\s*$")


@dataclass(frozen=True)
class ParsedTitle:
    artist: str
    song: str
    blocked: bool
    valid: bool


def parse_title(title: str) -> ParsedTitle:
    """解析帖子标题，返回 (artist, song, blocked, valid)。

    - blocked=True: 命中黑名单关键词，跳过且不视为合法标题
    - valid=True:  正则成功匹配出 (artist, song)
    """
    for kw in TITLE_BLOCKLIST:
        if kw in title:
            return ParsedTitle(artist="", song="", blocked=True, valid=False)

    for regex in (_TITLE_RE_BRACKETED, _TITLE_RE_SIMPLE):
        m = regex.match(title)
        if m:
            return ParsedTitle(
                artist=m.group("artist").strip(),
                song=m.group("song").strip(),
                blocked=False,
                valid=True,
            )

    return ParsedTitle(artist="", song="", blocked=False, valid=False)
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
cd /root/autosign && pytest tests/test_hifiti_parsing.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git -C /root/autosign add scripts/hifiti_parsing.py tests/test_hifiti_parsing.py
git -C /root/autosign commit -m "feat: parse_title with regex priority + blocklist"
```

---

## Task 5: hifiti_parsing.is_english_song — 英文歌检测

**Files:**
- Modify: `scripts/hifiti_parsing.py`
- Modify: `tests/test_hifiti_parsing.py`

- [ ] **Step 1: Append failing tests**

Append to `tests/test_hifiti_parsing.py`:

```python
from hifiti_parsing import is_english_song


def test_is_english_song_pure_english():
    assert is_english_song("Hello", "Adele") is True


def test_is_english_song_chinese_artist():
    assert is_english_song("Hello", "周杰伦") is False


def test_is_english_song_chinese_song():
    assert is_english_song("转载", "Adele") is False


def test_is_english_song_both_chinese():
    assert is_english_song("海阔天空", "Beyond") is False


def test_is_english_song_pure_chinese():
    assert is_english_song("七里香", "周杰伦") is False
```

- [ ] **Step 2: Run tests — expect ImportError**

```bash
cd /root/autosign && pytest tests/test_hifiti_parsing.py::test_is_english_song_pure_english -v
```

Expected: ERRORS — `cannot import name 'is_english_song'`.

- [ ] **Step 3: Append `is_english_song` to `hifiti_parsing.py`**

Add at module bottom (after `parse_title`):

```python
# ----- 英文歌检测 -----
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def is_english_song(song_name: str, artist: str) -> bool:
    """歌曲名和演唱者**都**不含 CJK 字符 → 视为英文歌。"""
    return not _CJK_RE.search(song_name) and not _CJK_RE.search(artist)
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
cd /root/autosign && pytest tests/test_hifiti_parsing.py -v
```

Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
git -C /root/autosign add scripts/hifiti_parsing.py tests/test_hifiti_parsing.py
git -C /root/autosign commit -m "feat: is_english_song based on CJK presence"
```

---

## Task 6: hifiti_parsing.extract_lyrics + pick_reply_content

**Files:**
- Modify: `scripts/hifiti_parsing.py`
- Modify: `tests/test_hifiti_parsing.py`
- Create: `tests/fixtures/thread_with_lyrics.html`
- Create: `tests/fixtures/thread_no_lyrics.html`

- [ ] **Step 1: Create test fixtures**

Create `tests/fixtures/thread_with_lyrics.html`:

```html
<html>
<body>
<table>
<tr><td class="t_f">
<div>
歌词分享：
<pre>
雨下整夜 我的爱溢出就像雨水
院子落叶 跟我的思念厚厚一叠
几句是非 也无法将我的热情冷却
你出现在我诗的每一页
</pre>
<p>夸克链接见下方</p>
</div>
</td></tr>
</table>
</body>
</html>
```

Create `tests/fixtures/thread_no_lyrics.html`:

```html
<html>
<body>
<table>
<tr><td class="t_f">
这是一段简短的描述文字。
没有歌词块。
</td></tr>
</table>
</body>
</html>
```

- [ ] **Step 2: Append failing tests**

Append to `tests/test_hifiti_parsing.py`:

```python
import random
from pathlib import Path
from hifiti_parsing import extract_lyrics, pick_reply_content, FALLBACK_REPLIES

FIXTURES = Path(__file__).parent / "fixtures"


def test_extract_lyrics_from_pre_block():
    html = (FIXTURES / "thread_with_lyrics.html").read_text(encoding="utf-8")
    lines = extract_lyrics(html)
    assert len(lines) >= 4
    assert any("雨下整夜" in line for line in lines)


def test_extract_lyrics_filters_short_lines():
    html = """<pre>
歌
词
是 一行
完整的歌词行 在这里
</pre>"""
    lines = extract_lyrics(html)
    # "歌"、"词"、"是 一行"（≤3字符的剔掉，保留 "是 一行" 是 4 chars），剩 "完整的歌词行 在这里"
    for line in lines:
        assert len(line.strip()) > 2


def test_extract_lyrics_no_block_returns_empty():
    html = (FIXTURES / "thread_no_lyrics.html").read_text(encoding="utf-8")
    assert extract_lyrics(html) == []


def test_pick_reply_content_uses_lyrics_when_available(monkeypatch):
    html = (FIXTURES / "thread_with_lyrics.html").read_text(encoding="utf-8")
    monkeypatch.setattr(random, "choice", lambda seq: seq[0])
    reply = pick_reply_content(html)
    assert reply == "雨下整夜 我的爱溢出就像雨水"


def test_pick_reply_content_falls_back_to_canned(monkeypatch):
    html = (FIXTURES / "thread_no_lyrics.html").read_text(encoding="utf-8")
    monkeypatch.setattr(random, "choice", lambda seq: seq[0])
    reply = pick_reply_content(html)
    assert reply == FALLBACK_REPLIES[0]


def test_fallback_replies_no_emoji():
    # 验证话术池没有 emoji（限于 BMP 之外的字符简单判定）
    for r in FALLBACK_REPLIES:
        for ch in r:
            assert ord(ch) < 0x2600 or ord(ch) > 0x27BF, f"emoji-ish char in {r!r}"
```

- [ ] **Step 3: Run tests — expect failures**

```bash
cd /root/autosign && pytest tests/test_hifiti_parsing.py::test_extract_lyrics_from_pre_block -v
```

Expected: ERRORS — `cannot import name 'extract_lyrics'`.

- [ ] **Step 4: Append `extract_lyrics`, `pick_reply_content`, `FALLBACK_REPLIES`**

Append to `scripts/hifiti_parsing.py`:

```python
import random

# ----- 回帖内容选择 -----
FALLBACK_REPLIES = (
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
)

# 优先匹配 <pre>，其次 <blockquote>，再其次 <td class="t_f"> 内的纯文本
_LYRICS_CONTAINER_RES = (
    re.compile(r"<pre[^>]*>(.*?)</pre>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<blockquote[^>]*>(.*?)</blockquote>", re.DOTALL | re.IGNORECASE),
)
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags(html_fragment: str) -> str:
    return _HTML_TAG_RE.sub("", html_fragment)


def extract_lyrics(html: str) -> List[str]:
    """从帖子 HTML 中提取歌词候选行（每行 > 2 字符）。"""
    for regex in _LYRICS_CONTAINER_RES:
        for m in regex.finditer(html):
            text = _strip_tags(m.group(1))
            lines = [
                line.strip()
                for line in text.split("\n")
                if len(line.strip()) > 2
            ]
            if len(lines) >= 5:
                return lines
    return []


def pick_reply_content(thread_html: str) -> str:
    """从帖子页选回帖内容；有歌词随机一行，无歌词则 fallback 话术池。"""
    lyrics = extract_lyrics(thread_html)
    if lyrics:
        return random.choice(lyrics)
    return random.choice(FALLBACK_REPLIES)
```

- [ ] **Step 5: Run tests — expect PASS**

```bash
cd /root/autosign && pytest tests/test_hifiti_parsing.py -v
```

Expected: 17 passed.

- [ ] **Step 6: Commit**

```bash
git -C /root/autosign add scripts/hifiti_parsing.py tests/test_hifiti_parsing.py tests/fixtures/
git -C /root/autosign commit -m "feat: extract_lyrics + pick_reply_content with fallback pool"
```

---

## Task 7: hifiti_parsing.parse_pan_links — 网盘链接 + 提取码

**Files:**
- Modify: `scripts/hifiti_parsing.py`
- Modify: `tests/test_hifiti_parsing.py`

- [ ] **Step 1: Append failing tests**

Append to `tests/test_hifiti_parsing.py`:

```python
from hifiti_parsing import parse_pan_links, PanLinks


def test_parse_pan_links_quark_only():
    html = '''
    <a href="https://pan.quark.cn/s/abc123def" target="_blank">夸克网盘</a>
    <div class="alert alert-success">a1b2</div>
    '''
    r = parse_pan_links(html)
    assert r == PanLinks(
        quark_url="https://pan.quark.cn/s/abc123def",
        quark_password="a1b2",
        baidu_url=None,
        baidu_password=None,
    )


def test_parse_pan_links_baidu_only():
    html = '''
    <a href="https://pan.baidu.com/s/xyz789abc" target="_blank">百度</a>
    <div class="alert alert-success">c3d4</div>
    '''
    r = parse_pan_links(html)
    assert r.baidu_url == "https://pan.baidu.com/s/xyz789abc"
    assert r.baidu_password == "c3d4"
    assert r.quark_url is None


def test_parse_pan_links_both_with_shared_password():
    """夸克和百度共用同一个提取码（默认假设）。"""
    html = '''
    <a href="https://pan.quark.cn/s/quarkid" target="_blank">夸克</a>
    <a href="https://pan.baidu.com/s/baiduid" target="_blank">百度</a>
    <div class="alert alert-success">pwd1</div>
    '''
    r = parse_pan_links(html)
    assert r.quark_url == "https://pan.quark.cn/s/quarkid"
    assert r.baidu_url == "https://pan.baidu.com/s/baiduid"
    assert r.quark_password == "pwd1"
    assert r.baidu_password == "pwd1"


def test_parse_pan_links_empty():
    r = parse_pan_links("<p>no links here</p>")
    assert r.quark_url is None
    assert r.baidu_url is None


def test_parse_pan_links_has_any():
    full = parse_pan_links('<a href="https://pan.quark.cn/s/x">x</a>')
    empty = parse_pan_links("<p>nothing</p>")
    assert full.has_any() is True
    assert empty.has_any() is False
```

- [ ] **Step 2: Run tests — expect ImportError**

```bash
cd /root/autosign && pytest tests/test_hifiti_parsing.py::test_parse_pan_links_quark_only -v
```

Expected: ERRORS — cannot import.

- [ ] **Step 3: Append `parse_pan_links` and `PanLinks` to `hifiti_parsing.py`**

```python
# ----- 网盘链接提取 -----
_QUARK_RE = re.compile(r'href="(https://pan\.quark\.cn/s/\w+)"')
_BAIDU_RE = re.compile(r'href="(https://pan\.baidu\.com/s/\w+)"')
_PWD_RE = re.compile(r'<div class="alert alert-success">\s*(\w+)\s*</div>')


@dataclass(frozen=True)
class PanLinks:
    quark_url: Optional[str]
    quark_password: Optional[str]
    baidu_url: Optional[str]
    baidu_password: Optional[str]

    def has_any(self) -> bool:
        return bool(self.quark_url or self.baidu_url)


def parse_pan_links(html: str) -> PanLinks:
    """从帖子 HTML 解析夸克/百度链接和提取码。

    默认假设：夸克和百度共用同一个提取码（取页面中第一个匹配的 alert-success）。
    若实测发现各有提取码，需在此扩展按链接位置归属的逻辑。
    """
    quark = _QUARK_RE.search(html)
    baidu = _BAIDU_RE.search(html)
    pwd_m = _PWD_RE.search(html)
    pwd = pwd_m.group(1) if pwd_m else None

    return PanLinks(
        quark_url=quark.group(1) if quark else None,
        quark_password=pwd if quark else None,
        baidu_url=baidu.group(1) if baidu else None,
        baidu_password=pwd if baidu else None,
    )
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
cd /root/autosign && pytest tests/test_hifiti_parsing.py -v
```

Expected: 22 passed.

- [ ] **Step 5: Commit**

```bash
git -C /root/autosign add scripts/hifiti_parsing.py tests/test_hifiti_parsing.py
git -C /root/autosign commit -m "feat: parse_pan_links with shared password assumption"
```

---

## Task 8: hifiti_parsing.parse_ranking — 日榜帖子列表

**Files:**
- Modify: `scripts/hifiti_parsing.py`
- Modify: `tests/test_hifiti_parsing.py`
- Create: `tests/fixtures/ranking.html`

- [ ] **Step 1: Create fixture**

Create `tests/fixtures/ranking.html`:

```html
<html>
<body>
<div class="bbs-thread-item">
  <a href="thread-69745-1.htm" class="thread-title">[流行] 周杰伦 - 七里香 [WAV]</a>
</div>
<div class="bbs-thread-item">
  <a href="thread-69740-1.htm" class="thread-title">林俊杰 - 江南</a>
</div>
<div class="bbs-thread-item">
  <a href="thread-69730-1.htm" class="thread-title">[公告] 积分使用问题说明</a>
</div>
<div class="bbs-thread-item">
  <a href="thread-69725-1.htm" class="thread-title">Adele - Hello</a>
</div>
</body>
</html>
```

- [ ] **Step 2: Append failing tests**

Append to `tests/test_hifiti_parsing.py`:

```python
from hifiti_parsing import parse_ranking, RankingItem


def test_parse_ranking_extracts_all_items():
    html = (FIXTURES / "ranking.html").read_text(encoding="utf-8")
    items = parse_ranking(html)
    assert len(items) == 4
    assert items[0] == RankingItem(tid=69745, title="[流行] 周杰伦 - 七里香 [WAV]")
    assert items[1].tid == 69740
    assert items[2].tid == 69730


def test_parse_ranking_preserves_order():
    html = (FIXTURES / "ranking.html").read_text(encoding="utf-8")
    items = parse_ranking(html)
    tids = [i.tid for i in items]
    assert tids == [69745, 69740, 69730, 69725]


def test_parse_ranking_empty():
    assert parse_ranking("<html></html>") == []


def test_parse_ranking_dedupes_same_tid():
    html = '''
    <a href="thread-100-1.htm">A</a>
    <a href="thread-100-2.htm">A again</a>
    '''
    items = parse_ranking(html)
    # 同一 tid 在列表里只出现一次（取第一个标题）
    assert len(items) == 1
    assert items[0].tid == 100
```

- [ ] **Step 3: Run tests — expect ImportError**

```bash
cd /root/autosign && pytest tests/test_hifiti_parsing.py::test_parse_ranking_extracts_all_items -v
```

Expected: ERRORS — cannot import.

- [ ] **Step 4: Append `parse_ranking` and `RankingItem` to `hifiti_parsing.py`**

```python
# ----- 日榜列表解析 -----
_THREAD_LINK_RE = re.compile(
    r'<a[^>]+href="thread-(\d+)-\d+\.htm"[^>]*>(.*?)</a>',
    re.DOTALL | re.IGNORECASE,
)


@dataclass(frozen=True)
class RankingItem:
    tid: int
    title: str


def parse_ranking(html: str) -> List[RankingItem]:
    """从日榜 HTML 提取 (tid, title)，保序去重。"""
    seen: set[int] = set()
    items: List[RankingItem] = []
    for m in _THREAD_LINK_RE.finditer(html):
        tid = int(m.group(1))
        if tid in seen:
            continue
        seen.add(tid)
        title = _strip_tags(m.group(2)).strip()
        items.append(RankingItem(tid=tid, title=title))
    return items
```

- [ ] **Step 5: Run tests — expect PASS**

```bash
cd /root/autosign && pytest tests/test_hifiti_parsing.py -v
```

Expected: 26 passed.

- [ ] **Step 6: Commit**

```bash
git -C /root/autosign add scripts/hifiti_parsing.py tests/test_hifiti_parsing.py tests/fixtures/ranking.html
git -C /root/autosign commit -m "feat: parse_ranking extracts thread tids preserving order"
```

---

## Task 9: hifiti_storage.py — SQLite DAO

**Files:**
- Create: `scripts/hifiti_storage.py`
- Create: `tests/test_hifiti_storage.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_hifiti_storage.py`:

```python
import sqlite3
from pathlib import Path
import pytest

from hifiti_storage import (
    Record,
    init_schema,
    insert_record,
    is_dedup,
    today_count,
    pending_unsynced,
    mark_synced,
    DuplicateError,
)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "records.db"
    conn = sqlite3.connect(path)
    init_schema(conn)
    yield conn
    conn.close()


def _sample(tid=1, song="七里香", artist="周杰伦"):
    return Record(
        tid=tid,
        title=f"[流行] {artist} - {song} [WAV]",
        song_name=song,
        artist=artist,
        post_url=f"https://www.hifiti.com/thread-{tid}-1.htm",
        quark_url="https://pan.quark.cn/s/abc",
        quark_password="pw1",
        baidu_url=None,
        baidu_password=None,
        reply_content="感谢分享",
    )


def test_init_schema_idempotent(db):
    init_schema(db)  # 二次调用不报错
    cur = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='records'")
    assert cur.fetchone() is not None


def test_insert_record_succeeds(db):
    rec = _sample()
    new_id = insert_record(db, rec)
    assert new_id > 0
    row = db.execute("SELECT tid, song_name FROM records WHERE id=?", (new_id,)).fetchone()
    assert row == (1, "七里香")


def test_insert_duplicate_tid_raises(db):
    insert_record(db, _sample(tid=1))
    with pytest.raises(DuplicateError):
        insert_record(db, _sample(tid=1))


def test_insert_duplicate_song_artist_raises(db):
    insert_record(db, _sample(tid=1, song="江南", artist="林俊杰"))
    with pytest.raises(DuplicateError):
        insert_record(db, _sample(tid=2, song="江南", artist="林俊杰"))


def test_is_dedup_by_tid(db):
    insert_record(db, _sample(tid=42))
    assert is_dedup(db, tid=42, song_name="任意", artist="任意") == "DEDUP_TID"


def test_is_dedup_by_song_artist(db):
    insert_record(db, _sample(tid=10, song="七里香", artist="周杰伦"))
    assert is_dedup(db, tid=99, song_name="七里香", artist="周杰伦") == "DEDUP_SONG"


def test_is_dedup_returns_none_when_new(db):
    assert is_dedup(db, tid=99, song_name="新歌", artist="新人") is None


def test_today_count_starts_zero(db):
    assert today_count(db) == 0


def test_today_count_after_insert(db):
    insert_record(db, _sample(tid=1))
    insert_record(db, _sample(tid=2, song="江南", artist="林俊杰"))
    assert today_count(db) == 2


def test_pending_unsynced_returns_unsynced_rows(db):
    insert_record(db, _sample(tid=1))
    insert_record(db, _sample(tid=2, song="江南", artist="林俊杰"))
    pending = pending_unsynced(db)
    assert len(pending) == 2
    tids = {r.tid for r in pending}
    assert tids == {1, 2}


def test_mark_synced_excludes_from_pending(db):
    rec1 = _sample(tid=1)
    rec2 = _sample(tid=2, song="江南", artist="林俊杰")
    id1 = insert_record(db, rec1)
    insert_record(db, rec2)
    mark_synced(db, id1)
    pending = pending_unsynced(db)
    assert len(pending) == 1
    assert pending[0].tid == 2
```

- [ ] **Step 2: Run tests — expect ImportError**

```bash
cd /root/autosign && pytest tests/test_hifiti_storage.py -v
```

Expected: ERRORS — `ModuleNotFoundError: No module named 'hifiti_storage'`.

- [ ] **Step 3: Create `scripts/hifiti_storage.py`**

```python
"""SQLite DAO for Phase 2 records."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace
from typing import List, Optional

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS records (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    tid               INTEGER NOT NULL UNIQUE,
    title             TEXT    NOT NULL,
    song_name         TEXT    NOT NULL,
    artist            TEXT    NOT NULL,
    post_url          TEXT    NOT NULL,
    quark_url         TEXT,
    quark_password    TEXT,
    baidu_url         TEXT,
    baidu_password    TEXT,
    reply_content     TEXT    NOT NULL,
    created_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    synced_to_bitable INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_song_artist ON records(song_name, artist);
CREATE INDEX IF NOT EXISTS idx_synced ON records(synced_to_bitable);
CREATE INDEX IF NOT EXISTS idx_created ON records(created_at);
"""


class DuplicateError(Exception):
    """tid 或 (song_name, artist) 已存在。"""


@dataclass
class Record:
    tid: int
    title: str
    song_name: str
    artist: str
    post_url: str
    quark_url: Optional[str]
    quark_password: Optional[str]
    baidu_url: Optional[str]
    baidu_password: Optional[str]
    reply_content: str
    id: Optional[int] = None
    created_at: Optional[str] = None


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.commit()


def insert_record(conn: sqlite3.Connection, rec: Record) -> int:
    try:
        cur = conn.execute(
            """
            INSERT INTO records (
                tid, title, song_name, artist, post_url,
                quark_url, quark_password, baidu_url, baidu_password, reply_content
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rec.tid, rec.title, rec.song_name, rec.artist, rec.post_url,
                rec.quark_url, rec.quark_password, rec.baidu_url, rec.baidu_password,
                rec.reply_content,
            ),
        )
        conn.commit()
        return cur.lastrowid
    except sqlite3.IntegrityError as e:
        raise DuplicateError(str(e)) from e


def is_dedup(
    conn: sqlite3.Connection, tid: int, song_name: str, artist: str
) -> Optional[str]:
    """返回 'DEDUP_TID' / 'DEDUP_SONG' / None。"""
    row = conn.execute("SELECT 1 FROM records WHERE tid=?", (tid,)).fetchone()
    if row:
        return "DEDUP_TID"
    row = conn.execute(
        "SELECT 1 FROM records WHERE song_name=? AND artist=?", (song_name, artist)
    ).fetchone()
    if row:
        return "DEDUP_SONG"
    return None


def today_count(conn: sqlite3.Connection) -> int:
    """统计本地时区"今天"已记录的条数（用于完成判定）。"""
    row = conn.execute(
        """
        SELECT count(*) FROM records
        WHERE date(created_at, 'localtime') = date('now', 'localtime')
        """
    ).fetchone()
    return row[0]


def pending_unsynced(conn: sqlite3.Connection) -> List[Record]:
    rows = conn.execute(
        """
        SELECT id, tid, title, song_name, artist, post_url,
               quark_url, quark_password, baidu_url, baidu_password,
               reply_content, created_at
        FROM records
        WHERE synced_to_bitable = 0
        ORDER BY id
        """
    ).fetchall()
    return [
        Record(
            id=r[0], tid=r[1], title=r[2], song_name=r[3], artist=r[4], post_url=r[5],
            quark_url=r[6], quark_password=r[7], baidu_url=r[8], baidu_password=r[9],
            reply_content=r[10], created_at=r[11],
        )
        for r in rows
    ]


def mark_synced(conn: sqlite3.Connection, record_id: int) -> None:
    conn.execute("UPDATE records SET synced_to_bitable=1 WHERE id=?", (record_id,))
    conn.commit()
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
cd /root/autosign && pytest tests/test_hifiti_storage.py -v
```

Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
git -C /root/autosign add scripts/hifiti_storage.py tests/test_hifiti_storage.py
git -C /root/autosign commit -m "feat: hifiti_storage with schema, insert, dedup, today_count"
```

---

## Task 10: 回帖 + 帖子页拉取（hifiti_rank_collect 内的私有函数）

**Files:**
- Create: `scripts/hifiti_reply.py`
- Create: `tests/test_hifiti_reply.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_hifiti_reply.py`:

```python
from unittest.mock import MagicMock, patch
import pytest

from hifiti_reply import post_reply, fetch_thread, ReplyOutcome


def _mock_resp(status=200, text="发表成功"):
    m = MagicMock()
    m.status_code = status
    m.text = text
    return m


def test_post_reply_success():
    session = MagicMock()
    session.post.return_value = _mock_resp(200, "发表成功，正在跳转")
    outcome = post_reply(session, tid=69745, message="感谢分享")
    assert outcome.success is True
    assert outcome.detail.startswith("status=200")


def test_post_reply_too_fast():
    session = MagicMock()
    session.post.return_value = _mock_resp(200, "回帖间隔过快")
    outcome = post_reply(session, tid=69745, message="感谢分享")
    assert outcome.success is False
    assert outcome.too_fast is True


def test_post_reply_business_error():
    session = MagicMock()
    session.post.return_value = _mock_resp(403, "权限不足")
    outcome = post_reply(session, tid=69745, message="感谢分享")
    assert outcome.success is False
    assert outcome.too_fast is False


def test_fetch_thread_returns_html():
    session = MagicMock()
    session.get.return_value = _mock_resp(200, "<html>thread body</html>")
    html = fetch_thread(session, tid=69745)
    assert html == "<html>thread body</html>"
    session.get.assert_called_once()
    called_url = session.get.call_args[0][0]
    assert "thread-69745-1.htm" in called_url


def test_fetch_thread_raises_on_non_200():
    session = MagicMock()
    session.get.return_value = _mock_resp(500, "boom")
    with pytest.raises(RuntimeError):
        fetch_thread(session, tid=69745)
```

- [ ] **Step 2: Run tests — expect ImportError**

```bash
cd /root/autosign && pytest tests/test_hifiti_reply.py -v
```

Expected: ERRORS — cannot import `hifiti_reply`.

- [ ] **Step 3: Create `scripts/hifiti_reply.py`**

```python
"""hifiti 回帖 + 帖子正文拉取。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import requests

from hifiti_common import (
    BASE_URL,
    REQUEST_TIMEOUT,
    snippet,
)

REPLY_URL_TEMPLATE = f"{BASE_URL}/post-create-{{tid}}-1.htm"
THREAD_URL_TEMPLATE = f"{BASE_URL}/thread-{{tid}}-1.htm"

REPLY_SUCCESS_KEYWORDS = ("成功", "发表", "已发布")
REPLY_TOO_FAST_KEYWORDS = ("过快", "间隔", "请稍后", "频繁")


@dataclass(frozen=True)
class ReplyOutcome:
    success: bool
    too_fast: bool
    detail: str


def fetch_thread(session: requests.Session, tid: int) -> str:
    """GET 帖子正文 HTML（带 sort=asc 让回帖后链接出现在主楼）。"""
    url = THREAD_URL_TEMPLATE.format(tid=tid) + "?sort=asc"
    resp = session.get(
        url,
        headers={"Referer": f"{BASE_URL}/"},
        timeout=REQUEST_TIMEOUT,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"fetch_thread tid={tid} status={resp.status_code}")
    return resp.text


def post_reply(session: requests.Session, tid: int, message: str) -> ReplyOutcome:
    """对帖子 tid 回帖。"""
    url = REPLY_URL_TEMPLATE.format(tid=tid)
    resp = session.post(
        url,
        data={
            "doctype": "1",
            "return_html": "1",
            "quotepid": "0",
            "message": message,
        },
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": BASE_URL,
            "Referer": THREAD_URL_TEMPLATE.format(tid=tid),
            "X-Requested-With": "XMLHttpRequest",
        },
        timeout=REQUEST_TIMEOUT,
    )
    body = snippet(resp.text)
    if resp.status_code != 200:
        return ReplyOutcome(False, False, f"status={resp.status_code} body='{body}'")
    for kw in REPLY_TOO_FAST_KEYWORDS:
        if kw in body:
            return ReplyOutcome(False, True, f"status=200 too_fast msg='{body}'")
    for kw in REPLY_SUCCESS_KEYWORDS:
        if kw in body:
            return ReplyOutcome(True, False, f"status=200 ok msg='{body}'")
    # 默认未识别但 200 视为成功（保守）
    return ReplyOutcome(True, False, f"status=200 (assumed) msg='{body}'")
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
cd /root/autosign && pytest tests/test_hifiti_reply.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git -C /root/autosign add scripts/hifiti_reply.py tests/test_hifiti_reply.py
git -C /root/autosign commit -m "feat: post_reply + fetch_thread with too_fast detection"
```

---

## Task 11: hifiti_bitable.py — 飞书 Bitable 客户端

**Files:**
- Create: `scripts/hifiti_bitable.py`
- Create: `tests/test_hifiti_bitable.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_hifiti_bitable.py`:

```python
from unittest.mock import MagicMock, patch
import pytest

from hifiti_bitable import (
    BitableConfig,
    get_tenant_access_token,
    insert_record_to_bitable,
    BitableError,
)
from hifiti_storage import Record


def _cfg():
    return BitableConfig(
        app_id="cli_xxx",
        app_secret="secret_xxx",
        app_token="apptoken_xxx",
        table_id="tbl_xxx",
    )


def _mock_resp(json_body, status=200):
    m = MagicMock()
    m.status_code = status
    m.json.return_value = json_body
    m.text = str(json_body)
    return m


def _sample_rec():
    return Record(
        id=1,
        tid=42,
        title="[流行] 周杰伦 - 七里香 [WAV]",
        song_name="七里香",
        artist="周杰伦",
        post_url="https://www.hifiti.com/thread-42-1.htm",
        quark_url="https://pan.quark.cn/s/abc",
        quark_password="pw1",
        baidu_url=None,
        baidu_password=None,
        reply_content="感谢分享",
        created_at="2026-04-26 21:00:00",
    )


@patch("hifiti_bitable.requests.post")
def test_get_tenant_access_token_success(mock_post):
    mock_post.return_value = _mock_resp(
        {"code": 0, "tenant_access_token": "t-abc-def", "expire": 7200}
    )
    token = get_tenant_access_token(_cfg())
    assert token == "t-abc-def"
    args, kwargs = mock_post.call_args
    assert "tenant_access_token/internal" in args[0]
    assert kwargs["json"]["app_id"] == "cli_xxx"


@patch("hifiti_bitable.requests.post")
def test_get_tenant_access_token_error_raises(mock_post):
    mock_post.return_value = _mock_resp({"code": 99, "msg": "invalid app_id"})
    with pytest.raises(BitableError):
        get_tenant_access_token(_cfg())


@patch("hifiti_bitable.requests.post")
def test_insert_record_to_bitable_success(mock_post):
    mock_post.return_value = _mock_resp({"code": 0, "data": {"record": {"record_id": "rec_1"}}})
    insert_record_to_bitable(_cfg(), token="t-abc", rec=_sample_rec())
    args, kwargs = mock_post.call_args
    assert "/bitable/v1/apps/apptoken_xxx/tables/tbl_xxx/records" in args[0]
    fields = kwargs["json"]["fields"]
    assert fields["歌曲名"] == "七里香"
    assert fields["演唱者"] == "周杰伦"
    assert fields["夸克链接"]["link"] == "https://pan.quark.cn/s/abc"
    assert fields["夸克提取码"] == "pw1"
    # 百度为空，应不出现或为空字段
    assert fields.get("百度链接") in (None, {"link": "", "text": ""})


@patch("hifiti_bitable.requests.post")
def test_insert_record_to_bitable_api_error(mock_post):
    mock_post.return_value = _mock_resp({"code": 1254000, "msg": "field invalid"})
    with pytest.raises(BitableError):
        insert_record_to_bitable(_cfg(), token="t", rec=_sample_rec())
```

- [ ] **Step 2: Run tests — expect ImportError**

```bash
cd /root/autosign && pytest tests/test_hifiti_bitable.py -v
```

Expected: ERRORS — cannot import.

- [ ] **Step 3: Create `scripts/hifiti_bitable.py`**

```python
"""飞书多维表格 (Bitable) 客户端。

环境变量（任一缺失则禁用 Bitable 同步）：
    BITABLE_APP_ID, BITABLE_APP_SECRET, BITABLE_APP_TOKEN, BITABLE_TABLE_ID
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

import requests

from hifiti_storage import Record

TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
INSERT_URL_TEMPLATE = (
    "https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records"
)
HTTP_TIMEOUT = 15


class BitableError(Exception):
    """Bitable API 调用失败。"""


@dataclass
class BitableConfig:
    app_id: str
    app_secret: str
    app_token: str
    table_id: str

    @classmethod
    def from_env(cls) -> Optional["BitableConfig"]:
        keys = ("BITABLE_APP_ID", "BITABLE_APP_SECRET", "BITABLE_APP_TOKEN", "BITABLE_TABLE_ID")
        vals = [os.environ.get(k, "").strip() for k in keys]
        if not all(vals):
            return None
        return cls(*vals)


def get_tenant_access_token(cfg: BitableConfig) -> str:
    resp = requests.post(
        TOKEN_URL,
        json={"app_id": cfg.app_id, "app_secret": cfg.app_secret},
        timeout=HTTP_TIMEOUT,
    )
    data = resp.json()
    if data.get("code") != 0:
        raise BitableError(f"token error: {data}")
    return data["tenant_access_token"]


def _build_link_field(url: Optional[str], text: Optional[str]) -> Optional[dict]:
    if not url:
        return None
    return {"link": url, "text": text or url}


def insert_record_to_bitable(cfg: BitableConfig, token: str, rec: Record) -> None:
    fields = {
        "歌曲名": rec.song_name,
        "演唱者": rec.artist,
        "帖子链接": {"link": rec.post_url, "text": rec.title},
        "夸克链接": _build_link_field(rec.quark_url, rec.quark_url),
        "夸克提取码": rec.quark_password or "",
        "百度链接": _build_link_field(rec.baidu_url, rec.baidu_url),
        "百度提取码": rec.baidu_password or "",
        "抓取时间": rec.created_at,
    }
    # 移除值为 None 的字段（Bitable 拒绝 null 链接字段）
    fields = {k: v for k, v in fields.items() if v is not None}

    url = INSERT_URL_TEMPLATE.format(app_token=cfg.app_token, table_id=cfg.table_id)
    resp = requests.post(
        url,
        json={"fields": fields},
        headers={"Authorization": f"Bearer {token}"},
        timeout=HTTP_TIMEOUT,
    )
    data = resp.json()
    if data.get("code") != 0:
        raise BitableError(f"insert error tid={rec.tid}: {data}")
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
cd /root/autosign && pytest tests/test_hifiti_bitable.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git -C /root/autosign add scripts/hifiti_bitable.py tests/test_hifiti_bitable.py
git -C /root/autosign commit -m "feat: hifiti_bitable client with token + insert"
```

---

## Task 12: hifiti_reporting.py — stdout 汇总卡片

**Files:**
- Create: `scripts/hifiti_reporting.py`
- Create: `tests/test_hifiti_reporting.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_hifiti_reporting.py`:

```python
from hifiti_reporting import format_report, ReportData, NewRecord, SkipEntry, FailEntry


def _record(song="七里香", artist="周杰伦", quark="https://pan.quark.cn/s/x", baidu=None):
    return NewRecord(
        song_name=song, artist=artist,
        post_url=f"https://www.hifiti.com/thread-1-1.htm",
        quark_url=quark, quark_password="pw" if quark else None,
        baidu_url=baidu, baidu_password="pw" if baidu else None,
    )


def test_report_includes_header_and_progress():
    data = ReportData(
        timestamp="2026-04-26 21:00:00",
        trigger_label="主跑",
        today_count=1,
        target=2,
        new_records=[_record()],
        skips=[],
        fails=[],
        bitable_summary="未配置，跳过",
    )
    out = format_report(data)
    assert "hifiti 日榜采集" in out
    assert "2026-04-26 21:00:00" in out
    assert "1/2" in out
    assert "周杰伦 - 七里香" in out


def test_report_lists_skips():
    data = ReportData(
        timestamp="t", trigger_label="重试", today_count=0, target=2,
        new_records=[],
        skips=[SkipEntry(tid=42, reason="DEDUP_TID", note="昨天已采")],
        fails=[],
        bitable_summary="未配置，跳过",
    )
    out = format_report(data)
    assert "tid=42" in out
    assert "DEDUP_TID" in out
    assert "昨天已采" in out


def test_report_lists_fails():
    data = ReportData(
        timestamp="t", trigger_label="主跑", today_count=0, target=2,
        new_records=[], skips=[],
        fails=[FailEntry(tid=99, reason="REPLY_FAIL", note="过快")],
        bitable_summary="未配置，跳过",
    )
    out = format_report(data)
    assert "tid=99" in out
    assert "REPLY_FAIL" in out


def test_report_no_section_when_empty_section():
    data = ReportData(
        timestamp="t", trigger_label="主跑", today_count=2, target=2,
        new_records=[_record()], skips=[], fails=[],
        bitable_summary="2 行新写入",
    )
    out = format_report(data)
    assert "跳过 (0)" not in out  # 空段不打印
    assert "失败 (0)" not in out
```

- [ ] **Step 2: Run tests — expect ImportError**

```bash
cd /root/autosign && pytest tests/test_hifiti_reporting.py -v
```

Expected: ERRORS — cannot import.

- [ ] **Step 3: Create `scripts/hifiti_reporting.py`**

```python
"""stdout 汇总卡片格式化（青龙转发到飞书机器人）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass(frozen=True)
class NewRecord:
    song_name: str
    artist: str
    post_url: str
    quark_url: Optional[str]
    quark_password: Optional[str]
    baidu_url: Optional[str]
    baidu_password: Optional[str]


@dataclass(frozen=True)
class SkipEntry:
    tid: int
    reason: str
    note: str


@dataclass(frozen=True)
class FailEntry:
    tid: int
    reason: str
    note: str


@dataclass
class ReportData:
    timestamp: str
    trigger_label: str
    today_count: int
    target: int
    new_records: List[NewRecord]
    skips: List[SkipEntry]
    fails: List[FailEntry]
    bitable_summary: str


def format_report(data: ReportData) -> str:
    lines: List[str] = []
    header = f"===== hifiti 日榜采集 {data.timestamp} ====="
    lines.append(header)
    lines.append(f"触发：{data.trigger_label}")
    lines.append(f"完成进度：{data.today_count}/{data.target} 已采集")
    lines.append("")

    if data.new_records:
        lines.append(f"新增记录 ({len(data.new_records)})：")
        for i, r in enumerate(data.new_records, start=1):
            lines.append(f"{i}. {r.artist} - {r.song_name}")
            lines.append(f"   帖子: {r.post_url}")
            if r.quark_url:
                lines.append(f"   夸克: {r.quark_url}  提取码: {r.quark_password or ''}")
            if r.baidu_url:
                lines.append(f"   百度: {r.baidu_url}  提取码: {r.baidu_password or ''}")
        lines.append("")

    if data.skips:
        lines.append(f"跳过 ({len(data.skips)})：")
        for s in data.skips:
            lines.append(f"- tid={s.tid} [{s.reason}]  {s.note}")
        lines.append("")

    if data.fails:
        lines.append(f"失败 ({len(data.fails)})：")
        for f in data.fails:
            lines.append(f"- tid={f.tid} [{f.reason}]  {f.note}")
        lines.append("")

    lines.append(f"Bitable 同步：{data.bitable_summary}")
    lines.append("=" * len(header))
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
cd /root/autosign && pytest tests/test_hifiti_reporting.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git -C /root/autosign add scripts/hifiti_reporting.py tests/test_hifiti_reporting.py
git -C /root/autosign commit -m "feat: hifiti_reporting markdown summary card"
```

---

## Task 13: hifiti_rank_collect.py — 主编排器（带文件锁）

**Files:**
- Create: `scripts/hifiti_rank_collect.py`

- [ ] **Step 1: Create the orchestrator script**

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hifiti 日榜自动采集（Phase 2）

每次运行：
1. 文件锁兜底 21:00 双触发
2. 检查 today_count，已 ≥2 立刻 exit(0)
3. 登录 hifiti（首账号）
4. 抓日榜 → 遍历到积满 2 条新记录
5. 同步 SQLite 未同步行到 Bitable（best-effort）
6. 输出 stdout 汇总卡片
7. exit 0/1 by today_count
"""
from __future__ import annotations

import fcntl
import os
import random
import sqlite3
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

import requests

from hifiti_common import BASE_URL, build_session, login, mask
from hifiti_parsing import (
    PanLinks, ParsedTitle, RankingItem,
    is_english_song, parse_pan_links, parse_ranking, parse_title, pick_reply_content,
)
from hifiti_reply import ReplyOutcome, fetch_thread, post_reply
from hifiti_storage import (
    DuplicateError, Record,
    init_schema, insert_record, is_dedup, mark_synced, pending_unsynced, today_count,
)
from hifiti_bitable import BitableConfig, BitableError, get_tenant_access_token, insert_record_to_bitable
from hifiti_reporting import (
    FailEntry, NewRecord, ReportData, SkipEntry, format_report,
)

SCRIPT_DIR = Path(__file__).resolve().parent
DB_PATH = SCRIPT_DIR / "records.db"
LOCK_PATH = SCRIPT_DIR / ".rank_collect.lock"

RANKING_URL = f"{BASE_URL}/index-0-5.htm"
TARGET = 2
POST_DELAY_MIN = 2.0
POST_DELAY_MAX = 5.0
ACCOUNT_SEPARATOR = "&"


# ---------- helpers ----------

def acquire_lock() -> Optional[object]:
    """抢文件锁；抢不到说明同名进程在跑，本次跳过（exit 0）。"""
    fd = open(LOCK_PATH, "w")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except BlockingIOError:
        fd.close()
        return None


def first_credential() -> Tuple[str, str]:
    accounts = os.environ.get("HIFITI_ACCOUNT", "").strip().strip(ACCOUNT_SEPARATOR)
    passwords = os.environ.get("HIFITI_PASSWORD", "").strip().strip(ACCOUNT_SEPARATOR)
    if not accounts or not passwords:
        raise SystemExit("[FATAL] HIFITI_ACCOUNT / HIFITI_PASSWORD 未设置")
    a = accounts.split(ACCOUNT_SEPARATOR)[0].strip()
    p = passwords.split(ACCOUNT_SEPARATOR)[0]
    return a, p


def fetch_ranking_html(session: requests.Session) -> str:
    resp = session.get(RANKING_URL, timeout=20)
    if resp.status_code != 200:
        raise SystemExit(f"[FATAL] 日榜抓取失败 status={resp.status_code}")
    return resp.text


def trigger_label() -> str:
    """根据当前小时和分钟猜是主跑还是哪次重试。"""
    now = datetime.now()
    if now.hour == 21 and now.minute < 5:
        return "主跑"
    return f"重试 {now.strftime('%H:%M')}"


def post_url(tid: int) -> str:
    return f"{BASE_URL}/thread-{tid}-1.htm"


# ---------- core flow ----------

def process_one(
    session: requests.Session,
    item: RankingItem,
    conn: sqlite3.Connection,
    skips: List[SkipEntry],
    fails: List[FailEntry],
) -> Optional[Record]:
    parsed = parse_title(item.title)
    if parsed.blocked:
        skips.append(SkipEntry(item.tid, "SKIP_BLOCKED", item.title))
        return None
    if not parsed.valid:
        skips.append(SkipEntry(item.tid, "SKIP_INVALID_TITLE", item.title))
        return None
    if is_english_song(parsed.song, parsed.artist):
        skips.append(SkipEntry(item.tid, "SKIP_ENGLISH", f"{parsed.artist}-{parsed.song}"))
        return None

    dedup = is_dedup(conn, item.tid, parsed.song, parsed.artist)
    if dedup == "DEDUP_TID":
        skips.append(SkipEntry(item.tid, "SKIP_DEDUP_TID", item.title))
        return None
    if dedup == "DEDUP_SONG":
        skips.append(SkipEntry(item.tid, "SKIP_DEDUP_SONG", f"{parsed.artist}-{parsed.song} 已存在"))
        return None

    # 拉帖子页 → 选回帖内容
    try:
        html_pre = fetch_thread(session, item.tid)
    except Exception as e:
        fails.append(FailEntry(item.tid, "FAIL_FETCH", str(e)))
        return None
    reply = pick_reply_content(html_pre)

    # 回帖
    try:
        outcome = post_reply(session, item.tid, reply)
    except requests.RequestException as e:
        fails.append(FailEntry(item.tid, "FAIL_REPLY", f"{type(e).__name__}: {e}"))
        return None

    if not outcome.success:
        fails.append(FailEntry(item.tid, "FAIL_REPLY", outcome.detail))
        return None

    # 回帖后再拉一次（链接已可见）
    try:
        html_post = fetch_thread(session, item.tid)
    except Exception as e:
        fails.append(FailEntry(item.tid, "FAIL_FETCH_POST", str(e)))
        return None

    pan = parse_pan_links(html_post)
    if not pan.has_any():
        skips.append(SkipEntry(item.tid, "SKIP_NO_LINK", item.title))
        return None

    rec = Record(
        tid=item.tid,
        title=item.title,
        song_name=parsed.song,
        artist=parsed.artist,
        post_url=post_url(item.tid),
        quark_url=pan.quark_url,
        quark_password=pan.quark_password,
        baidu_url=pan.baidu_url,
        baidu_password=pan.baidu_password,
        reply_content=reply,
    )
    try:
        insert_record(conn, rec)
    except DuplicateError as e:
        # 极端情况：另一进程刚刚插入了同 (song, artist)
        skips.append(SkipEntry(item.tid, "SKIP_DEDUP_RACE", str(e)))
        return None
    return rec


def sync_to_bitable(
    cfg: Optional[BitableConfig], conn: sqlite3.Connection
) -> str:
    if cfg is None:
        return "未配置，跳过"
    pending = pending_unsynced(conn)
    if not pending:
        return "无待同步行"
    try:
        token = get_tenant_access_token(cfg)
    except BitableError as e:
        return f"token 获取失败: {e}"

    ok, fail = 0, 0
    for r in pending:
        try:
            insert_record_to_bitable(cfg, token, r)
            mark_synced(conn, r.id)
            ok += 1
        except BitableError as e:
            print(f"[BITABLE_FAIL] tid={r.tid} {e}", file=sys.stderr)
            fail += 1
    return f"{ok} 行成功 / {fail} 行失败"


def main() -> int:
    lock_fd = acquire_lock()
    if lock_fd is None:
        print("[INFO] 已有同名进程在跑，本次跳过")
        return 0

    conn = sqlite3.connect(str(DB_PATH))
    init_schema(conn)

    if today_count(conn) >= TARGET:
        print(f"[INFO] 当天已采集 ≥{TARGET} 条，本次直接退出")
        return 0

    account, password = first_credential()
    session = build_session()

    ok, detail = login(session, account, password)
    if not ok:
        raise SystemExit(f"[FATAL] 登录失败 ({mask(account)}): {detail}")

    html = fetch_ranking_html(session)
    items = parse_ranking(html)

    skips: List[SkipEntry] = []
    fails: List[FailEntry] = []
    new_records: List[NewRecord] = []

    initial_count = today_count(conn)
    new_today = 0
    for idx, item in enumerate(items):
        if initial_count + new_today >= TARGET:
            break
        if idx > 0:
            time.sleep(random.uniform(POST_DELAY_MIN, POST_DELAY_MAX))
        rec = process_one(session, item, conn, skips, fails)
        if rec:
            new_today += 1
            new_records.append(NewRecord(
                song_name=rec.song_name, artist=rec.artist, post_url=rec.post_url,
                quark_url=rec.quark_url, quark_password=rec.quark_password,
                baidu_url=rec.baidu_url, baidu_password=rec.baidu_password,
            ))

    bitable_summary = sync_to_bitable(BitableConfig.from_env(), conn)

    final_count = today_count(conn)
    report = format_report(ReportData(
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        trigger_label=trigger_label(),
        today_count=final_count,
        target=TARGET,
        new_records=new_records,
        skips=skips,
        fails=fails,
        bitable_summary=bitable_summary,
    ))
    print(report)

    return 0 if final_count >= TARGET else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        print("[FATAL] 未捕获异常:")
        traceback.print_exc()
        sys.exit(1)
```

- [ ] **Step 2: Smoke test — import & syntax**

```bash
cd /root/autosign && python3 -c "import sys; sys.path.insert(0, 'scripts'); import hifiti_rank_collect; print('import OK')"
```

Expected: `import OK`.

- [ ] **Step 3: Smoke test — file lock works**

```bash
cd /root/autosign && python3 -c "
import sys; sys.path.insert(0, 'scripts')
from hifiti_rank_collect import acquire_lock, LOCK_PATH
fd1 = acquire_lock()
assert fd1 is not None, 'first should succeed'
fd2 = acquire_lock()
assert fd2 is None, 'second should fail'
print('lock OK')
"
```

Expected: `lock OK`.

- [ ] **Step 4: Run full test suite**

```bash
cd /root/autosign && pytest -v
```

Expected: All previous tests still pass (≥ 47 total).

- [ ] **Step 5: Commit**

```bash
git -C /root/autosign add scripts/hifiti_rank_collect.py
git -C /root/autosign commit -m "feat: hifiti_rank_collect orchestrator with file lock"
```

---

## Task 14: 部署 — 实测校准 + 青龙任务 + README

**Files:**
- Modify: `scripts/requirements.txt`
- Modify: `scripts/README.md`
- Modify: `README.md`
- Modify: `.gitignore`

- [ ] **Step 1: Verify scripts/requirements.txt covers runtime deps**

Read current file:

```bash
cat /root/autosign/scripts/requirements.txt
```

Expected: contains `requests`. No new runtime deps needed.

If empty/missing, write:

```txt
requests>=2.28
```

- [ ] **Step 2: Update `.gitignore`**

Append to `/root/autosign/.gitignore` (skip if already present):

```
# Phase 2 runtime data
scripts/records.db
scripts/.rank_collect.lock
```

- [ ] **Step 3: Add Phase 2 section to `scripts/README.md`**

Append:

```markdown
## Phase 2: 日榜自动采集 (`hifiti_rank_collect.py`)

**何时运行**：每天 21:00 主跑，21:30/22:00/22:30/23:00/23:30 自动重试到积满 2 条或午夜停。

**环境变量** (青龙 UI 配置)：

| 变量 | 必须 | 说明 |
|---|---|---|
| `HIFITI_ACCOUNT` | ✅ | 复用 Phase 1，取首个账号回帖 |
| `HIFITI_PASSWORD` | ✅ | 同上 |
| `BITABLE_APP_ID` | ❌ | 飞书自建应用 App ID（缺则跳过 Bitable 同步） |
| `BITABLE_APP_SECRET` | ❌ | 同上 |
| `BITABLE_APP_TOKEN` | ❌ | 多维表格 app_token（URL 里的） |
| `BITABLE_TABLE_ID` | ❌ | 表格 ID |

**Bitable 表格列**（在飞书新建表格时按此命名）：歌曲名 / 演唱者 / 帖子链接 / 夸克链接 / 夸克提取码 / 百度链接 / 百度提取码 / 抓取时间。

**青龙定时任务**（配置 → 定时任务 → 新建）：

| 任务名 | cron | 命令 | 通知模式 |
|---|---|---|---|
| hifiti 日榜采集 (主) | `0 21 * * *` | `task autosign/hifiti_rank_collect.py` | 成功+失败都通知 |
| hifiti 日榜采集 (重试) | `*/30 21-23 * * *` | `task autosign/hifiti_rank_collect.py` | 仅失败通知 |

**数据文件**：`scripts/records.db`（SQLite，gitignore；持久化在 podman volume）。

**手动运行**：

```bash
podman exec qinglong sh -c "cd /ql/data/scripts/autosign && python3 hifiti_rank_collect.py"
```
```

- [ ] **Step 4: Update top-level `README.md` 规划 section**

Find the "## 规划" section and replace with:

```markdown
## 规划

- **第一期** ✅ hifiti 登录 + 签到 + 飞书失败通知
- **第二期** ✅ hifiti 日榜 Top 2 自动回帖 + 网盘信息记录（SQLite + Bitable）
- **后续候选** 📅 网盘下载（夸克 quark-auto-save / 百度 BaiduPCS-Go）

详见设计文档第 11/16 节。
```

- [ ] **Step 5: Commit docs**

```bash
git -C /root/autosign add scripts/requirements.txt scripts/README.md README.md .gitignore
git -C /root/autosign commit -m "docs: Phase 2 deployment instructions + qinglong cron"
```

- [ ] **Step 6: 实测校准 — 抓 5 条真实日榜标题**

```bash
curl -sS https://www.hifiti.com/index-0-5.htm | grep -oP 'thread-\d+-\d+\.htm[^>]*>[^<]+' | head -10
```

逐条对照 §6.2 两条正则人工验证：

```bash
cd /root/autosign && python3 -c "
import sys; sys.path.insert(0, 'scripts')
from hifiti_parsing import parse_title
titles = ['<粘贴 5 条真实标题>']  # 替换为上面 curl 拿到的真实标题
for t in titles:
    print(t, '→', parse_title(t))
"
```

如命中率 < 90% 或解析错误，修 `_TITLE_RE_BRACKETED` / `_TITLE_RE_SIMPLE` 正则；同步更新 fixture 和测试。

- [ ] **Step 7: 实测校准 — 抓真实帖子页歌词块结构**

```bash
TID=<日榜上某真实帖子 ID>
curl -sS -b "bbs_token=<手动登录后获取的 token>" "https://www.hifiti.com/thread-${TID}-1.htm?sort=asc" -o /tmp/thread.html
grep -oE '<(pre|blockquote|div)[^>]*>' /tmp/thread.html | sort -u | head -20
```

如果歌词不在 `<pre>` / `<blockquote>` 而在某种 `<div class="...">`，扩展 `_LYRICS_CONTAINER_RES`。同步更新 fixture。

- [ ] **Step 8: 实测校准 — 网盘链接 + 提取码归属**

抓 5 个真实音乐帖（已回帖的），人工查看：夸克和百度的提取码是同一个还是两个独立的？

如果实测发现各有提取码：扩展 `parse_pan_links` 按链接周围 50 字符内的 alert-success 归属。

- [ ] **Step 9: 实测校准 — 回帖响应成功/过快关键字**

```bash
podman exec qinglong sh -c "cd /ql/data/scripts/autosign && python3 -c '
import sys; sys.path.insert(0, \".\")
from hifiti_common import build_session, login
from hifiti_reply import post_reply
import os
s = build_session()
login(s, os.environ[\"HIFITI_ACCOUNT\"].split(\"&\")[0], os.environ[\"HIFITI_PASSWORD\"].split(\"&\")[0])
o = post_reply(s, tid=<某测试帖>, message=\"测试\")
print(o)
'"
```

把响应文本里的成功/过快关键字写入 `REPLY_SUCCESS_KEYWORDS` / `REPLY_TOO_FAST_KEYWORDS`。

- [ ] **Step 10: 集成测试 — 容器内手动触发主脚本**

```bash
podman exec qinglong sh -c "cd /ql/data/scripts/autosign && python3 hifiti_rank_collect.py"
```

期望：

- 首次：抓到 2 条新记录写入 `records.db`，stdout 输出汇总卡片，exit 0
- 立即再跑：`today_count >= 2`，立即退出

```bash
podman exec qinglong sqlite3 /ql/data/scripts/autosign/records.db "SELECT tid,song_name,artist FROM records ORDER BY id DESC LIMIT 5"
```

期望：能看到刚抓到的记录。

- [ ] **Step 11: 在青龙 UI 创建 2 个定时任务**

1. 配置 → 定时任务 → 新建：
   - 名称：`hifiti 日榜采集 (主)`
   - 命令：`task autosign/hifiti_rank_collect.py`
   - 定时：`0 21 * * *`
   - 通知模式：成功+失败都通知

2. 再建一个：
   - 名称：`hifiti 日榜采集 (重试)`
   - 命令：`task autosign/hifiti_rank_collect.py`
   - 定时：`*/30 21-23 * * *`
   - 通知模式：仅失败通知

- [ ] **Step 12: Push final changes to GitHub**

```bash
git -C /root/autosign log --oneline | head -20
git -C /root/autosign push origin main
```

期望：所有 Phase 2 commits 推到 GitHub。

- [ ] **Step 13: 验收清单（设计文档 §13.3）**

逐项手工勾选：

- [ ] `hifiti_common.py` 抽取后，Phase 1 签到任务仍然成功
- [ ] 主跑产生 2 条新记录写入 SQLite
- [ ] 飞书收到汇总卡片，链接可点击
- [ ] Bitable 看到对应行（如已配置）
- [ ] 故意填错密码 → 飞书收到失败通知
- [ ] 第二天 21:00 自动触发，跑出新的 2 条
- [ ] 日榜上某帖出现两次（连续两天）→ 第二次被去重跳过
- [ ] 日榜出现一首英文歌 → SKIP_ENGLISH，不占配额

---

## 完工

所有 14 个任务完成后：

- 14 commits（每个 task 一个）
- 47+ 单元测试通过
- 真实集成测试通过
- 青龙定时任务已配置
- GitHub 主干已 push

后续维护：
- hifiti 接口变更 → 调正则 + fixture
- 新增字段 → 改 schema 加 migration（手工 ALTER TABLE）
- 升级 Phase 3：在此基础上加下载模块
