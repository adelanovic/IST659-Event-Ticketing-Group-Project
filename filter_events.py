"""
Pick selected fields out of data/raw_events.json into a flat CSV, one row per event.
Events missing any selected field are dropped, so every column is 100% populated.

    python fetch_ticketmaster.py     (download + normalized tables in data/csv/)
    python filter_events.py          (this flat, one-row-per-event view)

To change what's kept, edit FIELDS below. Paths come from docs/event_data_dictionary.csv;
"venue." means the event's venue and "classification." its primary classification.
"""

import csv
import json
import sys
from pathlib import Path

SRC = Path(__file__).parent / "data" / "raw_events.json"
OUT = Path(__file__).parent / "data" / "events_filtered.csv"

# output column -> path in the event JSON
FIELDS = {
    "event_id": "id",
    "event_name": "name",
    "event_url": "url",
    "start_local_date": "dates.start.localDate",
    "start_local_time": "dates.start.localTime",
    "status": "dates.status.code",
    "public_sale_start": "sales.public.startDateTime",
    "public_sale_end": "sales.public.endDateTime",
    "segment_id": "classification.segment.id",
    "segment": "classification.segment.name",
    "genre_id": "classification.genre.id",
    "genre": "classification.genre.name",
    "venue_id": "venue.id",
    "venue_name": "venue.name",
    "venue_address": "venue.address.line1",
    "venue_city": "venue.city.name",
    "venue_state": "venue.state.stateCode",
    "venue_postal_code": "venue.postalCode",
    "venue_country": "venue.country.countryCode",
    "venue_latitude": "venue.location.latitude",
    "venue_longitude": "venue.location.longitude",
    "venue_timezone": "venue.timezone",
}


def get(event, path):
    """Follow a dotted path; returns None if any step is missing."""
    head, _, rest = path.partition(".")
    if head == "venue":
        obj = (event.get("_embedded", {}).get("venues") or [None])[0]
    elif head == "classification":
        classes = event.get("classifications") or []
        obj = next((c for c in classes if c.get("primary")), classes[0] if classes else None)
    else:
        obj, rest = event, path
    for key in rest.split("."):
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def main():
    pages = json.loads(SRC.read_text(encoding="utf-8"))
    # fetch_ticketmaster.py's date windows can overlap, so keep each event id once
    unique = {}
    for p in pages:
        for e in p.get("_embedded", {}).get("events", []):
            unique.setdefault(e["id"], e)
    events = list(unique.values())

    rows, dropped = [], {}
    for e in events:
        if e.get("test"):
            continue
        row = {col: get(e, path) for col, path in FIELDS.items()}
        missing = [col for col, val in row.items() if val in (None, "")]
        if missing:
            for col in missing:
                dropped[col] = dropped.get(col, 0) + 1
            continue
        rows.append(row)

    if not rows:
        sys.exit("No events have all the selected fields.")
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Kept {len(rows)} of {len(events)} events -> {OUT}")
    for col, n in sorted(dropped.items(), key=lambda x: -x[1]):
        print(f"  dropped {n} missing {col}")


if __name__ == "__main__":
    main()
