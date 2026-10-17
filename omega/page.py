"""Extract a product offer from an HTML product page: JSON-LD first, then meta tags, plus text."""

import html as htmllib
import json
import re

from selectolax.parser import HTMLParser


def _walk(node):
    if isinstance(node, list):
        for x in node:
            yield from _walk(x)
    elif isinstance(node, dict):
        yield node
        for k in ("@graph", "mainEntity", "hasVariant"):
            if k in node:
                yield from _walk(node[k])


def _types(node):
    t = node.get("@type", [])
    return {t} if isinstance(t, str) else set(t)


def _loads(raw):
    raw = raw.strip().rstrip(";")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Some shops leave raw newlines or trailing commas in JSON-LD.
        cleaned = re.sub(r",\s*([}\]])", r"\1", raw.replace("\n", " ").replace("\t", " "))
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            return None


def _num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(" ", "").replace("€", "").replace("EUR", "").strip()
    if re.fullmatch(r"\d{1,3}(\.\d{3})*,\d{1,2}", s):
        s = s.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d+,\d{1,2}", s):
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _offers(o):
    if isinstance(o, dict) and "offers" in o and o.get("@type") != "Offer":
        o = o["offers"]
    out = []
    for x in o if isinstance(o, list) else [o]:
        if not isinstance(x, dict):
            continue
        if "offers" in x and isinstance(x["offers"], (list, dict)):
            out.extend(_offers(x["offers"]))
            continue
        price = _num(x.get("price") or x.get("lowPrice"))
        if price is None and isinstance(x.get("priceSpecification"), dict):
            price = _num(x["priceSpecification"].get("price"))
        if price is None and isinstance(x.get("priceSpecification"), list):
            for ps in x["priceSpecification"]:
                price = price or _num(ps.get("price"))
        out.append(
            {
                "price": price,
                "high": _num(x.get("highPrice")),
                "currency": x.get("priceCurrency"),
                "availability": str(x.get("availability", "")).split("/")[-1] or None,
                "sku": x.get("sku"),
                "gtin": x.get("gtin13") or x.get("gtin") or x.get("gtin14") or x.get("gtin8"),
                "name": x.get("name"),
                "url": x.get("url"),
            }
        )
    return out


def _brand(b):
    if isinstance(b, list):
        b = b[0] if b else None
    if isinstance(b, dict):
        return b.get("name")
    return b


def clean_text(tree):
    for sel in ("script", "style", "noscript", "svg", "header", "footer", "nav", "iframe"):
        for n in tree.css(sel):
            n.decompose()
    body = tree.body or tree.root
    txt = body.text(separator=" ") if body else ""
    return re.sub(r"\s+", " ", htmllib.unescape(txt)).strip()


def parse_product(html, url):
    tree = HTMLParser(html)
    products = []
    for s in tree.css('script[type="application/ld+json"]'):
        data = _loads(s.text() or "")
        if data is None:
            continue
        for node in _walk(data):
            if _types(node) & {"Product", "ProductGroup", "IndividualProduct", "Drug"}:
                products.append(node)
    meta = {}
    for m in tree.css("meta"):
        k = m.attributes.get("property") or m.attributes.get("name") or m.attributes.get("itemprop")
        v = m.attributes.get("content")
        if k and v and k not in meta:
            meta[k] = v
    title_node = tree.css_first("h1")
    h1 = re.sub(r"\s+", " ", title_node.text()).strip() if title_node else None
    rec = {"url": url, "h1": h1, "variants": []}
    if products:
        p = products[0]
        rec.update(
            name=p.get("name"),
            brand=_brand(p.get("brand")),
            gtin=p.get("gtin13") or p.get("gtin") or p.get("gtin14") or p.get("ean"),
            sku=p.get("sku"),
            description=htmllib.unescape(re.sub(r"<[^>]+>", " ", str(p.get("description") or ""))),
            image=(p.get("image")[0] if isinstance(p.get("image"), list) else p.get("image")),
        )
        if isinstance(rec["image"], dict):
            rec["image"] = rec["image"].get("url")
        main = [p] + [v for v in p.get("hasVariant", []) if isinstance(v, dict)] if isinstance(
            p.get("hasVariant"), list) else [p]
        for node in main:
            for off in _offers(node):
                off["name"] = off.get("name") or node.get("name")
                off["gtin"] = off.get("gtin") or node.get("gtin13") or node.get("gtin")
                rec["variants"].append(off)
    if not rec.get("name"):
        rec["name"] = meta.get("og:title") or h1
    if not rec["variants"]:
        price = _num(
            meta.get("product:price:amount")
            or meta.get("og:price:amount")
            or meta.get("price")
        )
        if price is None:
            n = tree.css_first('[itemprop="price"]')
            if n is not None:
                price = _num(n.attributes.get("content") or n.text())
        if price is not None:
            rec["variants"].append(
                {
                    "price": price,
                    "currency": meta.get("product:price:currency") or meta.get("og:price:currency"),
                    "availability": meta.get("product:availability"),
                    "name": rec.get("name"),
                }
            )
    rec["text"] = clean_text(tree)
    return rec
