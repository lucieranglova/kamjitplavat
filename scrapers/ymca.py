"""
scrapers/ymca.py
YMCA zveřejňuje rozvrh jako Google Calendar.
Stahujeme iCal feed a parsujeme události pro aktuální týden.

iCal URL: https://calendar.google.com/calendar/ical/
  k8pk9qrn84290s101ss3mn7eqk%40group.calendar.google.com/public/basic.ics

Každá událost = rezervace (bazén zavřený pro veřejnost nebo klub).
Volné časy = mezery mezi událostmi v rámci otevírací doby.

YMCA má jen 3 dráhy, otevírací doba:
  Po-Čt: 6:30-22:00, Pá: 6:30-21:00, So: 9:00-17:00, Ne: 13:00-21:00
"""
import json, re, sys
from datetime import datetime, date, timedelta, timezone, time
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit(1)

PRAGUE_TZ = timezone(timedelta(hours=2))
ICAL_URL = (
    "https://calendar.google.com/calendar/ical/"
    "k8pk9qrn84290s101ss3mn7eqk%40group.calendar.google.com/public/basic.ics"
)
HEADERS = {
    "User-Agent": "Mozilla/5.0 Chrome/124.0.0.0"
}

TOTAL_LANES = 3
DAY_KEYS = ["po","ut","st","ct","pa","so","ne"]

# Opening hours per day (from pools.json)
OPEN_HOURS = {
    "po": (time(6,30),  time(22,0)),
    "ut": (time(6,30),  time(22,0)),
    "st": (time(6,30),  time(22,0)),
    "ct": (time(6,30),  time(22,0)),
    "pa": (time(6,30),  time(21,0)),
    "so": (time(9,0),   time(17,0)),
    "ne": (time(13,0),  time(21,0)),
}

def fetch_ical() -> str | None:
    try:
        r = requests.get(ICAL_URL, headers=HEADERS, timeout=15)
        r.raise_for_status()
        print(f"  iCal len={len(r.text)}")
        return r.text
    except Exception as e:
        print(f"  fetch error: {e}", file=sys.stderr)
        return None


def parse_ical_datetime(s: str) -> datetime | None:
    """Parse iCal datetime string to datetime with Prague timezone."""
    s = s.strip()
    try:
        if "T" in s:
            if s.endswith("Z"):
                dt = datetime.strptime(s, "%Y%m%dT%H%M%SZ")
                dt = dt.replace(tzinfo=timezone.utc).astimezone(PRAGUE_TZ)
            else:
                dt = datetime.strptime(s.rstrip("Z"), "%Y%m%dT%H%M%S")
                dt = dt.replace(tzinfo=PRAGUE_TZ)
        else:
            dt = datetime.strptime(s, "%Y%m%d")
            dt = dt.replace(tzinfo=PRAGUE_TZ)
        return dt
    except Exception:
        return None


def parse_ical(text: str) -> list[dict]:
    """Parse iCal text → list of {summary, start, end} dicts."""
    events = []
    current = {}
    for line in text.splitlines():
        # Handle line folding (continuation lines start with space/tab)
        if line.startswith((" ", "\t")) and current:
            last_key = list(current.keys())[-1]
            current[last_key] += line[1:]
            continue
        if line.startswith("BEGIN:VEVENT"):
            current = {}
        elif line.startswith("END:VEVENT"):
            if "DTSTART" in current and "DTEND" in current:
                start = parse_ical_datetime(current["DTSTART"].split(":")[-1])
                end   = parse_ical_datetime(current["DTEND"].split(":")[-1])
                if start and end:
                    events.append({
                        "summary": current.get("SUMMARY","").split(":")[-1].strip(),
                        "start": start,
                        "end": end,
                    })
            current = {}
        elif ":" in line and current is not None:
            key, _, val = line.partition(":")
            # Strip params like DTSTART;TZID=...
            key = key.split(";")[0]
            current[key] = val

    print(f"  Parsed {len(events)} events total")
    return events


def classify_event(summary: str) -> dict:
    """
    Classify event by summary name.
    Returns dict with type and lane counts.
    
    Known types:
    - "Veřejnost/ Public swimming" → all 3 lanes free (volno)
    - "1 dráha Veřejnost" → 1 free lane, 2 reserved
    - "2 dráhy Veřejnost" → 2 free lanes, 1 reserved
    - "Aquaerobik", "FLOAT", "Floatfit", others → all 3 reserved (klub)
    """
    s = summary.lower()
    
    # Check for "X drah(y) veřejnost" pattern
    m = re.search(r"(\d+)\s*dráh", s)
    if m:
        free_count = int(m.group(1))
        reserved_count = TOTAL_LANES - free_count
        return {
            "type": "volno" if reserved_count == 0 else "smisene",
            "free_lanes": list(range(1, free_count + 1)),
            "reserved_lanes": list(range(free_count + 1, TOTAL_LANES + 1)),
        }
    
    # Full public swimming
    if "veřejnost" in s or "public swimming" in s or "verejnost" in s:
        return {
            "type": "volno",
            "free_lanes": list(range(1, TOTAL_LANES + 1)),
            "reserved_lanes": [],
        }
    
    # Everything else = reservation
    return {
        "type": "klub",
        "free_lanes": [],
        "reserved_lanes": list(range(1, TOTAL_LANES + 1)),
    }


def events_to_schedule(events: list[dict]) -> dict:
    """
    Convert events to schedule format for current week.
    Events are already classified — volno events show open lanes,
    klub events show reserved lanes.
    Gaps between events = closed (no block shown).
    """
    today = datetime.now(PRAGUE_TZ).date()
    monday = today - timedelta(days=today.weekday())

    schedule: dict[str, list[dict]] = {}

    for day_offset, day_key in enumerate(DAY_KEYS):
        day_date = monday + timedelta(days=day_offset)
        open_t, close_t = OPEN_HOURS.get(day_key, (time(6, 0), time(22, 0)))
        open_dt  = datetime.combine(day_date, open_t,  tzinfo=PRAGUE_TZ)
        close_dt = datetime.combine(day_date, close_t, tzinfo=PRAGUE_TZ)

        # Get events for this day, sorted by start
        day_events = []
        for e in events:
            if e["start"].date() != day_date:
                continue
            ev_start = max(e["start"], open_dt)
            ev_end   = min(e["end"],   close_dt)
            if ev_start < ev_end:
                classification = classify_event(e["summary"])
                day_events.append({
                    "start": ev_start,
                    "end":   ev_end,
                    "summary": e["summary"],
                    **classification,
                })
        day_events.sort(key=lambda x: x["start"])

        if not day_events:
            continue

        blocks = []
        for ev in day_events:
            blocks.append({
                "from": ev["start"].strftime("%H:%M"),
                "to":   ev["end"].strftime("%H:%M"),
                "type": ev["type"],
                "free_lanes": ev["free_lanes"],
                "reserved_lanes": ev["reserved_lanes"],
                "note": ev["summary"] if ev["type"] == "klub" else "",
            })

        schedule[day_key] = blocks
        res = sum(1 for b in blocks if b["type"] == "klub")
        volno = sum(1 for b in blocks if b["type"] == "volno")
        print(f"  {day_key}: {len(blocks)} bloků ({res} rez., {volno} volno)")

    return schedule


def main():
    lanes_path = Path(__file__).parent.parent / "data" / "lanes.json"
    existing = json.loads(lanes_path.read_text("utf-8")) if lanes_path.exists() \
               else {"updated_at": "", "pools": {}}
    existing.setdefault("pools", {})

    print("[YMCA] Fetching iCal…")
    ical = fetch_ical()
    if not ical:
        print("[YMCA] No data", file=sys.stderr)
        return

    events = parse_ical(ical)
    # Debug: show all unique summaries
    summaries = sorted(set(e["summary"] for e in events))
    print(f"  Unique summaries ({len(summaries)}):")
    for s in summaries:
        print(f"    '{s}'")
    schedule = events_to_schedule(events)

    if not schedule:
        print("[YMCA] No schedule", file=sys.stderr)
        return

    existing["pools"]["ymca"] = {
        "25m": {
            "name": "Podzemní 25m",
            "total_lanes": TOTAL_LANES,
            "schedule": schedule,
        }
    }
    existing["updated_at"] = datetime.now(PRAGUE_TZ).isoformat()
    lanes_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), "utf-8")
    print("[YMCA] Done.")


if __name__ == "__main__":
    main()
