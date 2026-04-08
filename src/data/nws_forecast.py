"""
NWS (National Weather Service) forecast cross-check.

Queries the free NWS API (api.weather.gov) for station-based forecasts
to provide a second independent opinion before trading. No API key needed.

If NWS and Open-Meteo disagree by more than a configurable threshold,
the trade should be skipped — the models are uncertain and the outcome
is closer to a coin flip.

Public interface: get_nws_forecast(station, target_date, market_type)
"""

import time
from datetime import date as _date, datetime, timezone

import httpx

# Cache NWS forecasts — they update every 1-6 hours
_nws_cache: dict = {}
_NWS_CACHE_TTL = 3600  # 1 hour

_NWS_BASE = "https://api.weather.gov"
_HEADERS = {
    "User-Agent": "KalshiWeatherBot/1.0 (contact: weather-bot@example.com)",
    "Accept": "application/geo+json",
}


def get_nws_forecast(station: str, target_date: str,
                     market_type: str = "high_temp") -> dict | None:
    """
    Fetch NWS forecast for a station and extract the predicted high/low temp
    for target_date.

    Args:
        station: NWS station ID (e.g. "KNYC", "KORD")
        target_date: "YYYY-MM-DD"
        market_type: "high_temp", "low_temp", or "precipitation"

    Returns:
        Dict with keys: high_f, low_f, precip_in, source, fetched_at
        or None if fetch fails.
    """
    cache_key = (station, target_date, market_type)
    cached = _nws_cache.get(cache_key)
    if cached:
        cached_at, cached_result = cached
        if time.time() - cached_at < _NWS_CACHE_TTL:
            return cached_result

    result = _fetch_gridpoint_forecast(station, target_date, market_type)
    if result:
        _nws_cache[cache_key] = (time.time(), result)
    return result


def _fetch_gridpoint_forecast(station: str, target_date: str,
                              market_type: str) -> dict | None:
    """
    NWS API flow:
    1. /points/{lat},{lon} → get gridpoint forecast URL
    2. /gridpoints/{office}/{x},{y}/forecast → get 7-day forecast

    We use the station's coordinates to get the gridpoint.
    """
    try:
        target = _date.fromisoformat(target_date)
    except (ValueError, TypeError):
        return None

    try:
        # Step 1: Get station metadata to find coordinates
        with httpx.Client(timeout=10.0, headers=_HEADERS) as client:
            # Get station info for coordinates
            station_resp = client.get(f"{_NWS_BASE}/stations/{station}")
            if station_resp.status_code != 200:
                return None

            station_data = station_resp.json()
            coords = station_data.get("geometry", {}).get("coordinates", [])
            if len(coords) < 2:
                return None

            lon, lat = coords[0], coords[1]

            # Step 2: Get gridpoint from coordinates
            points_resp = client.get(f"{_NWS_BASE}/points/{lat:.4f},{lon:.4f}")
            if points_resp.status_code != 200:
                return None

            points_data = points_resp.json()
            forecast_url = points_data.get("properties", {}).get("forecast")
            if not forecast_url:
                return None

            # Step 3: Get the actual forecast
            forecast_resp = client.get(forecast_url)
            if forecast_resp.status_code != 200:
                return None

            periods = forecast_resp.json().get("properties", {}).get("periods", [])
            if not periods:
                return None

            # Find periods matching our target date
            high_f = None
            low_f = None

            for period in periods:
                start_time = period.get("startTime", "")
                try:
                    period_date = datetime.fromisoformat(start_time).date()
                except (ValueError, TypeError):
                    continue

                if period_date != target:
                    continue

                temp = period.get("temperature")
                if temp is None:
                    continue

                # NWS gives temperature in F by default
                # "isDaytime": true = daytime period (high), false = nighttime (low)
                is_daytime = period.get("isDaytime", True)

                if is_daytime and high_f is None:
                    high_f = float(temp)
                elif not is_daytime and low_f is None:
                    low_f = float(temp)

            if high_f is None and low_f is None:
                return None

            return {
                "high_f": high_f,
                "low_f": low_f,
                "source": "NWS",
                "station": station,
                "target_date": target_date,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }

    except Exception:
        return None


def nws_agrees(nws_forecast: dict, open_meteo_mean: float,
               market_type: str = "high_temp",
               max_disagreement_f: float = 5.0) -> tuple[bool, float]:
    """
    Check if NWS and Open-Meteo forecasts agree within tolerance.

    Args:
        nws_forecast: Result from get_nws_forecast()
        open_meteo_mean: Mean temperature from Open-Meteo ensemble
        market_type: "high_temp" or "low_temp"
        max_disagreement_f: Max allowed disagreement in °F

    Returns:
        (agrees: bool, disagreement: float)
        agrees=True if within tolerance, disagreement is absolute difference
    """
    if not nws_forecast:
        # If NWS is unavailable, don't block the trade — degrade gracefully
        return True, 0.0

    if market_type == "high_temp":
        nws_temp = nws_forecast.get("high_f")
    elif market_type == "low_temp":
        nws_temp = nws_forecast.get("low_f")
    else:
        # No NWS comparison for precipitation yet
        return True, 0.0

    if nws_temp is None:
        return True, 0.0

    disagreement = abs(nws_temp - open_meteo_mean)
    agrees = disagreement <= max_disagreement_f

    return agrees, round(disagreement, 1)
