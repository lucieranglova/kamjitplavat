"""
scrapers/sutka.py
URL: sutka.eu/course/
Struktura (z debug logu):
  - Každých 9 řádků = 1 header (hodiny) + 8 drah
  - Header řádek: cell[0].colspan=2, cell[1]='6:00' atd.
  - Dráha 1: cell[0]='ÚT|19.5.' (rowspan=8), cell[1]='1', cell[2..]=sloty
  - Dráha 2-8: cell[0]='2'..'8', cell[1..]=sloty
  - Slot rezervovaný = class='active-field', volný = prázdný
  - Každý slot = 15 min, od 6:00 do 22:00 = 64 slotů
  - Colspan na aktivních buňkách = délka rezervace v slotech
"""
import json, re, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    sys.exit(1)

PRAGUE_TZ   = timezone(timedelta(hours=2))
URL         = "https://www.sutka.eu/course/"
TOTAL_LANES = 8
SLOT_MIN    = 15
START_HOUR  = 6
END_HOUR    = 22
TOTAL_SLOTS = (END_HOUR - START_HOUR) * (60 // SLOT_MIN)  # 64

DAY_KEYS = ["po","ut","st","ct","pa","so","ne"]
CZ_DAYS = {
    "po":"po","pá":"pa","pa":"pa","út":"ut","ut":"ut",
    "st":"st","čt":"ct","ct":"ct","so":"so","ne":"ne",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "cs-CZ,cs;q=0.9",
}

def fetch():
    r = requests.get(URL, headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.text

def detect_day(text):
    """'ÚT|19.5.' -> 'ut'"""
    word = text.split("|")[0].strip().lower()
    word = re.sub(r"[^\w]", "", word)
    return CZ_DAYS.get(word)

def is_header_row(tds):
    """Header row: cell[0] has colspan=2, cell[1] text='6:00'"""
    return (tds[0].get("colspan") == "2" and
            tds[1].get_text(strip=True) == "6:00")

def expand_row(tds, slot_start_idx):
    """
    Expand slot cells to list of booleans (length=TOTAL_SLOTS).
    Active-field cells can have colspan > 1.
    """
    result = []
    for td in tds[slot_start_idx:]:
        cs = int(td.get("colspan", 1))
        reserved = "active-field" in td.get("class", [])
        result.extend([reserved] * cs)
    # Trim or pad to TOTAL_SLOTS
    result = result[:TOTAL_SLOTS]
    result += [False] * (TOTAL_SLOTS - len(result))
    return result

def slots_to_blocks(per_lane_slots):
    """
    per_lane_slots: {lane_num: [bool * TOTAL_SLOTS]}
    Returns list of block dicts.
    """
    per_slot = []
    for s in range(TOTAL_SLOTS):
        free, res = [], []
        for ln in range(1, TOTAL_LANES + 1):
            lr = per_lane_slots.get(ln, [False] * TOTAL_SLOTS)
            (res if lr[s] else free).append(ln)
        mins = START_HOUR * 60 + s * SLOT_MIN
        per_slot.append({
            "free": free, "reserved": res,
            "from": f"{mins//60:02d}:{mins%60:02d}",
            "to":   f"{(mins+SLOT_MIN)//60:02d}:{(mins+SLOT_MIN)%60:02d}",
        })

    blocks = []
    i = 0
    while i < len(per_slot):
        cur = per_slot[i]
        j = i + 1
        while j < len(per_slot) and \
              per_slot[j]["free"] == cur["free"] and \
              per_slot[j]["reserved"] == cur["reserved"]:
            j += 1
        blocks.append({
            "from": cur["from"],
            "to":   per_slot[j-1]["to"],
            "type": "volno" if not cur["reserved"] else "klub",
            "free_lanes": cur["free"],
            "reserved_lanes": cur["reserved"],
            "note": "Rezervováno klubem" if cur["reserved"] else "",
        })
        i = j
    return blocks

def parse(html):
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    rows = table.find_all("tr")

    schedule = {}          # day_key -> {lane -> [bool]}
    current_day = None

    for row in rows:
        tds = row.find_all(["td","th"])
        if not tds:
            continue

        # Skip header rows (hodiny)
        if is_header_row(tds):
            continue

        cell0_text = tds[0].get_text(separator="|", strip=True)

        # Row with day label (rowspan=8): cell[0]='ÚT|19.5.', cell[1]='1', cell[2..]=slots
        if tds[0].get("rowspan") == "8":
            day = detect_day(cell0_text)
            if not day:
                continue
            current_day = day
            if day not in schedule:
                schedule[day] = {}
            lane = int(tds[1].get_text(strip=True))
            schedule[day][lane] = expand_row(tds, slot_start_idx=2)

        # Rows for lanes 2-8: cell[0]='2'..'8', cell[1..]=slots
        elif current_day and cell0_text.isdigit() and 2 <= int(cell0_text) <= TOTAL_LANES:
            lane = int(cell0_text)
            schedule[current_day][lane] = expand_row(tds, slot_start_idx=1)

    # Convert to blocks
    result = {}
    for day, lanes in schedule.items():
        result[day] = slots_to_blocks(lanes)
        res_count = sum(len(b["reserved_lanes"]) for b in result[day] if b["reserved_lanes"])
        print(f"[Šutka]   {day}: {len(result[day])} bloků")

    return result

def main():
    lanes_path = Path(__file__).parent.parent / "data" / "lanes.json"
    existing = json.loads(lanes_path.read_text("utf-8")) if lanes_path.exists() \
               else {"updated_at": "", "pools": {}}

    print("[Šutka] Fetching…")
    html = fetch()
    schedule = parse(html)

    if not schedule:
        print("[Šutka] No data parsed", file=sys.stderr)
        return

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
