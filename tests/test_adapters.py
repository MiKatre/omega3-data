import json
from types import SimpleNamespace

import pytest

from omega import adapters
from omega.http import Blocked, Http


class FakeHttp:
    def __init__(self, responses):
        self.responses = responses

    def get(self, url, *, accept=None, retries=2, binary=False, allow_404=False):
        if url not in self.responses:
            if allow_404:
                return None
            raise AssertionError(f"unexpected url: {url}")
        val = self.responses[url]
        return json.dumps(val) if not isinstance(val, str) else val


def test_woo_matches_short_description_and_expands_variations():
    base = "https://www.nutergia.es"
    http = FakeHttp({
        f"{base}/wp-json/wc/store/v1/products?per_page=100&page=1": [
            {
                "id": 14056,
                "name": "ERGY 3",
                "slug": "ergy-3",
                "permalink": f"{base}/producto/ergy-3/",
                "short_description": "<p>Aceite de pescado salvaje. Omega 3</p>",
                "description": "<p>Aporta omega-3, EPA y DHA</p>",
                "categories": [{"name": "Circulación"}],
                "prices": {"price": "1900", "currency_minor_unit": 2, "currency_code": "EUR"},
                "is_in_stock": True,
                "images": [{"src": f"{base}/img/ergy3.jpg"}],
                "variations": [{"id": 19880}, {"id": 19879}],
            },
            {
                "id": 999,
                "name": "Huile vierge de Cameline",
                "slug": "huile-cameline",
                "permalink": f"{base}/producto/cameline/",
                "short_description": "<p>Riche en oméga-3 ALA</p>",
                "description": "",
                "categories": [],
                "prices": {"price": "1500", "currency_minor_unit": 2, "currency_code": "EUR"},
                "is_in_stock": True,
                "variations": [],
            },
        ],
        f"{base}/wp-json/wc/store/v1/products?per_page=100&page=2": [],
        f"{base}/wp-json/wc/store/v1/products/19880": {
            "id": 19880,
            "variation": "presentacion: Bote de 60 perlas",
            "permalink": f"{base}/producto/ergy-3/?attribute_pa_presentacion=bote-de-60-perlas",
            "sku": "ERGY-3 60",
            "prices": {"price": "1900", "currency_minor_unit": 2, "currency_code": "EUR"},
            "is_in_stock": True,
            "images": [],
        },
        f"{base}/wp-json/wc/store/v1/products/19879": {
            "id": 19879,
            "variation": "presentacion: Bote de 180 perlas",
            "permalink": f"{base}/producto/ergy-3/?attribute_pa_presentacion=bote-de-180-perlas",
            "sku": "ERGY-3 180",
            "prices": {"price": "4150", "currency_minor_unit": 2, "currency_code": "EUR"},
            "is_in_stock": True,
            "images": [],
        },
    })

    offers = list(adapters.woo({"id": "nutergia-es", "base": base}, http, lambda *a: None))
    assert len(offers) == 2
    assert [(o["product_name"], o["variant_name"], o["price"]) for o in offers] == [
        ("ERGY 3", "Bote de 60 perlas", 19.0),
        ("ERGY 3", "Bote de 180 perlas", 41.5),
    ]
    assert offers[0]["image"] == f"{base}/img/ergy3.jpg"


def test_http_respects_retry_after_on_429(monkeypatch):
    sleeps = []
    monkeypatch.setattr("omega.http.time.sleep", sleeps.append)

    class FakeSession:
        def __init__(self):
            self.calls = 0

        def get(self, url, headers=None, timeout=None, allow_redirects=True):
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace(status_code=429, content=b"", headers={"Retry-After": "17"}, encoding="utf-8")
            return SimpleNamespace(status_code=200, content=b'{"products":[]}', headers={}, encoding="utf-8")

    h = Http(delay=0)
    h.sessions["shop.test"] = FakeSession()
    import threading
    h.locks["shop.test"] = threading.Lock()
    h.stats["shop.test"] = {"ok": 0, "blocked": 0, "error": 0}

    assert h.get("https://shop.test/products.json") == '{"products":[]}'
    assert 17 in sleeps


def test_http_caps_retry_after_and_raises_blocked(monkeypatch):
    sleeps = []
    monkeypatch.setattr("omega.http.time.sleep", sleeps.append)

    class Always429:
        def get(self, url, headers=None, timeout=None, allow_redirects=True):
            return SimpleNamespace(status_code=429, content=b"", headers={"Retry-After": "999"}, encoding="utf-8")

    h = Http(delay=0)
    h.sessions["shop.test"] = Always429()
    import threading
    h.locks["shop.test"] = threading.Lock()
    h.stats["shop.test"] = {"ok": 0, "blocked": 0, "error": 0}

    with pytest.raises(Blocked):
        h.get("https://shop.test/products.json", retries=1)
    assert sleeps == [60, 60]
