# omega3-data

Omega-3 supplements (fish oil, krill, algae, cod liver) sold in Spain and France, normalised to
**euros per gram of EPA+DHA**, with IFOS lab results (TOTOX, measured EPA/DHA, contaminants) where a
lot report matches the product.

- `catalog.json`: shops and how each is collected (Shopify feed, WooCommerce Store API, sitemap +
  product pages, or headless Chromium for shops that block plain clients).
- `omega/collect.py`: fetches offers into `.cache/raw/*.jsonl` (Chrome TLS fingerprint via curl_cffi;
  Playwright for `browser: true` sources).
- `omega/composition.py`: reads EPA/DHA per capsule/ml from label text (row tables, column tables,
  "per 4 capsules" headers, percentages of the capsule weight, daily-dose statements).
- `omega/ifos.py`: IFOS/IKOS/IAOS registry → latest lot report PDF per product → `data/ifos.json`.
- `omega/build.py`: groups offers into products (EAN when available), picks the best composition
  reading, matches IFOS lots by brand and label values, computes €/g → `data/latest.json` and the
  slim `site/data.json`.
- `overrides.json`: hand-reviewed composition, pack size or exclusions, keyed by EAN/URL/name.
- `site/index.html`: static page that reads `data.json`.

```sh
uv sync --all-groups
uv run pytest -q
uv run python -m omega.collect --no-browser        # HTTP sources
uv run playwright install chromium && uv run python -m omega.collect --browser-only
uv run python -m omega.ifos
uv run python -m omega.build
python3 -m http.server -d site 8000                # preview
```

`OMEGA_CACHE_AGE=86400` reuses pages fetched in the last day, so parser changes can be re-run
without refetching.

The **Collect omega-3 prices** workflow runs daily and on demand, commits `data/` and `site/data.json`,
and uploads raw offers as a 7-day artifact. Sources that fail keep their last offers for up to 7 days
(marked by their original `fetched_at`).

Only offers whose EPA/DHA reading is high or medium confidence (or reviewed) get a € per gram.
Shipping costs and promotions are not included.
