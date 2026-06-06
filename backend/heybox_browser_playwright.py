import logging
import queue
import threading

from playwright.sync_api import Error as PlaywrightError, sync_playwright

from config import PLAYWRIGHT_HEADLESS

LOGGER = logging.getLogger("uvicorn.error")


class PlaywrightBrowserWorker:
    def __init__(self, owner_cls):
        self._owner_cls = owner_cls
        self._jobs: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def submit(self, url: str):
        done = threading.Event()
        box = {}

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

    def _launch_context_once(self, playwright):
        return playwright.chromium.launch_persistent_context(
            self._owner_cls._profile_dir(),
            headless=PLAYWRIGHT_HEADLESS,
            ignore_https_errors=True,
            args=["--disable-blink-features=AutomationControlled"],
        )

    def _launch_context(self, playwright):
        try:
            return self._launch_context_once(playwright)
        except PlaywrightError as exc:
            message = str(exc)
            if "Executable doesn't exist" not in message and "playwright install" not in message:
                raise

            import subprocess
            import sys

            print("Playwright Chromium is missing; installing it before browser fallback.")
            subprocess.run(
                [sys.executable, "-m", "playwright", "install", "chromium"],
                check=True,
            )
            return self._launch_context_once(playwright)

    def _fail_pending(self, error: Exception) -> None:
        while True:
            try:
                _, finish = self._jobs.get_nowait()
            except queue.Empty:
                return
            finish(error=error)

    def _run(self):
        with sync_playwright() as p:
            try:
                context = self._launch_context(p)
            except Exception as error:
                self._fail_pending(error)
                return
            try:
                page = context.pages[0] if context.pages else context.new_page()
                while True:
                    url, finish = self._jobs.get()
                    if page.is_closed():
                        finish(error=PlaywrightError("browser window has been closed"))
                        break
                    try:
                        finish(result=self._owner_cls._scrape_on_page(page, url))
                    except PlaywrightError as error:
                        finish(error=error)
                        if self._owner_cls._is_browser_closed_error(error):
                            break
                    except Exception as error:
                        finish(error=error)
            finally:
                try:
                    context.close()
                except PlaywrightError:
                    pass
