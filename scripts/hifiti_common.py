"""共享 hifiti 客户端工具：session/login/retry/snippet/mask。

Phase 1 (hifiti_sign.py) 和 Phase 2 (hifiti_rank_collect.py) 都从这里导入。
"""
from __future__ import annotations

import hashlib
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
        return f"{name[:2]}***@{domain}"
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

    # hifiti 登录页 JS 强制 $.md5(password) 后再提交，明文会被服务器拒绝（code=password）
    hashed = hashlib.md5(password.encode("utf-8")).hexdigest()
    resp = request_with_retry(
        session,
        "POST",
        LOGIN_URL,
        data={"email": account, "password": hashed},
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
