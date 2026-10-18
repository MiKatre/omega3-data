"""Write a single-file copy of the site with data.json inlined (for sharing without a server).

Usage: python -m omega.embed OUTPUT.html [--fragment]
--fragment drops <!doctype>/<html>/<head>/<body> wrappers for hosts that add their own.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    out = Path(sys.argv[1])
    fragment = "--fragment" in sys.argv
    html = (ROOT / "site" / "index.html").read_text()
    data = (ROOT / "site" / "data.json").read_text()
    payload = data.replace("</", "<\\/")
    script = f'<script id="data" type="application/json">{payload}</script>\n'
    html = html.replace("<script>\nconst KIND", script + "<script>\nconst KIND", 1)
    if fragment:
        head = re.search(r"<head>(.*?)</head>", html, re.S).group(1)
        body = re.search(r"<body>(.*?)</body>", html, re.S).group(1)
        head = re.sub(r'<meta charset[^>]*>\s*|<meta name="viewport"[^>]*>\s*', "", head)
        html = head.strip() + "\n" + body.strip() + "\n"
    out.write_text(html)
    n = len(json.loads(data)["products"])
    print(f"wrote {out} ({len(html) // 1024} KB, {n} products)")


if __name__ == "__main__":
    main()
