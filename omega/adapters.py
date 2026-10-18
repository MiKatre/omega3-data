"""Source adapters. Each yields raw offer dicts: shop fields, price, pack text and composition text."""

import html as htmllib
import json
import re
from urllib.parse import urljoin, urlsplit

from .http import Blocked
from .page import parse_product

MARINE = re.compile(
    r"om[eé]ga[\s\-_]?3|omega3|\bEPA\b|\bDHA\b|krill|aceite de pescado|huile de poisson|fish oil|"
    r"h[ií]gado de bacalao|foie de morue|cod liver|epax|omegor|aceite de krill|calamar|calanus",
    re.I,
)
NOT_MARINE = re.compile(
    r"\b(lin|linaza|lino|flax|chia|ch[ií]a|perilla|onagra|primrose|bourrache|borraja|argousier|"
    r"espino amarillo|sacha inchi|nigelle|n[ée]gra|courge|calabaza|cannabis|chanvre|c[áa]ñamo)\b|"
    r"\b(perros?|gatos?|chiens?|chats?|mascotas?|pets?|dogs?|cats?|v[ée]t[ée]rinaire|veterinari[ao]|"
    r"caballos?|chevaux)\b|shampoo|champ[uú]|cr[eè]me|crema|s[ée]rum|lotion|gel douche|savon|"
    r"pienso|croquettes|t-shirt|shaker|libro|book|test kit|index test",
    re.I,
)

SITEMAP_SKIP = re.compile(
    r"/blog|/c/|/categor|/ingredientes/|/actifs/|conseil|/articles?/|/b/|"
    r"/inhaltsstoffe|benefits|/guia|/dossier|/actualites|comprendre|/ranges/|libro|/avis|/reviews?|"
    r"/pages?/|/tag/|/search|/recherche|/buscar",
    re.I,
)
SITEMAP_KW = re.compile(
    r"om[eé]ga[-_ ]?3|omega3|(^|[-/_])epa([-_/]|$)|(^|[-/_])dha([-_/]|$)|krill|aceite-de-pescado|"
    r"huile-de-poisson|fish-oil|higado-de-bacalao|foie-de-morue|cod-liver|epax|omegor|"
    r"ultra-omega|omega-forte|super-omega|mega-omega|aceite-de-higado",
    re.I,
)


def strip_html(s):
    s = re.sub(r"<(br|/p|/li|/tr|/td|/th|/h\d)[^>]*>", " \n ", s or "", flags=re.I)
    return re.sub(r"[ \t\r\f\v]+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def is_candidate(*texts):
    blob = " ".join(t or "" for t in texts)
    return bool(MARINE.search(blob))


def is_excluded(name):
    return bool(NOT_MARINE.search(name or ""))


# ---------------------------------------------------------------- Shopify
def shopify(src, http, log):
    base = src["base"].rstrip("/")
    page = 1
    seen = 0
    while page <= 40:
        raw = http.get(f"{base}/products.json?limit=250&page={page}", accept="application/json")
        products = json.loads(raw).get("products", [])
        if not products:
            break
        for p in products:
            seen += 1
            tags = p.get("tags") or []
            tags = tags if isinstance(tags, list) else [tags]
            body = strip_html(p.get("body_html"))
            head = " ".join([p["title"], p.get("product_type") or "", " ".join(tags), p["handle"]])
            if not is_candidate(head) or is_excluded(p["title"]):
                continue
            url = f"{base}/products/{p['handle']}"
            for v in p.get("variants", []):
                yield {
                    "url": url,
                    "product_name": p["title"],
                    "variant_name": None if v.get("title") in ("Default Title", "Default title")
                    else v.get("title"),
                    "brand": p.get("vendor"),
                    "gtin": v.get("barcode") or None,
                    "sku": v.get("sku"),
                    "price": float(v["price"]) if v.get("price") else None,
                    "currency": src.get("currency", "EUR"),
                    "available": v.get("available"),
                    "image": (p.get("images") or [{}])[0].get("src"),
                    "text": body,
                }
        page += 1
    log(f"{src['id']}: scanned {seen} products")


# ---------------------------------------------------------------- WooCommerce Store API
def woo(src, http, log):
    base = src["base"].rstrip("/")
    page = 1
    while page <= 30:
        raw = http.get(f"{base}/wp-json/wc/store/v1/products?per_page=100&page={page}",
                       accept="application/json", allow_404=True)
        if raw is None:
            break
        products = json.loads(raw)
        if not products:
            break
        for p in products:
            name = htmllib.unescape(p["name"])
            body = strip_html((p.get("short_description") or "") + " \n " + (p.get("description") or ""))
            cats = " ".join(c.get("name", "") for c in p.get("categories", []))
            if not is_candidate(name, cats, p.get("slug")) or is_excluded(name):
                continue
            pr = p.get("prices") or {}
            minor = 10 ** int(pr.get("currency_minor_unit", 2))
            price = int(pr["price"]) / minor if pr.get("price") else None
            yield {
                "url": p.get("permalink"),
                "product_name": name,
                "variant_name": None,
                "brand": (p.get("brands") or [{}])[0].get("name") if p.get("brands") else None,
                "gtin": None,
                "sku": p.get("sku"),
                "price": price,
                "currency": pr.get("currency_code", "EUR"),
                "available": p.get("is_in_stock"),
                "image": (p.get("images") or [{}])[0].get("src"),
                "text": body,
                "_needs_page": True,
            }
        page += 1


# ---------------------------------------------------------------- Sitemaps + product pages
def sitemap_urls(src, http, log, fetch_xml=None):
    host = urlsplit(src["base"]).netloc
    get_xml = fetch_xml or (lambda u: http.get(u, accept="application/xml,text/xml,*/*"))
    maps = src.get("sitemaps")
    if not maps:
        try:
            rb = get_xml(src["base"].rstrip("/") + "/robots.txt") or ""
        except Exception:
            rb = ""
        maps = re.findall(r"(?im)^sitemap:\s*(\S+)", rb) or [src["base"].rstrip("/") + "/sitemap.xml"]
    seen, queue, urls, blocked = set(), list(dict.fromkeys(maps)), [], []
    include = re.compile(src["include"]) if src.get("include") else None
    while queue and len(seen) < src.get("max_sitemaps", 80):
        m = queue.pop(0)
        if m in seen:
            continue
        seen.add(m)
        try:
            x = get_xml(m)
        except Exception as e:
            log(f"{src['id']}: sitemap {m} failed: {e}")
            if isinstance(e, Blocked):
                blocked.append(e)
            continue
        locs = [htmllib.unescape(u) for u in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", x or "")]
        if "<sitemapindex" in (x or ""):
            locs.sort(key=lambda u: 0 if re.search(r"product|produit|producto|article", u, re.I) else 1)
            sub = src.get("sitemap_filter")
            queue.extend(
                u for u in locs
                if not re.search(r"image|blog|post|categor|page-sitemap|cms|brand|marque|video", u, re.I)
                and (not sub or re.search(sub, u))
            )
        else:
            for u in locs:
                if SITEMAP_KW.search(u) and not SITEMAP_SKIP.search(u):
                    if include and not include.search(u):
                        continue
                    if urlsplit(u).netloc.split(":")[0].endswith(host.replace("www.", "")):
                        urls.append(u)
    if not urls and blocked:
        # Every sitemap was refused: report the source as blocked so it is retried in Chromium.
        raise blocked[0]
    return sorted(set(urls))


def page_offers(src, url, html):
    rec = parse_product(html, url)
    name = rec.get("name") or rec.get("h1") or ""
    if is_excluded(name):
        return []
    text = " \n ".join(x for x in [rec.get("description") or "", rec.get("text") or ""] if x)
    out = []
    variants = rec["variants"] or [{}]
    for v in variants:
        if v.get("currency") and v["currency"] != "EUR":
            continue
        out.append({
            "url": v.get("url") if v.get("url") and v.get("url").startswith("http") else url,
            "product_name": name,
            "variant_name": v.get("name") if v.get("name") and v.get("name") != name else None,
            "brand": rec.get("brand"),
            "gtin": v.get("gtin") or rec.get("gtin"),
            "sku": v.get("sku") or rec.get("sku"),
            "price": v.get("price"),
            "currency": v.get("currency") or "EUR",
            "available": None if not v.get("availability") else v["availability"] in (
                "InStock", "in stock", "instock", "LimitedAvailability", "OnlineOnly", "PreOrder"),
            "image": rec.get("image"),
            "text": text,
            "h1": rec.get("h1"),
        })
    # Several offers for the same variant are quantity tiers (buy 2, buy 3): keep the single-unit
    # price and remember the best tier separately.
    uniq = {}
    for o in out:
        k = (o["variant_name"], o["gtin"])
        if k in uniq:
            first = uniq[k]
            if o["price"] and first["price"] and o["price"] < first["price"]:
                first["volume_price"] = min(first.get("volume_price") or 1e9, o["price"])
            continue
        uniq[k] = o
    return list(uniq.values())


def sitemap(src, http, log, fetch_page, fetch_xml=None):
    urls = sitemap_urls(src, http, log, fetch_xml)
    limit = src.get("max_pages", 400)
    log(f"{src['id']}: {len(urls)} candidate URLs from sitemaps")
    for u in urls[:limit]:
        html = fetch_page(u)
        if html is None:
            continue
        yield from page_offers(src, u, html)


def listing(src, http, log, fetch_page):
    """Category/search listing pages -> product links -> product pages."""
    links = []
    for lst in src["listings"]:
        html = fetch_page(lst)
        if not html:
            continue
        for h in re.findall(r'href="([^"#]+)"', html):
            u = urljoin(lst, htmllib.unescape(h)).split("?")[0]
            if re.search(src["product_pattern"], u) and SITEMAP_KW.search(u):
                links.append(u)
    links = list(dict.fromkeys(links))
    log(f"{src['id']}: {len(links)} product links from listings")
    for u in links[: src.get("max_pages", 200)]:
        html = fetch_page(u)
        if html:
            yield from page_offers(src, u, html)


def urls(src, http, log, fetch_page):
    """A fixed list of product pages (shops with one or two omega-3 products and no usable sitemap)."""
    for u in src["urls"]:
        html = fetch_page(u)
        if html:
            yield from page_offers(src, u, html)
