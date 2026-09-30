# -*- coding: utf-8 -*-
"""报告生成：把结构化讲座清单渲染成 Markdown / HTML / JSON / CSV。

排版原则：
    1. 经济学相关讲座单独成章并置顶（用户要求「优先突出」）；
    2. 每条记录固定展示：标题、时间、地点、主办方、原文链接；
    3. 预告类（kind=preview）标 ★ 并排在前面，已举办的回顾类靠后；
    4. 报告末尾保留抓取统计与失败源，方便排查。
"""
from __future__ import annotations

import csv
import datetime as dt
import html as html_mod
import json
from pathlib import Path

from .models import Lecture

CN_NUM = "一二三四五六七八九十"


def _fmt_date(s: str | None) -> str:
    if not s:
        return "—"
    return s if not s.startswith("????") else f"?{s[4:]}"


def sort_key(lec: Lecture):
    """排序用的主键：预告优先 → 经济学相关优先 → 经济学分高优先。

    发布日期的新旧不放在这里，因为字符串无法直接表达「倒序」；
    改为在 sort_lectures 里先按日期倒序排一遍（Python 排序是稳定的，顺序会被保留）。
    """
    kind_rank = {"preview": 0, "other": 1, "review": 2}.get(lec.kind, 1)
    return (kind_rank, 0 if lec.is_econ else 1, -(lec.econ_score or 0), lec.title)


def sort_lectures(lectures: list[Lecture]) -> list[Lecture]:
    """先按发布日期倒序（新的在前），再稳定地按「预告/经济学/得分」分组。"""
    out = sorted(lectures, key=lambda x: str(x.published or "0000-00-00"), reverse=True)
    out.sort(key=sort_key)
    return out


def _econ_badge(lec: Lecture) -> str:
    return "🔴 经济学" if lec.is_econ else "⚪ 其他"


def _kind_badge(lec: Lecture) -> str:
    return {"preview": "★ 预告", "review": "已举办", "other": ""}.get(lec.kind, "")


def _fmt_time(lec: Lecture) -> str:
    if lec.event_time:
        return lec.event_time
    if lec.event_date and not lec.event_date.startswith("????"):
        return lec.event_date
    return "见原文海报" if lec.body_is_image else "见原文"


def _fmt_place(lec: Lecture) -> str:
    if lec.location:
        return lec.location
    return "见原文海报" if lec.body_is_image else "见原文"


def _channel_line(c: dict) -> str:
    """把一个渠道渲染成一行状态文案，如「财政税务学院 · 学术讲座（官网）— 已更新 3 条」。"""
    label = c.get("group") or c.get("name") or "未知渠道"
    col = c.get("col")
    head = f"{label} · {col}" if col else label
    status = c.get("status") or "—"
    if c.get("error"):
        return f"{head}（{c.get('kind', '官网')}）— {status}：{c['error'][:120]}"
    if status == "已更新":
        extra = f"（经济学 {c['econ']} 条）" if c.get("econ") else ""
        return f"{head}（{c.get('kind', '官网')}）— 已更新 {c['kept']} 条{extra}"
    if status == "有内容但均已结束":
        return f"{head}（{c.get('kind', '官网')}）— 有 {c['raw']} 条讲座，但均已结束"
    return f"{head}（{c.get('kind', '官网')}）— {status}"


def _updated_channels(lectures: list[Lecture], channels: list[dict]) -> list[dict]:
    """本期真正贡献了条目的渠道（按新增条数降序）。"""
    return sorted([c for c in channels if c.get("kept", 0) > 0],
                  key=lambda c: (-c["kept"], c.get("group", "")))


def render_markdown(user_id: str, lectures: list[Lecture], meta: dict) -> str:
    econ = [x for x in sort_lectures(lectures) if x.is_econ]
    other = [x for x in sort_lectures(lectures) if not x.is_econ]
    today = meta.get("today") or dt.date.today().isoformat()
    channels = meta.get("channels") or []
    updated = _updated_channels(lectures, channels)

    L: list[str] = []
    L.append(f"# 中南财经政法大学 · 即将开始的讲座（{today}）")
    L.append("")
    # ── 开头：一句话说清哪些学院有更新 ──────────────────────────
    if updated:
        L.append(f"**{len(updated)} 个渠道有更新**，共 **{len(lectures)}** 条"
                 f"（经济学相关 **{len(econ)}** 条）：")
        L.append("")
        for c in updated:
            extra = f"，经济学 {c['econ']} 条" if c.get("econ") and c["econ"] != c["kept"] else ""
            label = c.get("group") or c["name"]
            col = f" · {c['col']}" if c.get("col") else ""
            L.append(f"- **{label}**{col} — {c['kept']} 条{extra}")
    else:
        L.append(f"_本期没有找到即将开始的讲座（已抓取 {len(channels)} 个渠道）。_")
    L.append("")

    notes = [f"只收录 **{today}** 及以后开始的讲座"]
    if meta.get("dropped_past"):
        notes.append(f"已剔除 {meta['dropped_past']} 条已开始/已过期的")
    if meta.get("ocr_images"):
        notes.append(f"其中 {meta['ocr_images']} 张讲座海报已用 OCR 读出时间地点")
    if meta.get("econ_only"):
        notes.append("已按设置只保留经济学相关")
    L.append("> " + " ｜ ".join(notes) + "。")
    L.append("")

    def block(items: list[Lecture], start_no: int = 1) -> None:
        for i, lec in enumerate(items, start_no):
            tags = []
            if lec.upcoming:
                tags.append(lec.upcoming)
            if lec.ocr_used:
                tags.append("海报OCR")
            head = f"### {i}. {lec.title}"
            if tags:
                head += "　`" + " · ".join(tags) + "`"
            L.append(head)
            L.append(f"- **时间**：{_fmt_time(lec)}")
            L.append(f"- **地点**：{_fmt_place(lec)}")
            L.append(f"- **主办方**：{lec.organizer or lec.source_name}")
            if lec.speaker:
                L.append(f"- **主讲人**：{lec.speaker}")
            if lec.summary:
                L.append(f"- **摘要**：{lec.summary[:160]}")
            L.append(f"- **来源**：{lec.source_name}（{lec.channel}）｜ 发布 {_fmt_date(lec.published)}")
            L.append(f"- **原文**：[{lec.url}]({lec.url})")
            if lec.poster:
                L.append(f"- **海报**：[查看海报图]({lec.poster})")
            if lec.is_econ and lec.econ_hits:
                L.append(f"- **经济学线索**：{'、'.join(lec.econ_hits[:6])}（得分 {lec.econ_score}）")
            L.append("")

    if econ:
        L.append(f"## 一、经济学相关讲座（{len(econ)} 条）")
        L.append("")
        block(econ, 1)

    if other:
        idx = CN_NUM[1] if econ else CN_NUM[0]
        L.append(f"## {idx}、其他学科讲座（{len(other)} 条）")
        L.append("")
        block(other, 1)

    # ── 末尾：全部渠道的抓取状态清单 ────────────────────────────
    L.append("---")
    L.append("")
    if channels:
        ok_n = sum(1 for c in channels if c.get("status") == "已更新")
        fail_n = sum(1 for c in channels if c.get("status") == "抓取失败")
        L.append(f"### 渠道抓取状态（共 {len(channels)} 个：已更新 {ok_n}，抓取失败 {fail_n}）")
        L.append("")
        for c in sorted(channels, key=lambda x: (x.get("group") or "", x.get("key") or "")):
            L.append(f"- {_channel_line(c)}")
        L.append("")
    L.append(f"_报告生成时间：{dt.datetime.now():%Y-%m-%d %H:%M:%S} ｜ ZUEL 讲座雷达_")
    return "\n".join(L)


HTML_CSS = """
:root{--bg:#f7f5f2;--card:#fff;--ink:#1c1c1e;--muted:#6b6b70;--line:#e3ded6;
--econ:#b3261e;--econ-bg:#fdf2f1;--star:#c8860d;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:16px/1.75 -apple-system,BlinkMacSystemFont,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif}
.wrap{max-width:920px;margin:0 auto;padding:40px 22px 80px}
header{border-bottom:3px solid var(--econ);padding-bottom:18px;margin-bottom:26px}
h1{font-size:27px;margin:0 0 8px;letter-spacing:.4px}
.sub{color:var(--muted);font-size:14px}
.pill{display:inline-block;background:var(--econ);color:#fff;border-radius:999px;
padding:2px 11px;font-size:12.5px;margin-left:8px;vertical-align:2px}
h2{font-size:20px;margin:38px 0 14px;padding-left:12px;border-left:5px solid var(--econ)}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;
padding:16px 18px;margin:12px 0;box-shadow:0 1px 2px rgba(0,0,0,.03)}
.card.econ{border-left:4px solid var(--econ);background:linear-gradient(180deg,var(--econ-bg),#fff 46%)}
.t{font-size:17px;font-weight:650;margin:0 0 10px;line-height:1.5}
.meta{display:grid;grid-template-columns:88px 1fr;gap:4px 10px;font-size:14.5px}
.k{color:var(--muted)}
a{color:var(--econ);text-decoration:none;word-break:break-all}
a:hover{text-decoration:underline}
.tag{display:inline-block;font-size:12px;border-radius:6px;padding:1px 8px;margin-right:6px;
background:#f0ece6;color:var(--muted)}
.tag.econ{background:var(--econ);color:#fff}
.tag.when{background:#e8f1ec;color:#1f6b45;border:1px solid #cfe3d7}
.tag.ocr{background:#eef0fb;color:#3b4bab;border:1px solid #d9dcf3}
.headline{font-size:16.5px;margin:0 0 10px}
.chans{list-style:none;padding:0;margin:0;display:flex;flex-wrap:wrap;gap:8px}
.chans li{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--econ);
border-radius:8px;padding:7px 12px;font-size:14.5px}
.chans .sub{color:var(--muted)}
.chans .cnt{margin-left:8px;color:var(--muted);font-size:13px}
table{border-collapse:collapse;width:100%;font-size:14px;margin-top:8px}
th,td{border-bottom:1px solid var(--line);padding:7px 9px;text-align:left;vertical-align:top}
th{color:var(--muted);font-weight:600}
td:last-child,th:last-child{text-align:right}
.st-ok{color:#1f6b45;font-weight:600}
.st-none{color:var(--muted)}
.st-old{color:#8a6d1f}
.st-fail{color:var(--econ);font-weight:600}
.err{color:var(--muted);font-size:12px;margin-top:3px;word-break:break-all}
.poster{margin-top:10px;border:1px solid var(--line);border-radius:8px;overflow:hidden;
background:#faf8f5;text-align:center}
.poster img{max-width:100%;height:auto;display:block;margin:0 auto}
.empty{color:var(--muted);padding:20px;text-align:center;background:var(--card);
border:1px dashed var(--line);border-radius:12px}
footer{margin-top:40px;color:var(--muted);font-size:13px;text-align:center}
"""


def _esc(s) -> str:
    return html_mod.escape(str(s or ""), quote=True)


def render_html(user_id: str, lectures: list[Lecture], meta: dict) -> str:
    econ = [x for x in sort_lectures(lectures) if x.is_econ]
    other = [x for x in sort_lectures(lectures) if not x.is_econ]
    today = meta.get("today") or dt.date.today().isoformat()
    channels = meta.get("channels") or []
    updated = _updated_channels(lectures, channels)

    def card(lec: Lecture) -> str:
        tags = []
        if lec.upcoming:
            tags.append(f'<span class="tag when">{_esc(lec.upcoming)}</span>')
        if lec.ocr_used:
            tags.append('<span class="tag ocr">海报OCR</span>')
        if lec.is_econ:
            tags.append('<span class="tag econ">经济学</span>')
        rows = [
            ("时间", _fmt_time(lec)),
            ("地点", _fmt_place(lec)),
            ("主办方", lec.organizer or lec.source_name),
        ]
        if lec.speaker:
            rows.append(("主讲人", lec.speaker))
        if lec.summary:
            rows.append(("摘要", lec.summary[:170] + ("…" if len(lec.summary) > 170 else "")))
        rows.append(("来源", f"{lec.source_name}（{lec.channel}）· 发布 {_fmt_date(lec.published)}"))
        if lec.is_econ and lec.econ_hits:
            rows.append(("判定依据", "、".join(lec.econ_hits[:6]) + f"（得分 {lec.econ_score}）"))
        body = "".join(f'<div class="k">{_esc(k)}</div><div>{_esc(v)}</div>' for k, v in rows)
        poster = (f'<div class="poster"><a href="{_esc(lec.poster)}" target="_blank" rel="noopener">'
                  f'<img src="{_esc(lec.poster)}" alt="讲座海报" loading="lazy"></a></div>'
                  if lec.poster else "")
        return (f'<div class="card{" econ" if lec.is_econ else ""}">'
                f'<p class="t">{_esc(lec.title)}{"".join(tags)}</p>'
                f'<div class="meta">{body}</div>{poster}'
                f'<div class="meta" style="margin-top:6px"><div class="k">原文</div>'
                f'<div><a href="{_esc(lec.url)}" target="_blank" rel="noopener">{_esc(lec.url)}</a></div></div>'
                f'</div>')

    # 开头：一句话说清哪些学院有更新
    if updated:
        items = []
        for c in updated:
            label = c.get("group") or c["name"]
            col = f'<span class="sub"> · {_esc(c["col"])}</span>' if c.get("col") else ""
            extra = (f'<span class="cnt">经济学 {c["econ"]}</span>'
                     if c.get("econ") and c["econ"] != c["kept"] else "")
            items.append(f'<li><b>{_esc(label)}</b>{col}'
                         f'<span class="cnt">新增 {c["kept"]} 条</span>{extra}</li>')
        head_line = (f"<b>{len(updated)}</b> 个渠道有更新，共 <b>{len(lectures)}</b> 条"
                     f"（经济学相关 <b>{len(econ)}</b> 条）")
        updated_block = (f'<p class="headline">{head_line}</p><ul class="chans">{"".join(items)}</ul>')
    else:
        updated_block = ('<p class="headline">本期没有找到即将开始的讲座'
                         f'（已抓取 {len(channels)} 个渠道）</p>')

    notes = [f"只收录 <b>{today}</b> 及以后开始的讲座"]
    if meta.get("dropped_past"):
        notes.append(f"已剔除 {meta['dropped_past']} 条已开始/已过期的")
    if meta.get("ocr_images"):
        notes.append(f"{meta['ocr_images']} 张讲座海报已用 OCR 读出时间地点")
    if meta.get("econ_only"):
        notes.append("已按设置只保留经济学相关")

    parts = [
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        f"<title>ZUEL 即将开始的讲座 · {_esc(today)}</title>",
        f"<style>{HTML_CSS}</style></head><body><div class='wrap'>",
        "<header><h1>中南财经政法大学 · 即将开始的讲座"
        f"<span class='pill'>{_esc(today)}</span></h1>",
        f"<div class='sub'>用户 {_esc(user_id)} ｜ " + " ｜ ".join(notes) + "</div></header>",
        updated_block,
    ]
    if econ:
        parts.append(f"<h2>一、经济学相关讲座（{len(econ)} 条）</h2>")
        parts += [card(x) for x in econ]
    if other:
        num = "二" if econ else "一"
        parts.append(f"<h2>{num}、其他学科讲座（{len(other)} 条）</h2>")
        parts += [card(x) for x in other]

    # 末尾：全部渠道的抓取状态清单
    if channels:
        ok_n = sum(1 for c in channels if c.get("status") == "已更新")
        fail_n = sum(1 for c in channels if c.get("status") == "抓取失败")
        parts.append(f"<h2>渠道抓取状态（{len(channels)} 个：已更新 {ok_n}，抓取失败 {fail_n}）</h2>")
        parts.append("<table><tr><th>渠道</th><th>类型</th><th>状态</th><th>本期新增</th></tr>")
        for c in sorted(channels, key=lambda x: (x.get("group") or "", x.get("key") or "")):
            label = (c.get("group") or c.get("name") or "")
            if c.get("col"):
                label += f' <span class="sub">· {c["col"]}</span>'
            status = c.get("status") or "—"
            if c.get("error"):
                status = f'<span class="st-fail">抓取失败</span>' \
                         f'<div class="err">{_esc(c["error"][:140])}</div>'
            elif status == "已更新":
                status = '<span class="st-ok">已更新</span>'
            elif status == "有内容但均已结束":
                status = '<span class="st-old">有内容但均已结束</span>'
            else:
                status = f'<span class="st-none">{_esc(status)}</span>'
            parts.append(f"<tr><td>{label}</td><td>{_esc(c.get('kind', '官网'))}</td>"
                         f"<td>{status}</td><td>{c.get('kept', 0) or '—'}</td></tr>")
        parts.append("</table>")

    parts.append(f"<footer>报告生成时间 {dt.datetime.now():%Y-%m-%d %H:%M:%S} ｜ ZUEL 讲座雷达</footer>")
    parts.append("</div></body></html>")
    return "".join(parts)


def write_reports(out_dir: Path, user_id: str, lectures: list[Lecture], meta: dict) -> dict[str, str]:
    """落地四种产物，返回 {格式: 路径}。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = meta.get("today") or dt.date.today().isoformat()
    base = f"{user_id}_{stamp}"
    paths: dict[str, str] = {}

    md = render_markdown(user_id, lectures, meta)
    p = out_dir / f"{base}.md"
    p.write_text(md, encoding="utf-8")
    paths["markdown"] = str(p)

    p = out_dir / f"{base}.html"
    p.write_text(render_html(user_id, lectures, meta), encoding="utf-8")
    paths["html"] = str(p)

    data = [x.to_dict() for x in sort_lectures(lectures)]
    p = out_dir / f"{base}.json"
    p.write_text(json.dumps({"user": user_id, "meta": _jsonable(meta), "lectures": data},
                            ensure_ascii=False, indent=2), encoding="utf-8")
    paths["json"] = str(p)

    p = out_dir / f"{base}.csv"
    with p.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["标题", "开场状态", "讲座时间", "讲座日期", "地点", "主办方", "主讲人",
                    "经济学相关", "经济学得分", "类型", "海报OCR", "来源", "渠道",
                    "发布日期", "海报图", "原文链接"])
        for x in sort_lectures(lectures):
            w.writerow([x.title, x.upcoming or "", x.event_time or "", x.event_date or "",
                        x.location or "", x.organizer or x.source_name, x.speaker or "",
                        "是" if x.is_econ else "否", x.econ_score, x.kind,
                        "是" if x.ocr_used else "否",
                        x.source_name, x.channel, x.published or "", x.poster or "", x.url])
    paths["csv"] = str(p)
    return paths


def _jsonable(meta: dict) -> dict:
    out = {}
    for k, v in meta.items():
        if isinstance(v, (str, int, float, bool, list, dict, type(None))):
            out[k] = v
        else:
            out[k] = str(v)
    return out
