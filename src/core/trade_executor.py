"""
Trade executor with paper trading and live trading modes.
Handles order placement, tracking, and daily P&L.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from src.config import settings
from src.data.kalshi_client import KalshiClient
from src.core.notifications import notify_trade, notify_risk_alert


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
            note TEXT DEFAULT ''
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
    # Migrate: add note column if missing (for existing DBs)
    try:
        conn.execute("SELECT note FROM trades LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE trades ADD COLUMN note TEXT DEFAULT ''")
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
        "SELECT COUNT(*) FROM trades WHERE status = 'open'"
    ).fetchone()
    conn.close()
    return row[0] if row else 0


def is_ticker_already_open(ticker: str) -> bool:
    """Return True if there's already an open trade for this exact ticker."""
    conn = get_db()
    row = conn.execute(
        "SELECT COUNT(*) FROM trades WHERE status = 'open' AND ticker = ?", (ticker,)
    ).fetchone()
    conn.close()
    return (row[0] if row else 0) > 0


def get_open_trade_count_for_city(city: str) -> int:
    """Return number of open trades for a given city name."""
    conn = get_db()
    row = conn.execute(
        "SELECT COUNT(*) FROM trades WHERE status = 'open' AND city = ?", (city,)
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

        order_id = result.get("order", {}).get("order_id", "unknown")

        conn = get_db()
        cursor = conn.execute("""
            INSERT INTO trades (
                timestamp, ticker, city, target_date, threshold_f,
                side, direction, model_prob, market_price, edge, confidence,
                contracts, price_cents, position_size_usd, mode, status,
                forecast_mean, forecast_min, forecast_max, n_members, n_above,
                order_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'live', 'open', ?, ?, ?, ?, ?, ?)
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
            order_id,
        ))
        trade_id = cursor.lastrowid
        conn.commit()
        conn.close()

        bankroll = get_current_bankroll() - signal["position_size_usd"]
        log_bankroll(bankroll, f"Placed trade #{trade_id} on {signal['ticker']}")

        result = {"trade_id": trade_id, "mode": "live", "order_id": order_id, "status": "open"}
        notify_trade(signal, result)
        return result

    except Exception as e:
        return {"error": str(e), "mode": "live", "status": "failed"}


def execute_trade(signal: dict, client: KalshiClient = None) -> dict:
    """Route to paper or live execution based on config."""
    can_trade, reason = check_risk_limits()
    if not can_trade:
        notify_risk_alert(reason)
        return {"status": "blocked", "reason": reason}

    if is_ticker_already_open(signal["ticker"]):
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
        "bankroll": round(get_current_bankroll(), 2),
    }


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
        result.append(trade)
    return result


# Initialize DB on import
init_db()
