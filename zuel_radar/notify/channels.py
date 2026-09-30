# -*- coding: utf-8 -*-
"""
推送渠道实现 —— 目标是「推送到用户指定的微信账号」。

说明：微信个人号没有官方消息推送接口，因此「推送到某个微信账号」在实务上
有且只有下面几条路，本项目全部支持，按用户配置择一或并用：

  serverchan  Server酱   —— 扫码绑定微信后拿到 SendKey，服务端把消息下发到该微信。最省事。
  pushplus    PushPlus   —— 同样扫码绑定，支持群组 topic，可把多个微信号拉进一个 topic。
  wxpusher    WxPusher   —— 通过 uid 精确指定某个微信用户，适合「多用户各自收」的场景。
  wecom       企业微信群机器人 —— 消息进企业微信群（微信侧可接收），适合团队/课题组。

另有两个本地渠道用于自测：
  console     打印到终端
  file        只落地文件，不推送
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import requests

TIMEOUT = 20
UA = {"User-Agent": "ZUEL-Lecture-Radar/1.0"}


def utf8_len(s: str) -> int:
    return len(s.encode("utf-8"))


def truncate_markdown(md: str, limit_bytes: int, note: str = "\n\n> …（内容过长已截断，完整清单见附件报告）") -> str:
    """按 UTF-8 字节上限截断 markdown，尽量在条目边界处断开。"""
    if utf8_len(md) <= limit_bytes:
        return md
    budget = limit_bytes - utf8_len(note)
    out, used = [], 0
    for line in md.split("\n"):
        ln = line + "\n"
        if used + utf8_len(ln) > budget:
            break
        out.append(line)
        used += utf8_len(ln)
    return "\n".join(out).rstrip() + note


class Notifier:
    type = "base"
    limit_bytes = 20000

    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg or {}
        self.name = self.cfg.get("name") or self.type

    def send(self, title: str, markdown: str) -> tuple[bool, str]:
        raise NotImplementedError

    @staticmethod
    def _post(url: str, **kw) -> dict:
        last = ""
        for attempt in range(3):
            try:
                r = requests.post(url, headers=UA, timeout=TIMEOUT, **kw)
                try:
                    return r.json()
                except ValueError:
                    return {"_raw": r.text[:300], "_status": r.status_code}
            except Exception as exc:  # noqa: BLE001
                last = f"{type(exc).__name__}: {exc}"
                time.sleep(1.5 * (attempt + 1))
        return {"_error": last}


class ServerChanNotifier(Notifier):
    type = "serverchan"
    limit_bytes = 30000

    def send(self, title: str, markdown: str) -> tuple[bool, str]:
        key = (self.cfg.get("key") or "").strip()
        if not key:
            return False, "缺少 key"
        body = truncate_markdown(markdown, self.limit_bytes)
        res = self._post(f"https://sctapi.ftqq.com/{key}.send",
                         data={"title": title[:100], "desp": body})
        ok = str(res.get("code")) == "0"
        return ok, json.dumps(res, ensure_ascii=False)[:300]


class PushPlusNotifier(Notifier):
    type = "pushplus"
    limit_bytes = 20000

    def send(self, title: str, markdown: str) -> tuple[bool, str]:
        token = (self.cfg.get("token") or "").strip()
        if not token:
            return False, "缺少 token"
        payload = {
            "token": token,
            "title": title[:100],
            "content": truncate_markdown(markdown, self.limit_bytes),
            "template": self.cfg.get("template", "markdown"),
        }
        if self.cfg.get("topic"):
            payload["topic"] = self.cfg["topic"]
        res = self._post("https://www.pushplus.plus/send", json=payload)
        ok = str(res.get("code")) == "200"
        return ok, json.dumps(res, ensure_ascii=False)[:300]


class WxPusherNotifier(Notifier):
    type = "wxpusher"
    limit_bytes = 20000

    def send(self, title: str, markdown: str) -> tuple[bool, str]:
        app_token = (self.cfg.get("app_token") or "").strip()
        if not app_token:
            return False, "缺少 app_token"
        payload = {
            "appToken": app_token,
            "content": truncate_markdown(markdown, self.limit_bytes),
            "summary": title[:100],
            "contentType": 3,          # 3 = markdown
        }
        if self.cfg.get("uids"):
            payload["uids"] = list(self.cfg["uids"])
        if self.cfg.get("topic_ids"):
            payload["topicIds"] = [int(x) for x in self.cfg["topic_ids"]]
        if not payload.get("uids") and not payload.get("topicIds"):
            return False, "需要 uids 或 topic_ids 至少一项"
        res = self._post("https://wxpusher.zjiecode.com/api/send/message", json=payload)
        ok = str(res.get("code")) == "1000"
        return ok, json.dumps(res, ensure_ascii=False)[:300]


class WeComNotifier(Notifier):
    type = "wecom"
    limit_bytes = 4000      # 企业微信 markdown 正文上限 4096 字节

    def send(self, title: str, markdown: str) -> tuple[bool, str]:
        hook = (self.cfg.get("webhook") or "").strip()
        if not hook:
            return False, "缺少 webhook"
        content = f"## {title}\n\n" + truncate_markdown(
            markdown, self.limit_bytes - 200, "\n\n> …（已截断，完整清单见附件）")
        res = self._post(hook, json={"msgtype": "markdown", "markdown": {"content": content}})
        ok = str(res.get("errcode")) == "0"
        return ok, json.dumps(res, ensure_ascii=False)[:300]


class ConsoleNotifier(Notifier):
    type = "console"
    limit_bytes = 10 ** 9

    def send(self, title: str, markdown: str) -> tuple[bool, str]:
        print("\n" + "=" * 72)
        print(f"[推送预览] {title}")
        print("=" * 72)
        print(markdown)
        print("=" * 72 + "\n")
        return True, "已打印到终端"


class FileNotifier(Notifier):
    type = "file"
    limit_bytes = 10 ** 9

    def send(self, title: str, markdown: str) -> tuple[bool, str]:
        d = Path(self.cfg.get("dir") or "out")
        d.mkdir(parents=True, exist_ok=True)
        # 去掉文件名非法字符（含全角），并把空白折叠掉
        safe = re.sub(r'[\\/:*?"<>|｜－—\s　]+', "_", title).strip("_")[:60] or "report"
        p = d / f"{safe}.md"
        p.write_text(markdown, encoding="utf-8")
        return True, f"已写入 {p}"


REGISTRY: dict[str, type[Notifier]] = {
    c.type: c for c in (
        ServerChanNotifier, PushPlusNotifier, WxPusherNotifier,
        WeComNotifier, ConsoleNotifier, FileNotifier,
    )
}


def build_notifier(cfg: dict) -> Notifier:
    t = (cfg or {}).get("type")
    if t not in REGISTRY:
        raise ValueError(f"未知推送渠道类型: {t!r}")
    return REGISTRY[t](cfg)


def build_all(channels: list[dict]) -> list[Notifier]:
    return [build_notifier(c) for c in (channels or [])]
