"""
scrapers/podoli.py - FULL debug dump (jako Šutka)
"""
import csv, io, json, re, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit(1)

HEADERS = {"User-Agent": "Mozilla/5.0 Chrome/124.0.0.0"}

URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vRC_joLhOV1adM_gSW9h7mIBdj1g-dMd1AFVd7qSFGaNWodOCQ9KgL4BeY0yyYf7GHk-BNSR96p6i_2/pub?output=csv"

def main():
    r = requests.get(URL, headers=HEADERS, timeout=15)
    r.raise_for_status()
    rows = list(csv.reader(io.StringIO(r.content.decode("utf-8", errors="replace"))))
    print(f"[Podolí debug] {len(rows)} rows, {len(rows[0]) if rows else 0} cols")
    
    # Show hour row completely
    print("=== HOUR ROW (row 2) — non-empty cells only ===")
    for j, c in enumerate(rows[2]):
        if c.strip():
            print(f"  c{j}='{c.strip()}'")
    
    # Show pondělí rows (4-11) — ALL non-empty cells
    print("=== PONDĚLÍ ROWS (4-11) — non-empty cells only ===")
    for i in range(4, 12):
        row = rows[i]
        nonempty = [(j, c.strip()) for j, c in enumerate(row) if c.strip()]
        print(f"  ROW {i} ({rows[i][1].strip()}): {nonempty}")

if __name__ == "__main__":
    main()
