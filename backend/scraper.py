import queue
import re
import threading
import time
from pathlib import Path
from typing import Any

from playwright.sync_api import Error as PlaywrightError, sync_playwright

from config import (
    CAPTCHA_COOLDOWN,
    CAPTCHA_MANUAL_TIMEOUT,
    PLAYWRIGHT_HEADLESS,
    PLAYWRIGHT_TIMEOUT,
    PLAYWRIGHT_USER_DATA_DIR,
)


class CaptchaRequiredError(Exception):
    """Raised when Heybox returns captcha gating instead of post content."""


class CooldownError(Exception):
    """Raised when a recent captcha/rate-limit hit put scraping into cooldown."""

    def __init__(self, remaining: int):
        super().__init__(f"冷却中，请 {remaining} 秒后再试")
        self.remaining = remaining


class HeyboxScraper:
    VIDEO_URL_RE = re.compile(
        r"https?://[A-Za-z0-9._~:/?#\[\]@!$&()*+,;=%-]+?(?:\.mp4|\.m3u8)(?:[A-Za-z0-9._~:/?#\[\]@!$&()*+,;=%-]*)",
        re.IGNORECASE,
    )
    VIDEO_CONTENT_TYPES = (
        "video/",
        "application/vnd.apple.mpegurl",
        "application/x-mpegurl",
    )
    TITLE_KEYS = ("title", "share_title", "link_title", "post_title")
    DESCRIPTION_KEYS = (
        "description",
        "desc",
        "summary",
        "brief",
        "content",
        "text",
        "share_desc",
    )
    GENERIC_TITLES = {
        "高能玩家聚集地 - 小黑盒",
        "相关社区",
        "推荐内容",
        "相关推荐",
    }

    @classmethod
    def _looks_like_video_url(cls, url: str) -> bool:
        lower_url = url.lower()
        return ".mp4" in lower_url or ".m3u8" in lower_url

    @staticmethod
    def _is_post_detail_response(url: str) -> bool:
        return "/bbs/app/link/tree" in url.lower()

    @classmethod
    def _is_generic_title(cls, value: str | None) -> bool:
        return not value or value in cls.GENERIC_TITLES

    @staticmethod
    def _profile_dir() -> str:
        return str((Path(__file__).resolve().parent / PLAYWRIGHT_USER_DATA_DIR).absolute())

    @staticmethod
    def _clean_text(value: Any, max_length: int = 240) -> str | None:
        if not isinstance(value, str):
            return None

        text = re.sub(r"<[^>]+>", " ", value)
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            return None

        return text[:max_length]

    @classmethod
    def _clean_meta(cls, meta: dict[str, str]) -> dict[str, str]:
        cleaned: dict[str, str] = {}

        title = cls._clean_text(meta.get("title"), max_length=120)
        if title and not cls._is_generic_title(title):
            cleaned["title"] = title

        description = cls._clean_text(meta.get("description"), max_length=260)
        if description and description != title and description not in cls.GENERIC_TITLES:
            cleaned["description"] = description

        return cleaned

    @classmethod
    def _collect_video_urls(cls, value: Any) -> list[str]:
        urls: list[str] = []

        def add(url: str) -> None:
            if (
                url.startswith(("http://", "https://"))
                and cls._looks_like_video_url(url)
                and url not in urls
            ):
                urls.append(url)

        def walk(item: Any) -> None:
            if isinstance(item, str):
                for match in cls.VIDEO_URL_RE.finditer(item):
                    add(match.group(0))
                add(item)
                return

            if isinstance(item, dict):
                for child in item.values():
                    walk(child)
                return

            if isinstance(item, list):
                for child in item:
                    walk(child)

        walk(value)
        return urls

    @classmethod
    def _merge_meta(cls, target: dict[str, str], source: dict[str, str]) -> None:
        for key, value in source.items():
            if value and not target.get(key):
                target[key] = value

    @classmethod
    def _extract_metadata(cls, value: Any) -> dict[str, str]:
        meta: dict[str, str] = {}

        def walk(item: Any) -> None:
            if isinstance(item, dict):
                for key in cls.TITLE_KEYS:
                    if not meta.get("title"):
                        title = cls._clean_text(item.get(key), max_length=120)
                        if title and not cls._is_generic_title(title):
                            meta["title"] = title

                for key in cls.DESCRIPTION_KEYS:
                    if not meta.get("description"):
                        desc = cls._clean_text(item.get(key), max_length=260)
                        if desc and desc != meta.get("title") and desc not in cls.GENERIC_TITLES:
                            meta["description"] = desc

                for child in item.values():
                    walk(child)
                return

            if isinstance(item, list):
                for child in item:
                    walk(child)

        walk(value)
        return cls._clean_meta(meta)

    @classmethod
    def _scrape_on_page(cls, page, url: str) -> dict[str, Any] | None:
        candidates: list[str] = []
        metadata: dict[str, str] = {}
        captcha_required = False

        def add_candidate(video_url: str) -> None:
            if video_url not in candidates:
                candidates.append(video_url)

        def on_response(response):
            nonlocal captcha_required

            response_url = response.url
            content_type = response.headers.get("content-type", "").lower()

            if cls._looks_like_video_url(response_url) or any(
                marker in content_type for marker in cls.VIDEO_CONTENT_TYPES
            ):
                add_candidate(response_url)

            if not any(
                marker in content_type
                for marker in ("application/json", "text/", "javascript")
            ):
                return

            try:
                if "application/json" in content_type:
                    data = response.json()
                    if data.get("status") == "show_captcha":
                        captcha_required = True
                    for video_url in cls._collect_video_urls(data):
                        add_candidate(video_url)
                    if cls._is_post_detail_response(response_url):
                        cls._merge_meta(metadata, cls._extract_metadata(data))
                else:
                    text = response.text()
                    for video_url in cls._collect_video_urls(text):
                        add_candidate(video_url)
            except Exception:
                return

        page.on("response", on_response)
        try:
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(PLAYWRIGHT_TIMEOUT)

            if captcha_required and not PLAYWRIGHT_HEADLESS and not candidates:
                print("检测到小黑盒验证码，请在弹出的浏览器窗口中手动完成验证。")
                elapsed = 0
                try:
                    while elapsed < CAPTCHA_MANUAL_TIMEOUT and not candidates:
                        page.wait_for_timeout(1000)
                        elapsed += 1000
                except PlaywrightError:
                    pass
        finally:
            # 页面会被复用，注销本次监听器，避免下次响应触发上次的闭包
            try:
                page.remove_listener("response", on_response)
            except PlaywrightError:
                pass

        if candidates:
            return {
                "video_url": candidates[0],
                "meta": cls._clean_meta(metadata),
            }

        if captcha_required:
            raise CaptchaRequiredError("小黑盒返回验证码，无法自动解析该帖子")

        return None

    # 复用同一个常驻浏览器（同一线程）来跑所有解析任务：既复用已热身/已过验证的
    # 会话，又把请求串行化，避免并发撞限流。命中验证码后进入冷却，冷却期内的请求
    # 直接退避而不再去撞墙。
    _worker: "_BrowserWorker | None" = None
    _state_lock = threading.Lock()
    _cooldown_until = 0.0

    @classmethod
    def _get_worker(cls) -> "_BrowserWorker":
        with cls._state_lock:
            if cls._worker is None:
                cls._worker = _BrowserWorker()
            return cls._worker

    @classmethod
    def scrape(cls, url: str) -> dict[str, Any] | None:
        with cls._state_lock:
            remaining = cls._cooldown_until - time.monotonic()
        if remaining > 0:
            raise CooldownError(int(remaining) + 1)

        try:
            return cls._get_worker().submit(url)
        except CaptchaRequiredError:
            with cls._state_lock:
                cls._cooldown_until = time.monotonic() + CAPTCHA_COOLDOWN
            raise


class _BrowserWorker:
    """在专用线程上持有一个长存的 persistent context。

    Playwright 的 sync API 有线程亲和性，浏览器调用必须始终在同一线程发生。把所有
    解析任务路由到同一个 worker 线程，既复用热身好的会话（cookie、已通过的验证），
    又天然串行化请求，绝不并发打到小黑盒的限流上。
    """

    def __init__(self):
        self._jobs: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def submit(self, url: str) -> dict[str, Any] | None:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, daemon=True)
                self._thread.start()

        done = threading.Event()
        box: dict[str, Any] = {}

        def finish(result=None, error=None):
            box["result"] = result
            box["error"] = error
            done.set()

        self._jobs.put((url, finish))
        done.wait()

        if box.get("error") is not None:
            raise box["error"]
        return box.get("result")

    def _run(self):
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                HeyboxScraper._profile_dir(),
                headless=PLAYWRIGHT_HEADLESS,
                ignore_https_errors=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            try:
                # 复用 context 自带的默认页，避免每次 new_page() 在窗口里
                # 多出一个空白标签
                page = context.pages[0] if context.pages else context.new_page()
                while True:
                    url, finish = self._jobs.get()
                    if page.is_closed():
                        # 默认页被关掉（如用户手动关了窗口）：结束当前任务并退出，
                        # 下次 submit() 会重新拉起浏览器
                        finish(error=PlaywrightError("浏览器窗口已关闭"))
                        break
                    try:
                        finish(result=HeyboxScraper._scrape_on_page(page, url))
                    except Exception as e:
                        finish(error=e)
            finally:
                try:
                    context.close()
                except PlaywrightError:
                    pass
