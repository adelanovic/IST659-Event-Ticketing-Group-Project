"""Delete everything the website has stored: accounts, claimed venues, listed events,
and the loaded Ticketmaster data.

    down.bat                                  (stops the site, then runs this)
    .venv/Scripts/python reset_db.py          (asks first)
    .venv/Scripts/python reset_db.py --yes    (no question)

On SQL Server this drops the whole database named by DB_NAME (concert_tickets).
The next start creates it again and reloads the Ticketmaster data.
"""

import sys

from db import database_label, drop_database

label = database_label()
if "--yes" not in sys.argv:
    answer = input(f"Delete ALL data in the {label}? Type yes to continue: ")
    if answer.strip().lower() != "yes":
        sys.exit("Nothing deleted.")

drop_database()
print(f"Deleted the {label}.")
