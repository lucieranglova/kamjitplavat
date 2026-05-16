"""
scrapers/sutka.py
Scrapes lane schedule from sutka.eu
Runs nightly via GitHub Actions, outputs to data/lanes.json
"""

import json
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("ERROR: install requests and beautifulsoup4", file=sys.stderr)
    sys.exit(1)

PRAGUE_TZ = timezone(timedelta(hours=2))
LANES_URL = "https://www.sutka.eu/rezervace"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; BazenyPrahaBot/1.0; +https://github.com/your-repo)"
}

TYPE_MAP = {
    "volné": "volno", "volno": "volno", "veřejnost": "volno", "public": "volno",
    "klub": "klub", "trénink": "klub", "rezerv": "klub",
    "kurz": "kurzy", "lekce": "kurzy",
}

def normalize_type(raw: str) -> str:
    raw = raw.lower().strip()
    for key, val in TYPE_MAP.items():
        if key in raw:
            return val
    return "unknown"


def scrape_sutka() -> dict:
    result = {
        "50m": {
            "name": "50m bazén",
            "schedule": []
        }
    }

    try:
        resp = requests.get(LANES_URL, headers=HEADERS, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[Šutka] HTTP error: {e}", file=sys.stderr)
        return result

    soup = BeautifulSoup(resp.text, "html.parser")
    time_pattern = re.compile(r"\d{1,2}:\d{2}\s*[-–]\s*\d{1,2}:\d{2}")
    schedule = []

    for elem in soup.find_all(string=time_pattern):
        match = time_pattern.search(elem)
        if not match:
            continue
        time_str = match.group().replace("-", "–").replace(" ", "")
        # Try to get sibling or parent text for type
        parent = elem.parent
        parent_text = parent.get_text(separator=" ", strip=True) if parent else ""
        type_str = normalize_type(parent_text)
        note_str = parent_text.replace(match.group(), "").strip()[:80]

        schedule.append({
            "time": time_str,
            "type": type_str,
            "note": note_str
        })

    if schedule:
        result["50m"]["schedule"] = schedule
        print(f"[Šutka] Scraped {len(schedule)} blocks")
    else:
        print("[Šutka] No data found, using fallback", file=sys.stderr)
        result["50m"]["schedule"] = [
            {"time": "6:00–8:00",   "type": "volno", "note": "Ranní plavání"},
            {"time": "8:00–10:00",  "type": "klub",  "note": "Rezervováno klubem"},
            {"time": "10:00–22:00", "type": "volno", "note": "Volné plavání (část drah)"},
        ]

    return result


def main():
    lanes_path = Path(__file__).parent.parent / "data" / "lanes.json"

    if lanes_path.exists():
        with open(lanes_path, "r", encoding="utf-8") as f:
            existing = json.load(f)
    else:
        existing = {"updated_at": "", "pools": {}}

    print("Scraping Šutka…")
    existing.setdefault("pools", {})
    existing["pools"]["sutka"] = scrape_sutka()
    existing["updated_at"] = datetime.now(PRAGUE_TZ).isoformat()

    with open(lanes_path, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)

    print(f"[Šutka] Done.")


if __name__ == "__main__":
    main()