"""Print every account: customers with their names, venues with the venue they manage.

    .venv/Scripts/python show_accounts.py
"""

from sqlalchemy import select

from db import accounts, customers, engine, init_db, venues

init_db()
with engine.connect() as conn:
    rows = conn.execute(
        select(accounts, customers.c.first_name, customers.c.last_name, customers.c.phone,
               venues.c.venue_id, venues.c.name.label("venue_name"), venues.c.city)
        .outerjoin(customers, customers.c.account_id == accounts.c.account_id)
        .outerjoin(venues, venues.c.account_id == accounts.c.account_id)
        .order_by(accounts.c.account_id)).all()

print(f"{len(rows)} account(s) in {engine.url.render_as_string(hide_password=True)}\n")
for r in rows:
    if r.account_type == "customer":
        who = f"customer  {r.first_name} {r.last_name}, phone={r.phone or 'NULL'}"
    else:
        who = f"venue     #{r.venue_id} {r.venue_name} ({r.city})"
    print(f"#{r.account_id}  {r.email:<32} {who}")
    print(f"     created {r.created_at:%Y-%m-%d %H:%M} UTC, active={int(r.is_active)}, "
          f"hash={r.password_hash[:30]}...")
