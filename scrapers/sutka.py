"""
scrapers/sutka.py
URL: sutka.eu/course/
Tabulka struktura (z raw HTML):
  Řádek 0: hodiny (6:00 .. 21:00), každá hodina = 4 sloupce (15min)
  Řádky 1+: data
    - první <td> buňka: "ÚT 19.5." nebo prázdná (rowspan pro den)
    - druhá <td> buňka: číslo dráhy "1".."8"
    - zbývající <td>: každá = 1 slot (15min)
                      prázdná td = volno
                      td s <a href="/kurz/..."> = rezervováno
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

PRAGUE_TZ   = timezone(timedelta(hours=2))
URL         = "https://www.sutka.eu/course/"
TOTAL_LANES = 8
SLOT_MIN    = 15
START_HOUR  = 6
END_HOUR    = 22
DAY_KEYS    = ["po","ut","st","ct","pa","so","ne"]
CZ_DAYS     = {
    "po":"po","pon":"po","pond":"po","pondělí":"po","pondeli":"po",
    "út":"ut","ute":"ut","úterý":"ut","utery":"ut","út":"ut",
    "st":"st","stř":"st","středa":"st","streda":"st",
    "čt":"ct","čtv":"ct","čtvrtek":"ct","ctvrtek":"ct",
    "pá":"pa","pát":"pa","pátek":"pa","patek":"pa",
    "so":"so","sob":"so","sobota":"so",
    "ne":"ne","ned":"ne","neděle":"ne","nedele":"ne",
}
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "cs-CZ,cs;q=0.9",
}

TOTAL_SLOTS = (END_HOUR - START_HOUR) * (60 // SLOT_MIN)  # 64


def fetch() -> str | None:
    try:
        r = requests.get(URL, headers=HEADERS, timeout=15)
        r.raise_for_status()
        return r.text
    except Exception as e:
        print(f"[Šutka] HTTP error: {e}", file=sys.stderr)
        return None


def cell_is_reserved(td) -> bool:
    """True if td contains any <a> tag (= reserved by club)."""
    return bool(td.find("a"))


def detect_day(text: str) -> str | None:
    """Extract day key from cell text like 'ÚT 19.5.' or 'PO'."""
    for word in re.split(r"[\s\xa0]+", text.lower()):
        word = re.sub(r"[^\w]", "", word)
        if word in CZ_DAYS:
            return CZ_DAYS[word]
    return None


def parse(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        print("[Šutka] No table found", file=sys.stderr)
        return {}

    rows = table.find_all("tr")
    print(f"[Šutka] Rows: {len(rows)}")

    # lane_data[day][lane] = list of booleans (reserved per slot)
    lane_data: dict[str, dict[int, list[bool]]] = {}
    current_day = None

    for row in rows[1:]:  # skip header
        tds = row.find_all("td")
        if len(tds) < 3:
            continue

        # Try to read day from first td (may be empty due to rowspan)
        first_text = tds[0].get_text(separator=" ", strip=True)
        day = detect_day(first_text)
        if day:
            current_day = day
            if current_day not in lane_data:
                lane_data[current_day] = {}
            # lane number is in second td
            lane_text = tds[1].get_text(strip=True)
            slot_tds = tds[2:]
        elif current_day:
            # first td empty (rowspan) — lane in first td or second
            lane_text = tds[0].get_text(strip=True)
            if lane_text.isdigit() and 1 <= int(lane_text) <= TOTAL_LANES:
                slot_tds = tds[1:]
            else:
                lane_text = tds[1].get_text(strip=True)
                slot_tds = tds[2:]
        else:
            continue

        if not lane_text.isdigit():
            continue
        lane_num = int(lane_text)
        if not (1 <= lane_num <= TOTAL_LANES):
            continue

        reserved = [cell_is_reserved(td) for td in slot_tds[:TOTAL_SLOTS]]
        # Pad to TOTAL_SLOTS if short
        reserved += [False] * (TOTAL_SLOTS - len(reserved))
        lane_data[current_day][lane_num] = reserved

    if not lane_data:
        print("[Šutka] No lane data found", file=sys.stderr)
        return {}

    # Convert to slot blocks
    result: dict[str, list[dict]] = {}
    for day in DAY_KEYS:
        lanes = lane_data.get(day)
        if not lanes:
            continue

        per_slot = []
        for s in range(TOTAL_SLOTS):
            free, res = [], []
            for ln in range(1, TOTAL_LANES + 1):
                lr = lanes.get(ln, [False] * TOTAL_SLOTS)
                (res if (s < len(lr) and lr[s]) else free).append(ln)
            mins_from = START_HOUR * 60 + s * SLOT_MIN
            mins_to   = mins_from + SLOT_MIN
            per_slot.append({
                "free": free, "reserved": res,
                "from": f"{mins_from//60:02d}:{mins_from%60:02d}",
                "to":   f"{mins_to//60:02d}:{mins_to%60:02d}",
            })

        # Merge identical consecutive slots
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

        result[day] = blocks
        free_total = sum(len(b["free_lanes"]) * (
            (int(b["to"][:2])*60+int(b["to"][3:])) -
            (int(b["from"][:2])*60+int(b["from"][3:]))
        ) // SLOT_MIN for b in blocks)
        print(f"[Šutka]   {day}: {len(blocks)} bloků")

    return result


def main():
    lanes_path = Path(__file__).parent.parent / "data" / "lanes.json"
    existing = json.loads(lanes_path.read_text("utf-8")) if lanes_path.exists() \
               else {"updated_at": "", "pools": {}}

    print("[Šutka] Fetching…")
    html = fetch()
    if not html:
        print("[Šutka] Failed to fetch", file=sys.stderr)
        return

    schedule = parse(html)
    if not schedule:
        print("[Šutka] No data — keeping existing", file=sys.stderr)
        return

    existing.setdefault("pools", {})["sutka"] = {
        "50m": {
            "name": "50m bazén",
            "total_lanes": TOTAL_LANES,
            "schedule": schedule,
        }
    }
    existing["updated_at"] = datetime.now(PRAGUE_TZ).isoformat()
    lanes_path.write_text(
        json.dumps(existing, ensure_ascii=False, indent=2), "utf-8"
    )
    print("[Šutka] Done.")


if __name__ == "__main__":
    main()
