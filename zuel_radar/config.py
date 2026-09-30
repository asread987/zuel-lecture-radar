# -*- coding: utf-8 -*-
"""配置加载与校验：信息源库（sources.yaml）+ 每用户配置（config/users/*.yaml）。"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .sources import SourceSpec

ROOT = Path(__file__).resolve().parent.parent

# 各推送渠道的必填/可选字段
CHANNEL_SCHEMA: dict[str, dict] = {
    "serverchan": {"required": ["key"], "optional": ["name"],
                   "desc": "Server酱（sct.ftqq.com），key 形如 SCTxxxxxx，推送到微信"},
    "pushplus": {"required": ["token"], "optional": ["topic", "name", "template"],
                 "desc": "PushPlus（pushplus.plus），token 来自个人中心，推送到微信"},
    "wxpusher": {"required": ["app_token"], "optional": ["uids", "topic_ids", "name"],
                 "desc": "WxPusher（wxpusher.zjiecode.com），按 uid 精确推送到某个微信号"},
    "wecom": {"required": ["webhook"], "optional": ["name"],
              "desc": "企业微信群机器人 webhook（微信侧收消息）"},
    "console": {"required": [], "optional": ["name"], "desc": "仅打印到终端，用于自测"},
    "file": {"required": [], "optional": ["dir", "name"], "desc": "仅落地为文件，不推送"},
}


def _as_list(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [x.strip() for x in re.split(r"[,\s]+", v) if x.strip()]
    return list(v)


@dataclass
class UserConfig:
    user_id: str
    enabled: bool = True
    interval_days: int = 7                 # 每间隔多少天触发一次
    push_time: str = "20:00"               # 触发当天的推送时刻
    sources: list[str] = field(default_factory=lambda: ["all"])  # 源 key / 分组名 / all
    econ_only: bool = False                # 是否仅推经济学相关
    econ_threshold: float = 3.0
    lecture_only: bool = True              # 是否只保留讲座/论坛类
    drop_review: bool = False              # 额外丢弃「已举办」的回顾类文章
    # ── 只要「还没开始」的讲座 ──────────────────────────────
    upcoming_only: bool = True             # 只保留未来（含今天）的讲座
    upcoming_grace_days: int = 0           # 给刚开场/今天的活动留的宽限天数
    unknown_date_policy: str = "keep"      # 日期读不出来时：keep / drop
    unknown_date_max_age_days: int = 14    # keep 时，发布已超过 N 天的预告视为过期
    ocr_poster: bool = True                # 用 macOS Vision OCR 读海报上的时间/地点
    max_items: int = 40
    lookback_days: int = 30                # 无历史状态时回看天数
    max_pages: int = 2                     # 每源最多翻页数
    detail_limit: int = 25                 # 单次最多补全多少篇正文
    channels: list[dict] = field(default_factory=list)
    report_dir: str = "out"
    db: str = "data/radar.db"
    source_file: str = ""                  # 记录来源文件

    @staticmethod
    def from_dict(user_id: str, d: dict, source_file: str = "") -> "UserConfig":
        allowed = set(UserConfig.__dataclass_fields__)
        d = dict(d or {})
        d["user_id"] = user_id
        d["source_file"] = source_file
        return UserConfig(**{k: v for k, v in d.items() if k in allowed})

    def resolve_path(self, p: str) -> Path:
        """把相对路径解析到项目根目录下。"""
        path = Path(p)
        return path if path.is_absolute() else (ROOT / path)

    def validate(self) -> list[str]:
        errs: list[str] = []
        if self.interval_days < 1:
            errs.append("interval_days 必须 >= 1")
        if not re.match(r"^\d{1,2}:\d{2}$", self.push_time or ""):
            errs.append(f"push_time 格式应为 HH:MM，当前为 {self.push_time!r}")
        if not self.channels:
            errs.append("至少需要配置一个推送渠道（channels）")
        for i, ch in enumerate(self.channels):
            t = (ch or {}).get("type")
            if t not in CHANNEL_SCHEMA:
                errs.append(f"channels[{i}] 类型未知: {t!r}（可选：{', '.join(CHANNEL_SCHEMA)}）")
                continue
            for req in CHANNEL_SCHEMA[t]["required"]:
                if not ch.get(req):
                    errs.append(f"channels[{i}] ({t}) 缺少必填字段 {req}")
        if self.econ_threshold < 0:
            errs.append("econ_threshold 不能为负")
        if self.unknown_date_policy not in ("keep", "drop"):
            errs.append(f"unknown_date_policy 只能是 keep 或 drop，当前为 {self.unknown_date_policy!r}")
        if self.upcoming_grace_days < 0:
            errs.append("upcoming_grace_days 不能为负")
        if self.unknown_date_max_age_days < 0:
            errs.append("unknown_date_max_age_days 不能为负")
        return errs

    def wants_source(self, spec: SourceSpec) -> bool:
        """判断该用户是否订阅了某个源。"""
        sel = {s.lower() for s in self.sources}
        if not sel or "all" in sel or "*" in sel:
            return True
        return (spec.key.lower() in sel
                or (spec.group or spec.name).lower() in sel
                or spec.name.lower() in sel
                or spec.type.lower() in sel)


def load_sources(path: str | Path | None = None) -> list[SourceSpec]:
    p = Path(path) if path else (ROOT / "config" / "sources.yaml")
    if not p.exists():
        raise FileNotFoundError(f"找不到信息源配置：{p}")
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    raw = data.get("sources") or []
    specs = [SourceSpec.from_dict(d) for d in raw]
    keys = [s.key for s in specs]
    dup = {k for k in keys if keys.count(k) > 1}
    if dup:
        raise ValueError(f"信息源 key 重复：{sorted(dup)}")
    for s in specs:
        # 仅对「启用中」的源做必填校验；停用的占位源（如待填 feed_url 的公众号源）不阻塞启动
        if not s.enabled:
            continue
        if s.type == "bode" and not (s.list_url or (s.base and s.column)):
            raise ValueError(f"源 {s.key} 为 bode 类型，必须提供 list_url 或 base+column")
        if s.type == "rss" and not s.feed_url:
            raise ValueError(f"源 {s.key} 为 rss 类型，必须提供 feed_url")
        if s.type == "wechat_manual" and not s.urls:
            raise ValueError(f"源 {s.key} 为 wechat_manual 类型，必须提供 urls")
        if s.type == "sogou_wechat" and not s.keywords:
            raise ValueError(f"源 {s.key} 为 sogou_wechat 类型，必须提供 keywords")
    return specs


def load_users(users_dir: str | Path | None = None) -> list[UserConfig]:
    d = Path(users_dir) if users_dir else (ROOT / "config" / "users")
    if not d.exists():
        return []
    out: list[UserConfig] = []
    for f in sorted(d.glob("*.y*ml")):
        if f.name.startswith(("_", ".")):
            continue
        raw = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        uid = raw.pop("user_id", None) or f.stem
        out.append(UserConfig.from_dict(uid, raw, source_file=str(f)))
    return out


def load_user(user_id: str, users_dir: str | Path | None = None) -> UserConfig:
    for u in load_users(users_dir):
        if u.user_id == user_id:
            return u
    raise KeyError(f"未找到用户配置：{user_id}")


TEMPLATE_PATH = ROOT / "config" / "user-template.yaml"
USER_ID_PLACEHOLDER = "__USER_ID__"


def example_user_template() -> str:
    """返回用户配置模板。

    以 config/user-template.yaml 为唯一来源（方便随文档一起分发、也跟着版本走），
    文件缺失时退回到一份最小模板，保证 init-user 在任何情况下都能用。
    """
    if TEMPLATE_PATH.exists():
        return TEMPLATE_PATH.read_text(encoding="utf-8")
    return (
        f"# 最小模板（未找到 {TEMPLATE_PATH}，请参考 README）\n"
        f"user_id: {USER_ID_PLACEHOLDER}\n"
        "enabled: true\n"
        "interval_days: 7\n"
        'push_time: "20:00"\n'
        "sources: [all]\n"
        "upcoming_only: true\n"
        "econ_only: false\n"
        "channels:\n"
        "  - type: console\n"
        "report_dir: out\n"
        "db: data/radar.db\n"
    )


def render_user_template(user_id: str) -> str:
    """把模板里的占位符换成真实 user_id。"""
    tpl = example_user_template()
    if USER_ID_PLACEHOLDER in tpl:
        return tpl.replace(USER_ID_PLACEHOLDER, user_id, 1)
    # 兼容旧模板写法
    return re.sub(r"^user_id:\s*\S+", f"user_id: {user_id}", tpl, count=1, flags=re.M)
