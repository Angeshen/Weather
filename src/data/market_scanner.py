"""
Scan Kalshi for open weather markets: high temp, low temp, and precipitation.
Parse market tickers to extract city, date, threshold, and market type.
"""

import re
from datetime import datetime
from src.config import settings, CITY_CONFIG, SERIES_PREFIXES
from src.data.kalshi_client import KalshiClient


def parse_weather_ticker(ticker: str) -> dict | None:
    """
    Parse a Kalshi weather market ticker to extract details.

    Examples:
        KXHIGHNY-25MAR27-T62   -> NYC high temp, 2025-03-27, threshold 62°F
        KXLOWCHI-25APR01-T35   -> Chicago low temp, 2025-04-01, threshold 35°F
        KXRAINMIA-25MAR28-T050 -> Miami rain, 2025-03-28, threshold 0.50 inches

    Returns dict with series_ticker, date, threshold, market_type, or None.
    """
    # Match: PREFIX+CITY - DATE - T<number>
    pattern = r"^(KX(?:HIGH|LOW|RAIN)\w+)-(\d{2,4}[A-Z]{3}\d{1,2})-T(\d+)$"
    match = re.match(pattern, ticker, re.IGNORECASE)
    if not match:
        return None

    series_part = match.group(1).upper()
    date_part = match.group(2).upper()
    raw_threshold = int(match.group(3))

    # Find the matching series ticker
    matched_series = None
    for series in settings.weather_series:
        if series_part.startswith(series):
            matched_series = series
            break

    if not matched_series:
        return None

    # Determine market type from the series prefix
    city_info = CITY_CONFIG.get(matched_series, {})
    market_type = city_info.get("market_type", "high_temp")

    # Parse threshold: precipitation uses hundredths (T050 = 0.50 inches)
    if market_type == "precipitation":
        threshold = raw_threshold / 100.0
    else:
        threshold = float(raw_threshold)

    # Parse date
    target_date = None
    for fmt in ["%y%b%d", "%Y%b%d"]:
        try:
            target_date = datetime.strptime(date_part, fmt).strftime("%Y-%m-%d")
            break
        except ValueError:
            continue

    if not target_date:
        return None

    # Unit label for display
    unit = "in" if market_type == "precipitation" else "°F"

    return {
        "ticker": ticker,
        "series_ticker": matched_series,
        "target_date": target_date,
        "threshold_f": threshold,
        "market_type": market_type,
        "unit": unit,
        "city": city_info.get("name", "Unknown"),
    }


def _enrich_market(market: dict, parsed: dict) -> dict:
    """Add price/volume data from a raw Kalshi market to a parsed ticker dict."""
    yes_price = market.get("yes_bid", 0) / 100.0 if market.get("yes_bid") else None
    no_price = market.get("no_bid", 0) / 100.0 if market.get("no_bid") else None
    yes_ask = market.get("yes_ask", 0) / 100.0 if market.get("yes_ask") else None
    no_ask = market.get("no_ask", 0) / 100.0 if market.get("no_ask") else None
    last_price = market.get("last_price", 0) / 100.0 if market.get("last_price") else None
    volume = market.get("volume", 0)

    parsed.update({
        "yes_bid": yes_price,
        "yes_ask": yes_ask,
        "no_bid": no_price,
        "no_ask": no_ask,
        "last_price": last_price,
        "volume": volume,
        "market_status": market.get("status", ""),
        "subtitle": market.get("yes_sub_title", ""),
        "raw_market": market,
    })
    return parsed


def scan_weather_markets(client: KalshiClient) -> list[dict]:
    """
    Scan all KXHIGH series for open weather markets using authenticated client.
    """
    all_markets = []

    for series_ticker in settings.weather_series:
        try:
            markets_result = client.get_markets(series_ticker=series_ticker, status="open")

            for market in markets_result.get("markets", []):
                ticker = market.get("ticker", "")
                parsed = parse_weather_ticker(ticker)
                if not parsed:
                    continue
                all_markets.append(_enrich_market(market, parsed))

        except Exception as e:
            print(f"  [!] Error scanning {series_ticker}: {e}")
            continue

    return all_markets


def scan_weather_markets_public() -> list[dict]:
    """
    Scan weather markets using public endpoints (no auth needed).
    Useful for paper trading mode.
    """
    import httpx

    base_url = "https://api.elections.kalshi.com/trade-api/v2"
    all_markets = []

    with httpx.Client(timeout=30.0) as http:
        for series_ticker in settings.weather_series:
            try:
                resp = http.get(
                    f"{base_url}/markets",
                    params={
                        "series_ticker": series_ticker,
                        "status": "open",
                        "limit": 200,
                    },
                )
                resp.raise_for_status()
                data = resp.json()

                for market in data.get("markets", []):
                    ticker = market.get("ticker", "")
                    parsed = parse_weather_ticker(ticker)
                    if not parsed:
                        continue
                    all_markets.append(_enrich_market(market, parsed))

            except Exception as e:
                print(f"  [!] Error scanning {series_ticker} (public): {e}")
                continue

    return all_markets
