# -*- coding: utf-8 -*-
"""从讲座详情正文中抽取结构化字段：时间 / 地点 / 主办方 / 主讲人，并判断预告还是回顾。

抽取策略：
    1. 标签定位——在正文前部（默认前 6000 字）按「时间：」「地点：」等标签抓取同一行剩余内容；
    2. 同行无内容则顺延到下一非空行；
    3. 遇到下一个标签关键词即截断，避免把后续字段一并吞掉；
    4. 时间为空时，用日期正则在前 2000 字里兜底。
"""
from __future__ import annotations

import datetime as dt
import re

SCAN_LEN = 6000
FALLBACK_LEN = 2500

LABELS_TIME = ("讲座时间", "报告时间", "研讨时间", "会议时间", "活动时间", "举办时间",
               "论坛时间", "开讲时间", "时间", "日期")
LABELS_PLACE = ("讲座地点", "报告地点", "研讨地点", "会议地点", "活动地点", "举办地点",
                "论坛地点", "地点")
# 注意：不要把「会议室」当标签——它几乎总是出现在地点值内部
#（如「文泉楼北603会议室」），当标签会导致值被截断成「文泉楼北603」。
LABELS_ORG = ("主办单位", "主办方", "主办", "承办单位", "承办", "协办单位")
LABELS_SPEAKER = ("主讲嘉宾", "主讲人", "主讲专家", "报告人", "演讲人", "主讲", "嘉宾")

ALL_LABELS = (LABELS_TIME + LABELS_PLACE + LABELS_ORG + LABELS_SPEAKER
              + ("主持人", "题目", "摘要", "浏览次数", "文章来源", "发布者", "更新时间"))

# 标签必须出现在行首/空白/标点之后，避免误匹配「发布时间：」「更新时间：」里的「时间」
LABEL_BOUNDARY = r"(?:^|[\s\n。；;，,、|·（(【《\-—])"
# 若标签前紧跟这些词，说明它属于复合词（发布时间/更新时间…），不是我们要的字段
BLOCK_PREFIX = ("发布", "更新", "浏览", "截止", "报名", "开课", "考试", "打印",
                "阅读", "公示", "值班", "办公", "咨询", "联系", "预约", "使用")

DATE_RE = re.compile(r"(\d{4})\s*[年\-/]\s*(\d{1,2})\s*[月\-/]\s*(\d{1,2})\s*日?")
MD_RE = re.compile(r"(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*日")
TIME_RANGE_RE = re.compile(r"\d{1,2}\s*[:：]\s*\d{2}\s*(?:[-~—－至]\s*\d{1,2}\s*[:：]\s*\d{2})?")


# 发布元信息行（发布者/发布时间/浏览次数/文章来源…）。这些行必须从正文里剔除，
# 否则「时间」字段会错误地抓到「发布时间」，摘要里也会塞满这些噪声。
META_LINE_RE = re.compile(
    r"^\s*(?:发布者|发布时间|更新时间|发布日期|浏览次数|文章来源|来源|作者|编辑|"
    r"审核|责任编辑|字体|打印|分享|关闭)\s*[:：]?.*$",
    re.M,
)


def strip_meta_lines(text: str) -> str:
    """剔除发布元信息行，返回干净的正文。"""
    return META_LINE_RE.sub("", text or "")


# OCR 常见的形近字误识，主要集中在字段标签上（正文内容错字不影响抽取）。
OCR_FIXES = (
    ("立办单位", "主办单位"), ("止办单位", "主办单位"), ("主办单立", "主办单位"),
    ("承办单立", "承办单位"), ("协办单立", "协办单位"),
    ("讲座时问", "讲座时间"), ("讲屋时间", "讲座时间"), ("报告时问", "报告时间"),
    ("研讨时问", "研讨时间"), ("活动时问", "活动时间"),
    ("讲座地立", "讲座地点"), ("报告地立", "报告地点"), ("地立", "地点"),
    ("时问", "时间"), ("主 题", "主题"),
)


def normalize_ocr(text: str) -> str:
    """修正海报 OCR 文本里高频出现的形近字误识。"""
    if not text:
        return ""
    for bad, good in OCR_FIXES:
        if bad in text:
            text = text.replace(bad, good)
    return text


def _clean_value(v: str, maxlen: int = 80) -> str:
    v = re.sub(r"\s+", " ", (v or "")).strip(" 　:：,，。;；-—")
    # 截断到下一个标签出现处
    for lab in ALL_LABELS:
        idx = v.find(lab)
        if 0 < idx < len(v):
            v = v[:idx]
    v = v.strip(" 　:：,，。;；-—")
    return v[:maxlen]


def _grab(text: str, labels: tuple[str, ...], maxlen: int = 80) -> str | None:
    """按标签抓取字段值。标签需处于行首/空白/标点之后，且不是「发布时间」这类复合词。"""
    for lab in labels:
        pat = re.compile(LABEL_BOUNDARY + re.escape(lab) + r"\s*[:：]\s*")
        for m in pat.finditer(text):
            lab_start = text.find(lab, m.start(), m.end())
            pre = text[max(0, lab_start - 4):lab_start]
            if any(pre.endswith(bp) for bp in BLOCK_PREFIX):
                continue
            seg = text[m.end():]
            val = _clean_value(seg.split("\n", 1)[0], maxlen)
            if val:
                return val
            rest = [ln.strip() for ln in seg.split("\n")[1:6] if ln.strip()]
            if rest:
                val = _clean_value(rest[0], maxlen)
                if val:
                    return val
    return None


def normalize_event_date(s: str | None, ref_year: int | None = None) -> str | None:
    """把「2026年9月15日」「9月15日」等归一化为 ISO 日期。"""
    if not s:
        return None
    m = DATE_RE.search(s)
    if m:
        y, mo, d = (int(x) for x in m.groups())
        return f"{y:04d}-{mo:02d}-{d:02d}"
    m = MD_RE.search(s)
    if m:
        mo, d = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{ref_year or 0:04d}-{mo:02d}-{d:02d}" if ref_year else f"????-{mo:02d}-{d:02d}"
    return None


def classify_kind(title: str, text: str = "") -> str:
    """判断是「讲座预告」还是「活动回顾」。"""
    head = (title or "")[:60]
    body = (text or "")[:1200]
    # 回顾类的强特征：新闻报道口吻
    review_words = ("成功举办", "顺利召开", "顺利举行", "圆满结束", "圆满举办", "落下帷幕",
                    "回顾", "纪要", "报道", "侧记", "通讯员", "本次讲座由", "本次讲座邀请",
                    "进行了题为", "作了题为", "应邀来", "为我院师生", "开展名为",
                    "成功举行", "举行", "举办")
    preview_words = ("预告", "即将", "将举办", "将举行", "将召开", "将开讲", "通知", "邀请函",
                     "欢迎参加", "报名", "开讲", "讲座信息", "将于")
    if any(w in head for w in preview_words):
        return "preview"
    if any(w in head for w in review_words):
        return "review"
    if any(w in body for w in ("成功举办", "顺利举行", "圆满结束", "通讯员", "进行了题为",
                               "本次讲座由", "作了题为")):
        return "review"
    if any(w in body for w in preview_words):
        return "preview"
    # 标题里同时含时间和「讲座」，通常也是预告
    if re.search(r"\d{1,2}\s*月\s*\d{1,2}\s*日", head):
        return "preview"
    return "other"


def extract_fields(text: str, title: str = "", published: str | None = None) -> dict:
    """抽取结构化字段。返回 dict(event_time, event_date, location, organizer, speaker, kind)。

    published 是列表页给出的发布日期（YYYY-MM-DD）。它的作用很关键：
    正文里常见「4月23日上午」这种**不带年份**的写法，只有借发布年份补全，
    才能判断这场讲座到底过去了没有。
    """
    raw = text or ""
    body = strip_meta_lines(raw)[:SCAN_LEN]
    head = (title or "")[:200]
    ref_year = None
    if published and re.match(r"^\d{4}", published):
        ref_year = int(published[:4])

    event_time = _grab(body, LABELS_TIME, 60)
    location = _grab(body, LABELS_PLACE, 60)
    organizer = _grab(body, LABELS_ORG, 60)
    speaker = _grab(body, LABELS_SPEAKER, 50)

    # 标题里常直接带「讲座时间：...」
    if not event_time:
        event_time = _grab(head, LABELS_TIME, 60)
    # 兜底：正文里的第一个日期。注意必须在剔除元信息行之后搜索，
    # 否则会抓到「发布时间」而把讲座时间搞错。
    if not event_time:
        m = DATE_RE.search(body[:FALLBACK_LEN])
        if m:
            event_time = m.group(0)
        else:
            # 再退一步：只有「4月23日」这种月日写法，年份稍后按发布日期补
            m = MD_RE.search(body[:FALLBACK_LEN])
            if m:
                event_time = m.group(0)

    if event_time:
        m = re.search(re.escape(event_time[:10]), body)
        if m:
            tail = body[m.end():m.end() + 40]
            tr = TIME_RANGE_RE.search(tail)
            if tr and not TIME_RANGE_RE.search(event_time):
                event_time = f"{event_time} {tr.group(0)}"
    if event_time:
        event_time = re.sub(r"\s+", " ", event_time).strip()[:80] or None

    event_date = normalize_event_date(event_time or "", ref_year=ref_year)
    if event_date and event_date.startswith("????"):
        m = re.search(r"(20\d{2})\s*年", head + body[:500])
        if m:
            event_date = m.group(1) + event_date[4:]

    # 跨年修正：发布在年末、讲座在次年初（如 12 月发 1 月的预告），
    # 用发布年补年会算成今年的 1 月——那已经过去 300 多天，显然是次年的。
    if event_date and not event_date.startswith("????") and published and published != event_date:
        try:
            if event_date < published:
                gap = (dt.date.fromisoformat(published) - dt.date.fromisoformat(event_date)).days
                if gap > 180:
                    event_date = f"{int(event_date[:4]) + 1}{event_date[4:]}"
        except ValueError:
            pass

    if organizer:
        organizer = re.sub(r"^(单位|方)\s*[:：]?\s*", "", organizer)

    return {
        "event_time": event_time or None,
        "event_date": event_date,
        "location": location or None,
        "organizer": organizer or None,
        "speaker": speaker or None,
        "kind": classify_kind(title, raw),
    }


# 正文里常见的模板噪声（校情简介、导航、页脚）。命中即整行丢弃。
NOISE_PHRASES = (
    "教育部直属", "211工程", "985工程", "双一流", "湖北省共建", "重点建设高校",
    "学校首页", "学校主页", "书记信箱", "院长信箱", "书记、院长信箱", "学院概况",
    "师资队伍", "人才培养", "快速链接", "友情链接", "常用链接", "校外链接",
    "版权所有", "Copyright", "邮编", "联系电话", "邮箱：", "地址：", "首页", "更多>>",
    "学院简介", "组织机构", "现任领导", "历任领导", "历史沿革", "党政领导",
    "系别设置", "研究机构", "交流合作", "学生工作", "党群建设", "基层党建",
    "校友之家", "校友工作", "社会服务", "教学项目", "国际交流", "办事指南",
    "通知公告", "学院新闻", "科研动态", "学术活动", "科研机构", "培训动态",
    "工作动态", "招生", "EN", "Add:",
)


def make_summary(text: str, limit: int = 300) -> str:
    """生成正文摘要：剔除元信息行与模板噪声、去重复行，取信息密度较高的一段。"""
    if not text:
        return ""
    clean = strip_meta_lines(text)
    out: list[str] = []
    used = 0
    for raw in clean.split("\n"):
        ln = raw.strip()
        if len(ln) < 24:                       # 导航项普遍很短
            continue
        if any(p in ln for p in NOISE_PHRASES):
            continue
        if re.match(r"^[\d\s\-—:：/年月日]+$", ln):   # 纯日期行
            continue
        if out and (ln == out[-1] or ln in out[-1] or out[-1] in ln):
            continue                           # 去重复/互相包含的行（标题常被重复三遍）
        out.append(ln)
        used += len(ln)
        if used >= limit:
            break
    return re.sub(r"\s+", " ", " ".join(out)).strip()[:limit]


def looks_image_only(text: str, images: list[str]) -> bool:
    """正文是不是以海报图片为主（文字抽取不到有效细节）。"""
    if not images:
        return False
    body = make_summary(text, 400)
    return len(body) < 120
