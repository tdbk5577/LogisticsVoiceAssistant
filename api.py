"""
FastAPI backend for Truck AI.

The mobile app handles STT, TTS, wake word, and Bluetooth.
This API handles agent logic, routing, and all database operations.
"""

from datetime import date  # used for date-typed parameters in HOS endpoints

import anthropic  # Anthropic SDK — used for fallback intent classification
import requests as http  # HTTP client aliased as 'http' to avoid shadowing the stdlib
from fastapi import FastAPI, HTTPException, Response  # core FastAPI primitives
from fastapi.middleware.cors import CORSMiddleware  # allows cross-origin requests from the mobile app
from fastapi.staticfiles import StaticFiles  # serves the frontend build as static files
from pydantic import BaseModel  # base class for all request/response schemas

import os  # used to check whether the frontend directory exists at startup

import config  # project-wide constants and API keys loaded from environment variables
import database as db  # database access layer — all reads/writes go through here
from agents.logistics_agent import LogisticsAgent  # handles weather, routes, fuel, navigation
from agents.paperwork_agent import PaperworkAgent  # handles HOS logs, BOL, permits, regulations

app = FastAPI(title="Truck AI API")  # create the FastAPI application instance

app.add_middleware(  # register CORS middleware so the mobile app can call this API from any origin
    CORSMiddleware,
    allow_origins=["*"],  # accept requests from any origin
    allow_methods=["*"],  # accept all HTTP methods (GET, POST, PUT, DELETE, etc.)
    allow_headers=["*"],  # accept all request headers
)

_claude = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)  # shared Anthropic client used only for intent classification fallback

# ── Agent sessions ────────────────────────────────────────────────────────────
# Keyed by session_id so each device/driver gets independent history.

_sessions: dict[str, dict] = {}  # in-memory store mapping session_id to its agent instances and active-agent state


def _get_session(session_id: str) -> dict:  # returns the session for this driver, creating it if it doesn't exist yet
    if session_id not in _sessions:  # first request from this device — initialize a fresh session
        _sessions[session_id] = {  # build the session dict with one agent instance per domain
            "paperwork": PaperworkAgent(),  # agent that handles HOS, BOL, and regulatory paperwork
            "logistics": LogisticsAgent(),  # agent that handles routes, weather, fuel, and navigation
            "active": None,  # tracks which agent spoke last so follow-up messages stay in context
        }
    return _sessions[session_id]  # return the existing or newly created session


# ── Routing (mirrors orchestrator logic) ─────────────────────────────────────

_LOGISTICS_KW = {  # words that strongly signal a logistics question
    "weather", "wind", "rain", "snow", "storm", "fog",  # weather conditions
    "route", "road", "highway", "interstate", "exit",  # navigation terms
    "fuel", "gas", "diesel", "station", "miles", "distance",  # fuel and distance
    "traffic", "construction", "detour", "navigate", "directions",  # road conditions
    "weigh", "scale", "rest", "area", "eta", "arrive",  # weigh stations, rest areas, arrival
}
_PAPERWORK_KW = {  # words that strongly signal a paperwork question
    "log", "hos", "eld", "hours", "driving", "duty",  # hours-of-service terms
    "bol", "bill", "lading", "manifest", "paperwork", "document",  # shipping documents
    "form", "report", "inspection", "dvir", "permit",  # inspection and permit forms
    "ifta", "tax", "cdl", "license", "endorsement",  # licensing and tax filings
    "hazmat", "placard", "drug", "alcohol",  # compliance and safety
    "started", "starting", "stopped", "stopping", "clocked",  # duty-status change phrases
    "on-duty", "offduty", "sleeper", "berth", "logbook",  # HOS status labels
    "record", "recording", "update", "certify", "certifying",  # log actions
}
_CLASSIFY_PROMPT = """A long-haul truck driver said: "{text}"

Classify into exactly one category:
- logistics  (weather, routes, fuel, navigation, road conditions, ETA)
- paperwork  (HOS logs, BOL, inspection forms, permits, regulations, hours of service)
- unknown

Reply with one word only."""  # prompt sent to Claude when keyword matching fails to determine intent


def _classify(text: str, active: str | None) -> str:  # returns "logistics", "paperwork", or "unknown"
    words = set(text.lower().split())  # tokenize the input into a set for fast keyword intersection
    if words & _LOGISTICS_KW:  # any overlap with logistics keywords → route to logistics agent
        return "logistics"
    if words & _PAPERWORK_KW:  # any overlap with paperwork keywords → route to paperwork agent
        return "paperwork"
    # No strong keywords — if mid-conversation, stay with the active agent
    if active:  # continuing an existing conversation — don't switch agents on ambiguous input
        return active
    try:
        resp = _claude.messages.create(  # fall back to Claude for classification when keywords are inconclusive
            model=config.MODEL,  # model specified in config (e.g. claude-haiku for low latency)
            max_tokens=10,  # only need a single word back — cap tokens to reduce cost and latency
            messages=[{"role": "user", "content": _CLASSIFY_PROMPT.format(text=text)}],  # inject the driver's text into the classification prompt
        )
        category = next(  # extract the first text block from Claude's response
            (b.text.strip().lower() for b in resp.content if b.type == "text"), "unknown"  # default to "unknown" if no text block is present
        )
        return category if category in ("logistics", "paperwork") else "unknown"  # reject any response that isn't one of the two valid categories
    except Exception:  # if the Claude call fails for any reason, degrade gracefully
        return "unknown"  # unknown routes to the fallback reply in /chat


# ── Debug ─────────────────────────────────────────────────────────────────────

@app.get("/debug/weather")  # dev endpoint to test weather fetching without going through the full chat flow
def debug_weather(location: str = "Charlotte, NC"):  # defaults to Charlotte so it works without any query param
    from agents.logistics_agent import _fetch_weather  # imported here to avoid a circular import at module level
    return {"result": _fetch_weather(location)}  # returns raw weather data for the given location


@app.get("/debug/env")  # dev endpoint to verify that all required API keys are present in the environment
def debug_env():
    return {  # returns True/False for each key — never exposes the actual key values
        "ANTHROPIC_API_KEY": bool(config.ANTHROPIC_API_KEY),  # needed for agent responses and classification
        "ELEVENLABS_API_KEY": bool(config.ELEVENLABS_API_KEY),  # needed for TTS audio generation
        "OPENWEATHER_API_KEY": bool(config.OPENWEATHER_API_KEY),  # needed for weather data in logistics agent
        "GOOGLE_PLACES_API_KEY": bool(config.GOOGLE_PLACES_API_KEY),  # needed for fuel station lookups
    }


# ── Models ────────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):  # request body for POST /chat
    session_id: str  # unique identifier for this driver's session — typically the device ID
    text: str  # the driver's spoken message after STT conversion on the mobile app

class ChatResponse(BaseModel):  # response body for POST /chat
    text: str  # the agent's reply text — will be spoken aloud by the mobile app's TTS
    agent: str  # which agent produced the reply: "logistics", "paperwork", or "unknown"

class ProfileUpdate(BaseModel):  # request body for PUT /profile — all fields optional for partial updates
    driver_name: str | None = None  # driver's full name as it appears on their CDL
    carrier_address: str | None = None  # carrier's physical address for BOL and IFTA forms
    home_terminal: str | None = None  # the terminal the driver is based out of

class AlertnessResult(BaseModel):  # request body for POST /alertness — sent by the mobile app after a test
    level: str  # categorical result: "alert", "fatigued", or "impaired"
    overall_score: float  # composite score from 0.0 to 1.0
    memory_recalled: int  # number of items correctly recalled in the memory portion of the test
    math_correct: int  # number of arithmetic problems answered correctly
    math_avg_time: float  # average response time in seconds for math questions
    reaction_avg_time: float  # average reaction time in seconds for the reaction portion

class TTSRequest(BaseModel):  # request body for POST /tts
    text: str  # the text to synthesize into speech


# ── Chat ──────────────────────────────────────────────────────────────────────

@app.post("/chat", response_model=ChatResponse)  # main entry point — receives the driver's transcribed speech
def chat(req: ChatRequest):
    session = _get_session(req.session_id)  # fetch or create the session for this driver
    category = _classify(req.text, session["active"])  # determine which agent should handle this message

    if category == "logistics":  # message is about routes, weather, fuel, or navigation
        session["active"] = "logistics"  # mark logistics as the active agent for follow-up continuity
        return ChatResponse(text=session["logistics"].respond(req.text), agent="logistics")  # delegate to logistics agent and return its reply

    if category == "paperwork":  # message is about HOS, BOL, permits, or regulations
        session["active"] = "paperwork"  # mark paperwork as the active agent for follow-up continuity
        return ChatResponse(text=session["paperwork"].respond(req.text), agent="paperwork")  # delegate to paperwork agent and return its reply

    if session["active"] == "logistics":  # no clear category but driver was mid-conversation with logistics
        return ChatResponse(text=session["logistics"].respond(req.text), agent="logistics")  # keep routing to logistics to preserve context
    if session["active"] == "paperwork":  # no clear category but driver was mid-conversation with paperwork
        return ChatResponse(text=session["paperwork"].respond(req.text), agent="paperwork")  # keep routing to paperwork to preserve context

    return ChatResponse(  # truly ambiguous — no keywords, no active agent, classification returned "unknown"
        text="I can help with routes and weather, trucking paperwork, or run an alertness check. What do you need?",  # prompt the driver to clarify which domain they need help with
        agent="unknown",  # signal to the mobile app that no agent handled this turn
    )


@app.delete("/chat/{session_id}")  # clears a session — call this when the driver logs out or starts a new shift
def reset_session(session_id: str):
    _sessions.pop(session_id, None)  # remove the session if it exists; no-op if it doesn't
    return {"ok": True}  # always succeeds — idempotent


# ── Driver profile ────────────────────────────────────────────────────────────

@app.get("/profile")  # retrieves the driver's saved profile from the database
def get_profile():
    return db.get_driver_profile()  # returns the profile dict (name, carrier address, home terminal)


@app.put("/profile")  # updates one or more profile fields — omitted fields are left unchanged
def update_profile(body: ProfileUpdate):
    db.set_driver_profile(  # write the provided fields to the database
        driver_name=body.driver_name,  # None means "don't change this field"
        carrier_address=body.carrier_address,  # None means "don't change this field"
        home_terminal=body.home_terminal,  # None means "don't change this field"
    )
    return db.get_driver_profile()  # return the updated profile so the client can confirm the changes


# ── HOS ───────────────────────────────────────────────────────────────────────

@app.get("/hos/summary")  # returns a summary of hours-of-service usage for the given date (defaults to today)
def hos_summary(log_date: str | None = None):
    return db.get_hos_summary(log_date)  # returns drive time, on-duty time, remaining hours, etc.


@app.get("/hos/weekly")  # returns hours driven for each of the last 7 days — used for the 70-hour/8-day rule
def weekly_hours():
    return db.get_weekly_hours()  # returns a list of daily totals covering the rolling 8-day window


@app.post("/hos/certify/{log_date}")  # marks a specific day's log as certified by the driver
def certify_log(log_date: str):
    db.certify_log(log_date)  # sets the certified flag on the log entry for this date
    return {"ok": True, "log_date": log_date}  # echo the date back so the client can confirm which log was certified


# ── IFTA ─────────────────────────────────────────────────────────────────────

@app.get("/ifta/summary")  # returns IFTA fuel tax data for a given quarter and year
def ifta_summary(quarter: int, year: int):
    return db.get_ifta_summary(quarter, year)  # returns miles and fuel by jurisdiction for the requested period


# ── Alertness ─────────────────────────────────────────────────────────────────

@app.post("/alertness")  # saves the result of an alertness test completed on the mobile app
def save_alertness(result: AlertnessResult):
    from datetime import datetime  # imported here to keep it close to its only use
    db.save_alertness_log(  # persist all test metrics to the database
        timestamp=datetime.now().isoformat(),  # record when the test was taken
        level=result.level,  # categorical fatigue level from the test
        overall_score=result.overall_score,  # composite score
        memory_recalled=result.memory_recalled,  # memory sub-score
        math_correct=result.math_correct,  # math sub-score
        math_avg_time=result.math_avg_time,  # math response time
        reaction_avg_time=result.reaction_avg_time,  # reaction time
    )
    return {"ok": True}  # acknowledge the save


@app.get("/alertness/history")  # retrieves past alertness test results for trend display in the app
def alertness_history(limit: int = 20):
    return db.get_alertness_history(limit)  # returns the most recent `limit` results, newest first


# ── TTS ───────────────────────────────────────────────────────────────────────

@app.post("/tts")  # proxies a TTS request to ElevenLabs and streams the MP3 audio back to the client
def text_to_speech(req: TTSRequest):
    if not config.ELEVENLABS_API_KEY:  # refuse early if the key isn't configured rather than failing with a cryptic error
        raise HTTPException(status_code=503, detail="ELEVENLABS_API_KEY not set")
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{config.ELEVENLABS_VOICE_ID}"  # ElevenLabs endpoint for the configured voice
    resp = http.post(  # send the synthesis request to ElevenLabs
        url,
        headers={"xi-api-key": config.ELEVENLABS_API_KEY, "Content-Type": "application/json"},  # authenticate and declare JSON body
        json={
            "text": req.text,  # the text to synthesize
            "model_id": "eleven_turbo_v2",  # turbo model for lower latency — important in a voice assistant context
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},  # balanced voice settings for clear, consistent speech
        },
        timeout=15,  # fail fast if ElevenLabs doesn't respond — don't block the driver
    )
    if resp.status_code != 200:  # ElevenLabs returned an error
        raise HTTPException(status_code=502, detail=f"ElevenLabs {resp.status_code}: {resp.text[:300]}")  # surface the upstream error, truncated to avoid huge payloads
    return Response(content=resp.content, media_type="audio/mpeg")  # return the raw MP3 bytes so the mobile app can play them directly


# ── Frontend (must be last) ───────────────────────────────────────────────────

if os.path.isdir("frontend"):  # only mount if the frontend build exists — skipped in API-only deployments
    app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")  # serve the React/HTML build at the root path; must be registered last so it doesn't shadow API routes
