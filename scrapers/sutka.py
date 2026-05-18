"""
scrapers/sutka.py  
URL: sutka.eu/obsazenost-bazenu  (vždy aktuální týden, 200 OK)
Tabulka: řádky = dny×dráhy, sloupce = 15min sloty od 6:00
Buňka s <a href="/kurz/..."> = rezervováno, prázdná = volno.
"""

import json, re, sys
from datetime import datetime, timezone, timedelta, date
from pathlib import Path

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("pip install requests beautifulsoup4", file=sys.stderr)
    sys.exit(1)

PRAGUE_TZ   = timezone(timedelta(hours=2))
URL         = "https://www.sutka.eu/obsazenost-bazenu"
TOTAL_LANES = 8
SLOT_MIN    = 15
START_HOUR  = 6
DAY_KEYS    = ["po","ut","st","ct","pa","so","ne"]
CZ_DAYS     = {
    "po":"po","pon":"po","pondělí":"po",
    "út":"ut","ute":"ut","úterý":"ut",
    "st":"st","stř":"st","středa":"st",
    "čt":"ct","čtv":"ct","čtvrtek":"ct",
    "pá":"pa","pát":"pa","pátek":"pa",
    "so":"so","sob":"so","sobota":"so",
    "ne":"ne","ned":"ne","neděle":"ne",
}
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "cs-CZ,cs;q=0.9",
}


def fetch():
    try:
        r = requests.get(URL, headers=HEADERS, timeout=15)
        r.raise_for_status()
        return r.text
    except requests.RequestException as e:
        print(f"[Šutka] HTTP error: {e}", file=sys.stderr)
        return None


def parse(html: str) -> dict:
    """Parse the schedule table → {day_key: [slot_dict, ...]}"""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        print("[Šutka] No table found", file=sys.stderr)
        return {}

    rows = table.find_all("tr")
    if len(rows) < 2:
        return {}

    total_slots = (22 - START_HOUR) * (60 // SLOT_MIN)  # 64

    # Each data row: cells[0] = "PO 18.5." (day label, merged across 8 lane rows)
    #                cells[1] = lane number 1-8
    #                cells[2..] = slot cells
    # Day label only appears in first of 8 lane rows (rowspan=8), rest skip it.

    schedule: dict[str, dict[int, list[bool]]] = {}  # day -> lane -> [reserved]
    current_day = None

    for row in rows[1:]:
        cells = row.find_all("td")
        if not cells:
            continue

        texts = [c.get_text(" ", strip=True) for c in cells]

        # Detect if first cell is a day label (contains day abbreviation)
        first = texts[0].lower().split()[0] if texts[0] else ""
        if first in CZ_DAYS:
            current_day = CZ_DAYS[first]
            if current_day not in schedule:
                schedule[current_day] = {}
            lane_cell_idx = 1
        else:
            lane_cell_idx = 0

        if current_day is None:
            continue

        # Find lane number
        lane_num = None
        slot_start = None
        for i in range(lane_cell_idx, min(lane_cell_idx + 2, len(cells))):
            t = texts[i].strip()
            if t.isdigit() and 1 <= int(t) <= TOTAL_LANES:
                lane_num = int(t)
                slot_start = i + 1
                break

        if lane_num is None:
            continue

        slot_cells = cells[slot_start:]
        reserved = [bool(c.find("a", href=re.compile(r"/kurz/")))
                    for c in slot_cells]
        schedule[current_day][lane_num] = reserved

    # Convert to slot dicts
    result = {}
    for day, lanes in schedule.items():
        if not lanes:
            continue
        slots = []
        for s in range(total_slots):
            free, res = [], []
            for lane in range(1, TOTAL_LANES + 1):
                is_res = lanes.get(lane, [False] * total_slots)
                is_res = is_res[s] if s < len(is_res) else False
                (res if is_res else free).append(lane)
            slots.append({"free": free, "reserved": res})

        # Merge consecutive identical slots
        blocks = []
        i = 0
        while i < len(slots):
            cur = slots[i]
            j = i + 1
            while j < len(slots) and slots[j] == cur:
                j += 1
            fm = START_HOUR * 60 + i * SLOT_MIN
            tm = START_HOUR * 60 + j * SLOT_MIN
            blocks.append({
                "from": f"{fm//60:02d}:{fm%60:02d}",
                "to":   f"{tm//60:02d}:{tm%60:02d}",
                "type": "volno" if not cur["reserved"] else "klub",
                "free_lanes": cur["free"],
                "reserved_lanes": cur["reserved"],
                "note": "Rezervováno klubem" if cur["reserved"] else "",
            })
            i = j
        result[day] = blocks
        print(f"[Šutka]   {day}: {len(blocks)} bloků")

    return result


def main():
    lanes_path = Path(__file__).parent.parent / "data" / "lanes.json"
    existing = json.loads(lanes_path.read_text("utf-8")) if lanes_path.exists() \
               else {"updated_at": "", "pools": {}}

    print("[Šutka] Fetching…")
    html = fetch()
    schedule = parse(html) if html else {}

    if not any(schedule.values()):
        print("[Šutka] No data parsed — keeping existing", file=sys.stderr)
    else:
        print(f"[Šutka] Parsed {sum(len(v) for v in schedule.values())} total blocks")
        existing.setdefault("pools", {})["sutka"] = {
            "50m": {
                "name": "50m bazén",
                "total_lanes": TOTAL_LANES,
                "schedule": schedule,
            }
        }
        existing["updated_at"] = datetime.now(PRAGUE_TZ).isoformat()
        lanes_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), "utf-8")
        print("[Šutka] Done.")


if __name__ == "__main__":
    main()
