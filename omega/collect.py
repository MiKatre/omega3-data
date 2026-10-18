"""Collect raw offers from every source in catalog.json into .cache/raw/<source>.jsonl.

Usage:
  python -m omega.collect                 # all http sources, then browser sources
  python -m omega.collect --only hsn onatera
  python -m omega.collect --no-browser
"""

import argparse
import concurrent.futures as cf
import faulthandler
import gzip
import hashlib
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from . import adapters
from .http import Blocked, Http

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".cache"
RAW = CACHE / "raw"
PAGE_TEXT = ROOT / "data" / "page_text"
HTML = CACHE / "html"


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


class PageCache:
    """Reuses pages fetched within max_age seconds (local iteration); 0 disables reuse."""

    def __init__(self, max_age):
        self.max_age = max_age
        HTML.mkdir(parents=True, exist_ok=True)

    def path(self, url):
        return HTML / (hashlib.sha1(url.encode()).hexdigest() + ".html.gz")

    def get(self, url):
        p = self.path(url)
        if self.max_age and p.exists() and time.time() - p.stat().st_mtime < self.max_age:
            return gzip.decompress(p.read_bytes()).decode("utf-8", "ignore")
        return None

    def put(self, url, html):
        self.path(url).write_bytes(gzip.compress(html.encode("utf-8", "ignore")))


def run_source(src, http, cache, browser=None):
    t0 = time.time()
    budget = src.get("budget_s", 1500 if browser else 1200)
    stats = {"id": src["id"], "pages": 0, "blocked": 0, "errors": 0, "offers": 0, "status": "ok",
             "via": "browser" if browser else "http"}
    offers = []
    streak = {"blocked": 0, "errors": 0}

    def fetch_page(url):
        cached = cache.get(url)
        if cached is not None:
            return cached
        if streak["blocked"] >= 4 or streak["errors"] >= 8:
            return None
        if time.time() - t0 > budget:
            if not stats.get("partial"):
                stats["partial"] = f"time budget {budget}s reached"
                log(f"{src['id']}: time budget reached after {stats['pages']} pages")
            return None
        try:
            if browser is not None:
                html = browser.get(url, selector=src.get("wait_for"))
            else:
                html = http.get(url)
            streak["blocked"] = streak["errors"] = 0
        except Blocked as e:
            stats["blocked"] += 1
            streak["blocked"] += 1
            log(f"{src['id']}: blocked {e}")
            return None
        except Exception as e:
            stats["errors"] += 1
            streak["errors"] += 1
            log(f"{src['id']}: error {type(e).__name__} {str(e)[:120]}")
            return None
        if html:
            stats["pages"] += 1
            cache.put(url, html)
            if stats["pages"] % 50 == 0:
                log(f"{src['id']}: {stats['pages']} pages, {round(time.time() - t0)}s")
        return html

    kind = src["kind"]
    try:
        if kind == "shopify":
            gen = adapters.shopify(src, http, log)
        elif kind == "woo":
            gen = adapters.woo(src, http, log)
        elif kind == "algolia":
            gen = adapters.algolia(src, http, log)
        elif kind == "sitemap":
            fetch_xml = None
            if browser is not None:
                def fetch_xml(u):
                    try:
                        return http.get(u, accept="application/xml,text/xml,*/*")
                    except Exception:
                        return browser.get_raw(u)
            gen = adapters.sitemap(src, http, log, fetch_page, fetch_xml)
        elif kind == "urls":
            gen = adapters.urls(src, http, log, fetch_page)
        elif kind == "listing":
            gen = adapters.listing(src, http, log, fetch_page)
        elif kind == "custom":
            from . import custom

            gen = getattr(custom, src["id"].replace("-", "_"))(src, http, log, fetch_page, browser)
        else:
            raise ValueError(kind)
        for o in gen:
            o.update(source=src["id"], shop=src["name"], country=src["country"], fetched_at=now())
            offers.append(o)
    except Blocked as e:
        stats["status"] = f"blocked: {e}"
    except Exception as e:
        stats["status"] = f"error: {type(e).__name__}: {str(e)[:200]}"
        traceback.print_exc()
    if offers and (kind in ("woo", "shopify") or src.get("algolia", {}).get("pages")):
        # Feeds carry short descriptions; composition tables live on the product page.
        # A shop that blocks GitHub's IPs falls back to the page text saved by the last run that
        # could reach it (data/page_text/<id>.json, also refreshable from any machine).
        saved_path = PAGE_TEXT / f"{src['id']}.json"
        saved = json.loads(saved_path.read_text()) if saved_path.exists() else {}
        pages = {}
        for o in offers:
            if o["url"] not in pages:
                html = fetch_page(o["url"])
                found = adapters.page_offers(src, o["url"], html) if html else []
                if found:
                    saved[o["url"]] = {k: found[0].get(k) for k in ("gtin", "brand", "text")}
                pages[o["url"]] = found[:1] or ([saved[o["url"]]] if o["url"] in saved else [])
            extra = pages[o["url"]]
            if extra:
                o["gtin"] = o.get("gtin") or extra[0].get("gtin")
                o["brand"] = o.get("brand") or extra[0].get("brand")
                o["text"] = (o.get("text") or "") + " \n " + (extra[0].get("text") or "")
        if src.get("algolia", {}).get("pages") and saved:
            PAGE_TEXT.mkdir(parents=True, exist_ok=True)
            saved_path.write_text(json.dumps(saved, ensure_ascii=False, indent=0, sort_keys=True))
    if stats["status"] == "ok" and offers and stats["blocked"] and not stats["pages"]:
        stats["status"] = "blocked: product pages"  # offers kept; the browser step retries
    if stats["status"] == "ok" and not offers:
        stats["status"] = "empty" if not stats["blocked"] else "blocked"
    stats["offers"] = len(offers)
    stats["seconds"] = round(time.time() - t0)
    RAW.mkdir(parents=True, exist_ok=True)
    if offers or not (RAW / f"{src['id']}.jsonl").exists():
        with open(RAW / f"{src['id']}.jsonl", "w") as f:
            for o in offers:
                f.write(json.dumps(o, ensure_ascii=False) + "\n")
    log(f"{src['id']}: {stats}")
    return stats


def main(argv=None):
    # If a request ever hangs past the per-request timeout, dump every thread's stack once.
    faulthandler.dump_traceback_later(1500, exit=False)
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--skip", nargs="*", default=[])
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--browser-only", action="store_true")
    ap.add_argument("--cache-age", type=int, default=int(os.environ.get("OMEGA_CACHE_AGE", "0")))
    ap.add_argument("--workers", type=int, default=24)
    args = ap.parse_args(argv)

    catalog = json.loads((ROOT / "catalog.json").read_text())["sources"]
    sources = [s for s in catalog if not s.get("disabled")]
    if args.only:
        sources = [s for s in sources if s["id"] in args.only]
    sources = [s for s in sources if s["id"] not in args.skip]
    http = Http(delay=float(os.environ.get("OMEGA_DELAY", "1.0")), log=log)
    cache = PageCache(args.cache_age)
    report = {"started_at": now(), "sources": []}

    plain = [s for s in sources if not s.get("browser")]
    via_browser = [s for s in sources if s.get("browser")]
    delay = float(os.environ.get("OMEGA_DELAY", "1.0"))
    if not args.browser_only:
        with cf.ThreadPoolExecutor(args.workers) as ex:
            # One client per source: curl sessions are not shared between threads.
            for st in ex.map(lambda s: run_source(s, Http(delay=delay, log=log), cache), plain):
                report["sources"].append(st)
    if args.browser_only:
        # Retry in Chromium the plain sources that were blocked in the HTTP phase.
        prev = ROOT / "runs" / "report.json"
        if prev.exists():
            blocked = {s["id"] for s in json.loads(prev.read_text())["sources"]
                       if not s["offers"] and (s["blocked"] or "blocked" in s["status"])}
            via_browser += [s for s in plain if s["id"] in blocked]
            if blocked:
                log(f"retrying in browser: {', '.join(sorted(blocked))}")
    if via_browser and not args.no_browser:
        from .browser import Browser

        with Browser(log=log) as br:
            for s in via_browser:
                report["sources"].append(run_source(s, http, cache, browser=br))

    report["finished_at"] = now()
    report["http"] = http.stats
    (ROOT / "runs").mkdir(exist_ok=True)
    name = "report-browser.json" if args.browser_only else "report.json"
    (ROOT / "runs" / name).write_text(json.dumps(report, indent=1))
    ok = sum(1 for s in report["sources"] if s["offers"])
    log(f"done: {ok}/{len(report['sources'])} sources with offers, "
        f"{sum(s['offers'] for s in report['sources'])} offers")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
