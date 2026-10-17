"""Build data/latest.json from raw offers: parse composition, group by product, attach IFOS lots.

Usage: python -m omega.build
"""

import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .composition import certs_of, form_of, kind_of, pack_size, resolve, totox_claim
from .ifos import ALIASES
from .ifos import norm as brand_norm

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / ".cache" / "raw"
CONF_RANK = {"override": 4, "high": 3, "medium": 2, "low": 1, None: 0}

SHOP_SUFFIX = re.compile(
    r"\s*[|\-–—]\s*(onatera|prozis|drasanvi|puro omega|santé de l'athlète|salud del atleta|"
    r"atida|mifarma|dosfarma|promofarma|nutritienda|bulevip|myprotein|hsn|cocooncenter|newpharma|"
    r"pharma gdd|pharmashopi|supersmart|bulk|naturitas|greenweez|nutripure|comprar.*|"
    r"compra.*|achat.*|acheter.*|avis.*|precio.*|prix.*)\s*(?:[|\-–—].*)?$",
    re.I,
)


def slug(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def clean_name(n):
    n = re.sub(r"\s+", " ", (n or "").replace("&#039;", "'").replace("&amp;", "&")).strip()
    prev = None
    while prev != n:
        prev = n
        n = SHOP_SUFFIX.sub("", n).strip()
    return n


def gtin_of(v):
    d = re.sub(r"\D", "", str(v or ""))
    if len(d) == 12:
        d = "0" + d
    if len(d) == 14 and d.startswith("0"):
        d = d[1:]
    if len(d) not in (8, 13) or set(d) == {"0"}:
        return None
    return d


def load_overrides():
    p = ROOT / "overrides.json"
    return json.loads(p.read_text()) if p.exists() else {"rules": []}


def apply_override(o, rules):
    for r in rules:
        m = r["match"]
        if "gtin" in m and o.get("gtin") != m["gtin"]:
            continue
        if "url" in m and m["url"] not in (o.get("url") or ""):
            continue
        if "source" in m and o["source"] != m["source"]:
            continue
        if "name" in m and not re.search(m["name"], o.get("name") or "", re.I):
            continue
        if "exclude" in r:
            return "exclude"
        if "composition" in r:
            c = dict(r["composition"])
            c.setdefault("confidence", "override")
            c.setdefault("method", "reviewed")
            c["total"] = c.get("total") or (c.get("epa") or 0) + (c.get("dha") or 0)
            o["comp"] = c
        if "pack" in r:
            o["pack"] = r["pack"]
        for k in ("kind", "form", "brand"):
            if k in r:
                o[k] = r[k]
    return None


def offer_record(raw, rules):
    name = clean_name(raw.get("product_name") or raw.get("h1"))
    variant = raw.get("variant_name")
    if variant and re.fullmatch(r"(default title|\d+ ?(x|unidad|ud)?)", variant, re.I):
        variant = None
    full = f"{name} {variant}" if variant and variant.lower() not in name.lower() else name
    text = raw.get("text") or ""
    pack = pack_size(variant, name, raw.get("h1"))
    if not pack:
        m = re.search(
            r"(?:contenido|contenu|contenance|contient|contains|formato|format|presentaci[oó]n|"
            r"conditionnement|envase de|bo[iî]te de|pot de|flacon de)\s*:?\s*(?:de\s+)?([^.\n]{0,40})",
            text, re.I)
        if m:
            pack = pack_size(m.group(1))
    o = {
        "source": raw["source"], "shop": raw["shop"], "country": raw["country"],
        "url": raw["url"], "name": full, "base_name": name, "brand": raw.get("brand"),
        "gtin": gtin_of(raw.get("gtin")), "price": raw.get("price"),
        "available": raw.get("available"), "image": raw.get("image"),
        "fetched_at": raw.get("fetched_at"), "pack": pack,
    }
    o["comp"] = resolve(name, variant, text, pack)
    o["kind"] = kind_of(full, text)
    o["form"] = form_of(text)
    o["certs"] = certs_of(f"{full} {text}")
    o["totox_claim"] = totox_claim(text)
    if apply_override(o, rules) == "exclude":
        return None
    return o


def product_key(o):
    if o["gtin"]:
        return "g" + o["gtin"]
    n = re.sub(r"\b\d+\s*(?:c[áa]psulas?|capsules?|caps|perlas?|softgels?|g[ée]lules?|ml|uds?)\b",
               "", o["base_name"], flags=re.I)
    count = int(o["pack"]["count"]) if o["pack"] else 0
    return f"n{slug(o['source'])}:{slug(n)}:{count}"


def brand_candidates(o):
    out = []
    if o.get("brand"):
        out.append(o["brand"])
    words = re.findall(r"[\w'&+.-]+", o["base_name"])
    out += [" ".join(words[:k]) for k in (3, 2, 1) if len(words) >= k]
    out.append(o["shop"])
    return out


def ifos_index(path):
    if not path.exists():
        return {}, {}
    data = json.loads(path.read_text())
    by_brand = defaultdict(list)
    for p in data.get("products", []):
        if p.get("report"):
            by_brand[p["brand_id"]].append(p)
    brands = {b["id"]: brand_norm(b["name"]) for b in data.get("brands", [])}
    return by_brand, brands


def rda_count(rda):
    m = re.search(r"(\d+(?:\.\d+)?)\s*(softgel|capsule|cap|pearl|tablet|gumm|ml|teaspoon|tablespoon)",
                  rda or "", re.I)
    if not m:
        return None, None
    n = float(m.group(1))
    w = m.group(2).lower()
    if w == "teaspoon":
        return 5.0, "ml"
    if w == "tablespoon":
        return 10.0, "ml"
    return n, ("ml" if w == "ml" else "capsule")


def tokens(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    stop = {"omega", "3", "omega-3", "capsules", "capsulas", "softgels", "perlas", "mg", "de", "the",
            "oil", "aceite", "huile", "fish", "pescado", "poisson", "con", "with", "avec", "y", "et"}
    return {t for t in re.findall(r"[a-z0-9]+", s) if t not in stop and len(t) > 1}


def match_ifos(group, by_brand, brand_names):
    """Pick the registry product whose label EPA/DHA per daily dose matches this product."""
    comp = group["comp"]
    if not comp or comp.get("epa") is None:
        return None
    cands = set()
    for o in group["offers"]:
        for b in brand_candidates(o):
            n = brand_norm(b)
            if n in ALIASES:
                cands.add(ALIASES[n])
            for bid, bn in brand_names.items():
                if bn and len(bn) >= 3 and (n == bn or (" " in bn and n.startswith(bn + " "))):
                    cands.add(bid)
    best = None
    for bid in cands:
        for p in by_brand.get(bid, []):
            rep = p["report"]
            if "epa" not in rep or "dha" not in rep:
                continue
            n, unit = rda_count(p.get("rda"))
            if unit and unit != comp["unit"]:
                continue
            le, ld = rep["epa"]["label"], rep["dha"]["label"]
            fits = []
            for k in ([n] if n else []) + [1, 2, 3, 4, 5, 6, 8, 10]:
                pe, pd = le / k, ld / k
                if abs(pe - comp["epa"]) <= max(6, 0.06 * comp["epa"]) and abs(pd - comp["dha"]) <= max(
                    6, 0.06 * comp["dha"]
                ):
                    fits.append(k)
            if not fits:
                continue
            nt = tokens(p.get("name"))
            gt = tokens(group["name"])
            sim = len(nt & gt) / max(1, len(nt | gt))
            score = 1 + sim + (0.5 if n in fits else 0)
            if best is None or score > best[0]:
                best = (score, p, fits[0])
    if not best:
        return None
    p, k = best[1], best[2]
    rep = p["report"]
    return {
        "id": p["id"], "name": p.get("name"), "brand": p.get("brand"), "page": p["page"],
        "report_url": p.get("report_url"), "lot": p.get("lot"), "expiry": p.get("expiry"),
        "lots": p.get("lots"), "dose_units": k,
        "label": [rep["epa"]["label"], rep["dha"]["label"]],
        "measured": [rep["epa"]["measured"], rep["dha"]["measured"]],
        **{f: rep.get(f) for f in ("totox", "peroxide", "anisidine", "mercury", "pcbs", "dioxins")},
    }


def carry_over(products, report, max_age_days=7):
    """Keep last-known offers for sources that failed this run (marked by their old fetched_at)."""
    prev_path = ROOT / "data" / "latest.json"
    if not prev_path.exists():
        return {}
    failed = {s["id"] for s in report.get("sources", []) if s.get("offers", 0) == 0}
    if not failed:
        return {}
    prev = json.loads(prev_path.read_text())
    cutoff = datetime.now(timezone.utc).timestamp() - max_age_days * 86400
    by_id = {g["id"]: g for g in products}
    carried = Counter()
    for g in prev.get("products", []):
        keep = []
        for o in g.get("offers", []):
            if o.get("source") not in failed or not o.get("fetched_at"):
                continue
            if datetime.fromisoformat(o["fetched_at"]).timestamp() < cutoff:
                continue
            keep.append(o)
        if not keep:
            continue
        carried.update(o["source"] for o in keep)
        if g["id"] in by_id:
            by_id[g["id"]]["offers"].extend(keep)
        else:
            g = dict(g, offers=keep)
            products.append(g)
            by_id[g["id"]] = g
    for g in products:
        priced = [x["eur_per_g"] for x in g["offers"] if x.get("eur_per_g")]
        g["best_eur_per_g"] = min(priced) if priced else None
    return carried


def main():
    rules = load_overrides().get("rules", [])
    raws = []
    for p in sorted(RAW.glob("*.jsonl")):
        raws += [json.loads(line) for line in p.open()]
    offers = [o for o in (offer_record(r, rules) for r in raws) if o]
    groups = defaultdict(list)
    for o in offers:
        groups[product_key(o)].append(o)

    by_brand, brand_names = ifos_index(ROOT / "data" / "ifos.json")
    products, stats = [], Counter()
    for key, offs in groups.items():
        comps = [o["comp"] for o in offs if o["comp"]]
        comps.sort(key=lambda c: -CONF_RANK.get(c.get("confidence"), 0))
        comp = comps[0] if comps else None
        # Agreeing readings from several shops raise confidence.
        if comp and comp["confidence"] == "low":
            same = [c for c in comps if abs(c["total"] - comp["total"]) < 1]
            if len({id(c) for c in same}) >= 2 and len(offs) >= 2:
                comp = dict(comp, confidence="medium", method=comp["method"] + " +agree")
        names = Counter(o["base_name"] for o in offs)
        name = min(names, key=lambda n: (-names[n], len(n)))
        packs = [o["pack"] for o in offs if o["pack"]]
        pack = Counter(json.dumps(p, sort_keys=True) for p in packs).most_common(1)
        pack = json.loads(pack[0][0]) if pack else None
        kinds = Counter(o["kind"] for o in offs)
        g = {
            "id": key, "name": name, "brand": next((o["brand"] for o in offs if o["brand"]), None),
            "gtin": next((o["gtin"] for o in offs if o["gtin"]), None),
            "kind": kinds.most_common(1)[0][0],
            "form": next((o["form"] for o in offs if o["form"]), None),
            "certs": sorted({c for o in offs for c in o["certs"]}),
            "totox_claim": next((o["totox_claim"] for o in offs if o["totox_claim"]), None),
            "comp": comp, "offers": offs, "pack": pack,
            "image": next((o["image"] for o in offs if o["image"]), None),
        }
        g["ifos"] = match_ifos(g, by_brand, brand_names)
        out_offers = []
        for o in offs:
            pk = o["pack"] or pack
            row = {
                "shop": o["shop"], "source": o["source"], "country": o["country"], "url": o["url"],
                "name": o["name"], "price": o["price"], "available": o["available"],
                "count": pk["count"] if pk else None, "unit": pk["unit"] if pk else None,
                "fetched_at": o["fetched_at"],
            }
            ok = (comp and pk and o["price"] and pk["unit"] == comp["unit"]
                  and comp["confidence"] in ("high", "medium", "override"))
            if ok:
                grams = pk["count"] * comp["total"] / 1000
                row["eur_per_g"] = round(o["price"] / grams, 4) if grams else None
                if not row["eur_per_g"] or not 0.03 <= row["eur_per_g"] <= 15:
                    row["eur_per_g"] = None
                    stats["implausible_eur_per_g"] += 1
            out_offers.append(row)
        g["offers"] = out_offers
        priced = [x["eur_per_g"] for x in out_offers if x.get("eur_per_g")]
        g["best_eur_per_g"] = min(priced) if priced else None
        stats["products"] += 1
        stats["with_comp"] += bool(comp)
        stats["comparable"] += bool(priced)
        stats["ifos"] += bool(g["ifos"])
        products.append(g)

    for g in products:
        g.pop("pack", None)
    report = {"sources": []}
    for name in ("report.json", "report-browser.json"):
        rp = ROOT / "runs" / name
        if rp.exists():
            report["sources"] += json.loads(rp.read_text()).get("sources", [])
    carried = carry_over(products, report)
    products.sort(key=lambda g: (g["best_eur_per_g"] is None, g["best_eur_per_g"] or 0))
    per_source = Counter(o["source"] for o in offers)
    comparable_source = Counter(x["source"] for g in products for x in g["offers"] if x.get("eur_per_g"))
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "stats": dict(stats, offers=len(offers)),
        "sources": [
            {"id": s["id"], "status": s["status"], "offers": per_source.get(s["id"], 0),
             "comparable": comparable_source.get(s["id"], 0), "carried": carried.get(s["id"], 0)}
            for s in report.get("sources", [])
        ],
        "products": products,
    }
    (ROOT / "data").mkdir(exist_ok=True)
    (ROOT / "data" / "report.json").write_text(json.dumps(report, indent=1))
    (ROOT / "data" / "latest.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    out["stats"]["site_products"] = export_site(out)
    print(json.dumps(out["stats"]))
    return 0



def export_site(out):
    """Slim, comparable-only copy for the static site."""
    rows = []
    for g in out["products"]:
        offers = [o for o in g["offers"] if o.get("eur_per_g")]
        if not offers:
            continue
        c = g["comp"]
        offers.sort(key=lambda o: o["eur_per_g"])
        rows.append({
            "id": g["id"], "name": g["name"], "brand": g["brand"], "kind": g["kind"], "form": g["form"],
            "certs": g["certs"], "gtin": g["gtin"],
            "epa": c["epa"], "dha": c["dha"], "total": c["total"], "unit": c["unit"],
            "conf": c["confidence"], "method": c["method"], "snippet": (c.get("snippet") or "")[:260],
            "dose": c.get("daily_dose"),
            "totox_claim": g["totox_claim"], "ifos": g["ifos"],
            "offers": [{k: o[k] for k in ("shop", "country", "url", "price", "count", "unit",
                                           "eur_per_g", "available", "fetched_at")} for o in offers],
        })
    site = {
        "generated_at": out["generated_at"],
        "stats": out["stats"],
        "shops": sorted({(o["shop"], o["country"]) for r in rows for o in r["offers"]}),
        "products": rows,
    }
    (ROOT / "site").mkdir(exist_ok=True)
    (ROOT / "site" / "data.json").write_text(json.dumps(site, ensure_ascii=False, separators=(",", ":")))
    return len(rows)


if __name__ == "__main__":
    sys.exit(main())
