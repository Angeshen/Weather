"""
Flask web dashboard for the Kalshi Weather Trading Bot.
Provides a GUI to monitor, scan, and control the bot.
"""

import sqlite3
import threading
import time
from datetime import datetime, timezone

from flask import Flask, jsonify, render_template, request

from src.config import settings
from src.data.kalshi_client import KalshiClient
from src.data.market_scanner import (
    scan_weather_markets,
    scan_weather_markets_public,
    discover_active_series,
)
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
    exit_losing_positions,
    fetch_open_position_prices,
    reconcile_resting_orders,
)
from src.core.notifications import (
    notify_bot_status,
    notify_daily_summary,
    notify_scan_summary,
    notify_settlement,
    notify_risk_alert,
    notify_blocked_signal,
    notify_morning_ping,
    notify_confidence_spike,
    notify_weekly_summary,
    notify_no_markets,
    notify_settlement_pending,
    check_and_notify_drawdown,
    notify_streak_milestone,
    notify_gfs_model_run,
    notify_big_win,
    check_scan_heartbeat,
    test_notification,
)
from src.core.settlement import settle_open_trades
from src.core.backtest import run_backtest, get_backtest_progress
from src.core.telegram_commands import start_command_listener, is_paused

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
    "confidence_history": {},  # ticker -> list of (timestamp, confidence) tuples
    "last_errors": [],  # recent forecast/scan errors for dashboard display
}


def run_scan():
    """Run a single scan and return signals."""
    client = None
    if settings.trading_mode == "live":
        try:
            client = KalshiClient()
        except Exception:
            pass

    # Cancel any resting (unfilled) orders before scanning
    if settings.trading_mode == "live" and client:
        try:
            reconcile_resting_orders(client)
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

    fetched_at = datetime.now(timezone.utc).isoformat()
    scan_errors = []
    for market in markets:
        try:
            forecast = get_forecast_for_city(
                series_ticker=market["series_ticker"],
                target_date=market["target_date"],
                threshold=market.get("yes_threshold") or market["threshold_f"],
            )
            if forecast.get("error"):
                msg = f"Forecast error {market['ticker']}: {forecast['error']}"
                print(f"[forecast] {msg}")
                scan_errors.append(msg)
                continue
            if forecast.get("n_members", 0) == 0:
                msg = f"No forecast data for {market['ticker']} (API rate limit or outage?)"
                print(f"[forecast] {msg}")
                scan_errors.append(msg)
                continue

            signal = evaluate_market(market, forecast, bankroll)
            if signal:
                signal["forecast_fetched_at"] = fetched_at
                signals.append(signal)
        except Exception as e:
            import traceback
            msg = f"Exception evaluating {market.get('ticker','?')}: {e}"
            print(f"[scan error] {msg}")
            print(traceback.format_exc())
            scan_errors.append(msg)
            continue

    if scan_errors:
        bot_state["last_errors"] = scan_errors
    elif not scan_errors:
        bot_state["last_errors"] = []

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

    # Auto-discover active Kalshi series on startup
    active_series = discover_active_series()
    settings.weather_series = active_series
    last_discovery = time.time()

    client = None
    if settings.trading_mode == "live":
        try:
            client = KalshiClient()
        except Exception:
            pass

    while bot_state["running"]:
        # Re-discover series every 6 hours in case Kalshi adds new markets
        if time.time() - last_discovery > 21600:
            active_series = discover_active_series()
            settings.weather_series = active_series
            last_discovery = time.time()
        try:
            if settings.trading_mode == "live" and client:
                markets = scan_weather_markets(client)
            else:
                markets = scan_weather_markets_public()

            bankroll = get_current_bankroll()
            signals = []
            scan_fetched_at = datetime.now(timezone.utc).isoformat()

            for market in markets:
                try:
                    forecast = get_forecast_for_city(
                        series_ticker=market["series_ticker"],
                        target_date=market["target_date"],
                        threshold=market.get("yes_threshold") or market["threshold_f"],
                    )
                    if forecast.get("error"):
                        continue
                    signal = evaluate_market(market, forecast, bankroll)
                    if signal:
                        signal["forecast_fetched_at"] = scan_fetched_at
                        signals.append(signal)
                except Exception:
                    continue

            signals.sort(key=lambda s: s["edge"], reverse=True)

            # Track confidence trend per ticker (keep last 5 readings)
            now_ts = time.time()
            for sig in signals:
                ticker = sig["ticker"]
                hist = bot_state["confidence_history"].setdefault(ticker, [])
                hist.append((now_ts, sig["confidence"]))
                # Keep only last 5 readings
                bot_state["confidence_history"][ticker] = hist[-5:]
                # Annotate signal with trend direction
                if len(hist) >= 2:
                    delta = hist[-1][1] - hist[-2][1]
                    sig["confidence_trend"] = "rising" if delta > 0.01 else "falling" if delta < -0.01 else "stable"
                else:
                    sig["confidence_trend"] = "new"

            clean_markets = [{k: v for k, v in m.items() if k != "raw_market"} for m in markets]

            # Alert if scanner found no markets at all
            if not markets:
                try:
                    notify_no_markets(bot_state.get("scan_count", 0))
                except Exception:
                    pass

            # Drawdown alert (tracks peak bankroll, fires at 10% drawdown)
            try:
                check_and_notify_drawdown(bankroll)
            except Exception:
                pass

            # GFS model run alert (~00Z and ~12Z UTC)
            try:
                notify_gfs_model_run()
            except Exception:
                pass

            # Morning liveness ping (once per day)
            try:
                notify_morning_ping(len(markets), get_open_trade_count(), bankroll, get_stats())
            except Exception:
                pass

            # Settlement pending alert — trades expiring today
            try:
                from src.core.trade_executor import get_trade_history
                open_trades_list = [t for t in get_trade_history(50) if t.get("status") == "open"]
                notify_settlement_pending(open_trades_list)
            except Exception:
                pass

            # Confidence spike alerts
            try:
                for sig in signals:
                    notify_confidence_spike(sig)
            except Exception:
                pass

            # Execute trades — skip if paused via /pause command
            executed = 0
            if not is_paused():
                for signal in signals:
                    if executed >= settings.max_concurrent_trades:
                        notify_blocked_signal(signal, "Max concurrent trades reached")
                        continue
                    result = execute_trade(signal, client)
                    if result.get("status") not in ("blocked", "failed"):
                        executed += 1
                    elif result.get("status") == "blocked":
                        reason = result.get("reason", "Unknown")
                        if "Already have open" not in reason:
                            notify_blocked_signal(signal, reason)

            # Check for settlements every scan
            try:
                settle_results = settle_open_trades()
                if settle_results.get("settled", 0) > 0:
                    notify_settlement(settle_results)
                    # Big win celebration + streak milestone
                    for r in settle_results.get("results", []):
                        if r.get("pnl", 0) > 0:
                            notify_big_win(r, r["pnl"])
                    try:
                        _stats = get_stats()
                        notify_streak_milestone(_stats.get("win_streak", 0), _stats)
                    except Exception:
                        pass
            except Exception:
                pass

            # Exit positions that have lost threshold% of value
            try:
                exit_losing_positions(clean_markets, client)
            except Exception:
                pass

            # Weekly summary — every Sunday at daily summary time
            try:
                now_dt = datetime.now(timezone.utc)
                if now_dt.weekday() == 6:  # Sunday
                    hour = settings.telegram_daily_summary_hour
                    minute = settings.telegram_daily_summary_minute
                    if now_dt.hour == hour and now_dt.minute == minute:
                        from src.core.trade_executor import get_stats, get_db
                        import sqlite3 as _sq
                        conn = get_db()
                        week_ago = (now_dt.replace(hour=0, minute=0, second=0) - __import__('datetime').timedelta(days=7)).isoformat()
                        row = conn.execute(
                            "SELECT COUNT(*), SUM(CASE WHEN pnl_usd>0 THEN 1 ELSE 0 END), "
                            "SUM(CASE WHEN pnl_usd<=0 THEN 1 ELSE 0 END), COALESCE(SUM(pnl_usd),0) "
                            "FROM trades WHERE status='settled' AND timestamp >= ?", (week_ago,)
                        ).fetchone()
                        conn.close()
                        wk_trades, wk_wins, wk_losses, wk_pnl = row or (0, 0, 0, 0)
                        base_stats = get_stats()
                        notify_weekly_summary({
                            **base_stats,
                            "week_trades": wk_trades or 0,
                            "week_wins": wk_wins or 0,
                            "week_losses": wk_losses or 0,
                            "week_pnl": wk_pnl or 0,
                        })
            except Exception:
                pass

            # Update state
            bot_state["last_scan"] = datetime.now(timezone.utc).isoformat()
            bot_state["last_signals"] = signals
            bot_state["last_markets"] = clean_markets
            bot_state["scan_count"] += 1

        except Exception:
            pass

        # Heartbeat check — runs every loop iteration even if scan fails
        try:
            check_scan_heartbeat(bot_state.get("last_scan", ""))
        except Exception:
            pass

        # GFS model runs complete ~00Z and ~12Z UTC (midnight + noon UTC).
        # Scan more aggressively for 90 min after each run — freshest ensemble data.
        now_utc = datetime.now(timezone.utc)
        hour_utc = now_utc.hour
        minute_utc = now_utc.minute
        minutes_since_00z = hour_utc * 60 + minute_utc
        minutes_since_12z = abs(hour_utc - 12) * 60 + minute_utc
        in_model_window = minutes_since_00z <= 90 or minutes_since_12z <= 90
        sleep_secs = max(60, settings.scan_interval_seconds // 2) if in_model_window else settings.scan_interval_seconds

        # Sleep between scans, but check exits every 60s using live Kalshi prices
        elapsed = 0
        for _ in range(sleep_secs):
            if not bot_state["running"]:
                break
            time.sleep(1)
            elapsed += 1
            # Fast exit monitor — fetch fresh prices for open positions every 30s
            # Frequent checks are critical for scalping small 20% gains
            if elapsed % 30 == 0 and settings.trading_mode == "live" and client:
                try:
                    fresh_markets = fetch_open_position_prices(client)
                    if fresh_markets:
                        exit_losing_positions(fresh_markets, client)
                except Exception:
                    pass

    if client:
        client.close()


# --- Routes ---

@app.route("/")
def index():
    return render_template("dashboard.html")


@app.route("/api/status")
def api_status():
    now_utc = datetime.now(timezone.utc)
    h, m = now_utc.hour, now_utc.minute
    mins_total = h * 60 + m
    mins_since_00z = mins_total
    mins_since_12z = abs(h - 12) * 60 + m
    in_model_window = mins_since_00z <= 90 or mins_since_12z <= 90
    # Minutes until next model run window (00Z or 12Z)
    next_00z = (24 * 60) - mins_total
    next_12z = (12 * 60) - mins_total if mins_total < 12 * 60 else (24 * 60) - mins_total + (12 * 60)
    next_window_mins = min(next_00z, next_12z) if not in_model_window else 0
    return jsonify({
        "running": bot_state["running"],
        "is_paused": is_paused(),
        "mode": settings.trading_mode,
        "last_scan": bot_state["last_scan"],
        "scan_count": bot_state["scan_count"],
        "scan_interval": settings.scan_interval_seconds,
        "in_model_window": in_model_window,
        "next_model_run_mins": int(next_window_mins),
        "last_errors": bot_state.get("last_errors", []),
    })


@app.route("/api/stats")
def api_stats():
    return jsonify(get_stats())


@app.route("/api/daily-pnl")
def api_daily_pnl():
    """Return daily P&L history for bar chart (last 30 days)."""
    from src.core.trade_executor import get_db
    conn = get_db()
    rows = conn.execute(
        "SELECT date, total_pnl, trades_count, wins, losses "
        "FROM daily_pnl ORDER BY date DESC LIMIT 30"
    ).fetchall()
    conn.close()
    return jsonify([
        {"date": r[0], "total_pnl": round(r[1], 2), "trades": r[2], "wins": r[3], "losses": r[4]}
        for r in reversed(rows)
    ])


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
    notify_bot_status("started", bankroll=get_current_bankroll())
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
        "max_trades_per_city": settings.max_trades_per_city,
        "min_edge_threshold": settings.min_edge_threshold,
        "kelly_fraction": settings.kelly_fraction,
        "scan_interval_seconds": settings.scan_interval_seconds,
        "weather_series": settings.weather_series,
        "min_contract_price": settings.min_contract_price,
        "min_contract_price_high_edge": settings.min_contract_price_high_edge,
        "high_edge_price_threshold": settings.high_edge_price_threshold,
        "max_contract_price": settings.max_contract_price,
        "max_spread_cents": settings.max_spread_cents,
        "min_liquidity_volume": settings.min_liquidity_volume,
        "exit_loss_threshold": settings.exit_loss_threshold,
        "min_confidence_threshold": settings.min_confidence_threshold,
        "max_days_to_expiry": settings.max_days_to_expiry,
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
        "max_trades_per_city": ("max_trades_per_city", "MAX_TRADES_PER_CITY", int),
        "min_contract_price": ("min_contract_price", "MIN_CONTRACT_PRICE", float),
        "min_contract_price_high_edge": ("min_contract_price_high_edge", "MIN_CONTRACT_PRICE_HIGH_EDGE", float),
        "high_edge_price_threshold": ("high_edge_price_threshold", "HIGH_EDGE_PRICE_THRESHOLD", float),
        "max_contract_price": ("max_contract_price", "MAX_CONTRACT_PRICE", float),
        "max_spread_cents": ("max_spread_cents", "MAX_SPREAD_CENTS", int),
        "min_liquidity_volume": ("min_liquidity_volume", "MIN_LIQUIDITY_VOLUME", int),
        "exit_loss_threshold": ("exit_loss_threshold", "EXIT_LOSS_THRESHOLD", float),
        "min_confidence_threshold": ("min_confidence_threshold", "MIN_CONFIDENCE_THRESHOLD", float),
        "max_days_to_expiry": ("max_days_to_expiry", "MAX_DAYS_TO_EXPIRY", int),
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

    # If initial_bankroll was changed, sync it to bankroll_log so get_current_bankroll() reflects it immediately
    if "initial_bankroll" in data:
        try:
            new_br = float(data["initial_bankroll"])
            current_br = get_current_bankroll()
            if abs(new_br - current_br) > 1.0:
                log_bankroll(new_br, f"Bankroll updated via dashboard to ${new_br:.2f}")
        except Exception:
            pass

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


@app.route("/api/performance")
def api_performance():
    """Detailed performance analytics for the Performance dashboard tab."""
    from src.core.trade_executor import get_db
    conn = get_db()
    conn.row_factory = sqlite3.Row

    # All settled trades
    trades = [dict(r) for r in conn.execute(
        "SELECT * FROM trades WHERE status='settled' ORDER BY id ASC"
    ).fetchall()]

    # By market type (high vs low)
    by_type = {}
    for t in trades:
        ticker = t.get("ticker", "")
        mtype = "Low Temp" if "LOWT" in ticker else "High Temp"
        if mtype not in by_type:
            by_type[mtype] = {"total": 0, "wins": 0, "pnl": 0.0}
        by_type[mtype]["total"] += 1
        if (t.get("pnl_usd") or 0) > 0:
            by_type[mtype]["wins"] += 1
        by_type[mtype]["pnl"] = round(by_type[mtype]["pnl"] + (t.get("pnl_usd") or 0), 2)

    type_stats = []
    for name, data in by_type.items():
        type_stats.append({
            "type": name, "total": data["total"], "wins": data["wins"],
            "losses": data["total"] - data["wins"],
            "win_rate": round(data["wins"] / data["total"] * 100, 1) if data["total"] else 0,
            "pnl": data["pnl"],
        })

    # Forecast error analysis
    forecast_errors = []
    for t in trades:
        fm = t.get("forecast_mean")
        at = t.get("actual_temp")
        if fm is not None and at is not None:
            error = round(fm - at, 1)
            forecast_errors.append({
                "id": t["id"], "city": t.get("city", "?"), "ticker": t.get("ticker", ""),
                "forecast": fm, "actual": at, "error": error,
                "abs_error": abs(error), "pnl": t.get("pnl_usd", 0),
                "won": (t.get("pnl_usd") or 0) > 0,
                "side": t.get("side", ""), "direction": t.get("direction", ""),
                "threshold": t.get("threshold_f"),
                "gap": round(abs(fm - (t.get("threshold_f") or fm)), 1),
            })

    avg_error = round(sum(e["abs_error"] for e in forecast_errors) / len(forecast_errors), 1) if forecast_errors else 0
    avg_error_wins = [e for e in forecast_errors if e["won"]]
    avg_error_losses = [e for e in forecast_errors if not e["won"]]
    avg_err_w = round(sum(e["abs_error"] for e in avg_error_wins) / len(avg_error_wins), 1) if avg_error_wins else 0
    avg_err_l = round(sum(e["abs_error"] for e in avg_error_losses) / len(avg_error_losses), 1) if avg_error_losses else 0

    # Cumulative P&L series
    cum_pnl = []
    running = 0.0
    for t in trades:
        running = round(running + (t.get("pnl_usd") or 0), 2)
        cum_pnl.append({"id": t["id"], "city": t.get("city", "?"), "pnl": running})

    # Edge vs outcome
    edge_analysis = []
    for t in trades:
        edge_analysis.append({
            "id": t["id"], "edge": t.get("edge", 0), "pnl": t.get("pnl_usd", 0),
            "won": (t.get("pnl_usd") or 0) > 0, "size": t.get("position_size_usd", 0),
        })

    conn.close()
    return jsonify({
        "type_stats": type_stats,
        "forecast_errors": forecast_errors,
        "avg_error": avg_error,
        "avg_error_wins": avg_err_w,
        "avg_error_losses": avg_err_l,
        "cumulative_pnl": cum_pnl,
        "edge_analysis": edge_analysis,
        "total_trades": len(trades),
    })


@app.route("/api/open-trades")
def api_open_trades():
    """Open trades with unrealized P&L based on last scan prices."""
    client = None
    if settings.trading_mode == "live":
        try:
            client = KalshiClient()
        except Exception:
            pass
    trades = get_open_trades_with_current_prices(bot_state.get("last_markets", []), client=client)
    if client:
        client.close()
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
                threshold=market.get("yes_threshold") or market["threshold_f"],
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
            import traceback
            results.append({"ticker": market["ticker"], "error": str(e), "traceback": traceback.format_exc()})
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
    prev_bankroll = None

    while True:
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        target_hour = settings.telegram_daily_summary_hour
        target_minute = settings.telegram_daily_summary_minute
        if now.hour == target_hour and now.minute == target_minute and last_sent_date != today:
            stats = get_stats()

            # Unrealized P&L from open positions
            try:
                open_enriched = get_open_trades_with_current_prices(bot_state.get("last_markets", []), client=None)
                unrealized = sum(t["unrealized_pnl"] for t in open_enriched if t.get("unrealized_pnl") is not None)
                stats["unrealized_pnl"] = round(unrealized, 2)
            except Exception:
                pass

            # Previous bankroll for day-over-day comparison
            if prev_bankroll is not None:
                stats["prev_bankroll"] = prev_bankroll
            prev_bankroll = stats.get("bankroll", prev_bankroll)

            # Win streak calculation
            try:
                from src.core.trade_executor import get_db as _get_db
                conn = _get_db()
                recent = conn.execute(
                    "SELECT pnl_usd FROM trades WHERE status='settled' ORDER BY id DESC LIMIT 20"
                ).fetchall()
                conn.close()
                streak = 0
                for (pnl,) in recent:
                    if pnl is None:
                        break
                    if streak == 0:
                        streak = 1 if pnl > 0 else -1
                    elif streak > 0 and pnl > 0:
                        streak += 1
                    elif streak < 0 and pnl <= 0:
                        streak -= 1
                    else:
                        break
                stats["win_streak"] = streak
            except Exception:
                pass

            notify_daily_summary(stats)
            last_sent_date = today
        _time.sleep(30)


# Settlement background thread — checks every 15 min regardless of bot state
def _settlement_loop():
    import time as _time
    while True:
        _time.sleep(900)  # 15 minutes
        try:
            results = settle_open_trades()
            if results.get("settled", 0) > 0:
                notify_settlement(results)
        except Exception:
            pass

_settlement_thread = threading.Thread(target=_settlement_loop, daemon=True)
_settlement_thread.start()

# Start daily summary thread if Telegram is configured
if settings.telegram_bot_token and settings.telegram_chat_id:
    _daily_thread = threading.Thread(target=_daily_summary_loop, daemon=True)
    _daily_thread.start()
    start_command_listener(bot_state)
