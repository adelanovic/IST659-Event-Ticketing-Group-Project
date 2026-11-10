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
from datetime import date, datetime, timedelta, timezone
from functools import wraps

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from flask import (Flask, Response, abort, flash, redirect, render_template, request, session,
                   url_for)
from sqlalchemy import distinct, func, insert, or_, select, update
from sqlalchemy.exc import IntegrityError

import config
from db import (accounts, artists, customer_avatars, customers, engine, event_artists, events,
                genres, init_db, venues)

app = Flask(__name__)
app.config.update(
    SECRET_KEY=config.SECRET_KEY,
    MAX_CONTENT_LENGTH=3 * 1024 * 1024,     # bigger requests are refused (413) before reading
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
HOME_FOR = {"customer": "my_shows", "venue": "venue_home"}
PAGE_SIZE = 20
MAX_INT = 2_147_483_647     # SQL Server INT; bigger ids can't exist and would error


def now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def today():
    """Today's date; tests can pin it with app.config["TODAY"]."""
    return app.config.get("TODAY") or date.today()


@app.context_processor
def template_globals():
    name = session.get("display_name") or ""
    return {"site_name": config.SITE_NAME, "csrf_token": csrf_token,
            "current_name": name,
            "current_type": session.get("account_type"),
            "current_avatar": session.get("avatar_v"),          # photo version, or None
            "current_initial": name[:1].upper(),
            "current_hue": avatar_hue(session.get("account_id"))}


AVATAR_HUES = 6


def avatar_hue(account_id):
    """Which of the six initials-badge colours this account gets (stable per account)."""
    return (account_id or 0) % AVATAR_HUES


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


def start_session(account_id, account_type, display_name, avatar_v=None):
    """Fresh session on login, so an old session id can't be reused."""
    session.clear()
    session.update(account_id=account_id, account_type=account_type, display_name=display_name)
    if avatar_v:
        session["avatar_v"] = avatar_v


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
    return redirect(url_for("my_shows"))


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
        avatar_v = None
        if account.account_type == "customer":
            cust = conn.execute(select(customers.c.first_name, customer_avatars.c.updated_at)
                                .outerjoin(customer_avatars,
                                           customer_avatars.c.customer_id == customers.c.customer_id)
                                .where(customers.c.account_id == account.account_id)).first()
            name = cust.first_name if cust else None
            avatar_v = avatar_version(cust.updated_at) if cust and cust.updated_at else None
        else:
            name = conn.execute(select(venues.c.name)
                                .where(venues.c.account_id == account.account_id)).scalar()

    start_session(account.account_id, account.account_type, name or email, avatar_v)
    return redirect(next_url or url_for(HOME_FOR[account.account_type]))


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You're logged out.")
    return redirect(url_for("index"))


def tickets_for_customer(conn, customer_id):
    """Ticket orders shown on My shows, soonest first.

    Returns [] until the team's orders/tickets/price-tier tables exist. Then this
    is the one place to fill in (a view such as vw_customer_tickets fits well).
    Each item is a dict with:
        order_id, event_id, event_name, event_date, event_time, status,
        venue_id, venue_name, city, state, tier_name, quantity, total_paid, ordered_at
    """
    return []


@app.route("/my-shows")
@login_required("customer")
def my_shows():
    with engine.connect() as conn:
        customer = conn.execute(select(customers.c.customer_id, customers.c.first_name).where(
            customers.c.account_id == session["account_id"])).first()
        if customer is None:
            session.clear()
            return redirect(url_for("login"))
        tickets = tickets_for_customer(conn, customer.customer_id)
        suggestions, _ = find_shows(conn, date_to=today() + timedelta(days=6),
                                    on_sale_only=True, limit=3)
    cutoff = today()
    upcoming = [t for t in tickets if t["event_date"] >= cutoff]
    past = sorted((t for t in tickets if t["event_date"] < cutoff),
                  key=lambda t: t["event_date"], reverse=True)
    tab = "past" if request.args.get("tab") == "past" else "upcoming"
    return render_template("my_shows.html", customer=customer, tab=tab, upcoming=upcoming,
                           past=past, shown=past if tab == "past" else upcoming,
                           suggestions=suggestions)


def avatar_version(updated_at):
    """Changes whenever the photo changes, so browsers fetch the new one."""
    return updated_at.strftime("%Y%m%d%H%M%S%f")


def current_customer(conn):
    """The logged-in customer's account, profile and photo info, or None."""
    return conn.execute(
        select(accounts, customers.c.customer_id, customers.c.first_name, customers.c.last_name,
               customers.c.phone, customer_avatars.c.updated_at.label("avatar_updated"))
        .join(customers, customers.c.account_id == accounts.c.account_id)
        .outerjoin(customer_avatars, customer_avatars.c.customer_id == customers.c.customer_id)
        .where(accounts.c.account_id == session["account_id"])).first()


def render_account(c, form=None, errors=None, photo_error=None, status=200):
    with engine.connect() as conn:
        tickets = tickets_for_customer(conn, c.customer_id)
    upcoming = [t for t in tickets if t["event_date"] >= today()]
    stats = {"upcoming": len(upcoming), "tickets": sum(t["quantity"] for t in tickets),
             "next": min(upcoming, key=lambda t: t["event_date"]) if upcoming else None}
    form = form or {"first_name": c.first_name, "last_name": c.last_name, "phone": c.phone or ""}
    return render_template("account.html", c=c, stats=stats, form=form, errors=errors or {},
                           photo_error=photo_error, editing=bool(errors),
                           hue=avatar_hue(c.account_id),
                           avatar_v=avatar_version(c.avatar_updated) if c.avatar_updated else None), status


def logged_out_customer():
    session.clear()
    return redirect(url_for("login"))


@app.route("/account")
@login_required("customer")
def account():
    with engine.connect() as conn:
        c = current_customer(conn)
    if c is None:          # account was deleted while logged in
        return logged_out_customer()
    return render_account(c)


@app.route("/account/profile", methods=["POST"])
@login_required("customer")
def update_profile():
    form = {k: " ".join(request.form.get(k, "").split()) for k in ("first_name", "last_name", "phone")}
    errors = {}
    if not form["first_name"] or len(form["first_name"]) > 50:
        errors["first_name"] = "Enter your first name (up to 50 characters)."
    if not form["last_name"] or len(form["last_name"]) > 50:
        errors["last_name"] = "Enter your last name (up to 50 characters)."
    if form["phone"] and not PHONE_RE.match(form["phone"]):
        errors["phone"] = "Use digits only, with optional spaces, dashes or a leading +."
    with engine.begin() as conn:
        c = current_customer(conn)
        if c is None:
            return logged_out_customer()
        if not errors:
            conn.execute(update(customers).where(customers.c.customer_id == c.customer_id).values(
                first_name=form["first_name"], last_name=form["last_name"],
                phone=form["phone"] or None))
    if errors:
        return render_account(c, form=form, errors=errors, status=400)
    session["display_name"] = form["first_name"]
    flash("Your details are saved.")
    return redirect(url_for("account"))


MAX_AVATAR_BYTES = 2 * 1024 * 1024


def image_type(data):
    """The image's real type from its first bytes. Anything else, SVG included, is refused."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


@app.route("/account/avatar", methods=["POST"])
@login_required("customer")
def upload_avatar():
    upload = request.files.get("avatar")
    data = upload.read(MAX_AVATAR_BYTES + 1) if upload else b""
    content_type = image_type(data)
    error = None
    if not data:
        error = "Choose a photo to upload."
    elif len(data) > MAX_AVATAR_BYTES:
        error = "That photo is over 2 MB. Pick a smaller one."
    elif content_type is None:
        error = "Use a PNG, JPEG or WebP image."

    now = now_utc()
    with engine.begin() as conn:
        c = current_customer(conn)
        if c is None:
            return logged_out_customer()
        if not error:
            values = {"image": data, "content_type": content_type, "updated_at": now}
            changed = conn.execute(update(customer_avatars)
                                   .where(customer_avatars.c.customer_id == c.customer_id)
                                   .values(**values)).rowcount
            if not changed:
                conn.execute(insert(customer_avatars).values(customer_id=c.customer_id, **values))
    if error:
        return render_account(c, photo_error=error, status=400)
    session["avatar_v"] = avatar_version(now)
    flash("Your photo is updated.")
    return redirect(url_for("account"))


@app.route("/account/avatar/delete", methods=["POST"])
@login_required("customer")
def delete_avatar():
    with engine.begin() as conn:
        c = current_customer(conn)
        if c is None:
            return logged_out_customer()
        conn.execute(customer_avatars.delete().where(customer_avatars.c.customer_id == c.customer_id))
    session.pop("avatar_v", None)
    flash("Your photo is removed.")
    return redirect(url_for("account"))


@app.route("/account/avatar")
@login_required("customer")
def avatar_image():
    """The logged-in customer's own photo. Nobody else's can be fetched."""
    with engine.connect() as conn:
        row = conn.execute(
            select(customer_avatars.c.image, customer_avatars.c.content_type)
            .join(customers, customers.c.customer_id == customer_avatars.c.customer_id)
            .where(customers.c.account_id == session["account_id"])).first()
    if row is None:
        abort(404)
    return Response(bytes(row.image), mimetype=row.content_type, headers={
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, max-age=31536000",   # URL carries ?v=, so it changes on update
        "Content-Disposition": "inline",
    })


@app.errorhandler(413)
def too_large(_error):
    flash("That upload is too big. Photos can be up to 2 MB.")
    return redirect(url_for("account") if session.get("account_type") == "customer"
                    else url_for("index"))


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
        ev = by_id.setdefault(r.event_id, {"event_id": r.event_id, "name": r.name,
                                           "date": r.event_date, "time": r.event_time,
                                           "status": r.status, "artists": []})
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
    params = {k: v for k, v in {"q": q, "state": state}.items() if v}
    return render_template("venues.html", q=q, state=state, states=states, results=results,
                           total=total, page=page, pages=pages, params=params)


@app.route("/venues/<int:venue_id>")
def venue_detail(venue_id):
    if venue_id > MAX_INT:
        abort(404)
    with engine.connect() as conn:
        venue = conn.execute(select(venues).where(venues.c.venue_id == venue_id)).first()
        if venue is None:
            abort(404)
        rows = conn.execute(select(*EVENT_COLUMNS)
                            .select_from(events.join(venues, venues.c.venue_id == events.c.venue_id))
                            .where(events.c.venue_id == venue_id)
                            .order_by(events.c.event_date, events.c.event_time,
                                      events.c.event_id)).all()
        all_shows = with_lineups(conn, rows)
    cutoff = today()
    return render_template("venue_public.html", venue=venue,
                           upcoming=[s for s in all_shows if s["event_date"] >= cutoff],
                           past=[s for s in all_shows if s["event_date"] < cutoff])


# --- Shows: home page, search, show and artist pages ------------------------------

EVENT_COLUMNS = (events.c.event_id, events.c.name, events.c.event_date, events.c.event_time,
                 events.c.status, venues.c.venue_id, venues.c.name.label("venue_name"),
                 venues.c.city, venues.c.state)


def with_lineups(conn, rows):
    """Turn event rows into dicts with `artists` (billing order) and the headliner's `genre`."""
    shows = [dict(r._mapping, artists=[], genre=None) for r in rows]
    if not shows:
        return shows
    by_id = {s["event_id"]: s for s in shows}
    lineup = conn.execute(
        select(event_artists.c.event_id, artists.c.artist_id, artists.c.name,
               genres.c.genre_id, genres.c.name.label("genre_name"))
        .join(artists, artists.c.artist_id == event_artists.c.artist_id)
        .outerjoin(genres, genres.c.genre_id == artists.c.genre_id)
        .where(event_artists.c.event_id.in_(list(by_id)))
        .order_by(event_artists.c.event_id, event_artists.c.billing_order)).all()
    for r in lineup:
        show = by_id[r.event_id]
        show["artists"].append({"artist_id": r.artist_id, "name": r.name})
        if show["genre"] is None and r.genre_id is not None:
            show["genre"] = {"genre_id": r.genre_id, "name": r.genre_name}
    return shows


def find_shows(conn, *, q="", genre_id=None, state="", date_from=None, date_to=None,
               on_sale_only=False, limit=PAGE_SIZE, offset=0):
    """Shows matching the filters in date order, plus how many match in total."""
    filters = [events.c.event_date >= (date_from or today())]
    if on_sale_only:
        filters.append(events.c.status == "onsale")
    if date_to:
        filters.append(events.c.event_date <= date_to)
    if q:
        by_artist = (select(event_artists.c.event_id)
                     .join(artists, artists.c.artist_id == event_artists.c.artist_id)
                     .where(artists.c.name.contains(q, autoescape=True)))
        filters.append(or_(events.c.name.contains(q, autoescape=True),
                           venues.c.name.contains(q, autoescape=True),
                           venues.c.city.contains(q, autoescape=True),
                           events.c.event_id.in_(by_artist)))
    if genre_id:
        filters.append(events.c.event_id.in_(
            select(event_artists.c.event_id)
            .join(artists, artists.c.artist_id == event_artists.c.artist_id)
            .where(artists.c.genre_id == genre_id)))
    if state:
        filters.append(venues.c.state == state)

    joined = events.join(venues, venues.c.venue_id == events.c.venue_id)
    total = conn.execute(select(func.count()).select_from(joined).where(*filters)).scalar_one()
    rows = conn.execute(select(*EVENT_COLUMNS).select_from(joined).where(*filters)
                        .order_by(events.c.event_date, events.c.event_time, events.c.event_id)
                        .limit(limit).offset(offset)).all()
    return with_lineups(conn, rows), total


def popular_genres(conn, limit=12):
    """Genres with the most upcoming shows."""
    n = func.count(distinct(events.c.event_id))
    return conn.execute(
        select(genres.c.genre_id, genres.c.name, n.label("shows"))
        .join(artists, artists.c.genre_id == genres.c.genre_id)
        .join(event_artists, event_artists.c.artist_id == artists.c.artist_id)
        .join(events, events.c.event_id == event_artists.c.event_id)
        .where(events.c.event_date >= today())
        .group_by(genres.c.genre_id, genres.c.name)
        .order_by(n.desc(), genres.c.name).limit(limit)).all()


def parse_day(value):
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


@app.route("/")
def index():
    start = today()
    with engine.connect() as conn:
        # Six cards fill two rows of three; only shows you could actually buy.
        this_week, _ = find_shows(conn, date_to=start + timedelta(days=6),
                                  on_sale_only=True, limit=6)
        _, week_total = find_shows(conn, date_to=start + timedelta(days=6), limit=1)  # "See all" count
        genre_list = popular_genres(conn, limit=9)
        counts = {
            "shows": conn.execute(select(func.count()).select_from(events)
                                  .where(events.c.event_date >= start)).scalar_one(),
            "venues": conn.execute(select(func.count()).select_from(venues)).scalar_one(),
            "artists": conn.execute(select(func.count()).select_from(artists)).scalar_one(),
        }
        n_upcoming = func.count(events.c.event_id)
        busiest = conn.execute(
            select(venues.c.venue_id, venues.c.name, venues.c.city, venues.c.state,
                   n_upcoming.label("shows"))
            .join(events, events.c.venue_id == venues.c.venue_id)
            .where(events.c.event_date >= start)
            .group_by(venues.c.venue_id, venues.c.name, venues.c.city, venues.c.state)
            .order_by(n_upcoming.desc(), venues.c.name).limit(5)).all()
    weekend = start + timedelta(days=(5 - start.weekday()) % 7)   # next Saturday (or today)
    return render_template("index.html", this_week=this_week, week_total=week_total,
                           genres=genre_list, counts=counts, busiest=busiest,
                           week_end=start + timedelta(days=6),
                           weekend_from=weekend, weekend_to=weekend + timedelta(days=1))


@app.route("/events")
def events_search():
    q = request.args.get("q", "").strip()[:100]
    state = request.args.get("state", "").strip().upper()[:2]
    genre_arg = request.args.get("genre", "")
    genre_id = int(genre_arg) if genre_arg.isdigit() and int(genre_arg) <= MAX_INT else None
    date_from = max(parse_day(request.args.get("from")) or today(), today())
    date_to = parse_day(request.args.get("to"))
    if date_to and date_to < date_from:
        date_to = None
    page = max(1, min(request.args.get("page", 1, type=int) or 1, 10_000))

    with engine.connect() as conn:
        shows, total = find_shows(conn, q=q, genre_id=genre_id, state=state, date_from=date_from,
                                  date_to=date_to, limit=PAGE_SIZE,
                                  offset=(page - 1) * PAGE_SIZE)
        genre_list = conn.execute(select(genres).order_by(genres.c.name)).all()
        states = conn.execute(select(venues.c.state).distinct().order_by(venues.c.state)).scalars().all()

    genre_name = next((g.name for g in genre_list if g.genre_id == genre_id), None)
    filters_on = bool(q or genre_id or state or date_to or date_from != today())
    params = {k: v for k, v in {"q": q, "genre": genre_id, "state": state,
                                "from": date_from.isoformat() if date_from != today() else None,
                                "to": date_to.isoformat() if date_to else None}.items() if v}
    return render_template("events.html", shows=shows, total=total, q=q, state=state,
                           genre_id=genre_id, genre_name=genre_name, genres=genre_list,
                           states=states, date_from=date_from, date_to=date_to, page=page,
                           pages=max(1, -(-total // PAGE_SIZE)), params=params,
                           filters_on=filters_on, min_date=today())


@app.route("/events/<int:event_id>")
def event_detail(event_id):
    if event_id > MAX_INT:
        abort(404)
    with engine.connect() as conn:
        row = conn.execute(select(*EVENT_COLUMNS, venues.c.address, venues.c.postal_code)
                           .select_from(events.join(venues, venues.c.venue_id == events.c.venue_id))
                           .where(events.c.event_id == event_id)).first()
        if row is None:
            abort(404)
        show = with_lineups(conn, [row])[0]
        more = conn.execute(select(*EVENT_COLUMNS)
                            .select_from(events.join(venues, venues.c.venue_id == events.c.venue_id))
                            .where(events.c.venue_id == row.venue_id,
                                   events.c.event_id != event_id,
                                   events.c.event_date >= today())
                            .order_by(events.c.event_date, events.c.event_time).limit(4)).all()
        more = with_lineups(conn, more)
    return render_template("event.html", show=show, more=more, is_past=show["event_date"] < today())


@app.route("/artists/<int:artist_id>")
def artist_detail(artist_id):
    if artist_id > MAX_INT:
        abort(404)
    with engine.connect() as conn:
        artist = conn.execute(select(artists.c.artist_id, artists.c.name, genres.c.genre_id,
                                     genres.c.name.label("genre_name"))
                              .outerjoin(genres, genres.c.genre_id == artists.c.genre_id)
                              .where(artists.c.artist_id == artist_id)).first()
        if artist is None:
            abort(404)
        rows = conn.execute(select(*EVENT_COLUMNS)
                            .select_from(events.join(venues, venues.c.venue_id == events.c.venue_id)
                                         .join(event_artists, event_artists.c.event_id == events.c.event_id))
                            .where(event_artists.c.artist_id == artist_id,
                                   events.c.event_date >= today())
                            .order_by(events.c.event_date, events.c.event_time)).all()
        shows = with_lineups(conn, rows)
    return render_template("artist.html", artist=artist, shows=shows)


@app.errorhandler(404)
def not_found(_error):
    return render_template("error.html", code=404, title="We couldn't find that page",
                           message="The link may be old, or the show may have been removed."), 404


@app.errorhandler(400)
def bad_request(error):
    return render_template("error.html", code=400, title="Something went wrong with that form",
                           message=getattr(error, "description", None)
                           or "Go back, reload the page and try again."), 400


if __name__ == "__main__":
    # In Docker, init_db() runs once before gunicorn starts (see Dockerfile).
    init_db()
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)),
            debug=os.environ.get("FLASK_DEBUG") == "1")
