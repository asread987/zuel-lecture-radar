# -*- coding: utf-8 -*-
"""经济学相关性判定 + 活动/噪声判定。

思路：加权关键词命中 + 源级倾向加分。
    - STRONG（3 分）：学科本身就是经济学的词，如 经济学 / 财政 / 金融 / 收入分配 / 全要素生产率
    - MEDIUM（2 分）：强经济学语境但不排他的词，如 市场 / 贸易 / 投资 / 计量 / 劳动力
    - WEAK  （1 分）：经济学常见但泛化的词，如 政策 / 实证 / 均衡 / 激励
    标题命中权重 ×1.5（标题比正文更能代表主题），源级 econ_bias 每点 +2 分。
    is_econ = 总分 >= threshold（默认 3.0）。
"""
from __future__ import annotations

import datetime as dt
import re

STRONG = [
    "经济学", "经济", "宏观经济", "微观经济", "计量经济", "产业经济", "区域经济",
    "劳动经济", "数字经济", "数量经济", "制度经济", "发展经济", "世界经济",
    "政治经济学", "经济史", "经济增长", "经济周期", "经济韧性", "经济思想",
    "财政", "税收", "税务", "税制", "增值税", "所得税", "财税", "预算",
    "金融", "货币", "银行", "保险", "证券", "资本市场", "资产定价", "公司金融",
    "国际金融", "汇率", "债券", "股票", "基金", "融资",
    "国际贸易", "国际投资", "关税", "世界贸易", "全球价值链", "对外直接投资",
    "收入分配", "财富分配", "社会保障", "养老保险", "医疗保险", "养老金",
    "劳动力市场", "人力资本", "就业", "失业", "人口老龄化",
    "博弈论", "机制设计", "一般均衡", "福利经济学", "全要素生产率",
    "反垄断", "产业组织", "平台经济", "共同富裕", "乡村振兴", "城镇化",
    "货币政策", "财政政策", "宏观审慎", "量化宽松", "通货膨胀",
    "双重差分", "断点回归", "工具变量", "因果推断", "随机对照",
]

MEDIUM = [
    "市场", "贸易", "投资", "消费", "价格", "定价", "统计", "计量", "面板数据",
    "博弈", "效率", "福利", "政策评估", "劳动力", "人力", "人口", "产业",
    "产业链", "供应链", "价值链", "企业", "上市", "补贴", "债务", "杠杆",
    "增长", "配置", "均衡", "激励", "风险", "竞争", "产权", "交易成本",
    "财政收支", "转移支付", "税负", "营商环境", "生产率",
]

WEAK = [
    "政策", "制度", "改革", "数据", "模型", "实证", "识别", "估计", "效应",
    "机制", "结构", "转型", "发展", "治理", "公共", "监管", "分配", "成本",
    "收益", "评估", "指数", "调查", "抽样", "回归",
]

# 明显不是讲座的栏目噪声
NOISE_WORDS = [
    "公示", "名单", "招生", "招聘", "答辩", "考核", "推免", "奖学金", "评选",
    "拟录取", "选课", "调剂", "复试", "录取", "预算公开", "决算", "招标",
    "采购", "年检", "学籍", "评奖", "聘任", "党政联席",
]

LECTURE_WORDS = [
    "讲座", "讲坛", "讲堂", "论坛", "报告会", "学术报告", "研讨会", "沙龙",
    "工作坊", "Workshop", "seminar", "Seminar", "研讨会", "圆桌", "名家讲坛",
    "新锐论坛", "文澜大讲堂", "预告", "开讲",
]

_TITLE_WEIGHT = 1.5


def _hits(text: str, words: list[str]) -> list[str]:
    return [w for w in words if w in text]


def score(text: str, title: str = "", source_bias: int = 0) -> tuple[float, list[str]]:
    """返回 (经济学分, 命中词列表)。"""
    t = (title or "") + " " + (text or "")[:4000]
    st = set(_hits(t, STRONG))
    md = set(_hits(t, MEDIUM))
    wk = set(_hits(t, WEAK))

    s = len(st) * 3.0 + len(md) * 2.0 + len(wk) * 1.0
    # 标题命中额外加权
    th = set(_hits(title or "", STRONG)) | set(_hits(title or "", MEDIUM))
    s += len(th) * 3.0 * (_TITLE_WEIGHT - 1.0)
    s += source_bias * 2.0
    return round(s, 2), sorted(st | md)


def is_econ(text: str, title: str = "", source_bias: int = 0, threshold: float = 3.0) -> tuple[bool, float, list[str]]:
    s, hits = score(text, title, source_bias)
    return s >= threshold, s, hits


def is_noise(title: str, text: str = "") -> bool:
    """是否是招生/公示类的非讲座内容。"""
    head = (title or "")[:50]
    return any(w in head for w in NOISE_WORDS) and not any(w in head for w in LECTURE_WORDS)


def is_lecture(title: str, text: str = "") -> bool:
    hay = (title or "") + " " + (text or "")[:400]
    return any(w in hay for w in LECTURE_WORDS)


def econ_reason(title: str, text: str, source_bias: int = 0) -> str:
    """给出一条简短的「为什么判为经济学相关」的说明，用于报告展示。"""
    s, hits = score(text, title, source_bias)
    if not hits:
        return "无明确经济学关键词"
    return "、".join(hits[:6]) + f"（得分 {s}）"


def normalize_kw(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


# ── 开场状态判定：我们只要「还没开始」的讲座 ────────────────────────────────

def _iso_date(s: str | None) -> "dt.date | None":
    if not s or s.startswith("????"):
        return None
    try:
        return dt.date.fromisoformat(s)
    except ValueError:
        return None


def upcoming_status(
    lec,
    today: "dt.date",
    *,
    grace_days: int = 0,
    unknown_date_policy: str = "keep",
    unknown_date_max_age_days: int = 14,
) -> tuple[bool, str, str]:
    """判断一条讲座是否「还没开始」，返回 (是否保留, 状态文案, 分类)。

    分类取值：upcoming（还没开始）/ past（已结束或已办）/ unknown（日期读不出来但保留）。

    规则：
      1. 能解析出讲座日期时——日期早于 today - grace_days 即视为已结束，丢弃。
      2. 日期未知时（常见于「正文是海报」且 OCR 也没读出时间）：
         - 回顾类（kind=review）直接丢；
         - 若配置为 drop，直接丢；
         - 若配置为 keep，再看发布日期：发布已超过 unknown_date_max_age_days 天，
           说明这条预告大概率已经过期，丢弃；否则保留并标注「时间待确认」。

    grace_days 用来给「刚开场不久/今天」的活动留一点余地，默认 0 表示只留今天及以后。
    """
    d = _iso_date(getattr(lec, "event_date", None))
    if d is not None:
        if d < today - dt.timedelta(days=max(0, grace_days)):
            return False, f"已结束（{d.isoformat()}）", "past"
        if d == today:
            return True, "今天开始", "upcoming"
        return True, f"{d.isoformat()} 开始", "upcoming"

    if getattr(lec, "kind", "") == "review":
        return False, "已举办（回顾类）", "past"
    if unknown_date_policy == "drop":
        return False, "日期未知", "unknown"

    pub = _iso_date(getattr(lec, "published", None))
    if pub is not None:
        age = (today - pub).days
        if age > unknown_date_max_age_days:
            return False, f"日期未知且已发布 {age} 天", "past"
    return True, "时间待确认（见海报/原文）", "unknown"

