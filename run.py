#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ZUEL 讲座雷达 —— 命令行入口。

常用命令：
    python run.py doctor                  # 环境自检（浏览器 / 配置 / 依赖）
    python run.py sources                 # 查看内置信息源清单
    python run.py users                   # 查看用户及其下次预计推送时间
    python run.py init-user alice         # 生成一份新用户配置
    python run.py run --user alice --dry-run   # 只生成报告不推送（推荐先跑这个）
    python run.py run --user alice        # 正式跑一位用户
    python run.py due                     # 调度入口：只跑到期用户（供定时任务调用）
    python run.py all --dry-run           # 所有用户、只出报告
    python run.py logs                    # 最近推送记录
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from zuel_radar.config import (  # noqa: E402
    CHANNEL_SCHEMA, ROOT as PROJ_ROOT, load_sources, load_user, load_users,
    render_user_template,
)
from zuel_radar.fetcher import find_chrome  # noqa: E402
from zuel_radar.notify import build_notifier  # noqa: E402
from zuel_radar.pipeline import run_user  # noqa: E402
from zuel_radar.scheduler import due_users, next_run_hint  # noqa: E402
from zuel_radar.store import Store  # noqa: E402


def _ts() -> str:
    return dt.datetime.now().strftime("%H:%M:%S")


def log(msg: str) -> None:
    print(f"{_ts()} {msg}", flush=True)


def cmd_doctor(args) -> int:
    print("=" * 70)
    print("ZUEL 讲座雷达 · 环境自检")
    print("=" * 70)
    ok = True

    v = sys.version_info
    print(f"[{'✓' if v >= (3, 9) else '✗'}] Python {v.major}.{v.minor}.{v.micro}")
    ok &= v >= (3, 9)

    for mod in ("requests", "bs4", "yaml", "lxml"):
        try:
            __import__(mod)
            print(f"[✓] 依赖 {mod}")
        except ImportError:
            print(f"[✗] 依赖 {mod} 缺失 —— pip install -r requirements.txt")
            ok = False

    chrome = find_chrome()
    print(f"[{'✓' if chrome else '✗'}] 浏览器通道：{chrome or '未找到 Chrome/Chromium（被 WAF 保护的站点将无法抓取）'}")
    if chrome:
        print("    说明：zuel.edu.cn 各站点有 JS 反爬挑战，需要无头 Chrome 渲染。")

    from zuel_radar.ocr import vision_available
    ocr_ok = vision_available()
    print(f"[{'✓' if ocr_ok else '·'}] 海报 OCR："
          f"{'macOS Vision 可用' if ocr_ok else '不可用（非 macOS 或缺 osascript）'}")
    print("    说明：讲座预告的正文常是一张海报图，用它在图片里读「讲座时间/地点」。")
    print("          不可用时不影响抓取，只是这类条目会标注「时间待确认」。")

    try:
        sources = load_sources()
        print(f"[✓] 信息源 {len(sources)} 个（启用 {sum(1 for s in sources if s.enabled)} 个）")
    except Exception as exc:  # noqa: BLE001
        print(f"[✗] 信息源配置读取失败：{exc}")
        return 1

    users = load_users()
    print(f"[✓] 用户 {len(users)} 个")
    for u in users:
        errs = u.validate()
        if errs:
            print(f"    [✗] {u.user_id}: " + "；".join(errs))
            ok = False
        else:
            ch = ", ".join(c.get("type", "?") for c in u.channels)
            print(f"    [✓] {u.user_id}: 间隔 {u.interval_days} 天 / {u.push_time} / "
                  f"源 {len([s for s in sources if u.wants_source(s)])} 个 / 渠道 {ch}")

    out = PROJ_ROOT / "out"
    data = PROJ_ROOT / "data"
    for d in (out, data):
        d.mkdir(parents=True, exist_ok=True)
        print(f"[✓] 目录可写 {d}")

    print("=" * 70)
    print("自检" + ("通过" if ok else "未通过，请先处理上面的 ✗ 项"))
    return 0 if ok else 1


def cmd_sources(args) -> int:
    sources = load_sources()
    print(f"共 {len(sources)} 个信息源\n")
    for s in sources:
        flag = "启用" if s.enabled else "停用"
        print(f"· [{flag}] {s.key}")
        print(f"    名称：{s.name}   分组：{s.group or s.name}   类型：{s.type}   渠道：{s.channel}")
        print(f"    地址：{s.list_url or s.feed_url or (s.base + s.column)}")
        if s.econ_bias:
            print(f"    经济学倾向加分：+{s.econ_bias}")
        if s.note:
            print(f"    备注：{s.note}")
    return 0


def cmd_users(args) -> int:
    users = load_users()
    if not users:
        print("尚无用户配置。运行 python run.py init-user <标识> 创建。")
        return 0
    print(f"{'用户':<14}{'间隔':<8}{'推送时刻':<10}{'仅经济':<8}{'下次预计推送':<22}渠道")
    for u in users:
        store = Store(u.resolve_path(u.db))
        try:
            state = store.get_state(u.user_id)
        finally:
            store.close()
        ch = ",".join(c.get("type", "?") for c in u.channels)
        print(f"{u.user_id:<14}{str(u.interval_days) + '天':<8}{u.push_time:<10}"
              f"{'是' if u.econ_only else '否':<8}{next_run_hint(u, state):<22}{ch}")
    return 0


def cmd_init_user(args) -> int:
    d = PROJ_ROOT / "config" / "users"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{args.name}.yaml"
    if p.exists() and not args.force:
        print(f"已存在：{p}（加 --force 覆盖）")
        return 1
    p.write_text(render_user_template(args.name), encoding="utf-8")
    print(f"已创建用户配置：{p}")
    print()
    print("接下来两步：")
    print(f"  1) 编辑 {p.relative_to(PROJ_ROOT)}，把 channels 换成你自己的推送渠道")
    print("  2) 验证推送能不能通： python run.py test-push --user " + args.name)
    print("     然后干跑一次看报告： python run.py run --user " + args.name + " --dry-run")
    print()
    print("可用推送渠道（微信侧收消息，各人绑各自的微信）：")
    for k, v in CHANNEL_SCHEMA.items():
        print(f"  - {k}: {v['desc']}")
    return 0


def cmd_test_push(args) -> int:
    """发一条测试消息，验证推送凭据是否配好。

    分发给别人时这一步很关键：不用等到定时任务跑完才知道 key 填错了。
    """
    user = load_user(args.user)
    errs = user.validate()
    if errs:
        print("配置有误，先把这些改掉：")
        for e in errs:
            print(f"  ✗ {e}")
        return 1
    today = dt.date.today().isoformat()
    title = f"ZUEL 讲座雷达 · 测试推送（{today}）"
    md = (
        f"# {title}\n\n"
        "如果你在微信里看到这条消息，说明推送链路已经打通。\n\n"
        "- 用户：**{uid}**\n"
        "- 间隔：每 **{days}** 天一次\n"
        "- 订阅信息源：**{src}**\n"
        "- 仅经济学：**{econ}**\n\n"
        "接下来可以干跑一次看真实报告：`python run.py run --user {uid} --dry-run`\n"
    ).format(uid=user.user_id, days=user.interval_days,
             src="全部" if user.sources and "all" in [s.lower() for s in user.sources]
             else "、".join(user.sources),
             econ="是" if user.econ_only else "否")

    ok_any = False
    for ch in user.channels:
        nf = build_notifier(ch)
        try:
            ok, detail = nf.send(title, md)
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        ok_any = ok_any or ok
        print(f"  {'✓' if ok else '✗'} [{nf.name}] {detail}")
    print()
    print("测试推送" + ("成功：去看一下微信。" if ok_any else "失败：检查 key/token 是否填错、是否已绑定微信。"))
    return 0 if ok_any else 1


def _run_one(uid: str, args) -> int:
    user = load_user(uid)
    sources = load_sources()
    res = run_user(user, sources, dry_run=args.dry_run, no_push=args.no_push,
                   force=args.force, verbose=not args.quiet, today=_today_of(args),
                   log=log)
    print(res.summary())
    for path in res.reports.values():
        print(f"  → {path}")
    return 0 if res.ok else 1


def _today_of(args) -> dt.date | None:
    """解析 --today。用于回溯验证「只保留未开始的讲座」这条规则。"""
    raw = getattr(args, "today", None)
    if not raw:
        return None
    try:
        return dt.date.fromisoformat(raw)
    except ValueError:
        print(f"--today 需要 YYYY-MM-DD 格式，收到的是 {raw!r}")
        raise SystemExit(2)


def cmd_run(args) -> int:
    return _run_one(args.user, args)


def cmd_due(args) -> int:
    users = load_users()
    if not users:
        print("尚无用户配置。")
        return 0
    log(f"调度检查：{len(users)} 位用户" + ("（忽略推送时刻限制）" if args.ignore_time else ""))
    todo = due_users(users, ignore_time=args.ignore_time, log=log)
    if not todo:
        log("今天没有到期的用户，结束。")
        return 0
    rc = 0
    for u in todo:
        r = run_user(u, load_sources(), force=False, verbose=not args.quiet, log=log)
        print(r.summary())
        if not r.ok:
            rc = 1
    return rc


def cmd_all(args) -> int:
    rc = 0
    for u in load_users():
        r = run_user(u, load_sources(), dry_run=args.dry_run, no_push=args.no_push,
                     force=args.force, verbose=not args.quiet, today=_today_of(args),
                     log=log)
        print(r.summary())
        if not r.ok:
            rc = 1
    return rc


def cmd_logs(args) -> int:
    users = load_users()
    if not users:
        print("尚无用户配置。")
        return 0
    for u in users:
        store = Store(u.resolve_path(u.db))
        try:
            logs = store.recent_logs(args.limit, user_id=u.user_id)
            st = store.stats()
        finally:
            store.close()
        print(f"\n=== {u.user_id} ===")
        print(f"文章库：{st['articles']} 条（经济学 {st['econ']} 条）｜ 累计送达 {st['deliveries']} 条")
        for r in logs:
            print(f"  {r['run_at']}  [{r['channel']}] {'成功' if r['ok'] else '失败'} "
                  f"{r['count']} 条 {r['detail'][:60]}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="run.py", description="ZUEL 讲座雷达")
    p.add_argument("--version", action="version", version="ZUEL 讲座雷达 1.0.0")
    sub = p.add_subparsers(dest="cmd", required=True)
    def add_common(sp):
        sp.add_argument("--dry-run", action="store_true", help="只生成报告，不推送、不记录已送达")
        sp.add_argument("--no-push", action="store_true", help="不推送，但记录状态")
        sp.add_argument("--force", action="store_true", help="忽略 enabled:false 强制运行")
        sp.add_argument("--quiet", action="store_true", help="精简输出")
        sp.add_argument("--today", metavar="YYYY-MM-DD",
                        help="把「今天」设成指定日期，用于回溯验证只保留未开始讲座的规则")

    sp = sub.add_parser("doctor", help="环境自检")
    sp.set_defaults(func=cmd_doctor)

    sp = sub.add_parser("sources", help="列出信息源")
    sp.set_defaults(func=cmd_sources)

    sp = sub.add_parser("users", help="列出用户与下次推送时间")
    sp.set_defaults(func=cmd_users)

    sp = sub.add_parser("init-user", help="生成用户配置模板")
    sp.add_argument("name", help="用户标识（用于文件名）")
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(func=cmd_init_user)

    sp = sub.add_parser("run", help="运行单个用户")
    sp.add_argument("--user", required=True)
    add_common(sp)
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("test-push", help="发一条测试消息，验证推送凭据是否配好")
    sp.add_argument("--user", required=True)
    sp.set_defaults(func=cmd_test_push)

    sp = sub.add_parser("due", help="调度入口：只跑到期用户")
    sp.add_argument("--ignore-time", action="store_true", help="忽略 push_time 限制")
    add_common(sp)
    sp.set_defaults(func=cmd_due)

    sp = sub.add_parser("all", help="运行全部用户")
    add_common(sp)
    sp.set_defaults(func=cmd_all)

    sp = sub.add_parser("logs", help="查看推送记录")
    sp.add_argument("--limit", type=int, default=10)
    sp.set_defaults(func=cmd_logs)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    for name in ("dry_run", "no_push", "force", "quiet", "today"):
        if not hasattr(args, name):
            setattr(args, name, None if name == "today" else False)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
