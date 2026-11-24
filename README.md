# Concert Tickets: Venue & Customer Ticketing Platform

IST659 group project (Fall 2026, Group 2). A relational SQL Server database and web
application for concert ticketing, built from a sample of upcoming Ticketmaster events.

## What's in this repository

| Path | What it is |
|---|---|
| `fetch_ticketmaster.py` | Downloads upcoming US music events from the Ticketmaster Discovery API, cleans them and writes the CSVs in `data/csv/` |
| `generate_inserts.py` | Turns `data/csv/` into SQL Server `INSERT` statements for the team's UP script |
| `filter_events.py` | Older helper: one flat row per event from the raw download (`data/events_filtered.csv`) |
| `data/csv/` | The cleaned dataset (see below) |
| `website/` | The Flask website: customer and venue accounts, show search, venue pages. See `website/README.md` |
| `docs/` | Project charter and the Ticketmaster field dictionary |

## The dataset (`data/csv/`)

Five tables, one CSV each. Every table has its own integer key; foreign keys use those keys.

| File | Rows | Columns |
|---|---|---|
| `genres.csv` | 34 | genre_id, name |
| `venues.csv` | 946 | venue_id, name, address, city, state, postal_code |
| `artists.csv` | 1,997 | artist_id, name, genre_id |
| `events.csv` | 2,566 | event_id, name, venue_id, event_date, event_time, status |
| `event_artists.csv` | 3,639 | event_id, artist_id, billing_order (1 = headliner) |

Events run from Nov 13, 2026 to Feb 9, 2027: up to 30 randomly chosen shows per day,
keeping only complete listings. Every event has at least one performer, every
performer has a name and a genre, and duplicate venues, artists and shows are merged.
Only `venues.address` (4 rows) and `events.event_time` (12 rows, time not announced)
have blanks, which load as NULL.

To rebuild the CSVs from the saved download, or download a fresh sample:

```powershell
copy .env.example .env        # then add your Ticketmaster API key
pip install requests
python fetch_ticketmaster.py --no-fetch                                        # rebuild from data/raw_events.json
python fetch_ticketmaster.py --days 90 --per-day 30 --complete --target 2566   # new download
```

A new download is a new random sample, so the rows will differ.

## Quick start: run the website

You need **Docker Desktop** (installed and running) and **Git**. You don't need
Python or a Ticketmaster key to run the site.

**1. Start the class SQL Server** (skip if it's already running).

```powershell
git clone https://github.com/mafudge/learn-databases.git
cd learn-databases
docker-compose up -d
```

Keep the folder named `learn-databases`: the website joins the Docker network
`learn-databases_learndb_net`, which is named after that folder.

**2. Get this project and add your settings** (first time only).

```powershell
git clone <this repository's URL>
cd <repository folder>\website
copy .env.example .env
notepad .env
```

In `.env`, set `DB_PASSWORD` to the `MSSQL_SA_PASSWORD` value from
`learn-databases\docker-compose.yaml`. Leave everything else as it is.

**3. Start the site** (from the `website` folder).

```powershell
.\up.bat
```

The first start builds the image (1 to 2 minutes), then creates the
`concert_tickets` database and loads the data (about 10 seconds).

**4. Open http://127.0.0.1:5000** and log in with a demo account, or create your own.

| Login | Password | What it is |
|---|---|---|
| `demo.venue@example.com` | `DemoVenue2026!` | Venue account (Smalls Jazz Club) |
| `demo.customer@example.com` | `DemoCustomer2026!` | Customer account |

**Stopping it** (from the `website` folder):

| Command | What it does |
|---|---|
| `.\down.bat` | Stops the site and **deletes its database** (accounts, listed events). The next `.\up.bat` starts fresh. |
| `docker compose down` | Stops the site but keeps the data |
| `docker compose logs -f web` | Shows what the site is doing (Ctrl+C to stop watching) |

**On a Mac or Linux:** the `.bat` files are Windows-only. Use
`cp .env.example .env`, then `docker compose up -d --build` to start and
`docker compose down` to stop. If port 5000 is taken (macOS uses it for AirPlay
Receiver), change `"5000:8000"` in `website/docker-compose.yml` to `"5050:8000"` and
open http://127.0.0.1:5050.

### If it doesn't start

| What you see | Fix |
|---|---|
| `network learn-databases_learndb_net ... not found` | SQL Server isn't running, or the folder isn't named `learn-databases`. Do step 1. |
| `failed to connect to the docker API` | Docker Desktop isn't running. Start it and wait for it to finish starting. |
| The page doesn't load for the first minute | It's still starting. Check `docker compose logs -f web`; it's ready at `Listening at: http://0.0.0.0:8000`. |
| `Login failed for user 'sa'` in the logs | `DB_PASSWORD` in `website\.env` doesn't match learn-databases. Fix it, then run `.\up.bat` again. |
| `port is already allocated` | Something else is using port 5000. See the Mac note above to use another port. |

More detail, including running without Docker and the tests, is in
[`website/README.md`](website/README.md).

## Data use

Event data comes from the Ticketmaster Discovery API and is used for academic
purposes only.

## References

Anthropic. (2026). *Claude Code* (Claude Opus 5.5, model `claude-opus-5-5`) [AI coding assistant]. https://claude.com/claude-code

The data scripts (`fetch_ticketmaster.py`, `generate_inserts.py`, `filter_events.py`)
and the website were written with AI assistance, directed, reviewed and tested by the
team. See `website/README.md` for details on the website. Per the project charter, the
team writes the data models and the project's SQL.
