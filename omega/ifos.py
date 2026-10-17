"""IFOS / IKOS / IAOS registry (SGS Nutrasource): brands, products and the latest lot report.

Only brands that match a brand seen in collected offers are expanded, so a run stays small.
Writes data/ifos.json.
"""

import argparse
import html as htmllib
import io
import json
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from .http import Http

ROOT = Path(__file__).resolve().parent.parent
BASE = "https://certifications.nutrasource.ca"
FILES = "https://andi.nutrasource.ca/files/"

LEGAL = re.compile(
    r"\b(inc|ltd|llc|gmbh|s\.?l\.?u?|s\.?a\.?|sas|sarl|b\.?v\.?|co|corp|corporation|company|limited|"
    r"pty|ag|as|a/s|ab|oy|srl|spa|kft|laboratories|laboratorios|laboratoires|labs?|formulations|"
    r"products|health|international|group|nutrition|nutraceuticals?)\b\.?",
    re.I,
)
# Retail brand names that differ from the registry's company names.
ALIASES = {
    "essential series": "HSNS",
    "hsn": "HSNS",
    "ivb wellness lab": "VNBS",
    "ivb wellness": "VNBS",
    "ivb": "VNBS",
    "norsan": "SANO",
    "puro omega": "PURO",
    "gema herrerias": "GEMA",
}


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"[^a-z0-9&+ ]", " ", s)
    s = LEGAL.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def all_brands(http):
    out, page = [], 1
    while page < 40:
        raw = http.get(f"{BASE}/umbraco/surface/NutrasourceContent/GetFilteredBrands?pageNumber={page}"
                       f"&pageSize=100&byName=", accept="application/json")
        lst = json.loads(raw).get("list") or []
        if not lst:
            break
        for b in lst:
            if b.get("HasIfos") or b.get("HasIkos") or b.get("HasIAOS"):
                out.append({"id": b["BrandId"], "name": b["Name"].strip(), "norm": norm(b["Name"]),
                            "ifos": b.get("HasIfos"), "ikos": b.get("HasIkos"),
                            "iaos": b.get("HasIAOS")})
        page += 1
    return out


def match_brands(brands, offer_brands):
    by_id = {b["id"]: b for b in brands}
    hits = {}
    for raw in offer_brands:
        n = norm(raw)
        if not n or len(n) < 3:
            continue
        if n in ALIASES and ALIASES[n] in by_id:
            hits[ALIASES[n]] = by_id[ALIASES[n]]
            continue
        for b in brands:
            bn = b["norm"]
            if not bn or len(bn) < 3:
                continue
            if n == bn or (" " in bn and n.startswith(bn + " ")):
                hits[b["id"]] = b
    return list(hits.values())


VALUE_PATTERNS = {
    "epa": r"^EPA\s+([\d.]+)\s+([\d.]+)",
    "dha": r"^DHA\s+([\d.]+)\s+([\d.]+)",
    "total_o3": r"^(?:Total\s+Omega-?3s?|Omega-?3\s+Total)\s+([\d.]+)\s+([\d.]+)",
    "totox": r"TOTOX\s*<=?\s*[\d.]+\s+([\d.]+)",
    "peroxide": r"Peroxide\s*<=?.*?meq/kg\s+([\d.]+)",
    "anisidine": r"Anisidine\s*<=?\s*[\d.]+\s+([\d.]+)",
    "acid_value": r"Acid\s+Value\s*<=?.*?KOH/g\s+([\d.]+)",
    "mercury": r"Mercury\s*<=?.*?ppm\s+(<?\s*[\d.]+)",
    "lead": r"Lead\s*<=?.*?ppm\s+(<?\s*[\d.]+)",
    "pcbs": r"^PCBs\s*<=?.*?ppb\s+(<?\s*[\d.]+)",
    "dioxins": r"Dioxins\s+and\s+Furans\s*<=?.*?ppt\s+(<?\s*[\d.]+)",
    "dl_pcbs": r"Dioxin-Like\s+PCBs\s*<=?.*?ppt\s+(<?\s*[\d.]+)",
}


def parse_report(pdf_bytes):
    import pypdf

    text = "\n".join(p.extract_text() or "" for p in pypdf.PdfReader(io.BytesIO(pdf_bytes)).pages)
    out = {}
    for k, pat in VALUE_PATTERNS.items():
        m = re.search(pat, text, re.M | re.I)
        if not m:
            continue
        if k in ("epa", "dha", "total_o3"):
            out[k] = {"label": float(m.group(1)), "measured": float(m.group(2))}
        else:
            out[k] = m.group(1).replace(" ", "")
    stars = re.search(r"(\d)\s*(?:out of 5|/\s*5)\s*stars?", text, re.I)
    if stars:
        out["stars"] = int(stars.group(1))
    return out


def product(http, pid):
    page = htmllib.unescape(http.get(f"{BASE}/certified-products/product?id={pid}"))
    body = re.sub(r"<style.*?</style>|<script.*?</script>", "", page[page.find("<body"):], flags=re.S)
    txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body))
    rec = {"id": pid, "page": f"{BASE}/certified-products/product?id={pid}"}
    m = re.search(r"Blog (.*?) Product Summary (.*?) Product Type: (.*?) Recommended Daily Allowance: "
                  r"(.*?) (?:Website|Testing Results)", txt)
    if m:
        rec.update(name=m.group(1).strip(), summary=m.group(2).strip()[:400],
                   type=m.group(3).strip(), rda=m.group(4).strip())
    else:
        h = re.search(r"<h1[^>]*>(.*?)</h1>", page, re.S)
        rec["name"] = re.sub(r"<[^>]+>|\s+", " ", h.group(1)).strip() if h else None
    lots = re.findall(r'<option value="([^"]+\.pdf)"\s*>([^<]*)', page)
    rec["lots"] = len(lots)
    if lots:
        f, label = lots[0]
        rec["lot_label"] = label.strip()
        lm = re.search(r"Lot #:\s*([^()]+?)\s*(?:-\s*[^()]*)?\(([^)]+)\)", label)
        if lm:
            rec["lot"], rec["expiry"] = lm.group(1).strip(), lm.group(2).strip()
        rec["report_url"] = FILES + quote(f)
        try:
            rec["report"] = parse_report(http.get(rec["report_url"], binary=True))
        except Exception as e:
            rec["report_error"] = str(e)[:160]
    return rec


def brand_products(http, bid):
    page = http.get(f"{BASE}/certified-products/brand?id={bid}")
    return list(dict.fromkeys(re.findall(r"product\?id=(\w+)", page)))


def offer_brands():
    names = set()
    for p in (ROOT / ".cache" / "raw").glob("*.jsonl"):
        for line in p.open():
            o = json.loads(line)
            for k in ("brand", "shop"):
                if o.get(k):
                    names.add(o[k])
            words = re.findall(r"[\w'&+.-]+", o.get("product_name") or "")
            for k in (1, 2, 3):
                if len(words) >= k:
                    names.add(" ".join(words[:k]))
    return names


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--brands", nargs="*", help="registry brand ids to expand (default: matched)")
    args = ap.parse_args(argv)
    http = Http(delay=0.8)
    brands = all_brands(http)
    print(f"registry: {len(brands)} omega brands", flush=True)
    if args.brands:
        chosen = [b for b in brands if b["id"] in args.brands]
    else:
        chosen = match_brands(brands, offer_brands())
    print(f"expanding {len(chosen)}: {', '.join(b['name'] for b in chosen)}", flush=True)
    previous = {}
    out_path = ROOT / "data" / "ifos.json"
    if out_path.exists():
        previous = {p["id"]: p for p in json.loads(out_path.read_text()).get("products", [])}
    products = []
    for b in chosen:
        try:
            pids = brand_products(http, b["id"])
        except Exception as e:
            print(f"{b['id']}: {e}", flush=True)
            continue
        for pid in pids:
            try:
                rec = product(http, pid)
            except Exception as e:
                print(f"{pid}: {e}", flush=True)
                if pid in previous:
                    products.append(previous[pid])
                continue
            rec["brand_id"], rec["brand"] = b["id"], b["name"]
            products.append(rec)
            r = rec.get("report") or {}
            print(f"{pid} {rec.get('name')!s:40.40} lots={rec['lots']} totox={r.get('totox')}",
                  flush=True)
    out = {
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "brands": [{k: b[k] for k in ("id", "name")} for b in brands],
        "products": products,
    }
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"wrote {len(products)} IFOS products", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
