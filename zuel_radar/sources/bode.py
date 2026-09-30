# -*- coding: utf-8 -*-
"""博达 CMS（Bode）站点解析器 —— ZUEL 各学院官网均使用该系统。

结构规律（实测）：
    栏目列表页  https://<sub>.zuel.edu.cn/<columnId>/list.htm  (分页 list2.htm, list3.htm ...)
    文章详情页  https://<sub>.zuel.edu.cn/<YYYY>/<MMdd>/c<columnId>a<articleId>/page.htm

难点：站点导航里也存在指向文章的链接（如「书记信箱」），
需要先定位到「真正的列表容器」再取条目，否则会混入噪声。
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..models import clean_title
from ..fetcher import html_to_text

ART_LINK_RE = re.compile(r"/\d{4}/\d{4}/c\d+a\d+/page\.(?:htm|psp)$")
# 文章 URL 自带发布日期： /2026/0427/c3258a428291/page.htm -> 2026-04-27
URL_DATE_RE = re.compile(r"/(\d{4})/(\d{2})(\d{2})/")
DATE_PATTERNS = [
    re.compile(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})"),
    re.compile(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})"),
]


def date_from_url(url: str) -> str | None:
    """从博达 CMS 的文章 URL 里取发布日期（形如 /2026/0427/）。

    比列表页文字更可靠：不少栏目列表页只显示「04-27」而省略年份，
    会导致下游无法判断讲座是否已经过期。URL 里的日期总是完整的。
    """
    m = URL_DATE_RE.search(url or "")
    if m:
        y, mo, d = m.groups()
        if 1 <= int(mo) <= 12 and 1 <= int(d) <= 31:
            return f"{y}-{mo}-{d}"
    return None


def _parse_date(text: str) -> str | None:
    for pat in DATE_PATTERNS:
        m = pat.search(text or "")
        if m:
            y, mo, d = m.groups()
            try:
                return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
            except ValueError:
                continue
    m = re.search(r"\b(\d{2})-(\d{2})\b", text or "")
    if m:
        return f"????-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    return None


# 页眉页脚里指向文章的导航链接，标题形如「书记信箱」。命中即丢弃。
TITLE_NOISE = ("信箱", "更多", "首页", "联系我们", "友情链接", "快速链接", "返回",
               "English", "院友", "En")


def _extract_items(scope, base_url: str) -> list[dict]:
    """从给定范围里按文档顺序取出条目 [{title, url, published}]。

    注意登记去重（seen）的时机：Bode 的一个 <li> 里有 3 个指向同一篇文章的 <a>
    （图片链接、标题链接、「更多」链接），其中图片链接没有文字。
    必须在**标题校验通过之后**才登记 URL，否则图片链接会先占住 URL，
    真正带标题的那个被当成重复丢掉 —— 结果整页解析出 0 条。
    """
    items: list[dict] = []
    seen: set[str] = set()
    for a in scope.find_all("a", href=True):
        full = urljoin(base_url, a["href"].strip())
        if not ART_LINK_RE.search(full) or full in seen:
            continue
        title = clean_title(a.get_text(" ", strip=True) or a.get("title", ""))
        if not title or len(title) < 6:
            continue
        if any(n in title for n in TITLE_NOISE) and len(title) <= 12:
            continue                       # 导航类链接（如「书记信箱/院长信箱」）
        seen.add(full)
        # 日期可能在同一行的兄弟节点里；URL 里的日期总是完整的，优先用它
        ctx = ""
        node = a
        for _ in range(3):
            if node.parent is None:
                break
            node = node.parent
            ctx = node.get_text(" ", strip=True)
            if _parse_date(ctx):
                break
        ctx_date = _parse_date(ctx)
        url_date = date_from_url(full)
        # 上下文只有「04-27」这种缺年份的日期时，用 URL 里的完整日期覆盖
        published = url_date or ctx_date
        if url_date and ctx_date and not ctx_date.startswith("????"):
            published = ctx_date
        items.append({"title": title, "url": full, "published": published})
    return items


def parse_list(html: str, base_url: str) -> list[dict]:
    """从栏目列表页解析出条目列表 [{title, url, published}]，保持页面顺序。

    定位列表容器的策略（两个极端都不能用，实测踩过）：
      · 只取「最深」的合格容器 → 会误选页脚的「友情链接」小方框（里面也有几条文章链接）；
      · 只取「链接最多」的容器 → 会一直退到 <body>，把导航全兜进来。
    因此取「文章链接数接近全局最多（≥80%）的容器中最深的那个」。
    """
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    anchors = []
    for a in soup.find_all("a", href=True):
        full = urljoin(base_url, a["href"].strip())
        if ART_LINK_RE.search(full):
            anchors.append((a, full))
    if not anchors:
        return []

    counts: dict[int, int] = {}
    holder: dict[int, object] = {}
    for a, _ in anchors:
        for parent in a.parents:
            if getattr(parent, "name", None) in (None, "[document]"):
                continue
            key = id(parent)
            counts[key] = counts.get(key, 0) + 1
            holder[key] = parent

    max_n = max(counts.values())
    region = None
    qualified = [k for k, n in counts.items() if n >= max(3, int(max_n * 0.6))]
    if qualified:
        deepest = max(qualified, key=lambda k: len(list(holder[k].parents)))
        region = holder[deepest]

    items = _extract_items(region, base_url) if region is not None else []
    if len(items) < max(3, int(max_n * 0.5)):
        # 容器判定不理想（取到的条目明显少于页面里的文章链接总数）时，退回全文档扫描
        items = _extract_items(soup, base_url)
    return items


# WAF 会在页面里放一个空的 <div id="content_container">，正文实际渲染在它之外。
# 因此正文容器选择器刻意不含 #content_container，改用博达 CMS 真正的正文节点。
BODY_SELECTORS = (
    ".wp_articlecontent", "#vsb_content", ".article-content", "#article_content",
    ".v_news_content", "form[name='_newscontent_fromname']", ".entry", ".read",
)

# 模板/图标类图片，不属于正文内容
BAD_IMG_RE = re.compile(
    r"(?:/tpl/|/template|/_upload/tpl/|"
    r"/(?:logo|logof|icon|banner|nav|head|footer|bg|btn|arrow|close|share|"
    r"weixin|weibo|qrcode|ewm|dzt|xx)[^/]*$)",
    re.I,
)


def parse_detail(html: str, url: str) -> dict:
    """从详情页抽取正文文本、标题与正文配图。

    注意：ZUEL 大量讲座预告把「时间/地点/主讲人」做成一张海报图片贴在正文里，
    DOM 里几乎没有可用文字。因此这里同时返回 images，供上层把海报展示到报告里。
    """
    if not html:
        return {"text": "", "title": "", "images": [], "url": url}
    soup = BeautifulSoup(html, "lxml")

    title = ""
    for sel in ("h1.arti_title", "h1", ".article-title", ".arti_title", "#artibodytitle", "title"):
        node = soup.select_one(sel)
        if node and node.get_text(strip=True):
            title = clean_title(node.get_text(" ", strip=True))
            if len(title) > 4:
                break

    body_node = None
    for sel in BODY_SELECTORS:
        node = soup.select_one(sel)
        if node and (node.get_text(strip=True) or node.find("img")):
            body_node = node
            break

    html_part = str(body_node) if body_node is not None else html
    text = html_to_text(html_part)

    # 采集正文配图（用于「正文即海报」的场景）
    images: list[str] = []
    img_root = body_node if body_node is not None else soup
    for img in img_root.find_all("img"):
        src = (img.get("src") or img.get("data-src") or img.get("data-original") or "").strip()
        if not src or src.startswith("data:") or src.lower().endswith(".svg"):
            continue
        full = urljoin(url, src)
        if BAD_IMG_RE.search(full):
            continue
        w = img.get("width") or ""
        if w.isdigit() and int(w) < 120:          # 小图标跳过
            continue
        if full not in images:
            images.append(full)
    # 正文图（/_upload/article/）优先排在前面
    images.sort(key=lambda u: 0 if "/_upload/article/" in u else 1)
    return {"text": text, "title": title, "images": images[:3], "url": url}


def list_page_url(column: str, base: str, page: int) -> str:
    """构造栏目第 page 页的 URL（page=1 为 list.htm）。"""
    base = base.rstrip("/")
    if page <= 1:
        return f"{base}/{column}/list.htm"
    return f"{base}/{column}/list{page}.htm"
