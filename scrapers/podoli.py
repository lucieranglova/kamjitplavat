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
        "url": "https://docs.google.com/spreadsheets/d/e/2PACX-1vR854rTLdUeeKfN7voCtEPgsYaRqsTWmsq0IGz3UmJ_F4fzsRIiHNoT9P0hcX_TwiRc0yCZOVBlmPiR/pub?output=csv&gid=0",
    },
    "outdoor_33m": {
        "name": "Venkovní 33m",
        "total_lanes": 6,
        "seasonal": True,
        "slot_min": 15,
        "start_hour": 6,
        "end_hour": 22,
        "url": "https://docs.google.com/spreadsheets/d/e/2PACX-1vQrNP5TxfHgKq4zQkoku-QP7q4_dTuu_O2g_4TMmW-gPoXW1dBaWpJX8-1H_FqglublvpeFdDqmucPH/pub?output=csv&gid=0",
    },
    "outdoor_50m": {
        "name": "Venkovní 50m",
        "total_lanes": 8,
        "seasonal": True,
        "slot_min": 15,
        "start_hour": 6,
        "end_hour": 22,
        "url": "https://docs.google.com/spreadsheets/d/e/2PACX-1vRC_joLhOV1adM_gSW9h7mIBdj1g-dMd1AFVd7qSFGaNWodOCQ9KgL4BeY0yyYf7GHk-BNSR96p6i_2/pub?output=csv&gid=0",
    },
}


def fetch_csv(url: str) -> list[list[str]] | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
        r.raise_for_status()
        print(f"  CSV len={len(r.text)}, status={r.status_code}")
        import csv, io
        reader = csv.reader(io.StringIO(r.text))
        rows = [row for row in reader]
        print(f"  CSV rows={len(rows)}, cols={len(rows[0]) if rows else 0}")
        # Show first 5 rows for debug
        for i, row in enumerate(rows[:5]):
            print(f"  row{i}: {row[:6]}")
        return rows
    except Exception as e:
        print(f"  CSV error: {e}", file=sys.stderr)
        return None


def parse_csv(rows: list[list[str]], total_lanes: int, slot_min: int, start_hour: int) -> dict:
    """
    CSV layout from Podolí sheet (based on screenshot):
      Row 0: nadpis (skip)
      Row 1: empty + hodiny (6.00, 7.00...)
      Row 2: barevné bloky label (skip)
      Row 3+: data — col0=den+datum or lane num, col1=lane num or slot, col2+=slots
    Each non-empty cell = reserved, empty = volno.
    colspan is lost in CSV — each merged cell appears once, rest are empty.
    We expand by detecting transitions.
    """
    if not rows or len(rows) < 3:
        return {}

    # Find header row with hours
    hour_row_idx = None
    time_re = re.compile(r"^\d{1,2}[.:]\d{2}$")
    for i, row in enumerate(rows):
        matches = sum(1 for c in row if time_re.match(c.strip()))
        if matches >= 4:
            hour_row_idx = i
            break

    if hour_row_idx is None:
        print("  No hour row found", file=sys.stderr)
        return {}

    print(f"  Hour row at index {hour_row_idx}")

    # Build slot count from hour row
    hour_row = rows[hour_row_idx]
    # Count non-empty cells after first two (day/lane cols)
    n_cols = len(hour_row)
    # Data cols start after day+lane cols (first 2)
    data_col_start = 2
    total_slots = (22 - start_hour) * (60 // slot_min)  # 64

    # Parse data rows
    lane_data: dict[str, dict[int, list[bool]]] = {}
    current_day = None

    for row in rows[hour_row_idx + 2:]:  # skip hour row + colour-label row
        if not row or all(c.strip() == "" for c in row):
            continue

        col0 = row[0].strip() if row else ""
        col1 = row[1].strip() if len(row) > 1 else ""

        # Detect day in col0
        day = detect_day(col0)
        if day:
            current_day = day
            lane_data.setdefault(day, {})
            # Lane number after day text — look for digit in col0 or col1
            lane_num = None
            for part in re.split(r"[\s\n]+", col0):
                if part.isdigit() and 1 <= int(part) <= total_lanes:
                    lane_num = int(part)
                    break
            if lane_num is None and col1.isdigit() and 1 <= int(col1) <= total_lanes:
                lane_num = int(col1)
                slot_start = data_col_start
            elif lane_num is not None:
                slot_start = data_col_start
            else:
                continue
        elif current_day and col0.isdigit() and 1 <= int(col0) <= total_lanes:
            lane_num = int(col0)
            slot_start = 1
        elif current_day and col1.isdigit() and 1 <= int(col1) <= total_lanes:
            lane_num = int(col1)
            slot_start = data_col_start
        else:
            continue

        # Read slot cells — in CSV, colspan means value appears once then empty
        # We treat non-empty = reserved for that slot, propagate forward until next non-empty or empty
        raw_slots = row[slot_start:]

        # Expand: each cell in CSV represents one slot (colspan is flattened)
        # But merged cells in GSheets export: first cell has value, subsequent are empty
        # Strategy: non-empty = new reservation starts, empty after non-empty = reservation continues
        # empty after empty = volno
        reserved_slots: list[bool] = []
        last_was_reserved = False
        for cell in raw_slots:
            val = cell.strip()
            if val:
                reserved_slots.append(True)
                last_was_reserved = True
            else:
                # Empty after reservation could be continuation of colspan OR genuinely free
                # In GSheets CSV, merged cell: first=value, rest=empty up to next cell
                # We can't reliably distinguish — treat empty as volno (conservative)
                reserved_slots.append(False)
                last_was_reserved = False

        reserved_slots = reserved_slots[:total_slots]
        reserved_slots += [False] * (total_slots - len(reserved_slots))
        lane_data[current_day][lane_num] = reserved_slots

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
            print(f"  → skip (fetch error)")
            continue
        schedule = parse_csv(
            rows, cfg["total_lanes"], cfg["slot_min"], cfg["start_hour"]
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
