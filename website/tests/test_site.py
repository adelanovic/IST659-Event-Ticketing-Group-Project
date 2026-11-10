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
    assert r.status_code == 302 and r.headers["Location"] == "/my-shows"

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
    assert log_in(client, " ADA@example.com ").headers["Location"] == "/my-shows"
    log_out(client)
    register_venue(client, choice="1")
    log_out(client)
    assert log_in(client, "booking@venue.test").headers["Location"] == "/venue"


def test_pages_are_limited_to_their_account_type(client):
    assert client.get("/account").headers["Location"].startswith("/login")
    register_customer(client)
    assert client.get("/venue").headers["Location"] == "/my-shows"
    log_out(client)
    register_venue(client, choice="1")
    assert client.get("/account").headers["Location"] == "/venue"


@pytest.mark.parametrize("next_url", ["https://evil.example", "//evil.example"])
def test_login_never_redirects_off_site(client, next_url):
    register_customer(client)
    log_out(client)
    assert log_in(client, "ada@example.com", next_url=next_url).headers["Location"] == "/my-shows"


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


# --- Public venue search -------------------------------------------------------

def test_venue_search_is_public_and_busiest_first(client):
    html = page(client, "/venues")
    assert "2 venues" in html and "busiest first" in html
    assert html.index("The Fillmore") < html.index("Fillmore Auditorium")   # 1 upcoming vs 0
    assert "1</strong> upcoming show" in html and "Next: Sun Dec 6, 2026" in html


@pytest.mark.parametrize("query, expected, absent", [
    ("q=fillmore", ["The Fillmore", "Fillmore Auditorium"], []),
    ("q=denver", ["Fillmore Auditorium"], ["The Fillmore<"]),
    ("state=CA", ["The Fillmore"], ["Fillmore Auditorium"]),
    ("q=fillmore&state=co", ["Fillmore Auditorium"], ["The Fillmore<"]),
])
def test_venue_search_filters(client, query, expected, absent):
    html = page(client, f"/venues?{query}")
    for name in expected:
        assert name in html
    for name in absent:
        assert name not in html


def test_venue_search_no_match_and_wildcards(client):
    assert "No venues match" in page(client, "/venues?q=%25%25")
    assert "No venues match" in page(client, "/venues?q=zzz")
    assert client.get("/venues?page=-5").status_code == 200   # bad page numbers are clamped


def test_venue_detail_splits_upcoming_and_past(client):
    html = page(client, "/venues/1")
    upcoming, past = html.split("Past shows (1)")
    assert "Matinee" in upcoming and "TBA" in upcoming
    assert "Big Rock Night" in past and "Headliner Band · Opening Act" in past


@pytest.mark.parametrize("venue_id", [999, 99999999999])
def test_unknown_venue_is_404(client, venue_id):
    assert client.get(f"/venues/{venue_id}").status_code == 404


# --- Venues listing events -----------------------------------------------------

def list_event(client, **fields):
    data = {"csrf_token": csrf(client, "/venue/events/new"), "name": "Holiday Show",
            "event_date": "2026-12-20", "event_time": "19:30", "performers": "", "genre_id": "",
            **fields}
    return client.post("/venue/events/new", data=data)


def test_only_venues_can_list_events(client):
    assert client.get("/venue/events/new").headers["Location"].startswith("/login")
    register_customer(client)
    assert client.get("/venue/events/new").headers["Location"] == "/my-shows"


def test_listing_an_event_links_existing_and_new_performers(client):
    register_venue(client, choice="2")   # Fillmore Auditorium, no events yet
    r = list_event(client, performers=" headliner   band \nBrand New Artist\nHEADLINER BAND\n\n",
                   genre_id="1")
    assert r.status_code == 302 and r.headers["Location"] == "/venue"

    with db.engine.connect() as conn:
        ev = conn.execute(select(db.events).where(db.events.c.name == "Holiday Show")).one()
        lineup = conn.execute(
            select(db.artists.c.artist_id, db.artists.c.name, db.artists.c.genre_id)
            .join(db.event_artists, db.event_artists.c.artist_id == db.artists.c.artist_id)
            .where(db.event_artists.c.event_id == ev.event_id)
            .order_by(db.event_artists.c.billing_order)).all()
    assert (ev.venue_id, ev.status, str(ev.event_time)) == (2, "onsale", "19:30:00")
    assert ev.tm_event_id is None
    assert lineup[0].artist_id == 1                       # matched "Headliner Band", repeat dropped
    assert (lineup[1].name, lineup[1].genre_id) == ("Brand New Artist", 1)
    assert len(lineup) == 2

    assert "Holiday Show" in page(client, "/venue")
    public = page(client, "/venues/2")
    assert "Holiday Show" in public and "Headliner Band · Brand New Artist" in public


def test_listing_an_event_without_time_or_performers(client):
    register_venue(client, choice="2")
    assert list_event(client, event_time="").status_code == 302
    assert "TBA" in page(client, "/venues/2")


@pytest.mark.parametrize("fields, field_error", [
    ({"name": ""}, "Enter the event name"),
    ({"event_date": ""}, "Pick the date of the show"),
    ({"event_date": "2026-12-01"}, "Pick a date from"),           # before "today" (Dec 6)
    ({"event_date": "2031-01-01"}, "Pick a date from"),           # more than two years out
    ({"event_time": "25:99"}, "Use a time like"),
    ({"performers": "\n".join(f"Act {i}" for i in range(11))}, "at most 10 performers"),
    ({"genre_id": "999"}, "Pick a genre from the list"),
])
def test_list_event_validation(client, fields, field_error):
    register_venue(client, choice="2")
    r = list_event(client, **fields)
    assert r.status_code == 400 and field_error in r.get_data(as_text=True)
    with db.engine.connect() as conn:
        assert conn.execute(select(db.events).where(db.events.c.venue_id == 2)).first() is None


def test_same_event_twice_is_rejected(client):
    register_venue(client, choice="2")
    list_event(client)
    r = list_event(client)
    assert r.status_code == 400 and "already has an event" in r.get_data(as_text=True)


# --- Shows: home, search, show and artist pages ------------------------------

def add_show(event_id, name, venue_id, day, artist_ids=(), status="onsale", when=None):
    """Extra upcoming shows for these tests (the sample has only one)."""
    from datetime import date
    from sqlalchemy import insert
    with db.engine.begin() as conn:
        conn.execute(insert(db.events).values(event_id=event_id, name=name, venue_id=venue_id,
                                              event_date=date(2026, 12, day), event_time=when,
                                              status=status))
        for order, artist_id in enumerate(artist_ids, start=1):
            conn.execute(insert(db.event_artists).values(event_id=event_id, artist_id=artist_id,
                                                         billing_order=order))


@pytest.fixture
def shows(client):
    add_show(10, "Winter Rock Fest", 1, 8, artist_ids=(1, 2))      # Rock headliner, SF
    add_show(11, "Denver Jazz Night", 2, 20, artist_ids=(2,))      # no genre, CO
    add_show(12, "Called Off", 2, 9, status="cancelled")
    return client


def test_home_page_shows_this_week_genres_and_venues(shows):
    html = page(shows, "/")
    week = html.split("On sale this week")[1].split("Browse by genre")[0]
    assert "Winter Rock Fest" in week
    assert "Matinee" not in week and "Called Off" not in week   # cancelled shows aren't highlighted
    assert "Denver Jazz Night" not in week                       # Dec 20 is beyond 7 days
    assert "See all 3 →" in week                                   # links to every show that week
    assert 'href="/events?genre=1"' in html and "Busiest venues" in html
    assert "Upcoming shows</dt><dd>4" in html


def test_show_search_matches_names_artists_venues_and_cities(shows):
    assert "Winter Rock Fest" in page(shows, "/events?q=headliner")   # artist name
    assert "Denver Jazz Night" in page(shows, "/events?q=auditorium")  # venue name
    html = page(shows, "/events?q=san+francisco")                     # city
    assert "Winter Rock Fest" in html and "Denver Jazz Night" not in html
    assert "No shows match" in page(shows, "/events?q=%25")             # wildcard is literal


def test_show_search_filters_and_past_shows_hidden(shows):
    html = page(shows, "/events")
    assert "<strong>4</strong> shows" in html and "Big Rock Night" not in html   # Dec 5 is past
    html = page(shows, "/events?genre=1")
    assert "Rock shows" in html and "Winter Rock Fest" in html and "Matinee" not in html
    html = page(shows, "/events?state=CO")
    assert "Denver Jazz Night" in html and "Winter Rock Fest" not in html
    html = page(shows, "/events?from=2026-12-09&to=2026-12-31")
    assert "Denver Jazz Night" in html and "Called Off" in html and "Winter Rock Fest" not in html
    assert page(shows, "/events?from=junk&to=2026-01-01&genre=abc&page=0")   # bad input is ignored


def test_show_page_links_lineup_and_venue(shows):
    html = page(shows, "/events/10")
    assert "Winter Rock Fest" in html and "Tickets coming soon" in html
    assert 'href="/artists/1"' in html and 'href="/artists/2"' in html
    assert 'href="/venues/1"' in html and "More at The Fillmore" in html and "Matinee" in html
    assert "This show was cancelled." in page(shows, "/events/12")
    assert "already happened" in page(shows, "/events/1")


def test_artist_page_lists_upcoming_shows(shows):
    html = page(shows, "/artists/2")
    assert "Opening Act" in html and "<strong>2</strong> upcoming shows" in html
    assert "Winter Rock Fest" in html and "Denver Jazz Night" in html
    assert "Big Rock Night" not in html                                  # past show


def test_friendly_404_and_current_page_in_menu(shows):
    r = shows.get("/events/999")
    assert r.status_code == 404 and "Browse shows" in r.get_data(as_text=True)
    assert shows.get("/artists/99999999999").status_code == 404
    assert shows.get("/no-such-page").status_code == 404
    html = page(shows, "/events")
    assert 'href="/events" aria-current="page"' in html


# --- Customer profile ---------------------------------------------------------

PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6300010000000500010d0a2db40000000049454e44ae426082")


def upload(client, data, filename="me.png"):
    from io import BytesIO
    return client.post("/account/avatar", data={"csrf_token": csrf(client, "/account"),
                                                "avatar": (BytesIO(data), filename)},
                       content_type="multipart/form-data")


def test_profile_is_personal_not_a_database_dump(client):
    register_customer(client)
    html = page(client, "/account")
    assert "Ada Lovelace" in html and "Member since" in html
    assert 'class="pill-active">Active' in html
    assert "password_hash" not in html and "$argon2id$" not in html
    assert 'class="avatar xl hue-' in html and ">A</span>" in html          # initials badge
    assert 'class="avatar sm hue-' in html                                   # same badge in header


def test_editing_profile_details(client):
    register_customer(client)
    token = csrf(client, "/account")
    r = client.post("/account/profile", data={"csrf_token": token, "first_name": "  Augusta ",
                                              "last_name": "King", "phone": ""})
    assert r.status_code == 302
    html = page(client, "/account")
    assert "Augusta King" in html and "Not added" in html
    assert "<span>Augusta</span>" in html                                    # header name updates
    r = client.post("/account/profile", data={"csrf_token": token, "first_name": "",
                                              "last_name": "King", "phone": "call me"})
    assert r.status_code == 400
    body = r.get_data(as_text=True)
    assert "Enter your first name" in body and "Use digits only" in body and "<details class=\"edit-details\" open" in body


def test_photo_upload_show_and_remove(client):
    register_customer(client)
    assert client.get("/account/avatar").status_code == 404
    assert upload(client, PNG_1PX).status_code == 302
    html = page(client, "/account")
    assert '<img class="avatar xl" src="/account/avatar?v=' in html and "Remove photo" in html
    r = client.get("/account/avatar")
    assert r.data == PNG_1PX and r.mimetype == "image/png"
    assert r.headers["X-Content-Type-Options"] == "nosniff"

    upload(client, PNG_1PX)                                                  # replacing works too
    with db.engine.connect() as conn:
        assert conn.execute(select(db.customer_avatars)).all().__len__() == 1

    client.post("/account/avatar/delete", data={"csrf_token": csrf(client, "/account")})
    assert client.get("/account/avatar").status_code == 404
    assert "Add a photo" in page(client, "/account")


@pytest.mark.parametrize("data, filename, message", [
    (b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>", "x.svg", "PNG, JPEG or WebP"),
    (b"GIF89a....", "fake.png", "PNG, JPEG or WebP"),                       # name says png, bytes don't
    (b"", "empty.png", "Choose a photo"),
])
def test_photo_upload_rejects_non_images(client, data, filename, message):
    register_customer(client)
    r = upload(client, data, filename)
    assert r.status_code == 400 and message in r.get_data(as_text=True)


def test_photo_upload_size_limits(client):
    register_customer(client)
    r = upload(client, PNG_1PX + b"\0" * (2 * 1024 * 1024))                  # just over 2 MB
    assert r.status_code == 400 and "over 2 MB" in r.get_data(as_text=True)
    r = upload(client, PNG_1PX + b"\0" * (4 * 1024 * 1024))                  # over the request cap
    assert r.status_code == 302
    assert "too big" in page(client, "/account")


def test_photos_are_private_to_their_owner(client):
    register_customer(client)
    upload(client, PNG_1PX)
    log_out(client)
    assert client.get("/account/avatar").headers["Location"].startswith("/login")
    register_customer(client, email="other@example.com")
    assert client.get("/account/avatar").status_code == 404                  # their own, which is none


# --- My shows ------------------------------------------------------------------

def test_my_shows_is_for_customers_only(client):
    assert client.get("/my-shows").headers["Location"].startswith("/login")
    register_venue(client, choice="2")
    assert client.get("/my-shows").headers["Location"] == "/venue"


def test_my_shows_empty_state_and_menu_link(shows):
    register_customer(shows)
    html = page(shows, "/my-shows")
    assert "Hi, Ada" in html and "No tickets yet" in html and "Find a show" in html
    assert 'href="/my-shows" aria-current="page"' in html              # in the menu, highlighted
    assert "Winter Rock Fest" in html                                     # on-sale suggestion
    assert "Shows you've been to" in page(shows, "/my-shows?tab=past")


def test_my_shows_lists_tickets_by_tab(shows, monkeypatch):
    """The page is ready for orders: feed it tickets in the documented shape."""
    from datetime import date, datetime, time
    import app as app_module
    base = {"event_time": time(20, 0), "status": "onsale", "venue_id": 1,
            "venue_name": "The Fillmore", "city": "San Francisco", "state": "CA",
            "tier_name": "General Admission", "ordered_at": datetime(2026, 11, 1)}
    tickets = [
        {**base, "order_id": 501, "event_id": 10, "event_name": "Winter Rock Fest",
         "event_date": date(2026, 12, 8), "quantity": 2, "total_paid": 118.5},
        {**base, "order_id": 400, "event_id": 1, "event_name": "Big Rock Night",
         "event_date": date(2026, 12, 5), "quantity": 1, "total_paid": 45},
    ]
    monkeypatch.setattr(app_module, "tickets_for_customer", lambda conn, customer_id: tickets)
    register_customer(shows)

    html = page(shows, "/my-shows")
    assert "Upcoming <span>1</span>" in html and "Past <span>1</span>" in html
    assert "Winter Rock Fest" in html.split("On sale this week")[0]
    assert "Order #501" in html and "$118.50" in html and "General Admission" in html
    assert "Order #400" not in html
    past = page(shows, "/my-shows?tab=past")
    assert "Order #400" in past and "is-past" in past and "Order #501" not in past


# --- Fresh start and reset -------------------------------------------------------

def test_demo_accounts_log_in_and_are_not_duplicated(client):
    import load_ticketmaster_data as loader
    for _ in range(2):                                   # second run must skip both
        with db.engine.begin() as conn:
            loader.create_demo_accounts(conn)
    with db.engine.connect() as conn:
        emails = conn.execute(select(db.accounts.c.email)).scalars().all()
    assert sorted(emails) == ["demo.customer@example.com", "demo.venue@example.com"]

    # No Smalls Jazz Club in the sample, so the busiest unclaimed venue is used.
    assert log_in(client, "demo.venue@example.com", "DemoVenue2026!").headers["Location"] == "/venue"
    assert "The Fillmore" in page(client, "/venue")
    log_out(client)
    assert log_in(client, "demo.customer@example.com", "DemoCustomer2026!").headers["Location"] == "/my-shows"


def test_reset_removes_everything(client):
    register_customer(client)
    db.drop_database()
    from sqlalchemy import inspect
    with db.engine.connect() as conn:
        assert inspect(conn).get_table_names() == []


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
