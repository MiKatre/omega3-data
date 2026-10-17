"""Markdown run summary for the GitHub Actions job page."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    data = json.loads((ROOT / "data" / "latest.json").read_text())
    st = data["stats"]
    print("## Omega-3 collection\n")
    print(f"{st.get('offers', 0)} offers → {st.get('products', 0)} products, "
          f"{st.get('comparable', 0)} with € per gram, {st.get('ifos', 0)} matched to IFOS lots, "
          f"{st.get('site_products', 0)} on the site.\n")
    print("| Source | Status | Offers | Comparable | Carried over |")
    print("|---|---|---:|---:|---:|")
    for s in sorted(data["sources"], key=lambda s: -s["comparable"]):
        print(f"| {s['id']} | {s['status'][:60]} | {s['offers']} | {s['comparable']} | "
              f"{s.get('carried', 0)} |")


if __name__ == "__main__":
    main()
