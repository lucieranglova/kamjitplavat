"""
scrapers/podoli.py
Parsuje Google Sheets pubhtml pro každý ze tří bazénů Podolí.

Layout sheetu:
  Řádek 0: nadpis (přeskočit)
  Řádek 1: hodiny 6:00 7:00 … (každá hodina = N sloupců = 15min sloty)
  Řádek 2: barevné bloky (přeskočit)
  Řádek 3+: data:
    - první buňka = "pondělí 1" nebo "18.5. 2" nebo jen "3" atd.
      → detekujeme den a číslo dráhy
    - ostatní buňky = obsazení (prázdné = volno, text = rezervováno)
"""

import json, re, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("pip install requests beautifulsoup4", file=sys.stderr)
    sys.exit(1)

PRAGUE_TZ = timezone(timedelta(hours=2))
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

DAY_KEYS = ["po","ut","st","ct","pa","so","ne"]
CZ_DAY_MAP = {
    "pondělí":"po","pondeli":"po","po":"po",
    "úterý":"ut","utery":"ut","út":"ut","ut":"ut",
    "středa":"st","streda":"st","st":"st",
    "čtvrtek":"ct","ctvrtek":"ct","čt":"ct","ct":"ct",
    "pátek":"pa","patek":"pa","pá":"pa","pa":"pa",
    "sobota":"so","so":"so",
    "neděle":"ne","nedele":"ne","ne":"ne",
}

POOLS = {
    "indoor_50m": {
        "name": "Vnitřní 50m", "total_lanes": 8, "seasonal": False,
        "url": "https://docs.google.com/spreadsheets/d/e/2PACX-1vR854rTLdUeeKfN7voCtEPgsYaRqsTWmsq0IGz3UmJ_F4fzsRIiHNoT9P0hcX_TwiRc0yCZOVBlmPiR/pubhtml",
    },
    "outdoor_33m": {
        "name": "Venkovní 33m", "total_lanes": 6, "seasonal": True,
        "url": "https://docs.google.com/spreadsheets/d/e/2PACX-1vQrNP5TxfHgKq4zQkoku-QP7q4_dTuu_O2g_4TMmW-gPoXW1dBaWpJX8-1H_FqglublvpeFdDqmucPH/pubhtml",
    },
    "outdoor_50m": {
        "name": "Venkovní 50m", "total_lanes": 8, "seasonal": True,
        "url": "https://docs.google.com/spreadsheets/d/e/2PACX-1vRC_joLhOV1adM_gSW9h7mIBdj1g-dMd1AFVd7qSFGaNWodOCQ9KgL4BeY0yyYf7GHk-BNSR96p6i_2/pubhtml",
    },
}

START_HOUR = 6
SLOT_MIN   = 15


def make_fallback(lanes: int) -> dict:
    return {k: [
        {"from":"06:00","to":"07:30","type":"volno","free_lanes":list(range(1,lanes+1)),"reserved_lanes":[],"note":"Ranní plavání"},
        {"from":"07:30","to":"09:00","type":"klub", "free_lanes":list(range(1,lanes//2+1)),"reserved_lanes":list(range(lanes//2+1,lanes+1)),"note":"Plavecký klub"},
        {"from":"09:00","to":"12:00","type":"volno","free_lanes":list(range(1,lanes+1)),"reserved_lanes":[],"note":"Volné plavání"},
        {"from":"14:00","to":"21:45","type":"volno","free_lanes":list(range(1,lanes+1)),"reserved_lanes":[],"note":"Volné plavání"},
    ] for k in DAY_KEYS}


def fetch_html(url: str) -> str | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        return r.text
    except Exception as e:
        print(f"  HTTP error: {e}", file=sys.stderr)
        return None


def parse_sheet(html: str, total_lanes: int) -> dict:
    """Returns {day_key: [slot_dict, ...]} or {}"""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        print("  No table found", file=sys.stderr)
        return {}

    rows = [r.find_all(["td","th"]) for r in table.find_all("tr")]
    rows = [r for r in rows if r]
    if len(rows) < 4:
        return {}

    # ── Step 1: find the header row with hour labels (6:00, 7:00…) ──
    hour_row_idx = None
    time_re = re.compile(r"^\d{1,2}[:.]\d{2}$")
    for i, row in enumerate(rows):
        texts = [c.get_text(strip=True) for c in row]
        matches = sum(1 for t in texts if time_re.match(t))
        if matches >= 4:
            hour_row_idx = i
            break

    if hour_row_idx is None:
        print("  Could not find hour header row", file=sys.stderr)
        return {}

    # ── Step 2: build time axis from hour row ──
    # Each hour label cell may span multiple columns (colspan)
    # We expand to get one entry per slot column
    hour_row = rows[hour_row_idx]
    time_axis: list[str | None] = []  # one entry per physical column
    for cell in hour_row:
        text = cell.get_text(strip=True)
        colspan = int(cell.get("colspan", 1))
        label = text if time_re.match(text) else None
        for _ in range(colspan):
            time_axis.append(label)

    # Build list of (col_index, from_str, to_str) for data columns
    # Fill gaps between hour labels
    slot_cols: list[tuple[int, str, str]] = []
    current_h = START_HOUR
    current_m = 0
    for ci, label in enumerate(time_axis):
        if label is not None:
            # Parse label to set current time
            parts = re.split(r"[:.]+", label)
            current_h = int(parts[0])
            current_m = int(parts[1]) if len(parts) > 1 else 0
        from_s = f"{current_h:02d}:{current_m:02d}"
        end_m  = current_h * 60 + current_m + SLOT_MIN
        to_s   = f"{end_m//60:02d}:{end_m%60:02d}"
        slot_cols.append((ci, from_s, to_s))
        current_m += SLOT_MIN
        if current_m >= 60:
            current_h += current_m // 60
            current_m  = current_m % 60

    # ── Step 3: parse data rows ──
    # Format: first cell = "pondělí 1" / "18.5. 2" / "3" + lane number
    lane_data: dict[str, dict[int, list[bool]]] = {}  # day -> lane -> [reserved per slot]
    current_day = None

    data_rows = rows[hour_row_idx + 2:]  # skip hour row + colour-block row

    for row in data_rows:
        if not row:
            continue
        first_text = row[0].get_text(" ", strip=True)

        # Detect day name in first cell
        words = first_text.lower().split()
        for w in words:
            w_clean = re.sub(r"[^\w]", "", w)
            if w_clean in CZ_DAY_MAP:
                current_day = CZ_DAY_MAP[w_clean]
                if current_day not in lane_data:
                    lane_data[current_day] = {}
                break

        if current_day is None:
            continue

        # Find lane number — last number 1-8 in first cell text
        lane_num = None
        for w in reversed(words):
            if w.isdigit() and 1 <= int(w) <= total_lanes:
                lane_num = int(w)
                break
        if lane_num is None:
            continue

        # First cell may span multiple columns — get its colspan
        first_colspan = int(row[0].get("colspan", 1))

        # Remaining cells = slot data, starting at column index = first_colspan
        start_ci = first_colspan
        data_cells = row[1:]  # cells after the label cell

        # Expand data cells considering colspan
        expanded: list[str] = []
        for cell in data_cells:
            text = cell.get_text(strip=True)
            cs   = int(cell.get("colspan", 1))
            for _ in range(cs):
                expanded.append(text)

        # Map expanded cells to slot columns
        reserved_slots: list[bool] = [False] * len(slot_cols)
        for i, text in enumerate(expanded):
            ci = start_ci + i
            # find matching slot_col
            for sc_idx, (sc_ci, _, _) in enumerate(slot_cols):
                if sc_ci == ci:
                    reserved_slots[sc_idx] = bool(text.strip())
                    break

        lane_data[current_day][lane_num] = reserved_slots

    if not lane_data:
        print("  No lane data parsed", file=sys.stderr)
        return {}

    # ── Step 4: convert to blocks ──
    result: dict[str, list[dict]] = {}
    for day in DAY_KEYS:
        lanes = lane_data.get(day)
        if not lanes:
            continue

        n_slots = len(slot_cols)
        per_slot: list[dict] = []
        for s in range(n_slots):
            free, res = [], []
            for ln in range(1, total_lanes + 1):
                is_res = lanes.get(ln, [False]*n_slots)
                is_res_val = is_res[s] if s < len(is_res) else False
                (res if is_res_val else free).append(ln)
            per_slot.append({"free": free, "reserved": res,
                              "from": slot_cols[s][1], "to": slot_cols[s][2]})

        # Merge consecutive identical blocks
        blocks: list[dict] = []
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
                "note": "" if not cur["reserved"] else "Rezervováno",
            })
            i = j

        result[day] = blocks
        print(f"  {day}: {len(blocks)} bloků, "
              f"{sum(len(b['reserved_lanes']) for b in blocks)} rezervací")

    return result


def main():
    lanes_path = Path(__file__).parent.parent / "data" / "lanes.json"
    existing = json.loads(lanes_path.read_text("utf-8")) if lanes_path.exists() \
               else {"updated_at": "", "pools": {}}
    existing.setdefault("pools", {})

    podoli_data = {}
    for pool_id, cfg in POOLS.items():
        print(f"[Podolí] {cfg['name']}…")
        html = fetch_html(cfg["url"])
        schedule = {}
        if html:
            schedule = parse_sheet(html, cfg["total_lanes"])
        if not any(schedule.values()):
            print(f"  → fallback", file=sys.stderr)
            schedule = make_fallback(cfg["total_lanes"])
        else:
            print(f"  → OK")
        podoli_data[pool_id] = {
            "name": cfg["name"],
            "total_lanes": cfg["total_lanes"],
            "seasonal": cfg["seasonal"],
            "schedule": schedule,
        }

    existing["pools"]["podoli"] = podoli_data
    existing["updated_at"] = datetime.now(PRAGUE_TZ).isoformat()
    lanes_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), "utf-8")
    print("[Podolí] Done.")


if __name__ == "__main__":
    main()
