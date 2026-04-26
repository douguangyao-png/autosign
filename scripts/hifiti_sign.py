#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hifiti 论坛每日签到

环境变量:
  HIFITI_ACCOUNT   账号/邮箱，多账号用 & 分隔 (最多 10 个)
                   例: a@x.com&b@y.com
  HIFITI_PASSWORD  密码，多密码用 & 分隔，数量与账号严格一致
                   例: pw1&pw2

退出码:
  0  全部账号签到成功 (包含"已签到"情况)
  1  任一账号失败或配置错误

通知由青龙面板的"通知设置"按失败策略触发，脚本本身不调用
飞书/其他 webhook。
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

BASE_URL = "https://www.hifiti.com"
LOGIN_URL = f"{BASE_URL}/user-login.htm"
SIGN_URL = f"{BASE_URL}/sg_sign.htm"

USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/147.0.0.0 Mobile Safari/537.36"
)

REQUEST_TIMEOUT = 20
NETWORK_RETRIES = 1
RETRY_DELAY = 3
ACCOUNT_DELAY_MIN = 1.5
ACCOUNT_DELAY_MAX = 3.0

MAX_ACCOUNTS = 10
ACCOUNT_SEPARATOR = "&"

LOGIN_ERROR_KEYWORDS = ("密码", "错误", "不存在", "不正确", "失败", "不匹配", "验证码")
SIGN_SUCCESS_KEYWORDS = ("成功", "已签", "已经", "奖励", "获得", "+")
SIGN_ERROR_KEYWORDS = ("未登录", "请登录", "请先登录", "登录失效", "登录已过期")

MAX_LOG_SNIPPET = 500


@dataclass
class AccountResult:
    account: str
    status: str
    detail: str


def build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
    )
    return session


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


def snippet(text: str) -> str:
    return (text or "")[:MAX_LOG_SNIPPET].replace("\n", " ").strip()


def login(session: requests.Session, account: str, password: str) -> Tuple[bool, str]:
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
    return (
        False,
        f"status={resp.status_code} 无 bbs_token cookie; body='{body}'",
    )


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


STATUS_ICON = {
    "OK": "✅",
    "LOGIN_FAIL": "❌",
    "SIGN_FAIL": "❌",
    "NETWORK_ERROR": "⚠️",
}


def mask(account: str) -> str:
    if "@" in account:
        name, _, domain = account.partition("@")
        if len(name) <= 2:
            return f"{name[:1]}***@{domain}"
        return f"{name[:2]}***@{domain}"
    if len(account) <= 3:
        return f"{account[:1]}***"
    return f"{account[:3]}***"


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
