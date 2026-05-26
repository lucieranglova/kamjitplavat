"""
Scraper pro Koupaliště Petynka – koupalistepetynka.cz
Stahuje: teplotu vody, teplotu vzduchu, počet návštěvníků, volná parkovací místa.
Výstup: data/petynka_live.json
"""

import json, re, sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("Chybí knihovny: pip install requests beautifulsoup4")
    sys.exit(1)

URL = "https://koupalistepetynka.cz/"
OUTPUT = Path(__file__).parent.parent / "data" / "petynka_live.json"


def scrape() -> dict:
    try:
        r = requests.get(URL, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (compatible; PrahaBazenyBot/1.0)"
        })
        r.raise_for_status()
    except Exception as e:
        print(f"[petynka] Chyba při stahování: {e}")
        return {}

    soup = BeautifulSoup(r.text, "html.parser")
    text = soup.get_text(" ", strip=True)

    data = {}

    # ── Teplota vody ──────────────────────────────────────────────────────────
    # HTML obsahuje: "26 °C TEPLOTA VODY" nebo varianty
    m = re.search(r'(\d+)\s*°C\s*TEPLOTA VODY', text, re.IGNORECASE)
    if not m:
        # Záloha: najdi číslo před "TEPLOTA VODY"
        m = re.search(r'(\d+)[^\n]{0,10}TEPLOTA VODY', text, re.IGNORECASE)
    if m:
        data["water_temp"] = int(m.group(1))

    # ── Teplota vzduchu ───────────────────────────────────────────────────────
    m = re.search(r'(\d+)\s*°C\s*TEPLOTA VZDUCHU', text, re.IGNORECASE)
    if not m:
        m = re.search(r'(\d+)[^\n]{0,10}TEPLOTA VZDUCHU', text, re.IGNORECASE)
    if m:
        data["air_temp"] = int(m.group(1))

    # ── Počet návštěvníků ─────────────────────────────────────────────────────
    # "1118 návštěvníků (max 1300)"
    m = re.search(r'(\d+)\s*návštěvníků\s*\(\s*max\s*(\d+)\s*\)', text, re.IGNORECASE)
    if m:
        data["visitors"] = int(m.group(1))
        data["visitors_max"] = int(m.group(2))

    # ── Volná parkovací místa ─────────────────────────────────────────────────
    # "0 PARKOVACÍCH MÍST"
    m = re.search(r'(\d+)\s*PARKOVAC[IÍ]+CH\s*M[IÍ]+ST', text, re.IGNORECASE)
    if m:
        data["parking_free"] = int(m.group(1))

    data["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    data["open"] = bool(data)  # True pokud jsme cokoliv načetli

    return data


def main():
    print("[petynka] Stahuji data...")
    data = scrape()

    if data:
        print(f"[petynka] ✓ voda={data.get('water_temp')}°C, "
              f"vzduch={data.get('air_temp')}°C, "
              f"návštěvníci={data.get('visitors')}/{data.get('visitors_max')}, "
              f"parking={data.get('parking_free')}")
    else:
        print("[petynka] ✗ Žádná data (koupaliště je zřejmě zavřeno nebo mimo sezónu)")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[petynka] Uloženo → {OUTPUT}")


if __name__ == "__main__":
    main()