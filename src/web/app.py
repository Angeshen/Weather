"""
Flask web dashboard for the Kalshi Weather Trading Bot.
Provides a GUI to monitor, scan, and control the bot.
"""

import threading
import time
from datetime import datetime, timezone

from flask import Flask, jsonify, render_template, request

from src.config import settings
from src.data.kalshi_client import KalshiClient
from src.data.market_scanner import scan_weather_markets, scan_weather_markets_public
from src.data.weather import get_forecast_for_city
from src.core.edge_calculator import evaluate_market
from src.core.trade_executor import (
    execute_trade,
    get_current_bankroll,
    get_trade_history,
    get_stats,
    get_bankroll_history,
    get_settled_trades,
    update_trade_note,
    log_bankroll,
    init_db,
    get_open_trade_count,
    get_win_rate_by_city,
    get_open_trades_with_current_prices,
)
from src.core.notifications import (
    notify_bot_status,
    notify_daily_summary,
    notify_scan_summary,
    notify_settlement,
    notify_risk_alert,
    test_notification,
)
from src.core.settlement import settle_open_trades
from src.core.backtest import run_backtest, get_backtest_progress

app = Flask(
    __name__,
    template_folder="templates",
    static_folder="static",
)

# Bot state
bot_state = {
    "running": False,
    "thread": None,
    "last_scan": None,
    "last_signals": [],
    "last_markets": [],
    "scan_count": 0,
}


def run_scan():
    """Run a single scan and return signals."""
    client = None
    if settings.trading_mode == "live":
        try:
            client = KalshiClient()
        except Exception:
            pass

    try:
        if settings.trading_mode == "live" and client:
            markets = scan_weather_markets(client)
        else:
            markets = scan_weather_markets_public()
    except Exception as e:
        return [], [], str(e)

    bankroll = get_current_bankroll()
    signals = []

    for market in markets:
        try:
            forecast = get_forecast_for_city(
                series_ticker=market["series_ticker"],
                target_date=market["target_date"],
                threshold=market["threshold_f"],
            )
            if forecast.get("error"):
                continue

            signal = evaluate_market(market, forecast, bankroll)
            if signal:
                signals.append(signal)
        except Exception:
            continue

    signals.sort(key=lambda s: s["edge"], reverse=True)

    if client:
        client.close()

    # Strip raw_market (not JSON serializable)
    clean_markets = []
    for m in markets:
        cm = {k: v for k, v in m.items() if k != "raw_market"}
        clean_markets.append(cm)

    return signals, clean_markets, None


def bot_loop():
    """Background bot loop."""
    init_db()
    log_bankroll(get_current_bankroll(), "Bot started via GUI")

    client = None
    if settings.trading_mode == "live":
        try:
            client = KalshiClient()
        except Exception:
            pass

    while bot_state["running"]:
        try:
            if settings.trading_mode == "live" and client:
                markets = scan_weather_markets(client)
            else:
                markets = scan_weather_markets_public()

            bankroll = get_current_bankroll()
            signals = []

            for market in markets:
                try:
                    forecast = get_forecast_for_city(
                        series_ticker=market["series_ticker"],
                        target_date=market["target_date"],
                        threshold=market["threshold_f"],
                    )
                    if forecast.get("error"):
                        continue
                    signal = evaluate_market(market, forecast, bankroll)
                    if signal:
                        signals.append(signal)
                except Exception:
                    continue

            signals.sort(key=lambda s: s["edge"], reverse=True)

            # Execute trades
            executed = 0
            for signal in signals:
                if executed >= settings.max_concurrent_trades:
                    break
                result = execute_trade(signal, client)
                if result.get("status") not in ("blocked", "failed"):
                    executed += 1

            # Check for settlements every scan
            try:
                settle_results = settle_open_trades()
                if settle_results.get("settled", 0) > 0:
                    notify_settlement(settle_results)
            except Exception:
                pass

            # Stale signal detection — warn if market price moved 10%+ against open position
            try:
                price_lookup = {m["ticker"]: m for m in markets}
                open_trades = get_open_trades_with_current_prices(
                    [{k: v for k, v in m.items() if k != "raw_market"} for m in markets]
                )
                for ot in open_trades:
                    if ot.get("unrealized_pnl") is None:
                        continue
                    entry = ot.get("market_price", 0)
                    if not entry:
                        continue
                    drift = abs((ot.get("unrealized_pnl", 0)) / (entry * ot.get("contracts", 1) + 0.001))
                    if drift >= 0.10:
                        notify_risk_alert(
                            f"Position drift alert: {ot['ticker']}\n"
                            f"Entry: {entry*100:.0f}¢  |  Unrealized P&L: ${ot['unrealized_pnl']:+.2f} ({drift*100:.0f}% move)"
                        )
            except Exception:
                pass

            # Update state
            clean_markets = [{k: v for k, v in m.items() if k != "raw_market"} for m in markets]
            bot_state["last_scan"] = datetime.now(timezone.utc).isoformat()
            bot_state["last_signals"] = signals
            bot_state["last_markets"] = clean_markets
            bot_state["scan_count"] += 1

        except Exception:
            pass

        # Sleep in small increments so we can stop quickly
        for _ in range(settings.scan_interval_seconds):
            if not bot_state["running"]:
                break
            time.sleep(1)

    if client:
        client.close()


# --- Routes ---

@app.route("/")
def index():
    return render_template("dashboard.html")


@app.route("/api/status")
def api_status():
    return jsonify({
        "running": bot_state["running"],
        "mode": settings.trading_mode,
        "last_scan": bot_state["last_scan"],
        "scan_count": bot_state["scan_count"],
        "scan_interval": settings.scan_interval_seconds,
    })


@app.route("/api/stats")
def api_stats():
    return jsonify(get_stats())


@app.route("/api/trades")
def api_trades():
    limit = request.args.get("limit", 50, type=int)
    return jsonify(get_trade_history(limit))


@app.route("/api/scan", methods=["POST"])
def api_scan():
    """Run a manual scan."""
    signals, markets, error = run_scan()
    bot_state["last_scan"] = datetime.now(timezone.utc).isoformat()
    bot_state["last_signals"] = signals
    bot_state["last_markets"] = markets
    bot_state["scan_count"] += 1
    return jsonify({
        "signals": signals,
        "markets_count": len(markets),
        "error": error,
    })


@app.route("/api/signals")
def api_signals():
    return jsonify(bot_state["last_signals"])


@app.route("/api/markets")
def api_markets():
    return jsonify(bot_state["last_markets"])


@app.route("/api/bot/start", methods=["POST"])
def api_bot_start():
    if bot_state["running"]:
        return jsonify({"status": "already_running"})

    bot_state["running"] = True
    bot_state["thread"] = threading.Thread(target=bot_loop, daemon=True)
    bot_state["thread"].start()
    notify_bot_status("started")
    return jsonify({"status": "started"})


@app.route("/api/bot/stop", methods=["POST"])
def api_bot_stop():
    bot_state["running"] = False
    notify_bot_status("stopped")
    return jsonify({"status": "stopped"})


@app.route("/api/balance")
def api_balance():
    """Return Kalshi account balance (live mode only)."""
    if settings.trading_mode != "live":
        return jsonify({"balance": None, "mode": "paper"})
    try:
        client = KalshiClient()
        result = client.get_balance()
        client.close()
        # Balance is returned as cents or dollars depending on API version
        raw = result.get("balance", result.get("portfolio_value", 0))
        balance = float(raw) / 100.0 if isinstance(raw, int) and raw > 1000 else float(raw)
        return jsonify({"balance": round(balance, 2), "mode": "live"})
    except Exception as e:
        return jsonify({"balance": None, "mode": "live", "error": str(e)})


@app.route("/api/config")
def api_config():
    return jsonify({
        "trading_mode": settings.trading_mode,
        "initial_bankroll": settings.initial_bankroll,
        "max_trade_size": settings.max_trade_size,
        "daily_loss_limit": settings.daily_loss_limit,
        "max_concurrent_trades": settings.max_concurrent_trades,
        "min_edge_threshold": settings.min_edge_threshold,
        "kelly_fraction": settings.kelly_fraction,
        "scan_interval_seconds": settings.scan_interval_seconds,
        "weather_series": settings.weather_series,
    })


@app.route("/api/mode", methods=["POST"])
def api_set_mode():
    """Switch between paper and live trading mode."""
    data = request.get_json() or {}
    new_mode = data.get("mode", "").lower()
    if new_mode not in ("paper", "live"):
        return jsonify({"error": "Mode must be 'paper' or 'live'"}), 400

    settings.trading_mode = new_mode
    _save_setting_to_env("TRADING_MODE", new_mode)
    return jsonify({"trading_mode": settings.trading_mode})


@app.route("/api/settings", methods=["POST"])
def api_save_settings():
    """Save settings to .env and apply them in memory."""
    data = request.get_json() or {}
    updated = []

    # Map of JSON keys to (settings attr, env key, type)
    editable = {
        "trading_mode": ("trading_mode", "TRADING_MODE", str),
        "min_edge_threshold": ("min_edge_threshold", "MIN_EDGE_THRESHOLD", float),
        "max_trade_size": ("max_trade_size", "MAX_TRADE_SIZE", float),
        "daily_loss_limit": ("daily_loss_limit", "DAILY_LOSS_LIMIT", float),
        "kelly_fraction": ("kelly_fraction", "KELLY_FRACTION", float),
        "scan_interval_seconds": ("scan_interval_seconds", "SCAN_INTERVAL_SECONDS", int),
        "initial_bankroll": ("initial_bankroll", "INITIAL_BANKROLL", float),
        "max_concurrent_trades": ("max_concurrent_trades", "MAX_CONCURRENT_TRADES", int),
    }

    for key, (attr, env_key, cast) in editable.items():
        if key in data:
            try:
                val = cast(data[key])
                setattr(settings, attr, val)
                _save_setting_to_env(env_key, str(val))
                updated.append(key)
            except (ValueError, TypeError) as e:
                return jsonify({"error": f"Invalid value for {key}: {e}"}), 400

    return jsonify({"updated": updated, "count": len(updated)})


def _save_setting_to_env(key: str, value: str):
    """Update a single key in the .env file on disk."""
    from pathlib import Path
    env_path = Path(__file__).parent.parent.parent / ".env"
    if not env_path.exists():
        return

    lines = env_path.read_text().splitlines()
    found = False
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            found = True
            break
    if not found:
        lines.append(f"{key}={value}")

    env_path.write_text("\n".join(lines) + "\n")


@app.route("/api/equity")
def api_equity():
    """Get equity curve data."""
    bankroll = get_bankroll_history(200)
    settled = get_settled_trades()
    return jsonify({"bankroll_history": bankroll, "settled_trades": settled})


@app.route("/api/backtest", methods=["POST"])
def api_backtest():
    """Run a historical backtest."""
    data = request.get_json() or {}
    days = data.get("days", 30)
    days = min(max(int(days), 7), 90)
    results = run_backtest(days)
    return jsonify(results)


@app.route("/api/backtest/progress")
def api_backtest_progress():
    """Get live backtest progress."""
    return jsonify(get_backtest_progress())


@app.route("/api/trade/note", methods=["POST"])
def api_trade_note():
    """Save a note on a trade."""
    data = request.get_json() or {}
    trade_id = data.get("trade_id")
    note = data.get("note", "")
    if not trade_id:
        return jsonify({"error": "trade_id required"}), 400
    update_trade_note(int(trade_id), note)
    return jsonify({"status": "saved"})


@app.route("/api/city-stats")
def api_city_stats():
    """Win rate and P&L broken down by city."""
    return jsonify(get_win_rate_by_city())


@app.route("/api/open-trades")
def api_open_trades():
    """Open trades with unrealized P&L based on last scan prices."""
    trades = get_open_trades_with_current_prices(bot_state.get("last_markets", []))
    return jsonify(trades)


@app.route("/api/debug/forecast")
def api_debug_forecast():
    """Show forecast + filter results for first 5 markets to diagnose why no signals fire."""
    _, markets, _ = run_scan()
    bankroll = get_current_bankroll()
    results = []
    for market in markets[:5]:
        try:
            forecast = get_forecast_for_city(
                series_ticker=market["series_ticker"],
                target_date=market["target_date"],
                threshold=market["threshold_f"],
            )
            signal = evaluate_market(market, forecast, bankroll)
            results.append({
                "ticker": market["ticker"],
                "yes_ask": market.get("yes_ask"),
                "no_ask": market.get("no_ask"),
                "n_members": forecast.get("n_members"),
                "confidence": round(forecast.get("confidence", 0), 4),
                "prob_above": round(forecast.get("prob_above", 0), 4),
                "prob_below": round(forecast.get("prob_below", 0), 4),
                "mean_val": round(forecast.get("mean_val", 0), 1),
                "signal": signal is not None,
                "error": forecast.get("error"),
            })
        except Exception as e:
            results.append({"ticker": market["ticker"], "error": str(e)})
    return jsonify(results)


@app.route("/api/settle", methods=["POST"])
def api_settle():
    """Manually trigger settlement check."""
    results = settle_open_trades()
    if results.get("settled", 0) > 0:
        notify_settlement(results)
    return jsonify(results)


@app.route("/api/telegram/test", methods=["POST"])
def api_telegram_test():
    """Send a test message to verify Telegram is configured."""
    success = test_notification()
    if success:
        return jsonify({"status": "sent"})
    return jsonify({"error": "Failed — check TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env"}), 400


@app.route("/api/telegram/daily", methods=["POST"])
def api_telegram_daily():
    """Manually trigger a daily summary notification."""
    stats = get_stats()
    notify_daily_summary(stats)
    return jsonify({"status": "sent", "stats": stats})


# Daily summary scheduler thread
def _daily_summary_loop():
    """Send a daily summary at the configured local time (hour:minute)."""
    import time as _time
    last_sent_date = None
    while True:
        now = datetime.now()  # local time
        today = now.strftime("%Y-%m-%d")
        target_hour = settings.telegram_daily_summary_hour
        target_minute = settings.telegram_daily_summary_minute
        if now.hour == target_hour and now.minute == target_minute and last_sent_date != today:
            stats = get_stats()
            notify_daily_summary(stats)
            last_sent_date = today
        _time.sleep(30)


# Start daily summary thread if Telegram is configured
if settings.telegram_bot_token and settings.telegram_chat_id:
    _daily_thread = threading.Thread(target=_daily_summary_loop, daemon=True)
    _daily_thread.start()
