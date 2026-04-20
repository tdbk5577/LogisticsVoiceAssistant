"""
Build printable log reports from HOS and alertness records and email them.
"""

import re
from datetime import date, datetime

import requests

import config
import database as db


def parse_spoken_email(spoken: str) -> str:
    """Convert spoken email (e.g. 'john dot smith at gmail dot com') to standard format."""
    text = spoken.lower().strip()
    text = re.sub(r'\b(dot|period)\b', '.', text)
    text = re.sub(r'\b(at sign|at)\b', '@', text)
    text = re.sub(r'\b(underscore|under score)\b', '_', text)
    text = re.sub(r'\b(dash|hyphen|minus)\b', '-', text)
    text = text.replace(' ', '')
    return text


def build_hos_report(log_date: str = None) -> str:
    today = log_date or date.today().isoformat()
    header = db.get_hos_log(today)
    summary = db.get_hos_summary(today)

    lines = [
        "=" * 52,
        "  HOURS OF SERVICE LOG — FMCSA Form 395.8",
        f"  Date: {today}",
        "=" * 52,
    ]

    if header:
        if header.get("from_location"):
            lines.append(f"  From:      {header['from_location']}")
        if header.get("to_location"):
            lines.append(f"  To:        {header['to_location']}")
        if header.get("carrier_name"):
            lines.append(f"  Carrier:   {header['carrier_name']}")
        if header.get("truck_number"):
            lines.append(f"  Truck #:   {header['truck_number']}")
        if header.get("trailer_number"):
            lines.append(f"  Trailer #: {header['trailer_number']}")
        if header.get("co_driver"):
            lines.append(f"  Co-Driver: {header['co_driver']}")
        odo_start = header.get("odometer_start")
        odo_end = header.get("odometer_end")
        if odo_start or odo_end:
            lines.append(f"  Odometer:  {odo_start or '---'} → {odo_end or '---'}")
        if header.get("total_miles_today"):
            lines.append(f"  Miles:     {header['total_miles_today']}")
        if header.get("bol_numbers"):
            lines.append(f"  BOL:       {header['bol_numbers']}")
        lines.append(f"  Certified: {'YES' if header.get('certified') else 'NO'}")

    lines += ["", "  DUTY STATUS ENTRIES", "  " + "-" * 46]

    with db._connect() as conn:
        entries = conn.execute(
            """SELECT status, start_time, end_time, location, remarks
               FROM hos_entries WHERE log_date = ? ORDER BY start_time""",
            (today,),
        ).fetchall()

    if entries:
        for e in entries:
            label = e["status"].replace("_", " ").title()
            end = e["end_time"] or "ongoing"
            loc = f"  {e['location']}" if e["location"] else ""
            rem = f"  [{e['remarks']}]" if e["remarks"] else ""
            lines.append(f"  {e['start_time']} – {end:<8}  {label}{loc}{rem}")
    else:
        lines.append("  No entries recorded.")

    lines += ["", "  DAILY HOURS SUMMARY", "  " + "-" * 46]
    lines.append(f"  Driving:  {summary['driving_hours']:.1f} hrs  ({summary['driving_remaining']:.1f} remaining of 11)")
    lines.append(f"  On-Duty:  {summary['on_duty_hours']:.1f} hrs  ({summary['on_duty_remaining']:.1f} remaining of 14)")

    weekly = db.get_weekly_hours()
    lines.append(f"  Weekly:   {weekly['weekly_on_duty_hours']:.1f} hrs  ({weekly['hours_remaining']:.1f} remaining of 70)")

    return "\n".join(lines)


def build_alertness_report(limit: int = 10) -> str:
    records = db.get_alertness_history(limit)

    lines = [
        "",
        "=" * 52,
        "  ALERTNESS TEST LOG",
        "=" * 52,
    ]

    if not records:
        lines.append("  No alertness tests recorded.")
        return "\n".join(lines)

    for r in records:
        ts = r.get("timestamp", "")
        ts_display = ts[:16].replace("T", " ") if "T" in ts else ts[:16]
        level = r["level"].upper()
        score = f"{r['overall_score']:.0f}%"
        lines.append(f"\n  {ts_display}  |  {level}  |  Score: {score}")
        if r.get("memory_recalled") is not None:
            lines.append(f"    Memory recalled:  {r['memory_recalled']} words")
        if r.get("math_correct") is not None:
            avg = f"  ({r['math_avg_time']:.1f}s avg)" if r.get("math_avg_time") else ""
            lines.append(f"    Math correct:     {r['math_correct']}{avg}")
        if r.get("reaction_avg_time") is not None:
            lines.append(f"    Reaction time:    {r['reaction_avg_time']:.2f}s avg")

    return "\n".join(lines)


def build_full_report() -> str:
    driver_name = db.get_driver_name() or "Driver"
    now = datetime.now().strftime("%Y-%m-%d  %H:%M")

    header = "\n".join([
        "=" * 52,
        "  ELMEEDA DRIVER'S ASSISTANT — LOG REPORT",
        f"  Generated: {now}",
        f"  Driver:    {driver_name}",
        "=" * 52,
    ])

    return header + "\n" + build_hos_report() + "\n" + build_alertness_report() + "\n\n" + "=" * 52 + "\n  End of Report\n" + "=" * 52


def send_log_email(to_email: str) -> None:
    """Build and send the full log report to to_email via Mailgun. Raises on failure."""
    if not config.MAILGUN_API_KEY or not config.MAILGUN_DOMAIN:
        raise ValueError("Email not configured — set MAILGUN_API_KEY and MAILGUN_DOMAIN in .env")

    report = build_full_report()
    driver_name = db.get_driver_name() or "Driver"
    today = date.today().isoformat()

    resp = requests.post(
        f"https://api.mailgun.net/v3/{config.MAILGUN_DOMAIN}/messages",
        auth=("api", config.MAILGUN_API_KEY),
        data={
            "from": config.EMAIL_FROM or f"Elmeeda <logs@{config.MAILGUN_DOMAIN}>",
            "to": to_email,
            "subject": f"Driver Log — {driver_name} — {today}",
            "text": report,
        },
        timeout=15,
    )
    resp.raise_for_status()
