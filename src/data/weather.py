"""
Multi-model ensemble forecast using Open-Meteo free forecast API.

Fetches 5 deterministic NWP models (GFS, ECMWF, ICON, GEM, MeteoFrance),
then generates 10 perturbed members per model using realistic RMSE to build
a 50-member pseudo-ensemble. Free, fast, no rate limits.

For live trading upgrade path: subscribe to Open-Meteo $99/month plan
and set OPEN_METEO_API_KEY in .env to get true 51-member ECMWF ENS.

Public interface: get_forecast_for_city / compute_threshold_probability.
"""

import math
import time
from datetime import date as _date

import httpx
from src.config import CITY_CONFIG

# In-memory forecast cache — NWP models update every 6-12h
# Key: (series_ticker, target_date, threshold), Value: (fetched_timestamp, result)
_forecast_cache: dict = {}
_CACHE_TTL_SECONDS = 6 * 3600  # 6 hours

_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

_MODELS = [
    "gfs_seamless",
    "ecmwf_ifs025",
    "icon_seamless",
    "gem_seamless",
    "meteofrance_seamless",
]

_VARIABLE_MAP = {
    "high_temp": "temperature_2m_max",
    "low_temp": "temperature_2m_min",
    "precipitation": "precipitation_sum",
}

# Realistic NWP RMSE by forecast horizon (°F)
_RMSE_DAY1 = 2.5
_RMSE_DAY2 = 4.0
_MEMBERS_PER_MODEL = 10


def fetch_ensemble_forecast(lat: float, lon: float, target_date: str,
                            market_type: str = "high_temp") -> list[float]:
    """
    Build a 50-member pseudo-ensemble from 5 deterministic NWP models.

    Each model's forecast is perturbed with realistic RMSE-scaled noise
    using a deterministic Box-Muller transform (reproducible per city+date).

    Returns list of 50 pseudo-member daily values in °F (or inches for precip).
    """
    try:
        target = _date.fromisoformat(target_date)
        days_ahead = (target - _date.today()).days
        if days_ahead < 0 or days_ahead > 7:
            return []
    except (ValueError, TypeError):
        return []

    variable = _VARIABLE_MAP.get(market_type, "temperature_2m_max")
    rmse = _RMSE_DAY1 if days_ahead <= 1 else _RMSE_DAY2
    rng_seed = hash(f"{lat:.2f}_{lon:.2f}_{target_date}")

    deterministic_values = []
    for model in _MODELS:
        try:
            params = {
                "latitude": lat,
                "longitude": lon,
                "daily": variable,
                "start_date": target_date,
                "end_date": target_date,
                "temperature_unit": "fahrenheit",
                "models": model,
                "timezone": "America/New_York",
            }
            with httpx.Client(timeout=15.0) as client:
                resp = client.get(_FORECAST_URL, params=params)
                if resp.status_code != 200:
                    continue
                vals = resp.json().get("daily", {}).get(variable, [])
                if vals and vals[0] is not None:
                    deterministic_values.append(float(vals[0]))
        except Exception:
            continue

    if not deterministic_values:
        return []

    pseudo_ensemble = []
    for base_val in deterministic_values:
        for i in range(_MEMBERS_PER_MODEL):
            s1 = max((hash(f"{rng_seed}_{base_val:.2f}_a{i}") % 100000) / 100000.0, 1e-9)
            s2 = max((hash(f"{rng_seed}_{base_val:.2f}_b{i}") % 100000) / 100000.0, 1e-9)
            z = math.sqrt(-2 * math.log(s1)) * math.cos(2 * math.pi * s2)
            pseudo_ensemble.append(base_val + z * rmse)

    return pseudo_ensemble


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
