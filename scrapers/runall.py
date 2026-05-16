"""
scrapers/run_all.py
Master runner — spustí všechny scrapers a zapíše výsledek do data/lanes.json
Spouštěn přes GitHub Actions každou noc.
"""

import subprocess
import sys
from pathlib import Path

SCRAPERS = [
    "podoli.py",
    "sutka.py",
]

def main():
    scraper_dir = Path(__file__).parent
    errors = []

    for scraper in SCRAPERS:
        path = scraper_dir / scraper
        print(f"\n{'='*40}")
        print(f"▶ Spouštím: {scraper}")
        result = subprocess.run(
            [sys.executable, str(path)],
            capture_output=False
        )
        if result.returncode != 0:
            errors.append(scraper)
            print(f"✗ {scraper} skončil s chybou {result.returncode}")
        else:
            print(f"✓ {scraper} OK")

    print(f"\n{'='*40}")
    if errors:
        print(f"⚠ Chyby ve scraperech: {', '.join(errors)}")
        sys.exit(1)
    else:
        print("✅ Všechny scrapers hotovy.")

if __name__ == "__main__":
    main()