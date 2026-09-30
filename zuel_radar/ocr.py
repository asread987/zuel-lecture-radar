# -*- coding: utf-8 -*-
"""海报图片文字识别（macOS 内置 Vision 框架，零额外依赖）。

为什么需要它：
    ZUEL 大量讲座预告的正文就是一张海报图片，DOM 里没有文字，
    导致「讲座时间」读不出来——而「只保留即将开始的讲座」恰恰依赖这个日期。

实现方式：
    调用 macOS 自带的 Vision 框架（VNRecognizeTextRequest）做中文 OCR。
    走 `osascript -l JavaScript` 的 ObjC 桥，因此**不需要 pip 安装任何东西**，
    也不依赖系统外的 OCR 二进制。实测识别质量足以读出
    「讲座时间：2026年9月28日（周一）14:00-15:30」「讲座地点：文泉楼北603会议室」。

降级策略：
    非 macOS、或 osascript 不可用、或识别失败时，一律返回空串，
    上层逻辑照常走「时间见海报」分支，不会因为 OCR 挂掉而中断整轮抓取。
"""
from __future__ import annotations

import hashlib
import os
import platform
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

OCR_UA = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    )
}

# 传给 osascript 的 JXA 脚本。用 run(argv) 接收图片路径，一次进程识别多张图。
JXA_SCRIPT = r"""
ObjC.import('Vision');
ObjC.import('Foundation');

function ocrOne(path) {
  var url = $.NSURL.fileURLWithPath(path);
  var req = $.VNRecognizeTextRequest.alloc.init;
  req.recognitionLevel = 0;                 // 0 = accurate
  req.usesLanguageCorrection = true;
  req.recognitionLanguages = $(['zh-Hans', 'en-US']);
  var handler = $.VNImageRequestHandler.alloc.initWithURLOptions(url, $());
  handler.performRequestsError($.NSArray.arrayWithObject(req), $());
  var res = req.results;
  var out = [];
  for (var i = 0; i < res.count; i++) {
    out.push(ObjC.unwrap(res.objectAtIndex(i).topCandidates(1).objectAtIndex(0).string));
  }
  return out.join('\n');
}

function run(argv) {
  var parts = [];
  for (var i = 0; i < argv.length; i++) {
    try { parts.push(ocrOne(argv[i])); } catch (e) { parts.push(''); }
  }
  return parts.join('\n<<<OCR-SPLIT>>>\n');
}
"""

SPLIT = "\n<<<OCR-SPLIT>>>\n"


def vision_available() -> bool:
    """本机是否可用 macOS Vision OCR。"""
    return platform.system() == "Darwin" and shutil.which("osascript") is not None


class PosterOCR:
    """海报 OCR：带磁盘缓存与失败降级。"""

    def __init__(self, cache_dir: str | Path | None = None, timeout: int = 60,
                 verbose: bool = False, enabled: bool | None = None) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.timeout = timeout
        self.verbose = verbose
        self.enabled = vision_available() if enabled is None else (enabled and vision_available())
        self._lock = threading.Lock()
        self._script_path: Path | None = None
        self.stats = {"hit": 0, "ocr": 0, "fail": 0, "download_fail": 0}
        if self.cache_dir and self.enabled:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ---------- 缓存 ----------
    def _cache_file(self, url: str) -> Path | None:
        if not (self.enabled and self.cache_dir):
            return None
        return self.cache_dir / f"ocr_{hashlib.sha1(url.encode('utf-8')).hexdigest()}.txt"

    def _cache_get(self, url: str) -> str | None:
        p = self._cache_file(url)
        if p and p.exists():
            self.stats["hit"] += 1
            return p.read_text(encoding="utf-8", errors="ignore")
        return None

    def _cache_put(self, url: str, text: str) -> None:
        p = self._cache_file(url)
        if p and text:
            try:
                p.write_text(text, encoding="utf-8")
            except OSError:
                pass

    # ---------- 脚本 ----------
    def _script(self) -> str:
        if self._script_path and self._script_path.exists():
            return str(self._script_path)
        fd, path = tempfile.mkstemp(suffix=".js", prefix="zuel_ocr_")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(JXA_SCRIPT)
        self._script_path = Path(path)
        return path

    # ---------- 下载 ----------
    def _download(self, url: str, dest_dir: Path) -> Path | None:
        import requests
        try:
            r = requests.get(url, headers=OCR_UA, timeout=25)
            if r.status_code != 200 or not r.content:
                self.stats["download_fail"] += 1
                return None
            ctype = (r.headers.get("Content-Type") or "").lower()
            ext = ".png" if "png" in ctype else (".jpg" if ("jpeg" in ctype or "jpg" in ctype) else ".img")
            p = dest_dir / (hashlib.sha1(url.encode()).hexdigest()[:16] + ext)
            p.write_bytes(r.content)
            return p
        except Exception:  # noqa: BLE001
            self.stats["download_fail"] += 1
            return None

    # ---------- 主接口 ----------
    def ocr_urls(self, urls: list[str]) -> dict[str, str]:
        """批量识别海报。返回 {url: 文本}，识别失败的 url 不出现在结果里。"""
        result: dict[str, str] = {}
        if not self.enabled or not urls:
            return result

        pending: list[str] = []
        for u in urls:
            if not u:
                continue
            cached = self._cache_get(u)
            if cached is not None:
                result[u] = cached
            else:
                pending.append(u)
        if not pending:
            return result

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            local: list[tuple[str, Path]] = []
            for u in pending:
                p = self._download(u, tmp_dir)
                if p:
                    local.append((u, p))
            if not local:
                return result
            texts = self._run_ocr([str(p) for _, p in local])
            for (u, _), t in zip(local, texts):
                t = (t or "").strip()
                if t:
                    result[u] = t
                    self._cache_put(u, t)
        return result

    def _run_ocr(self, paths: list[str]) -> list[str]:
        """调一次 osascript 识别多张图（进程启动开销约 0.3-0.8s，合并调用很划算）。"""
        cmd = ["osascript", "-l", "JavaScript", self._script()] + paths
        with self._lock:
            try:
                proc = subprocess.run(cmd, capture_output=True, timeout=self.timeout,
                                      stdin=subprocess.DEVNULL)
            except subprocess.TimeoutExpired:
                self.stats["fail"] += len(paths)
                return [""] * len(paths)
        if proc.returncode != 0:
            self.stats["fail"] += len(paths)
            if self.verbose:
                err = proc.stderr.decode("utf-8", errors="ignore").strip()
                print(f"    [ocr] osascript 失败：{err[:160]}")
            return [""] * len(paths)
        out = proc.stdout.decode("utf-8", errors="ignore")
        self.stats["ocr"] += len(paths)
        parts = out.split(SPLIT)
        if len(parts) != len(paths):        # 数量对不上时按位置尽量对齐
            parts = (parts + [""] * len(paths))[:len(paths)]
        return parts

    def close(self) -> None:
        if self._script_path and self._script_path.exists():
            try:
                self._script_path.unlink()
            except OSError:
                pass
