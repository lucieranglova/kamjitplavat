"""
scrapers/sutka.py - FULL debug dump
"""
import json, re, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    sys.exit(1)

PRAGUE_TZ = timezone(timedelta(hours=2))
URL = "https://www.sutka.eu/course/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "cs-CZ,cs;q=0.9",
}

def fetch():
    r = requests.get(URL, headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.text

def main():
    lanes_path = Path(__file__).parent.parent / "data" / "lanes.json"
    print("[Šutka] Fetching…")
    html = fetch()
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    rows = table.find_all("tr")
    print(f"[Šutka] {len(rows)} rows total")
    print("=== ALL ROWS, FIRST 4 CELLS EACH ===")
    for i, row in enumerate(rows):
        tds = row.find_all(["td","th"])
        cells = []
        for td in tds[:4]:
            text = td.get_text(separator="|", strip=True)
            cls  = " ".join(td.get("class", []))
            title = td.get("title","")
            rs   = td.get("rowspan","")
            cs   = td.get("colspan","")
            has_a = bool(td.find("a"))
            cells.append(f"[txt={repr(text)[:25]} cls={cls[:15]} title={title[:15]} rs={rs} cs={cs} a={has_a}]")
        print(f"  ROW {i:02d} ({len(tds)} cells): {' | '.join(cells)}")

if __name__ == "__main__":
    main()
