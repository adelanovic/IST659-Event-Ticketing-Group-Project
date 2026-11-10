"""End-to-end tests of registration, login and the venue pages.

    .venv\\Scripts\\python -m pytest
"""

import sqlite3

import pytest
from argon2 import PasswordHasher
from sqlalchemy import select

import db
from conftest import PASSWORD, csrf, log_in, log_out, register_customer, register_venue


def page(client, path):
    return client.get(path).get_data(as_text=True)


# --- Customers ---------------------------------------------------------------

def test_customer_registration_stores_hash_not_password(client):
    r = register_customer(client, email="Ada@Example.com")
    assert r.status_code == 302 and r.headers["Location"] == "/account"

    with db.engine.connect() as conn:
        acct = conn.execute(select(db.accounts)).one()
        cust = conn.execute(select(db.customers)).one()
    assert acct.email == "ada@example.com"            # stored lowercase
    assert acct.account_type == "customer"
    assert acct.password_hash.startswith("$argon2id$")
    assert PASSWORD not in acct.password_hash
    assert cust.account_id == acct.account_id

    html = page(client, "/account")
    assert "Ada Lovelace" in html and PASSWORD not in html


def test_duplicate_email_rejected_ignoring_case(client):
    register_customer(client)
    log_out(client)
    r = register_customer(client, email="ADA@example.com")
    assert r.status_code == 400
    assert "already exists" in r.get_data(as_text=True)


def test_customer_form_validation(client):
    r = register_customer(client, first_name="", email="not-an-email", phone="call me",
                          password="short", confirm_password="different")
    assert r.status_code == 400
    assert r.get_data(as_text=True).count('class="error"') == 5


# --- Login ---------------------------------------------------------------------

def test_wrong_password_and_unknown_email_look_the_same(client):
    register_customer(client)
    log_out(client)
    wrong = log_in(client, "ada@example.com", "WrongPass123")
    unknown = log_in(client, "nobody@example.com", "WrongPass123")
    assert wrong.status_code == unknown.status_code == 401
    message = "match an account."   # the page HTML-escapes the apostrophe in "don't"
    assert message in wrong.get_data(as_text=True)
    assert message in unknown.get_data(as_text=True)


def test_login_sends_each_account_type_home(client):
    register_customer(client)
    log_out(client)
    assert log_in(client, " ADA@example.com ").headers["Location"] == "/account"
    log_out(client)
    register_venue(client, choice="1")
    log_out(client)
    assert log_in(client, "booking@venue.test").headers["Location"] == "/venue"


def test_pages_are_limited_to_their_account_type(client):
    assert client.get("/account").headers["Location"].startswith("/login")
    register_customer(client)
    assert client.get("/venue").headers["Location"] == "/account"
    log_out(client)
    register_venue(client, choice="1")
    assert client.get("/account").headers["Location"] == "/venue"


@pytest.mark.parametrize("next_url", ["https://evil.example", "//evil.example"])
def test_login_never_redirects_off_site(client, next_url):
    register_customer(client)
    log_out(client)
    assert log_in(client, "ada@example.com", next_url=next_url).headers["Location"] == "/account"


# --- CSRF ----------------------------------------------------------------------

def test_post_with_wrong_token_rejected(client):
    csrf(client)   # starts a session with a real token
    r = client.post("/register", data={"csrf_token": "forged", "first_name": "Eve"})
    assert r.status_code == 400


def test_post_with_empty_token_and_no_session_rejected(client):
    # Regression test: "" == "" used to pass the check for visitors without a session.
    r = client.post("/register", data={
        "csrf_token": "", "first_name": "Eve", "last_name": "X", "email": "eve@example.com",
        "phone": "", "password": PASSWORD, "confirm_password": PASSWORD})
    assert r.status_code == 400


# --- Venues --------------------------------------------------------------------

def test_venue_search_lists_unclaimed_matches_with_event_counts(client):
    html = page(client, "/register/venue?q=fillmore")
    assert "The Fillmore" in html and "Fillmore Auditorium" in html
    assert "2 events" in html and "0 events" in html


def test_venue_search_treats_wildcards_as_text(client):
    assert "No unclaimed venues match" in page(client, "/register/venue?q=%25%25")


def test_claiming_a_venue_brings_its_events(client):
    r = register_venue(client, choice="1")
    assert r.status_code == 302 and r.headers["Location"] == "/venue"
    html = page(client, "/venue")
    assert "The Fillmore" in html
    assert "Big Rock Night" in html and "Headliner Band · Opening Act" in html   # billing order
    assert "Matinee" in html and "TBA" in html                                   # no start time


def test_claimed_venue_disappears_and_cannot_be_claimed_twice(client):
    register_venue(client, choice="1")
    log_out(client)
    assert 'value="1"' not in page(client, "/register/venue?q=fillmore")
    r = register_venue(client, email="other@venue.test", choice="1")
    assert r.status_code == 400 and "just claimed" in r.get_data(as_text=True)
    with db.engine.connect() as conn:   # the failed attempt left no account behind
        assert conn.execute(select(db.accounts.c.email)).scalars().all() == ["booking@venue.test"]


def test_adding_a_new_venue(client):
    r = register_venue(client, choice="new", new_name="The Westcott Theater",
                       new_address="524 Westcott St", new_city="Syracuse", new_state="ny",
                       new_postal="13210")
    assert r.status_code == 302
    html = page(client, "/venue")
    assert "Syracuse, NY 13210" in html and "added on this site" in html
    with db.engine.connect() as conn:
        venue = conn.execute(select(db.venues).where(db.venues.c.tm_venue_id.is_(None))).one()
    assert venue.state == "NY" and venue.account_id is not None


def test_venue_form_validation(client):
    r = register_venue(client, choice="new", new_name="", new_city="", new_state="New York",
                       new_postal="abc")
    assert r.status_code == 400
    assert r.get_data(as_text=True).count('class="error"') == 4
    r = register_venue(client, choice="")
    assert r.status_code == 400 and "Pick your venue" in r.get_data(as_text=True)


def test_email_cannot_be_both_customer_and_venue(client):
    register_customer(client)
    log_out(client)
    r = register_venue(client, email="ada@example.com", choice="1")
    assert r.status_code == 400 and "already exists" in r.get_data(as_text=True)


# --- Migration from the first version ------------------------------------------

def test_old_customers_table_is_migrated_without_losing_logins(fresh_db):
    with fresh_db.begin() as conn:
        db.metadata.drop_all(conn)
    path = str(fresh_db.url.database)
    old_hash = PasswordHasher().hash("OldPass123!")
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE customers (customer_id INTEGER PRIMARY KEY, email TEXT UNIQUE "
                  "NOT NULL, password_hash TEXT NOT NULL, first_name TEXT NOT NULL, last_name TEXT "
                  "NOT NULL, phone TEXT, created_at DATETIME NOT NULL, last_login_at DATETIME, "
                  "is_active BOOLEAN NOT NULL)")
        c.execute("INSERT INTO customers VALUES (1, 'old@example.com', ?, 'Old', 'User', NULL, "
                  "'2026-11-09 10:00:00.000000', NULL, 1)", (old_hash,))

    db.init_db()

    with fresh_db.connect() as conn:
        acct = conn.execute(select(db.accounts)).one()
        cust = conn.execute(select(db.customers)).one()
    assert (acct.email, acct.password_hash, acct.account_type) == ("old@example.com", old_hash, "customer")
    assert (cust.first_name, cust.account_id) == ("Old", acct.account_id)
