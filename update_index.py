"""
update_index.py
---------------
Downloads constituents of a BIST index from KAP and writes them to
{index}.csv (e.g. xu100.csv or xu500.csv).

KAP embeds index data inside a <script> tag as a JavaScript string, so the
JSON arrives with escaped quotes. Each index entry looks like:

    {"code":"XU100","content":[
        {"stockCode":"AGHOL", ...},
        {"stockCode":"AKBNK", ...},
        ...
     ],...,"name":"BIST 100"}

We unescape the quotes, locate the entry for the requested index code,
and read out the `stockCode` values.

A Midas fallback is kept in case KAP is unreachable. It scrapes the
index-specific page on getmidas.com.

Setup:
    pip install requests

Usage:
    python update_index.py                      # XU100 (default) → xu100.csv
    python update_index.py -i xu500             # XU500           → xu500.csv
    python update_index.py --index xu500
    python update_index.py -i xu500 -s midas    # use Midas as fallback
    python update_index.py -o foo.csv           # custom output
"""

import argparse
import csv
import re
import sys
from pathlib import Path

import requests

URL_KAP = "https://kap.org.tr/tr/Endeksler"
HERE = Path(__file__).resolve().parent

# Per-index configuration. Add new indices here as needed.
INDICES = {
    "xu100": {
        "kap_code": "XU100",
        "midas_url": "https://www.getmidas.com/canli-borsa/xu100-bist-100-hisseleri",
        "default_output": HERE / "xu100.csv",
        "expected_count": 100,
    },
    "xu500": {
        "kap_code": "XU500",
        "midas_url": "https://www.getmidas.com/canli-borsa/bist-500-hisseleri",
        "default_output": HERE / "xu500.csv",
        "expected_count": 500,
    },
}

DEBUG_HTML = HERE / "kap_debug.html"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

ENTRY_RE = re.compile(r'"stockCode"\s*:\s*"([^"]+)"')

# Midas anchor format: <a href="/canli-borsa/akbnk-hisse/">AKBNK</a>
# Accept single or double quotes, with or without trailing slash.
MIDAS_HREF_RE = re.compile(r'/canli-borsa/([a-z0-9]{3,6})-hisse/?["\']')
TICKER_RE = re.compile(r"^[A-Z0-9]{3,6}$")


def _fetch(url: str, timeout: int = 30) -> str:
    resp = requests.get(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.text


def fetch_from_kap(kap_code: str) -> list[dict]:
    """Pull constituents of the given index code from KAP's /tr/Endeksler page."""
    print(f"GET {URL_KAP}  (index code: {kap_code})")
    raw = _fetch(URL_KAP)

    # JSON is inside a JS string literal — every " is \". Unescape first.
    html = raw.replace('\\"', '"')

    code_m = re.search(rf'"code"\s*:\s*"{kap_code}"', html)
    if not code_m:
        DEBUG_HTML.write_text(raw, encoding="utf-8")
        sys.exit(
            f"'{kap_code}' index not found in KAP response.\n"
            f"Raw HTML saved to: {DEBUG_HTML}\n"
            f"You can fall back to: --source midas"
        )

    content_m = re.search(
        r'"content"\s*:\s*\[([^\]]+)\]',
        html[code_m.end():],
    )
    if not content_m:
        DEBUG_HTML.write_text(raw, encoding="utf-8")
        sys.exit(
            f"Found {kap_code} but no 'content' array after it.\n"
            f"Raw HTML: {DEBUG_HTML}"
        )

    members: list[dict] = []
    seen: set[str] = set()
    for entry in ENTRY_RE.finditer(content_m.group(1)):
        code = entry.group(1).strip().upper()
        if not TICKER_RE.match(code) or code in seen:
            continue
        seen.add(code)
        members.append({
            "ticker": code,
            "yf_symbol": f"{code}.IS",
        })
    return members


def fetch_from_midas(midas_url: str) -> list[dict]:
    """Pull constituents from a Midas index page.

    Each row has an anchor like <a href="/canli-borsa/akbnk-hisse/">AKBNK</a>.
    A regex over hrefs is enough — no BeautifulSoup needed.
    """
    print(f"GET {midas_url}")
    html = _fetch(midas_url)

    members: list[dict] = []
    seen: set[str] = set()
    for m in MIDAS_HREF_RE.finditer(html):
        code = m.group(1).upper()
        if not TICKER_RE.match(code) or code in seen:
            continue
        seen.add(code)
        members.append({
            "ticker": code,
            "yf_symbol": f"{code}.IS",
        })

    if not members:
        debug = HERE / "midas_debug.html"
        debug.write_text(html, encoding="utf-8")
        print(
            f"No tickers matched. Raw HTML saved to: {debug}\n"
            f"Inspect it for /canli-borsa/...-hisse/ links and update the regex.",
            file=sys.stderr,
        )

    return members


def write_csv(members: list[dict], path: Path):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["ticker", "yf_symbol"])
        w.writeheader()
        for m in sorted(members, key=lambda x: x["ticker"]):
            w.writerow(m)


def main():
    ap = argparse.ArgumentParser(
        description="Download BIST index constituents from KAP (or Midas as fallback)."
    )
    ap.add_argument("-i", "--index", choices=list(INDICES.keys()), default="xu100",
                    help="Which BIST index to fetch. Default: xu100.")
    ap.add_argument("-s", "--source", choices=["kap", "midas"], default="kap",
                    help="Data source. Default is KAP; use 'midas' as fallback.")
    ap.add_argument("-o", "--output", type=Path, default=None,
                    help="Output CSV path. Default: {index}.csv next to this script.")
    args = ap.parse_args()

    cfg = INDICES[args.index]
    output_path = args.output or cfg["default_output"]

    if args.source == "kap":
        members = fetch_from_kap(cfg["kap_code"])
    else:
        members = fetch_from_midas(cfg["midas_url"])

    if not members:
        sys.exit(f"Source '{args.source}' returned no tickers for {args.index}.")

    write_csv(members, output_path)
    print(f"Wrote {len(members)} tickers to {output_path} "
          f"(index: {args.index}, source: {args.source})")

    expected = cfg["expected_count"]
    if len(members) < expected * 0.9:
        print(
            f"Warning: expected ~{expected} tickers, got {len(members)}. "
            f"Inspect the output, or try the other --source option.",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
