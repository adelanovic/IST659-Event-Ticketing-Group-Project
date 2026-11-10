"""Database connection and the tables the website uses.

Logins live in `accounts` (one row per login, customer or venue). A customer's
details are in `customers`; a venue account manages one row in `venues`
(venues.account_id). The Ticketmaster tables (genres, venues, artists, events,
event_artists) match the team's generated INSERT file, plus venues.account_id.

The team's UP/DOWN SQL file is the official schema; these definitions let the
site create the same tables for development. See sql/account_tables.sql.
"""

import re
import time

from sqlalchemy import (Boolean, CheckConstraint, Column, Date, DateTime, ForeignKey, Index,
                        Integer, MetaData, SmallInteger, String, Table, Time, Unicode,
                        create_engine, inspect, insert, select, text)
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

import config

engine = create_engine(config.DATABASE_URL, pool_pre_ping=True)
metadata = MetaData()

accounts = Table(
    "accounts", metadata,
    Column("account_id", Integer, primary_key=True, autoincrement=True),
    Column("email", Unicode(255), nullable=False, unique=True),     # login name, stored lowercase
    Column("password_hash", String(255), nullable=False),           # Argon2id hash, never the password
    Column("account_type", String(10), nullable=False),
    Column("created_at", DateTime, nullable=False),                 # UTC
    Column("last_login_at", DateTime),                              # UTC
    Column("is_active", Boolean, nullable=False, default=True),
    CheckConstraint("account_type IN ('customer', 'venue')", name="CK_accounts_account_type"),
)

customers = Table(
    "customers", metadata,
    Column("customer_id", Integer, primary_key=True, autoincrement=True),
    Column("account_id", Integer, ForeignKey("accounts.account_id"), nullable=False, unique=True),
    Column("first_name", Unicode(50), nullable=False),
    Column("last_name", Unicode(50), nullable=False),
    Column("phone", String(20)),
)

genres = Table(
    "genres", metadata,
    Column("genre_id", Integer, primary_key=True, autoincrement=True),
    Column("name", Unicode(50), nullable=False),
)

venues = Table(
    "venues", metadata,
    Column("venue_id", Integer, primary_key=True, autoincrement=True),
    Column("tm_venue_id", String(25)),                  # NULL for venues added on this site
    Column("name", Unicode(100), nullable=False),
    Column("address", Unicode(100)),
    Column("city", Unicode(50), nullable=False),
    Column("state", String(2), nullable=False),
    Column("postal_code", String(10), nullable=False),
    Column("account_id", Integer, ForeignKey("accounts.account_id")),   # venue account managing it
)
# One account per venue. Filtered, because SQL Server's plain UNIQUE allows only one NULL.
Index("UX_venues_account_id", venues.c.account_id, unique=True,
      mssql_where=venues.c.account_id.isnot(None), sqlite_where=venues.c.account_id.isnot(None))

artists = Table(
    "artists", metadata,
    Column("artist_id", Integer, primary_key=True, autoincrement=True),
    Column("tm_attraction_id", String(25)),
    Column("name", Unicode(100), nullable=False),
    Column("genre_id", Integer, ForeignKey("genres.genre_id")),
)

events = Table(
    "events", metadata,
    Column("event_id", Integer, primary_key=True, autoincrement=True),
    Column("tm_event_id", String(25)),
    Column("name", Unicode(200), nullable=False),
    Column("venue_id", Integer, ForeignKey("venues.venue_id"), nullable=False),
    Column("event_date", Date, nullable=False),
    Column("event_time", Time),
    Column("status", String(20), nullable=False),
)

event_artists = Table(
    "event_artists", metadata,
    Column("event_id", Integer, ForeignKey("events.event_id"), primary_key=True),
    Column("artist_id", Integer, ForeignKey("artists.artist_id"), primary_key=True),
    Column("billing_order", SmallInteger, nullable=False),
)


def _database_name(url):
    """The database name, checked so it's safe to put inside SQL text."""
    name = url.database
    if not name or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise SystemExit(f"DB_NAME must be letters, digits and underscores, got {name!r}")
    return name


def database_label():
    url = make_url(config.DATABASE_URL)
    if url.get_backend_name() == "mssql":
        return f"SQL Server database {url.database} on {url.host}"
    return f"SQLite file {url.database}"


def drop_database():
    """Delete everything the site stored. SQL Server: drop the whole database; SQLite: drop the tables."""
    url = make_url(config.DATABASE_URL)
    engine.dispose()   # close this process's own pooled connections first
    if url.get_backend_name() != "mssql":
        with engine.begin() as conn:
            metadata.drop_all(conn)
        return
    name = _database_name(url)
    master = create_engine(url.set(database="master"), isolation_level="AUTOCOMMIT")
    with master.connect() as conn:
        # SINGLE_USER ... ROLLBACK IMMEDIATE disconnects anyone else using it (SQLPad, SSMS).
        conn.execute(text(f"IF DB_ID(N'{name}') IS NOT NULL BEGIN "
                          f"ALTER DATABASE [{name}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE; "
                          f"DROP DATABASE [{name}]; END"))
    master.dispose()


def ensure_database(wait_seconds=60):
    """SQL Server only: wait for the server to accept logins, then create the database if missing."""
    url = make_url(config.DATABASE_URL)
    if url.get_backend_name() != "mssql":
        return
    name = _database_name(url)

    master = create_engine(url.set(database="master"), isolation_level="AUTOCOMMIT")
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            with master.connect() as conn:
                conn.execute(text(f"IF DB_ID(N'{name}') IS NULL CREATE DATABASE [{name}]"))
            break
        except OperationalError as exc:
            if time.monotonic() > deadline:
                raise SystemExit(f"Can't reach SQL Server at {url.host}:{url.port}: {exc.orig}")
            print(f"Waiting for SQL Server at {url.host}:{url.port}...")
            time.sleep(3)
    master.dispose()


def migrate_old_customers(conn):
    """The first version kept email/password in `customers`. Move those rows into `accounts`.

    The old table is renamed to customers_old, copied, and dropped only after the
    copy succeeds, so a failure part-way never loses a row; the next run resumes.
    """
    insp = inspect(conn)
    tables = insp.get_table_names()
    if "customers" in tables and "email" in {c["name"] for c in insp.get_columns("customers")}:
        if conn.dialect.name == "mssql":
            conn.execute(text("EXEC sp_rename 'customers', 'customers_old'"))
        else:
            conn.execute(text("ALTER TABLE customers RENAME TO customers_old"))
        tables.append("customers_old")
    if "customers_old" not in tables:
        return

    metadata.create_all(conn, tables=[accounts, customers])
    old = Table("customers_old", MetaData(), autoload_with=conn)   # reflected, so dates parse
    taken = set(conn.execute(select(accounts.c.email)).scalars())
    moved = 0
    for r in conn.execute(select(old).order_by(old.c.customer_id)).mappings():
        if r["email"] in taken:
            continue
        account_id = conn.execute(insert(accounts).values(
            email=r["email"], password_hash=r["password_hash"], account_type="customer",
            created_at=r["created_at"], last_login_at=r["last_login_at"],
            is_active=r["is_active"])).inserted_primary_key[0]
        conn.execute(insert(customers).values(
            account_id=account_id, first_name=r["first_name"], last_name=r["last_name"],
            phone=r["phone"]))
        moved += 1
    old.drop(conn)
    print(f"Moved {moved} customer login(s) into the accounts table.")


def add_venue_account_column(conn):
    """If venues was created elsewhere (e.g. the team's UP script) without account_id, add it."""
    insp = inspect(conn)
    if "venues" not in insp.get_table_names():
        return
    if "account_id" in {c["name"] for c in insp.get_columns("venues")}:
        return
    conn.execute(text("ALTER TABLE venues ADD account_id INT NULL "
                      "CONSTRAINT FK_venues_account REFERENCES accounts(account_id)"))
    where = "WHERE account_id IS NOT NULL"
    conn.execute(text(f"CREATE UNIQUE INDEX UX_venues_account_id ON venues(account_id) {where}"))
    print("Added venues.account_id.")


def init_db():
    """Create the database (SQL Server) and any missing tables."""
    ensure_database()
    with engine.begin() as conn:
        migrate_old_customers(conn)
        metadata.create_all(conn)
        add_venue_account_column(conn)
