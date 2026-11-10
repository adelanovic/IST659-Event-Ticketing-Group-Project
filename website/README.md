# Concert Tickets website

Registration and login for two kinds of accounts:

- **Customers** (`/register`): a row in `accounts` plus a row in `customers`.
- **Venues** (`/register/venue`): a row in `accounts`, linked from `venues.account_id`.
  A venue either claims one of the Ticketmaster venues (search by name or city;
  each venue can be claimed once) or adds a new venue. Its page lists its events.

Data lives in the `concert_tickets` database on SQL Server. Passwords are
stored only as Argon2id hashes.

## Run it in Docker (recommended)

SQL Server runs separately, from the `learn-databases` project. Start it first:

```powershell
cd ..\..\learn-databases
docker-compose up -d
```

Then, from this folder:

```powershell
copy .env.example .env      # first time only; then set SECRET_KEY and DB_PASSWORD
up.bat                      # start the site
down.bat                    # stop the site and DELETE its database
```

Nothing is kept between runs: `down.bat` drops the `concert_tickets` database
(all accounts, claimed venues and listed events), and the next `up.bat` starts
from a clean copy. On a fresh start the container loads the Ticketmaster CSVs from
`../data/csv` and creates two demo logins:

| Login | Password | What it is |
|---|---|---|
| `demo.venue@example.com` | `DemoVenue2026!` | Venue account managing Smalls Jazz Club |
| `demo.customer@example.com` | `DemoCustomer2026!` | Customer account |

`down.bat` asks before deleting; `down.bat /y` skips the question. To wipe the
database without stopping anything, run `.venv\Scripts\python reset_db.py`.

Open http://127.0.0.1:5000. The web container joins the `learn-databases_learndb_net`
network and reaches SQL Server at hostname `mssql`. On start it waits up to 60
seconds for SQL Server, creates the `concert_tickets` database and the
`customers` table if they don't exist, then starts gunicorn.

| Task | Command |
|---|---|
| Rebuild after code changes (keeps data) | `up.bat` |
| See logs | `docker compose logs -f web` |
| Stop and delete data | `down.bat` |
| Stop but keep data | `docker compose down` |

## Run it without Docker

Double-click `run.bat`, or run `.\run.bat` in this folder. It creates `.venv` on
first use and starts the site at http://127.0.0.1:5000, connecting to the same
SQL Server through `localhost:1433`. Stop the Docker container first, since both
use port 5000.

Running `python app.py` with your normal Python fails because Flask is only
installed in `.venv`.

## Seeing the data

- `.venv\Scripts\python show_accounts.py` lists every account and what it's linked to.
- SQLPad (http://127.0.0.1:5002) or Adminer (http://127.0.0.1:5003) from
  learn-databases: database `concert_tickets`, tables `dbo.accounts`, `dbo.customers`, `dbo.venues`.

## Tests

```powershell
.venv\Scripts\python -m pip install -r requirements-dev.txt   # first time only
.venv\Scripts\python -m pytest
```

The tests use a temporary SQLite database with a few sample venues and events,
so they don't need SQL Server and never change your data. They cover customer and
venue registration, login, page access by account type, CSRF protection, venue
claiming, and the migration from the first `customers` layout.

## Settings (`.env`)

| Setting | Meaning |
|---|---|
| `SECRET_KEY` | Signs login cookies. Keep it secret; changing it logs everyone out. |
| `DB_HOST`, `DB_PORT` | `localhost` / `1433` for `run.bat`. Docker overrides `DB_HOST` to `mssql`. |
| `DB_USER`, `DB_PASSWORD` | SQL Server login (the learn-databases `sa` account by default) |
| `DB_NAME` | Database to use; created automatically if missing |
| `SITE_NAME` | Name shown in the header |

Leave out `DB_HOST` to use a local SQLite file (`concert_tickets.db`) instead.

## Files

| File | What it does |
|---|---|
| `app.py` | Pages: home, show search (`/events`), show, artist and venue pages, customer and venue registration, log in/out, customer account, venue owner page and event listing |
| `db.py` | Database connection and table definitions; creates the database and missing tables, migrates the old `customers` layout |
| `config.py` | Reads settings from `.env` |
| `templates/`, `static/` | HTML and CSS |
| `Dockerfile`, `docker-compose.yml` | Container image and how it joins the SQL Server network |
| `sql/account_tables.sql` | `accounts`, `customers` and `venues.account_id` as SQL Server DDL, for the team's UP/DOWN file |
| `load_ticketmaster_data.py` | Fills an empty database: Ticketmaster CSVs and the demo logins (runs on container start) |
| `reset_db.py` | Deletes the site's database |
| `up.bat`, `down.bat` | Start the site; stop it and delete its data |
| `show_accounts.py` | Prints every account |
| `tests/` | Automated tests (`pytest`) |
| `requirements.txt`, `requirements-dev.txt` | Exact package versions for the site, plus pytest for the tests |

## Security choices

- Passwords are hashed with Argon2id (`argon2-cffi`), never stored or logged in plain text.
- Login gives the same error for a wrong email or a wrong password, and takes the same time either way.
- Forms carry a CSRF token; the session cookie is HttpOnly and SameSite=Lax.
- Emails are stored lowercase and must be unique.
- The container runs as a non-root user.

For a class project this connects as `sa`. A real deployment would use a
dedicated SQL login with rights only on `concert_tickets`, HTTPS, and login rate limiting.

## References

Anthropic. (2026). *Claude Code* (Claude Opus 5.5, model `claude-opus-5-5`) [AI coding assistant]. https://claude.com/claude-code

### How AI was used

This website was generated with Claude Code, an AI coding assistant, working from
the team's prompts and design decisions, in November 2026. The team directed the
work, reviewed and tested the results, and is responsible for the final submission.

Generated with AI assistance:

- All website code: `app.py`, `db.py`, `config.py`, `load_ticketmaster_data.py`,
  `reset_db.py`, `show_accounts.py`
- The page templates (`templates/`) and visual design (`static/style.css`)
- The Docker setup (`Dockerfile`, `docker-compose.yml`, `up.bat`, `down.bat`, `run.bat`)
- The automated tests (`tests/`)
- `sql/account_tables.sql`, written as a reference for the login tables. The
  team's own UP/DOWN SQL file is the official schema.

Per the project charter, the team writes the conceptual and logical data models
and the project's SQL file; this website is the AI-assisted frontend.
