# Truck AI — Voice Assistant for Long-Haul Drivers

A voice-first AI co-pilot for commercial truck drivers. Runs on macOS using the Anthropic API.
Wake word: **"Hey Truck"** (also: "Truck AI", "Hey Driver", "Hey Assistant")

---

## Architecture

```
main.py
└── Orchestrator (orchestrator.py)
    ├── VoiceEngine      — STT (Google) + TTS (macOS say)
    ├── LogisticsAgent   — routes, weather, fuel stops, weigh stations
    ├── PaperworkAgent   — HOS logs, BOL, permits, FMCSA regs
    └── DrowsyTest       — 3-part alertness test with scored assessment
```

The orchestrator listens for the wake word, classifies the driver's command via keyword matching (with Claude as fallback), and routes to the appropriate agent. It tracks the active agent so follow-up questions stay in context.

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

### Database (`database.py` → `data/truck_ai.db`)

4 SQLite tables matching the physical form layouts:

| Table | Mirrors | Key columns |
|---|---|---|
| `hos_logs` | FMCSA 395.8 header | date, truck #, trailer #, BOL numbers, carrier, co-driver |
| `hos_entries` | 395.8 duty status grid | status, start_time, end_time, location, remarks |
| `ifta_fuel` | IFTA-100 fuel receipt detail | jurisdiction, gallons, price/gallon, vendor, receipt #, odometer |
| `ifta_crossings` | State line crossings | jurisdiction, odometer, crossing_time (used to compute miles/state) |

Database is auto-created on first run. IFTA quarterly miles per state are derived from the crossings table by diffing odometer readings at each state line.

---

## Agent 3 — Drowsy Driving Test (`agents/drowsy_test.py`)

A structured 3-part voice test. Results are scored and assessed by Claude, then spoken aloud. Every test is logged to `data/alertness_log.json`.

| Test | What it measures | Scoring |
|---|---|---|
| **Word recall** | Memory — repeat 5 words after 3-second delay | Words recalled / 5 |
| **Mental math** | Cognitive speed — 3 timed arithmetic questions | Accuracy × speed factor |
| **Reaction time** | Physical alertness — say anything on "Now!" | Penalizes avg response > 1 s |

**Outcome levels:** `alert` (≥75%) · `warning` (50–74%) · `danger` (<50%)

---

## Key Files

| File | Purpose |
|---|---|
| `main.py` | Entry point — `python main.py` |
| `orchestrator.py` | Wake word loop, intent routing, voice I/O |
| `voice_engine.py` | Microphone input (Google STT) + speech output (`say`) |
| `claude_client.py` | Thin Anthropic API wrapper with prompt caching |
| `config.py` | API keys, FMCSA constants, model selection |
| `.env` | Secrets (copy from `.env.example`) |
| `database.py` | SQLite DB layer — HOS and IFTA read/write functions |
| `data/truck_ai.db` | Auto-created SQLite database (HOS logs, IFTA records) |
| `data/alertness_log.json` | Auto-created drowsy test history |

---

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# Fill in .env with your API keys
python main.py
```

**Required:** `ANTHROPIC_API_KEY`
**Optional:** `OPENWEATHER_API_KEY` (weather), `GOOGLE_PLACES_API_KEY` (rest areas, repair shops)
