import re
from typing import Any

from playwright.sync_api import sync_playwright

from config import PLAYWRIGHT_TIMEOUT


class CaptchaRequiredError(Exception):
    """Raised when Heybox returns captcha gating instead of post content."""


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

    @staticmethod
    def scrape(url: str) -> dict[str, Any] | None:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(ignore_https_errors=True)

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

                if HeyboxScraper._looks_like_video_url(response_url) or any(
                    marker in content_type
                    for marker in HeyboxScraper.VIDEO_CONTENT_TYPES
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
                        for video_url in HeyboxScraper._collect_video_urls(data):
                            add_candidate(video_url)
                        if HeyboxScraper._is_post_detail_response(response_url):
                            HeyboxScraper._merge_meta(
                                metadata,
                                HeyboxScraper._extract_metadata(data),
                            )
                    else:
                        text = response.text()
                        for video_url in HeyboxScraper._collect_video_urls(text):
                            add_candidate(video_url)
                except Exception:
                    return

            page.on("response", on_response)
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(PLAYWRIGHT_TIMEOUT)
            browser.close()

            if candidates:
                return {
                    "video_url": candidates[0],
                    "meta": HeyboxScraper._clean_meta(metadata),
                }

            if captcha_required:
                raise CaptchaRequiredError("小黑盒返回验证码，无法自动解析该帖子")

            return None
