"""
Fetch 31-member GFS ensemble forecasts from Open-Meteo for:
  - Daily high temperature
  - Daily low temperature
  - Daily precipitation sum
Compute probability of thresholds being exceeded.
"""

import time
import httpx
from src.config import CITY_CONFIG, settings

# In-memory forecast cache — GFS updates every 6h, no need to re-fetch more often
# Key: (series_ticker, target_date, threshold), Value: (timestamp, result)
_forecast_cache: dict = {}
_CACHE_TTL_SECONDS = 6 * 3600  # 6 hours


OPEN_METEO_ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"

# Map market_type -> Open-Meteo daily variable name
_VARIABLE_MAP = {
    "high_temp": "temperature_2m_max",
    "low_temp": "temperature_2m_min",
    "precipitation": "precipitation_sum",
}


def _extract_ensemble_values(daily: dict, variable: str) -> list[float]:
    """Extract ensemble member values from the Open-Meteo daily response.

    Open-Meteo returns one key per member: e.g. temperature_2m_max_member01,
    temperature_2m_max_member02, ... plus a non-suffixed ensemble-mean key.
    We want only the per-member keys (each is a list with one value per date).
    """
    values = []

    # Collect member-keyed fields (temperature_2m_max_member01, etc.)
    for key, val_list in daily.items():
        if key.startswith(f"{variable}_member") and isinstance(val_list, list):
            for val in val_list:
                if val is not None:
                    values.append(float(val))

    # Fallback: if no member keys found, use the primary ensemble-mean field
    if not values:
        primary = daily.get(variable, [])
        if isinstance(primary, list):
            for val in primary:
                if val is not None:
                    values.append(float(val))

    return values


# Ensemble models available from Open-Meteo (model name -> member count)
ENSEMBLE_MODELS = {
    "gfs_seamless": 31,
    "ecmwf_ifs025": 51,
    "icon_seamless": 40,
}


def _fetch_single_model(lat: float, lon: float, target_date: str,
                        market_type: str, model: str) -> list[float]:
    """Fetch ensemble forecast from a single model."""
    variable = _VARIABLE_MAP.get(market_type, "temperature_2m_max")

    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": variable,
        "start_date": target_date,
        "end_date": target_date,
        "models": model,
    }

    if market_type in ("high_temp", "low_temp"):
        params["temperature_unit"] = "fahrenheit"
    if market_type == "precipitation":
        params["precipitation_unit"] = "inch"
    api_key = getattr(settings, "open_meteo_api_key", "")
    if api_key:
        params["apikey"] = api_key

    with httpx.Client(timeout=20.0) as client:
        resp = client.get(OPEN_METEO_ENSEMBLE_URL, params=params)
        resp.raise_for_status()
        data = resp.json()

    return _extract_ensemble_values(data.get("daily", {}), variable)


def fetch_ensemble_forecast(lat: float, lon: float, target_date: str,
                            market_type: str = "high_temp") -> list[float]:
    """
    Fetch ensemble forecasts from multiple models (GFS, ECMWF, ICON)
    and combine all members into a single super-ensemble.

    Returns:
        Combined list of forecast values from all available models.
        Temperature in °F, precipitation in inches.
    """
    all_values = []
    models_used = []

    for model in ENSEMBLE_MODELS:
        try:
            values = _fetch_single_model(lat, lon, target_date, market_type, model)
            if values:
                all_values.extend(values)
                models_used.append(model)
        except Exception:
            continue  # Model unavailable or failed, skip

    # Fallback: if no models returned data, try GFS alone one more time
    if not all_values:
        try:
            all_values = _fetch_single_model(lat, lon, target_date, market_type, "gfs_seamless")
        except Exception:
            pass

    return all_values


def compute_threshold_probability(ensemble_values: list[float], threshold: float,
                                  market_type: str = "high_temp") -> dict:
    """
    Given ensemble forecast values, compute probability of exceeding the threshold.

    Returns dict with forecast stats and probability analysis.
    """
    if not ensemble_values:
        return {
            "prob_above": 0.5, "prob_below": 0.5,
            "n_members": 0, "n_above": 0,
            "mean_val": 0, "min_val": 0, "max_val": 0,
            "confidence": 0, "market_type": market_type,
        }

    n = len(ensemble_values)
    n_above = sum(1 for v in ensemble_values if v > threshold)
    n_below = n - n_above
    prob_above = n_above / n
    prob_below = n_below / n
    confidence = abs(prob_above - 0.5) * 2

    return {
        "prob_above": prob_above,
        "prob_below": prob_below,
        "n_members": n,
        "n_above": n_above,
        "mean_val": sum(ensemble_values) / n,
        "min_val": min(ensemble_values),
        "max_val": max(ensemble_values),
        "confidence": confidence,
        "market_type": market_type,
    }


def get_forecast_for_city(series_ticker: str, target_date: str, threshold: float,
                         cache_ttl: int = _CACHE_TTL_SECONDS) -> dict:
    """
    Full pipeline: fetch ensemble for a city and compute threshold probability.

    Args:
        series_ticker: e.g. "KXHIGHNY", "KXLOWCHI", "KXRAINMIA"
        target_date: "YYYY-MM-DD"
        threshold: threshold value (°F for temp, inches for precip)

    Returns:
        Dict with forecast data and probability analysis.
    """
    city = CITY_CONFIG.get(series_ticker)
    if not city:
        return {"error": f"Unknown series ticker: {series_ticker}"}

    # Check cache — GFS only updates every 6h, no need to re-fetch on every scan
    cache_key = (series_ticker, target_date, threshold)
    cached = _forecast_cache.get(cache_key)
    if cached:
        cached_at, cached_result = cached
        if time.time() - cached_at < cache_ttl:
            return cached_result

    market_type = city.get("market_type", "high_temp")

    ensemble_values = fetch_ensemble_forecast(
        city["lat"], city["lon"], target_date, market_type
    )

    analysis = compute_threshold_probability(ensemble_values, threshold, market_type)
    analysis["city"] = city["name"]
    analysis["series_ticker"] = series_ticker
    analysis["target_date"] = target_date
    analysis["threshold"] = threshold

    # Backward-compatible aliases for the dashboard/edge calculator
    analysis["mean_high"] = analysis["mean_val"]
    analysis["min_high"] = analysis["min_val"]
    analysis["max_high"] = analysis["max_val"]
    analysis["threshold_f"] = threshold

    # Store in cache only if we got real data (n_members > 0)
    if analysis.get("n_members", 0) > 0:
        _forecast_cache[cache_key] = (time.time(), analysis)

    return analysis
