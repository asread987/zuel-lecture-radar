# -*- coding: utf-8 -*-
"""执行流水线：采集 → 补全正文 → 抽字段 → 判定经济学 → 去重 → 出报告 → 推送。

多用户串行执行、共享同一份抓取缓存与文章库；每个用户独立判定「本次该收哪些」。
"""
from __future__ import annotations

import datetime as dt
import fcntl
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path

from . import classify
from .config import UserConfig
from .extract import extract_fields, looks_image_only, make_summary, normalize_ocr
from .fetcher import Fetcher
from .models import Lecture, clean_title
from .notify import build_notifier
from .ocr import PosterOCR
from .report import sort_lectures, write_reports
from .sources import SourceSpec, bode, collect
from .store import Store


@contextmanager
def run_lock(path: Path):
    """进程级互斥锁：防止两个触发器（如 launchd 与手动执行）同时跑同一轮而重复推送。

    拿不到锁时立即让出（yield False），由调用方跳过本轮——因为「间隔 N 天」判定
    是幂等的，另一轮跑完就等价于本轮已完成。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "w")
    try:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        fh.write(f"{os.getpid()} {dt.datetime.now().isoformat(timespec='seconds')}\n")
        fh.flush()
        yield True
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()


@dataclass
class RunResult:
    user_id: str
    ok: bool = True
    total: int = 0
    econ: int = 0
    pushed: int = 0
    push_ok: bool = False
    reports: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    skipped_reason: str = ""

    def summary(self) -> str:
        if self.skipped_reason:
            return f"[{self.user_id}] 跳过：{self.skipped_reason}"
        p = f"，已推送 {self.pushed} 条" if self.pushed else ""
        return (f"[{self.user_id}] 收录 {self.total} 条（经济学 {self.econ} 条）{p}"
                f"，推送{'成功' if self.push_ok else '未执行/失败'}")


def _cutoff_date(user: UserConfig, state: dict, today: dt.date) -> dt.date:
    last = state.get("last_push_date")
    if last:
        try:
            return dt.date.fromisoformat(last)
        except ValueError:
            pass
    return today - dt.timedelta(days=max(1, user.lookback_days))


def run_user(
    user: UserConfig,
    all_sources: list[SourceSpec],
    *,
    dry_run: bool = False,
    no_push: bool = False,
    force: bool = False,
    verbose: bool = True,
    today: dt.date | None = None,
    log=print,
) -> RunResult:
    """跑一位用户。带进程级互斥，避免多个触发器并发导致重复推送。

    today 可注入一个「假想今天」，用于回溯验证「只保留未开始的讲座」这条规则
    （默认取真实日期）。
    """
    lock_path = user.resolve_path("data/.run.lock")
    with run_lock(lock_path) as got:
        if not got:
            res = RunResult(user_id=user.user_id, ok=True)
            res.skipped_reason = "已有另一轮正在运行（互斥锁被占用），本轮跳过"
            log(f"[{user.user_id}] {res.skipped_reason}")
            return res
        return _run_user_impl(user, all_sources, dry_run=dry_run, no_push=no_push,
                              force=force, verbose=verbose, today=today, log=log)


def _run_user_impl(
    user: UserConfig,
    all_sources: list[SourceSpec],
    *,
    dry_run: bool = False,
    no_push: bool = False,
    force: bool = False,
    verbose: bool = True,
    today: dt.date | None = None,
    log=print,
) -> RunResult:
    res = RunResult(user_id=user.user_id)
    today = today or dt.date.today()

    errs = user.validate()
    if errs:
        res.ok = False
        res.errors.extend(errs)
        log(f"[{user.user_id}] 配置有误：" + "；".join(errs))
        return res
    if not user.enabled and not force:
        res.skipped_reason = "enabled: false"
        return res

    store = Store(user.resolve_path(user.db))
    state = store.get_state(user.user_id)
    cutoff = _cutoff_date(user, state, today)
    sent = store.sent_uids(user.user_id)

    my_sources = [s for s in all_sources if s.enabled and user.wants_source(s)]
    if not my_sources:
        res.skipped_reason = "没有匹配到任何信息源"
        store.close()
        return res

    if verbose:
        log(f"[{user.user_id}] 信息源 {len(my_sources)} 个 ｜ 检索窗口 {cutoff} ~ {today} ｜ "
            f"上次推送 {state.get('last_push_date') or '（无，视为首次）'}")

    fetcher = Fetcher(
        cache_dir=user.resolve_path("data/cache"),
        verbose=verbose,
    )
    poster_ocr = PosterOCR(
        cache_dir=user.resolve_path("data/ocr_cache"),
        verbose=verbose,
        enabled=user.ocr_poster,
    )
    if verbose and user.ocr_poster and not poster_ocr.enabled:
        log("    提示：当前环境不支持 macOS Vision OCR，海报类讲座的时间将无法解析")

    raw: list[dict] = []
    channels: list[dict] = []      # 每个渠道的抓取状态，最终会列在报告末尾
    try:
        # ---- 阶段 0：采集各渠道的列表页 ----
        for spec in my_sources:
            # 用户配置的 max_pages 作为翻页上限（不能超过源自身声明的页数）
            page_spec = replace(spec, pages=min(spec.pages, user.max_pages))
            items = collect(page_spec, fetcher, cutoff=cutoff, max_items=user.max_items * 3)
            entry = {
                "key": spec.key,
                "name": spec.name,
                "group": spec.group or spec.name,
                "col": spec.name.split("·", 1)[1] if "·" in spec.name else "",
                "kind": "公众号" if spec.channel == "wechat" else "官网",
                "raw": 0, "kept": 0, "econ": 0, "status": "", "error": "",
            }
            bad = [i for i in items if i.get("_error")]
            if bad:
                entry.update(status="抓取失败", error=bad[0]["_error"])
                res.errors.append(f"{spec.name}: {bad[0]['_error']}")
                channels.append(entry)
                if verbose:
                    log(f"    ✗ {spec.name} {bad[0]['_error']}")
                continue
            for it in items:
                it["_spec"] = spec
            raw.extend(items)
            entry["raw"] = len(items)
            channels.append(entry)
            if verbose:
                log(f"    ✓ {spec.name:<16} {len(items):>3} 条")

        # ---- 阶段 1：去重 + 补齐正文 ----
        by_url: dict[str, dict] = {}
        for it in raw:
            if it.get("url"):
                by_url.setdefault(it["url"], it)
        candidates = [it for it in by_url.values() if _uid_of(it) not in sent]
        if verbose:
            log(f"[{user.user_id}] 候选 {len(candidates)} 条（已去重、剔除该用户收到过的），开始补全正文…")

        reuse = store.get_payloads([_uid_of(it) for it in candidates])
        prepared: list[dict] = []
        detail_used = 0
        for it in candidates:
            spec: SourceSpec = it["_spec"]
            old = reuse.get(_uid_of(it))
            if old and old.get("event_time") is not None:
                prepared.append({"it": it, "spec": spec, "lec": Lecture.from_dict(old),
                                 "reused": True, "text": "", "images": []})
                continue
            text = it.get("text") or ""
            images: list[str] = list(it.get("images") or [])
            if not text and detail_used < user.detail_limit:
                detail_used += 1
                try:
                    html = fetcher.get(it["url"], force_browser=(spec.type != "bode"))
                    detail = bode.parse_detail(html, it["url"])
                    text = detail.get("text", "")
                    images = detail.get("images", [])
                except Exception as exc:  # noqa: BLE001
                    if verbose:
                        log(f"      正文抓取失败 {it['url'][:60]} ({type(exc).__name__})")
            prepared.append({"it": it, "spec": spec, "lec": None, "reused": False,
                             "text": text, "images": images})

        # ---- 阶段 2：批量 OCR 海报 ----
        # 很多讲座预告的正文就是一张海报，「时间/地点」只存在于图里。
        # 用 macOS 内置 Vision 识别，一次性调 osascript 处理全部图片以摊薄进程开销。
        ocr_map: dict[str, str] = {}
        if user.ocr_poster:
            need: list[str] = []
            for p in prepared:
                if p["reused"] or not p["images"]:
                    continue
                prelim = extract_fields(p["text"],
                                        clean_title(p["it"].get("title") or ""),
                                        published=p["it"].get("published"))
                if prelim["event_time"] is None or len(p["text"]) < 100:
                    need.extend(p["images"])
            need = list(dict.fromkeys(need))
            if need:
                t0 = time.time()
                ocr_map = poster_ocr.ocr_urls(need)
                if verbose:
                    log(f"[{user.user_id}] 海报 OCR：{len(ocr_map)}/{len(need)} 张识别成功"
                        f"（{time.time() - t0:.1f}s）")

        # ---- 阶段 3：组装条目 + 过滤（只留还没开始的讲座）----
        lectures: list[Lecture] = []
        dropped_past = 0
        for p in prepared:
            it, spec = p["it"], p["spec"]
            lec = p["lec"]
            if lec is None:
                text, images = p["text"], p["images"]
                extra = [ocr_map[u] for u in images if ocr_map.get(u)]
                if extra:
                    text = (text + "\n" + normalize_ocr("\n".join(extra))).strip()
                lec = _build_lecture(it, spec, text, user, images, ocr_used=bool(extra))

            ok, why, category = _keep(lec, user, today)
            if not ok:
                if category == "past":
                    dropped_past += 1
                continue
            lec.upcoming = why
            lectures.append(lec)
            for c in channels:
                if c["key"] == spec.key:
                    c["kept"] += 1
                    if lec.is_econ:
                        c["econ"] += 1
                    break

        if verbose and dropped_past:
            log(f"[{user.user_id}] 已开始/已过期，丢弃 {dropped_past} 条")

        # 落库（便于多用户之间复用已抽取的字段）
        if lectures:
            store.save_articles(lectures)

        # ---- 渠道最终状态 ----
        for c in channels:
            if c["status"] == "抓取失败":
                continue
            if c["kept"] > 0:
                c["status"] = "已更新"
            elif c["raw"] > 0:
                c["status"] = "有内容但均已结束"
            else:
                c["status"] = "未更新讲座信息"

        lectures = sort_lectures(lectures)[:user.max_items]

        res.total = len(lectures)
        res.econ = sum(1 for x in lectures if x.is_econ)

        meta = {
            "today": today.isoformat(),
            "since": cutoff.isoformat(),
            "econ_only": user.econ_only,
            "channels": channels,
            "errors": res.errors,
            "dropped_past": dropped_past,
            "ocr_images": len(ocr_map),
        }
        res.reports = write_reports(user.resolve_path(user.report_dir), user.user_id, lectures, meta)
        if verbose:
            log(f"[{user.user_id}] 报告已生成：{res.reports.get('markdown')}")

        # ---- 推送 ----
        if no_push or dry_run:
            res.pushed = 0
            store.set_state(user.user_id, status="dry-run", count=res.total)
            if not dry_run:
                store.mark_sent(user.user_id, [x.uid for x in lectures])
            return res

        if not lectures:
            res.push_ok = True
            store.set_state(user.user_id, last_push_date=today.isoformat(),
                            status="无新内容", count=0)
            if verbose:
                log(f"[{user.user_id}] 本期无新增，按规则推进「上次推送日」并跳过推送")
            return res

        title = f"ZUEL讲座速递 {today.isoformat()}｜{res.total}条（经济学{res.econ}）"
        md = Path(res.reports["markdown"]).read_text(encoding="utf-8")
        ok_any = False
        for ch in user.channels:
            nf = build_notifier(ch)
            try:
                ok, detail = nf.send(title, md)
            except Exception as exc:  # noqa: BLE001
                ok, detail = False, f"{type(exc).__name__}: {exc}"
            ok_any = ok_any or ok
            store.log_push(user.user_id, res.total, nf.name, ok, detail,
                           res.reports.get("markdown", ""))
            log(f"    {'✓' if ok else '✗'} 推送[{nf.name}] {detail if not ok else ''}")

        res.push_ok = ok_any
        if ok_any:
            res.pushed = res.total
            store.mark_sent(user.user_id, [x.uid for x in lectures])
            store.set_state(user.user_id, last_push_date=today.isoformat(),
                            status="成功", count=res.total)
        else:
            store.set_state(user.user_id, status="推送失败", count=res.total)
            res.ok = False
        return res
    finally:
        fetcher.close()
        poster_ocr.close()
        store.close()


def _uid_of(it: dict) -> str:
    return Lecture(title=it.get("title", ""), url=it.get("url", ""),
                   source_key=it.get("source_key", ""),
                   source_name=it.get("source_name", "")).uid


def _build_lecture(it: dict, spec: SourceSpec, text: str, user: UserConfig,
                   images: list[str] | None = None, ocr_used: bool = False) -> Lecture:
    title = clean_title(it.get("title") or "")
    fields = extract_fields(text, title, published=it.get("published"))
    text = text or it.get("summary", "")
    images = images or []
    ok_econ, score, hits = classify.is_econ(
        text, title, source_bias=spec.econ_bias, threshold=user.econ_threshold)
    return Lecture(
        title=title,
        url=it.get("url", ""),
        source_key=spec.key,
        source_name=spec.name,
        channel=it.get("channel", "web"),
        published=it.get("published"),
        event_time=fields["event_time"],
        event_date=fields["event_date"],
        location=fields["location"],
        organizer=fields["organizer"],
        speaker=fields["speaker"],
        kind=fields["kind"],
        poster=images[0] if images else None,
        body_is_image=looks_image_only(text, images),
        ocr_used=ocr_used,
        summary=make_summary(text),
        is_econ=ok_econ,
        econ_score=score,
        econ_hits=hits,
        raw_title=it.get("title", ""),
    )


def _keep(lec: Lecture, user: UserConfig, today: dt.date) -> tuple[bool, str, str]:
    """是否收录某条讲座。返回 (是否收录, 说明文案, 分类)。

    分类用于统计：ok / past（已结束）/ filtered（不符合筛选条件）/ noise（非讲座内容）。
    """
    head = lec.title or ""
    if classify.is_noise(head):
        return False, "招生/公示类内容", "noise"
    if user.lecture_only and not classify.is_lecture(head, lec.summary):
        return False, "非讲座类内容", "noise"
    if user.drop_review and lec.kind == "review":
        return False, "已举办（回顾类）", "past"
    if user.upcoming_only:
        ok, why, category = classify.upcoming_status(
            lec, today,
            grace_days=user.upcoming_grace_days,
            unknown_date_policy=user.unknown_date_policy,
            unknown_date_max_age_days=user.unknown_date_max_age_days,
        )
        if not ok:
            return False, why, category
        lec.upcoming = why
    if user.econ_only and not lec.is_econ:
        return False, "非经济学相关", "filtered"
    return True, lec.upcoming or "", "ok"
