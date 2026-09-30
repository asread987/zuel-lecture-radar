# -*- coding: utf-8 -*-
"""ZUEL 讲座雷达 —— 数据模型。"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict
from typing import Any


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


@dataclass
class Lecture:
    """一条讲座/学术活动记录。"""

    title: str
    url: str
    source_key: str                      # 源标识，如 jjxy-lectures
    source_name: str                     # 显示名，如 经济学院
    channel: str = "web"                 # web / wechat / rss
    published: str | None = None         # 列表页发布日期 YYYY-MM-DD
    event_time: str | None = None        # 讲座时间（正文抽取）
    event_date: str | None = None        # 讲座日期（规范化 YYYY-MM-DD，可解析时）
    location: str | None = None          # 地点
    organizer: str | None = None         # 主办方
    speaker: str | None = None           # 主讲人
    kind: str = "other"                  # preview(预告) / review(回顾) / other
    poster: str | None = None            # 海报/正文配图 URL（很多讲座预告的详情就是一张海报）
    body_is_image: bool = False          # 正文以图片为主，文字抽取不到细节
    ocr_used: bool = False               # 是否用海报 OCR 补齐了字段
    upcoming: str | None = None          # 开场状态：即将开始 / 今天 / 时间待确认
    summary: str = ""                    # 正文摘要
    is_econ: bool = False                # 是否经济学相关
    econ_score: float = 0.0
    econ_hits: list[str] = field(default_factory=list)
    raw_title: str = ""

    @property
    def uid(self) -> str:
        """稳定唯一 ID：优先用文章 URL，退化到 源+标题。"""
        basis = self.url or f"{self.source_key}|{self.title}"
        return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["uid"] = self.uid
        return d

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Lecture":
        d = {k: v for k, v in d.items() if k in Lecture.__dataclass_fields__}
        return Lecture(**d)


def clean_title(title: str) -> str:
    """去掉列表页标题里常见的栏目前缀/后缀噪声。"""
    t = _norm(title)
    t = re.sub(r"^[【\[]([^】\]]{2,10})[】\]]\s*", "", t)      # 去掉开头的【学术讲座】
    t = re.sub(r"\s*\d{4}-\d{2}-\d{2}\s*$", "", t)             # 去掉结尾日期
    t = re.sub(r"\s*\(\d+\)\s*$", "", t)
    return t.strip(" -|·") or _norm(title)
