# -*- coding: utf-8 -*-
"""微信公众号渠道解析。

mp.weixin.qq.com 正文页的结构（实测稳定）：
    <h1 id="activity-name">标题</h1>
    <div id="js_content">正文</div>
    发布时间来自页面脚本里的 var ct = "1694000000"; 或 meta[property=og:...]
公众号文章没有公开 API，因此实务上推荐经由 RSSHub / wechat2rss 转成 Feed，
在 sources 里用 type: rss 配置 feed_url 即可（见 rss.py 的说明）。
本模块另外提供两条路径：
  1) wechat_manual —— 手工登记若干微信文章 URL，直接抓正文；
  2) sogou_wechat —— 用搜狗微信搜索按关键词兜底检索（稳定性差，默认关闭）。
"""
from __future__ import annotations

import re
import time
from urllib.parse import quote

from bs4 import BeautifulSoup

from ..models import clean_title
from ..fetcher import html_to_text


def parse_mp_article(html: str, url: str = "") -> dict:
    """解析一篇 mp.weixin.qq.com 文章，返回 {title, text, published}。"""
    if not html:
        return {"title": "", "text": "", "published": None}
    soup = BeautifulSoup(html, "lxml")

    title = ""
    node = soup.select_one("#activity-name") or soup.select_one("h1.rich_media_title") \
        or soup.select_one("meta[property='og:title']")
    if node:
        title = node.get("content") if node.name == "meta" else node.get_text(" ", strip=True)
    if not title and soup.title:
        title = soup.title.get_text(strip=True)
    title = clean_title(title or "")

    published = None
    m = re.search(r'var\s+ct\s*=\s*"?(\d{9,11})"?', html)
    if m:
        published = time.strftime("%Y-%m-%d", time.localtime(int(m.group(1))))
    else:
        m = re.search(r'"(?:publish_time|publishTime)"\s*:\s*"(\d{9,11})"', html)
        if m:
            published = time.strftime("%Y-%m-%d", time.localtime(int(m.group(1))))
        else:
            mt = soup.select_one("meta[property='article:published_time']")
            if mt and mt.get("content"):
                mm = re.search(r"(\d{4}-\d{2}-\d{2})", mt["content"])
                published = mm.group(1) if mm else None

    body = soup.select_one("#js_content")
    text = html_to_text(str(body)) if body else html_to_text(html)
    return {"title": title, "text": text, "published": published}


def sogou_search_url(keyword: str, page: int = 1) -> str:
    """搜狗微信搜索的文章检索页。"""
    return f"https://weixin.sogou.com/weixin?type=2&query={quote(keyword)}&page={page}"


def parse_sogou_list(html: str) -> list[dict]:
    """解析搜狗微信搜索结果页，返回 [{title, url, published, summary}]。"""
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    out: list[dict] = []
    for li in soup.select("ul.news-list li"):
        a = li.select_one("h3 a")
        if not a:
            continue
        href = a.get("href", "")
        if href.startswith("/link?"):
            href = "https://weixin.sogou.com" + href
        title = clean_title(a.get_text(" ", strip=True))
        # 搜狗的发布时间用 JS 拼装，退化为取 s-p 里的时间戳脚本
        sp = li.select_one(".s-p")
        published = None
        if sp:
            js = sp.get("t") or ""
            if js.isdigit():
                published = time.strftime("%Y-%m-%d", time.localtime(int(js)))
            else:
                m = re.search(r"(\d{4}-\d{2}-\d{2})", sp.get_text(" ", strip=True))
                published = m.group(1) if m else None
        summary = ""
        p = li.select_one(".txt-info")
        if p:
            summary = p.get_text(" ", strip=True)[:300]
        if title and href:
            out.append({"title": title, "url": href, "published": published, "summary": summary})
    return out
