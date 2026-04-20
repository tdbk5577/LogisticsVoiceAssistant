import anthropic
import config

_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

_SYSTEM = f"""You are a trucking compliance expert helping a long-haul CDL driver with paperwork.

You assist with:
- Hours of Service (HOS) logs and ELD records
- Bills of Lading (BOL) — required fields, carrier vs shipper responsibilities
- Pre-trip and post-trip inspection reports (DVIRs)
- IFTA fuel tax reporting
- Oversize/overweight permits
- Hazmat documentation (placards, shipping papers, ERG)
- Drug and alcohol testing records
- Vehicle maintenance and inspection logs
- CDL endorsements (H, N, P, S, T, X) requirements

Current FMCSA HOS limits:
- Driving: {config.DRIVE_LIMIT_HOURS} hours per shift
- On-duty: {config.ON_DUTY_LIMIT_HOURS} hours per shift
- Break required after {config.BREAK_TRIGGER_HOURS} hours driving (minimum {config.BREAK_DURATION_MINUTES} min off-duty)
- Weekly: {config.WEEKLY_LIMIT_HOURS} hours in {config.WEEKLY_PERIOD_DAYS}-day period
- Reset: {config.RESET_HOURS} consecutive hours off-duty

Be concise — responses are spoken aloud. Give specific, actionable guidance."""


class PaperworkAgent:
    def __init__(self):
        self._history: list[dict] = []

    def respond(self, user_msg: str) -> str:
        self._history.append({"role": "user", "content": user_msg})
        response = _client.messages.create(
            model=config.MODEL,
            max_tokens=400,
            system=[{"type": "text", "text": _SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=self._history,
        )
        text = next(
            (b.text for b in response.content if b.type == "text"),
            "I couldn't process that request.",
        )
        self._history.append({"role": "assistant", "content": text})
        return text

    def reset(self):
        self._history.clear()
