"""
Registration and login for the concert ticketing site: customers and venues.

    run.bat                       local, then open http://127.0.0.1:5000
    docker compose up -d --build  container, same address

Passwords are hashed with Argon2id before they reach the database; the
password itself is never stored or logged.
"""

import hmac
import os
import re
import secrets
from datetime import date, datetime, timezone
from functools import wraps

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from flask import Flask, abort, flash, redirect, render_template, request, session, url_for
from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import IntegrityError

import config
from db import (accounts, artists, customers, engine, event_artists, events, genres, init_db,
                venues)

app = Flask(__name__)
app.config.update(
    SECRET_KEY=config.SECRET_KEY,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)
hasher = PasswordHasher()   # Argon2id with the library's recommended settings
# Hashed once at startup. Logins for unknown emails are checked against it so they
# take as long as real ones, which stops attackers from probing which emails exist.
DUMMY_HASH = hasher.hash(secrets.token_hex(16))

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^[0-9 +().-]{7,20}$")
STATE_RE = re.compile(r"^[A-Z]{2}$")
ZIP_RE = re.compile(r"^\d{5}(-\d{4})?$")
MIN_PASSWORD, MAX_PASSWORD = 8, 128
HOME_FOR = {"customer": "account", "venue": "venue_home"}
PAGE_SIZE = 20
MAX_INT = 2_147_483_647     # SQL Server INT; bigger ids can't exist and would error


def now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def today():
    """Today's date; tests can pin it with app.config["TODAY"]."""
    return app.config.get("TODAY") or date.today()


@app.context_processor
def template_globals():
    return {"site_name": config.SITE_NAME, "csrf_token": csrf_token,
            "current_name": session.get("display_name"),
            "current_type": session.get("account_type")}


# --- CSRF protection: every POST form carries a token tied to the session -----

def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


@app.before_request
def check_csrf():
    if request.method == "POST":
        sent = request.form.get("csrf_token", "")
        expected = session.get("csrf", "")
        # Both must be present: with no session, "" == "" would otherwise pass.
        if not sent or not expected or not hmac.compare_digest(sent, expected):
            abort(400, "Your form expired. Go back, reload the page and try again.")


def login_required(account_type):
    """Only logged-in accounts of this type; others go to their own home page."""
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if "account_id" not in session:
                flash("Log in to continue.")
                return redirect(url_for("login", next=request.path))
            if session.get("account_type") != account_type:
                return redirect(url_for(HOME_FOR[session["account_type"]]))
            return view(*args, **kwargs)
        return wrapper
    return decorator


def start_session(account_id, account_type, display_name):
    """Fresh session on login, so an old session id can't be reused."""
    session.clear()
    session.update(account_id=account_id, account_type=account_type, display_name=display_name)


# --- Shared form checks ------------------------------------------------------

def check_login_fields(email, password, confirm, errors):
    if not EMAIL_RE.match(email) or len(email) > 255:
        errors["email"] = "Enter an email address like name@example.com."
    if not MIN_PASSWORD <= len(password) <= MAX_PASSWORD:
        errors["password"] = f"Use {MIN_PASSWORD} to {MAX_PASSWORD} characters."
    elif password.lower() == email:
        errors["password"] = "Your password can't be your email address."
    if password != confirm:
        errors["confirm_password"] = "The two passwords don't match."


def new_account(conn, email, password, account_type):
    """Insert the login row; raises IntegrityError if the email is taken."""
    return conn.execute(insert(accounts).values(
        email=email, password_hash=hasher.hash(password), account_type=account_type,
        created_at=now_utc(), last_login_at=now_utc(), is_active=True,
    )).inserted_primary_key[0]


EMAIL_TAKEN = "An account with this email already exists. Log in instead."


# --- Pages -------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        return render_template("register.html", form={}, errors={})

    form = {k: request.form.get(k, "").strip() for k in ("first_name", "last_name", "email", "phone")}
    form["email"] = form["email"].lower()
    password = request.form.get("password", "")

    errors = {}
    if not form["first_name"] or len(form["first_name"]) > 50:
        errors["first_name"] = "Enter your first name (up to 50 characters)."
    if not form["last_name"] or len(form["last_name"]) > 50:
        errors["last_name"] = "Enter your last name (up to 50 characters)."
    if form["phone"] and not PHONE_RE.match(form["phone"]):
        errors["phone"] = "Use digits only, with optional spaces, dashes or a leading +."
    check_login_fields(form["email"], password, request.form.get("confirm_password", ""), errors)
    if errors:
        return render_template("register.html", form=form, errors=errors), 400

    try:
        with engine.begin() as conn:
            account_id = new_account(conn, form["email"], password, "customer")
            conn.execute(insert(customers).values(
                account_id=account_id, first_name=form["first_name"],
                last_name=form["last_name"], phone=form["phone"] or None))
    except IntegrityError:
        errors["email"] = EMAIL_TAKEN
        return render_template("register.html", form=form, errors=errors), 400

    start_session(account_id, "customer", form["first_name"])
    flash(f"Welcome, {form['first_name']}. Your account is ready.")
    return redirect(url_for("account"))


def search_unclaimed_venues(q):
    """Venues nobody has claimed yet whose name or city matches, with their event counts."""
    if len(q) < 2:
        return []
    n_events = (select(events.c.venue_id, func.count().label("n"))
                .group_by(events.c.venue_id).subquery())
    stmt = (select(venues.c.venue_id, venues.c.name, venues.c.address, venues.c.city,
                   venues.c.state, func.coalesce(n_events.c.n, 0).label("event_count"))
            .outerjoin(n_events, n_events.c.venue_id == venues.c.venue_id)
            .where(venues.c.account_id.is_(None))
            .where(venues.c.name.contains(q, autoescape=True)
                   | venues.c.city.contains(q, autoescape=True))
            .order_by(venues.c.name, venues.c.city)
            .limit(25))
    with engine.connect() as conn:
        return conn.execute(stmt).all()


@app.route("/register/venue", methods=["GET", "POST"])
def register_venue():
    q = request.values.get("q", "").strip()[:100]
    if request.method == "GET":
        return render_template("register_venue.html", q=q, results=search_unclaimed_venues(q),
                               form={"choice": ""}, errors={})

    fields = ("email", "choice", "new_name", "new_address", "new_city", "new_state", "new_postal")
    form = {k: request.form.get(k, "").strip() for k in fields}
    form["email"] = form["email"].lower()
    form["new_state"] = form["new_state"].upper()
    password = request.form.get("password", "")

    errors = {}
    check_login_fields(form["email"], password, request.form.get("confirm_password", ""), errors)
    claim_id = int(form["choice"]) if form["choice"].isdigit() else None
    if form["choice"] == "new":
        if not form["new_name"] or len(form["new_name"]) > 100:
            errors["new_name"] = "Enter the venue name (up to 100 characters)."
        if len(form["new_address"]) > 100:
            errors["new_address"] = "Keep the address under 100 characters."
        if not form["new_city"] or len(form["new_city"]) > 50:
            errors["new_city"] = "Enter the city."
        if not STATE_RE.match(form["new_state"]):
            errors["new_state"] = "Use the two-letter state code, e.g. NY."
        if not ZIP_RE.match(form["new_postal"]):
            errors["new_postal"] = "Use a 5-digit ZIP code, e.g. 13244."
    elif claim_id is None:
        errors["choice"] = "Pick your venue from the search results, or choose “My venue isn't listed”."

    venue_name = None
    if not errors:
        try:
            with engine.begin() as conn:
                account_id = new_account(conn, form["email"], password, "venue")
                if claim_id is not None:
                    # Only claims a venue nobody else has; checked and set in one statement.
                    claimed = conn.execute(
                        update(venues)
                        .where(venues.c.venue_id == claim_id, venues.c.account_id.is_(None))
                        .values(account_id=account_id))
                    if claimed.rowcount != 1:
                        raise LookupError
                    venue_name = conn.execute(select(venues.c.name)
                                              .where(venues.c.venue_id == claim_id)).scalar_one()
                else:
                    conn.execute(insert(venues).values(
                        name=form["new_name"], address=form["new_address"] or None,
                        city=form["new_city"], state=form["new_state"],
                        postal_code=form["new_postal"], account_id=account_id))
                    venue_name = form["new_name"]
        except IntegrityError:
            errors["email"] = EMAIL_TAKEN
        except LookupError:
            errors["choice"] = "That venue was just claimed by another account. Search again."

    if errors:
        return render_template("register_venue.html", q=q, results=search_unclaimed_venues(q),
                               form=form, errors=errors), 400

    start_session(account_id, "venue", venue_name)
    flash(f"{venue_name} is registered. This is your venue page.")
    return redirect(url_for("venue_home"))


@app.route("/login", methods=["GET", "POST"])
def login():
    next_url = request.values.get("next", "")
    if not next_url.startswith("/") or next_url.startswith("//"):
        next_url = ""   # only allow redirects within this site

    if request.method == "GET":
        return render_template("login.html", email="", next=next_url, error=None)

    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    with engine.connect() as conn:
        account = conn.execute(select(accounts).where(accounts.c.email == email)).first()

    try:
        hasher.verify(account.password_hash if account else DUMMY_HASH, password)
        ok = account is not None and account.is_active
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        ok = False
    if not ok:
        # Same message either way, so the form doesn't reveal which emails are registered.
        return render_template("login.html", email=email, next=next_url,
                               error="That email and password don't match an account."), 401

    changes = {"last_login_at": now_utc()}
    if hasher.check_needs_rehash(account.password_hash):
        changes["password_hash"] = hasher.hash(password)   # upgrade old hash settings
    with engine.begin() as conn:
        conn.execute(update(accounts).where(accounts.c.account_id == account.account_id)
                     .values(**changes))
        if account.account_type == "customer":
            name = conn.execute(select(customers.c.first_name)
                                .where(customers.c.account_id == account.account_id)).scalar()
        else:
            name = conn.execute(select(venues.c.name)
                                .where(venues.c.account_id == account.account_id)).scalar()

    start_session(account.account_id, account.account_type, name or email)
    return redirect(next_url or url_for(HOME_FOR[account.account_type]))


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You're logged out.")
    return redirect(url_for("index"))


@app.route("/account")
@login_required("customer")
def account():
    with engine.connect() as conn:
        row = conn.execute(
            select(accounts, customers.c.customer_id, customers.c.first_name,
                   customers.c.last_name, customers.c.phone)
            .join(customers, customers.c.account_id == accounts.c.account_id)
            .where(accounts.c.account_id == session["account_id"])).first()
    if row is None:          # account was deleted while logged in
        session.clear()
        return redirect(url_for("login"))
    return render_template("account.html", c=row)


@app.route("/venue")
@login_required("venue")
def venue_home():
    with engine.connect() as conn:
        acct = conn.execute(select(accounts).where(
            accounts.c.account_id == session["account_id"])).first()
        venue = conn.execute(select(venues).where(
            venues.c.account_id == session["account_id"])).first()
        if acct is None or venue is None:
            session.clear()
            return redirect(url_for("login"))
        venue_events = events_at_venue(conn, venue.venue_id)
    return render_template("venue.html", acct=acct, venue=venue, venue_events=venue_events)


MAX_PERFORMERS = 10
MAX_DAYS_AHEAD = 2 * 365


def parse_performers(text_block):
    """One name per line, in billing order; blanks and repeats (ignoring case) dropped."""
    names, seen = [], set()
    for line in text_block.splitlines():
        name = " ".join(line.split())
        if name and name.lower() not in seen:
            seen.add(name.lower())
            names.append(name)
    return names


@app.route("/venue/events/new", methods=["GET", "POST"])
@login_required("venue")
def list_event():
    with engine.connect() as conn:
        venue = conn.execute(select(venues).where(
            venues.c.account_id == session["account_id"])).first()
        genre_list = conn.execute(select(genres).order_by(genres.c.name)).all()
    if venue is None:
        session.clear()
        return redirect(url_for("login"))

    first_day = today()
    last_day = date.fromordinal(first_day.toordinal() + MAX_DAYS_AHEAD)
    ctx = {"venue": venue, "genres": genre_list, "min_date": first_day, "max_date": last_day,
           "max_performers": MAX_PERFORMERS}
    if request.method == "GET":
        return render_template("list_event.html", form={}, errors={}, **ctx)

    form = {k: request.form.get(k, "").strip()
            for k in ("name", "event_date", "event_time", "performers", "genre_id")}
    errors = {}

    name = " ".join(form["name"].split())
    if not name or len(name) > 200:
        errors["name"] = "Enter the event name (up to 200 characters)."

    event_date = None
    try:
        event_date = date.fromisoformat(form["event_date"])
    except ValueError:
        errors["event_date"] = "Pick the date of the show."
    else:
        if not first_day <= event_date <= last_day:
            errors["event_date"] = (f"Pick a date from {first_day:%b} {first_day.day}, {first_day.year} "
                                    f"to {last_day:%b} {last_day.day}, {last_day.year}.")

    event_time = None
    if form["event_time"]:
        try:
            event_time = datetime.strptime(form["event_time"], "%H:%M").time()
        except ValueError:
            errors["event_time"] = "Use a time like 7:30 PM, or leave it blank if it isn't set yet."

    performers = parse_performers(form["performers"])
    if len(performers) > MAX_PERFORMERS:
        errors["performers"] = f"List at most {MAX_PERFORMERS} performers."
    elif any(len(p) > 100 for p in performers):
        errors["performers"] = "Each performer name can be up to 100 characters."

    genre_id = int(form["genre_id"]) if form["genre_id"].isdigit() else None
    if genre_id is not None and genre_id not in {g.genre_id for g in genre_list}:
        errors["genre_id"] = "Pick a genre from the list."

    if not errors:
        with engine.begin() as conn:
            duplicate = conn.execute(select(events.c.event_id).where(
                events.c.venue_id == venue.venue_id, events.c.event_date == event_date,
                events.c.name == name)).first()
            if duplicate:
                errors["name"] = "This venue already has an event with this name on that date."
            else:
                event_id = conn.execute(insert(events).values(
                    name=name, venue_id=venue.venue_id, event_date=event_date,
                    event_time=event_time, status="onsale")).inserted_primary_key[0]
                for order, performer in enumerate(performers, start=1):
                    conn.execute(insert(event_artists).values(
                        event_id=event_id, billing_order=order,
                        artist_id=find_or_add_artist(conn, performer, genre_id)))

    if errors:
        return render_template("list_event.html", form=form, errors=errors, **ctx), 400

    flash(f"“{name}” is listed for {event_date:%a %b} {event_date.day}, {event_date.year}.")
    return redirect(url_for("venue_home"))


def find_or_add_artist(conn, name, genre_id):
    """Reuse an artist with this name (ignoring case); otherwise add one with the chosen genre."""
    existing = conn.execute(select(artists.c.artist_id)
                            .where(func.lower(artists.c.name) == name.lower())
                            .order_by(artists.c.artist_id)).first()
    if existing:
        return existing.artist_id
    return conn.execute(insert(artists).values(name=name, genre_id=genre_id)).inserted_primary_key[0]


def events_at_venue(conn, venue_id):
    """All events at a venue in date order, each with its artists in billing order."""
    rows = conn.execute(
        select(events.c.event_id, events.c.name, events.c.event_date, events.c.event_time,
               events.c.status, artists.c.name.label("artist"))
        .outerjoin(event_artists, event_artists.c.event_id == events.c.event_id)
        .outerjoin(artists, artists.c.artist_id == event_artists.c.artist_id)
        .where(events.c.venue_id == venue_id)
        .order_by(events.c.event_date, events.c.event_time, events.c.event_id,
                  event_artists.c.billing_order)).all()
    by_id = {}
    for r in rows:
        ev = by_id.setdefault(r.event_id, {"name": r.name, "date": r.event_date,
                                           "time": r.event_time, "status": r.status,
                                           "artists": []})
        if r.artist:
            ev["artists"].append(r.artist)
    return list(by_id.values())


# --- Public venue search -------------------------------------------------------

@app.route("/venues")
def venues_search():
    q = request.args.get("q", "").strip()[:100]
    state = request.args.get("state", "").strip().upper()[:2]
    page = max(1, min(request.args.get("page", 1, type=int) or 1, 10_000))

    # Upcoming shows per venue: how many, and the date of the next one.
    upcoming = (select(events.c.venue_id,
                       func.count().label("upcoming"),
                       func.min(events.c.event_date).label("next_date"))
                .where(events.c.event_date >= today())
                .group_by(events.c.venue_id).subquery())
    n_upcoming = func.coalesce(upcoming.c.upcoming, 0)

    filters = []
    if q:
        filters.append(venues.c.name.contains(q, autoescape=True)
                       | venues.c.city.contains(q, autoescape=True))
    if state:
        filters.append(venues.c.state == state)

    base = select(venues).outerjoin(upcoming, upcoming.c.venue_id == venues.c.venue_id).where(*filters)
    # With a search, alphabetical; without one, the busiest venues first.
    order = ([venues.c.name, venues.c.city] if q
             else [n_upcoming.desc(), venues.c.name])
    stmt = (select(venues.c.venue_id, venues.c.name, venues.c.address, venues.c.city,
                   venues.c.state, n_upcoming.label("upcoming"), upcoming.c.next_date)
            .select_from(venues)
            .outerjoin(upcoming, upcoming.c.venue_id == venues.c.venue_id)
            .where(*filters)
            .order_by(*order)
            .limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE))

    with engine.connect() as conn:
        total = conn.execute(select(func.count()).select_from(base.subquery())).scalar_one()
        results = conn.execute(stmt).all()
        states = conn.execute(select(venues.c.state).distinct().order_by(venues.c.state)).scalars().all()

    pages = max(1, -(-total // PAGE_SIZE))
    return render_template("venues.html", q=q, state=state, states=states, results=results,
                           total=total, page=page, pages=pages)


@app.route("/venues/<int:venue_id>")
def venue_detail(venue_id):
    if venue_id > MAX_INT:
        abort(404)
    with engine.connect() as conn:
        venue = conn.execute(select(venues).where(venues.c.venue_id == venue_id)).first()
        if venue is None:
            abort(404)
        all_events = events_at_venue(conn, venue_id)
    cutoff = today()
    return render_template("venue_public.html", venue=venue,
                           upcoming=[e for e in all_events if e["date"] >= cutoff],
                           past=[e for e in all_events if e["date"] < cutoff])


if __name__ == "__main__":
    # In Docker, init_db() runs once before gunicorn starts (see Dockerfile).
    init_db()
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)),
            debug=os.environ.get("FLASK_DEBUG") == "1")
