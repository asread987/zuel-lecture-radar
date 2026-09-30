# -*- coding: utf-8 -*-
"""
信息源注册表与统一采集接口。

每个源在 config/sources.yaml 里声明，采集后统一产出「原始条目」：
    {title, url, published, summary, text?}
再由 pipeline 负责正文补全、字段抽取、经济学判定与去重。

支持的 type：
    bode          博达 CMS 栏目列表页（ZUEL 各学院官网，主力渠道）
    rss           标准 RSS/Atom（承接微信公众号经 RSSHub/wechat2rss 转出的 Feed）
    wechat_manual 手工登记的微信公众号文章 URL 列表
    sogou_wechat  搜狗微信关键词检索（兜底，稳定性差）
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

from . import bode, rss, wechat

SOURCE_TYPES = ("bode", "rss", "wechat_manual", "sogou_wechat")


@dataclass
class SourceSpec:
    key: str
    name: str
    type: str
    enabled: bool = True
    base: str = ""            # bode: 站点根，如 https://jjxy.zuel.edu.cn/
    column: str = ""          # bode: 栏目 ID
    list_url: str = ""        # bode/rss: 显式列表页地址（优先于 base+column）
    pages: int = 2            # bode: 最多翻几页
    feed_url: str = ""        # rss
    urls: list[str] = field(default_factory=list)   # wechat_manual
    keywords: list[str] = field(default_factory=list)  # sogou_wechat
    group: str = ""           # 归属：学院名
    econ_bias: int = 0        # 源级经济学倾向加分（经济类学院 +1）
    channel: str = "web"      # 展示用：web / wechat
    note: str = ""

    @staticmethod
    def from_dict(d: dict) -> "SourceSpec":
        allowed = set(SourceSpec.__dataclass_fields__)
        return SourceSpec(**{k: v for k, v in d.items() if k in allowed})

    def list_urls(self) -> list[str]:
        """需要抓取的列表页 URL 列表。"""
        if self.type == "bode":
            if self.list_url:
                # 显式地址 + 分页派生
                urls = [self.list_url]
                m = re.match(r"^(.*/list)(\d*)(\.(?:htm|psp))$", self.list_url)
                if m and self.pages > 1:
                    base, _, ext = m.group(1), m.group(2), m.group(4)
                    for p in range(2, self.pages + 1):
                        urls.append(f"{base}{p}.{ext}")
                return urls
            if self.base and self.column:
                return [bode.list_page_url(self.column, self.base, p)
                        for p in range(1, max(1, self.pages) + 1)]
        return []

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def _parse_loose_date(s: str | None) -> dt.date | None:
    if not s or "????" in s:
        return None
    try:
        return dt.date.fromisoformat(s)
    except ValueError:
        return None


def collect(spec: SourceSpec, fetcher, cutoff: dt.date | None = None,
            max_items: int = 60) -> list[dict]:
    """采集一个源，返回原始条目列表（已按 cutoff 过滤，按时间倒序靠前优先）。"""
    items: list[dict] = []
    try:
        if spec.type == "bode":
            items = _collect_bode(spec, fetcher, cutoff, max_items)
        elif spec.type == "rss":
            items = _collect_rss(spec, fetcher, max_items)
        elif spec.type == "wechat_manual":
            items = _collect_wechat_manual(spec, fetcher, max_items)
        elif spec.type == "sogou_wechat":
            items = _collect_sogou(spec, fetcher, max_items)
        else:
            raise ValueError(f"未知源类型: {spec.type}")
    except Exception as exc:  # noqa: BLE001
        return [{"_error": f"{type(exc).__name__}: {exc}"}]

    for it in items:
        it.setdefault("source_key", spec.key)
        it.setdefault("source_name", spec.name)
        it.setdefault("channel", spec.channel)
        it.setdefault("group", spec.group or spec.name)

    if cutoff:
        kept = []
        for it in items:
            d = _parse_loose_date(it.get("published"))
            if d is None or d >= cutoff:
                kept.append(it)
        items = kept
    return items[:max_items]


def _collect_bode(spec: SourceSpec, fetcher, cutoff, max_items) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for url in spec.list_urls():
        html = fetcher.get(url)
        rows = bode.parse_list(html, url)
        if not rows:
            break
        fresh = 0
        for r in rows:
            if r["url"] in seen:
                continue
            seen.add(r["url"])
            out.append(r)
            fresh += 1
            d = _parse_loose_date(r.get("published"))
            if d is None or (cutoff and d >= cutoff):
                pass
        # 本页没有「新于 cutoff」的条目，且已有内容，则停止翻页
        page_fresh = [r for r in rows
                      if (_parse_loose_date(r.get("published")) or dt.date.max) >= (cutoff or dt.date.min)]
        if cutoff and not page_fresh and len(out) > 0:
            break
        if fresh == 0:
            break
    return out


def _collect_rss(spec: SourceSpec, fetcher, max_items) -> list[dict]:
    if not spec.feed_url:
        return []
    html = fetcher.get(spec.feed_url, force_browser=True)
    return rss.parse_feed(html)[:max_items]


def _collect_wechat_manual(spec: SourceSpec, fetcher, max_items) -> list[dict]:
    out: list[dict] = []
    for u in spec.urls[:max_items]:
        try:
            html = fetcher.get(u, force_browser=True)
            meta = wechat.parse_mp_article(html, u)
            out.append({"title": meta.get("title") or u, "url": u,
                        "published": meta.get("published"), "text": meta.get("text", "")})
        except Exception:  # noqa: BLE001
            continue
    return out


def _collect_sogou(spec: SourceSpec, fetcher, max_items) -> list[dict]:
    out: list[dict] = []
    for kw in spec.keywords:
        try:
            html = fetcher.get(wechat.sogou_search_url(kw), force_browser=True)
            out.extend(wechat.parse_sogou_list(html))
        except Exception:  # noqa: BLE001
            continue
    dedup: dict[str, dict] = {}
    for it in out:
        dedup.setdefault(it["url"], it)
    return list(dedup.values())[:max_items]
