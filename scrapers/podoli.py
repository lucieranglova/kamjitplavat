"""
scrapers/podoli.py
CSV export z Google Sheets.
Struktura (z debug logu):
  row0: prázdný + nadpis
  row1: prázdný
  row2: '' '' '' '6.00' '' '' '7.00' ... → hour row (col3+)
  row3: barevné bloky label (skip)
  row4+: '' 'pondělí 1' '' 'klub text' '' ...
         '' 'pondělí 2' '' '' '' ...  → den+dráha v col1, sloty od col3
         '' '2' '' '' ...             → jen číslo dráhy v col1
Obsazená buňka = neprázdný text v slot slotu, prázdná = volno.
Encoding: response.text může mít špatné encoding → použijeme response.content + decode utf-8.
"""
import csv, io, json, re, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit(1)

PRAGUE_TZ  = timezone(timedelta(hours=2))
SLOT_MIN   = 15
START_HOUR = 6
END_HOUR   = 22
TOTAL_SLOTS = (END_HOUR - START_HOUR) * (60 // SLOT_MIN)  # 64

DAY_KEYS = ["po","ut","st","ct","pa","so","ne"]
CZ_DAYS  = {
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


def fetch_csv(url: str) -> list[list[str]] | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
        r.raise_for_status()
        # Force UTF-8 — r.text may misdetect encoding
        text = r.content.decode("utf-8", errors="replace")
        rows = list(csv.reader(io.StringIO(text)))
        print(f"  rows={len(rows)}, cols={len(rows[0]) if rows else 0}, status={r.status_code}")
        return rows
    except Exception as e:
        print(f"  fetch error: {e}", file=sys.stderr)
        return None


def parse_csv(rows: list[list[str]], total_lanes: int) -> dict:
    """
    Layout:
      - Hour row: col0='' col1='' col2='' col3='6.00' col4='' col5='' col6='7.00' ...
        (every 4th col starting at col3 = hour label, intermediate = quarter slots)
      - Data rows: col1 = 'pondělí 1' or '2'..'8', col3+ = slot values
    """
    if not rows:
        return {}

    # Find hour row (has '6.00' or '6:00' somewhere from col3 onward)
    time_re = re.compile(r"^6[.:.]0+$")
    hour_row_idx = None
    for i, row in enumerate(rows):
        if any(time_re.match(c.strip()) for c in row[2:]):
            hour_row_idx = i
            break

    if hour_row_idx is None:
        print("  No hour row found", file=sys.stderr)
        return {}

    # Data starts 2 rows after hour row (skip colour-label row)
    data_start = hour_row_idx + 2

    lane_data: dict[str, dict[int, list[bool]]] = {}
    current_day = None

    for row in rows[data_start:]:
        if len(row) < 4:
            continue

        col1 = row[1].strip()
        if not col1:
            continue

        # Try to detect day from col1
        day = detect_day(col1)
        if day:
            current_day = day
            lane_data.setdefault(day, {})
            # Lane number is at end of col1: 'pondělí 1' → 1
            nums = re.findall(r"\d+", col1)
            lane_num = int(nums[-1]) if nums and 1 <= int(nums[-1]) <= total_lanes else None
            if lane_num is None:
                continue
        elif current_day and re.match(r"^\d+$", col1) and 1 <= int(col1) <= total_lanes:
            lane_num = int(col1)
        else:
            continue

        # Slots start at col3, step=1 (each col = one 15-min slot)
        slot_cells = row[3:3 + TOTAL_SLOTS]
        reserved = [bool(c.strip()) for c in slot_cells]
        reserved += [False] * (TOTAL_SLOTS - len(reserved))
        lane_data[current_day][lane_num] = reserved

    if not lane_data:
        return {}

    # Convert to time blocks
    result: dict[str, list[dict]] = {}
    for day, lanes in lane_data.items():
        per_slot = []
        for s in range(TOTAL_SLOTS):
            free, res = [], []
            for ln in range(1, total_lanes + 1):
                lr = lanes.get(ln, [False] * TOTAL_SLOTS)
                (res if (s < len(lr) and lr[s]) else free).append(ln)
            mins = START_HOUR * 60 + s * SLOT_MIN
            per_slot.append({
                "free": free, "reserved": res,
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
            continue
        schedule = parse_csv(rows, cfg["total_lanes"])
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
