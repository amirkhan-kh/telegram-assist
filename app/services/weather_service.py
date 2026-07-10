"""Weather lookup for Jarvis-style briefings.

Uses Open-Meteo's public geocoding/forecast endpoints so the assistant can give
weather without introducing another paid key. Network failures degrade to a
clear Uzbek message instead of failing the command.
"""

from __future__ import annotations

import asyncio
import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from app.logging_conf import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class WeatherReport:
    location: str
    scope: str
    text: str


_WEATHER_CODES = {
    0: "ochiq",
    1: "asosan ochiq",
    2: "qisman bulutli",
    3: "bulutli",
    45: "tuman",
    48: "qirovli tuman",
    51: "mayda yomg'ir",
    53: "yomg'ir",
    55: "kuchli yomg'ir",
    61: "yomg'ir",
    63: "yomg'ir",
    65: "kuchli yomg'ir",
    71: "qor",
    73: "qor",
    75: "kuchli qor",
    80: "qisqa yomg'ir",
    81: "qisqa yomg'ir",
    82: "kuchli qisqa yomg'ir",
    95: "momaqaldiroq",
}


async def get_weather(location: str, scope: str) -> WeatherReport:
    """Fetch and format a weather report."""
    return await asyncio.to_thread(_get_weather_sync, location, scope)


def _get_weather_sync(location: str, scope: str) -> WeatherReport:
    loc = (location or "Tashkent").strip() or "Tashkent"
    try:
        place = _geocode(loc)
        data = _forecast(place["latitude"], place["longitude"])
        text = _format(place["name"], data, scope)
        return WeatherReport(location=place["name"], scope=scope, text=text)
    except Exception as exc:  # noqa: BLE001 - user-facing command must degrade
        logger.warning("weather.lookup.failed", location=loc, error=str(exc))
        return WeatherReport(
            location=loc,
            scope=scope,
            text="Ob-havo ma'lumotini hozir olib bo'lmadi. Keyinroq urinib ko'ring.",
        )


def _fetch_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=12) as resp:  # noqa: S310 - fixed API URLs
        return json.loads(resp.read().decode("utf-8"))


def _geocode(location: str) -> dict[str, Any]:
    q = urllib.parse.urlencode({"name": location, "count": 1, "language": "uz"})
    data = _fetch_json(f"https://geocoding-api.open-meteo.com/v1/search?{q}")
    results = data.get("results") or []
    if not results:
        raise ValueError(f"location not found: {location}")
    item = results[0]
    return {
        "name": item.get("name") or location,
        "latitude": item["latitude"],
        "longitude": item["longitude"],
    }


def _forecast(lat: float, lon: float) -> dict[str, Any]:
    q = urllib.parse.urlencode(
        {
            "latitude": lat,
            "longitude": lon,
            "daily": ",".join(
                [
                    "weather_code",
                    "temperature_2m_max",
                    "temperature_2m_min",
                    "precipitation_probability_max",
                ]
            ),
            "forecast_days": 7,
            "timezone": "auto",
        }
    )
    return _fetch_json(f"https://api.open-meteo.com/v1/forecast?{q}")


def _format(location: str, data: dict[str, Any], scope: str) -> str:
    daily = data.get("daily") or {}
    dates = daily.get("time") or []
    max_t = daily.get("temperature_2m_max") or []
    min_t = daily.get("temperature_2m_min") or []
    codes = daily.get("weather_code") or []
    rain = daily.get("precipitation_probability_max") or []
    if not dates:
        return f"{location} uchun prognoz topilmadi."

    start = 1 if scope == "tomorrow" else 0
    count = 7 if scope == "week" else 1
    rows = []
    for idx in range(start, min(len(dates), start + count)):
        d = _label_date(dates[idx])
        code = _WEATHER_CODES.get(int(codes[idx]), "ob-havo")
        rows.append(
            f"{d}: {code}, {round(min_t[idx])}…{round(max_t[idx])}°C, "
            f"yomg'ir ehtimoli {rain[idx] or 0}%"
        )
    header = f"🌤 {location} ob-havosi"
    if scope == "week":
        return header + "\n" + "\n".join(rows)
    return header + "\n" + rows[0]


def _label_date(raw: str) -> str:
    try:
        d = date.fromisoformat(raw)
    except ValueError:
        return raw
    today = datetime.now().date()
    if d == today:
        return "Bugun"
    if d == today + timedelta(days=1):
        return "Ertaga"
    return d.strftime("%d.%m")
