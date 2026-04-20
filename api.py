"""
FastAPI backend for Truck AI.

The mobile app handles STT, TTS, wake word, and Bluetooth.
This API handles agent logic, routing, and all database operations.
"""

from datetime import date

import anthropic
import requests as http
from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import os

import config
import database as db
from agents.logistics_agent import LogisticsAgent
from agents.paperwork_agent import PaperworkAgent

app = FastAPI(title="Truck AI API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_claude = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

# ── Agent sessions ────────────────────────────────────────────────────────────
# Keyed by session_id so each device/driver gets independent history.

_sessions: dict[str, dict] = {}


def _get_session(session_id: str) -> dict:
    if session_id not in _sessions:
        _sessions[session_id] = {
            "paperwork": PaperworkAgent(),
            "logistics": LogisticsAgent(),
            "active": None,
        }
    return _sessions[session_id]


# ── Routing (mirrors orchestrator logic) ─────────────────────────────────────

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
    "ifta", "tax", "cdl", "license", "endorsement",
    "hazmat", "placard", "drug", "alcohol", "test",
}
_CLASSIFY_PROMPT = """A long-haul truck driver said: "{text}"

Classify into exactly one category:
- logistics  (weather, routes, fuel, navigation, road conditions, ETA)
- paperwork  (HOS logs, BOL, inspection forms, permits, regulations, hours of service)
- unknown

Reply with one word only."""


def _classify(text: str, active: str | None) -> str:
    words = set(text.lower().split())
    if words & _LOGISTICS_KW:
        return "logistics"
    if words & _PAPERWORK_KW:
        return "paperwork"
    try:
        resp = _claude.messages.create(
            model=config.MODEL,
            max_tokens=10,
            messages=[{"role": "user", "content": _CLASSIFY_PROMPT.format(text=text)}],
        )
        category = next(
            (b.text.strip().lower() for b in resp.content if b.type == "text"), "unknown"
        )
        return category if category in ("logistics", "paperwork") else "unknown"
    except Exception:
        return active or "unknown"


# ── Debug ─────────────────────────────────────────────────────────────────────

@app.get("/debug/env")
def debug_env():
    return {
        "ANTHROPIC_API_KEY_set": bool(os.getenv("ANTHROPIC_API_KEY")),
        "config_key_set": bool(config.ANTHROPIC_API_KEY),
    }


# ── Models ────────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    session_id: str
    text: str

class ChatResponse(BaseModel):
    text: str
    agent: str

class ProfileUpdate(BaseModel):
    driver_name: str | None = None
    carrier_address: str | None = None
    home_terminal: str | None = None

class AlertnessResult(BaseModel):
    level: str
    overall_score: float
    memory_recalled: int
    math_correct: int
    math_avg_time: float
    reaction_avg_time: float

class TTSRequest(BaseModel):
    text: str


# ── Chat ──────────────────────────────────────────────────────────────────────

@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    session = _get_session(req.session_id)
    category = _classify(req.text, session["active"])

    if category == "logistics":
        session["active"] = "logistics"
        return ChatResponse(text=session["logistics"].respond(req.text), agent="logistics")

    if category == "paperwork":
        session["active"] = "paperwork"
        return ChatResponse(text=session["paperwork"].respond(req.text), agent="paperwork")

    if session["active"] == "logistics":
        return ChatResponse(text=session["logistics"].respond(req.text), agent="logistics")
    if session["active"] == "paperwork":
        return ChatResponse(text=session["paperwork"].respond(req.text), agent="paperwork")

    return ChatResponse(
        text="I can help with routes and weather, trucking paperwork, or run an alertness check. What do you need?",
        agent="unknown",
    )


@app.delete("/chat/{session_id}")
def reset_session(session_id: str):
    _sessions.pop(session_id, None)
    return {"ok": True}


# ── Driver profile ────────────────────────────────────────────────────────────

@app.get("/profile")
def get_profile():
    return db.get_driver_profile()


@app.put("/profile")
def update_profile(body: ProfileUpdate):
    db.set_driver_profile(
        driver_name=body.driver_name,
        carrier_address=body.carrier_address,
        home_terminal=body.home_terminal,
    )
    return db.get_driver_profile()


# ── HOS ───────────────────────────────────────────────────────────────────────

@app.get("/hos/summary")
def hos_summary(log_date: str | None = None):
    return db.get_hos_summary(log_date)


@app.get("/hos/weekly")
def weekly_hours():
    return db.get_weekly_hours()


@app.post("/hos/certify/{log_date}")
def certify_log(log_date: str):
    db.certify_log(log_date)
    return {"ok": True, "log_date": log_date}


# ── IFTA ─────────────────────────────────────────────────────────────────────

@app.get("/ifta/summary")
def ifta_summary(quarter: int, year: int):
    return db.get_ifta_summary(quarter, year)


# ── Alertness ─────────────────────────────────────────────────────────────────

@app.post("/alertness")
def save_alertness(result: AlertnessResult):
    from datetime import datetime
    db.save_alertness_log(
        timestamp=datetime.now().isoformat(),
        level=result.level,
        overall_score=result.overall_score,
        memory_recalled=result.memory_recalled,
        math_correct=result.math_correct,
        math_avg_time=result.math_avg_time,
        reaction_avg_time=result.reaction_avg_time,
    )
    return {"ok": True}


@app.get("/alertness/history")
def alertness_history(limit: int = 20):
    return db.get_alertness_history(limit)


# ── TTS ───────────────────────────────────────────────────────────────────────

@app.post("/tts")
def text_to_speech(req: TTSRequest):
    if not config.ELEVENLABS_API_KEY:
        raise HTTPException(status_code=503, detail="ELEVENLABS_API_KEY not set")
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{config.ELEVENLABS_VOICE_ID}"
    resp = http.post(
        url,
        headers={"xi-api-key": config.ELEVENLABS_API_KEY, "Content-Type": "application/json"},
        json={
            "text": req.text,
            "model_id": "eleven_turbo_v2",
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
        },
        timeout=15,
    )
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"ElevenLabs {resp.status_code}: {resp.text[:300]}")
    return Response(content=resp.content, media_type="audio/mpeg")


# ── Frontend (must be last) ───────────────────────────────────────────────────

if os.path.isdir("frontend"):
    app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
