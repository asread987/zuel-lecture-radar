# -*- coding: utf-8 -*-
"""
抓取层 —— HTTP 优先，遇到 WAF 的 JS 挑战自动回退到无头 Chrome。

背景（2026-09 实测于本机）：
    zuel.edu.cn 各站点前置了 JS 反爬挑战，会检测
    navigator.webdriver / HeadlessChrome / Puppeteer / Playwright / 软件 WebGL 渲染器，
    并用 crypto.subtle 校验后才把正文注入 #content_container。
    直接 requests 只能拿到约 46KB 的空壳页（0 个文章链接）。
    绕法：Chrome --headless=new + 覆盖 UA + --virtual-time-budget。

    另外两个踩过的坑，务必保留当前写法：
      1) 不要传 --user-data-dir。实测在本机 Chrome 156 / macOS 27 上，
         只要指定自定义 profile 目录，Chrome 必然卡死（等 GoogleUpdater 唤醒后不返回），
         不传则稳定 1.7~2.0s 完成。因此这里用默认 profile + --incognito 保证不污染浏览记录。
      2) 不要用 capture_output=True。Chrome 会派生后台子进程继承 stdout 管道，
         导致管道迟迟不关闭、subprocess.run 死等。改用临时文件重定向。

    注意：浏览器通道会共用默认 profile，因此必须串行（见 _browser_lock）。
    若用户自己的 Chrome 正在运行，无头实例可能被接管而返回空，doctor 会给出提示。
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import requests

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
HTTP_HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
}

WAF_MARKERS = (
    "content_container",
    "__webdriver_evaluate",
    "cdc_adoQpoasnfa76pfcZLmcfl_Array",
    "window.domAutomationController",
)
ART_LINK_RE = re.compile(r"/\d{4}/\d{4}/c\d+a\d+/page\.(?:htm|psp)")

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
]

# 渲染用 flag。刻意不包含 --enable-automation（会把 navigator.webdriver 置真而被 WAF 识别），
# 也不包含 --user-data-dir（见模块开头说明）。
CHROME_FLAGS = [
    "--headless=new", "--no-sandbox", "--incognito",
    "--disable-blink-features=AutomationControlled",
    "--disable-component-update", "--disable-background-networking",
    "--disable-default-apps", "--disable-sync", "--disable-extensions",
    "--no-first-run", "--no-default-browser-check",
    "--mute-audio", "--hide-scrollbars",
]


def find_chrome() -> str | None:
    for p in CHROME_CANDIDATES:
        if os.path.exists(p):
            return p
    for name in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge"):
        p = shutil.which(name)
        if p:
            return p
    return None


def chrome_default_profile_dir() -> Path:
    if os.uname().sysname == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
    return Path.home() / ".config" / "google-chrome"


def chrome_is_running() -> bool:
    """粗略判断用户自己的 Chrome 是否在运行（存在 SingletonLock 即认为在跑）。"""
    try:
        return (chrome_default_profile_dir() / "SingletonLock").exists()
    except Exception:  # noqa: BLE001
        return False


def looks_like_shell(html: str) -> bool:
    """判断返回的是否是 WAF 空壳页（有挑战特征、且几乎没有链接）。"""
    if not html:
        return True
    n_links = html.count("<a ") + html.count("href=")
    if any(m in html for m in WAF_MARKERS) and n_links < 20:
        return True
    return len(html) < 3000 and n_links < 3


def looks_like_content(html: str) -> bool:
    """判断响应是否「有实际内容」——这是缓存前的最后一道闸门。

    必须单独有这个函数，因为**渲染后的空壳页体积不小**（约 46KB，因为内嵌了挑战脚本），
    只用「体积太小」来判定会漏掉它，结果把空壳页当成正常页缓存下来，
    之后所有解析都返回空——表现为渠道明明有内容却报「未更新讲座信息」。
    实测：正常渲染的列表页有 100+ 个 href，空壳页是 0 个。
    """
    if not html or len(html) < 800:
        return False
    head = html[:600].lower()
    if "<?xml" in head or "<rss" in head or "<feed" in head:
        return len(html) >= 400            # Feed 不做链接数判断
    return html.count("href=") >= 10


def html_to_text(html: str) -> str:
    """HTML → 纯文本（去掉 script/style 后剥标签）。"""
    if not html:
        return ""
    body = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    body = re.sub(r"(?is)<!--.*?-->", " ", body)
    body = re.sub(r"(?i)<(br|/p|/div|/li|/tr|/h\d)[^>]*>", "\n", body)
    body = re.sub(r"<[^>]+>", " ", body)
    for a, b in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                 ("&quot;", '"'), ("&#39;", "'"), ("&ldquo;", "“"), ("&rdquo;", "”")):
        body = body.replace(a, b)
    body = re.sub(r"[ \t\u00a0]+", " ", body)
    body = re.sub(r"\n\s*\n+", "\n", body)
    return body.strip()


class FetchError(RuntimeError):
    pass


class Fetcher:
    """带 WAF 回退能力的抓取器。浏览器通道串行，HTTP 通道可并发。"""

    def __init__(
        self,
        chrome_path: str | None = None,
        timeout: int = 25,
        browser_timeout: int = 60,
        browser_budget: int = 9000,
        min_delay: float = 1.2,
        cache_dir: str | Path | None = None,
        use_cache: bool = True,
        cache_ttl: int = 1800,
        verbose: bool = False,
    ) -> None:
        self.chrome = chrome_path or find_chrome()
        self.timeout = timeout
        self.browser_timeout = browser_timeout
        self.browser_budget = browser_budget
        self.min_delay = min_delay
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.use_cache = use_cache
        self.cache_ttl = cache_ttl
        self.verbose = verbose

        self._force_browser: dict[str, bool] = {}
        self._session = requests.Session()
        self._session.headers.update(HTTP_HEADERS)
        self._lock = threading.Lock()
        self._browser_lock = threading.Lock()      # 浏览器必须串行：共用默认 profile
        self._last_hit: dict[str, float] = {}
        self.stats = {"http": 0, "browser": 0, "cache": 0, "fail": 0}
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ---------------- 缓存 ----------------
    def _cache_path(self, url: str) -> Path | None:
        if not (self.use_cache and self.cache_dir):
            return None
        return self.cache_dir / f"{hashlib.sha1(url.encode('utf-8')).hexdigest()}.html"

    def _cache_read(self, url: str) -> str | None:
        p = self._cache_path(url)
        if not p or not p.exists():
            return None
        if time.time() - p.stat().st_mtime > self.cache_ttl:
            return None
        try:
            html = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return None
        # 缓存里可能是早期版本写进去的 WAF 空壳页，发现即丢弃并重新抓
        if not looks_like_content(html):
            return None
        self.stats["cache"] += 1
        return html

    def _cache_write(self, url: str, html: str) -> None:
        # 只缓存确认有内容的页面，避免把空壳页污染进缓存
        if not html or not looks_like_content(html):
            return
        p = self._cache_path(url)
        if p:
            try:
                p.write_text(html, encoding="utf-8")
            except OSError:
                pass

    # ---------------- 限速 ----------------
    def _throttle(self, url: str) -> None:
        host = re.sub(r"^https?://([^/]+).*$", r"\1", url)
        with self._lock:
            wait = self.min_delay - (time.time() - self._last_hit.get(host, 0.0))
            if wait > 0:
                time.sleep(wait)
            self._last_hit[host] = time.time()

    # ---------------- HTTP ----------------
    def _http(self, url: str) -> str:
        r = self._session.get(url, timeout=self.timeout)
        r.encoding = r.apparent_encoding or "utf-8"
        if r.status_code >= 400:
            raise FetchError(f"HTTP {r.status_code}: {url}")
        return r.text

    # ---------------- 浏览器 ----------------
    def _run_chrome(self, url: str) -> str:
        if not self.chrome:
            raise FetchError("未找到可用的 Chrome/Chromium，无法渲染被 WAF 保护的页面")
        cmd = [self.chrome] + CHROME_FLAGS + [
            f"--user-agent={BROWSER_UA}",
            f"--virtual-time-budget={self.browser_budget}",
            "--dump-dom", url,
        ]
        out_fd, out_path = tempfile.mkstemp(suffix=".html")
        err_fd, err_path = tempfile.mkstemp(suffix=".log")
        try:
            # 用文件重定向而不是管道：Chrome 的派生子进程会继承管道导致死等
            with os.fdopen(out_fd, "wb") as fo, os.fdopen(err_fd, "wb") as fe:
                proc = subprocess.run(cmd, stdout=fo, stderr=fe,
                                      timeout=self.browser_timeout, stdin=subprocess.DEVNULL)
            html = Path(out_path).read_bytes().decode("utf-8", errors="ignore")
            if proc.returncode != 0 and not html:
                err = Path(err_path).read_bytes().decode("utf-8", errors="ignore")
                raise FetchError(f"Chrome 退出码 {proc.returncode}: {err.strip()[-200:]}")
            return html
        except subprocess.TimeoutExpired as exc:
            raise FetchError(f"浏览器渲染超时（>{self.browser_timeout}s）: {url}") from exc
        finally:
            for p in (out_path, err_path):
                try:
                    os.unlink(p)
                except OSError:
                    pass

    def _browser(self, url: str, retries: int = 1) -> str:
        last = ""
        for attempt in range(retries + 1):
            with self._browser_lock:
                html = self._run_chrome(url)
            if looks_like_content(html):
                return html
            last = f"渲染结果无有效内容（{len(html)} 字节，{html.count('href=')} 个链接）"
            if attempt < retries:
                time.sleep(1.5)
        hint = ""
        if chrome_is_running():
            hint = "；检测到用户自己的 Chrome 正在运行，可能导致无头实例被接管，建议定时任务执行时关闭 Chrome"
        raise FetchError(f"{last}{hint}: {url}")

    # ---------------- 对外接口 ----------------
    def get(self, url: str, force_browser: bool | None = None, allow_cache: bool = True) -> str:
        """抓取一个 URL 并返回 HTML，自动在 HTTP / 浏览器之间选择。"""
        if allow_cache:
            cached = self._cache_read(url)
            if cached:
                return cached

        host = re.sub(r"^https?://([^/]+).*$", r"\1", url)
        want_browser = self._force_browser.get(host, False) if force_browser is None else force_browser

        html = ""
        if not want_browser:
            self._throttle(url)
            try:
                html = self._http(url)
                if looks_like_shell(html):
                    if self.verbose:
                        print(f"    [waf] {host} 返回空壳页 → 切换浏览器渲染")
                    self._force_browser[host] = True
                    html = ""
                else:
                    self.stats["http"] += 1
            except Exception as exc:  # noqa: BLE001
                if self.verbose:
                    print(f"    [http] 失败（{type(exc).__name__}）→ 尝试浏览器渲染")
                html = ""

        if not html:
            try:
                html = self._browser(url)
                self.stats["browser"] += 1
                self._force_browser[host] = True
            except FetchError:
                self.stats["fail"] += 1
                raise

        self._cache_write(url, html)
        return html

    def close(self) -> None:
        self._session.close()
