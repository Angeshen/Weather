"""
Trade executor with paper trading and live trading modes.
Handles order placement, tracking, and daily P&L.
"""

import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

from src.config import settings
from src.data.kalshi_client import KalshiClient
from src.core.notifications import (notify_trade, notify_risk_alert, notify_early_exit,
    notify_order_error, notify_daily_loss_limit, notify_cooldown_block, notify_grace_period_skip,
    notify_price_move)


DB_PATH = Path(__file__).parent.parent.parent / "data" / "trades.db"


def init_db():
    """Create the trades database and tables if they don't exist."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            ticker TEXT NOT NULL,
            city TEXT,
            target_date TEXT,
            threshold_f REAL,
            side TEXT NOT NULL,
            direction TEXT,
            model_prob REAL,
            market_price REAL,
            edge REAL,
            confidence REAL,
            contracts INTEGER,
            price_cents INTEGER,
            position_size_usd REAL,
            mode TEXT NOT NULL DEFAULT 'paper',
            status TEXT NOT NULL DEFAULT 'open',
            pnl_usd REAL DEFAULT 0,
            settled_at TEXT,
            forecast_mean REAL,
            forecast_min REAL,
            forecast_max REAL,
            n_members INTEGER,
            n_above INTEGER,
            order_id TEXT,
            note TEXT DEFAULT '',
            actual_temp REAL,
            filled_contracts INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS forecast_accuracy (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            threshold_f REAL,
            forecast_mean REAL,
            actual_temp REAL,
            error_f REAL,
            side TEXT,
            won INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_pnl (
            date TEXT PRIMARY KEY,
            total_pnl REAL DEFAULT 0,
            trades_count INTEGER DEFAULT 0,
            wins INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bankroll_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            bankroll REAL NOT NULL,
            event TEXT
        )
    """)
    # Migrate: add columns if missing (for existing DBs)
    for col, definition in [
        ("note", "TEXT DEFAULT ''"),
        ("actual_temp", "REAL"),
        ("filled_contracts", "INTEGER"),
    ]:
        try:
            conn.execute(f"SELECT {col} FROM trades LIMIT 1")
        except sqlite3.OperationalError:
            conn.execute(f"ALTER TABLE trades ADD COLUMN {col} {definition}")
    conn.commit()
    conn.close()


def update_trade_note(trade_id: int, note: str):
    """Update the note for a trade."""
    conn = get_db()
    conn.execute("UPDATE trades SET note = ? WHERE id = ?", (note, trade_id))
    conn.commit()
    conn.close()


def get_db():
    return sqlite3.connect(str(DB_PATH))


def get_current_bankroll() -> float:
    """Get current bankroll from the log, or return initial bankroll."""
    conn = get_db()
    row = conn.execute(
        "SELECT bankroll FROM bankroll_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if row:
        return row[0]
    return settings.initial_bankroll


def log_bankroll(bankroll: float, event: str = ""):
    conn = get_db()
    conn.execute(
        "INSERT INTO bankroll_log (timestamp, bankroll, event) VALUES (?, ?, ?)",
        (datetime.now(timezone.utc).isoformat(), bankroll, event),
    )
    conn.commit()
    conn.close()


def get_daily_loss_today() -> float:
    """Get total P&L for today (negative = losses)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    conn = get_db()
    row = conn.execute(
        "SELECT total_pnl FROM daily_pnl WHERE date = ?", (today,)
    ).fetchone()
    conn.close()
    return row[0] if row else 0.0


def get_open_trade_count() -> int:
    conn = get_db()
    row = conn.execute(
        "SELECT COUNT(*) FROM trades WHERE status = 'open' AND mode = ?",
        (settings.trading_mode,)
    ).fetchone()
    conn.close()
    return row[0] if row else 0


def is_ticker_already_open(ticker: str) -> tuple[bool, str]:
    """Return (True, reason) if ticker is blocked, (False, '') if clear.
    Blocks if: open trade exists in current mode, or early-exited within the last hour."""
    conn = get_db()
    mode = settings.trading_mode
    # Check open trades — scoped to current mode so paper/live don't block each other
    row = conn.execute(
        "SELECT COUNT(*) FROM trades WHERE status = 'open' AND ticker = ? AND mode = ?", (ticker, mode)
    ).fetchone()
    if (row[0] if row else 0) > 0:
        conn.close()
        return True, "open"
    # Check if early-exited (settled with loss) within the last 60 minutes — scoped to mode
    one_hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    row = conn.execute(
        "SELECT id, settled_at FROM trades WHERE ticker = ? AND mode = ? AND status = 'settled' "
        "AND pnl_usd < 0 AND settled_at > ? ORDER BY id DESC LIMIT 1",
        (ticker, mode, one_hour_ago)
    ).fetchone()
    conn.close()
    if row:
        try:
            settled_at = datetime.fromisoformat(row[1])
            if settled_at.tzinfo is None:
                settled_at = settled_at.replace(tzinfo=timezone.utc)
            elapsed = (datetime.now(timezone.utc) - settled_at).total_seconds()
            remaining_min = max(0, (3600 - elapsed) / 60)
        except Exception:
            remaining_min = 60
        return True, f"cooldown:{remaining_min:.0f}"
    return False, ""


def get_open_trade_count_for_city(city: str) -> int:
    """Return number of open trades for a given city name, scoped to current trading mode."""
    conn = get_db()
    row = conn.execute(
        "SELECT COUNT(*) FROM trades WHERE status = 'open' AND city = ? AND mode = ?",
        (city, settings.trading_mode)
    ).fetchone()
    conn.close()
    return row[0] if row else 0


def check_risk_limits() -> tuple[bool, str]:
    """Check if we can place more trades based on risk limits."""
    daily_pnl = get_daily_loss_today()
    if daily_pnl <= -settings.daily_loss_limit:
        return False, f"Daily loss limit hit: ${abs(daily_pnl):.2f} lost today (limit: ${settings.daily_loss_limit})"

    open_count = get_open_trade_count()
    if open_count >= settings.max_concurrent_trades:
        return False, f"Max concurrent trades reached: {open_count}/{settings.max_concurrent_trades}"

    return True, "OK"


def execute_paper_trade(signal: dict) -> dict:
    """Execute a paper trade — log it without placing a real order."""
    conn = get_db()
    cursor = conn.execute("""
        INSERT INTO trades (
            timestamp, ticker, city, target_date, threshold_f,
            side, direction, model_prob, market_price, edge, confidence,
            contracts, price_cents, position_size_usd, mode, status,
            forecast_mean, forecast_min, forecast_max, n_members, n_above
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'paper', 'open', ?, ?, ?, ?, ?)
    """, (
        datetime.now(timezone.utc).isoformat(),
        signal["ticker"],
        signal["city"],
        signal["target_date"],
        signal["threshold_f"],
        signal["side"],
        signal["direction"],
        signal["model_prob"],
        signal["market_price"],
        signal["edge"],
        signal["confidence"],
        signal["contracts"],
        signal["price_cents"],
        signal["position_size_usd"],
        signal.get("forecast_mean"),
        signal.get("forecast_min"),
        signal.get("forecast_max"),
        signal.get("n_members"),
        signal.get("n_above"),
    ))
    trade_id = cursor.lastrowid
    conn.commit()
    conn.close()

    result = {"trade_id": trade_id, "mode": "paper", "status": "open"}
    notify_trade(signal, result)
    return result


def execute_live_trade(signal: dict, client: KalshiClient) -> dict:
    """Execute a live trade on Kalshi."""
    try:
        result = client.place_order(
            ticker=signal["ticker"],
            side=signal["side"],
            quantity=signal["contracts"],
            price_cents=signal["price_cents"],
            order_type="limit",
        )

        order = result.get("order", {})
        order_id = order.get("order_id", "unknown")

        # Partial fill tracking: actual filled qty may differ from requested
        filled_contracts = order.get("filled_count") or order.get("quantity_filled")
        if filled_contracts is not None:
            filled_contracts = int(filled_contracts)
        else:
            filled_contracts = signal["contracts"]  # assume full fill if not reported

        # Fill quality: detect price slippage
        fill_price_cents = order.get("avg_price") or order.get("avg_fill_price")
        if fill_price_cents and abs(int(fill_price_cents) - signal["price_cents"]) > 3:
            try:
                from src.core.notifications import notify_fill_quality
                notify_fill_quality(
                    ticker=signal["ticker"],
                    city=signal["city"],
                    requested_cents=signal["price_cents"],
                    filled_cents=int(fill_price_cents),
                    requested_contracts=signal["contracts"],
                    filled_contracts=filled_contracts,
                )
            except Exception:
                pass

        actual_size = round(filled_contracts * signal["market_price"], 2)

        conn = get_db()
        cursor = conn.execute("""
            INSERT INTO trades (
                timestamp, ticker, city, target_date, threshold_f,
                side, direction, model_prob, market_price, edge, confidence,
                contracts, price_cents, position_size_usd, mode, status,
                forecast_mean, forecast_min, forecast_max, n_members, n_above,
                order_id, filled_contracts
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'live', 'open', ?, ?, ?, ?, ?, ?, ?)
        """, (
            datetime.now(timezone.utc).isoformat(),
            signal["ticker"],
            signal["city"],
            signal["target_date"],
            signal["threshold_f"],
            signal["side"],
            signal["direction"],
            signal["model_prob"],
            signal["market_price"],
            signal["edge"],
            signal["confidence"],
            filled_contracts,
            signal["price_cents"],
            actual_size,
            signal.get("forecast_mean"),
            signal.get("forecast_min"),
            signal.get("forecast_max"),
            signal.get("n_members"),
            signal.get("n_above"),
            order_id,
            filled_contracts,
        ))
        trade_id = cursor.lastrowid
        conn.commit()
        conn.close()

        bankroll = get_current_bankroll() - actual_size
        log_bankroll(bankroll, f"Placed trade #{trade_id} on {signal['ticker']} ({filled_contracts}/{signal['contracts']} filled)")

        result = {"trade_id": trade_id, "mode": "live", "order_id": order_id, "status": "open",
                  "filled_contracts": filled_contracts, "requested_contracts": signal["contracts"]}
        notify_trade(signal, result)
        return result

    except Exception as e:
        return {"error": str(e), "mode": "live", "status": "failed"}


def execute_trade(signal: dict, client: KalshiClient = None) -> dict:
    """Route to paper or live execution based on config."""
    can_trade, reason = check_risk_limits()
    if not can_trade:
        if "Daily loss limit" in reason:
            daily_pnl = get_daily_loss_today()
            notify_daily_loss_limit(daily_pnl, settings.daily_loss_limit)
        else:
            notify_risk_alert(reason)
        return {"status": "blocked", "reason": reason}

    blocked, block_reason = is_ticker_already_open(signal["ticker"])
    if blocked:
        if block_reason.startswith("cooldown:"):
            remaining = float(block_reason.split(":")[1])
            notify_cooldown_block(signal["ticker"], signal.get("city", ""), remaining, signal.get("edge", 0))
            return {"status": "blocked", "reason": f"Cooldown active on {signal['ticker']} ({remaining:.0f} min remaining)"}
        return {"status": "blocked", "reason": f"Already have open trade on {signal['ticker']}"}

    city = signal.get("city", "")
    if city and get_open_trade_count_for_city(city) >= settings.max_trades_per_city:
        return {"status": "blocked", "reason": f"Max trades per city reached for {city} ({settings.max_trades_per_city})"}

    if settings.trading_mode == "live" and client:
        return execute_live_trade(signal, client)
    else:
        return execute_paper_trade(signal)


def get_trade_history(limit: int = 50) -> list[dict]:
    """Get recent trade history."""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    columns = [desc[0] for desc in conn.execute("SELECT * FROM trades LIMIT 0").description]
    conn.close()
    return [dict(zip(columns, row)) for row in rows]


def get_bankroll_history(limit: int = 100) -> list[dict]:
    """Get bankroll history for equity curve."""
    conn = get_db()
    rows = conn.execute(
        "SELECT timestamp, bankroll, event FROM bankroll_log ORDER BY id DESC LIMIT ?",
        (limit,)
    ).fetchall()
    conn.close()
    return [{"timestamp": r[0], "bankroll": r[1], "event": r[2]} for r in reversed(rows)]


def get_settled_trades() -> list[dict]:
    """Get all settled trades for P&L chart."""
    conn = get_db()
    rows = conn.execute(
        "SELECT timestamp, settled_at, pnl_usd, city, ticker FROM trades WHERE status = 'settled' ORDER BY id"
    ).fetchall()
    conn.close()
    cumulative = 0
    result = []
    for r in rows:
        cumulative += r[2]
        result.append({
            "timestamp": r[1] or r[0],
            "pnl": r[2],
            "cumulative_pnl": round(cumulative, 2),
            "city": r[3],
            "ticker": r[4],
        })
    return result


def get_stats() -> dict:
    """Get overall trading statistics."""
    conn = get_db()

    total = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    open_trades = conn.execute("SELECT COUNT(*) FROM trades WHERE status = 'open'").fetchone()[0]
    settled = conn.execute("SELECT COUNT(*) FROM trades WHERE status = 'settled'").fetchone()[0]
    wins = conn.execute("SELECT COUNT(*) FROM trades WHERE status = 'settled' AND pnl_usd > 0").fetchone()[0]
    total_pnl = conn.execute("SELECT COALESCE(SUM(pnl_usd), 0) FROM trades WHERE status = 'settled'").fetchone()[0]

    conn.close()

    return {
        "total_trades": total,
        "open_trades": open_trades,
        "settled_trades": settled,
        "wins": wins,
        "losses": settled - wins,
        "win_rate": (wins / settled * 100) if settled > 0 else 0,
        "total_pnl": round(total_pnl, 2),
        "daily_pnl": round(get_daily_loss_today(), 2),
        "bankroll": round(get_current_bankroll(), 2),
    }


def _exit_loss_threshold():
    return settings.exit_loss_threshold


def exit_losing_positions(current_markets: list, client=None) -> list[dict]:
    """
    Check open trades against current market prices.
    If a position has lost 20%+ of its value, exit it to cut losses.

    Returns list of exited trades with realized P&L.
    """
    conn = get_db()
    conn.row_factory = sqlite3.Row
    open_trades = conn.execute(
        "SELECT * FROM trades WHERE status = 'open'"
    ).fetchall()
    conn.close()

    price_lookup = {m["ticker"]: m for m in current_markets}
    exited = []

    for row in open_trades:
        trade = dict(row)
        ticker = trade["ticker"]
        market = price_lookup.get(ticker)
        if not market:
            continue

        entry_price = trade.get("market_price", 0)
        contracts = trade.get("contracts", 0)
        cost = trade.get("position_size_usd", 0)
        side = trade.get("side", "yes")

        if not entry_price or not contracts or not cost:
            continue

        # Skip early exit for low-price contracts — bid/ask spread is noise on illiquid markets
        if entry_price < 0.10:
            continue

        # Skip exit check for trades entered less than 15 minutes ago
        try:
            entered_at = datetime.fromisoformat(trade["timestamp"])
            if entered_at.tzinfo is None:
                entered_at = entered_at.replace(tzinfo=timezone.utc)
            age_seconds = (datetime.now(timezone.utc) - entered_at).total_seconds()
        except Exception:
            age_seconds = 9999  # If parsing fails, assume old trade — allow exit check
        if age_seconds < 900:
            # Notify if the trade would have been exited but is protected
            current_bid_check = market.get("yes_bid") if trade.get("side") == "yes" else market.get("no_bid")
            if current_bid_check and cost:
                tentative_loss = (cost - current_bid_check * contracts) / cost
                if tentative_loss >= settings.exit_loss_threshold:
                    try:
                        notify_grace_period_skip(ticker, trade.get("city", ""), age_seconds / 60, tentative_loss)
                    except Exception:
                        pass
            continue

        # Current bid price (what we can sell at) — use bid only, never ask
        # If no bid exists we cannot exit, so skip
        if side == "yes":
            current_bid = market.get("yes_bid") or 0
        else:
            current_bid = market.get("no_bid") or 0

        if not current_bid:
            continue

        # Value if we sell now vs entry cost
        current_value = current_bid * contracts
        loss_pct = (cost - current_value) / cost if cost > 0 else 0

        # Price movement alert — significant move but not yet at exit threshold
        move_pct = (current_bid - entry_price) / entry_price if entry_price > 0 else 0
        if abs(move_pct) >= 0.20:
            try:
                notify_price_move(ticker, trade.get("city", ""), side, entry_price, current_bid, move_pct)
            except Exception:
                pass

        # Profit target exit: if current bid >= 60% of $1 max payout, lock in gains
        # Only on multi-day trades (age > 4 hours) to avoid churning same-day positions
        max_payout_per_contract = 1.0 - entry_price  # net profit per contract if it settles
        profit_target = entry_price + max_payout_per_contract * 0.60  # 60% of the way to $1
        if current_bid >= profit_target and age_seconds > 14400:
            realized_pnl = round(current_value - cost, 2)
            gain_pct = (current_bid - entry_price) / max_payout_per_contract * 100 if max_payout_per_contract > 0 else 0
            if settings.trading_mode == "live" and client:
                try:
                    sell_price_cents = int(current_bid * 100)
                    client.sell_order(ticker, side, contracts, sell_price_cents)
                except Exception as e:
                    notify_order_error(f"Profit exit order failed for {ticker}: {e}")
                    continue
            conn = get_db()
            conn.execute(
                "UPDATE trades SET status = 'settled', pnl_usd = ?, settled_at = ? WHERE id = ?",
                (realized_pnl, datetime.now(timezone.utc).isoformat(), trade["id"])
            )
            conn.commit()
            conn.close()
            bankroll = get_current_bankroll() + realized_pnl
            log_bankroll(bankroll, f"Profit exit {ticker}: {gain_pct:.0f}% of max gain locked")
            try:
                from src.core.notifications import notify_profit_exit
                notify_profit_exit(
                    ticker=ticker, city=trade.get("city", ""),
                    entry_cents=int(entry_price * 100), exit_cents=int(current_bid * 100),
                    contracts=contracts, pnl=realized_pnl, gain_pct=gain_pct,
                )
            except Exception:
                pass
            exited.append({"ticker": ticker, "pnl": realized_pnl, "gain_pct": gain_pct, "exit_type": "profit"})
            continue

        if loss_pct < _exit_loss_threshold():
            continue  # Position is fine, hold

        # Exit the position (loss)
        realized_pnl = round(current_value - cost, 2)

        if settings.trading_mode == "live" and client:
            try:
                sell_price_cents = int(current_bid * 100)
                client.sell_order(ticker, side, contracts, sell_price_cents)
            except Exception as e:
                notify_order_error(f"Exit order failed for {ticker}: {e}")
                continue

        # Mark as settled with loss in DB
        conn = get_db()
        conn.execute(
            "UPDATE trades SET status = 'settled', pnl_usd = ?, settled_at = ? WHERE id = ?",
            (realized_pnl, datetime.now(timezone.utc).isoformat(), trade["id"])
        )
        conn.commit()
        conn.close()

        bankroll = get_current_bankroll() + realized_pnl
        log_bankroll(bankroll, f"Exited {ticker} early: {loss_pct*100:.0f}% loss")

        notify_early_exit(ticker, entry_price, current_bid, realized_pnl, loss_pct,
                          city=trade.get("city", ""), contracts=contracts, cost=cost)
        exited.append({"ticker": ticker, "pnl": realized_pnl, "loss_pct": loss_pct, "exit_type": "loss"})

    return exited


def get_win_rate_by_city() -> list[dict]:
    """Get win rate and P&L broken down by city."""
    conn = get_db()
    rows = conn.execute("""
        SELECT city,
               COUNT(*) as total,
               SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) as wins,
               SUM(CASE WHEN pnl_usd <= 0 THEN 1 ELSE 0 END) as losses,
               ROUND(SUM(pnl_usd), 2) as total_pnl
        FROM trades
        WHERE status = 'settled' AND city IS NOT NULL AND city != ''
        GROUP BY city
        ORDER BY total_pnl DESC
    """).fetchall()
    conn.close()
    result = []
    for r in rows:
        city, total, wins, losses, total_pnl = r
        result.append({
            "city": city,
            "total": total,
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
            "total_pnl": total_pnl or 0,
        })
    return result


def log_forecast_accuracy(city: str, target_date: str, threshold_f: float,
                          forecast_mean: float, actual_temp: float,
                          side: str, won: bool):
    """Log model forecast vs actual temp for bias correction tracking."""
    conn = get_db()
    error_f = round(forecast_mean - actual_temp, 2) if forecast_mean and actual_temp else None
    conn.execute("""
        INSERT INTO forecast_accuracy
            (timestamp, city, target_date, threshold_f, forecast_mean, actual_temp, error_f, side, won)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.now(timezone.utc).isoformat(),
        city, target_date, threshold_f, forecast_mean, actual_temp, error_f,
        side, 1 if won else 0,
    ))
    conn.commit()
    conn.close()


def get_city_bias(city: str, min_samples: int = 5) -> float:
    """
    Get the mean forecast error (forecast_mean - actual) for a city.
    Positive bias = model runs warm (overpredicts). Negative = runs cold.
    Returns 0.0 if not enough samples yet.
    """
    conn = get_db()
    row = conn.execute("""
        SELECT AVG(error_f), COUNT(*)
        FROM forecast_accuracy
        WHERE city = ? AND error_f IS NOT NULL
        ORDER BY id DESC
        LIMIT 30
    """, (city,)).fetchone()
    conn.close()
    avg_error, count = row if row else (None, 0)
    if count < min_samples or avg_error is None:
        return 0.0
    return round(avg_error, 2)


def get_forecast_accuracy_stats() -> list[dict]:
    """Get per-city forecast accuracy summary for dashboard/commands."""
    conn = get_db()
    rows = conn.execute("""
        SELECT city,
               COUNT(*) as samples,
               ROUND(AVG(error_f), 2) as mean_bias,
               ROUND(AVG(ABS(error_f)), 2) as mae,
               SUM(won) as wins,
               COUNT(*) - SUM(won) as losses
        FROM forecast_accuracy
        WHERE error_f IS NOT NULL
        GROUP BY city
        ORDER BY city
    """).fetchall()
    conn.close()
    return [
        {"city": r[0], "samples": r[1], "mean_bias": r[2], "mae": r[3],
         "wins": r[4], "losses": r[5]}
        for r in rows
    ]


def get_open_trades_with_current_prices(last_markets: list) -> list[dict]:
    """Get open trades enriched with current market price for unrealized P&L."""
    conn = get_db()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM trades WHERE status = 'open' ORDER BY id DESC"
    ).fetchall()
    conn.close()

    # Build a price lookup from last scan markets
    price_lookup = {m["ticker"]: m for m in last_markets}

    result = []
    for row in rows:
        trade = dict(row)
        ticker = trade["ticker"]
        current = price_lookup.get(ticker, {})

        # Current market price for our side
        if trade.get("side") == "yes":
            current_price = current.get("yes_ask") or current.get("yes_bid")
        else:
            current_price = current.get("no_ask") or current.get("no_bid")

        entry_price = trade.get("market_price", 0)
        contracts = trade.get("contracts", 0)

        if current_price and entry_price and contracts:
            # Unrealized P&L = (current_price - entry_price) * contracts
            unrealized_pnl = round((current_price - entry_price) * contracts, 2)
        else:
            unrealized_pnl = None

        trade["current_price"] = current_price
        trade["unrealized_pnl"] = unrealized_pnl
        trade["volume"] = current.get("volume")
        trade["yes_bid"] = current.get("yes_bid")
        trade["yes_ask"] = current.get("yes_ask")
        result.append(trade)
    return result


# Initialize DB on import
init_db()
