"""Test setup: a throwaway SQLite database, rebuilt for every test with a few sample rows.

Runs entirely offline; SQL Server is never touched.
"""

import os
import re
import sys
import tempfile
from datetime import date, time
from pathlib import Path

# Must be set before config/db/app are imported: DATABASE_URL wins over .env.
_tmp = Path(tempfile.mkdtemp(prefix="concert_tests_"))
os.environ["DATABASE_URL"] = f"sqlite:///{(_tmp / 'test.db').as_posix()}"
os.environ["SECRET_KEY"] = "test-secret-key"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from sqlalchemy import insert  # noqa: E402

import db  # noqa: E402
from app import app as flask_app  # noqa: E402

PASSWORD = "Concert2026!"

# Two Ticketmaster venues to claim, one with events.
SAMPLE = {
    db.genres: [{"genre_id": 1, "name": "Rock"}],
    db.venues: [
        {"venue_id": 1, "tm_venue_id": "TMV1", "name": "The Fillmore", "address": "1805 Geary Blvd",
         "city": "San Francisco", "state": "CA", "postal_code": "94115"},
        {"venue_id": 2, "tm_venue_id": "TMV2", "name": "Fillmore Auditorium", "address": None,
         "city": "Denver", "state": "CO", "postal_code": "80218"},
    ],
    db.artists: [
        {"artist_id": 1, "tm_attraction_id": "TMA1", "name": "Headliner Band", "genre_id": 1},
        {"artist_id": 2, "tm_attraction_id": "TMA2", "name": "Opening Act", "genre_id": None},
    ],
    db.events: [
        {"event_id": 1, "tm_event_id": "TME1", "name": "Big Rock Night", "venue_id": 1,
         "event_date": date(2026, 12, 5), "event_time": time(20, 0), "status": "onsale"},
        {"event_id": 2, "tm_event_id": "TME2", "name": "Matinee", "venue_id": 1,
         "event_date": date(2026, 12, 6), "event_time": None, "status": "cancelled"},
    ],
    db.event_artists: [
        {"event_id": 1, "artist_id": 1, "billing_order": 1},
        {"event_id": 1, "artist_id": 2, "billing_order": 2},
    ],
}


@pytest.fixture
def fresh_db():
    """Empty schema, no rows."""
    with db.engine.begin() as conn:
        db.metadata.drop_all(conn)
    db.init_db()
    return db.engine


@pytest.fixture
def client(fresh_db):
    """Schema plus the sample venues/events, and a logged-out browser."""
    with fresh_db.begin() as conn:
        for table, rows in SAMPLE.items():
            conn.execute(insert(table), rows)
    flask_app.config["TESTING"] = True
    flask_app.config["TODAY"] = date(2026, 12, 6)   # Matinee (Dec 6) upcoming, Dec 5 show past
    return flask_app.test_client()


def csrf(client, path="/login"):
    """The form token from a page, as a browser would submit it."""
    html = client.get(path).get_data(as_text=True)
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def register_customer(client, email="ada@example.com", **overrides):
    data = {"csrf_token": csrf(client, "/register"), "first_name": "Ada", "last_name": "Lovelace",
            "email": email, "phone": "315-555-0100", "password": PASSWORD,
            "confirm_password": PASSWORD, **overrides}
    return client.post("/register", data=data)


def register_venue(client, email="booking@venue.test", **fields):
    data = {"csrf_token": csrf(client, "/register/venue"), "email": email,
            "password": PASSWORD, "confirm_password": PASSWORD, **fields}
    return client.post("/register/venue", data=data)


def log_in(client, email, password=PASSWORD, next_url=""):
    return client.post("/login", data={"csrf_token": csrf(client), "email": email,
                                       "password": password, "next": next_url})


def log_out(client):
    return client.post("/logout", data={"csrf_token": csrf(client)})
