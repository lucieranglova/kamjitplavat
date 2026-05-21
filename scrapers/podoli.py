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
    print("=== ALL ROWS, FIRST 10 CELLS ===")
    for i, row in enumerate(rows):
        # Show index + value for each of first 10 cells, mark empty vs non-empty
        cells = []
        for j, c in enumerate(row[:10]):
            v = c.strip()
            cells.append(f"c{j}={'['+v[:8]+']' if v else '_'}")
        print(f"  ROW {i:02d} ({len(row)} cols): {' '.join(cells)}")

if __name__ == "__main__":
    main()
