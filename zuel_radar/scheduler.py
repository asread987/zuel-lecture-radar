# -*- coding: utf-8 -*-
"""调度判定：决定某位用户「今天该不该推」。

规则（对应需求「每间隔用户自定义的天数触发一次，在触发当天 20:00 推送」）：
    调度器由系统定时任务在每天固定时刻唤起居（默认 20:00）；
    每位用户自行判断——距上次成功推送是否已满 interval_days 天。
    满足则本次运行该用户，不满足则跳过。首次运行（无历史状态）立即执行。
"""
from __future__ import annotations

import datetime as dt

from .config import UserConfig
from .store import Store


def parse_hhmm(s: str) -> tuple[int, int]:
    try:
        h, m = s.split(":")
        return int(h), int(m)
    except Exception:  # noqa: BLE001
        return 20, 0


def is_due(user: UserConfig, state: dict, now: dt.datetime | None = None,
           ignore_time: bool = False) -> tuple[bool, str]:
    """返回 (是否到期, 原因说明)。"""
    now = now or dt.datetime.now()
    today = now.date()

    if not user.enabled:
        return False, "用户已停用（enabled: false）"

    last = state.get("last_push_date")
    if not last:
        return True, "首次运行，立即执行"

    try:
        last_d = dt.date.fromisoformat(last)
    except ValueError:
        return True, f"上次推送日期无法解析（{last}），按到期处理"

    days = (today - last_d).days
    if days < 0:
        return True, "上次推送日期在未来，按到期处理"
    if days < user.interval_days:
        return False, f"距上次推送仅 {days} 天，未满 {user.interval_days} 天"

    if not ignore_time:
        hh, mm = parse_hhmm(user.push_time)
        if (now.hour, now.minute) < (hh, mm):
            return False, f"今天是触发日，但尚未到推送时刻 {user.push_time}"
    return True, f"距上次推送 {days} 天，已满 {user.interval_days} 天"


def due_users(users: list[UserConfig], now: dt.datetime | None = None,
              ignore_time: bool = False, log=print) -> list[UserConfig]:
    """筛出当天需要执行的用户。"""
    now = now or dt.datetime.now()
    out: list[UserConfig] = []
    for u in users:
        store = Store(u.resolve_path(u.db))
        try:
            state = store.get_state(u.user_id)
        finally:
            store.close()
        ok, why = is_due(u, state, now=now, ignore_time=ignore_time)
        log(f"  {'→ 执行' if ok else '· 跳过'}  {u.user_id:<12} {why}")
        if ok:
            out.append(u)
    return out


def next_run_hint(user: UserConfig, state: dict, now: dt.datetime | None = None) -> str:
    """给出一条人类可读的「下次预计推送时间」。"""
    now = now or dt.datetime.now()
    last = state.get("last_push_date")
    if not last:
        return f"今天 {user.push_time}（首次）"
    try:
        last_d = dt.date.fromisoformat(last)
    except ValueError:
        return "未知"
    nxt = last_d + dt.timedelta(days=user.interval_days)
    if nxt < now.date():
        nxt = now.date()
    return f"{nxt.isoformat()} {user.push_time}"
