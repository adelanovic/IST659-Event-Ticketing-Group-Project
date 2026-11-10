"""Load ../data/csv into the website's database for development.

    .venv/Scripts/python load_ticketmaster_data.py

Does nothing if venues already has rows. For the class submission the team's
UP/DOWN SQL file loads this data instead (sql/insert_ticketmaster_data.sql).
"""

import csv
from datetime import date, time
from pathlib import Path

from sqlalchemy import func, insert, select

from db import artists, engine, event_artists, events, genres, init_db, venues

CSV_DIR = Path(__file__).resolve().parent.parent / "data" / "csv"
INT_COLS = {"genre_id", "venue_id", "artist_id", "event_id", "billing_order"}

# Parents before children so foreign keys resolve.
LOAD_ORDER = [("genres", genres), ("venues", venues), ("artists", artists),
              ("events", events), ("event_artists", event_artists)]


def convert(col, value):
    if value == "":
        return None
    if col in INT_COLS:
        return int(value)
    if col == "event_date":
        return date.fromisoformat(value)
    if col == "event_time":
        return time.fromisoformat(value)
    return value


def main():
    init_db()
    with engine.begin() as conn:
        if conn.execute(select(func.count()).select_from(venues)).scalar_one():
            print("venues already has data; nothing loaded.")
            return
        for name, table in LOAD_ORDER:
            with open(CSV_DIR / f"{name}.csv", newline="", encoding="utf-8") as f:
                rows = [{c: convert(c, v) for c, v in r.items()} for r in csv.DictReader(f)]
            for start in range(0, len(rows), 500):
                conn.execute(insert(table), rows[start:start + 500])
            print(f"  {name:<14}{len(rows):>6} rows")
    print("Loaded Ticketmaster data.")


if __name__ == "__main__":
    main()
