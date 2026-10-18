"""Headless Chromium fetcher for shops that block plain HTTP clients (empleovino's recipe)."""

import random
import time

from .http import BLOCK_MARKERS, Blocked

INIT = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['es-ES', 'es', 'fr', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
window.chrome = window.chrome || {runtime: {}};
"""
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)


class Browser:
    def __init__(self, log=None, headless=True):
        self.log = log or (lambda *a: None)
        self.headless = headless
        self.pw = None
        self.browser = None
        self.context = None
        self.page = None
        self.count = 0

    def __enter__(self):
        from playwright.sync_api import sync_playwright

        self.pw = sync_playwright().start()
        self._launch()
        return self

    def _launch(self):
        if self.browser:
            try:
                self.browser.close()
            except Exception:
                pass
        self.browser = self.pw.chromium.launch(
            headless=self.headless, args=["--disable-blink-features=AutomationControlled"]
        )
        self.context = self.browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent=UA,
            locale="es-ES",
            timezone_id="Europe/Madrid",
            extra_http_headers={"Accept-Language": "es-ES,es;q=0.9,fr;q=0.8,en;q=0.7"},
        )
        self.context.add_init_script(INIT)
        # Skip heavy assets; HTML + scripts are enough for JSON-LD and prices.
        self.context.route(
            "**/*",
            lambda route: route.abort()
            if route.request.resource_type in ("image", "media", "font")
            else route.continue_(),
        )
        self.page = self.context.new_page()

    def __exit__(self, *exc):
        try:
            self.browser.close()
        finally:
            self.pw.stop()

    def get(self, url, wait_ms=(1500, 3000), selector=None):
        for attempt in range(3):
            try:
                resp = self.page.goto(url, wait_until="domcontentloaded", timeout=45000)
                if selector:
                    try:
                        self.page.wait_for_selector(selector, timeout=8000)
                    except Exception:
                        pass
                self.page.wait_for_timeout(random.randint(*wait_ms))
                html = self.page.content()
                status = resp.status if resp else 0
                self.count += 1
                if status in (403, 429) or (len(html) < 40000 and BLOCK_MARKERS.search(html)):
                    # Challenge pages often clear themselves after a few seconds.
                    self.page.wait_for_timeout(6000)
                    html = self.page.content()
                    if len(html) < 40000 and BLOCK_MARKERS.search(html):
                        raise Blocked(f"browser challenge {status} {url}")
                if status == 404:
                    return None
                return html
            except Blocked:
                if attempt == 2:
                    raise
                self.log(f"browser blocked on {url}; relaunching")
                time.sleep(10 * (attempt + 1))
                self._launch()
            except Exception as e:
                if attempt == 2:
                    raise
                self.log(f"browser error {type(e).__name__} on {url}")
                time.sleep(3)
        return None

    def get_raw(self, url):
        """Raw response body (sitemaps, JSON) fetched with the browser's cookies and fingerprint."""
        resp = self.page.goto(url, wait_until="domcontentloaded", timeout=45000)
        if resp is None:
            return None
        if resp.status in (403, 429):
            self.page.wait_for_timeout(6000)
            resp = self.page.goto(url, wait_until="domcontentloaded", timeout=45000)
        if resp is None or resp.status != 200:
            raise Blocked(f"browser HTTP {resp.status if resp else 0} {url}")
        return resp.text()

    def evaluate(self, js):
        return self.page.evaluate(js)
