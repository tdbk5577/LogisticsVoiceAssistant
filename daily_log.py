"""
DailyLogChecker — runs at startup to:
  1. Complete any missing fields from the prior day's 395.8 log
  2. Prompt for required fields on today's log if it hasn't been started
"""

import re
from datetime import date, timedelta

import database as db

# ── Parsers ───────────────────────────────────────────────────────────────────

_STATE_ABBREVS = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "idaho": "ID", "illinois": "IL",
    "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY",
    "louisiana": "LA", "maine": "ME", "maryland": "MD", "massachusetts": "MA",
    "michigan": "MI", "minnesota": "MN", "mississippi": "MS", "missouri": "MO",
    "montana": "MT", "nebraska": "NE", "nevada": "NV", "new hampshire": "NH",
    "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH",
    "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA",
    "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
    "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT",
    "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY",
}

_STATUS_MAP = {
    "driving": "driving",
    "drive": "driving",
    "off duty": "off_duty",
    "off": "off_duty",
    "sleeper berth": "sleeper_berth",
    "sleeper": "sleeper_berth",
    "bunk": "sleeper_berth",
    "on duty not driving": "on_duty_not_driving",
    "on duty": "on_duty_not_driving",
    "not driving": "on_duty_not_driving",
    "loading": "on_duty_not_driving",
    "unloading": "on_duty_not_driving",
    "fueling": "on_duty_not_driving",
    "inspecting": "on_duty_not_driving",
}


def _parse_status(text: str) -> str | None:
    t = text.lower()
    for kw, val in _STATUS_MAP.items():
        if kw in t:
            return val
    return None


def _parse_time(text: str) -> str | None:
    t = (text.lower()
         .replace("oh", "0")
         .replace("o'clock", ":00")
         .replace("hundred", ":00"))

    pm = "pm" in t
    am = "am" in t

    def _apply_ampm(h: int) -> int:
        if pm and h < 12:
            return h + 12
        if am and h == 12:
            return 0
        return h

    # "7:30", "07:30"
    m = re.search(r'(\d{1,2}):(\d{2})', t)
    if m:
        h, mn = _apply_ampm(int(m.group(1))), int(m.group(2))
        if 0 <= h <= 23 and 0 <= mn <= 59:
            return f"{h:02d}:{mn:02d}"

    # "7 30" (two separate numbers)
    m = re.search(r'\b(\d{1,2})\s+(\d{2})\b', t)
    if m:
        h, mn = _apply_ampm(int(m.group(1))), int(m.group(2))
        if 0 <= h <= 23 and 0 <= mn <= 59:
            return f"{h:02d}:{mn:02d}"

    # "730" or "1430"
    m = re.search(r'\b(\d{3,4})\b', t)
    if m:
        n = m.group(1)
        h, mn = (int(n[0]), int(n[1:])) if len(n) == 3 else (int(n[:2]), int(n[2:]))
        h = _apply_ampm(h)
        if 0 <= h <= 23 and 0 <= mn <= 59:
            return f"{h:02d}:{mn:02d}"

    # Just an hour: "7" or "14"
    m = re.search(r'\b(\d{1,2})\b', t)
    if m:
        h = _apply_ampm(int(m.group(1)))
        if 0 <= h <= 23:
            return f"{h:02d}:00"

    return None


def _parse_odometer(text: str) -> int | None:
    digits = re.sub(r'[^0-9]', '', text)
    return int(digits) if len(digits) >= 4 else None


def _parse_jurisdiction(text: str) -> str | None:
    t = text.lower().strip()
    if t in _STATE_ABBREVS:
        return _STATE_ABBREVS[t]
    letters = re.sub(r'[^a-zA-Z]', '', t).upper()
    return letters[:2] if len(letters) >= 2 else None


def _parse_gallons(text: str) -> float | None:
    m = re.search(r'\b(\d+(?:\.\d+)?)\b', text)
    return float(m.group(1)) if m else None


def _is_skip(text: str) -> bool:
    return bool(text) and any(
        w in text.lower() for w in ("none", "skip", "no", "n/a", "nothing", "same")
    )


# ── Checker ───────────────────────────────────────────────────────────────────

class DailyLogChecker:
    def __init__(self, voice_engine):
        self._v = voice_engine

    def run(self):
        self._ensure_driver_profile()
        self._complete_prior_day()
        self._certify_yesterday()
        self._start_today()

    def review_ifta(self):
        """Prompt for any unlogged fuel stops and state crossings for today."""
        today = date.today().isoformat()
        if not db.get_hos_log(today):
            self._v.speak("No log started for today yet.")
            return
        self._review_prior_ifta(today)

    # ── Driver profile setup (first run only) ─────────────────────────────────

    def _ensure_driver_profile(self):
        profile = db.get_driver_profile()

        if not profile.get("driver_name"):
            self._v.speak("Welcome to Truck AI. What is your full name for the driver logs?")
            name = self._v.listen(timeout=12, phrase_limit=10)
            name = name.title() if name else "Driver"
            db.set_driver_profile(driver_name=name)
            self._v.speak(f"Got it, {name}. I'll use that on every log.")

        if not profile.get("carrier_address"):
            self._v.speak(
                "One-time setup: what is your carrier's address? "
                "Include street, city, and state."
            )
            addr = self._v.listen(timeout=15, phrase_limit=20)
            if addr:
                db.set_driver_profile(carrier_address=addr)

        if not profile.get("home_terminal"):
            self._v.speak("What city and state is your home terminal?")
            terminal = self._v.listen(timeout=12, phrase_limit=10)
            if terminal:
                db.set_driver_profile(home_terminal=terminal)

    # ── Prior day completion ───────────────────────────────────────────────────

    def _complete_prior_day(self):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        log = db.get_hos_log(yesterday)
        if not log:
            return

        open_entries = db.get_open_entries(yesterday)
        missing_odo = not log.get("odometer_end")

        if not open_entries and not missing_odo:
            return

        self._v.speak(f"Yesterday's log for {yesterday} has missing information. Let's complete it first.")

        # Close open duty status entries
        for entry in open_entries:
            status = entry["status"].replace("_", " ")
            loc = entry["location"] or "unknown location"
            t = self._ask_time(
                f"Your {status} started at {entry['start_time']} in {loc} "
                f"has no end time. What time did it end?"
            )
            if t:
                db.close_entry(entry["id"], t)

        # Odometer end
        if missing_odo:
            odo = self._ask_odometer("What was your ending odometer reading for yesterday?")
            if odo:
                db.update_log_header(yesterday, odometer_end=odo)
                # Auto-calculate total miles if we have both odometer readings
                if log.get("odometer_start"):
                    total = odo - log["odometer_start"]
                    db.update_log_header(yesterday, total_miles_today=total)

        summary = db.get_hos_summary(yesterday)
        self._v.speak(
            f"Yesterday's log complete. "
            f"{summary['driving_hours']} hours driving, "
            f"{summary['on_duty_hours']} hours on duty total."
        )

    # ── New day startup ────────────────────────────────────────────────────────

    def _start_today(self):
        today = date.today().isoformat()
        if db.get_hos_log(today):
            return  # Already started today

        yesterday = (date.today() - timedelta(days=1)).isoformat()
        prev = db.get_hos_log(yesterday) or {}
        driver = db.get_driver_name() or "Driver"
        weekday = date.today().strftime("%A, %B %d")

        self._v.speak(f"Starting log for {weekday}. Driver: {driver}.")

        # Carrier name — carry forward or prompt
        carrier = self._carry_forward(
            "carrier", prev.get("carrier_name"),
            "What is your carrier name?"
        )

        # Truck/tractor number — carry forward or prompt
        truck = self._carry_forward(
            "truck number", prev.get("truck_number"),
            "What is your truck or tractor number?"
        )

        # Trailer number — always prompt, changes frequently
        trailer_resp = self._ask_optional(
            "What is your trailer number? Say none if you're bobtail."
        )
        trailer = None if not trailer_resp or _is_skip(trailer_resp) else trailer_resp

        # From location — default to yesterday's to_location
        from_default = prev.get("to_location") or prev.get("from_location")
        from_loc = self._carry_forward(
            "starting location", from_default,
            "Where are you starting from today? City and state."
        )

        # To location — always prompt
        to_loc = self._ask_text("Where are you headed today? City and state.")

        # Co-driver — carry forward or prompt
        co_resp = self._ask_optional(
            "Any co-driver today? Say their name, or say none if you're solo."
        )
        co_driver = None if not co_resp or _is_skip(co_resp) else co_resp

        # BOL and shipping doc numbers — optional
        bol_resp = self._ask_optional(
            "Any Bill of Lading or shipping document numbers? Say none to skip."
        )
        bol = None if not bol_resp or _is_skip(bol_resp) else bol_resp

        # Odometer start — required
        odo_start = self._ask_odometer("What is your starting odometer reading?")

        # Save header
        db.update_log_header(
            today,
            carrier_name=carrier,
            truck_number=truck,
            trailer_number=trailer,
            from_location=from_loc,
            to_location=to_loc,
            co_driver=co_driver,
            bol_numbers=bol,
            odometer_start=odo_start,
        )

        # First duty status — required
        self._v.speak(
            "What is your current duty status? "
            "Say driving, off duty, sleeper berth, or on duty not driving."
        )
        status = self._ask_status()

        # Start time — required
        start_time = self._ask_time(
            "What time did that start? For example say 7 30 for seven thirty."
        )

        # Location — required
        location = self._ask_text("What city and state are you in?")

        if status and start_time:
            db.log_duty_status(
                status=status,
                start_time=start_time,
                location=location or "",
                log_date=today,
            )
            self._v.speak(
                f"Log started. {status.replace('_', ' ')} at {start_time}. "
                "Have a safe drive."
            )
        else:
            self._v.speak(
                "Couldn't complete the log start. "
                "Say 'Hey Truck, I started driving' anytime to log your status."
            )

    # ── IFTA daily review ─────────────────────────────────────────────────────

    def _review_prior_ifta(self, log_date: str):
        self._v.speak("Any fuel stops not already logged? Say yes or no.")
        resp = self._v.listen(timeout=8, phrase_limit=5)
        if resp and "yes" in resp.lower():
            self._collect_fuel_stops(log_date)

        self._v.speak("Any state line crossings not already logged? Say yes or no.")
        resp = self._v.listen(timeout=8, phrase_limit=5)
        if resp and "yes" in resp.lower():
            self._collect_state_crossings(log_date)

    def _collect_fuel_stops(self, log_date: str):
        while True:
            jurisdiction = self._ask_jurisdiction(
                "What two-letter state code did you fuel in? For example, say T X for Texas."
            )
            if not jurisdiction:
                self._v.speak("Couldn't get state, skipping fuel stop.")
                break

            gallons = self._ask_gallons("How many gallons?")
            if not gallons:
                self._v.speak("Couldn't get gallons, skipping this stop.")
            else:
                price_resp = self._ask_optional("Price per gallon? Say skip if unknown.")
                price = None
                if price_resp and not _is_skip(price_resp):
                    m = re.search(r'\d+(?:\.\d+)?', price_resp)
                    price = float(m.group()) if m else None

                vendor_resp = self._ask_optional("Vendor name? Say skip if unknown.")
                vendor = None if not vendor_resp or _is_skip(vendor_resp) else vendor_resp

                db.log_fuel_purchase(
                    jurisdiction=jurisdiction,
                    gallons=gallons,
                    price_per_gallon=price,
                    vendor=vendor or "",
                    purchase_date=log_date,
                )
                self._v.speak(f"Logged {gallons} gallons in {jurisdiction}.")

            self._v.speak("Any more fuel stops? Say yes or no.")
            resp = self._v.listen(timeout=8, phrase_limit=5)
            if not resp or "yes" not in resp.lower():
                break

    def _collect_state_crossings(self, log_date: str):
        while True:
            jurisdiction = self._ask_jurisdiction(
                "What state did you enter? Say the two-letter code."
            )
            if not jurisdiction:
                self._v.speak("Couldn't get state, skipping crossing.")
                break

            odometer = self._ask_odometer(f"Odometer when you entered {jurisdiction}?")
            crossing_time = self._ask_time(f"What time did you cross into {jurisdiction}?")

            if odometer:
                db.log_state_crossing(
                    jurisdiction=jurisdiction,
                    odometer=odometer,
                    crossing_time=crossing_time,
                    crossing_date=log_date,
                )
                self._v.speak(f"Logged entry into {jurisdiction}.")

            self._v.speak("Any more crossings? Say yes or no.")
            resp = self._v.listen(timeout=8, phrase_limit=5)
            if not resp or "yes" not in resp.lower():
                break

    # ── Driver certification ──────────────────────────────────────────────────

    def _certify_yesterday(self):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        log = db.get_hos_log(yesterday)
        if not log or log.get("certified"):
            return
        self._v.speak(
            f"Do you certify yesterday's log for {yesterday} "
            "is true and correct? Say yes or no."
        )
        resp = self._v.listen(timeout=10, phrase_limit=5)
        if resp and "yes" in resp.lower():
            db.certify_log(yesterday)
            self._v.speak("Log certified.")
        else:
            self._v.speak("Okay, you can certify it later.")

    # ── Voice helpers ─────────────────────────────────────────────────────────

    def _ask_text(self, prompt: str) -> str | None:
        self._v.speak(prompt)
        return self._v.listen(timeout=12, phrase_limit=15)

    def _ask_optional(self, prompt: str) -> str | None:
        self._v.speak(prompt)
        return self._v.listen(timeout=10, phrase_limit=15)

    def _ask_time(self, prompt: str) -> str | None:
        self._v.speak(prompt)
        for attempt in range(2):
            resp = self._v.listen(timeout=10, phrase_limit=8)
            if resp:
                t = _parse_time(resp)
                if t:
                    return t
            if attempt == 0:
                self._v.speak("Say the time as numbers — for example 7 30 or 14 45.")
        return None

    def _ask_odometer(self, prompt: str) -> int | None:
        self._v.speak(prompt)
        for attempt in range(2):
            resp = self._v.listen(timeout=10, phrase_limit=10)
            if resp:
                odo = _parse_odometer(resp)
                if odo:
                    return odo
            if attempt == 0:
                self._v.speak("Please say your odometer reading as a number.")
        return None

    def _ask_status(self) -> str | None:
        for attempt in range(3):
            resp = self._v.listen(timeout=10, phrase_limit=8)
            if resp:
                s = _parse_status(resp)
                if s:
                    return s
            if attempt < 2:
                self._v.speak(
                    "Please say driving, off duty, sleeper berth, or on duty not driving."
                )
        return None

    def _ask_jurisdiction(self, prompt: str) -> str | None:
        self._v.speak(prompt)
        for attempt in range(2):
            resp = self._v.listen(timeout=8, phrase_limit=5)
            if resp:
                j = _parse_jurisdiction(resp)
                if j:
                    return j
            if attempt == 0:
                self._v.speak("Say the two-letter state code, like T X for Texas.")
        return None

    def _ask_gallons(self, prompt: str) -> float | None:
        self._v.speak(prompt)
        for attempt in range(2):
            resp = self._v.listen(timeout=8, phrase_limit=5)
            if resp:
                g = _parse_gallons(resp)
                if g:
                    return g
            if attempt == 0:
                self._v.speak("Say a number, for example eighty or one hundred twenty.")
        return None

    def _carry_forward(self, label: str, prev_value: str | None, prompt: str) -> str | None:
        if prev_value:
            self._v.speak(f"Same {label} as yesterday — {prev_value}? Say yes or no.")
            resp = self._v.listen(timeout=8, phrase_limit=5)
            if resp and "yes" in resp.lower():
                return prev_value
        return self._ask_text(prompt)
