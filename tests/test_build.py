import json
from datetime import datetime, timezone

from omega import build

COMP = {"epa": 400, "dha": 200, "total": 600, "unit": "capsule", "confidence": "high", "method": "table"}


def offer(source, **kw):
    return {"shop": f"Shop {source}", "source": source, "country": "ES", "url": f"https://{source}.test/p",
            "price": 10, "count": 60, "unit": "capsule", "available": True, "fetched_at": None, **kw}


def product(comp, offers):
    return {"id": "g1", "name": "Brand Omega 3", "brand": "Brand", "kind": "fish", "form": None,
            "certs": [], "gtin": "1", "totox_claim": None, "ifos": None, "comp": comp, "offers": offers}


def site_input(products):
    return {"generated_at": "now", "stats": {}, "products": products}


def test_carry_over_keeps_composition_for_priced_offers(tmp_path, monkeypatch):
    # The failed source's page held the composition; this run only a comp-less listing remains.
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    (tmp_path / "data").mkdir()
    prev = {"products": [product(COMP, [offer("a", eur_per_g=0.5, fetched_at=now)])]}
    (tmp_path / "data" / "latest.json").write_text(json.dumps(prev))
    monkeypatch.setattr(build, "ROOT", tmp_path)
    products = [product(None, [offer("b")])]
    report = {"sources": [{"id": "a", "offers": 0}, {"id": "b", "offers": 1}]}

    assert build.carry_over(products, report) == {"a": 1}
    assert products[0]["comp"] == COMP
    assert products[0]["best_eur_per_g"] == 0.5
    assert build.export_site(site_input(products)) == 1


def test_export_site_skips_priced_product_without_composition(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(build, "ROOT", tmp_path)
    assert build.export_site(site_input([product(None, [offer("a", eur_per_g=0.5)])])) == 0
    assert "g1" in capsys.readouterr().err
