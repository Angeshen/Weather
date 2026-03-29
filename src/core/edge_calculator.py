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


# Liquidity thresholds — skip illiquid markets that are hard to fill at fair price
_MIN_VOLUME = 50          # Minimum total contracts traded (weeds out ghost markets)
_MAX_SPREAD_CENTS = 15    # Max bid-ask spread in cents (15¢ = very wide, skip)


def _is_liquid(market: dict) -> bool:
    """
    Check if a market has enough liquidity to trade safely.
    Wide spreads mean we'd pay too much slippage; low volume means poor fills.
    """
    volume = market.get("volume", 0) or 0
    yes_bid = market.get("yes_bid") or 0
    yes_ask = market.get("yes_ask") or 0
    no_bid = market.get("no_bid") or 0
    no_ask = market.get("no_ask") or 0

    if volume < _MIN_VOLUME:
        return False

    # Check spread on whichever side has quotes
    if yes_bid and yes_ask:
        spread = int(yes_ask * 100) - int(yes_bid * 100)
        if spread > _MAX_SPREAD_CENTS:
            return False
    if no_bid and no_ask:
        spread = int(no_ask * 100) - int(no_bid * 100)
        if spread > _MAX_SPREAD_CENTS:
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

    # Trade when ensemble strongly agrees
    # 0.65 confidence = 82.5%+ of members on one side
    if confidence < 0.65:
        return None
    market_type = market.get("market_type", "high_temp")
    unit = market.get("unit", "°F")
    yes_label, no_label = _direction_labels(market_type)

    yes_ask = market.get("yes_ask")
    no_ask = market.get("no_ask")

    signals = []

    def _build_signal(side, direction, model_prob, price):
        # Only buy contracts up to 65¢ for reasonable risk/reward
        # At 30¢ you risk $30 to win $70. At 65¢ you risk $65 to win $35.
        if price > 0.65:
            return None

        # Skip very cheap contracts (< 5¢) — usually near-expiry noise
        if price < 0.05:
            return None

        size = compute_position_size(model_prob, price, bankroll, days_to_expiry)
        if size <= 0:
            return None
        return {
            "ticker": market["ticker"],
            "city": market["city"],
            "target_date": market["target_date"],
            "threshold_f": market["threshold_f"],
            "market_type": market_type,
            "unit": unit,
            "side": side,
            "direction": direction,
            "model_prob": round(model_prob, 4),
            "market_price": price,
            "edge": round(calculate_edge(model_prob, price), 4),
            "confidence": round(confidence, 4),
            "position_size_usd": size,
            "contracts": max(1, int(size / price)),
            "price_cents": int(price * 100),
            "days_to_expiry": days_to_expiry,
            "forecast_mean": round(forecast["mean_high"], 1),
            "forecast_min": round(forecast["min_high"], 1),
            "forecast_max": round(forecast["max_high"], 1),
            "n_members": forecast["n_members"],
            "n_above": forecast["n_above"],
        }

    # Check YES side
    if yes_ask and yes_ask > 0:
        edge_yes = calculate_edge(model_prob_above, yes_ask)
        if edge_yes >= settings.min_edge_threshold:
            sig = _build_signal("yes", yes_label, model_prob_above, yes_ask)
            if sig:
                signals.append(sig)

    # Check NO side
    if no_ask and no_ask > 0:
        edge_no = calculate_edge(model_prob_below, no_ask)
        if edge_no >= settings.min_edge_threshold:
            sig = _build_signal("no", no_label, model_prob_below, no_ask)
            if sig:
                signals.append(sig)

    if not signals:
        return None

    return max(signals, key=lambda s: s["edge"])
