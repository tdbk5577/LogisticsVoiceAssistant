import anthropic
import requests
import config

_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

_SYSTEM = """You are a logistics co-pilot for a long-haul truck driver.
Help with: route planning, weather at locations, nearby fuel stops, weigh station info,
rest areas, road hazards, commercial vehicle restrictions, and ETAs.
Be brief — your responses are spoken aloud. Max 2-3 sentences.
Never use markdown, bullet points, asterisks, dashes, or lists. Plain spoken English only.
For weather always include wind speed. For routes note any CDL/commercial restrictions.

Tool guidance:
- Use find_truck_stops_or_weigh_stations for truck stops and weigh station lookups.
- Use search_places for everything else: rest areas, repair shops, hotels, restaurants, etc.
- Use get_weather for current conditions at a location."""

# ── Tool definitions ─────────────────────────────────────────────────────────

_WEATHER_TOOL = {
    "name": "get_weather",
    "description": "Get current weather conditions for a city or highway location.",
    "input_schema": {
        "type": "object",
        "properties": {
            "location": {
                "type": "string",
                "description": "City name or 'City, State' (e.g. 'Denver, CO')"
            }
        },
        "required": ["location"],
    },
}

_OSM_TOOL = {
    "name": "find_truck_stops_or_weigh_stations",
    "description": (
        "Find nearby truck stops (Pilot, Love's, TA, Flying J, Petro) or weigh stations "
        "using OpenStreetMap. Use this — not search_places — for these two types."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "location": {
                "type": "string",
                "description": "City, state or highway landmark (e.g. 'Salina, KS')",
            },
            "type": {
                "type": "string",
                "enum": ["truck_stops", "weigh_stations", "both"],
                "description": "What to search for",
            },
            "radius_miles": {
                "type": "integer",
                "description": "Search radius in miles (default 25)",
            },
        },
        "required": ["location", "type"],
    },
}

_PLACES_TOOL = {
    "name": "search_places",
    "description": (
        "Search for any logistics-related place that is NOT a truck stop or weigh station: "
        "rest areas, truck repair shops, motels, restaurants, CAT scales, hospitals, etc."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What to find, including location context (e.g. 'truck repair shop Amarillo TX')",
            },
        },
        "required": ["query"],
    },
}

# ── Tool implementations ──────────────────────────────────────────────────────

def _fetch_weather(location: str) -> str:
    if not config.OPENWEATHER_API_KEY:
        return "Weather service unavailable — no API key configured."
    try:
        r = requests.get(
            "https://api.openweathermap.org/data/2.5/weather",
            params={"q": location, "appid": config.OPENWEATHER_API_KEY, "units": "imperial"},
            timeout=5,
        )
        r.raise_for_status()
        d = r.json()
        desc = d["weather"][0]["description"]
        temp = d["main"]["temp"]
        wind = d["wind"]["speed"]
        feels = d["main"]["feels_like"]
        return f"{d['name']}: {desc}, {temp:.0f}°F (feels {feels:.0f}°F), wind {wind:.0f} mph"
    except Exception:
        return f"Weather unavailable for {location}."


def _geocode(location: str) -> tuple[float, float] | None:
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": location, "format": "json", "limit": 1},
            headers={"User-Agent": "TruckAI/1.0 (voice assistant)"},
            timeout=6,
        )
        r.raise_for_status()
        hits = r.json()
        if hits:
            return float(hits[0]["lat"]), float(hits[0]["lon"])
    except Exception:
        pass
    return None


def _find_osm(location: str, search_type: str, radius_miles: int = 25) -> str:
    coords = _geocode(location)
    if not coords:
        return f"Could not locate '{location}'."

    lat, lng = coords
    radius_m = radius_miles * 1609
    parts = []

    if search_type in ("truck_stops", "both"):
        parts += [
            f'node["amenity"="fuel"]["hgv"="yes"](around:{radius_m},{lat},{lng});',
            f'way["amenity"="fuel"]["hgv"="yes"](around:{radius_m},{lat},{lng});',
            f'node["amenity"="fuel"]["name"~"Pilot|Love|TA |Petro|Flying J|Kwik Trip",'
            f'i](around:{radius_m},{lat},{lng});',
        ]
    if search_type in ("weigh_stations", "both"):
        parts += [
            f'node["highway"="weigh_station"](around:{radius_m},{lat},{lng});',
            f'way["highway"="weigh_station"](around:{radius_m},{lat},{lng});',
        ]

    query = f"[out:json][timeout:15];({''.join(parts)});out center 8;"
    try:
        r = requests.post(
            "https://overpass-api.de/api/interpreter",
            data={"data": query},
            timeout=18,
        )
        r.raise_for_status()
        elements = r.json().get("elements", [])
    except Exception as e:
        return f"OpenStreetMap search failed: {e}"

    if not elements:
        label = search_type.replace("_", " ")
        return f"No {label} found within {radius_miles} miles of {location}."

    seen: set[str] = set()
    lines = []
    for el in elements:
        tags = el.get("tags", {})
        name = tags.get("name") or tags.get("operator") or "Unnamed"
        if name in seen:
            continue
        seen.add(name)
        city = tags.get("addr:city", "")
        lines.append(f"- {name}" + (f", {city}" if city else ""))
        if len(lines) == 6:
            break

    label = search_type.replace("_", " ").title()
    return f"{label} near {location}:\n" + "\n".join(lines)


def _search_places(query: str) -> str:
    if not config.GOOGLE_PLACES_API_KEY:
        return "Google Places unavailable — add GOOGLE_PLACES_API_KEY to your .env file."
    try:
        r = requests.get(
            "https://maps.googleapis.com/maps/api/place/textsearch/json",
            params={"query": query, "key": config.GOOGLE_PLACES_API_KEY},
            timeout=8,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("status") != "OK":
            return f"No results for '{query}'."

        lines = []
        for place in data["results"][:4]:
            name = place["name"]
            addr = place.get("formatted_address", "")
            rating = place.get("rating", "")
            suffix = f" ★{rating}" if rating else ""
            lines.append(f"- {name}{suffix}: {addr}")
        return f"Results for '{query}':\n" + "\n".join(lines)
    except Exception as e:
        return f"Places search failed: {e}"


def _dispatch(name: str, inputs: dict) -> str:
    if name == "get_weather":
        return _fetch_weather(inputs["location"])
    if name == "find_truck_stops_or_weigh_stations":
        return _find_osm(inputs["location"], inputs["type"], inputs.get("radius_miles", 25))
    if name == "search_places":
        return _search_places(inputs["query"])
    return "Unknown tool."


# ── Agent ─────────────────────────────────────────────────────────────────────

_TOOLS = [_WEATHER_TOOL, _OSM_TOOL, _PLACES_TOOL]


class LogisticsAgent:
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
