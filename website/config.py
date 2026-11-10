"""Settings, read from environment variables or website/.env."""

import os
import secrets
from pathlib import Path

HERE = Path(__file__).parent


def _load_env_file():
    env_file = HERE / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _secret_key():
    """Signs the login cookie. Kept in a file so logins survive a restart."""
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    key_file = HERE / ".secret_key"
    if not key_file.exists():
        key_file.write_text(secrets.token_hex(32), encoding="utf-8")
    return key_file.read_text(encoding="utf-8").strip()


_load_env_file()

SITE_NAME = os.environ.get("SITE_NAME", "Concert Tickets")
SECRET_KEY = _secret_key()


def _database_url():
    """DATABASE_URL wins; else DB_HOST etc. build a SQL Server URL; else a local SQLite file."""
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    if os.environ.get("DB_HOST"):
        from sqlalchemy.engine import URL   # builds the URL so passwords need no escaping
        return URL.create(
            "mssql+pymssql",
            username=os.environ.get("DB_USER", "sa"),
            password=os.environ.get("DB_PASSWORD", ""),
            host=os.environ["DB_HOST"],
            port=int(os.environ.get("DB_PORT", 1433)),
            database=os.environ.get("DB_NAME", "concert_tickets"),
        )
    return f"sqlite:///{(HERE / 'concert_tickets.db').as_posix()}"


DATABASE_URL = _database_url()
