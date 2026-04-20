import anthropic
import config
import database as db

_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

_SYSTEM = f"""You are a trucking compliance expert and logbook assistant for a long-haul CDL driver.

You can record HOS duty status changes, check hours remaining, log fuel purchases for IFTA,
record state line crossings, and pull IFTA quarterly summaries — all by calling tools.

When a driver tells you what they're doing (e.g. "I just started driving", "fueled up in Texas,
80 gallons at Pilot"), extract the details and call the right tool immediately.
Always confirm back what was recorded in one sentence.

For general paperwork questions (BOL requirements, DVIR rules, permit info, CDL endorsements,
hazmat docs) answer from your knowledge — no tool needed.

FMCSA HOS limits:
- Driving: {config.DRIVE_LIMIT_HOURS} hrs/shift · On-duty: {config.ON_DUTY_LIMIT_HOURS} hrs/shift
- Break: {config.BREAK_DURATION_MINUTES} min after {config.BREAK_TRIGGER_HOURS} hrs driving
- Weekly: {config.WEEKLY_LIMIT_HOURS} hrs / {config.WEEKLY_PERIOD_DAYS}-day window
- Reset: {config.RESET_HOURS} consecutive off-duty hours

Be concise — responses are spoken aloud. Never use markdown, bullet points, asterisks, dashes, or lists. Plain spoken English only."""

# ── Tool definitions ──────────────────────────────────────────────────────────

_TOOLS = [
    {
        "name": "log_duty_status",
        "description": (
            "Record a HOS duty status change on today's 395.8 log. "
            "Use when the driver starts driving, goes off duty, gets in the sleeper, "
            "or begins on-duty non-driving work (loading, fueling, inspections)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["driving", "off_duty", "sleeper_berth", "on_duty_not_driving"],
                    "description": "New duty status",
                },
                "start_time": {
                    "type": "string",
                    "description": "Time the status began, 24-hour HH:MM (e.g. '08:30')",
                },
                "location": {
                    "type": "string",
                    "description": "Nearest city and state (e.g. 'Amarillo, TX')",
                },
                "remarks": {
                    "type": "string",
                    "description": "Optional: truck number, BOL, shipper name, etc.",
                },
            },
            "required": ["status", "start_time"],
        },
    },
    {
        "name": "get_hos_summary",
        "description": (
            "Get today's driving hours, on-duty hours, and remaining time "
            "under the 11-hour drive limit and 14-hour on-duty window."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "log_date": {
                    "type": "string",
                    "description": "Date to check in YYYY-MM-DD format. Omit for today.",
                }
            },
        },
    },
    {
        "name": "get_weekly_hours",
        "description": "Get total on-duty hours used in the current 8-day rolling window and hours remaining before the 70-hour limit.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "log_fuel_purchase",
        "description": (
            "Record a diesel fuel purchase for IFTA reporting. "
            "Use whenever the driver fuels up. Jurisdiction is the state where fuel was purchased."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "jurisdiction": {
                    "type": "string",
                    "description": "2-letter state/province where fuel was purchased (e.g. 'TX')",
                },
                "gallons": {
                    "type": "number",
                    "description": "Number of gallons purchased",
                },
                "price_per_gallon": {
                    "type": "number",
                    "description": "Price per gallon in dollars",
                },
                "vendor": {
                    "type": "string",
                    "description": "Truck stop name (e.g. 'Pilot', \"Love's\", 'TA')",
                },
                "vendor_city": {
                    "type": "string",
                    "description": "City where fuel was purchased",
                },
                "receipt_number": {
                    "type": "string",
                    "description": "Receipt or transaction number if available",
                },
                "odometer": {
                    "type": "integer",
                    "description": "Odometer reading at time of purchase",
                },
            },
            "required": ["jurisdiction", "gallons"],
        },
    },
    {
        "name": "log_state_crossing",
        "description": (
            "Record entering a new state or province for IFTA mileage tracking. "
            "Call this every time the driver crosses a state line."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "jurisdiction": {
                    "type": "string",
                    "description": "2-letter state/province code being entered (e.g. 'OK')",
                },
                "odometer": {
                    "type": "integer",
                    "description": "Odometer reading at the state line",
                },
                "crossing_time": {
                    "type": "string",
                    "description": "Time of crossing in HH:MM format. Omit to use current time.",
                },
            },
            "required": ["jurisdiction", "odometer"],
        },
    },
    {
        "name": "get_ifta_summary",
        "description": "Get the IFTA quarterly summary: total miles and fuel by state, fleet MPG, and net taxable gallons per jurisdiction.",
        "input_schema": {
            "type": "object",
            "properties": {
                "quarter": {
                    "type": "integer",
                    "description": "Quarter number (1, 2, 3, or 4)",
                },
                "year": {
                    "type": "integer",
                    "description": "4-digit year (e.g. 2025)",
                },
            },
            "required": ["quarter", "year"],
        },
    },
]


# ── Tool dispatch ─────────────────────────────────────────────────────────────

def _dispatch(name: str, inputs: dict) -> str:
    try:
        if name == "log_duty_status":
            result = db.log_duty_status(
                status=inputs["status"],
                start_time=inputs["start_time"],
                location=inputs.get("location", ""),
                remarks=inputs.get("remarks", ""),
            )
            return (
                f"Logged {inputs['status'].replace('_', ' ')} at {inputs['start_time']}. "
                f"Today: {result['driving_hours']}h driving "
                f"({result['driving_remaining']}h remaining), "
                f"{result['on_duty_hours']}h on-duty "
                f"({result['on_duty_remaining']}h remaining)."
            )

        if name == "get_hos_summary":
            r = db.get_hos_summary(inputs.get("log_date"))
            return (
                f"HOS for {r['date']}: "
                f"{r['driving_hours']}h driving ({r['driving_remaining']}h left), "
                f"{r['on_duty_hours']}h on-duty ({r['on_duty_remaining']}h left). "
                f"{r['entries']} status entries logged."
            )

        if name == "get_weekly_hours":
            r = db.get_weekly_hours()
            return (
                f"8-day window: {r['weekly_on_duty_hours']}h used of 70h limit. "
                f"{r['hours_remaining']}h remaining."
            )

        if name == "log_fuel_purchase":
            r = db.log_fuel_purchase(
                jurisdiction=inputs["jurisdiction"],
                gallons=inputs["gallons"],
                price_per_gallon=inputs.get("price_per_gallon"),
                vendor=inputs.get("vendor", ""),
                vendor_city=inputs.get("vendor_city", ""),
                receipt_number=inputs.get("receipt_number", ""),
                odometer=inputs.get("odometer"),
            )
            cost = f"${r['total_cost']:.2f}" if r["total_cost"] else "cost not recorded"
            return (
                f"IFTA: logged {inputs['gallons']} gallons in {r['jurisdiction']} "
                f"at {r['vendor']} ({cost})."
            )

        if name == "log_state_crossing":
            r = db.log_state_crossing(
                jurisdiction=inputs["jurisdiction"],
                odometer=inputs["odometer"],
                crossing_time=inputs.get("crossing_time"),
            )
            return f"Logged entry into {r['jurisdiction']} at odometer {r['odometer']}."

        if name == "get_ifta_summary":
            r = db.get_ifta_summary(inputs["quarter"], inputs["year"])
            top_states = sorted(
                r["miles_by_jurisdiction"].items(), key=lambda x: x[1], reverse=True
            )[:5]
            state_summary = ", ".join(f"{s}: {m}mi" for s, m in top_states)
            return (
                f"IFTA {r['quarter']}: {r['total_miles']} total miles, "
                f"{r['total_gallons']} gallons, {r['fleet_mpg']} MPG. "
                f"Top states — {state_summary}."
            )

        return "Unknown tool."
    except Exception as e:
        return f"Error: {e}"


# ── Agent ─────────────────────────────────────────────────────────────────────

class PaperworkAgent:
    def __init__(self):
        self._history: list[dict] = []

    def respond(self, user_msg: str) -> str:
        self._history.append({"role": "user", "content": user_msg})
        messages = list(self._history)

        while True:
            response = _client.messages.create(
                model=config.MODEL,
                max_tokens=400,
                system=[{"type": "text", "text": _SYSTEM, "cache_control": {"type": "ephemeral"}}],
                tools=_TOOLS,
                messages=messages,
            )
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason != "tool_use":
                break

            results = [
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": _dispatch(block.name, block.input),
                }
                for block in response.content
                if block.type == "tool_use"
            ]
            messages.append({"role": "user", "content": results})

        text = next(
            (b.text for b in response.content if b.type == "text"),
            "I couldn't process that request.",
        )
        self._history.append({"role": "assistant", "content": text})
        return text

    def reset(self):
        self._history.clear()
