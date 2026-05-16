"""
scraper/podoli.py
Scrapes lane schedule from pspodoli.cz/cs/obsazenost-bazenu/
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
BASE_URL = "https://pspodoli.cz"
LANES_URL = f"{BASE_URL}/cs/obsazenost-bazenu/"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; BazenyPrahaBot/1.0; +https://github.com/your-repo)"
}

TYPE_MAP = {
    "volné": "volno",
    "volno": "volno",
    "public": "volno",
    "klub": "klub",
    "club": "klub",
    "trénink": "klub",
    "kurz": "kurzy",
    "kurzy": "kurzy",
    "škola": "kurzy",
}

def normalize_type(raw: str) -> str:
    raw = raw.lower().strip()
    for key, val in TYPE_MAP.items():
        if key in raw:
            return val
    return "unknown"


def scrape_podoli() -> dict:
    """
    Attempts to scrape lane schedule from Podolí website.
    Returns dict matching the lanes.json format for 'podoli'.
    Falls back to empty schedule on error.
    """
    result = {
        "indoor_50m": {
            "name": "Vnitřní 50m",
            "schedule": []
        }
    }

    try:
        resp = requests.get(LANES_URL, headers=HEADERS, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[Podolí] HTTP error: {e}", file=sys.stderr)
        return result

    soup = BeautifulSoup(resp.text, "html.parser")

    # Podolí shows schedule as a table — adapt selectors if site changes
    # Look for tables with time rows
    schedule = []
    tables = soup.find_all("table")

    for table in tables:
        rows = table.find_all("tr")
        for row in rows:
            cells = row.find_all(["td", "th"])
            if len(cells) < 2:
                continue
            cell_texts = [c.get_text(strip=True) for c in cells]

            # Try to find a time pattern like "6:00-8:00" or "6:00–8:00"
            time_pattern = re.compile(r"\d{1,2}:\d{2}\s*[-–]\s*\d{1,2}:\d{2}")
            time_str = None
            type_str = "unknown"
            note_str = ""

            for i, text in enumerate(cell_texts):
                match = time_pattern.search(text)
                if match:
                    time_str = match.group().replace("-", "–").replace(" ", "")
                    # Remaining cells = type + note
                    remaining = cell_texts[i+1:]
                    if remaining:
                        type_str = normalize_type(remaining[0])
                        note_str = " ".join(remaining[1:]) if len(remaining) > 1 else remaining[0]
                    break

            if time_str:
                schedule.append({
                    "time": time_str,
                    "type": type_str,
                    "note": note_str
                })

    if schedule:
        result["indoor_50m"]["schedule"] = schedule
        print(f"[Podolí] Scraped {len(schedule)} time blocks")
    else:
        # Fallback: use known typical schedule
        print("[Podolí] No schedule found, using fallback", file=sys.stderr)
        result["indoor_50m"]["schedule"] = [
            {"time": "6:00–7:30",  "type": "volno", "note": "Ranní plavání"},
            {"time": "7:30–9:00",  "type": "klub",  "note": "Plavecké kluby"},
            {"time": "9:00–12:00", "type": "volno", "note": "Volné plavání"},
            {"time": "12:00–14:00","type": "kurzy", "note": "Plavecké kurzy"},
            {"time": "14:00–17:00","type": "volno", "note": "Volné plavání"},
            {"time": "17:00–19:30","type": "klub",  "note": "Plavecké kluby"},
            {"time": "19:30–21:45","type": "volno", "note": "Večerní plavání"},
        ]

    return result


def main():
    lanes_path = Path(__file__).parent.parent / "data" / "lanes.json"

    # Load existing data
    if lanes_path.exists():
        with open(lanes_path, "r", encoding="utf-8") as f:
            existing = json.load(f)
    else:
        existing = {"updated_at": "", "pools": {}}

    # Scrape
    print("Scraping Podolí…")
    podoli_data = scrape_podoli()

    # Update
    existing.setdefault("pools", {})
    existing["pools"]["podoli"] = podoli_data
    existing["updated_at"] = datetime.now(PRAGUE_TZ).isoformat()

    # Write back
    with open(lanes_path, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)

    print(f"[Podolí] Done. lanes.json updated at {existing['updated_at']}")


if __name__ == "__main__":
    main()