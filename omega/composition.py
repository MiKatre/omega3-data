"""Turn product names and descriptions into pack size and EPA/DHA per unit.

Labels say "per capsule", "par dose (4 capsules)", "por 2 perlas", "10 ml", percentages of a
1000 mg capsule, or nothing at all. Every result carries the matched snippet and a confidence so a
reviewer can check it and the site can hide guesses.
"""

import re
from collections import Counter

NUM = r"\d{1,2}(?:[ .  ]\d{3})+(?:,\d+)?|\d+(?:[.,]\d+)?"
UNIT_WORDS = (
    r"c[áa]psulas?(?:\s+blandas)?|capsules?|caps\b|c[áa]ps\b|g[ée]lules?|perlas?|perles?|softgels?|"
    r"comprim[ée]s?|comprimidos?|tabletas?|tablets?|gominolas?|gummies|gommes?|kapseln|kapsel|"
    r"licaps|vcaps|vegicaps|unidades|unit[ée]s|sticks?|sobres?|viales?|ampollas?|cuillères?|"
    r"cucharaditas?|cucharadas?|cuill[eè]res?\s+[àa]\s+caf[ée]"
)
UNIT_RE = re.compile(UNIT_WORDS, re.I)

BOILERPLATE_WORDS = re.compile(
    r"contribu|beneficial effect|efecto beneficioso|effet b[ée]n[ée]fique|se obtiene con|"
    r"s['’]obtient|is obtained with|ingesta diaria de 250|apport journalier de 250|daily intake of 250|"
    r"bienfait|recomendad[ao] por la EFSA|EFSA|presi[oó]n arterial|pression art[ée]rielle|"
    r"triglic[ée]ridos en sangre|triglyc[ée]rides sanguins|lactan|allaitement|embaraz|grossesse|"
    r"ingesta diaria adicional|apport quotidien suppl[ée]mentaire",
    re.I,
)
FOCUS = re.compile(r"EPA|DHA|eicosapenta|docosahexa", re.I)


def focus(text, radius=700):
    """Keep only windows around EPA/DHA mentions, then drop health-claim sentences."""
    spans = []
    for m in FOCUS.finditer(text):
        a, b = max(0, m.start() - radius), m.end() + radius
        if spans and a <= spans[-1][1]:
            spans[-1][1] = b
        else:
            spans.append([a, b])
    t = " \n ".join(text[a:b] for a, b in spans)
    parts = re.split(r"(?<=[.;\n])", t)
    return "".join(p for p in parts if not BOILERPLATE_WORDS.search(p))


def num(s):
    s = s.replace(" ", " ").replace(" ", " ").strip()
    if re.fullmatch(r"\d{1,3}(?:[ .]\d{3})+(?:,\d+)?", s):
        s = re.sub(r"[ .]", "", s).replace(",", ".")
    else:
        s = s.replace(",", ".")
    return float(s)


def unit_kind(word):
    w = (word or "").lower()
    if w.startswith("ml") or "cuchar" in w or "cuill" in w:
        return "ml"
    if re.match(r"gomin|gumm|gomm", w):
        return "gummy"
    return "capsule"


DAILY_RE = re.compile(
    rf"(\d{{1,2}}(?:[.,]5)?)(?:\s*(?:[àa]|-|o|ou|or|to)\s*(\d{{1,2}}))?\s*({UNIT_WORDS}|ml\b)[^.\n]{{0,40}}?"
    rf"(?:al\s+d[ií]a|diari[ao]s?|par\s+jour|/\s*jour|por\s+d[ií]a|per\s+day|a\s+day|daily|quotidien|"
    rf"journali[eè]re?s?)|"
    rf"(?:tomar|prendre|take|consommer|ingerir|avaler|dosis\s+diaria|dose\s+journali[eè]re|"
    rf"posolog[ií]a|posologie|modo\s+de\s+empleo|conseils?\s+d['’]utilisation|utilisation|"
    rf"recommended\s+dose|dosage\s+recommand[ée])\s*:?\s*(?:de\s+)?(\d{{1,2}}(?:[.,]5)?)"
    rf"(?:\s*(?:[àa]|-|o|ou|to)\s*(\d{{1,2}}))?\s*({UNIT_WORDS}|ml\b)",
    re.I,
)


def daily_dose(text):
    for m in DAILY_RE.finditer(text):
        n = m.group(2) or m.group(1) if m.group(1) else (m.group(5) or m.group(4))
        w = m.group(3) or m.group(6)
        try:
            v = num(n)
        except (TypeError, ValueError):
            continue
        if 0.5 <= v <= 12:
            return v, unit_kind(w)
    return None


# ------------------------------------------------------------------ pack size
PACK_RE = re.compile(
    rf"(?:(\d{{1,2}})\s*[x×]\s*)?(\d{{2,4}})(?:\s*\+\s*(\d{{1,3}}))?\s*({UNIT_WORDS})",
    re.I,
)
ML_RE = re.compile(r"(?:(\d{1,2})\s*[x×]\s*)?(\d{2,4}(?:[.,]\d+)?)\s*ml\b", re.I)
MULTI_RE = re.compile(
    r"(?:pack|lote?|lot|duplo|tr[ií]plo|bundle)\s*(?:de\s*)?(\d)\b|\b(\d)\s*(?:flacons|botes|frascos|"
    r"bo[iî]tes|envases|unidades|uds|packs)\b|\b[x×]\s*(\d)\b|\b(\d)\s*[x×]\b|(?:duplo)",
    re.I,
)


def pack_size(*names):
    """First explicit count in variant name, product name, then description."""
    for name in names:
        if not name:
            continue
        n = name.replace(" ", " ")
        best = None
        for m in PACK_RE.finditer(n):
            mult = int(m.group(1) or 1)
            count = int(m.group(2)) + int(m.group(3) or 0)
            word = m.group(4)
            if 5 <= count <= 2000:
                best = {"count": count * mult, "unit": unit_kind(word), "multi": mult}
                break
        if not best:
            m = ML_RE.search(n)
            if m and 10 <= num(m.group(2)) <= 2000:
                mult = int(m.group(1) or 1)
                best = {"count": num(m.group(2)) * mult, "unit": "ml", "multi": mult}
        if best:
            bonus = re.search(rf"\+\s*(\d{{1,3}})\s*(?:{UNIT_WORDS})?\s*(?:offert|gratis|gratuit|free|de\s+regalo|"
                              rf"regalo|cadeau)", n, re.I)
            if bonus and best["unit"] != "ml":
                best["count"] += int(bonus.group(1))
            if best["multi"] == 1:
                mm = MULTI_RE.search(n)
                if mm:
                    k = next((int(g) for g in mm.groups() if g), 2 if "duplo" in mm.group(0).lower()
                             else 1)
                    if 1 < k <= 6:
                        best["count"] *= k
                        best["multi"] = k
            return best
    return None


# ------------------------------------------------------------------ EPA / DHA amounts
E_LAB = (r"(?:\bEPA\b|\bE\.P\.A\.?|(?:[áa]cido|acide|acid)?\s*eicosapenta\w*(?:\s+acid)?"
         r"(?:\s*\(\s*EPA\s*\))?)")
D_LAB = (r"(?:\bDHA\b|\bD\.H\.A\.?|(?:[áa]cido|acide|acid)?\s*docosahexa\w*(?:\s+acid)?"
         r"(?:\s*\(\s*DHA\s*\))?)")
AMT_NUM = r"\d{1,2}(?:[ .]\d{3})+(?:,\d+)?|\d+(?:[.,]\d+)?"
TOKEN_RE = re.compile(
    rf"(?P<ED>{E_LAB}\s*(?:\+|&|/|\by\b|\bet\b|\band\b|\be\b|\bund\b)\s*{D_LAB})"
    rf"|(?P<DE>{D_LAB}\s*(?:\+|&|/|\by\b|\bet\b|\band\b|\be\b)\s*{E_LAB})"
    rf"|(?P<E>{E_LAB})|(?P<D>{D_LAB})"
    rf"|(?P<O3>om[eé]ga[\s-]?3(?![\s-]?[-/,]?\s*6)(?:\s+(?:totale?s?|totaux|total))?)"
    rf"|(?P<OIL>huile\s+de\s+(?:poissons?|krill|foie\s+de\s+morue|algues?)|aceite\s+de\s+"
    rf"(?:pescado|krill|h[ií]gado\s+de\s+bacalao|algas?)|fish\s+oil|krill\s+oil|algal\s+oil|"
    rf"phospholipides?|fosfol[ií]pidos?|vitamin[ea]\s+[ADE]\d?|astaxant\w+|colina|choline)"
    rf"|(?P<BASIS>(?:\bpor\b|\bpar\b|\bpour\b|\bper\b|\bpara\b|\bpro\b|\bcada\b|\bchaque\b|"
    rf"\beach\b|\bdans\b|teneur\s+pour|composici[oó]n\s+por|apport\s+pour)\s*(?:\w+\s+){{0,2}}?"
    rf"\(?\s*(?P<bn>\d{{1,3}}(?:[.,]5)?)?\s*(?P<bw>{UNIT_WORDS}|ml\b|g\b|gr\b|mililitros?|"
    rf"dose|dosis|portion|raci[oó]n|serving|toma|jour|d[ií]a|day))"
    rf"|(?P<AMT>(?P<an>{AMT_NUM})\s*(?P<au>mg|%|g\b|µg|mcg|ui\b|iu\b))",
    re.I,
)
GENITIVE = re.compile(r"^\s*(?:d['’]\s*|de\s+|of\s+|di\s+|du\s+)?(?:l['’]\s*)?(?:acide\s+|[áa]cido\s+)?\(?$",
                      re.I)
LABELS = ("E", "D", "ED", "DE", "O3", "OIL")


def tokens(text):
    out = []
    for m in TOKEN_RE.finditer(text):
        kind = m.lastgroup
        if kind in ("bn", "bw"):
            kind = "BASIS"
        if kind == "an" or kind == "au":
            kind = "AMT"
        t = {"k": kind, "s": m.start(), "e": m.end()}
        if kind == "AMT":
            try:
                t["v"] = num(m.group("an"))
            except ValueError:
                continue
            t["u"] = m.group("au").lower()
        elif kind == "BASIS":
            t["n"] = num(m.group("bn")) if m.group("bn") else None
            t["w"] = m.group("bw").lower()
            t["raw"] = m.group(0)
        out.append(t)
    return out


def _basis_of(text, toks, i, amount_tok):
    """Nearest basis before token i (or right after the amount)."""
    # Right after the amount: "500 mg d'EPA par capsule".
    if amount_tok is not None:
        tail = text[amount_tok["e"]:amount_tok["e"] + 90]
        m = re.match(rf"[^\d\n.;]{{0,60}}?\b(?:par|por|per|pour)\s+(?:dose|dosis|toma|portion|raci[oó]n|serving)"
                     rf"(?:\s+(?:journali[eè]re|diaria|quotidienne|recommand[ée]e|recomendada|daily))?\s*"
                     rf"[(:]?\s*(?:de\s+)?(\d{{1,2}})\s*({UNIT_WORDS}|ml\b)", tail, re.I)
        if m:
            return num(m.group(1)), unit_kind(m.group(2)), m.group(0), "high"
        m = re.match(rf"[^\d\n.;]{{0,30}}?\b(?:par|por|per|pour|cada|each|chaque)\s+(?:(\d{{1,2}})\s*)?"
                     rf"({UNIT_WORDS}|ml\b)", tail, re.I)
        if m:
            return (num(m.group(1)) if m.group(1) else 1.0), unit_kind(m.group(2)), m.group(0), "high"
    limit = toks[i]["s"] - 320
    for j in range(i - 1, -1, -1):
        t = toks[j]
        if t["s"] < limit:
            break
        if t["k"] != "BASIS":
            continue
        w = t["w"]
        if w in ("g", "gr"):
            return None, None, t["raw"], "per100g"
        if re.match(r"dose|dosis|portion|raci|serving|toma|jour|d[ií]a|day", w):
            # "pour 4 capsules" often follows "par dose": keep looking only a little further.
            nxt = text[t["e"]:t["e"] + 40]
            m = re.match(rf"\s*(?:journali[eè]re|diaria|quotidienne)?\s*:?\s*\(?\s*(\d{{1,2}})\s*({UNIT_WORDS}|ml\b)",
                         nxt, re.I)
            if m:
                return num(m.group(1)), unit_kind(m.group(2)), t["raw"] + m.group(0), "high"
            return None, None, t["raw"], "daily"
        if w.startswith("ml") or w.startswith("milil"):
            n = t["n"] or 1.0
            if n >= 100:
                return n, "ml", t["raw"], "high"
            return n, "ml", t["raw"], "high"
        return (t["n"] or 1.0), unit_kind(w), t["raw"], "high"
    # "(2 cápsulas)" headers without a preposition.
    window = text[max(0, toks[i]["s"] - 200):toks[i]["s"]]
    ms = list(re.finditer(rf"\(\s*(\d{{1,2}})\s*({UNIT_WORDS}|ml\b)\s*\)", window, re.I))
    if ms:
        m = ms[-1]
        return num(m.group(1)), unit_kind(m.group(2)), m.group(0), "high"
    if re.search(r"dosis\s+diaria|dose\s+journali|daily\s+dose|al\s+d[ií]a|par\s+jour|por\s+d[ií]a|"
                 r"apport\s+quotidien|cantidad\s+diaria|ingesta\s+diaria|dose\s+quotidienne",
                 window[-160:], re.I):
        return None, None, "daily", "daily"
    return None, None, None, None


def _value_after(toks, i, text):
    """Amount directly after label i, with only non-numeric text (no other label) between."""
    if i + 1 < len(toks) and toks[i + 1]["k"] == "AMT":
        gap = text[toks[i]["e"]:toks[i + 1]["s"]]
        if len(gap) <= 45 and not re.search(r"\d|[.;](?:\s|$)", gap):
            return toks[i + 1]
    return None


def _value_before(toks, i, text):
    if i >= 1 and toks[i - 1]["k"] == "AMT":
        gap = text[toks[i - 1]["e"]:toks[i]["s"]]
        if GENITIVE.match(gap):
            strong = bool(gap.strip())
            return toks[i - 1], strong
    return None, False


def epa_dha(text, name=""):
    """All plausible EPA/DHA readings in document order, best first."""
    t = (text or "").replace("\u00a0", " ").replace("\u202f", " ")
    t = re.sub(r"[ \t]+", " ", t)
    t = focus(t)
    toks = tokens(t)
    vals = []  # (label, token index, amount token, layout)
    used_amounts = set()
    i = 0
    while i < len(toks):
        tk = toks[i]
        if tk["k"] not in LABELS:
            i += 1
            continue
        # Column layout: a run of labels followed by a run of amounts.
        if i + 1 < len(toks) and toks[i + 1]["k"] in LABELS:
            run = [i]
            j = i + 1
            while j < len(toks) and toks[j]["k"] in LABELS and toks[j]["s"] - toks[j - 1]["e"] < 60:
                run.append(j)
                j += 1
            amts = []
            while j < len(toks) and toks[j]["k"] == "AMT" and (not amts or toks[j]["s"] - amts[-1]["e"] < 40):
                amts.append(toks[j])
                j += 1
            kinds = [toks[r]["k"] for r in run]
            if amts and len(amts) >= len(run) and any(k in ("E", "D", "ED", "DE") for k in kinds):
                mg_amts = [a for a in amts]
                for pos, r in enumerate(run):
                    if toks[r]["k"] in ("E", "D", "ED", "DE") and pos < len(mg_amts):
                        vals.append((toks[r]["k"], r, mg_amts[pos], "column"))
                i = j
                continue
            # Not a usable column: fall through for the last label of the run.
            i = run[-1]
            tk = toks[i]
            if tk["k"] not in ("E", "D", "ED", "DE"):
                i += 1
                continue
        if tk["k"] in ("E", "D", "ED", "DE"):
            before, strong = _value_before(toks, i, t)
            after = _value_after(toks, i, t)
            pick = None
            after_gap = t[tk["e"]:after["s"]] if after is not None else ""
            if before is not None and strong and id(before) not in used_amounts:
                pick, layout = before, "reverse"
            elif before is not None and after is not None and re.search(
                    r"[,;·+/]|\b(?:y|et|and|e|und)\b", after_gap) and id(before) not in used_amounts:
                # "3840mg DHA TG, 240mg EPA": the amount after the comma belongs to the next item.
                pick, layout = before, "reverse"
            elif after is not None:
                pick, layout = after, "row"
            elif before is not None and id(before) not in used_amounts:
                pick, layout = before, "reverse"
            if pick is not None:
                used_amounts.add(id(pick))
                vals.append((tk["k"], i, pick, layout))
        i += 1
    readings = []
    for a in range(len(vals)):
        la, ia, ta, lay = vals[a]
        if la in ("ED", "DE") and ta["u"] in ("mg", "g"):
            # "Huile de poisson riche en (EPA et DHA) 2708 mg" is the oil weight, not EPA+DHA.
            if any(toks[j]["k"] == "OIL" and toks[ia]["s"] - toks[j]["e"] < 90
                   for j in range(max(0, ia - 4), ia)):
                continue
            total = ta["v"] * (1000 if ta["u"] == "g" else 1)
            readings.append({"epa": None, "dha": None, "total": total, "unit_kind": "mg",
                             "i": ia, "amount": dict(ta, e=max(ta["e"], toks[ia]["e"])), "layout": lay})
            continue
        if la != "E":
            continue
        best = None
        for b in range(len(vals)):
            lb, ib, tb, layb = vals[b]
            if lb != "D" or tb["u"] != ta["u"] or tb is ta:
                continue
            dist = abs(tb["s"] - ta["s"])
            if dist > 260:
                continue
            if best is None or dist < best[0]:
                best = (dist, ib, tb)
        if best:
            mult = 1000 if ta["u"] == "g" else 1
            last = ta if ta["e"] >= best[2]["e"] else best[2]
            tail_end = max(last["e"], toks[ia]["e"], toks[best[1]]["e"])
            readings.append({"epa": ta["v"] * mult, "dha": best[2]["v"] * mult, "total": None,
                             "unit_kind": "%" if ta["u"] == "%" else "mg", "i": min(ia, best[1]),
                             "amount": dict(last, e=tail_end), "layout": lay})
    out = []
    for r in readings:
        if r["unit_kind"] == "mg":
            tot = r["total"] if r["total"] is not None else r["epa"] + r["dha"]
            if tot <= 0:
                continue
        per, unit, basis, conf = _basis_of(t, toks, r["i"], r["amount"])
        s = toks[r["i"]]["s"]
        oil = None
        for j in range(r["i"] - 1, max(-1, r["i"] - 8), -1):
            if toks[j]["k"] == "OIL" and re.match(r"(huile|aceite|fish|krill|algal)", t[toks[j]["s"]:toks[j]["e"]], re.I):
                for jj in range(j + 1, min(len(toks), j + 4)):
                    a = toks[jj]
                    if a["k"] == "AMT" and a["u"] == "mg" and a["s"] - toks[j]["e"] < 70:
                        own = a["v"] in (r["epa"], r["dha"], r["total"])
                        if 200 <= a["v"] <= 8000 and not own and "." not in t[toks[j]["e"]:a["s"]]:
                            oil = a["v"]
                        break
                break
        out.append({
            "epa": r["epa"], "dha": r["dha"], "total": r["total"], "kind": r["unit_kind"],
            "per": per, "unit": unit, "basis": basis, "confidence": conf, "layout": r["layout"],
            "oil": oil,
            "snippet": t[max(0, s - 140):s + 160].strip(),
        })
    # Explicit-basis readings first, then document order.
    # Explicit EPA and DHA pairs beat combined "EPA+DHA" totals; percentages come last.
    out.sort(key=lambda r: (r["kind"] == "%", r["epa"] is None, r["confidence"] != "high",
                            r["confidence"] is None))
    if not out and name:
        ne = re.search(rf"\bEPA\s*({AMT_NUM})\s*mg", name, re.I)
        nd = re.search(rf"\bDHA\s*({AMT_NUM})\s*mg", name, re.I)
        if ne and nd:
            out.append({"epa": num(ne.group(1)), "dha": num(nd.group(1)), "total": None, "kind": "mg",
                        "per": None, "unit": None, "basis": None, "confidence": None,
                        "layout": "name", "snippet": name})
    return out


KINDS = [
    ("krill", r"krill"),
    ("algae", r"\balga|\balgue|algas\b|algal|schizochytrium|microalg|vegan|v[ée]g[ée]tal|vegetal"),
    ("cod_liver", r"foie de morue|h[ií]gado de bacalao|cod liver|lebertran|morue"),
    ("calamari", r"calamar|squid"),
    ("blend", r"3[\s\-/]*6[\s\-/]*9|om[eé]ga[\s\-]*3[\s\-]*6"),
]


def kind_of(name, text=""):
    n = (name or "").lower()
    for k, pat in KINDS:
        if re.search(pat, n, re.I):
            return k
    t = (text or "")[:1500].lower()
    if re.search(r"schizochytrium|aceite de algas|huile d['’]algue|algal oil", t):
        return "algae"
    if re.search(r"huile de krill|aceite de krill|krill oil", t):
        return "krill"
    return "fish"


def form_of(text):
    t = text or ""
    if re.search(r"\brTG\b|re-?est[ée]rifi|reesterific|r[ée]-?est[ée]rifi", t, re.I):
        return "rTG"
    if re.search(r"[ée]thyl[\s-]*ester|ester[s]?\s+[ée]thyl|[ée]steres?\s+et[ií]lic|\bEE\b", t, re.I):
        return "EE"
    if re.search(r"phospholipid|fosfol[ií]pid", t, re.I):
        return "PL"
    if re.search(r"triglyc[ée]rid|triglic[ée]rid|\bTG\b", t, re.I):
        return "TG"
    return None


CERTS = [
    ("IFOS", r"\bIFOS\b"),
    ("Friend of the Sea", r"friends?\s+of\s+the\s+sea"),
    ("MSC", r"\bMSC\b"),
    ("Epax", r"\bEPAX\b"),
    ("Omegavie", r"omegavie"),
    ("AlaskOmega", r"alaskomega"),
    ("MEG-3", r"MEG-3"),
    ("KD-Pur", r"KD-?Pur"),
]


def certs_of(text):
    return [c for c, pat in CERTS if re.search(pat, text or "", re.I)]


def totox_claim(text):
    m = re.search(rf"TOTOX[^\d<≤]{{0,40}}([<≤]|inf[ée]rieure?\s+[àa]|menor\s+(?:de|a)|"
                  rf"below|under|less than)?\s*({NUM})", text or "", re.I)
    if not m:
        return None
    v = num(m.group(2))
    if 0 < v <= 26:
        return {"value": v, "upper_bound": bool(m.group(1))}
    return None


PLAUSIBLE = {"capsule": (25, 1100), "gummy": (15, 700), "ml": (40, 450)}
OIL_RE = re.compile(
    rf"(?:c[áa]psulas?|perlas?|capsules?|g[ée]lules?|softgels?|perles?)\s*(?:blandas?\s+)?(?:de|d['’]|of|:)\s*({NUM})\s*mg|"
    rf"({NUM})\s*mg\s*(?:par|por|per)\s+(?:c[áa]psula|perla|capsule|g[ée]lule|softgel)",
    re.I,
)
NAME_OIL_RE = re.compile(r"\b(500|600|700|750|800|1000|1\.000|1 000|1200|1250|1300|1400|1500)\s*mg\b", re.I)


def oil_per_unit(name, text):
    m = OIL_RE.search(text or "")
    if m:
        v = num(next(g for g in m.groups() if g))
        if 200 <= v <= 1600:
            return v
    m = NAME_OIL_RE.search(name or "")
    if m:
        return num(m.group(1))
    return None


def resolve(name, variant, text, pack=None):
    """Best EPA/DHA per single unit (capsule, ml or gummy) for an offer, or None."""
    readings = epa_dha(text, name)
    pack_unit = (pack or {}).get("unit")
    dose = daily_dose(text or "")
    oil = None
    for r in readings:
        if r["confidence"] == "per100g":
            continue
        e, d, tot = r["epa"], r["dha"], r["total"]
        method = r["layout"]
        if r["kind"] == "%":
            oil = oil or oil_per_unit(f"{name} {variant or ''}", text)
            if not oil or e + d > 95:
                continue
            e, d = e * oil / 100, d * oil / 100
            per, unit, conf, method = 1.0, pack_unit or "capsule", "medium", f"% of {oil:g} mg oil"
        else:
            per, unit, conf = r["per"], r["unit"], r["confidence"]
        if conf == "daily" or per is None:
            if dose and (not pack_unit or dose[1] == pack_unit):
                per, unit = dose[0], dose[1]
                conf = "medium" if conf == "daily" else "low"
                method += " /daily dose"
            else:
                per, unit, conf = 1.0, pack_unit or "capsule", "low"
        unit = unit or pack_unit or "capsule"
        if pack_unit and unit != pack_unit:
            if pack_unit == "gummy" and unit == "capsule":
                unit = "gummy"
            elif pack_unit == "capsule" and unit == "gummy":
                unit = "capsule"
            else:
                continue
        oil_label = r.get("oil")
        if oil_label and unit == "capsule" and oil_label / per > 1700:
            # "Huile de poisson 2 000 mg" cannot be one capsule: the table is per daily dose.
            if dose and dose[1] == "capsule" and oil_label / dose[0] <= 1700:
                per, conf = dose[0], "medium"
                method += " /oil-weight dose"
            else:
                continue
        total = (tot if tot is not None else e + d) / per
        if oil_label and total > 0.95 * oil_label / per:
            continue
        cap_weight = oil_per_unit(name, "") if unit == "capsule" else None
        if cap_weight and cap_weight <= 1500 and total > 0.92 * cap_weight:
            # "Omega 3 1000 mg" cannot hold 900+ mg EPA+DHA per capsule unless it says so per dose.
            if dose and dose[1] == unit and total / dose[0] <= 0.92 * cap_weight and conf != "high":
                per *= dose[0]
                total /= dose[0]
                method += " /capsule weight"
            else:
                continue
        lo, hi = PLAUSIBLE.get(unit, (25, 1400))
        if not lo <= total <= hi:
            if dose and conf == "low" and dose[1] == unit and lo <= total / dose[0] <= hi:
                per *= dose[0]
                total /= dose[0]
                conf = "medium"
                method += " /daily dose"
            else:
                continue
        return {
            "epa": round(e / per, 1) if e is not None else None,
            "dha": round(d / per, 1) if d is not None else None,
            "total": round(total, 1),
            "unit": unit,
            "per_label": per,
            "confidence": conf,
            "method": method,
            "snippet": r["snippet"][:400],
            "daily_dose": dose[0] if dose and dose[1] == unit else None,
        }
    return dha_only(name, text, pack_unit)


DHA_ONLY_RE = re.compile(
    rf"({NUM})\s*mg\s*(?:de\s+|d['’]\s*|of\s+)?DHA[^.\d]{{0,25}}?(?:por|par|per|pour|cada|each|chaque|/)\s*"
    rf"({UNIT_WORDS})|(?:{UNIT_WORDS})\s*=\s*({NUM})\s*mg\s*(?:de\s+)?DHA|DHA\s*:?\s*({NUM})\s*mg\s*"
    rf"(?:por|par|per|pour|/)\s*({UNIT_WORDS})",
    re.I,
)


def dha_only(name, text, pack_unit):
    """Algae/DHA products that state only DHA per capsule ("250 mg de DHA por perla")."""
    if not re.search(r"\bDHA\b", name or "", re.I) or re.search(r"\bEPA\b", name or "", re.I):
        return None
    m = DHA_ONLY_RE.search(focus(text or ""))
    if not m:
        return None
    v = num(m.group(1) or m.group(3) or m.group(4))
    lo, hi = PLAUSIBLE["capsule"]
    if not lo <= v <= hi or (pack_unit and pack_unit != "capsule"):
        return None
    return {"epa": 0.0, "dha": v, "total": v, "unit": "capsule", "per_label": 1.0,
            "confidence": "medium", "method": "DHA only", "snippet": m.group(0), "daily_dose": None}


COMMON_PACKS = {30, 40, 45, 50, 60, 70, 80, 90, 100, 112, 120, 150, 160, 180, 200, 220, 240, 250, 270,
                300, 360, 400, 500, 600}


def pack_from_text(text, variant=None):
    """Most frequent common pack size mentioned in the page text, times a "x2 flacons" multiplier."""
    counts = Counter()
    for m in re.finditer(rf"\b(\d{{2,3}})\s*({UNIT_WORDS})", text or "", re.I):
        n = int(m.group(1))
        if n in COMMON_PACKS:
            counts[(n, unit_kind(m.group(2)))] += 1
    if not counts:
        return None
    (n, unit), hits = counts.most_common(1)[0]
    if hits < 2 and len(counts) > 1:
        return None
    mult = 1
    mm = re.search(r"(?:x\s*(\d)|(\d)\s*(?:x|flacons|botes|frascos|bo[iî]tes|pots|envases))", variant or "", re.I)
    if mm:
        mult = int(mm.group(1) or mm.group(2))
    return {"count": n * mult, "unit": unit, "multi": mult, "from_text": True}
