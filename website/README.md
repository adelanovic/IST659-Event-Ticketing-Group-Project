# Concert Tickets website

Registration and login for two kinds of accounts:

- **Customers** (`/register`): a row in `accounts` plus a row in `customers`.
- **Venues** (`/register/venue`): a row in `accounts`, linked from `venues.account_id`.
  A venue either claims one of the Ticketmaster venues (search by name or city;
  each venue can be claimed once) or adds a new venue. Its page lists its events.

Data lives in the `concert_tickets` database on SQL Server. Passwords are
stored only as Argon2id hashes.

## Load the Ticketmaster data (development)

Venues need venue rows to claim. Until the team's UP/DOWN script is ready, load
the CSVs from `../data/csv` once:

```powershell
.venv\Scripts\python load_ticketmaster_data.py
```

It skips loading if `venues` already has rows.

## Run it in Docker (recommended)

SQL Server runs separately, from the `learn-databases` project. Start it first:

```powershell
cd ..\..\learn-databases
docker-compose up -d
```

Then, from this folder:

```powershell
copy .env.example .env      # first time only; then set SECRET_KEY
docker compose up -d --build
```

Open http://127.0.0.1:5000. The web container joins the `learn-databases_learndb_net`
network and reaches SQL Server at hostname `mssql`. On start it waits up to 60
seconds for SQL Server, creates the `concert_tickets` database and the
`customers` table if they don't exist, then starts gunicorn.

| Task | Command |
|---|---|
| Rebuild after code changes | `docker compose up -d --build` |
| See logs | `docker compose logs -f web` |
| Stop | `docker compose down` |

Registrations are stored in SQL Server, so stopping or rebuilding the web
container doesn't lose them.

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
| `app.py` | Pages: home, customer and venue registration, log in/out, customer account, venue page |
| `db.py` | Database connection and table definitions; creates the database and missing tables, migrates the old `customers` layout |
| `config.py` | Reads settings from `.env` |
| `templates/`, `static/` | HTML and CSS |
| `Dockerfile`, `docker-compose.yml` | Container image and how it joins the SQL Server network |
| `sql/account_tables.sql` | `accounts`, `customers` and `venues.account_id` as SQL Server DDL, for the team's UP/DOWN file |
| `load_ticketmaster_data.py` | Loads the Ticketmaster CSVs for development |
| `show_accounts.py` | Prints every account |

## Security choices

- Passwords are hashed with Argon2id (`argon2-cffi`), never stored or logged in plain text.
- Login gives the same error for a wrong email or a wrong password, and takes the same time either way.
- Forms carry a CSRF token; the session cookie is HttpOnly and SameSite=Lax.
- Emails are stored lowercase and must be unique.
- The container runs as a non-root user.

For a class project this connects as `sa`. A real deployment would use a
dedicated SQL login with rights only on `concert_tickets`, HTTPS, and login rate limiting.
