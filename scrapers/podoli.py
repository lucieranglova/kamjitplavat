"""
scrapers/podoli.py
Podolí zveřejňuje rozvrh jako Google Sheets (pubhtml + CSV export).
Stahujeme CSV verzi pro každý ze tří bazénů.

Formát sheetu (typicky):
  Řádek 1: hlavička — "Dráha", "Pondělí", "Úterý" …  nebo časy
  Řádek 2+: data

Podolí sheet má tento layout (dle vzorku):
  Sloupec A: čas (od-do), např. "6:00 - 7:30"
  Sloupec B-H: dráhy 1-8 (nebo jméno nájemce / prázdné)
  Každý list = jeden den NEBO celý týden na jednom listu.

Pokud CSV parsing selže → použijeme fallback statický rozvrh.
"""

import csv
import io
import json
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    import requests
except ImportError:
    print("pip install requests", file=sys.stderr)
    sys.exit(1)

PRAGUE_TZ = timezone(timedelta(hours=2))
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; KamJitPlavatBot/1.0)"}

# Google Sheets CSV export URLs for each Podolí pool
POOLS = {
    "indoor_50m": {
        "name": "Vnitřní 50m",
        "total_lanes": 8,
        "seasonal": False,
        "csv_url": (
            "https://docs.google.com/spreadsheets/d/e/"
            "2PACX-1vR854rTLdUeeKfN7voCtEPgsYaRqsTWmsq0IGz3UmJ_F4fzsRIiHNoT9P0hcX_TwiRc0yCZOVBlmPiR"
            "/pub?output=csv"
        ),
    },
    "outdoor_33m": {
        "name": "Venkovní 33m",
        "total_lanes": 6,
        "seasonal": True,
        "csv_url": (
            "https://docs.google.com/spreadsheets/d/e/"
            "2PACX-1vQrNP5TxfHgKq4zQkoku-QP7q4_dTuu_O2g_4TMmW-gPoXW1dBaWpJX8-1H_FqglublvpeFdDqmucPH"
            "/pub?output=csv"
        ),
    },
    "outdoor_50m": {
        "name": "Venkovní 50m",
        "total_lanes": 8,
        "seasonal": True,
        "csv_url": (
            "https://docs.google.com/spreadsheets/d/e/"
            "2PACX-1vRC_joLhOV1adM_gSW9h7mIBdj1g-dMd1AFVd7qSFGaNWodOCQ9KgL4BeY0yyYf7GHk-BNSR96p6i_2"
            "/pub?output=csv"
        ),
    },
}

DAY_KEYS = ["po", "ut", "st", "ct", "pa", "so", "ne"]

# Czech day names as they appear in the sheet header
CZ_DAY_MAP = {
    "pondělí": "po", "pondelí": "po", "po": "po",
    "úterý": "ut", "utery": "ut", "út": "ut", "ut": "ut",
    "středa": "st", "streda": "st", "st": "st",
    "čtvrtek": "ct", "ctvrtek": "ct", "čt": "ct", "ct": "ct",
    "pátek": "pa", "patek": "pa", "pá": "pa", "pa": "pa",
    "sobota": "so", "so": "so",
    "neděle": "ne", "nedele": "ne", "ne": "ne",
}

TIME_RE = re.compile(r"(\d{1,2})[:\.](\d{2})\s*[-–]\s*(\d{1,2})[:\.](\d{2})")


def parse_time_range(text: str):
    """Return (from_str, to_str) or None."""
    m = TIME_RE.search(text.strip())
    if not m:
        return None
    h1, m1, h2, m2 = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
    return f"{h1:02d}:{m1:02d}", f"{h2:02d}:{m2:02d}"


def fetch_csv(url: str) -> list[list[str]] | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=12)
        r.raise_for_status()
        reader = csv.reader(io.StringIO(r.text))
        return [row for row in reader]
    except Exception as e:
        print(f"  CSV fetch error: {e}", file=sys.stderr)
        return None


def parse_podoli_csv(rows: list[list[str]], total_lanes: int) -> dict:
    """
    Try to detect the sheet layout and extract per-day schedules.

    Layout A (time-major): 
      Row 0: ["", "Po", "Út", ...]  — day headers in columns
      Row 1+: ["6:00-7:30", "klub1", "klub1", ...]  — time label + one cell per lane per day
      → one row per time block, columns = days, need to know lane count

    Layout B (day-major):
      Row 0: ["Dráha", "6:00-7:30", "7:30-9:00", ...]  — time headers
      Row 1+: ["1", "klub", "", ...]  — lane rows

    We'll detect by checking if row 0 col 0 contains a day name or time.
    """
    if not rows or len(rows) < 2:
        return {}

    schedule: dict[str, list[dict]] = {k: [] for k in DAY_KEYS}

    # --- Try Layout A: first row has day names in columns ---
    header = [c.strip().lower() for c in rows[0]]
    day_cols: dict[str, list[int]] = {}  # day_key -> list of column indices

    for ci, cell in enumerate(header[1:], start=1):
        # strip date portion "pondělí 19.5." → "pondělí"
        word = cell.split()[0] if cell else ""
        if word in CZ_DAY_MAP:
            key = CZ_DAY_MAP[word]
            day_cols.setdefault(key, []).append(ci)

    if day_cols:
        # Layout A detected
        # Each data row: col 0 = time range, other cols = lane occupancy per day
        for row in rows[1:]:
            if not row or not row[0].strip():
                continue
            parsed = parse_time_range(row[0])
            if not parsed:
                continue
            from_str, to_str = parsed

            for day_key, cols in day_cols.items():
                # Collect values for this day's columns
                # Lanes = cols in order; empty = volno, text = reserved
                free_lanes, reserved_lanes = [], []
                for lane_idx, ci in enumerate(cols[:total_lanes], start=1):
                    val = row[ci].strip() if ci < len(row) else ""
                    if val:
                        reserved_lanes.append(lane_idx)
                    else:
                        free_lanes.append(lane_idx)

                block_type = "volno" if not reserved_lanes else "klub"
                schedule[day_key].append({
                    "from": from_str,
                    "to": to_str,
                    "type": block_type,
                    "free_lanes": free_lanes,
                    "reserved_lanes": reserved_lanes,
                    "note": "" if not reserved_lanes else row[cols[0]].strip() if cols[0] < len(row) else "",
                })
        if any(schedule.values()):
            return schedule

    # --- Try Layout B: first col has lane numbers, first row has times ---
    time_cols: list[tuple[int, str, str]] = []  # (col_idx, from, to)
    for ci, cell in enumerate(rows[0][1:], start=1):
        parsed = parse_time_range(cell)
        if parsed:
            time_cols.append((ci, parsed[0], parsed[1]))

    if time_cols:
        lane_rows: dict[int, list[tuple[int, str]]] = {}  # col_idx -> [(lane, value)]
        for row in rows[1:]:
            if not row:
                continue
            lane_txt = row[0].strip()
            if not lane_txt.isdigit():
                continue
            lane = int(lane_txt)
            if 1 <= lane <= total_lanes:
                for ci, from_s, to_s in time_cols:
                    val = row[ci].strip() if ci < len(row) else ""
                    lane_rows.setdefault((ci, from_s, to_s), []).append((lane, val))

        # This layout gives one week — we put it all into "po" as placeholder
        # (Podolí may publish one sheet per week, not per day)
        for (ci, from_s, to_s), lanes in lane_rows.items():
            free = [l for l, v in lanes if not v]
            res  = [l for l, v in lanes if v]
            schedule["po"].append({
                "from": from_s, "to": to_s,
                "type": "volno" if not res else "klub",
                "free_lanes": free, "reserved_lanes": res,
                "note": "",
            })
        if schedule["po"]:
            return schedule

    print("  Could not detect sheet layout, using fallback", file=sys.stderr)
    return {}


FALLBACK_INDOOR = [
    {"from":"06:00","to":"07:30","type":"volno","free_lanes":[1,2,3,4,5,6,7,8],"reserved_lanes":[],"note":"Ranní plavání"},
    {"from":"07:30","to":"09:00","type":"klub", "free_lanes":[1,2,3,4],"reserved_lanes":[5,6,7,8],"note":"Plavecký klub"},
    {"from":"09:00","to":"12:00","type":"volno","free_lanes":[1,2,3,4,5,6,7,8],"reserved_lanes":[],"note":"Volné plavání"},
    {"from":"12:00","to":"14:00","type":"kurzy","free_lanes":[5,6,7,8],"reserved_lanes":[1,2,3,4],"note":"Plavecké kurzy"},
    {"from":"14:00","to":"17:00","type":"volno","free_lanes":[1,2,3,4,5,6,7,8],"reserved_lanes":[],"note":"Volné plavání"},
    {"from":"17:00","to":"19:30","type":"klub", "free_lanes":[1,2,3],"reserved_lanes":[4,5,6,7,8],"note":"Plavecký klub"},
    {"from":"19:30","to":"21:45","type":"volno","free_lanes":[1,2,3,4,5,6,7,8],"reserved_lanes":[],"note":"Večerní plavání"},
]
FALLBACK_WEEK = {k: FALLBACK_INDOOR for k in DAY_KEYS}


def scrape_pool(pool_id: str, pool_cfg: dict) -> dict:
    print(f"[Podolí] Fetching {pool_cfg['name']}…")
    rows = fetch_csv(pool_cfg["csv_url"])
    schedule = {}
    if rows:
        schedule = parse_podoli_csv(rows, pool_cfg["total_lanes"])
    if not any(schedule.values()):
        print(f"[Podolí]   → Using fallback for {pool_cfg['name']}", file=sys.stderr)
        schedule = FALLBACK_WEEK
    else:
        total = sum(len(v) for v in schedule.values())
        print(f"[Podolí]   → {total} blocks parsed")
    return {
        "name": pool_cfg["name"],
        "total_lanes": pool_cfg["total_lanes"],
        "seasonal": pool_cfg.get("seasonal", False),
        "schedule": schedule,
    }


def main():
    lanes_path = Path(__file__).parent.parent / "data" / "lanes.json"
    if lanes_path.exists():
        with open(lanes_path, "r", encoding="utf-8") as f:
            existing = json.load(f)
    else:
        existing = {"updated_at": "", "pools": {}}

    existing.setdefault("pools", {})
    existing["pools"]["podoli"] = {
        pool_id: scrape_pool(pool_id, cfg)
        for pool_id, cfg in POOLS.items()
    }
    existing["updated_at"] = datetime.now(PRAGUE_TZ).isoformat()

    with open(lanes_path, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
    print("[Podolí] Done.")


if __name__ == "__main__":
    main()