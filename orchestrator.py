import anthropic
import config
from voice_engine import VoiceEngine
from agents.logistics_agent import LogisticsAgent
from agents.paperwork_agent import PaperworkAgent
from agents.drowsy_test import DrowsyTest
from daily_log import DailyLogChecker

_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

_WAKE_WORDS = ["hey truck", "truck ai", "hey driver", "hey assistant"]

_LOGISTICS_KW = {
    "weather", "wind", "rain", "snow", "storm", "fog",
    "route", "road", "highway", "interstate", "exit",
    "fuel", "gas", "diesel", "station", "miles", "distance",
    "traffic", "construction", "detour", "navigate", "directions",
    "weigh", "scale", "rest", "area", "eta", "arrive",
}
_PAPERWORK_KW = {
    "log", "hos", "eld", "hours", "driving", "duty",
    "bol", "bill", "lading", "manifest", "paperwork", "document",
    "form", "report", "inspection", "dvir", "permit",
    "ifta", "fuel", "tax", "cdl", "license", "endorsement",
    "hazmat", "placard", "drug", "alcohol", "test",
}
_DROWSY_KW = {
    "tired", "sleepy", "drowsy", "fatigue", "fatigued",
    "alertness", "alert", "awake", "wake", "check",
    "test", "safe", "focus",
}

_CLASSIFY_PROMPT = """A long-haul truck driver said: "{text}"

Classify into exactly one category:
- logistics  (weather, routes, fuel, navigation, road conditions, ETA)
- paperwork  (HOS logs, BOL, inspection forms, permits, regulations, hours of service)
- drowsy_test (alertness check, tiredness, fatigue test)
- unknown

Reply with one word only."""


class Orchestrator:
    def __init__(self):
        self._voice = VoiceEngine()
        self._logistics = LogisticsAgent()
        self._paperwork = PaperworkAgent()
        self._drowsy = DrowsyTest(self._voice)
        self._active: str | None = None  # "logistics" | "paperwork" | None

    # ── Routing ──────────────────────────────────────────────────────────────

    def _classify(self, text: str) -> str:
        words = set(text.lower().split())

        # Drowsy check takes priority — safety first
        if words & _DROWSY_KW:
            return "drowsy_test"
        if words & _LOGISTICS_KW:
            return "logistics"
        if words & _PAPERWORK_KW:
            return "paperwork"

        # Ambiguous — ask Claude
        try:
            response = _client.messages.create(
                model=config.MODEL,
                max_tokens=10,
                messages=[{"role": "user", "content": _CLASSIFY_PROMPT.format(text=text)}],
            )
            category = next(
                (b.text.strip().lower() for b in response.content if b.type == "text"),
                "unknown",
            )
            return category if category in ("logistics", "paperwork", "drowsy_test") else "unknown"
        except Exception:
            return self._active or "unknown"

    def handle(self, text: str) -> str:
        """Route text to the correct agent and return the spoken response."""
        category = self._classify(text)

        if category == "drowsy_test":
            self._active = None
            self._drowsy.run()
            return ""  # DrowsyTest speaks its own output

        if category == "logistics":
            self._active = "logistics"
            return self._logistics.respond(text)

        if category == "paperwork":
            self._active = "paperwork"
            return self._paperwork.respond(text)

        # Unknown — continue with whichever agent is active, or ask for clarification
        if self._active == "logistics":
            return self._logistics.respond(text)
        if self._active == "paperwork":
            return self._paperwork.respond(text)

        return (
            "I can help with routes and weather, trucking paperwork, "
            "or run an alertness test. What do you need?"
        )

    # ── Main loop ────────────────────────────────────────────────────────────

    def _is_ifta_review(self, text: str) -> bool:
        t = text.lower()
        return "ifta" in t and any(w in t for w in ("check", "review", "log", "record"))

    def run(self):
        checker = DailyLogChecker(self._voice)
        checker.run()

        self._voice.speak("Elmeeda Drivers Assistant ready.")
        print("[ELMEEDA] Listening for wake word... (Ctrl+C to quit)")

        try:
            while True:
                if not self._voice.listen_for_wake_word(_WAKE_WORDS):
                    continue

                self._voice.speak("Yes?")
                command = self._voice.listen(timeout=8, phrase_limit=20)

                if not command:
                    self._voice.speak("Didn't catch that.")
                    continue

                if self._is_ifta_review(command):
                    checker.review_ifta()
                    continue

                response = self.handle(command)
                if response:
                    self._voice.speak(response)

        except KeyboardInterrupt:
            self._voice.speak("Elmeeda signing off. Drive safe.")
            print("\n[ELMEEDA] Shutting down.")
