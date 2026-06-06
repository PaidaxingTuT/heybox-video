import asyncio
import logging
import queue
import threading
from typing import Any

from config import CAPTCHA_MANUAL_TIMEOUT, PLAYWRIGHT_HEADLESS, PLAYWRIGHT_TIMEOUT

LOGGER = logging.getLogger("uvicorn.error")


class NodriverBrowserWorker:
    def __init__(self, owner_cls):
        self._owner_cls = owner_cls
        self._jobs: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def submit(self, url: str) -> dict[str, Any] | None:
        done = threading.Event()
        box: dict[str, Any] = {}

        def finish(result=None, error=None):
            box["result"] = result
            box["error"] = error
            done.set()

        self._jobs.put((url, finish))

        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, daemon=True)
                self._thread.start()

        done.wait()

        if box.get("error") is not None:
            raise box["error"]
        return box.get("result")

    def _fail_pending(self, error: Exception) -> None:
        while True:
            try:
                _, finish = self._jobs.get_nowait()
            except queue.Empty:
                return
            finish(error=error)

    async def _launch_browser(self, *, headless: bool):
        import nodriver

        LOGGER.info(
            "heybox parse nodriver launch mode=%s",
            "headless" if headless else "headful",
        )
        return await nodriver.start(
            user_data_dir=self._owner_cls._profile_dir(),
            headless=headless,
            browser_args=["--disable-blink-features=AutomationControlled"],
            lang="zh-CN",
        )

    @staticmethod
    async def _stop_browser(browser) -> None:
        if browser is None:
            return
        try:
            await browser.stop()
        except Exception:
            pass

    @staticmethod
    def _cookie_to_state(cookie: Any) -> dict[str, Any]:
        return {
            "name": getattr(cookie, "name", None),
            "value": getattr(cookie, "value", None),
            "domain": getattr(cookie, "domain", None),
            "path": getattr(cookie, "path", "/"),
            "expires": getattr(cookie, "expires", None),
            "secure": bool(getattr(cookie, "secure", False)),
        }

    @staticmethod
    async def _safe_evaluate(tab, expression: str, *, return_by_value: bool = True) -> Any | None:
        try:
            return await tab.evaluate(expression, return_by_value=return_by_value)
        except Exception:
            return None

    async def _refresh_fast_state(self, browser, tab) -> None:
        owner = self._owner_cls
        cookies = []
        try:
            raw_cookies = await browser.cookies.get_all(requests_cookie_format=True)
            cookies = [
                self._cookie_to_state(cookie)
                for cookie in raw_cookies
                if owner._is_fast_cookie_domain(
                    owner._state_value(getattr(cookie, "domain", None), max_length=200)
                )
            ]
        except Exception:
            cookies = []

        try:
            local_storage = await tab.get_local_storage()
        except Exception:
            local_storage = {}

        session_storage = await self._safe_evaluate(
            tab,
            """(() => {
                const output = {};
                for (let index = 0; index < window.sessionStorage.length; index += 1) {
                    const key = window.sessionStorage.key(index);
                    if (key !== null) {
                        output[key] = window.sessionStorage.getItem(key);
                    }
                }
                return output;
            })()""",
        )

        browser_snapshot = {
            "href": await self._safe_evaluate(tab, "window.location.href"),
            "origin": await self._safe_evaluate(tab, "window.location.origin"),
            "userAgent": await self._safe_evaluate(tab, "navigator.userAgent"),
            "localStorage": local_storage if isinstance(local_storage, dict) else {},
            "sessionStorage": session_storage if isinstance(session_storage, dict) else {},
        }

        owner._save_browser_session_snapshot(
            cookies=cookies,
            browser_snapshot=browser_snapshot,
        )

    @staticmethod
    async def _try_verify(tab) -> None:
        try:
            await tab.verify_cf()
        except Exception:
            return

    async def _page_has_captcha(self, tab) -> bool:
        text = await self._safe_evaluate(
            tab,
            "document.body ? document.body.innerText : ''",
        )
        if not isinstance(text, str):
            return False

        lowered = text.lower()
        return any(
            token in lowered
            for token in (
                "captcha",
                "verify you are human",
                "浜烘満楠岃瘉",
                "楠岃瘉鐮?",
                "瀹夊叏楠岃瘉",
            )
        )

    async def _extract_dom_result(self, tab, url: str) -> dict[str, Any] | None:
        owner = self._owner_cls
        payload = await self._safe_evaluate(
            tab,
            """(() => {
                const videos = [];
                const seen = new Set();
                const add = (value) => {
                    if (typeof value !== "string") return;
                    const url = value.trim();
                    if (!url || seen.has(url)) return;
                    seen.add(url);
                    videos.push(url);
                };

                document.querySelectorAll("video, source").forEach((element) => {
                    add(element.currentSrc);
                    add(element.src);
                    add(element.getAttribute("src"));
                    add(element.getAttribute("data-src"));
                });

                const metaContent = (selector) => {
                    const node = document.querySelector(selector);
                    return node ? node.getAttribute("content") || "" : "";
                };

                return {
                    videos,
                    title:
                        metaContent('meta[property="og:title"]') ||
                        metaContent('meta[name="twitter:title"]') ||
                        document.title ||
                        "",
                    description:
                        metaContent('meta[name="description"]') ||
                        metaContent('meta[property="og:description"]') ||
                        metaContent('meta[name="twitter:description"]') ||
                        "",
                };
            })()""",
        )
        if not isinstance(payload, dict):
            return None

        videos = owner._collect_video_urls(payload.get("videos"))
        meta = owner._clean_meta(
            {
                "title": owner._state_value(payload.get("title"), max_length=120) or "",
                "description": owner._state_value(payload.get("description"), max_length=260) or "",
            }
        )
        result = owner._make_result(videos, meta, source="browser")
        if result:
            LOGGER.info(
                "heybox parse nodriver success link_id=%s stage=dom_fallback",
                owner._extract_link_id(url) or "-",
            )
        return result

    @staticmethod
    def _as_browser_result(result: dict[str, Any] | None, backend: str) -> dict[str, Any] | None:
        if not result:
            return result

        wrapped = dict(result)
        wrapped["source"] = "browser"
        wrapped["browser_backend"] = backend
        return wrapped

    async def _scrape_with_browser(
        self,
        browser,
        tab,
        url: str,
        *,
        allow_manual: bool,
    ) -> tuple[dict[str, Any] | None, bool]:
        owner = self._owner_cls
        link_id = owner._extract_link_id(url) or "-"
        await tab.get(url)
        await tab.wait(PLAYWRIGHT_TIMEOUT / 1000)
        await self._refresh_fast_state(browser, tab)

        result = owner._scrape_fast(url)
        if result:
            stage = "manual_retry" if allow_manual else "warm_session"
            LOGGER.info("heybox parse nodriver success link_id=%s stage=%s", link_id, stage)
            return self._as_browser_result(result, "nodriver"), False

        result = await self._extract_dom_result(tab, url)
        if result:
            return self._as_browser_result(result, "nodriver"), False

        captcha_required = await self._page_has_captcha(tab)
        if captcha_required and not allow_manual:
            LOGGER.info(
                "heybox parse nodriver captcha_detected link_id=%s stage=headless_probe",
                link_id,
            )
            return None, True

        if allow_manual and not PLAYWRIGHT_HEADLESS:
            LOGGER.info(
                "heybox parse nodriver waiting_captcha link_id=%s timeout_ms=%s",
                link_id,
                CAPTCHA_MANUAL_TIMEOUT,
            )
            elapsed = 0
            attempt = 0
            while elapsed < CAPTCHA_MANUAL_TIMEOUT:
                if attempt == 0 or attempt % 5 == 0:
                    await self._try_verify(tab)

                await asyncio.sleep(1)
                elapsed += 1000
                attempt += 1
                await self._refresh_fast_state(browser, tab)
                result = owner._scrape_fast(url)
                if result:
                    LOGGER.info("heybox parse nodriver success link_id=%s stage=manual_retry", link_id)
                    return self._as_browser_result(result, "nodriver"), False

                result = await self._extract_dom_result(tab, url)
                if result:
                    return self._as_browser_result(result, "nodriver"), False

            captcha_required = await self._page_has_captcha(tab)

        if captcha_required:
            LOGGER.info(
                "heybox parse nodriver blocked link_id=%s reason=show_captcha",
                link_id,
            )
            raise owner.CaptchaRequiredError("小黑盒返回验证码，无法自动解析该帖子")

        LOGGER.info("heybox parse nodriver miss link_id=%s", link_id)
        return None, False

    async def _run_async(self) -> None:
        browser = None
        try:
            browser = await self._launch_browser(headless=True)
        except Exception as error:
            self._fail_pending(error)
            return

        try:
            tab = browser.main_tab or await browser.get("about:blank")
            while True:
                url, finish = await asyncio.to_thread(self._jobs.get)
                relaunched_for_manual = False
                try:
                    if tab is None:
                        tab = browser.main_tab or await browser.get("about:blank")
                    result, needs_manual = await self._scrape_with_browser(
                        browser,
                        tab,
                        url,
                        allow_manual=False,
                    )
                    if needs_manual and not PLAYWRIGHT_HEADLESS:
                        await self._stop_browser(browser)
                        browser = await self._launch_browser(headless=False)
                        tab = browser.main_tab or await browser.get("about:blank")
                        relaunched_for_manual = True
                        result, _ = await self._scrape_with_browser(
                            browser,
                            tab,
                            url,
                            allow_manual=True,
                        )
                    finish(result=result)
                except Exception as error:
                    finish(error=error)
                    if self._owner_cls._is_browser_closed_error(error):
                        break
                finally:
                    if relaunched_for_manual and not PLAYWRIGHT_HEADLESS:
                        await self._stop_browser(browser)
                        browser = await self._launch_browser(headless=True)
                        tab = browser.main_tab or await browser.get("about:blank")
        finally:
            await self._stop_browser(browser)

    def _run(self):
        asyncio.run(self._run_async())
