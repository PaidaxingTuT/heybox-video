import logging
import threading
import time
from typing import Any

from config import BROWSER_BACKEND, CAPTCHA_COOLDOWN
from heybox_browser_nodriver import NodriverBrowserWorker
from heybox_browser_playwright import PlaywrightBrowserWorker
from heybox_fast import HeyboxFastMixin

LOGGER = logging.getLogger("uvicorn.error")


class CaptchaRequiredError(Exception):
    """Raised when Heybox returns captcha gating instead of post content."""


class CooldownError(Exception):
    """Raised when a recent captcha/rate-limit hit put scraping into cooldown."""

    def __init__(self, remaining: int):
        super().__init__(f"冷却中，请 {remaining} 秒后再试")
        self.remaining = remaining


class HeyboxScraper(HeyboxFastMixin):
    CaptchaRequiredError = CaptchaRequiredError

    _worker: Any = None
    _state_lock = threading.Lock()
    _cooldown_until = 0.0

    @classmethod
    def _get_worker(cls):
        with cls._state_lock:
            if cls._worker is None:
                cls._worker = (
                    NodriverBrowserWorker(cls)
                    if BROWSER_BACKEND == "nodriver"
                    else PlaywrightBrowserWorker(cls)
                )
            return cls._worker

    @classmethod
    def scrape(cls, url: str) -> dict[str, Any] | None:
        link_id = cls._extract_link_id(url) or "-"
        try:
            fast_result = cls._scrape_fast(url)
        except Exception:
            LOGGER.exception("heybox parse fast error link_id=%s", link_id)
            fast_result = None
        if fast_result:
            return fast_result

        with cls._state_lock:
            remaining = cls._cooldown_until - time.monotonic()
        if remaining > 0:
            LOGGER.info(
                "heybox parse cooldown link_id=%s remaining=%s",
                link_id,
                int(remaining) + 1,
            )
            raise CooldownError(int(remaining) + 1)

        LOGGER.info("heybox parse fallback link_id=%s reason=fast_miss_or_blocked", link_id)

        def submit_browser_job(*, retried: bool = False) -> dict[str, Any] | None:
            try:
                return cls._get_worker().submit(url)
            except Exception as exc:
                if retried or not cls._is_browser_closed_error(exc):
                    raise

                LOGGER.info(
                    "heybox parse browser restart link_id=%s reason=window_closed",
                    link_id,
                )
                with cls._state_lock:
                    cls._worker = None
                return submit_browser_job(retried=True)

        try:
            return submit_browser_job()
        except CaptchaRequiredError:
            with cls._state_lock:
                cls._cooldown_until = time.monotonic() + CAPTCHA_COOLDOWN
            raise
