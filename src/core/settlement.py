"""
Auto-settlement engine.
Fetches actual observed weather data from Open-Meteo historical API
and settles open trades by comparing actual values to thresholds.
"""

import sqlite3
from datetime import datetime, timezone, timedelta

import httpx

from src.config import CITY_CONFIG
from src.core.trade_executor import get_db, get_current_bankroll, log_bankroll, log_forecast_accuracy
from src.core.notifications import notify_daily_summary


OPEN_METEO_HISTORICAL_URL = "https://archive-api.open-meteo.com/v1/archive"
KALSHI_PUBLIC_URL = "https://api.elections.kalshi.com/trade-api/v2"


def fetch_kalshi_resolution(ticker: str) -> float | None:
    """
    Fetch the official Kalshi expiration_value for a settled market.
    This is the exact NWS value Kalshi used to resolve the contract.
    Returns the value as a float, or None if not yet resolved.
    """
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(f"{KALSHI_PUBLIC_URL}/markets/{ticker}")
            resp.raise_for_status()
            market = resp.json().get("market", {})
            expiration_value = market.get("expiration_value", "")
            if expiration_value:
                return float(expiration_value)
    except Exception:
        pass
    return None


def fetch_actual_weather(lat: float, lon: float, date: str, market_type: str) -> float | None:
    """
    Fetch actual observed weather data for a given city and date.

    Returns:
        Actual value (°F for temp, inches for precip), or None if unavailable.
    """
    variable_map = {
        "high_temp": "temperature_2m_max",
        "low_temp": "temperature_2m_min",
        "precipitation": "precipitation_sum",
    }
    variable = variable_map.get(market_type, "temperature_2m_max")

    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": variable,
        "start_date": date,
        "end_date": date,
        "timezone": "America/New_York",
    }

    if market_type in ("high_temp", "low_temp"):
        params["temperature_unit"] = "fahrenheit"
    if market_type == "precipitation":
        params["precipitation_unit"] = "inch"

    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.get(OPEN_METEO_HISTORICAL_URL, params=params)
            resp.raise_for_status()
            data = resp.json()

        daily = data.get("daily", {})
        values = daily.get(variable, [])
        if values and values[0] is not None:
            return float(values[0])
    except Exception:
        pass

    return None


def settle_open_trades() -> dict:
    """
    Check all open trades whose target_date has passed and settle them.

    Returns dict with settlement results.
    """
    conn = get_db()
    conn.row_factory = sqlite3.Row

    # Only settle trades whose target date is in the past
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")

    open_trades = conn.execute(
        "SELECT * FROM trades WHERE status = 'open' AND target_date < ?",
        (today,)
    ).fetchall()

    settled = 0
    wins = 0
    losses = 0
    total_pnl = 0.0
    results = []

    for trade in open_trades:
        trade = dict(trade)
        ticker = trade["ticker"]
        series_ticker = None
        market_type = "high_temp"

        # Find the series ticker and market type from the ticker
        for series, info in CITY_CONFIG.items():
            if ticker.upper().startswith(series):
                series_ticker = series
                market_type = info.get("market_type", "high_temp")
                break

        if not series_ticker:
            continue

        city_info = CITY_CONFIG.get(series_ticker)
        if not city_info:
            continue

        # Only settle using Kalshi's official expiration_value (exact NWS number).
        # Never fall back to Open-Meteo — it can have unfinalized same-day data
        # which causes premature incorrect settlements.
        actual = fetch_kalshi_resolution(ticker)

        if actual is None:
            continue  # Kalshi hasn't resolved yet — try again next scan

        threshold = trade["threshold_f"]
        direction = trade.get("direction", "").upper()
        side = trade.get("side", "").lower()

        # Determine if trade won
        actual_above = actual > threshold

        if side == "yes":
            won = actual_above
        else:  # side == "no"
            won = not actual_above

        # Calculate P&L
        # Each contract pays $1 on a win. You paid `cost` total for `contracts` contracts.
        # Win: profit = contracts * $1 - cost
        # Loss: lose entire stake = -cost
        cost = trade["position_size_usd"]
        contracts = trade.get("contracts") or 0
        if won:
            pnl = round(contracts * 1.0 - cost, 2)
            wins += 1
        else:
            pnl = round(-cost, 2)
            losses += 1

        pnl = round(pnl, 2)
        total_pnl += pnl
        settled += 1

        # Update trade in DB — store actual temp and settled P&L
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("""
            UPDATE trades SET status = 'settled', pnl_usd = ?, settled_at = ?, actual_temp = ?
            WHERE id = ?
        """, (pnl, now, actual, trade["id"]))

        # Log forecast accuracy for bias correction
        try:
            log_forecast_accuracy(
                city=trade.get("city", ""),
                target_date=trade["target_date"],
                threshold_f=trade["threshold_f"],
                forecast_mean=trade.get("forecast_mean"),
                actual_temp=actual,
                side=trade.get("side", ""),
                won=won,
            )
        except Exception:
            pass

        # Update daily P&L
        trade_date = trade["target_date"]
        existing = conn.execute(
            "SELECT * FROM daily_pnl WHERE date = ?", (trade_date,)
        ).fetchone()
        if existing:
            conn.execute("""
                UPDATE daily_pnl SET total_pnl = total_pnl + ?, trades_count = trades_count + 1,
                wins = wins + ?, losses = losses + ? WHERE date = ?
            """, (pnl, 1 if won else 0, 0 if won else 1, trade_date))
        else:
            conn.execute("""
                INSERT INTO daily_pnl (date, total_pnl, trades_count, wins, losses)
                VALUES (?, ?, 1, ?, ?)
            """, (trade_date, pnl, 1 if won else 0, 0 if won else 1))

        unit = "in" if market_type == "precipitation" else "°F"
        forecast_mean = trade.get("forecast_mean")
        model_error = round(forecast_mean - actual, 1) if forecast_mean else None
        results.append({
            "trade_id": trade["id"],
            "ticker": ticker,
            "city": trade.get("city", "?"),
            "target_date": trade["target_date"],
            "threshold": threshold,
            "actual": actual,
            "forecast_mean": forecast_mean,
            "model_error": model_error,
            "unit": unit,
            "direction": direction,
            "side": side,
            "won": won,
            "pnl": pnl,
        })

    conn.commit()

    # Update bankroll
    if total_pnl != 0:
        new_bankroll = get_current_bankroll() + total_pnl
        log_bankroll(new_bankroll, f"Settled {settled} trades, P&L: ${total_pnl:+.2f}")

    conn.close()

    return {
        "settled": settled,
        "wins": wins,
        "losses": losses,
        "total_pnl": round(total_pnl, 2),
        "results": results,
    }
