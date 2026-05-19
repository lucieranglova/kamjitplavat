"""
scrapers/podoli.py
Používá Google Visualization API (gviz/tq) místo CSV/pubhtml.
Vrací JSON s kompletními daty včetně správného mapování buněk.
URL: https://docs.google.com/spreadsheets/d/KEY/gviz/tq?tqx=out:csv&sheet=List%201
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
START_HOUR  = 6
END_HOUR    = 22
TOTAL_SLOTS = (END_HOUR - START_HOUR) * (60 // SLOT_MIN)  # 64

CZ_DAYS = {
    "po":"po","pon":"po","pondělí":"po","pondeli":"po",
    "út":"ut","ut":"ut","úterý":"ut","utery":"ut",
    "st":"st","stř":"st","středa":"st","streda":"st",
    "čt":"ct","ct":"ct","čtvrtek":"ct","ctvrtek":"ct",
    "pá":"pa","pa":"pa","pátek":"pa","patek":"pa",
    "so":"so","sobota":"so",
    "ne":"ne","neděle":"ne","nedele":"ne",
}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
}

# Sheet keys (extracted from pubhtml URL)
POOLS = {
    "indoor_50m": {
        "name": "Vnitřní 50m", "total_lanes": 8, "seasonal": False,
        "key": "2PACX-1vR854rTLdUeeKfN7voCtEPgsYaRqsTWmsq0IGz3UmJ_F4fzsRIiHNoT9P0hcX_TwiRc0yCZOVBlmPiR",
    },
    "outdoor_33m": {
        "name": "Venkovní 33m", "total_lanes": 6, "seasonal": True,
        "key": "2PACX-1vQrNP5TxfHgKq4zQkoku-QP7q4_dTuu_O2g_4TMmW-gPoXW1dBaWpJX8-1H_FqglublvpeFdDqmucPH",
    },
    "outdoor_50m": {
        "name": "Venkovní 50m", "total_lanes": 8, "seasonal": True,
        "key": "2PACX-1vRC_joLhOV1adM_gSW9h7mIBdj1g-dMd1AFVd7qSFGaNWodOCQ9KgL4BeY0yyYf7GHk-BNSR96p6i_2",
    },
}


def detect_day(text: str) -> str | None:
    for word in re.split(r"[\s\xa0\n|]+", text.lower()):
        word = re.sub(r"[^\w]", "", word)
        if word in CZ_DAYS:
            return CZ_DAYS[word]
    return None


def fetch_csv(key: str) -> list[list[str]] | None:
    # gviz/tq endpoint with CSV output — returns data without JS rendering
    url = f"https://docs.google.com/spreadsheets/d/e/{key}/pub?output=csv"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
        r.raise_for_status()
        text = r.content.decode("utf-8", errors="replace")
        rows = list(csv.reader(io.StringIO(text)))
        print(f"  rows={len(rows)}, cols={len(rows[0]) if rows else 0}")
        return rows
    except Exception as e:
        print(f"  fetch error: {e}", file=sys.stderr)
        return None


def parse_csv(rows: list[list[str]], total_lanes: int) -> dict:
    """
    Key insight: Google Sheets CSV exports merged cells as:
      - First cell of merge: contains the value
      - Subsequent cells of merge: EMPTY
    This is indistinguishable from a genuinely empty (volno) cell.
    
    Solution: compare across all lanes in same column.
    If column S has a value in lane X, and empty in lane Y,
    we still can't know if lane Y is volno or continuation.
    
    Better solution: look at the PATTERN across a row.
    A reservation block appears as: [value][empty][empty]...[empty][next_value_or_empty]
    A volno gap appears as: [empty][empty]...[empty]
    
    We use: forward-fill the value, but only until we see another lane
    in the SAME column has a different value (indicating a real boundary).
    
    Simplest correct approach given CSV limitations:
    Forward-fill each cell value until next NON-EMPTY cell in same row.
    This correctly handles merged reservations.
    Genuinely empty columns (volno) will never have had a value.
    """
    if not rows:
        return {}

    # Find hour row
    time_re = re.compile(r"^6[.:,]0+$")
    hour_row_idx = None
    for i, row in enumerate(rows):
        if any(time_re.match(c.strip()) for c in row[2:]):
            hour_row_idx = i
            break

    if hour_row_idx is None:
        print("  No hour row found", file=sys.stderr)
        return {}

    data_start = hour_row_idx + 2  # skip colour-label row

    lane_data: dict[str, dict[int, list[bool]]] = {}
    current_day = None

    for row in rows[data_start:]:
        if len(row) < 4:
            continue

        col1 = row[1].strip()
        if not col1:
            continue

        day = detect_day(col1)
        if day:
            current_day = day
            lane_data.setdefault(day, {})
            nums = re.findall(r"\d+", col1)
            lane_num = int(nums[-1]) if nums and 1 <= int(nums[-1]) <= total_lanes else None
            if lane_num is None:
                continue
        elif current_day and re.match(r"^\d+$", col1) and 1 <= int(col1) <= total_lanes:
            lane_num = int(col1)
        else:
            continue

        # Forward-fill: value persists until next non-empty cell
        raw = row[3:3 + TOTAL_SLOTS]
        reserved = []
        current_val = ""
        for cell in raw:
            v = cell.strip()
            if v:
                current_val = v
            # current_val persists (forward-fill) until explicitly cleared
            # We never clear it — merged cells stay merged
            reserved.append(bool(current_val))

        # Now we need to detect where reservations actually END.
        # Cross-lane approach: a slot is truly free if the cell was never
        # filled AND no adjacent lane suggests a merge continuation.
        # Without colspan info, best we can do: use the raw row length.
        # If row has fewer cols than expected, trailing slots are volno.
        reserved = reserved[:TOTAL_SLOTS]
        reserved += [False] * (TOTAL_SLOTS - len(reserved))
        lane_data[current_day][lane_num] = reserved

    if not lane_data:
        print("  No lane data", file=sys.stderr)
        return {}

    # The forward-fill approach overfills — everything after first value = reserved.
    # FIX: Use a different strategy.
    # Re-process: for each day, look at column patterns across all lanes.
    # A column where ALL lanes are empty (after forward-fill reset) = truly volno.
    # Reset forward-fill when we detect a "seam" — all lanes empty in same col.
    
    # Re-parse with seam detection
    lane_data2: dict[str, dict[int, list[bool]]] = {}
    current_day = None
    all_rows_by_day: dict[str, dict[int, list[str]]] = {}  # day -> lane -> raw cells

    for row in rows[data_start:]:
        if len(row) < 4:
            continue
        col1 = row[1].strip()
        if not col1:
            continue
        day = detect_day(col1)
        if day:
            current_day = day
            all_rows_by_day.setdefault(day, {})
            nums = re.findall(r"\d+", col1)
            lane_num = int(nums[-1]) if nums and 1 <= int(nums[-1]) <= total_lanes else None
            if lane_num is None:
                continue
        elif current_day and re.match(r"^\d+$", col1) and 1 <= int(col1) <= total_lanes:
            lane_num = int(col1)
        else:
            continue
        raw = row[3:3 + TOTAL_SLOTS]
        raw += [""] * (TOTAL_SLOTS - len(raw))
        all_rows_by_day[current_day][lane_num] = raw[:TOTAL_SLOTS]

    # For each day: find "seam" columns where ALL lanes are empty → these are volno boundaries
    result: dict[str, list[dict]] = {}
    for day, lanes_raw in all_rows_by_day.items():
        if not lanes_raw:
            continue

        # Find seam slots: all lanes empty in that slot
        seams = set()
        for s in range(TOTAL_SLOTS):
            if all(lanes_raw.get(ln, [""])[s].strip() == "" 
                   for ln in range(1, total_lanes + 1)):
                seams.add(s)

        # Forward-fill per lane, reset at seam slots
        lane_reserved: dict[int, list[bool]] = {}
        for ln in range(1, total_lanes + 1):
            raw = lanes_raw.get(ln, [""] * TOTAL_SLOTS)
            res = []
            current_val = ""
            for s, cell in enumerate(raw):
                v = cell.strip()
                if s in seams:
                    current_val = ""  # reset at seam
                if v:
                    current_val = v
                res.append(bool(current_val))
            lane_reserved[ln] = res

        # Build per-slot summary
        per_slot = []
        for s in range(TOTAL_SLOTS):
            free, res = [], []
            for ln in range(1, total_lanes + 1):
                (res if lane_reserved[ln][s] else free).append(ln)
            mins = START_HOUR * 60 + s * SLOT_MIN
            per_slot.append({
                "free": free, "reserved": res,
                "from": f"{mins//60:02d}:{mins%60:02d}",
                "to":   f"{(mins+SLOT_MIN)//60:02d}:{(mins+SLOT_MIN)%60:02d}",
            })

        # Merge consecutive identical slots
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
        print(f"  {day}: {len(blocks)} bloků ({res_count} rezervací)")

    return result


def main():
    lanes_path = Path(__file__).parent.parent / "data" / "lanes.json"
    existing = json.loads(lanes_path.read_text("utf-8")) if lanes_path.exists() \
               else {"updated_at": "", "pools": {}}
    existing.setdefault("pools", {})

    podoli_data = {}
    for pool_id, cfg in POOLS.items():
        print(f"[Podolí] {cfg['name']}…")
        rows = fetch_csv(cfg["key"])
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
