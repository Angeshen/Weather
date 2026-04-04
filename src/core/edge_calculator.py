"""
Calculate edge between model probability and market price.
Determine trade signals and Kelly criterion position sizing.
"""

from src.config import settings


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
    Compute dollar position size using fractional Kelly, scaled by days to expiry.

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

    net_odds = (1.0 - market_price) / market_price

    raw_kelly = kelly_size(win_prob, net_odds)
    fractional = raw_kelly * settings.kelly_fraction

    position = fractional * bankroll

    # Scale down position for near-expiry markets where ensemble has less predictive value:
    # 7+ days: full size | 3-6 days: 75% | 1-2 days: 50% | same day: 25%
    if days_to_expiry <= 0:
        expiry_scale = 0.25
    elif days_to_expiry <= 2:
        expiry_scale = 0.50
    elif days_to_expiry <= 6:
        expiry_scale = 0.75
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
    try:
        target = _date.fromisoformat(market.get("target_date", ""))
        days_to_expiry = (target - _date.today()).days
    except (ValueError, TypeError):
        days_to_expiry = 7
    # Skip illiquid markets — wide spreads eat our edge, thin volume means bad fills
    if not _is_liquid(market):
        return None

    n_members = forecast.get("n_members", 0)
    if n_members < 40:
        return None  # Need substantial ensemble for reliable probability

    model_prob_above = forecast["prob_above"]
    model_prob_below = forecast["prob_below"]
    confidence = forecast["confidence"]

    # Apply bias correction: if model historically runs warm/cold for this city,
    # shift the effective threshold before computing probabilities.
    # e.g. if model has +2°F warm bias, treat threshold as 2°F higher (harder to exceed).
    try:
        from src.core.trade_executor import get_city_bias
        city = market.get("city", "")
        bias = get_city_bias(city)  # positive = model runs warm
        if bias != 0.0:
            threshold = market.get("threshold_f", 0)
            n_above = forecast.get("n_above", 0)
            n_members = forecast.get("n_members", 1)
            # Adjust mean — shift all members by -bias to correct for warm/cold bias
            # This effectively raises/lowers the bar for prob_above
            corrected_mean = forecast.get("mean_high", threshold) - bias
            shift = corrected_mean - forecast.get("mean_high", corrected_mean)
            # Approximate corrected prob by shifting threshold instead of resampling
            import scipy.stats as _stats
            std = (forecast.get("max_high", corrected_mean) - forecast.get("min_high", corrected_mean)) / 4 or 2.5
            model_prob_above = float(1 - _stats.norm.cdf(threshold, loc=corrected_mean, scale=std))
            model_prob_above = max(0.01, min(0.99, model_prob_above))
            model_prob_below = 1.0 - model_prob_above
    except Exception:
        pass  # If bias correction fails, proceed with raw forecast probs

    # Trade when ensemble strongly agrees (configurable threshold)
    if confidence < float(settings.min_confidence_threshold):
        return None

    # Skip markets too far out — GFS accuracy degrades fast beyond 2 days
    if days_to_expiry > int(settings.max_days_to_expiry):
        return None

    # Skip same-day markets — forecast is stale by expiry day, market has already priced in current conditions
    if days_to_expiry <= 0:
        return None
    market_type = market.get("market_type", "high_temp")
    unit = market.get("unit", "°F")
    yes_label, no_label = _direction_labels(market_type)

    yes_ask = market.get("yes_ask")
    no_ask = market.get("no_ask")

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
        # Only buy contracts up to max_contract_price for reasonable risk/reward
        if price > settings.max_contract_price:
            return None

        # Skip very cheap contracts — usually near-expiry noise.
        # Exception: allow down to min_contract_price_high_edge when edge is strong.
        # Hard floor of 3¢ regardless of edge — a 1-2¢ contract means market is 98-99% confident
        # the other side wins; our NWP model should not override that level of market consensus.
        edge = model_prob - price
        min_price = settings.min_contract_price_high_edge if edge >= settings.high_edge_price_threshold else settings.min_contract_price
        min_price = max(min_price, 0.03)
        if price < min_price:
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
        }

    # Check YES side — model_prob_yes is prob that YES wins given actual contract direction
    if yes_ask and yes_ask > 0:
        edge_yes = calculate_edge(model_prob_yes, yes_ask)
        if edge_yes >= settings.min_edge_threshold:
            sig = _build_signal("yes", yes_direction, model_prob_yes, yes_ask)
            if sig:
                signals.append(sig)

    # Check NO side
    if no_ask and no_ask > 0:
        edge_no = calculate_edge(model_prob_no, no_ask)
        if edge_no >= settings.min_edge_threshold:
            sig = _build_signal("no", no_direction, model_prob_no, no_ask)
            if sig:
                signals.append(sig)

    # Only take the single best signal per market — never trade both sides
    if len(signals) > 1:
        signals = [max(signals, key=lambda s: s["edge"])]

    if not signals:
        return None

    return max(signals, key=lambda s: s["edge"])
