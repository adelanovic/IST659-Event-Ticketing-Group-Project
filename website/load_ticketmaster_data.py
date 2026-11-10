"""Fill an empty database: the Ticketmaster CSVs, plus demo logins if DEMO_ACCOUNTS=1.

    .venv/Scripts/python load_ticketmaster_data.py

Runs automatically when the Docker container starts. Skips the data if venues
already has rows, and skips each demo login that already exists. For the class
submission the team's UP/DOWN SQL file loads this data instead
(sql/insert_ticketmaster_data.sql).
"""

import csv
import os
from datetime import date, datetime, time, timezone
from pathlib import Path

from argon2 import PasswordHasher
from sqlalchemy import case, func, insert, select, update

from db import (accounts, artists, customers, engine, event_artists, events, genres, init_db,
                venues)

# In Docker the CSVs are mounted at CSV_DIR; locally they're in ../data/csv.
CSV_DIR = Path(os.environ.get("CSV_DIR") or Path(__file__).resolve().parent.parent / "data" / "csv")
INT_COLS = {"genre_id", "venue_id", "artist_id", "event_id", "billing_order"}

# Parents before children so foreign keys resolve.
LOAD_ORDER = [("genres", genres), ("venues", venues), ("artists", artists),
              ("events", events), ("event_artists", event_artists)]

# Test logins, recreated on every fresh start. Not for real use.
DEMO_VENUE = {"email": "demo.venue@example.com", "password": "DemoVenue2026!",
              "venue_name": "Smalls Jazz Club"}
DEMO_CUSTOMER = {"email": "demo.customer@example.com", "password": "DemoCustomer2026!",
                 "first_name": "Demo", "last_name": "Customer"}


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


def load_csvs(conn):
    if conn.execute(select(func.count()).select_from(venues)).scalar_one():
        print("Ticketmaster data already loaded.")
        return
    if not (CSV_DIR / "venues.csv").exists():
        print(f"No CSVs found in {CSV_DIR}; skipping the Ticketmaster data.")
        return
    for name, table in LOAD_ORDER:
        with open(CSV_DIR / f"{name}.csv", newline="", encoding="utf-8") as f:
            rows = [{c: convert(c, v) for c, v in r.items()} for r in csv.DictReader(f)]
        for start in range(0, len(rows), 500):
            conn.execute(insert(table), rows[start:start + 500])
        print(f"  {name:<14}{len(rows):>6} rows")
    print("Loaded Ticketmaster data.")


def _new_account(conn, email, password, account_type):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return conn.execute(insert(accounts).values(
        email=email, password_hash=PasswordHasher().hash(password), account_type=account_type,
        created_at=now, is_active=True)).inserted_primary_key[0]


def create_demo_accounts(conn):
    """A demo venue (Smalls Jazz Club, else the busiest unclaimed venue) and a demo customer."""
    existing = set(conn.execute(select(accounts.c.email)).scalars())

    if DEMO_VENUE["email"] not in existing:
        n_events = (select(events.c.venue_id, func.count().label("n"))
                    .group_by(events.c.venue_id).subquery())
        venue = conn.execute(
            select(venues.c.venue_id, venues.c.name)
            .outerjoin(n_events, n_events.c.venue_id == venues.c.venue_id)
            .where(venues.c.account_id.is_(None))
            .order_by(case((venues.c.name == DEMO_VENUE["venue_name"], 0), else_=1),
                      func.coalesce(n_events.c.n, 0).desc(), venues.c.venue_id)).first()
        if venue:
            account_id = _new_account(conn, DEMO_VENUE["email"], DEMO_VENUE["password"], "venue")
            conn.execute(update(venues).where(venues.c.venue_id == venue.venue_id)
                         .values(account_id=account_id))
            print(f"Demo venue login: {DEMO_VENUE['email']} / {DEMO_VENUE['password']} ({venue.name})")

    if DEMO_CUSTOMER["email"] not in existing:
        account_id = _new_account(conn, DEMO_CUSTOMER["email"], DEMO_CUSTOMER["password"], "customer")
        conn.execute(insert(customers).values(
            account_id=account_id, first_name=DEMO_CUSTOMER["first_name"],
            last_name=DEMO_CUSTOMER["last_name"]))
        print(f"Demo customer login: {DEMO_CUSTOMER['email']} / {DEMO_CUSTOMER['password']}")


def main():
    init_db()
    with engine.begin() as conn:
        load_csvs(conn)
        if os.environ.get("DEMO_ACCOUNTS") == "1":
            create_demo_accounts(conn)


if __name__ == "__main__":
    main()
