"""
SQLite database for HOS logs and IFTA fuel tax records.

Schema mirrors the physical paper forms:
  - hos_logs       → FMCSA Form 395.8 header fields
  - hos_entries    → 395.8 duty status grid rows
  - ifta_fuel      → IFTA-100 fuel purchase detail (per-receipt rows)
  - ifta_crossings → State line crossings (used to compute miles per jurisdiction)
"""

import os
import sqlite3
from datetime import date, datetime

DB_PATH = "data/truck_ai.db"


# ── Setup ─────────────────────────────────────────────────────────────────────

def _connect() -> sqlite3.Connection:
    os.makedirs("data", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with _connect() as conn:
        conn.executescript("""
            -- Driver profile — single row, pre-fills every log
            CREATE TABLE IF NOT EXISTS driver_profile (
                id          INTEGER PRIMARY KEY DEFAULT 1,
                driver_name TEXT,
                updated_at  TEXT DEFAULT (datetime('now'))
            );

            -- FMCSA 395.8 header — one row per calendar day
            CREATE TABLE IF NOT EXISTS hos_logs (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                log_date             DATE    NOT NULL UNIQUE,
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
                created_at           TEXT    DEFAULT (datetime('now'))
            );

            -- 395.8 duty status grid — each row is one status period
            CREATE TABLE IF NOT EXISTS hos_entries (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                log_date     DATE NOT NULL,
                status       TEXT NOT NULL CHECK(status IN (
                                 'off_duty','sleeper_berth',
                                 'driving','on_duty_not_driving')),
                start_time   TEXT NOT NULL,
                end_time     TEXT,
                location     TEXT,
                remarks      TEXT,
                created_at   TEXT DEFAULT (datetime('now'))
            );

            -- IFTA-100 fuel purchase detail — one row per receipt
            CREATE TABLE IF NOT EXISTS ifta_fuel (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
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
                created_at       TEXT DEFAULT (datetime('now'))
            );

            -- State/province crossings — used to calculate miles per jurisdiction
            CREATE TABLE IF NOT EXISTS ifta_crossings (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                crossing_date      DATE NOT NULL,
                crossing_time      TEXT NOT NULL,
                jurisdiction       TEXT NOT NULL,
                odometer           INTEGER NOT NULL,
                created_at         TEXT DEFAULT (datetime('now'))
            );
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
    with _connect() as conn:
        # Close the previous open entry
        conn.execute(
            """UPDATE hos_entries SET end_time = ?
               WHERE log_date = ? AND end_time IS NULL""",
            (start_time, today),
        )
        conn.execute(
            """INSERT INTO hos_entries (log_date, status, start_time, location, remarks)
               VALUES (?, ?, ?, ?, ?)""",
            (today, status, start_time, location, remarks),
        )
        # Ensure a daily log header row exists
        conn.execute(
            "INSERT OR IGNORE INTO hos_logs (log_date) VALUES (?)", (today,)
        )
    return get_hos_summary(today)


def get_hos_summary(log_date: str = None) -> dict:
    """Return driving hours, on-duty hours, and remaining limits for a given date."""
    today = log_date or date.today().isoformat()
    with _connect() as conn:
        rows = conn.execute(
            """SELECT status, start_time, end_time
               FROM hos_entries WHERE log_date = ?
               ORDER BY start_time""",
            (today,),
        ).fetchall()

    totals = {s: 0.0 for s in ("driving", "on_duty_not_driving", "off_duty", "sleeper_berth")}
    for row in rows:
        end = row["end_time"] or datetime.now().strftime("%H:%M")
        hrs = _hours_between(today, row["start_time"], today, end)
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
    with _connect() as conn:
        rows = conn.execute(
            """SELECT log_date, status, start_time, end_time
               FROM hos_entries
               WHERE log_date >= date('now', '-7 days')
               AND status IN ('driving', 'on_duty_not_driving')""",
        ).fetchall()

    total = 0.0
    now = datetime.now()
    for row in rows:
        end = row["end_time"] or now.strftime("%H:%M")
        total += _hours_between(row["log_date"], row["start_time"], row["log_date"], end)

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
    cols = ", ".join(f"{k} = ?" for k in updates)
    with _connect() as conn:
        conn.execute(f"INSERT OR IGNORE INTO hos_logs (log_date) VALUES (?)", (today,))
        conn.execute(f"UPDATE hos_logs SET {cols} WHERE log_date = ?",
                     (*updates.values(), today))
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
    with _connect() as conn:
        conn.execute(
            """INSERT INTO ifta_fuel
               (purchase_date, jurisdiction, fuel_type, gallons, price_per_gallon,
                total_amount, vendor, vendor_city, receipt_number, odometer)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
    with _connect() as conn:
        conn.execute(
            """INSERT INTO ifta_crossings (crossing_date, crossing_time, jurisdiction, odometer)
               VALUES (?, ?, ?, ?)""",
            (today, now_time, jurisdiction.upper(), odometer),
        )
    return {"recorded": True, "jurisdiction": jurisdiction.upper(), "odometer": odometer}


def get_ifta_summary(quarter: int, year: int) -> dict:
    """
    Compile IFTA-100 data for a quarter.
    Returns miles and fuel by jurisdiction, plus fleet MPG.
    """
    start_month = (quarter - 1) * 3 + 1
    # Last day of the quarter's final month
    end_month = start_month + 2
    if end_month in (1, 3, 5, 7, 8, 10, 12):
        end_day = 31
    elif end_month in (4, 6, 9, 11):
        end_day = 30
    else:
        end_day = 29 if year % 4 == 0 else 28
    start_date = f"{year}-{start_month:02d}-01"
    end_date = f"{year}-{end_month:02d}-{end_day}"

    with _connect() as conn:
        fuel_rows = conn.execute(
            """SELECT jurisdiction,
                      SUM(gallons)   AS total_gallons,
                      SUM(total_amount) AS total_cost
               FROM ifta_fuel
               WHERE purchase_date BETWEEN ? AND ?
               GROUP BY jurisdiction""",
            (start_date, end_date),
        ).fetchall()

        crossing_rows = conn.execute(
            """SELECT jurisdiction, odometer
               FROM ifta_crossings
               WHERE crossing_date BETWEEN ? AND ?
               ORDER BY crossing_date, crossing_time""",
            (start_date, end_date),
        ).fetchall()

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
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM hos_logs WHERE log_date = ?", (log_date,)
        ).fetchone()
    return dict(row) if row else None


def get_open_entries(log_date: str) -> list[dict]:
    """Return duty status entries with no end_time for a given date."""
    with _connect() as conn:
        rows = conn.execute(
            """SELECT * FROM hos_entries
               WHERE log_date = ? AND end_time IS NULL
               ORDER BY start_time""",
            (log_date,),
        ).fetchall()
    return [dict(r) for r in rows]


def close_entry(entry_id: int, end_time: str):
    """Set end_time on a specific duty status entry."""
    with _connect() as conn:
        conn.execute(
            "UPDATE hos_entries SET end_time = ? WHERE id = ?",
            (end_time, entry_id),
        )


def get_driver_name() -> str | None:
    with _connect() as conn:
        row = conn.execute("SELECT driver_name FROM driver_profile WHERE id = 1").fetchone()
    return row["driver_name"] if row else None


def get_driver_profile() -> dict:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM driver_profile WHERE id = 1").fetchone()
    return dict(row) if row else {}


def set_driver_name(name: str):
    with _connect() as conn:
        conn.execute(
            """INSERT INTO driver_profile (id, driver_name) VALUES (1, ?)
               ON CONFLICT(id) DO UPDATE SET driver_name = excluded.driver_name,
               updated_at = datetime('now')""",
            (name,),
        )


def set_driver_profile(
    driver_name: str = None,
    carrier_address: str = None,
    home_terminal: str = None,
):
    with _connect() as conn:
        conn.execute(
            """INSERT INTO driver_profile (id, driver_name, carrier_address, home_terminal)
               VALUES (1, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   driver_name     = COALESCE(excluded.driver_name,     driver_name),
                   carrier_address = COALESCE(excluded.carrier_address, carrier_address),
                   home_terminal   = COALESCE(excluded.home_terminal,   home_terminal),
                   updated_at      = datetime('now')""",
            (driver_name, carrier_address, home_terminal),
        )


def certify_log(log_date: str = None) -> bool:
    today = log_date or date.today().isoformat()
    now = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        conn.execute("INSERT OR IGNORE INTO hos_logs (log_date) VALUES (?)", (today,))
        conn.execute(
            "UPDATE hos_logs SET certified = 1, certified_at = ? WHERE log_date = ?",
            (now, today),
        )
    return True


# ── Migrations (safe to run on existing DBs) ─────────────────────────────────

def _migrate():
    with _connect() as conn:
        for sql in [
            "ALTER TABLE hos_logs ADD COLUMN odometer_start INTEGER",
            "ALTER TABLE hos_logs ADD COLUMN odometer_end INTEGER",
            "ALTER TABLE hos_logs ADD COLUMN certified_at TEXT",
            "ALTER TABLE driver_profile ADD COLUMN carrier_address TEXT",
            "ALTER TABLE driver_profile ADD COLUMN home_terminal TEXT",
        ]:
            try:
                conn.execute(sql)
            except Exception:
                pass


# Initialize on import
init_db()
_migrate()
