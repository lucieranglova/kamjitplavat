"""
scrapers/podoli.py
Používá Google Sheets gviz/tq JSON API které vrací colspan informace.
URL: https://docs.google.com/spreadsheets/d/e/KEY/pub?gid=0&single=true&output=json
→ Vrátí JSON s buňkami které mají colspan zachovaný.
"""
import json, re, sys
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


def fetch_gviz(key: str) -> list | None:
    """Fetch via gviz/tq which returns JSON with proper cell data."""
    url = (f"https://docs.google.com/spreadsheets/d/e/{key}"
           f"/gviz/tq?tqx=out:json&sheet=List%201")
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        # Response is: /*O_o*/\ngoogle.visualization.Query.setResponse({...});
        text = r.text
        # Extract JSON
        m = re.search(r'setResponse\((\{.*\})\)', text, re.DOTALL)
        if not m:
            print(f"  gviz: no JSON found, len={len(text)}", file=sys.stderr)
            return None
        data = json.loads(m.group(1))
        rows = data.get("table", {}).get("rows", [])
        print(f"  gviz: {len(rows)} rows")
        return rows
    except Exception as e:
        print(f"  gviz error: {e}", file=sys.stderr)
        return None


def parse_gviz(rows: list, total_lanes: int) -> dict:
    """
    gviz rows: list of {"c": [{"v": value, "f": formatted}, ...]}
    Cells with colspan are repeated (gviz expands merges).
    Actually gviz does NOT expand merges — merged cells appear once with full value,
    subsequent cells are null.
    
    Layout (same as CSV):
      col0: empty
      col1: day+lane or lane number  
      col2: empty
      col3+: 15-min slots
      
    With gviz, null cells after a value = continuation of merge = RESERVED.
    Null cells that were never part of a merge = VOLNO.
    
    Key insight with gviz: we can detect merge continuation by checking if
    the cell value is None/null AND the previous non-null cell in same row had a value.
    But we still can't distinguish "null because merged" from "null because volno".
    
    However: in gviz, merged cells show as {"v": null} for continuation cells.
    The FIRST cell of a merge has the actual value.
    
    Better: use gviz p (properties) or just track per-row null patterns.
    After a non-null value, nulls = continuation until BOTH:
    - this row has null AND
    - a "boundary" row (where other lanes also have values starting) occurs
    
    Actually: let's use the SIMPLEST correct approach:
    For each lane-row, expand nulls by forward-filling ONLY until the next
    value-start slot of ANY lane in the same day. This is correct because
    Podolí sheet always has at least one lane starting a new block at each
    real time boundary.
    """
    if not rows:
        return {}

    def cell_val(c):
        if c is None:
            return ""
        v = c.get("v")
        f = c.get("f")
        if f and str(f).strip():
            return str(f).strip()
        if v and str(v).strip() not in ("None", "null", ""):
            return str(v).strip()
        return ""

    # Find header row (contains time values like 6, 7, 8...)
    hour_row_idx = None
    for i, row in enumerate(rows):
        cells = row.get("c", [])
        vals = [cell_val(c) for c in cells]
        # Hour row has numeric values 6,7,8... or "6.00","7.00"
        nums = sum(1 for v in vals[2:] if re.match(r"^[67891]\d?\.?\d*$", v.strip()))
        if nums >= 4:
            hour_row_idx = i
            break

    if hour_row_idx is None:
        print("  No hour row", file=sys.stderr)
        return {}

    data_start = hour_row_idx + 2  # skip colour row

    # Collect raw data: day -> lane -> [cell_val per slot]
    all_rows_by_day: dict[str, dict[int, list[str]]] = {}
    current_day = None

    for row in rows[data_start:]:
        cells = row.get("c", [])
        if len(cells) < 4:
            continue
        col1 = cell_val(cells[1]) if len(cells) > 1 else ""
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

        raw = [cell_val(c) for c in cells[3:3 + TOTAL_SLOTS]]
        raw += [""] * (TOTAL_SLOTS - len(raw))
        all_rows_by_day[current_day][lane_num] = raw[:TOTAL_SLOTS]

    if not all_rows_by_day:
        return {}

    # Now build reserved arrays.
    # In gviz: null after value = merged (reserved), null after null = volno.
    # BUT: null after value that ended = also null.
    # Strategy: 
    #   1. Find all slots where ANY lane starts a new value (real time boundaries)
    #   2. Per lane: value at boundary → reserved until next boundary
    #      No value at boundary → check if previous boundary had value for this lane
    #      AND no intervening boundary had null for this lane (continuation vs volno)
    # 
    # Simplest correct rule for gviz nulls:
    #   Forward-fill each lane's value, BUT reset to volno when we hit a slot
    #   where this lane is null AND the previous slot for this lane was ALSO null
    #   (i.e., two consecutive nulls = definitely volno, not continuation)

    result: dict[str, list[dict]] = {}
    for day, lanes_raw in all_rows_by_day.items():
        lane_reserved: dict[int, list[bool]] = {}
        for ln in range(1, total_lanes + 1):
            raw = lanes_raw.get(ln, [""] * TOTAL_SLOTS)
            res = [False] * TOTAL_SLOTS
            current_val = ""
            prev_was_null = True  # start as null (before row begins)
            for s, cell in enumerate(raw):
                v = cell.strip()
                if v:
                    # New value starts → reserved
                    current_val = v
                    prev_was_null = False
                    res[s] = True
                elif not prev_was_null:
                    # Null after non-null → could be merge continuation → reserved
                    res[s] = True
                    prev_was_null = False  # still in continuation
                else:
                    # Null after null → volno
                    current_val = ""
                    res[s] = False
                    prev_was_null = True
            lane_reserved[ln] = res

        # Build per-slot summary
        per_slot = []
        for s in range(TOTAL_SLOTS):
            free, res2 = [], []
            for ln in range(1, total_lanes + 1):
                (res2 if lane_reserved[ln][s] else free).append(ln)
            mins = START_HOUR * 60 + s * SLOT_MIN
            per_slot.append({
                "free": free, "reserved": res2,
                "from": f"{mins//60:02d}:{mins%60:02d}",
                "to":   f"{(mins+SLOT_MIN)//60:02d}:{(mins+SLOT_MIN)%60:02d}",
            })

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
        rows = fetch_gviz(cfg["key"])
        if not rows:
            print("  gviz failed, skipping")
            continue
        # Debug: show first 10 data rows
        print(f"  First rows sample:")
        for i, row in enumerate(rows[:12]):
            cells = row.get("c", [])
            vals = []
            for c in cells[:8]:
                if c is None:
                    vals.append("NULL")
                else:
                    v = c.get("v","")
                    f = c.get("f","")
                    vals.append(f"'{f or v}'")
            print(f"    row{i}: {vals}")
        schedule = parse_gviz(rows, cfg["total_lanes"])
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
