"""
scrapers/podoli.py
Podolí zveřejňuje rozvrhy jako Google Sheets pubhtml.
Struktura je totožná se Šutkou:
  - 1 tabulka, celý týden
  - Řádky: den (rowspan=8) + dráha 1-8 + sloty 15min
  - Obsazená buňka = má text nebo bgcolor (jakýkoliv nenulový obsah)
  - Prázdná buňka = volno
  - Header řádky: obsahují hodiny (6:00, 7:00...)
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
DAY_KEYS    = ["po","ut","st","ct","pa","so","ne"]
CZ_DAYS     = {
    "po":"po","pon":"po","pondělí":"po","pondeli":"po",
    "út":"ut","ut":"ut","úterý":"ut","utery":"ut",
    "st":"st","stř":"st","středa":"st","streda":"st",
    "čt":"ct","ct":"ct","čtvrtek":"ct","ctvrtek":"ct",
    "pá":"pa","pa":"pa","pátek":"pa","patek":"pa",
    "so":"so","sobota":"so",
    "ne":"ne","neděle":"ne","nedele":"ne",
}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}

POOLS = {
    "indoor_50m": {
        "name": "Vnitřní 50m",
        "total_lanes": 8,
        "seasonal": False,
        "slot_min": 15,
        "start_hour": 6,
        "end_hour": 22,
        "url": "https://docs.google.com/spreadsheets/d/e/2PACX-1vR854rTLdUeeKfN7voCtEPgsYaRqsTWmsq0IGz3UmJ_F4fzsRIiHNoT9P0hcX_TwiRc0yCZOVBlmPiR/pubhtml",
    },
    "outdoor_33m": {
        "name": "Venkovní 33m",
        "total_lanes": 6,
        "seasonal": True,
        "slot_min": 15,
        "start_hour": 6,
        "end_hour": 22,
        "url": "https://docs.google.com/spreadsheets/d/e/2PACX-1vQrNP5TxfHgKq4zQkoku-QP7q4_dTuu_O2g_4TMmW-gPoXW1dBaWpJX8-1H_FqglublvpeFdDqmucPH/pubhtml",
    },
    "outdoor_50m": {
        "name": "Venkovní 50m",
        "total_lanes": 8,
        "seasonal": True,
        "slot_min": 15,
        "start_hour": 6,
        "end_hour": 22,
        "url": "https://docs.google.com/spreadsheets/d/e/2PACX-1vRC_joLhOV1adM_gSW9h7mIBdj1g-dMd1AFVd7qSFGaNWodOCQ9KgL4BeY0yyYf7GHk-BNSR96p6i_2/pubhtml",
    },
}


def detect_day(text: str) -> str | None:
    for word in re.split(r"[\s\xa0\n|]+", text.lower()):
        word = re.sub(r"[^\w]", "", word)
        if word in CZ_DAYS:
            return CZ_DAYS[word]
    return None


def cell_is_reserved(td) -> bool:
    """Any cell with non-empty text content = reserved."""
    return bool(td.get_text(strip=True))


def is_header_row(tds) -> bool:
    """Header: first cell has colspan=2 and second cell text looks like a time."""
    if not tds:
        return False
    cs = tds[0].get("colspan","")
    if cs in ("2","3"):
        for td in tds[1:4]:
            t = td.get_text(strip=True)
            if re.match(r"^\d{1,2}:\d{2}$", t):
                return True
    return False


def parse_pool(html: str, total_lanes: int, slot_min: int, start_hour: int, end_hour: int) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        return {}

    rows = table.find_all("tr")
    total_slots = (end_hour - start_hour) * (60 // slot_min)

    lane_data: dict[str, dict[int, list[bool]]] = {}
    current_day = None

    for row in rows:
        tds = row.find_all(["td","th"])
        if not tds:
            continue
        if is_header_row(tds):
            continue

        cell0_text = tds[0].get_text(separator="|", strip=True)

        # Row with day label (rowspan=8): detect day, lane=1, slots from index 2
        if tds[0].get("rowspan") in ("8","6","7"):
            day = detect_day(cell0_text)
            if not day:
                continue
            current_day = day
            lane_data.setdefault(day, {})
            lane_text = tds[1].get_text(strip=True) if len(tds) > 1 else ""
            if not lane_text.isdigit():
                continue
            lane_num = int(lane_text)
            slot_tds = tds[2:]

        # Rows for lanes 2-N: lane num in cell 0, slots from index 1
        elif current_day and cell0_text.isdigit() and 1 <= int(cell0_text) <= total_lanes:
            lane_num = int(cell0_text)
            slot_tds = tds[1:]

        else:
            continue

        reserved = []
        for td in slot_tds:
            cs = int(td.get("colspan", 1))
            is_res = cell_is_reserved(td)
            reserved.extend([is_res] * cs)

        reserved = reserved[:total_slots]
        reserved += [False] * (total_slots - len(reserved))
        lane_data[current_day][lane_num] = reserved

    if not lane_data:
        return {}

    # Convert to blocks
    result: dict[str, list[dict]] = {}
    for day, lanes in lane_data.items():
        per_slot = []
        for s in range(total_slots):
            free, res = [], []
            for ln in range(1, total_lanes + 1):
                lr = lanes.get(ln, [False] * total_slots)
                (res if (s < len(lr) and lr[s]) else free).append(ln)
            mins = start_hour * 60 + s * slot_min
            per_slot.append({
                "free": free, "reserved": res,
                "from": f"{mins//60:02d}:{mins%60:02d}",
                "to":   f"{(mins+slot_min)//60:02d}:{(mins+slot_min)%60:02d}",
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
                "note": "Rezervováno" if cur["reserved"] else "",
            })
            i = j

        result[day] = blocks
        print(f"  {day}: {len(blocks)} bloků")

    return result


def fetch(url: str) -> str | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        return r.text
    except Exception as e:
        print(f"  HTTP error: {e}", file=sys.stderr)
        return None


def main():
    lanes_path = Path(__file__).parent.parent / "data" / "lanes.json"
    existing = json.loads(lanes_path.read_text("utf-8")) if lanes_path.exists() \
               else {"updated_at": "", "pools": {}}
    existing.setdefault("pools", {})

    podoli_data = {}
    for pool_id, cfg in POOLS.items():
        print(f"[Podolí] {cfg['name']}…")
        html = fetch(cfg["url"])
        if not html:
            print(f"  → skip (HTTP error)")
            continue
        schedule = parse_pool(
            html, cfg["total_lanes"], cfg["slot_min"],
            cfg["start_hour"], cfg["end_hour"]
        )
        if not schedule:
            print(f"  → no data parsed", file=sys.stderr)
            continue
        podoli_data[pool_id] = {
            "name": cfg["name"],
            "total_lanes": cfg["total_lanes"],
            "seasonal": cfg["seasonal"],
            "schedule": schedule,
        }
        print(f"  → OK")

    if podoli_data:
        existing["pools"]["podoli"] = podoli_data
        existing["updated_at"] = datetime.now(PRAGUE_TZ).isoformat()
        lanes_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), "utf-8")
        print("[Podolí] Done.")
    else:
        print("[Podolí] No data — keeping existing", file=sys.stderr)


if __name__ == "__main__":
    main()
