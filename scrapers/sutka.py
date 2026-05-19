"""
scrapers/sutka.py - debug version to understand structure
"""
import json, re, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    sys.exit(1)

PRAGUE_TZ   = timezone(timedelta(hours=2))
URL         = "https://www.sutka.eu/course/"
TOTAL_LANES = 8
SLOT_MIN    = 15
START_HOUR  = 6
END_HOUR    = 22
DAY_KEYS    = ["po","ut","st","ct","pa","so","ne"]
CZ_DAYS     = {
    "po":"po","pon":"po","pondělí":"po","pondeli":"po",
    "út":"ut","ut":"ut","úterý":"ut","utery":"ut",
    "st":"st","stř":"st","středa":"st","streda":"st",
    "čt":"ct","ct":"ct","čtvrtek":"ct","ctvrtek":"ct",
    "pá":"pa","pa":"pa","pátek":"pa","patek":"pa",
    "so":"so","sobota":"so",
    "ne":"ne","neděle":"ne","nedele":"ne",
}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "cs-CZ,cs;q=0.9",
}
TOTAL_SLOTS = (END_HOUR - START_HOUR) * (60 // SLOT_MIN)


def fetch():
    try:
        r = requests.get(URL, headers=HEADERS, timeout=15)
        r.raise_for_status()
        return r.text
    except Exception as e:
        print(f"[Šutka] HTTP error: {e}", file=sys.stderr)
        return None


def detect_day(text):
    for word in re.split(r"[\s\xa0\n]+", text.lower()):
        word = re.sub(r"[^\w]", "", word)
        if word in CZ_DAYS:
            return CZ_DAYS[word]
    return None


def slot_reserved(td):
    """Slot is reserved if td has class 'active-field' or contains <a>."""
    classes = td.get("class", [])
    return "active-field" in classes or bool(td.find("a"))


def parse(html):
    soup = BeautifulSoup(html, "html.parser")

    # Page has multiple tables — one per day
    tables = soup.find_all("table")
    print(f"[Šutka] Found {len(tables)} tables")

    # Day labels are in elements just before each table
    # Find all elements with day text
    result = {}

    for table in tables:
        # Find day label in previous siblings
        day = None
        for sib in table.previous_siblings:
            if not hasattr(sib, 'get_text'):
                continue
            text = sib.get_text(separator=" ", strip=True)
            if text:
                day = detect_day(text)
                print(f"[Šutka debug] Before table, text='{text[:40]}', day={day}")
                if day:
                    break

        if not day:
            # Try parent's previous siblings
            for sib in table.parent.previous_siblings:
                if not hasattr(sib, 'get_text'):
                    continue
                text = sib.get_text(separator=" ", strip=True)
                if text:
                    day = detect_day(text)
                    if day:
                        break

        if not day:
            # Try heading elements anywhere near table
            heading = table.find_previous(["h1","h2","h3","h4","div","span","p"])
            if heading:
                text = heading.get_text(strip=True)
                day = detect_day(text)
                print(f"[Šutka debug] Heading: '{text[:40]}', day={day}")

        rows = table.find_all("tr")
        print(f"[Šutka debug] Table day={day}, rows={len(rows)}, first row cells={len(rows[0].find_all(['td','th'])) if rows else 0}")
        if rows:
            # Show first row cell details
            for i, td in enumerate(rows[0].find_all(["td","th"])[:5]):
                print(f"[Šutka debug]   header cell {i}: text='{td.get_text(strip=True)[:20]}' class={td.get('class',[])} title='{td.get('title','')[:20]}'")

    return {}


def main():
    lanes_path = Path(__file__).parent.parent / "data" / "lanes.json"
    print("[Šutka] Fetching…")
    html = fetch()
    if html:
        parse(html)
    print("[Šutka] Debug done — no data written")


if __name__ == "__main__":
    main()
