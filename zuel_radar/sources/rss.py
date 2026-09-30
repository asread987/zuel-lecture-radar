# -*- coding: utf-8 -*-
"""RSS / Atom 源解析 —— 用于承接微信公众号渠道。

微信公众号没有公开的内容 API，实务上最稳的做法是经由中间服务把公众号转成
RSS/Atom 再消费，常见选择：
  - 自建 RSSHub（route: /wechat/... 或 /wechat2rss/...）
  - wechat2rss（自部署，输出标准 Atom）
  - Feeddd / 其它第三方公众号转 RSS 服务
本模块对「任何标准 RSS/Atom」都能解析，因此上面任选其一即可；
也支持直接把若干篇微信文章 URL 作为手工源（type: wechat_manual）。

搜狗微信搜索（sogou_wechat）作为无中间服务时的尽力而为兜底，稳定性较差，
默认关闭，需要显式在源配置里开启。
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from ..models import clean_title

NS = {"atom": "http://www.w3.org/2005/Atom", "dc": "http://purl.org/dc/elements/1.1/",
      "content": "http://purl.org/rss/1.0/modules/content/"}


def _strip_html(s: str) -> str:
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", s or "")
    s = re.sub(r"<[^>]+>", " ", s)
    s = s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"\s+", " ", s).strip()


def _date_iso(raw: str) -> str | None:
    if not raw:
        return None
    raw = raw.strip()
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if m:
        return m.group(0)
    try:
        return parsedate_to_datetime(raw).strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001
        return None


def parse_feed(xml_text: str) -> list[dict]:
    """解析 RSS 2.0 或 Atom，返回 [{title, url, published, summary}]。"""
    if not xml_text:
        return []
    try:
        root = ET.fromstring(xml_text.strip())
    except ET.ParseError:
        # 有些源带 BOM 或前导垃圾
        m = re.search(r"<(\?xml|rss|feed)[\s\S]*", xml_text)
        if not m:
            return []
        try:
            root = ET.fromstring(m.group(0))
        except ET.ParseError:
            return []

    items: list[dict] = []
    tag = root.tag.lower()
    if tag.endswith("feed"):  # Atom
        for e in root.findall("atom:entry", NS):
            title = (e.findtext("atom:title", default="", namespaces=NS) or "").strip()
            link = ""
            for ln in e.findall("atom:link", NS):
                if ln.get("rel") in (None, "alternate") and ln.get("href"):
                    link = ln.get("href")
                    break
            published = _date_iso(e.findtext("atom:updated", default="", namespaces=NS)
                                  or e.findtext("atom:published", default="", namespaces=NS))
            summary = _strip_html(e.findtext("atom:summary", default="", namespaces=NS)
                                  or e.findtext("atom:content", default="", namespaces=NS))
            if title:
                items.append({"title": clean_title(title), "url": link,
                              "published": published, "summary": summary[:800]})
    else:  # RSS 2.0
        for e in root.iter():
            if not e.tag.lower().endswith("item"):
                continue
            title = (e.findtext("title") or "").strip()
            link = (e.findtext("link") or "").strip()
            desc = _strip_html(e.findtext("description") or e.findtext("content:encoded", default="", namespaces=NS))
            published = _date_iso(e.findtext("pubDate") or e.findtext("dc:date", default="", namespaces=NS))
            if title:
                items.append({"title": clean_title(title), "url": link,
                              "published": published, "summary": desc[:800]})
    return items
