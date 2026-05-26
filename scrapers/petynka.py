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

    data = {}

    # Stránka používá strukturu: číslo v <h2> nebo velkém tagu,
    # popis hned za ním jako text nebo v dalším elementu.
    # Získáme celý text ale zachováme newlines pro správné párování.
    full_text = soup.get_text("\n", strip=True)

    # ── Teplota vody ──────────────────────────────────────────────────────────
    # Hledáme číslo na řádku před "TEPLOTA VODY" (case-insensitive)
    m = re.search(r'(\d+)\s*°?\s*C?\s*\n+\s*TEPLOTA VODY', full_text, re.IGNORECASE)
    if not m:
        # Záloha: číslo a label na stejném řádku
        m = re.search(r'(\d+)\s*°C\s*TEPLOTA VODY', full_text, re.IGNORECASE)
    if not m:
        # Záloha 2: najdi "TEPLOTA VODY" a číslo kdekoliv blízko před ním
        m = re.search(r'(\d+)\b[^\n]{0,30}\n[^\n]{0,10}TEPLOTA VODY', full_text, re.IGNORECASE)
    if m:
        data["water_temp"] = int(m.group(1))

    # ── Teplota vzduchu ───────────────────────────────────────────────────────
    m = re.search(r'(\d+)\s*°?\s*C?\s*\n+\s*TEPLOTA VZDUCHU', full_text, re.IGNORECASE)
    if not m:
        m = re.search(r'(\d+)\s*°C\s*TEPLOTA VZDUCHU', full_text, re.IGNORECASE)
    if not m:
        m = re.search(r'(\d+)\b[^\n]{0,30}\n[^\n]{0,10}TEPLOTA VZDUCHU', full_text, re.IGNORECASE)
    if m:
        data["air_temp"] = int(m.group(1))

    # ── Počet návštěvníků ─────────────────────────────────────────────────────
    # Číslo na jednom řádku, "návštěvníků (max N)" na dalším
    m = re.search(
        r'(\d+)\s*\n+\s*návštěvníků\s*\(?\s*max\s*(\d+)\s*\)?',
        full_text, re.IGNORECASE
    )
    if not m:
        # Vše na jednom řádku
        m = re.search(r'(\d+)\s*návštěvníků\s*\(?\s*max\s*(\d+)\s*\)?', full_text, re.IGNORECASE)
    if m:
        data["visitors"] = int(m.group(1))
        data["visitors_max"] = int(m.group(2))

    # ── Volná parkovací místa ─────────────────────────────────────────────────
    # Číslo na řádku před "PARKOVACÍCH MÍST"
    m = re.search(r'(\d+)\s*\n+\s*PARKOVAC', full_text, re.IGNORECASE)
    if not m:
        m = re.search(r'(\d+)\s*PARKOVAC[IÍ]+CH\s*M[IÍ]+ST', full_text, re.IGNORECASE)
    if m:
        data["parking_free"] = int(m.group(1))

    # open = True pokud jsme načetli aspoň teplotu nebo návštěvníky
    found = any(k in data for k in ("water_temp", "air_temp", "visitors", "parking_free"))
    data["open"] = found
    data["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return data


def main():
    print("[petynka] Stahuji data...")
    data = scrape()

    if data.get("open"):
        print(f"[petynka] ✓ voda={data.get('water_temp')}°C "
              f"vzduch={data.get('air_temp')}°C "
              f"návštěvníci={data.get('visitors')}/{data.get('visitors_max')} "
              f"parking={data.get('parking_free')}")
    else:
        print("[petynka] ✗ Žádná data (koupaliště mimo sezónu nebo chyba)")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[petynka] Uloženo → {OUTPUT}")


if __name__ == "__main__":
    main()