# Truck AI — Voice Assistant for Long-Haul Drivers

A voice-first AI co-pilot for commercial truck drivers. Runs on macOS or as a hosted API backend.


---

## Architecture

```
main.py  (local)                          api.py  (hosted)
└── Orchestrator (orchestrator.py)        └── FastAPI — REST API for mobile app
    ├── VoiceEngine      — STT (Google) + TTS (ElevenLabs, falls back to macOS say)
    ├── LogisticsAgent   — routes, weather, fuel stops, weigh stations
    ├── PaperworkAgent   — HOS logs, BOL, permits, FMCSA regs, IFTA
    └── DrowsyTest       — 3-part alertness test with scored assessment
```

The orchestrator listens for the wake word, classifies the driver's command via keyword matching (with Claude as fallback), and routes to the appropriate agent. It tracks the active agent so follow-up questions stay in context.

---

## Deployment (Mobile Prototype)

The backend is designed to be hosted on **Railway** with the mobile app built in **React Native + Expo**.

| Layer | Tool |
|---|---|
| Backend API | FastAPI (`api.py`) on Railway |
| Database | SQLite (`data/truck_ai.db`) — migrate to PostgreSQL to scale |
| Mobile app | React Native + Expo (share via QR code, no App Store needed) |
| TTS | ElevenLabs streaming (falls back to macOS `say` locally) |
| STT + wake word | Handled on-device by the mobile app |

**To deploy on Railway:** push to GitHub → New project → Deploy from repo → set env vars.

The mobile app handles: STT, wake word detection, ElevenLabs TTS playback, Bluetooth audio routing, and the drowsy test timing. The backend handles: agent logic, intent routing, and all database reads/writes.

### API Endpoints

| Endpoint | Purpose |
|---|---|
| `POST /chat` | Send driver speech text → get agent response |
| `DELETE /chat/{session_id}` | Reset conversation history |
| `GET /profile` | Get driver profile |
| `PUT /profile` | Update driver name, carrier address, home terminal |
| `GET /hos/summary` | Today's driving + on-duty hours and remaining time |
| `GET /hos/weekly` | 8-day rolling hours used / remaining |
| `POST /hos/certify/{date}` | Certify a day's log with timestamp |
| `GET /ifta/summary?quarter=&year=` | Quarterly IFTA report |
| `POST /alertness` | Save drowsy test result |

---

## Agent 1 — Logistics (`agents/logistics_agent.py`)

Handles: routing, weather, fuel stops, weigh stations, rest areas, road hazards, ETAs.

| Tool | Source | Cost | Used for |
|---|---|---|---|
| `get_weather` | OpenWeather API | Free tier | Current conditions, wind, temp |
| `find_truck_stops_or_weigh_stations` | OpenStreetMap Overpass | Free, no key | Pilot, Love's, TA, Flying J, weigh stations |
| `search_places` | Google Places Text Search | Free tier | Rest areas, repair shops, motels, CAT scales, anything else |

OpenStreetMap geocodes locations via Nominatim then queries Overpass within a configurable radius (default 25 miles). Google Places requires `GOOGLE_PLACES_API_KEY` in `.env`; degrades gracefully if absent.

---

## Agent 2 — Paperwork (`agents/paperwork_agent.py`)

Handles: HOS/ELD logs, Bills of Lading, DVIRs, IFTA fuel tax, oversize/overweight permits, hazmat documentation, CDL endorsements, drug/alcohol testing records.

FMCSA HOS limits injected into the system prompt:
- Driving: 11 hrs/shift · On-duty: 14 hrs/shift · Break: 30 min after 8 hrs driving
- Weekly: 70 hrs / 8-day period · Reset: 10 consecutive hours off-duty

### Voice Tools

| What the driver says | Tool | Action |
|---|---|---|
| *"I started driving at 7:30 in Amarillo"* | `log_duty_status` | Records status change on today's 395.8 log |
| *"How many hours do I have left today?"* | `get_hos_summary` | Returns driving + on-duty hours and remaining time |
| *"How many hours left this week?"* | `get_weekly_hours` | Returns 70-hr window usage and hours remaining |
| *"Fueled up in Texas, 95 gallons at Love's, $3.84"* | `log_fuel_purchase` | Logs IFTA fuel receipt |
| *"Just crossed into Oklahoma at mile 184,000"* | `log_state_crossing` | Records state line crossing for mileage tracking |
| *"Give me my IFTA report for Q1"* | `get_ifta_summary` | Compiles miles + fuel by jurisdiction, fleet MPG |

### Daily Log Flow (`daily_log.py`)

Runs at startup via `DailyLogChecker`. Sequences:

1. **One-time profile setup** — driver name, carrier address, home terminal (never asked again)
2. **Prior day completion** — closes any open HOS entries, fills missing odometer end
3. **IFTA review** — triggered separately mid-day via `checker.review_ifta()` ("Hey Truck, IFTA check"); prompts for any missed fuel stops and state crossings
4. **Log certification** — "Do you certify yesterday's log is true and correct?" Records a timestamp
5. **Today's startup** — carrier, truck/trailer numbers, from/to locations, co-driver, BOL numbers, odometer start, first duty status

### Database (`database.py` → `data/truck_ai.db`)

5 SQLite tables matching the physical form layouts:

| Table | Mirrors | Key columns |
|---|---|---|
| `driver_profile` | One-time driver setup | name, carrier address, home terminal |
| `hos_logs` | FMCSA 395.8 header | date, truck #, trailer #, BOL, carrier, co-driver, from/to, certified_at |
| `hos_entries` | 395.8 duty status grid | status, start_time, end_time, location, remarks |
| `ifta_fuel` | IFTA-100 fuel receipt detail | jurisdiction, gallons, price/gallon, vendor, receipt #, odometer |
| `ifta_crossings` | State line crossings | jurisdiction, odometer, crossing_time (used to compute miles/state) |
| `alertness_logs` | Drowsy test history | timestamp, level, overall_score, memory/math/reaction sub-scores |

Database is auto-created on first run. IFTA quarterly miles per state are derived from the crossings table by diffing odometer readings at each state line.

---

## Agent 3 — Drowsy Driving Test (`agents/drowsy_test.py`)

A structured 3-part voice test. Results are scored and assessed by Claude, then spoken aloud. Every test is logged to the database (`alertness_logs` table).

| Test | What it measures | Scoring |
|---|---|---|
| **Word recall** | Memory — repeat 5 words after 3-second delay | Words recalled / 5 |
| **Mental math** | Cognitive speed — 3 timed arithmetic questions | Accuracy × speed factor |
| **Reaction time** | Physical alertness — say anything on "Now!" | Penalizes avg response > 1 s |

**Outcome levels:** `alert` (≥75%) · `warning` (50–74%) · `danger` (<50%)

In the mobile prototype the drowsy test runs fully on-device (timing, audio, scoring). Results are posted to `POST /alertness` when complete.

---

## Key Files

| File | Purpose |
|---|---|
| `main.py` | Entry point for local use — `python main.py` |
| `api.py` | FastAPI backend for hosted/mobile deployment |
| `orchestrator.py` | Wake word loop, intent routing, voice I/O |
| `voice_engine.py` | Microphone input (Google STT) + ElevenLabs TTS (falls back to `say`) |
| `config.py` | API keys, FMCSA constants, model selection |
| `.env` | Secrets (copy from `.env.example`) |
| `database.py` | SQLite DB layer — all read/write functions |
| `daily_log.py` | Startup flow — profile setup, prior-day completion, IFTA review, certification |
| `Procfile` | Railway deploy command (`uvicorn api:app`) |
| `data/truck_ai.db` | Auto-created SQLite database |

---

## Setup

**Local:**
```bash
pip install -r requirements-local.txt   # includes PyAudio + SpeechRecognition
brew install ffmpeg                      # for ElevenLabs audio streaming
cp .env.example .env
# Fill in .env with your API keys
python main.py
```

**Hosted (Railway):**
```bash
# Push to GitHub, connect repo in Railway, set env vars
```

**Required:** `ANTHROPIC_API_KEY`
**Optional:** `OPENWEATHER_API_KEY` (weather), `GOOGLE_PLACES_API_KEY` (rest areas, repair shops), `ELEVENLABS_API_KEY` + `ELEVENLABS_VOICE_ID` (better TTS voice)
