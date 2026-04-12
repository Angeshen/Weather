"""
Calculate edge between model probability and market price.
Determine trade signals and Kelly criterion position sizing.
"""

import time as _time
from src.config import settings

# --- Momentum tracker ---
# Stores recent yes_ask prices per ticker across scan cycles.
# Key: ticker, Value: list of (timestamp, yes_ask) tuples, newest first.
_price_history: dict[str, list[tuple[float, float]]] = {}
_PRICE_HISTORY_MAX = 6       # keep last 6 observations (~30-60 min at 5-10 min scans)
_PRICE_HISTORY_TTL = 7200    # discard prices older than 2 hours


def record_price(ticker: str, yes_ask: float | None):
    """Record a price observation for momentum tracking. Called during scan."""
    if not yes_ask or yes_ask <= 0:
        return
    now = _time.time()
    history = _price_history.setdefault(ticker, [])
    # Deduplicate — don't add if last entry is same price and < 30s ago
    if history and abs(history[0][1] - yes_ask) < 0.005 and now - history[0][0] < 30:
        return
    history.insert(0, (now, yes_ask))
    # Trim old entries
    _price_history[ticker] = [
        (t, p) for t, p in history[:_PRICE_HISTORY_MAX]
        if now - t < _PRICE_HISTORY_TTL
    ]


def get_momentum(ticker: str, side: str) -> float | None:
    """
    Return price momentum for a ticker.
    Positive = price moving up (good for YES buyers, bad for NO buyers).
    Negative = price moving down (good for NO buyers, bad for YES buyers).
    Returns None if not enough history.
    """
    history = _price_history.get(ticker, [])
    if len(history) < 2:
        return None  # Not enough data — allow trade
    newest_price = history[0][1]
    oldest_price = history[-1][1]
    return newest_price - oldest_price


def calculate_edge(model_prob: float, market_prob: float) -> float:
    """
    Edge = model probability - market probability.
    Positive edge on YES side means model thinks YES is underpriced.
    """
    return model_prob - market_prob


def kelly_size(win_prob: float, odds: float) -> float:
    """
    Kelly criterion: f* = (p * b - q) / b
    where p = win probability, q = 1-p, b = net odds (payout - 1).

    Returns fraction of bankroll to bet (can be negative = don't bet).
    """
    if odds <= 0 or win_prob <= 0 or win_prob >= 1:
        return 0.0

    q = 1.0 - win_prob
    kelly = (win_prob * odds - q) / odds
    return max(kelly, 0.0)


def compute_position_size(win_prob: float, market_price: float, bankroll: float,
                          days_to_expiry: int = 7) -> float:
    """
    Compute dollar position size using flat sizing for scalping strategy.

    Scalping sells at +25% gain, not hold-to-expiry, so Kelly criterion
    (designed for binary outcomes) over-sizes positions. Instead we use a
    flat 3-5% of bankroll per trade, scaled by edge strength and expiry.

    Args:
        win_prob: Model's estimated probability of winning (0-1).
        market_price: Current market price as probability (0-1), i.e. cost per contract.
        bankroll: Current bankroll in dollars.
        days_to_expiry: Days until market expires. Closer = smaller position.

    Returns:
        Dollar amount to bet.
    """
    if market_price <= 0 or market_price >= 1:
        return 0.0

    # Base: 4% of bankroll per trade
    base_pct = 0.04
    position = base_pct * bankroll

    # Scale up slightly for higher edge (more confident trades get more $)
    edge = win_prob - market_price
    if edge >= 0.15:
        position *= 1.5   # 6% of bankroll for strong edge
    elif edge >= 0.10:
        position *= 1.25  # 5% of bankroll for good edge

    # Scale down for near-expiry markets where forecast has less value
    if days_to_expiry <= 0:
        expiry_scale = 0.25
    elif days_to_expiry <= 1:
        expiry_scale = 0.60
    elif days_to_expiry <= 2:
        expiry_scale = 0.80
    else:
        expiry_scale = 1.0

    position = position * expiry_scale
    position = min(position, settings.max_trade_size)
    position = max(position, 0.0)

    return round(position, 2)


def _direction_labels(market_type: str) -> tuple[str, str]:
    """Return (yes_label, no_label) for the market type."""
    if market_type == "precipitation":
        return ("RAIN YES", "RAIN NO")
    elif market_type == "low_temp":
        return ("LOW ABOVE", "LOW BELOW")
    else:
        return ("ABOVE", "BELOW")


def _is_liquid(market: dict, required_contracts: int = 0) -> bool:
    """
    Check if a market has enough liquidity to trade safely.
    Wide spreads mean we'd pay too much slippage; low volume means poor fills.
    If required_contracts is provided, also checks we won't exceed available volume.
    """
    volume = float(market.get("volume", 0) or 0)
    yes_bid = market.get("yes_bid") or 0
    yes_ask = market.get("yes_ask") or 0
    no_bid = market.get("no_bid") or 0
    no_ask = market.get("no_ask") or 0

    if volume < int(settings.min_liquidity_volume):
        return False

    # If we know how many contracts we need, ensure historical volume can absorb it.
    # Require 2x our contract count — historical volume != live book depth, so 2x
    # gives a reasonable buffer for partial fills and thin order books.
    if required_contracts > 0 and volume < required_contracts * 2:
        return False

    # Check spread on whichever side has quotes
    if yes_bid and yes_ask:
        spread = int(yes_ask * 100) - int(yes_bid * 100)
        if spread > int(settings.max_spread_cents):
            return False
    if no_bid and no_ask:
        spread = int(no_ask * 100) - int(no_bid * 100)
        if spread > int(settings.max_spread_cents):
            return False

    return True


def evaluate_market(market: dict, forecast: dict, bankroll: float) -> dict | None:
    """
    Evaluate a single market against its forecast to produce a trade signal.

    Args:
        market: Parsed market dict from market_scanner.
        forecast: Forecast analysis dict from weather module.
        bankroll: Current bankroll.

    Returns:
        Trade signal dict, or None if no edge.
    """
    from datetime import date as _date
    ticker = market.get("ticker", "?")
    try:
        target = _date.fromisoformat(market.get("target_date", ""))
        days_to_expiry = (target - _date.today()).days
    except (ValueError, TypeError):
        days_to_expiry = 7
    # Skip illiquid markets — wide spreads eat our edge, thin volume means bad fills
    if not _is_liquid(market):
        print(f"[filter] {ticker}: REJECTED — illiquid (vol={market.get('volume',0)}, spread check failed)")
        return None

    n_members = forecast.get("n_members", 0)
    if n_members < 40:
        print(f"[filter] {ticker}: REJECTED — ensemble too small ({n_members} members)")
        return None

    model_prob_above = forecast["prob_above"]
    model_prob_below = forecast["prob_below"]
    confidence = forecast["confidence"]

    # Apply bias correction: if model historically runs warm/cold for this city,
    # shift the effective threshold before computing probabilities.
    # e.g. if model has +2°F warm bias, treat threshold as 2°F higher (harder to exceed).
    # Uses ensemble members directly — no scipy needed.
    try:
        from src.core.trade_executor import get_city_bias
        city = market.get("city", "")
        bias = get_city_bias(city)  # positive = model runs warm
        if bias != 0.0:
            # Instead of resampling, shift the threshold by +bias.
            # If model runs +2°F warm, actual temps are ~2°F lower than forecast,
            # so it's harder to exceed the threshold → shift threshold up by bias.
            threshold_val = market.get("yes_threshold") or market.get("threshold_f", 0)
            corrected_threshold = threshold_val + bias
            mean_val = forecast.get("mean_high", 0) or forecast.get("mean_val", 0)
            std = (forecast.get("max_high", mean_val) - forecast.get("min_high", mean_val)) / 4
            if std > 0 and mean_val:
                # Approximate normal CDF using math.erf (no scipy needed)
                import math
                z = (corrected_threshold - mean_val) / std
                corrected_prob_below = 0.5 * (1 + math.erf(z / math.sqrt(2)))
                model_prob_above = max(0.01, min(0.99, 1.0 - corrected_prob_below))
                model_prob_below = 1.0 - model_prob_above
                confidence = abs(model_prob_above - 0.5) * 2
    except Exception:
        pass  # If bias correction fails, proceed with raw forecast probs

    # Trade when ensemble strongly agrees (configurable threshold)
    if confidence < float(settings.min_confidence_threshold):
        print(f"[filter] {ticker}: REJECTED — confidence {confidence:.2f} < {settings.min_confidence_threshold}")
        return None

    # Skip markets too far out — GFS accuracy degrades fast beyond 2 days
    if days_to_expiry > int(settings.max_days_to_expiry):
        print(f"[filter] {ticker}: REJECTED — expiry {days_to_expiry}d > max {settings.max_days_to_expiry}d")
        return None

    # Skip expired markets only — same-day markets are fine for scalping
    # (freshest forecasts, highest volume, best accuracy)
    if days_to_expiry < 0:
        print(f"[filter] {ticker}: REJECTED — expired market")
        return None

    # Skip markets where the forecast mean is too close to the threshold.
    # With scalping strategy (sell at +20%), we can accept tighter gaps since
    # we're not holding to expiry. 2°F buffer for temperature.
    market_type = market.get("market_type", "high_temp")
    forecast_mean = forecast.get("mean_val", 0) or forecast.get("mean_high", 0)
    threshold = market.get("yes_threshold") or market.get("threshold_f", 0)
    gap = abs(forecast_mean - threshold) if forecast_mean and threshold else 99.0
    if forecast_mean and threshold:
        gap = abs(forecast_mean - threshold)
        min_buffer = 0.10 if market_type == "precipitation" else (3.0 if market_type == "low_temp" else 1.0)
        if gap < min_buffer:
            print(f"[filter] {ticker}: REJECTED — buffer {gap:.1f} < {min_buffer} (mean={forecast_mean:.1f}, thresh={threshold})")
            return None

    # NWS cross-check: if NWS forecast disagrees with Open-Meteo by >5°F,
    # the models are uncertain and we should skip. Degrades gracefully
    # (if NWS is unavailable, trade proceeds normally).
    nws_disagreement = 0.0
    try:
        from src.data.nws_forecast import get_nws_forecast, nws_agrees
        from src.config import CITY_CONFIG
        series_ticker = market.get("series_ticker", "")
        city_cfg = CITY_CONFIG.get(series_ticker, {})
        nws_station = city_cfg.get("nws_station")
        if nws_station and market_type in ("high_temp", "low_temp"):
            nws = get_nws_forecast(nws_station, market.get("target_date", ""), market_type)
            agrees, nws_disagreement = nws_agrees(nws, forecast_mean, market_type, max_disagreement_f=5.0)
            if not agrees:
                print(f"[filter] {ticker}: REJECTED — NWS disagrees by {nws_disagreement:.1f}°F")
                return None
    except Exception:
        pass  # NWS unavailable — proceed with Open-Meteo only

    unit = market.get("unit", "°F")
    yes_label, no_label = _direction_labels(market_type)

    yes_ask = market.get("yes_ask")
    no_ask = market.get("no_ask")
    yes_bid = market.get("yes_bid")
    no_bid = market.get("no_bid")

    # Spread-aware pricing: when spread is wide (>4¢), use midpoint instead
    # of ask for our limit order. Gets better fills and preserves more edge.
    # Then add a small fill-improvement bump (1-2¢) to jump the order queue.
    def _smart_price(ask, bid):
        if not ask or ask <= 0:
            return None
        if not bid or bid <= 0:
            base = ask
        else:
            spread_cents = int(ask * 100) - int(bid * 100)
            if spread_cents > 4:
                # Use midpoint rounded up to nearest cent (we're buying)
                mid = (ask + bid) / 2
                base = round(mid * 100 + 0.5) / 100  # ceil to nearest cent
            else:
                base = ask  # Tight spread — just take the ask

        # Fill improvement: bump price 1-2¢ above base to jump the queue.
        # On a winning trade, paying 82¢ vs 80¢ costs $0.26 on 13 contracts
        # but getting filled on all 13 vs 5 earns $1.44 more. Easy math.
        bump = 0.02 if base >= 0.50 else 0.01
        improved = base + bump
        # Never exceed 95¢ — diminishing returns above that
        return min(improved, 0.95)

    yes_entry = _smart_price(yes_ask, yes_bid)
    no_entry = _smart_price(no_ask, no_bid)

    # Use the actual YES direction and threshold from Kalshi's subtitle
    # e.g. "62° or above" -> yes_means_above=True, yes_threshold=62
    # e.g. "53° or below" -> yes_means_above=False, yes_threshold=53
    yes_means_above = market.get("yes_means_above", True)
    yes_threshold = market.get("yes_threshold") or market.get("threshold_f")

    # Model prob that YES wins = prob_above if YES=above, prob_below if YES=below
    model_prob_yes = model_prob_above if yes_means_above else model_prob_below
    model_prob_no = 1.0 - model_prob_yes

    # Direction labels based on actual contract direction
    yes_direction = "ABOVE" if yes_means_above else "BELOW"
    no_direction = "BELOW" if yes_means_above else "ABOVE"

    signals = []

    def _build_signal(side, direction, model_prob, price):
        # Minimum win probability floor: never buy contracts where our model
        # says we only have a 10-20% chance of winning. Even with "edge" on
        # paper, the variance is extreme — you lose 80-90% of these trades
        # and the tiny expected gain doesn't cover fees + bad luck streaks.
        if model_prob < 0.30:
            print(f"[filter] {ticker}/{side}: REJECTED — model_prob {model_prob:.2f} < 0.30")
            return None

        # Momentum confirmation: skip if price is moving against us by >3¢.
        # For YES buyers, rising price is good (market agrees with us).
        # For NO buyers, falling price is good (YES getting cheaper = NO getting pricier).
        momentum = get_momentum(market.get("ticker", ""), side)
        if momentum is not None:
            adverse = momentum < -0.03 if side == "yes" else momentum > 0.03
            if adverse:
                print(f"[filter] {ticker}/{side}: REJECTED — adverse momentum {momentum:+.2f}")
                return None

        # Only buy contracts up to max_contract_price for reasonable risk/reward
        if price > settings.max_contract_price:
            print(f"[filter] {ticker}/{side}: REJECTED — price {price:.2f} > max {settings.max_contract_price}")
            return None

        # Skip cheap contracts — at 3-4¢ the bid/ask spread alone can be 60%+ of
        # the price, making it impossible to scalp. Hard floor of 8¢ ensures we only
        # trade contracts with enough liquidity to sell back at a small profit.
        edge = model_prob - price
        min_price = settings.min_contract_price_high_edge if edge >= settings.high_edge_price_threshold else settings.min_contract_price
        min_price = max(min_price, 0.08)
        if price < min_price:
            print(f"[filter] {ticker}/{side}: REJECTED — price {int(price*100)}¢ < min {int(min_price*100)}¢")
            return None

        size = compute_position_size(model_prob, price, bankroll, days_to_expiry)
        if size <= 0:
            return None

        raw_contracts = max(1, int(size / price))

        # Cap contracts at 50% of historical volume — avoids trying to fill
        # orders the market can't absorb (e.g. 15,000 contracts on 10,000 volume).
        # Worst case: smaller fill, less $ deployed, but trade still valid.
        market_volume = float(market.get("volume", 0) or 0)
        if market_volume > 0:
            contracts = min(raw_contracts, max(1, int(market_volume * 0.5)))
        else:
            contracts = raw_contracts

        # Tight-gap low-temp trades: reduce to 25% size when forecast-threshold
        # gap is < 4°F. Low-temp forecasts have ~3° error margin, so tight gaps
        # are essentially coin flips. Still trade them, just smaller.
        if market_type == "low_temp" and gap < 4.0:
            reduced = max(1, int(contracts * 0.25))
            print(f"[sizing] {ticker}/{side}: tight low-temp gap {gap:.1f}°F — reducing {contracts}→{reduced} contracts")
            contracts = reduced

        # Recalculate actual position size based on capped contracts
        actual_size = round(contracts * price, 2)

        # Re-check liquidity with the actual contract count
        if not _is_liquid(market, required_contracts=contracts):
            return None

        return {
            "ticker": market["ticker"],
            "city": market["city"],
            "target_date": market["target_date"],
            "threshold_f": yes_threshold,
            "yes_means_above": yes_means_above,
            "market_type": market_type,
            "unit": unit,
            "side": side,
            "direction": direction,
            "model_prob": round(model_prob, 4),
            "market_price": price,
            "edge": round(calculate_edge(model_prob, price), 4),
            "confidence": round(confidence, 4),
            "position_size_usd": actual_size,
            "contracts": contracts,
            "price_cents": int(price * 100),
            "days_to_expiry": days_to_expiry,
            "forecast_mean": round(forecast["mean_high"], 1),
            "forecast_min": round(forecast["min_high"], 1),
            "forecast_max": round(forecast["max_high"], 1),
            "n_members": forecast["n_members"],
            "n_above": forecast["n_above"],
            "nws_disagreement": nws_disagreement,
        }

    # Log that this market passed all pre-signal filters
    print(f"[filter] {ticker}: PASSED pre-filters (conf={confidence:.2f}, expiry={days_to_expiry}d, gap={gap:.1f}°F, yes_entry={yes_entry}, no_entry={no_entry}, prob_yes={model_prob_yes:.2f}, prob_no={model_prob_no:.2f})")

    # Check YES side — use spread-aware entry price for better fills
    if yes_entry and yes_entry > 0:
        edge_yes = calculate_edge(model_prob_yes, yes_entry)
        if edge_yes >= settings.min_edge_threshold:
            sig = _build_signal("yes", yes_direction, model_prob_yes, yes_entry)
            if sig:
                signals.append(sig)
        else:
            print(f"[filter] {ticker}/yes: REJECTED — edge {edge_yes:.3f} < min {settings.min_edge_threshold}")

    # Check NO side — use spread-aware entry price
    if no_entry and no_entry > 0:
        edge_no = calculate_edge(model_prob_no, no_entry)
        if edge_no >= settings.min_edge_threshold:
            sig = _build_signal("no", no_direction, model_prob_no, no_entry)
            if sig:
                signals.append(sig)
        else:
            print(f"[filter] {ticker}/no: REJECTED — edge {edge_no:.3f} < min {settings.min_edge_threshold}")

    # Only take the single best signal per market — never trade both sides
    if len(signals) > 1:
        signals = [max(signals, key=lambda s: s["edge"])]

    if not signals:
        return None

    return max(signals, key=lambda s: s["edge"])
