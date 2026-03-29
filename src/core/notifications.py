"""
Telegram notification system for trade alerts and daily summaries.
"""

import httpx
from src.config import settings


TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def _send_message(text: str, parse_mode: str = "HTML") -> bool:
    """Send a message via Telegram Bot API. Returns True if successful."""
    token = settings.telegram_bot_token
    chat_id = settings.telegram_chat_id

    if not token or not chat_id:
        return False

    url = TELEGRAM_API.format(token=token)
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(url, json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode,
            })
            return resp.status_code == 200
    except Exception:
        return False


def notify_trade(signal: dict, result: dict):
    """Send a notification when a trade is placed."""
    mode = result.get("mode", "paper").upper()
    trade_id = result.get("trade_id", "?")
    status = result.get("status", "?")

    unit = signal.get("unit", "°F")
    market_type = signal.get("market_type", "high_temp")
    type_label = {"high_temp": "High Temp", "low_temp": "Low Temp", "precipitation": "Rain"}.get(market_type, market_type)

    text = (
        f"🔔 <b>Trade Placed</b> [{mode}]\n"
        f"\n"
        f"<b>{signal['city']}</b> — {type_label}\n"
        f"📅 {signal['target_date']}\n"
        f"🎯 Threshold: {signal['threshold_f']}{unit}\n"
        f"📊 Direction: <b>{signal['direction']}</b>\n"
        f"\n"
        f"Model: {signal['model_prob']*100:.1f}%  vs  Market: {signal['market_price']*100:.1f}%\n"
        f"⚡ Edge: <b>{signal['edge']*100:.1f}%</b>\n"
        f"💰 Size: <b>${signal['position_size_usd']:.2f}</b> ({signal['contracts']} contracts)\n"
        f"🌡️ Forecast: {signal.get('forecast_mean', '?')}{unit} "
        f"({signal.get('forecast_min', '?')}-{signal.get('forecast_max', '?')})\n"
        f"\n"
        f"Trade #{trade_id} — {status}"
    )
    _send_message(text)


def notify_scan_summary(markets_count: int, signals_count: int, trades_executed: int):
    """Send a brief scan summary (only when there are signals)."""
    if signals_count == 0:
        return  # Don't spam on empty scans

    text = (
        f"📡 <b>Scan Complete</b>\n"
        f"Markets: {markets_count} | Signals: {signals_count} | Trades: {trades_executed}"
    )
    _send_message(text)


def notify_daily_summary(stats: dict):
    """Send end-of-day performance summary with unrealized P&L, streak, and day comparison."""
    import time
    pnl = stats.get("total_pnl", 0)
    pnl_emoji = "📈" if pnl >= 0 else "📉"
    win_rate = stats.get("win_rate", 0)
    bankroll = stats.get("bankroll", 0)
    prev_bankroll = stats.get("prev_bankroll", bankroll)
    day_change = bankroll - prev_bankroll
    day_emoji = "📈" if day_change >= 0 else "📉"

    unrealized = stats.get("unrealized_pnl")
    unrealized_str = f"📊 Unrealized: <b>${unrealized:+.2f}</b>\n" if unrealized is not None else ""

    streak = stats.get("win_streak", 0)
    streak_str = ""
    if streak >= 3:
        streak_str = f"🔥 Win streak: <b>{streak}</b>\n"
    elif streak <= -3:
        streak_str = f"❄️ Loss streak: <b>{abs(streak)}</b>\n"

    text = (
        f"📊 <b>Daily Summary</b>\n\n"
        f"💰 Bankroll: <b>${bankroll:,.2f}</b>\n"
        f"{day_emoji} Today: <b>${day_change:+,.2f}</b>\n"
        f"{pnl_emoji} All-time P&L: <b>${pnl:+,.2f}</b>\n"
        f"{unrealized_str}"
        f"🎯 Win Rate: {win_rate:.1f}%\n"
        f"{streak_str}\n"
        f"Open: {stats.get('open_trades', 0)} | "
        f"Settled: {stats.get('settled_trades', 0)} | "
        f"W/L: {stats.get('wins', 0)}/{stats.get('losses', 0)}"
    )
    _send_message(text)


_last_risk_alert: dict = {}

def notify_risk_alert(message: str):
    """Send alert when risk limits are hit. Cooldown of 1 hour per unique message."""
    import time
    now = time.time()
    last_sent = _last_risk_alert.get(message, 0)
    if now - last_sent < 3600:
        return  # Don't repeat the same alert within 1 hour
    _last_risk_alert[message] = now
    text = f"⚠️ <b>Risk Alert</b>\n\n{message}"
    _send_message(text)


def notify_bot_status(status: str):
    """Send bot start/stop notifications."""
    emoji = "🟢" if status == "started" else "🔴"
    mode = settings.trading_mode.upper()
    text = f"{emoji} <b>Bot {status.upper()}</b> — Mode: {mode}"
    _send_message(text)


def notify_settlement(results: dict):
    """Send notification when trades are settled."""
    settled = results.get("settled", 0)
    if settled == 0:
        return

    wins = results.get("wins", 0)
    losses = results.get("losses", 0)
    pnl = results.get("total_pnl", 0)
    pnl_emoji = "✅" if pnl >= 0 else "❌"

    lines = [f"{pnl_emoji} <b>{settled} Trade(s) Settled</b>\n"]
    for r in results.get("results", []):
        outcome = "✅ WON" if r["won"] else "❌ LOST"
        lines.append(
            f"  {r['city']} {r['target_date']} — "
            f"Actual: {r['actual']}{r['unit']} vs {r['threshold']}{r['unit']} "
            f"→ {outcome} (${r['pnl']:+.2f})"
        )

    lines.append(f"\nTotal P&L: <b>${pnl:+.2f}</b>  |  W/L: {wins}/{losses}")
    _send_message("\n".join(lines))


def notify_early_exit(ticker: str, entry_price: float, exit_price: float, realized_pnl: float, loss_pct: float):
    """Send notification when bot auto-exits a losing position."""
    text = (
        f"⚡ <b>Early Exit</b>\n\n"
        f"<b>{ticker}</b>\n"
        f"Entry: {entry_price*100:.0f}¢ → Exit: {exit_price*100:.0f}¢\n"
        f"📉 Loss cut: {loss_pct*100:.0f}%\n"
        f"💰 Realized P&L: <b>${realized_pnl:+.2f}</b>"
    )
    _send_message(text)


def notify_order_error(message: str):
    """Send notification when a live order fails (technical error, not risk event)."""
    text = f"🔧 <b>Order Error</b>\n\n{message}"
    _send_message(text)


def notify_blocked_signal(signal: dict, reason: str):
    """Alert when a high-edge signal is blocked by risk limits."""
    edge = signal.get("edge", 0)
    if edge < 0.20:
        return  # Only alert on strong signals (20%+ edge) that get blocked
    unit = signal.get("unit", "°F")
    text = (
        f"🚫 <b>High-Edge Signal Blocked</b>\n\n"
        f"<b>{signal['city']}</b> — {signal['target_date']}\n"
        f"Direction: {signal['direction']}\n"
        f"⚡ Edge: <b>{edge*100:.1f}%</b> | Confidence: {signal.get('confidence', 0)*100:.1f}%\n"
        f"💰 Would-be size: ${signal.get('position_size_usd', 0):.2f}\n\n"
        f"🔒 Blocked: <i>{reason}</i>"
    )
    _send_message(text)


_morning_ping_sent_date: str = ""
MORNING_PING_HOUR = 8   # 8am local server time

def notify_morning_ping(markets_count: int, open_trades: int, bankroll: float):
    """Send a morning liveness ping at 8am local time once per day."""
    global _morning_ping_sent_date
    from datetime import datetime, date
    now = datetime.now()
    today = date.today().isoformat()
    if _morning_ping_sent_date == today:
        return  # Already sent today
    if now.hour != MORNING_PING_HOUR:
        return  # Not 8am yet
    _morning_ping_sent_date = today
    text = (
        f"☀️ <b>Good Morning!</b>\n\n"
        f"💰 Bankroll: <b>${bankroll:,.2f}</b>\n"
        f"📡 Watching <b>{markets_count}</b> markets\n"
        f"📂 Open positions: <b>{open_trades}</b>"
    )
    _send_message(text)


_last_confidence_spike: dict = {}

def notify_confidence_spike(signal: dict):
    """Alert when ensemble confidence rapidly jumps to 80%+ in one scan."""
    import time
    ticker = signal.get("ticker", "")
    confidence = signal.get("confidence", 0)
    if confidence < 0.80:
        return
    now = time.time()
    last = _last_confidence_spike.get(ticker, 0)
    if now - last < 7200:
        return  # Max once per 2 hours per ticker
    _last_confidence_spike[ticker] = now
    unit = signal.get("unit", "°F")
    text = (
        f"⚡ <b>Confidence Spike</b>\n\n"
        f"<b>{signal['city']}</b> — {signal['target_date']}\n"
        f"Direction: {signal['direction']}\n"
        f"🎯 Confidence: <b>{confidence*100:.1f}%</b> (rapid ensemble convergence)\n"
        f"Edge: {signal.get('edge', 0)*100:.1f}% | Size: ${signal.get('position_size_usd', 0):.2f}"
    )
    _send_message(text)


def notify_weekly_summary(stats: dict):
    """Send weekly performance summary every Sunday."""
    pnl = stats.get("total_pnl", 0)
    week_pnl = stats.get("week_pnl", 0)
    pnl_emoji = "📈" if week_pnl >= 0 else "📉"
    text = (
        f"📅 <b>Weekly Summary</b>\n\n"
        f"💰 Bankroll: <b>${stats.get('bankroll', 0):,.2f}</b>\n"
        f"{pnl_emoji} This week: <b>${week_pnl:+,.2f}</b>\n"
        f"📊 All-time P&L: <b>${pnl:+,.2f}</b>\n"
        f"🎯 Win Rate: {stats.get('win_rate', 0):.1f}%\n\n"
        f"Trades this week: {stats.get('week_trades', 0)}\n"
        f"W/L: {stats.get('week_wins', 0)}/{stats.get('week_losses', 0)}"
    )
    _send_message(text)


def test_notification() -> bool:
    """Send a test message to verify Telegram is configured."""
    return _send_message("✅ <b>Kalshi Weather Bot</b> — Telegram notifications working!")
