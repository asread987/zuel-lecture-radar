#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
源发现工具 —— 从各学院官网的栏目页导航中挖出「讲座类」栏目 ID。

为什么这样做：
    zuel.edu.cn 各站点前置了 JS 反爬挑战（检测 navigator.webdriver /
    HeadlessChrome / 软件 WebGL 渲染器，并用 crypto.subtle 校验），
    直接 requests 只能拿到约 46KB 的空壳页。必须用无头 Chrome 渲染。
    首页导航是 JS 异步填充的、且栏目名不统一，因此改为：
      用本研贯通通知页里暴露的文章 URL 反推出各学院的一个已知栏目 ID 作为种子，
      渲染该栏目页，从侧边导航里读出全部栏目名 + ID。

用法：
    python scripts/discover_sources.py            # 探测并打印
    python scripts/discover_sources.py --json data/sources_probe.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

from bs4 import BeautifulSoup  # noqa: E402

from zuel_radar.fetcher import Fetcher  # noqa: E402

# 学院 -> 已知栏目 ID（取自校研究生院「本研贯通」通知页里各学院的官网文章链接）
SEEDS: dict[str, tuple[str, str]] = {
    "jjxy":     ("经济学院", "2744"),
    "csxy":     ("财政税务学院", "7103"),
    "finance":  ("金融学院", "1150"),
    "jmxy":     ("经济贸易学院", "17786"),
    "wls":      ("文澜学院", "3770"),
    "law":      ("法学院", "16281"),
    "cjs":      ("刑事司法学院", "17544"),
    "jjjcxy":   ("纪检监察学院", "13954"),
    "ipschool": ("知识产权学院", "17084"),
    "sil":      ("国际法学院", "17137"),
    "gsxy":     ("工商管理学院", "8384"),
    "kjxy":     ("会计学院", "16198"),
    "ggglxy":   ("公共管理学院", "11247"),
    "wgyxy":    ("外国语学院", "10336"),
    "xwcb":     ("新闻与文化传播学院", "6928"),
    "tsxy":     ("统计与数学学院", "4801"),
    "xagx":     ("信息工程学院", "2091"),
    "mkszyxy":  ("马克思主义学院", "1369"),
    "zxy":      ("哲学院", "4567"),
    "yjsy":     ("研究生院", "6250"),
}

KEYWORDS = ["讲座", "讲坛", "讲堂", "论坛", "学术活动", "学术预告", "学术信息", "学术交流", "沙龙"]
SKIP = ["通知公告", "学院新闻", "新闻", "公示", "招生"]
LIST_RE = re.compile(r"/(\d+)/list\w*\.(?:htm|psp)")


def discover(fetcher: Fetcher, sub: str, name: str, seed: str) -> dict:
    base = f"https://{sub}.zuel.edu.cn/"
    url = f"{base}{seed}/list.htm"
    res = {"subdomain": sub, "name": name, "base": base, "seed_url": url,
           "ok": False, "error": None, "candidates": [], "all_columns": []}
    try:
        html = fetcher.get(url)
    except Exception as exc:  # noqa: BLE001
        res["error"] = f"{type(exc).__name__}: {exc}"
        return res
    if not html:
        res["error"] = "空响应"
        return res
    res["ok"] = True
    soup = BeautifulSoup(html, "lxml")
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        text = re.sub(r"\s+", "", a.get_text(strip=True) or "")
        full = urljoin(base, a["href"].strip())
        m = LIST_RE.search(full)
        if not m or sub not in full or full in seen or not text or len(text) > 12:
            continue
        seen.add(full)
        col = m.group(1)
        res["all_columns"].append({"column": col, "text": text, "url": full})
        if any(k in text for k in KEYWORDS) and not any(s == text for s in SKIP):
            res["candidates"].append({"column": col, "text": text, "url": full})
    return res


def count_items(fetcher: Fetcher, url: str) -> int:
    try:
        html = fetcher.get(url)
        return len(set(re.findall(r"/\d{4}/\d{4}/c\d+a\d+/page\.(?:htm|psp)", html)))
    except Exception:  # noqa: BLE001
        return -1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default="data/sources_probe.json")
    ap.add_argument("--workers", type=int, default=3)
    args = ap.parse_args()

    fetcher = Fetcher(cache_dir=PROJ / "data" / "cache", verbose=False,
                      use_cache=True, cache_ttl=86400)
    print(f"浏览器通道: {fetcher.chrome}\n")
    print(f"探测 {len(SEEDS)} 个学院（每院 1 次渲染）...\n")

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(lambda kv: discover(fetcher, kv[0], kv[1][0], kv[1][1]),
                                SEEDS.items()))
    for r in results:
        if not r["ok"]:
            print(f"✗ {r['name']:10s} {r.get('error')}")
            continue
        for c in r["candidates"]:
            c["item_count"] = count_items(fetcher, c["url"])
        print(f"✓ {r['name']:10s} {r['base']}  栏目{len(r['all_columns'])}个")
        if r["candidates"]:
            for c in sorted(r["candidates"], key=lambda x: -x["item_count"]):
                mark = "★" if c["item_count"] > 0 else "·"
                print(f"    {mark} c{c['column']:<6} 条数={c['item_count']:>3}  〔{c['text']}〕 {c['url']}")
        else:
            names = "、".join(x["text"] for x in r["all_columns"][:12])
            print(f"    未匹配到讲座栏目。该页可见栏目：{names}")

    Path(args.json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    fetcher.close()
    print(f"\n已写入 {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
