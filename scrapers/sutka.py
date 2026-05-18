"""
scrapers/sutka.py
Scrapes lane schedule from sutka.eu/50m-plavecky-bazen
Structure: HTML table, rows = lanes 1-8, columns = 15-min slots from 6:00
Empty cell = volno, cell with /kurz/... link = rezervováno
"""

import json
import re
import sys
from datetime import datetime, timezone, timedelta, date
from pathlib import Path

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("pip install requests beautifulsoup4", file=sys.stderr)
    sys.exit(1)

PRAGUE_TZ  = timezone(timedelta(hours=2))
BASE_URL   = "https://www.sutka.eu"
LANES_URL  = f"{BASE_URL}/50m-plavecky-bazen"
TOTAL_LANES = 8
SLOT_MINUTES = 15          # each column = 15 min
START_HOUR   = 6           # table starts at 06:00
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; KamJitPlavatBot/1.0)"}

# Map JS day index (Mon=0) to our keys
DAY_KEYS = ["po", "ut", "st", "ct", "pa", "so", "ne"]


def fetch_table_for_date(target_date: date) -> list[dict] | None:
    """Fetch page and parse the schedule table for target_date.
    Returns list of slot dicts or None on error."""
    try:
        # Šutka accepts ?od=DD.MM.YYYY&do=DD.MM.YYYY to filter by week
        ds = target_date.strftime("%d.%m.%Y")
        resp = requests.get(
            LANES_URL,
            params={"od": ds, "do": ds},
            headers=HEADERS,
            timeout=12,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[Šutka] HTTP error: {e}", file=sys.stderr)
        return None

    soup = BeautifulSoup(resp.text, "html.parser")

    # Find the schedule table — it's the first big table with lane rows
    table = soup.find("table")
    if not table:
        print("[Šutka] No table found", file=sys.stderr)
        return None

    rows = table.find_all("tr")
    if len(rows) < 2:
        return None

    # Header row: first <td> or <th> cells contain hour labels (6:00, 7:00 …)
    # We count how many columns there are to derive time slots
    header_row = rows[0]
    header_cells = header_row.find_all(["td", "th"])

    # Build time axis: skip first 2 cells (label + lane#), rest = slots
    # Each hour appears once as label, represents 4×15min slots
    # Simpler: total data columns = (22-6)*4 = 64 slots
    total_slots = (22 - START_HOUR) * (60 // SLOT_MINUTES)  # 64

    # Parse lane rows (rows 1..8, skip header)
    # Each data row: cells[0]=day label (merged), cells[1]=lane number,
    #   cells[2..] = slot cells
    lane_reservations: dict[int, list[bool]] = {}  # lane -> [reserved per slot]

    data_rows = [r for r in rows[1:] if r.find_all("td")]
    for row in data_rows:
        cells = row.find_all("td")
        if not cells:
            continue

        # Detect lane number — first cell that is just a digit 1-8
        lane_num = None
        slot_start_idx = None
        for i, c in enumerate(cells):
            txt = c.get_text(strip=True)
            if txt.isdigit() and 1 <= int(txt) <= TOTAL_LANES:
                lane_num = int(txt)
                slot_start_idx = i + 1
                break

        if lane_num is None or slot_start_idx is None:
            continue

        slot_cells = cells[slot_start_idx:]
        reserved = []
        for cell in slot_cells:
            # Reserved = has a link to /kurz/...
            has_club = bool(cell.find("a", href=re.compile(r"/kurz/")))
            reserved.append(has_club)

        lane_reservations[lane_num] = reserved

    if not lane_reservations:
        print("[Šutka] Could not parse lane rows", file=sys.stderr)
        return None

    # Collapse 15-min slots into continuous blocks
    return build_schedule(lane_reservations, total_slots)


def build_schedule(lane_res: dict[int, list[bool]], total_slots: int) -> list[dict]:
    """Turn per-lane slot reservations into time-block schedule dicts."""
    # For each slot, determine which lanes are free/reserved
    slots_data = []
    for s in range(total_slots):
        free = []
        reserved = []
        for lane in range(1, TOTAL_LANES + 1):
            res_list = lane_res.get(lane, [])
            is_res = res_list[s] if s < len(res_list) else False
            if is_res:
                reserved.append(lane)
            else:
                free.append(lane)
        slots_data.append({"free": free, "reserved": reserved})

    # Merge consecutive slots with same free/reserved pattern into blocks
    blocks = []
    i = 0
    while i < len(slots_data):
        current = slots_data[i]
        j = i + 1
        while j < len(slots_data) and slots_data[j] == current:
            j += 1
        # slots i..j-1 are the same
        from_mins = START_HOUR * 60 + i * SLOT_MINUTES
        to_mins   = START_HOUR * 60 + j * SLOT_MINUTES
        from_str  = f"{from_mins//60:02d}:{from_mins%60:02d}"
        to_str    = f"{to_mins//60:02d}:{to_mins%60:02d}"

        res = current["reserved"]
        free = current["free"]
        block_type = "volno" if not res else "klub"

        blocks.append({
            "from": from_str,
            "to": to_str,
            "type": block_type,
            "free_lanes": free,
            "reserved_lanes": res,
            "note": "" if not res else "Rezervováno klubem"
        })
        i = j

    return blocks


def scrape_week() -> dict:
    """Scrape all 7 days of the current week and return schedule dict."""
    today = datetime.now(PRAGUE_TZ).date()
    # Monday of current week
    monday = today - timedelta(days=today.weekday())

    schedule = {}
    for offset, key in enumerate(DAY_KEYS):
        target = monday + timedelta(days=offset)
        print(f"[Šutka] Fetching {key} ({target})…")
        blocks = fetch_table_for_date(target)
        if blocks:
            schedule[key] = blocks
            print(f"[Šutka]   → {len(blocks)} blocks")
        else:
            schedule[key] = []
            print(f"[Šutka]   → failed, empty")

    return schedule


def main():
    lanes_path = Path(__file__).parent.parent / "data" / "lanes.json"
    if lanes_path.exists():
        with open(lanes_path, "r", encoding="utf-8") as f:
            existing = json.load(f)
    else:
        existing = {"updated_at": "", "pools": {}}

    schedule = scrape_week()

    existing.setdefault("pools", {})
    existing["pools"]["sutka"] = {
        "50m": {
            "name": "50m bazén",
            "total_lanes": TOTAL_LANES,
            "schedule": schedule
        }
    }
    existing["updated_at"] = datetime.now(PRAGUE_TZ).isoformat()

    with open(lanes_path, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)

    print("[Šutka] Done — lanes.json updated")


if __name__ == "__main__":
    main()