"""
PostgreSQL database for HOS logs and IFTA fuel tax records.

Schema mirrors the physical paper forms:
  - hos_logs       → FMCSA Form 395.8 header fields
  - hos_entries    → 395.8 duty status grid rows
  - ifta_fuel      → IFTA-100 fuel purchase detail (per-receipt rows)
  - ifta_crossings → State line crossings (used to compute miles per jurisdiction)
"""

import os
from contextlib import contextmanager
from datetime import date, datetime

import psycopg2
import psycopg2.extras

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/truck_ai")

_TS_NOW = "TO_CHAR(NOW(), 'YYYY-MM-DD\"T\"HH24:MI:SS')"


@contextmanager
def _connect():
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with _connect() as cur:
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS driver_profile (
                id              INTEGER PRIMARY KEY,
                driver_name     TEXT,
                carrier_address TEXT,
                home_terminal   TEXT,
                updated_at      TEXT DEFAULT {_TS_NOW}
            )
        """)
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS hos_logs (
                id                   SERIAL PRIMARY KEY,
                log_date             DATE NOT NULL UNIQUE,
                from_location        TEXT,
                to_location          TEXT,
                odometer_start       INTEGER,
                odometer_end         INTEGER,
                total_miles_today    INTEGER,
                carrier_name         TEXT,
                truck_number         TEXT,
                trailer_number       TEXT,
                co_driver            TEXT,
                bol_numbers          TEXT,
                shipping_doc_numbers TEXT,
                certified            INTEGER DEFAULT 0,
                certified_at         TEXT,
                created_at           TEXT DEFAULT {_TS_NOW}
            )
        """)
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS hos_entries (
                id         SERIAL PRIMARY KEY,
                log_date   DATE NOT NULL,
                status     TEXT NOT NULL CHECK(status IN (
                               'off_duty','sleeper_berth',
                               'driving','on_duty_not_driving')),
                start_time TEXT NOT NULL,
                end_time   TEXT,
                location   TEXT,
                remarks    TEXT,
                created_at TEXT DEFAULT {_TS_NOW}
            )
        """)
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS ifta_fuel (
                id               SERIAL PRIMARY KEY,
                purchase_date    DATE NOT NULL,
                jurisdiction     TEXT NOT NULL,
                fuel_type        TEXT NOT NULL DEFAULT 'diesel',
                gallons          REAL NOT NULL,
                price_per_gallon REAL,
                total_amount     REAL,
                vendor           TEXT,
                vendor_city      TEXT,
                receipt_number   TEXT,
                odometer         INTEGER,
                vehicle_unit     TEXT,
                created_at       TEXT DEFAULT {_TS_NOW}
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS alertness_logs (
                id                SERIAL PRIMARY KEY,
                timestamp         TEXT NOT NULL,
                level             TEXT NOT NULL,
                overall_score     REAL NOT NULL,
                memory_recalled   INTEGER,
                math_correct      INTEGER,
                math_avg_time     REAL,
                reaction_avg_time REAL
            )
        """)
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS ifta_crossings (
                id            SERIAL PRIMARY KEY,
                crossing_date DATE NOT NULL,
                crossing_time TEXT NOT NULL,
                jurisdiction  TEXT NOT NULL,
                odometer      INTEGER NOT NULL,
                created_at    TEXT DEFAULT {_TS_NOW}
            )
        """)


# ── HOS ───────────────────────────────────────────────────────────────────────

def log_duty_status(
    status: str,
    start_time: str,
    location: str = "",
    remarks: str = "",
    log_date: str = None,
) -> dict:
    """
    Record a duty status change for today's 395.8 log.
    Automatically closes the previous open entry.
    Returns a summary of hours remaining.
    """
    today = log_date or date.today().isoformat()
    with _connect() as cur:
        cur.execute(
            """UPDATE hos_entries SET end_time = %s
               WHERE log_date = %s AND end_time IS NULL""",
            (start_time, today),
        )
        cur.execute(
            """INSERT INTO hos_entries (log_date, status, start_time, location, remarks)
               VALUES (%s, %s, %s, %s, %s)""",
            (today, status, start_time, location, remarks),
        )
        cur.execute(
            "INSERT INTO hos_logs (log_date) VALUES (%s) ON CONFLICT DO NOTHING", (today,)
        )
    return get_hos_summary(today)


def get_hos_summary(log_date: str = None) -> dict:
    """Return driving hours, on-duty hours, and remaining limits for a given date."""
    today = log_date or date.today().isoformat()
    with _connect() as cur:
        cur.execute(
            """SELECT status, start_time, end_time
               FROM hos_entries WHERE log_date = %s
               ORDER BY start_time""",
            (today,),
        )
        rows = cur.fetchall()

    totals = {s: 0.0 for s in ("driving", "on_duty_not_driving", "off_duty", "sleeper_berth")}
    for row in rows:
        end = row["end_time"] or datetime.now().strftime("%H:%M")
        hrs = _hours_between(str(today), row["start_time"], str(today), end)
        if row["status"] in totals:
            totals[row["status"]] += hrs

    driving = round(totals["driving"], 2)
    on_duty = round(totals["driving"] + totals["on_duty_not_driving"], 2)
    return {
        "date": today,
        "driving_hours": driving,
        "on_duty_hours": on_duty,
        "driving_remaining": round(max(0, 11 - driving), 2),
        "on_duty_remaining": round(max(0, 14 - on_duty), 2),
        "entries": len(rows),
    }


def get_weekly_hours() -> dict:
    """Return total on-duty hours in the current 8-day rolling window."""
    with _connect() as cur:
        cur.execute(
            """SELECT log_date, status, start_time, end_time
               FROM hos_entries
               WHERE log_date >= CURRENT_DATE - INTERVAL '7 days'
               AND status IN ('driving', 'on_duty_not_driving')"""
        )
        rows = cur.fetchall()

    total = 0.0
    now = datetime.now()
    for row in rows:
        end = row["end_time"] or now.strftime("%H:%M")
        total += _hours_between(str(row["log_date"]), row["start_time"], str(row["log_date"]), end)

    used = round(total, 2)
    return {
        "weekly_on_duty_hours": used,
        "weekly_limit": 70,
        "hours_remaining": round(max(0, 70 - used), 2),
    }


def update_log_header(log_date: str = None, **fields) -> bool:
    """Update the 395.8 header fields for a given day."""
    today = log_date or date.today().isoformat()
    allowed = {
        "from_location", "to_location", "total_miles_today",
        "odometer_start", "odometer_end",
        "carrier_name", "truck_number", "trailer_number",
        "co_driver", "bol_numbers", "shipping_doc_numbers",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return False
    cols = ", ".join(f"{k} = %s" for k in updates)
    with _connect() as cur:
        cur.execute(
            "INSERT INTO hos_logs (log_date) VALUES (%s) ON CONFLICT DO NOTHING", (today,)
        )
        cur.execute(
            f"UPDATE hos_logs SET {cols} WHERE log_date = %s",
            (*updates.values(), today),
        )
    return True


# ── IFTA ─────────────────────────────────────────────────────────────────────

def log_fuel_purchase(
    jurisdiction: str,
    gallons: float,
    purchase_date: str = None,
    price_per_gallon: float = None,
    vendor: str = "",
    vendor_city: str = "",
    receipt_number: str = "",
    odometer: int = None,
    fuel_type: str = "diesel",
) -> dict:
    """Record a fuel purchase receipt for IFTA reporting."""
    today = purchase_date or date.today().isoformat()
    total = round(gallons * price_per_gallon, 2) if price_per_gallon else None
    with _connect() as cur:
        cur.execute(
            """INSERT INTO ifta_fuel
               (purchase_date, jurisdiction, fuel_type, gallons, price_per_gallon,
                total_amount, vendor, vendor_city, receipt_number, odometer)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (today, jurisdiction.upper(), fuel_type, gallons,
             price_per_gallon, total, vendor, vendor_city, receipt_number, odometer),
        )
    return {
        "recorded": True,
        "date": today,
        "jurisdiction": jurisdiction.upper(),
        "gallons": gallons,
        "total_cost": total,
        "vendor": vendor or "unknown",
    }


def log_state_crossing(
    jurisdiction: str,
    odometer: int,
    crossing_time: str = None,
    crossing_date: str = None,
) -> dict:
    """Record entering a new state/province for IFTA mileage tracking."""
    today = crossing_date or date.today().isoformat()
    now_time = crossing_time or datetime.now().strftime("%H:%M")
    with _connect() as cur:
        cur.execute(
            """INSERT INTO ifta_crossings (crossing_date, crossing_time, jurisdiction, odometer)
               VALUES (%s, %s, %s, %s)""",
            (today, now_time, jurisdiction.upper(), odometer),
        )
    return {"recorded": True, "jurisdiction": jurisdiction.upper(), "odometer": odometer}


def get_ifta_summary(quarter: int, year: int) -> dict:
    """
    Compile IFTA-100 data for a quarter.
    Returns miles and fuel by jurisdiction, plus fleet MPG.
    """
    start_month = (quarter - 1) * 3 + 1
    end_month = start_month + 2
    if end_month in (1, 3, 5, 7, 8, 10, 12):
        end_day = 31
    elif end_month in (4, 6, 9, 11):
        end_day = 30
    else:
        end_day = 29 if year % 4 == 0 else 28
    start_date = f"{year}-{start_month:02d}-01"
    end_date = f"{year}-{end_month:02d}-{end_day}"

    with _connect() as cur:
        cur.execute(
            """SELECT jurisdiction,
                      SUM(gallons)      AS total_gallons,
                      SUM(total_amount) AS total_cost
               FROM ifta_fuel
               WHERE purchase_date BETWEEN %s AND %s
               GROUP BY jurisdiction""",
            (start_date, end_date),
        )
        fuel_rows = cur.fetchall()

        cur.execute(
            """SELECT jurisdiction, odometer
               FROM ifta_crossings
               WHERE crossing_date BETWEEN %s AND %s
               ORDER BY crossing_date, crossing_time""",
            (start_date, end_date),
        )
        crossing_rows = cur.fetchall()

    fuel_by_state = {
        r["jurisdiction"]: {
            "gallons": round(r["total_gallons"] or 0, 3),
            "cost": round(r["total_cost"] or 0, 2),
        }
        for r in fuel_rows
    }

    miles_by_state: dict[str, float] = {}
    for i, c in enumerate(crossing_rows[:-1]):
        miles = crossing_rows[i + 1]["odometer"] - c["odometer"]
        if miles > 0:
            miles_by_state[c["jurisdiction"]] = miles_by_state.get(c["jurisdiction"], 0) + miles

    total_miles = sum(miles_by_state.values())
    total_gallons = sum(v["gallons"] for v in fuel_by_state.values())
    mpg = round(total_miles / total_gallons, 2) if total_gallons > 0 else 0

    return {
        "quarter": f"Q{quarter} {year}",
        "period": f"{start_date} to {end_date}",
        "total_miles": int(total_miles),
        "total_gallons": round(total_gallons, 3),
        "fleet_mpg": mpg,
        "miles_by_jurisdiction": {k: int(v) for k, v in miles_by_state.items()},
        "fuel_by_jurisdiction": fuel_by_state,
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _hours_between(date1: str, time1: str, date2: str, time2: str) -> float:
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            t1 = datetime.strptime(f"{date1} {time1}", f"%Y-%m-%d {fmt}")
            t2 = datetime.strptime(f"{date2} {time2}", f"%Y-%m-%d {fmt}")
            return max(0.0, (t2 - t1).total_seconds() / 3600)
        except ValueError:
            continue
    return 0.0


def get_hos_log(log_date: str) -> dict | None:
    """Return the 395.8 header row for a given date, or None if not started."""
    with _connect() as cur:
        cur.execute("SELECT * FROM hos_logs WHERE log_date = %s", (log_date,))
        row = cur.fetchone()
    return dict(row) if row else None


def get_open_entries(log_date: str) -> list[dict]:
    """Return duty status entries with no end_time for a given date."""
    with _connect() as cur:
        cur.execute(
            """SELECT * FROM hos_entries
               WHERE log_date = %s AND end_time IS NULL
               ORDER BY start_time""",
            (log_date,),
        )
        rows = cur.fetchall()
    return [dict(r) for r in rows]


def close_entry(entry_id: int, end_time: str):
    """Set end_time on a specific duty status entry."""
    with _connect() as cur:
        cur.execute(
            "UPDATE hos_entries SET end_time = %s WHERE id = %s",
            (end_time, entry_id),
        )


def get_driver_name() -> str | None:
    with _connect() as cur:
        cur.execute("SELECT driver_name FROM driver_profile WHERE id = 1")
        row = cur.fetchone()
    return row["driver_name"] if row else None


def get_driver_profile() -> dict:
    with _connect() as cur:
        cur.execute("SELECT * FROM driver_profile WHERE id = 1")
        row = cur.fetchone()
    return dict(row) if row else {}


def set_driver_name(name: str):
    with _connect() as cur:
        cur.execute(
            f"""INSERT INTO driver_profile (id, driver_name) VALUES (1, %s)
               ON CONFLICT (id) DO UPDATE SET
                   driver_name = EXCLUDED.driver_name,
                   updated_at  = {_TS_NOW}""",
            (name,),
        )


def set_driver_profile(
    driver_name: str = None,
    carrier_address: str = None,
    home_terminal: str = None,
):
    with _connect() as cur:
        cur.execute(
            f"""INSERT INTO driver_profile (id, driver_name, carrier_address, home_terminal)
               VALUES (1, %s, %s, %s)
               ON CONFLICT (id) DO UPDATE SET
                   driver_name     = COALESCE(EXCLUDED.driver_name,     driver_profile.driver_name),
                   carrier_address = COALESCE(EXCLUDED.carrier_address, driver_profile.carrier_address),
                   home_terminal   = COALESCE(EXCLUDED.home_terminal,   driver_profile.home_terminal),
                   updated_at      = {_TS_NOW}""",
            (driver_name, carrier_address, home_terminal),
        )


def get_alertness_history(limit: int = 20) -> list[dict]:
    with _connect() as cur:
        cur.execute(
            "SELECT * FROM alertness_logs ORDER BY timestamp DESC LIMIT %s", (limit,)
        )
        rows = cur.fetchall()
    return [dict(r) for r in rows]


def save_alertness_log(
    timestamp: str,
    level: str,
    overall_score: float,
    memory_recalled: int,
    math_correct: int,
    math_avg_time: float,
    reaction_avg_time: float,
):
    with _connect() as cur:
        cur.execute(
            """INSERT INTO alertness_logs
               (timestamp, level, overall_score, memory_recalled,
                math_correct, math_avg_time, reaction_avg_time)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (timestamp, level, overall_score, memory_recalled,
             math_correct, math_avg_time, reaction_avg_time),
        )


def certify_log(log_date: str = None) -> bool:
    today = log_date or date.today().isoformat()
    now = datetime.now().isoformat(timespec="seconds")
    with _connect() as cur:
        cur.execute(
            "INSERT INTO hos_logs (log_date) VALUES (%s) ON CONFLICT DO NOTHING", (today,)
        )
        cur.execute(
            "UPDATE hos_logs SET certified = 1, certified_at = %s WHERE log_date = %s",
            (now, today),
        )
    return True


# ── Migrations (safe to run on existing DBs) ─────────────────────────────────

def _migrate():
    with _connect() as cur:
        for sql in [
            "ALTER TABLE hos_logs ADD COLUMN IF NOT EXISTS odometer_start INTEGER",
            "ALTER TABLE hos_logs ADD COLUMN IF NOT EXISTS odometer_end INTEGER",
            "ALTER TABLE hos_logs ADD COLUMN IF NOT EXISTS certified_at TEXT",
            "ALTER TABLE driver_profile ADD COLUMN IF NOT EXISTS carrier_address TEXT",
            "ALTER TABLE driver_profile ADD COLUMN IF NOT EXISTS home_terminal TEXT",
        ]:
            cur.execute(sql)


# Initialize on import
init_db()
_migrate()
