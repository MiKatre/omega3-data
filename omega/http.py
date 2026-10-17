"""HTTP fetching with a Chrome TLS fingerprint, per-host pacing, retries and block detection."""

import gzip
import random
import re
import threading
import time
from urllib.parse import urlsplit

from curl_cffi import requests as cr

BLOCK_MARKERS = re.compile(
    r"cf-chl-|challenge-platform|captcha-delivery|datadome|px-captcha|/distil/|"
    r"Access Denied</title>|Attention Required|Just a moment\.\.\.|Pardon Our Interruption",
    re.I,
)


class Blocked(RuntimeError):
    pass


class FetchError(RuntimeError):
    pass


class Http:
    """One curl_cffi session per host so cookies from a first visit are reused like a browser."""

    def __init__(self, delay=1.0, timeout=30, impersonate="chrome", log=None):
        self.delay = delay
        self.timeout = timeout
        self.impersonate = impersonate
        self.sessions = {}
        self.last = {}
        self.locks = {}
        self.guard = threading.Lock()
        self.stats = {}
        self.log = log or (lambda *a: None)

    def _host(self, url):
        return urlsplit(url).netloc

    def _session(self, host):
        with self.guard:
            if host not in self.sessions:
                self.sessions[host] = cr.Session(impersonate=self.impersonate)
                self.locks[host] = threading.Lock()
                self.stats[host] = {"ok": 0, "blocked": 0, "error": 0}
            return self.sessions[host], self.locks[host]

    def get(self, url, *, accept=None, retries=2, binary=False, allow_404=False):
        host = self._host(url)
        session, lock = self._session(host)
        headers = {"Accept-Language": "es-ES,es;q=0.9,fr;q=0.8,en;q=0.7"}
        if accept:
            headers["Accept"] = accept
        err = None
        for attempt in range(retries + 1):
            with lock:
                wait = self.delay - (time.monotonic() - self.last.get(host, 0))
                if wait > 0:
                    time.sleep(wait + random.uniform(0, self.delay / 3))
                try:
                    r = session.get(url, headers=headers, timeout=self.timeout, allow_redirects=True)
                except Exception as e:  # network errors, TLS resets
                    err = FetchError(f"{type(e).__name__}: {str(e)[:120]}")
                    self.last[host] = time.monotonic()
                    time.sleep(2 * (attempt + 1))
                    continue
                self.last[host] = time.monotonic()
            status = r.status_code
            body = r.content
            if body[:2] == b"\x1f\x8b":
                try:
                    body = gzip.decompress(body)
                except OSError:
                    pass
            if status == 404 and allow_404:
                return None
            if status in (403, 429, 503) or (
                status == 202 and not body.strip()
            ):
                self.stats[host]["blocked"] += 1
                err = Blocked(f"HTTP {status} {url}")
                time.sleep(5 * (attempt + 1))
                continue
            if status >= 500:
                err = FetchError(f"HTTP {status} {url}")
                time.sleep(3 * (attempt + 1))
                continue
            if status != 200:
                self.stats[host]["error"] += 1
                raise FetchError(f"HTTP {status} {url}")
            if binary:
                self.stats[host]["ok"] += 1
                return body
            text = body.decode(r.encoding or "utf-8", "ignore") if r.encoding else body.decode(
                "utf-8", "ignore"
            )
            if len(text) < 30000 and BLOCK_MARKERS.search(text):
                self.stats[host]["blocked"] += 1
                err = Blocked(f"challenge page {url}")
                time.sleep(5 * (attempt + 1))
                continue
            self.stats[host]["ok"] += 1
            return text
        if isinstance(err, Blocked):
            raise err
        self.stats[host]["error"] += 1
        raise err or FetchError(url)
