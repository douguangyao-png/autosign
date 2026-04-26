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
