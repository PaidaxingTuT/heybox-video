from playwright.sync_api import sync_playwright

from config import PLAYWRIGHT_TIMEOUT


class HeyboxScraper:

    @staticmethod
    def scrape(url: str) -> str | None:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            target = None

            def on_response(response):
                nonlocal target
                if ".mp4" in response.url:
                    target = response.url

            page.on("response", on_response)
            page.goto(url)
            page.wait_for_timeout(PLAYWRIGHT_TIMEOUT)
            browser.close()

            return target
