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

## Running the website

See [`website/README.md`](website/README.md). In short: start the course's
`learn-databases` SQL Server container, then run `website\up.bat` and open
http://127.0.0.1:5000.

## Data use

Event data comes from the Ticketmaster Discovery API and is used for academic
purposes only. Keep this repository private and don't redistribute the data.

## References

Anthropic. (2026). *Claude Code* (Claude Opus 5.5, model `claude-opus-5-5`) [AI coding assistant]. https://claude.com/claude-code

The data scripts (`fetch_ticketmaster.py`, `generate_inserts.py`, `filter_events.py`)
and the website were written with AI assistance, directed, reviewed and tested by the
team. See `website/README.md` for details on the website. Per the project charter, the
team writes the data models and the project's SQL.
