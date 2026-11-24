"""
Pull concert data from the Ticketmaster Discovery API, save the raw responses,
and write the CSV tables for a simple ticketing database:
genres, venues, artists, events, event_artists.

Setup:
    1. Put your API key (the "Consumer Key" from developer.ticketmaster.com)
       in a file named .env next to this script:
           TICKETMASTER_API_KEY=your_key_here
    2. pip install requests

Usage:
    python fetch_ticketmaster.py                          # US music events, up to 1000
    python fetch_ticketmaster.py --city "New York" --max-events 2000
    python fetch_ticketmaster.py --days 90 --per-day 30   # random sample from each of the next 90 days
    python fetch_ticketmaster.py --days 90 --per-day 30 --complete --target 2566
                                  # same, but only events with named, genred performers
    python fetch_ticketmaster.py --no-fetch               # rebuild CSVs from saved raw JSON

Output:
    data/raw_events.json  - list of the unmodified API responses, one per page.
                            Events are in each page's ["_embedded"]["events"].
    data/csv/*.csv        - one CSV per table (see TABLES below for columns).

Ticketmaster only lets one query page through ~1000 results (page * size < 1000).
To go past that, this script restarts the query from the date of the last event
it received, and drops the duplicates that overlap between windows.
"""

import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

BASE_URL = "https://app.ticketmaster.com/discovery/v2/events.json"
ROOT = Path(__file__).parent
RAW_FILE = ROOT / "data" / "raw_events.json"
CSV_DIR = ROOT / "data" / "csv"

MAX_PAGE_SIZE = 200
MAX_DEPTH = 1000          # API rejects page * size >= 1000
REQUEST_GAP = 0.25        # API allows 5 requests/second
MAX_RETRIES = 5


# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #

def load_api_key():
    key = os.environ.get("TICKETMASTER_API_KEY")
    env_file = ROOT / ".env"
    if not key and env_file.exists():
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            if line.strip().startswith("TICKETMASTER_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not key:
        sys.exit("No API key found. Add TICKETMASTER_API_KEY=... to a .env file.")
    return key


def get_page(params):
    """GET one page, retrying on rate limits / server errors with backoff."""
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(BASE_URL, params=params, timeout=30)
        except requests.RequestException as exc:
            wait = 2 ** attempt
            print(f"  network error ({exc.__class__.__name__}), retrying in {wait}s...")
            time.sleep(wait)
            continue

        if resp.status_code == 200:
            return resp.json()
        if resp.status_code in (401, 403):
            sys.exit(f"Ticketmaster rejected the API key (HTTP {resp.status_code}). Check .env.")
        if resp.status_code == 429 or resp.status_code >= 500:
            wait = 2 ** attempt
            print(f"  HTTP {resp.status_code}, retrying in {wait}s...")
            time.sleep(wait)
            continue
        # Other 4xx: bad parameters or paging limit. Show Ticketmaster's message.
        try:
            detail = resp.json()["errors"][0]["detail"]
        except (ValueError, KeyError, IndexError, TypeError):
            detail = resp.text[:300]
        raise RuntimeError(f"HTTP {resp.status_code}: {detail}")
    raise RuntimeError(f"Gave up after {MAX_RETRIES} attempts.")


def page_events(page):
    return page.get("_embedded", {}).get("events", [])


def fetch_pages(api_key, args):
    base = {
        "apikey": api_key,
        "classificationName": args.classification,
        "countryCode": args.country,
        "size": args.page_size,
        "sort": "date,asc",
    }
    if args.city:
        base["city"] = args.city
    if args.state:
        base["stateCode"] = args.state
    if args.keyword:
        base["keyword"] = args.keyword

    # Only upcoming events; otherwise date-sorting surfaces stale past listings.
    window_start = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    pages, seen = [], set()
    pages_per_window = -(-MAX_DEPTH // args.page_size)   # ceil: pages 0..n-1 keep page*size < 1000

    while len(seen) < args.max_events:
        print(f"Window starting {window_start}")
        last_dt, window_done = None, False
        for page_num in range(pages_per_window):
            if page_num * args.page_size >= MAX_DEPTH:
                break
            data = get_page({**base, "startDateTime": window_start, "page": page_num})
            pages.append(data)
            events = page_events(data)
            new = [e for e in events if e["id"] not in seen]
            seen.update(e["id"] for e in new)
            for e in events:
                dt = e.get("dates", {}).get("start", {}).get("dateTime")
                if dt and (last_dt is None or dt > last_dt):
                    last_dt = dt

            total_pages = data.get("page", {}).get("totalPages", 0)
            print(f"  page {page_num + 1}: {len(events)} events ({len(new)} new, {len(seen)} total)")
            time.sleep(REQUEST_GAP)
            if page_num + 1 >= total_pages or not events:
                window_done = True   # nothing left for this search at all
                break
            if len(seen) >= args.max_events:
                break

        if window_done or len(seen) >= args.max_events:
            break
        # Slide the window forward. Same-timestamp overlap is removed by `seen`.
        if not last_dt or last_dt <= window_start:
            print("Can't advance past", window_start, "- too many events at one time; stopping.")
            break
        window_start = last_dt
    return pages


def is_complete(event, first_day, last_day):
    """True if the event needs no cleanup: a real venue, a date inside the window, and at
    least one performer, every one of them with a name and a real genre, and not a
    ticket add-on such as a VIP upgrade."""
    if event.get("test") or is_add_on(event):
        return False
    venue = next((v for v in event.get("_embedded", {}).get("venues", []) if not v.get("test")), None)
    if not venue or not dig(venue, "city", "name"):
        return False
    local = dig(event, "dates", "start", "localDate")
    if not local or not first_day <= local <= last_day:
        return False
    performers = [a for a in event.get("_embedded", {}).get("attractions", []) if not a.get("test")]
    return bool(performers) and all((a.get("name") or "").strip() and genre_name(a)
                                    for a in performers)


def show_key(event):
    """Same name, venue, date and start time = the same show, even under two Ticketmaster ids
    (some shows are sold through two of Ticketmaster's ticketing systems)."""
    venue = next((v for v in event.get("_embedded", {}).get("venues", []) if not v.get("test")), {})
    return ((clean(event.get("name")) or "").lower(),
            comparable_name(venue.get("name")),
            (clean(dig(venue, "city", "name")) or "").lower(),
            (clean(dig(venue, "state", "stateCode")) or "").upper(),
            dig(event, "dates", "start", "localDate"),
            dig(event, "dates", "start", "localTime"))


def fetch_by_day(api_key, args):
    """Spread mode: a random sample of up to --per-day events for each of the next --days days.

    With --complete, each day's request asks for 200 events and keeps only complete
    ones (see is_complete). Spare complete events are held back and used to top up
    to --target if some days come up short. The saved raw file then holds only the
    kept events, in the same page shape as the API's responses.
    """
    if args.complete:
        return fetch_complete_by_day(api_key, args)
    base = {
        "apikey": api_key,
        "classificationName": args.classification,
        "countryCode": args.country,
        "size": args.per_day,
        "sort": "random",
    }
    if args.city:
        base["city"] = args.city
    if args.state:
        base["stateCode"] = args.state
    if args.keyword:
        base["keyword"] = args.keyword

    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    pages, seen = [], set()
    for d in range(args.days):
        day = today + timedelta(days=d)
        data = get_page({**base,
                         "startDateTime": day.strftime("%Y-%m-%dT00:00:00Z"),
                         "endDateTime": day.strftime("%Y-%m-%dT23:59:59Z")})
        pages.append(data)
        events = page_events(data)
        new = [e for e in events if e["id"] not in seen]
        seen.update(e["id"] for e in new)
        total = data.get("page", {}).get("totalElements", 0)
        print(f"  {day:%Y-%m-%d}: took {len(new)} of {total} events ({len(seen)} total)")
        time.sleep(REQUEST_GAP)
    return pages


def fetch_complete_by_day(api_key, args):
    base = {
        "apikey": api_key,
        "classificationName": args.classification,
        "countryCode": args.country,
        "size": MAX_PAGE_SIZE,
        "sort": "random",
    }
    for key, arg in (("city", args.city), ("stateCode", args.state), ("keyword", args.keyword)):
        if arg:
            base[key] = arg

    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    first_day = today.strftime("%Y-%m-%d")
    last_day = (today + timedelta(days=args.days - 1)).strftime("%Y-%m-%d")
    spare_per_day = args.per_day   # held back per day for topping up
    kept, spares, seen, shows = [], [], set(), set()
    for d in range(args.days):
        day = today + timedelta(days=d)
        data = get_page({**base,
                         "startDateTime": day.strftime("%Y-%m-%dT00:00:00Z"),
                         "endDateTime": day.strftime("%Y-%m-%dT23:59:59Z")})
        events = [e for e in page_events(data) if e["id"] not in seen]
        good = []
        for ev in events:
            if is_complete(ev, first_day, last_day) and show_key(ev) not in shows:
                shows.add(show_key(ev))
                good.append(ev)
        seen.update(ev["id"] for ev in good)
        kept.append(good[:args.per_day])
        spares.append(good[args.per_day:args.per_day + spare_per_day])
        total = data.get("page", {}).get("totalElements", 0)
        print(f"  {day:%Y-%m-%d}: {len(good)} complete of {len(events)} fetched "
              f"({total} that day), kept {len(kept[-1])}")
        time.sleep(REQUEST_GAP)

    # Top up short days' shortfall from other days' spares, spreading them evenly.
    count = sum(len(k) for k in kept)
    target = args.target or count
    while count < target and any(spares):
        for i, extra in enumerate(spares):
            if extra and count < target:
                kept[i].append(extra.pop(0))
                count += 1
    print(f"Kept {count} complete events (target {target}).")
    return [{"_embedded": {"events": day_events},
             "page": {"note": "filtered by fetch_ticketmaster.py --complete"}}
            for day_events in kept if day_events]


# --------------------------------------------------------------------------- #
# Building the CSV tables
# --------------------------------------------------------------------------- #
#
# Only what a basic ticketing database needs. Every table gets its own integer
# key (1, 2, 3, ...) for use as the SQL primary key. Ticketmaster's own ids are used
# only inside this script (to merge duplicates and link rows) and are not written
# to the CSVs: the data is static, so they aren't needed afterwards.

TABLES = {
    "genres":        ["genre_id", "name"],
    "venues":        ["venue_id", "name", "address", "city", "state", "postal_code"],
    "artists":       ["artist_id", "name", "genre_id"],
    "events":        ["event_id", "name", "venue_id", "event_date", "event_time", "status"],
    "event_artists": ["event_id", "artist_id", "billing_order"],
}

# Ticketmaster placeholder genres that mean "no genre"
NO_GENRE = {"Undefined", "Other", ""}


def dig(obj, *keys):
    for k in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(k)
    return obj


def genre_name(obj):
    """Genre name from the primary classification, or None if it's a placeholder."""
    classes = obj.get("classifications") or []
    primary = next((c for c in classes if c.get("primary")), classes[0] if classes else {})
    name = (dig(primary, "genre", "name") or "").strip()
    return None if name in NO_GENRE else name


def clean(text):
    """Trim and collapse whitespace; None stays None."""
    return " ".join(text.split()) if isinstance(text, str) else text


# Listings that are ticket add-ons, not performances.
ADD_ON = re.compile(r"\b(vip upgrade|upgrade|early entry|parking|voucher|gift card|shuttle|"
                    r"add-?on|locker rental)\b", re.I)


def is_add_on(event):
    return bool(ADD_ON.search(event.get("name") or ""))


def venue_key(v):
    """Same name, city and state = same place, whatever Ticketmaster id it has.
    (Not ZIP: Ticketmaster's duplicate records sometimes carry different ZIPs for
    the same street address.)"""
    return (comparable_name(v["name"]), (clean(v["city"]) or "").lower(),
            (clean(v["state"]) or "").upper())


def comparable_name(name):
    """Lower case, without a leading "The": "The Bluebird Cafe" matches "Bluebird Cafe"."""
    name = (clean(name) or "").lower()
    return name[4:] if name.startswith("the ") else name


def collect(pages):
    """Pull the fields we keep out of the raw pages, keyed by Ticketmaster id.

    Ticketmaster sometimes lists one venue or artist under two ids. Those are merged:
    venues by name + city + state, artists by name (ignoring case) + genre. The first id
    seen is kept, and events and lineups point at it.
    """
    venues, artists, events, links = {}, {}, {}, {}
    venue_alias, artist_alias = {}, {}          # duplicate tm id -> kept tm id
    show_ids = {}                               # (name, venue, date, time) -> kept event id
    venue_by_key, artist_by_key = {}, {}
    merged = {"venues": 0, "artists": 0, "add_ons": 0, "shows": 0}
    for page in pages:
        for e in page_events(page):
            if e.get("test") or e["id"] in events:
                continue
            if is_add_on(e):
                merged["add_ons"] += 1
                continue
            emb = e.get("_embedded", {})
            venue = next((v for v in emb.get("venues", []) if not v.get("test")), None)
            date = dig(e, "dates", "start", "localDate")
            if not venue or not date or not dig(venue, "city", "name"):
                continue   # an event without a real place or a day isn't sellable

            vid = venue_alias.get(venue["id"], venue["id"])
            if vid not in venues:
                record = {
                    "name": clean(venue.get("name")),
                    "address": clean(dig(venue, "address", "line1")),
                    "city": clean(dig(venue, "city", "name")),
                    "state": clean(dig(venue, "state", "stateCode")),
                    "postal_code": clean(venue.get("postalCode")),
                }
                kept = venue_by_key.get(venue_key(record))
                if kept:
                    venue_alias[vid] = vid = kept
                    venues[kept]["address"] = venues[kept]["address"] or record["address"]
                    merged["venues"] += 1
                else:
                    venue_by_key[venue_key(record)] = vid
                    venues[vid] = record

            key = ((clean(e.get("name")) or "").lower(), vid, date,
                   dig(e, "dates", "start", "localTime"))
            if key in show_ids:
                merged["shows"] += 1
                continue
            show_ids[key] = e["id"]
            events[e["id"]] = {
                "name": clean(e.get("name")),
                "tm_venue_id": vid,
                "event_date": date,
                "event_time": dig(e, "dates", "start", "localTime"),
                "status": dig(e, "dates", "status", "code"),
            }
            order = 0
            for a in emb.get("attractions", []):
                name = clean(a.get("name"))
                if a.get("test") or not name:
                    continue
                aid = artist_alias.get(a["id"], a["id"])
                if aid not in artists:
                    key = (name.lower(), genre_name(a))
                    if key in artist_by_key:
                        artist_alias[aid] = aid = artist_by_key[key]
                        merged["artists"] += 1
                    else:
                        artist_by_key[key] = aid
                        artists[aid] = {"name": name, "genre": genre_name(a)}
                if (e["id"], aid) in links:
                    continue
                order += 1
                links[(e["id"], aid)] = order
    print(f"Cleanup: merged {merged['venues']} duplicate venue(s) and {merged['artists']} "
          f"duplicate artist(s); dropped {merged['shows']} repeated show(s) and "
          f"{merged['add_ons']} add-on listing(s).")
    return venues, artists, events, links


def build_tables(pages):
    venues, artists, events, links = collect(pages)

    genre_names = sorted({a["genre"] for a in artists.values() if a["genre"]})
    genre_id = {name: i for i, name in enumerate(genre_names, 1)}

    venue_order = sorted(venues, key=lambda k: (venues[k]["name"] or "", k))
    venue_id = {k: i for i, k in enumerate(venue_order, 1)}

    artist_order = sorted(artists, key=lambda k: (artists[k]["name"] or "", k))
    artist_id = {k: i for i, k in enumerate(artist_order, 1)}

    event_order = sorted(events, key=lambda k: (events[k]["event_date"],
                                                events[k]["event_time"] or "",
                                                events[k]["name"] or "", k))
    event_id = {k: i for i, k in enumerate(event_order, 1)}

    return {
        "genres": [{"genre_id": i, "name": n} for n, i in genre_id.items()],
        "venues": [{"venue_id": venue_id[k], **venues[k]} for k in venue_order],
        "artists": [{"artist_id": artist_id[k], "name": artists[k]["name"],
                     "genre_id": genre_id.get(artists[k]["genre"])} for k in artist_order],
        "events": [{"event_id": event_id[k], "name": events[k]["name"],
                    "venue_id": venue_id[events[k]["tm_venue_id"]],
                    "event_date": events[k]["event_date"], "event_time": events[k]["event_time"],
                    "status": events[k]["status"]} for k in event_order],
        "event_artists": sorted(
            ({"event_id": event_id[ev], "artist_id": artist_id[ar], "billing_order": n}
             for (ev, ar), n in links.items()),
            key=lambda r: (r["event_id"], r["billing_order"])),
    }


def write_csvs(tables):
    CSV_DIR.mkdir(parents=True, exist_ok=True)
    # remove CSVs from older, bigger versions of this script
    for old in CSV_DIR.glob("*.csv"):
        if old.stem not in TABLES:
            old.unlink()
            print(f"  removed old {old.name}")
    for name, cols in TABLES.items():
        with open(CSV_DIR / f"{name}.csv", "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=cols)
            writer.writeheader()
            writer.writerows(tables[name])
        print(f"  {name + '.csv':<20}{len(tables[name]):>6} rows")


# --------------------------------------------------------------------------- #

def main():
    parser = argparse.ArgumentParser(description="Download Ticketmaster events and write normalized CSVs.")
    parser.add_argument("--city", help='e.g. "New York"')
    parser.add_argument("--state", help="two-letter state code, e.g. NY")
    parser.add_argument("--keyword", help="artist or search term")
    parser.add_argument("--country", default="US")
    parser.add_argument("--classification", default="music", help='default "music"')
    parser.add_argument("--max-events", type=int, default=1000, help="stop after this many unique events")
    parser.add_argument("--page-size", type=int, default=MAX_PAGE_SIZE, help=f"1-{MAX_PAGE_SIZE}")
    parser.add_argument("--days", type=int,
                        help="spread mode: sample events from each of the next N days")
    parser.add_argument("--per-day", type=int, default=30,
                        help=f"spread mode: events per day, 1-{MAX_PAGE_SIZE} (default 30)")
    parser.add_argument("--complete", action="store_true",
                        help="spread mode: keep only events needing no cleanup (performers "
                             "with names and genres, date inside the window)")
    parser.add_argument("--target", type=int,
                        help="spread mode with --complete: top up short days to this many events")
    parser.add_argument("--no-fetch", action="store_true",
                        help="skip the API and rebuild CSVs from data/raw_events.json")
    args = parser.parse_args()
    if not 1 <= args.page_size <= MAX_PAGE_SIZE:
        parser.error(f"--page-size must be between 1 and {MAX_PAGE_SIZE}")
    if args.max_events < 1:
        parser.error("--max-events must be at least 1")
    if args.days is not None and not (1 <= args.days <= 365 and 1 <= args.per_day <= MAX_PAGE_SIZE):
        parser.error(f"--days must be 1-365 and --per-day 1-{MAX_PAGE_SIZE}")

    if args.no_fetch:
        if not RAW_FILE.exists():
            sys.exit(f"{RAW_FILE} not found; run without --no-fetch first.")
        pages = json.loads(RAW_FILE.read_text(encoding="utf-8"))
    else:
        try:
            fetch = fetch_by_day if args.days else fetch_pages
            pages = fetch(load_api_key(), args)
        except RuntimeError as exc:
            sys.exit(f"Ticketmaster error: {exc}")
        if not any(page_events(p) for p in pages):
            sys.exit("No events returned. Try a different city/keyword.")
        RAW_FILE.parent.mkdir(parents=True, exist_ok=True)
        RAW_FILE.write_text(json.dumps(pages, indent=2), encoding="utf-8")
        print(f"\nSaved raw responses to {RAW_FILE}")

    tables = build_tables(pages)
    print(f"\nWrote {CSV_DIR}:")
    write_csvs(tables)


if __name__ == "__main__":
    main()
