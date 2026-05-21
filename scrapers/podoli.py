"""
scrapers/podoli.py
CSV struktura (z debug logu):
  Hour row: c3=6.00, c7=7.00, c11=8.00 ... c67=22
  → každé 4 sloupce = 1 hodina = 4×15min slotů
  → col_to_time(c) = 6:00 + (c-3)*15min
  
  Data rows:
    c1 = "pondělí 1" nebo "18.5. 2" nebo "3".."8" (den + číslo dráhy)
    c2..c67 = sloty (neprázdná hodnota = začátek rezervace)
  
  Délka rezervace: od hodnoty v col X do další hodnoty v col Y (téže dráhy)
  → dráha je rezervovaná v slotech [X, Y)
  
  Dráhy 6,7,8 nemají žádné hodnoty → volné celý den.
"""
import csv, io, json, re, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit(1)

PRAGUE_TZ   = timezone(timedelta(hours=2))
SLOT_MIN    = 15
START_COL   = 2   # první datový sloupec (c2 = 5:45 nebo začátek)
HOUR_START  = 3   # c3 = 6:00
HOUR_VAL    = 6   # hodnota v c3
END_COL     = 67  # poslední sloupec (c67 = 22:00)

CZ_DAYS = {
    "po":"po","pon":"po","pondělí":"po","pondeli":"po",
    "út":"ut","ut":"ut","úterý":"ut","utery":"ut",
    "st":"st","stř":"st","středa":"st","streda":"st",
    "čt":"ct","ct":"ct","čtvrtek":"ct","ctvrtek":"ct",
    "pá":"pa","pa":"pa","pátek":"pa","patek":"pa",
    "so":"so","sobota":"so",
    "ne":"ne","neděle":"ne","nedele":"ne",
}
HEADERS = {"User-Agent": "Mozilla/5.0 Chrome/124.0.0.0"}

POOLS = {
    "indoor_50m": {
        "name": "Vnitřní 50m", "total_lanes": 8, "seasonal": False,
        "url": "https://docs.google.com/spreadsheets/d/e/2PACX-1vR854rTLdUeeKfN7voCtEPgsYaRqsTWmsq0IGz3UmJ_F4fzsRIiHNoT9P0hcX_TwiRc0yCZOVBlmPiR/pub?output=csv",
    },
    "outdoor_33m": {
        "name": "Venkovní 33m", "total_lanes": 6, "seasonal": True,
        "url": "https://docs.google.com/spreadsheets/d/e/2PACX-1vQrNP5TxfHgKq4zQkoku-QP7q4_dTuu_O2g_4TMmW-gPoXW1dBaWpJX8-1H_FqglublvpeFdDqmucPH/pub?output=csv",
    },
    "outdoor_50m": {
        "name": "Venkovní 50m", "total_lanes": 8, "seasonal": True,
        "url": "https://docs.google.com/spreadsheets/d/e/2PACX-1vRC_joLhOV1adM_gSW9h7mIBdj1g-dMd1AFVd7qSFGaNWodOCQ9KgL4BeY0yyYf7GHk-BNSR96p6i_2/pub?output=csv",
    },
}


def detect_day(text: str) -> str | None:
    for word in re.split(r"[\s\xa0\n|]+", text.lower()):
        word = re.sub(r"[^\w]", "", word)
        if word in CZ_DAYS:
            return CZ_DAYS[word]
    return None


def col_to_mins(col: int, hour_col: int = HOUR_START, hour_val: int = HOUR_VAL) -> int:
    """Convert column index to minutes since midnight."""
    return hour_val * 60 + (col - hour_col) * SLOT_MIN


def mins_to_str(m: int) -> str:
    return f"{m//60:02d}:{m%60:02d}"


def fetch_csv(url: str) -> list | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        return list(csv.reader(io.StringIO(r.content.decode("utf-8", errors="replace"))))
    except Exception as e:
        print(f"  fetch error: {e}", file=sys.stderr)
        return None


def find_hour_col(rows: list) -> int | None:
    """Find column index of 6.00 in hour row."""
    time_re = re.compile(r"^6[.,]0+$")
    for row in rows[:5]:
        for j, c in enumerate(row):
            if time_re.match(c.strip()):
                return j
    return None


def parse_csv(rows: list, total_lanes: int) -> dict:
    hour_col = find_hour_col(rows)
    if hour_col is None:
        print("  No hour col found", file=sys.stderr)
        return {}

    # Find end col (22:00)
    end_col = hour_col + (22 - 6) * 4  # 6:00 + 16h * 4slots = col for 22:00

    # Find data start row
    data_start = None
    for i, row in enumerate(rows):
        c1 = row[1].strip() if len(row) > 1 else ""
        if detect_day(c1):
            data_start = i
            break
    if data_start is None:
        print("  No data rows found", file=sys.stderr)
        return {}

    # Collect per-day per-lane: list of (col, value) tuples
    # day -> lane -> [(col, value), ...]
    events: dict[str, dict[int, list]] = {}
    current_day = None

    for row in rows[data_start:]:
        if len(row) < 2:
            continue
        c1 = row[1].strip()
        if not c1:
            continue

        day = detect_day(c1)
        if day:
            current_day = day
            events.setdefault(day, {})
            nums = re.findall(r"\d+", c1)
            lane_num = int(nums[-1]) if nums and 1 <= int(nums[-1]) <= total_lanes else None
            if lane_num is None:
                continue
        elif current_day and re.match(r"^\d+$", c1) and 1 <= int(c1) <= total_lanes:
            lane_num = int(c1)
        else:
            continue

        # Collect all non-empty slot columns for this lane
        lane_events = []
        for col_idx in range(2, len(row)):
            v = row[col_idx].strip()
            if v:
                lane_events.append((col_idx, v))

        events[current_day][lane_num] = lane_events

    # Build schedule blocks
    result: dict[str, list[dict]] = {}
    for day, lanes in events.items():
        # For each slot, determine free/reserved per lane
        # Slot range: from hour_col to end_col (exclusive)
        n_slots = end_col - hour_col  # = 64 slots from 6:00 to 22:00

        # Build per-lane reserved array
        lane_reserved: dict[int, list[bool]] = {}
        for ln in range(1, total_lanes + 1):
            res = [False] * n_slots
            lane_evts = lanes.get(ln, [])
            # Each event: reserved from its col until next event col (or end)
            for ei, (col, val) in enumerate(lane_evts):
                next_col = lane_evts[ei + 1][0] if ei + 1 < len(lane_evts) else end_col
                start_slot = col - hour_col
                end_slot   = next_col - hour_col
                # Clamp
                start_slot = max(0, min(start_slot, n_slots))
                end_slot   = max(0, min(end_slot, n_slots))
                for s in range(start_slot, end_slot):
                    res[s] = True
            lane_reserved[ln] = res

        # Build per-slot summary
        per_slot = []
        for s in range(n_slots):
            free, res2 = [], []
            for ln in range(1, total_lanes + 1):
                (res2 if lane_reserved[ln][s] else free).append(ln)
            mins = 6 * 60 + s * SLOT_MIN
            per_slot.append({
                "free": free, "reserved": res2,
                "from": mins_to_str(mins),
                "to":   mins_to_str(mins + SLOT_MIN),
            })

        # Merge consecutive identical slots into blocks
        blocks, i = [], 0
        while i < len(per_slot):
            cur = per_slot[i]
            j = i + 1
            while j < len(per_slot) and \
                  per_slot[j]["free"] == cur["free"] and \
                  per_slot[j]["reserved"] == cur["reserved"]:
                j += 1
            blocks.append({
                "from": cur["from"], "to": per_slot[j-1]["to"],
                "type": "volno" if not cur["reserved"] else "klub",
                "free_lanes": cur["free"], "reserved_lanes": cur["reserved"],
                "note": "Rezervováno" if cur["reserved"] else "",
            })
            i = j

        result[day] = blocks
        res_count = sum(1 for b in blocks if b["type"] == "klub")
        print(f"  {day}: {len(blocks)} bloků ({res_count} rez.)")

    return result


def main():
    lanes_path = Path(__file__).parent.parent / "data" / "lanes.json"
    existing = json.loads(lanes_path.read_text("utf-8")) if lanes_path.exists() \
               else {"updated_at": "", "pools": {}}
    existing.setdefault("pools", {})

    podoli_data = {}
    for pool_id, cfg in POOLS.items():
        print(f"[Podolí] {cfg['name']}…")
        rows = fetch_csv(cfg["url"])
        if not rows:
            continue
        schedule = parse_csv(rows, cfg["total_lanes"])
        if not schedule:
            print(f"  → no data", file=sys.stderr)
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
