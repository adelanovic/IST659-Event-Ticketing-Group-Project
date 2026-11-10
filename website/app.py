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
from datetime import datetime, timezone
from functools import wraps

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from flask import Flask, abort, flash, redirect, render_template, request, session, url_for
from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import IntegrityError

import config
from db import (accounts, artists, customers, engine, event_artists, events, init_db,
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


def now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


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
        rows = conn.execute(
            select(events.c.event_id, events.c.name, events.c.event_date, events.c.event_time,
                   events.c.status, artists.c.name.label("artist"))
            .outerjoin(event_artists, event_artists.c.event_id == events.c.event_id)
            .outerjoin(artists, artists.c.artist_id == event_artists.c.artist_id)
            .where(events.c.venue_id == venue.venue_id)
            .order_by(events.c.event_date, events.c.event_time, events.c.event_id,
                      event_artists.c.billing_order)).all()

    # One entry per event, with its artists in billing order.
    venue_events = {}
    for r in rows:
        ev = venue_events.setdefault(r.event_id, {"name": r.name, "date": r.event_date,
                                                  "time": r.event_time, "status": r.status,
                                                  "artists": []})
        if r.artist:
            ev["artists"].append(r.artist)
    return render_template("venue.html", acct=acct, venue=venue,
                           venue_events=list(venue_events.values()))


if __name__ == "__main__":
    # In Docker, init_db() runs once before gunicorn starts (see Dockerfile).
    init_db()
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)),
            debug=os.environ.get("FLASK_DEBUG") == "1")
