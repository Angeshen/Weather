"""
Scan Kalshi for open weather markets: high temp, low temp, and precipitation.
Parse market tickers to extract city, date, threshold, and market type.
"""

import re
import time
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
    def _parse_price(val):
        """Parse a price value that may be a dollar string ('0.0700'), cents int, or None."""
        if val is None:
            return None
        try:
            f = float(val)
            # If value looks like it's already in [0,1] range it's dollars, else cents
            return f if f <= 1.0 else f / 100.0
        except (ValueError, TypeError):
            return None

    yes_price = _parse_price(market.get("yes_bid_dollars") or market.get("yes_bid"))
    no_price = _parse_price(market.get("no_bid_dollars") or market.get("no_bid"))
    yes_ask = _parse_price(market.get("yes_ask_dollars") or market.get("yes_ask"))
    no_ask = _parse_price(market.get("no_ask_dollars") or market.get("no_ask"))
    last_price = _parse_price(market.get("last_price_dollars") or market.get("last_price"))
    volume = market.get("volume_fp") or market.get("volume", 0)

    # Parse YES direction and corrected threshold from subtitle
    # e.g. "62° or above" -> yes_means_above=True, yes_threshold=62
    # e.g. "53° or below" -> yes_means_above=False, yes_threshold=53
    subtitle = market.get("yes_sub_title", "")
    yes_means_above = True  # default
    yes_threshold = parsed.get("threshold_f")  # fallback to ticker threshold
    if subtitle:
        import re as _re
        m = _re.search(r'(\d+(?:\.\d+)?)[°]?\s*or\s*(above|below)', subtitle, _re.IGNORECASE)
        if m:
            yes_threshold = float(m.group(1))
            yes_means_above = m.group(2).lower() == "above"

    parsed.update({
        "yes_bid": yes_price,
        "yes_ask": yes_ask,
        "no_bid": no_price,
        "no_ask": no_ask,
        "last_price": last_price,
        "volume": volume,
        "market_status": market.get("status", ""),
        "subtitle": subtitle,
        "yes_means_above": yes_means_above,
        "yes_threshold": yes_threshold,
        "raw_market": market,
    })
    return parsed


def discover_active_series() -> list[str]:
    """
    Query Kalshi to discover which weather series currently have open markets.
    Returns list of active series tickers to replace the hardcoded list.
    Falls back to settings.weather_series if discovery fails.
    """
    import httpx
    base_url = "https://api.elections.kalshi.com/trade-api/v2"
    found = set()

    try:
        with httpx.Client(timeout=15.0) as http:
            # Check all possible series combinations we know about
            candidates = []
            standard_suffixes = ["NY", "CHI", "MIA", "LAX", "DEN",
                                  "SEA", "DAL", "ATL", "PHX", "HOU", "BOS", "PHI", "DC"]
            kxhight_suffixes = ["HOU", "PHX", "BOS", "DAL", "DC", "SEA", "PHI", "ATL",
                                 "NY", "CHI", "MIA", "LAX", "DEN"]
            kxlowt_suffixes = ["NYC", "CHI", "MIA", "LAX", "DEN",
                               "SEA", "DAL", "ATL", "PHX", "HOU", "BOS", "DC", "PHI"]
            # Also add explicit known tickers that don't follow prefix+suffix pattern
            extras = ["KXHIGHPHIL"]
            for prefix in SERIES_PREFIXES:
                if prefix == "KXHIGHT":
                    suffixes = kxhight_suffixes
                elif prefix == "KXLOWT":
                    suffixes = kxlowt_suffixes
                else:
                    suffixes = standard_suffixes
                for suffix in suffixes:
                    candidates.append(f"{prefix}{suffix}")
            candidates.extend(extras)

            for series in candidates:
                try:
                    resp = http.get(
                        f"{base_url}/markets",
                        params={"series_ticker": series, "status": "open", "limit": 1},
                        timeout=5.0,
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        if data.get("markets"):
                            found.add(series)
                except Exception:
                    continue
    except Exception:
        pass

    if found:
        # Only return series we have city config for
        active = [s for s in sorted(found) if s in CITY_CONFIG]
        if active:
            print(f"[market_scanner] Auto-discovered {len(active)} active series: {active}")
            return active

    # Fall back to hardcoded list
    return settings.weather_series


def scan_weather_markets(client: KalshiClient) -> list[dict]:
    """
    Scan all KXHIGH series for open weather markets using authenticated client.
    """
    all_markets = []

    for i, series_ticker in enumerate(settings.weather_series):
        if i > 0:
            time.sleep(0.5)
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
        for i, series_ticker in enumerate(settings.weather_series):
            if i > 0:
                time.sleep(0.5)
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
