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

    unit = signal.get("unit", "°F")
    market_type = signal.get("market_type", "high_temp")
    type_label = {"high_temp": "High Temp", "low_temp": "Low Temp", "precipitation": "Rain"}.get(market_type, market_type)
    contracts = signal.get('contracts', 0)
    cost = signal.get('position_size_usd', 0)
    win_target = round(contracts * 1.0 - cost, 2) if contracts and cost else 0
    days = signal.get('days_to_expiry', '?')
    days_str = f"same-day" if days == 0 else f"{days}d"
    entry_c = int(signal['market_price'] * 100)

    text = (
        f"🔔 <b>Trade Placed</b> [{mode}] #{trade_id}\n"
        f"\n"
        f"<b>{signal['city']}</b> — {type_label} — {signal['target_date']} ({days_str})\n"
        f"🎯 Threshold: {signal['threshold_f']}{unit} | Direction: <b>{signal['direction']}</b>\n"
        f"\n"
        f"Model: {signal['model_prob']*100:.1f}%  vs  Market: {entry_c}¢\n"
        f"⚡ Edge: <b>{signal['edge']*100:.1f}%</b> | Confidence: {signal.get('confidence', 0)*100:.1f}%\n"
        f"💰 {contracts} contracts × {entry_c}¢ = <b>${cost:.2f}</b>\n"
        f"🏆 Win target: <b>${win_target:,.2f}</b>\n"
        f"🌡️ Forecast: {signal.get('forecast_mean', '?')}{unit} ({signal.get('forecast_min', '?')}–{signal.get('forecast_max', '?')})\n"
        f"👥 Ensemble: {signal.get('n_members', '?')} members, {signal.get('n_above', '?')} above threshold"
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


def notify_bot_status(status: str, bankroll: float = 0):
    """Send bot start/stop notifications."""
    emoji = "🟢" if status == "started" else "🔴"
    mode = settings.trading_mode.upper()
    bankroll_str = f"\n💰 Bankroll: <b>${bankroll:,.2f}</b>" if bankroll and status == "started" else ""
    text = f"{emoji} <b>Bot {status.upper()}</b> — Mode: {mode}{bankroll_str}"
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
        side_str = r.get('side', 'yes').upper()
        lines.append(
            f"  {outcome} <b>{r['city']}</b> {r['target_date']}\n"
            f"  {side_str} — Actual: <b>{r['actual']}{r['unit']}</b> vs threshold {r['threshold']}{r['unit']}\n"
            f"  P&L: <b>${r['pnl']:+.2f}</b>"
        )

    lines.append(f"\nBatch P&L: <b>${pnl:+.2f}</b>  |  W/L this batch: {wins}/{losses}")
    _send_message("\n".join(lines))


def notify_early_exit(ticker: str, entry_price: float, exit_price: float, realized_pnl: float, loss_pct: float,
                      city: str = "", contracts: int = 0, cost: float = 0):
    """Send notification when bot auto-exits a losing position."""
    city_str = f"<b>{city}</b> — " if city else ""
    contracts_str = f"{contracts} contracts × {exit_price*100:.0f}¢" if contracts else ""
    text = (
        f"⚡ <b>Early Exit</b>\n\n"
        f"{city_str}<code>{ticker}</code>\n"
        f"Entry: {entry_price*100:.0f}¢ → Exit: {exit_price*100:.0f}¢\n"
        f"📉 Loss: {loss_pct*100:.0f}% of position\n"
        f"💰 Realized P&L: <b>${realized_pnl:+.2f}</b> (staked ${cost:.2f})\n"
        f"{contracts_str}"
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

def notify_morning_ping(markets_count: int, open_trades: int, bankroll: float, stats: dict = None):
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
    mode = settings.trading_mode.upper()
    stats = stats or {}
    pnl = stats.get('total_pnl', 0)
    win_rate = stats.get('win_rate', 0)
    settled = stats.get('settled_trades', 0)
    pnl_str = f"${pnl:+,.2f}" if pnl != 0 else "$0.00"
    text = (
        f"☀️ <b>Good Morning!</b> [{mode}]\n\n"
        f"💰 Bankroll: <b>${bankroll:,.2f}</b>\n"
        f"📊 All-time P&L: <b>{pnl_str}</b> | Win rate: {win_rate:.1f}%\n"
        f"📡 Watching <b>{markets_count}</b> markets\n"
        f"📂 Open positions: <b>{open_trades}</b> | Settled: {settled}"
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


_last_no_markets_alert: float = 0

def notify_no_markets(scan_count: int = 0):
    """Alert when the market scanner returns 0 markets — may indicate API issue. Max once/hour."""
    import time
    global _last_no_markets_alert
    now = time.time()
    if now - _last_no_markets_alert < 3600:
        return
    _last_no_markets_alert = now
    text = (
        f"⚠️ <b>No Markets Found</b>\n\n"
        f"Scanner returned 0 markets (scan #{scan_count}).\n"
        f"Kalshi API may be down or all series are inactive."
    )
    _send_message(text)


def notify_daily_loss_limit(daily_loss: float, limit: float):
    """Alert when daily loss limit is hit and bot stops trading."""
    text = (
        f"🛑 <b>Daily Loss Limit Hit</b>\n\n"
        f"Lost <b>${abs(daily_loss):,.2f}</b> today (limit: ${limit:,.2f})\n"
        f"Bot will not open new trades until tomorrow.\n"
        f"Use /resume after midnight to re-enable manually."
    )
    _send_message(text)


def notify_cooldown_block(ticker: str, city: str, minutes_remaining: float, signal_edge: float):
    """Alert when a signal is blocked by the 1-hour re-entry cooldown."""
    text = (
        f"⏳ <b>Re-entry Blocked (Cooldown)</b>\n\n"
        f"<b>{city}</b> — <code>{ticker}</code>\n"
        f"Signal edge: {signal_edge*100:.1f}% — but cooldown active\n"
        f"⏱ {minutes_remaining:.0f} min remaining before re-entry allowed"
    )
    _send_message(text)


def notify_grace_period_skip(ticker: str, city: str, age_minutes: float, loss_pct: float):
    """Log when a trade is shielded from early exit by the grace period."""
    text = (
        f"🛡️ <b>Grace Period Active</b>\n\n"
        f"<b>{city}</b> — <code>{ticker}</code>\n"
        f"Current loss: {loss_pct*100:.0f}% — but trade is only {age_minutes:.0f} min old\n"
        f"Early exit skipped (15-min grace period in effect)"
    )
    _send_message(text)


def notify_settlement_pending(open_trades: list):
    """Morning alert listing open trades that expire today and need to settle."""
    if not open_trades:
        return
    from datetime import date
    today = date.today().isoformat()
    expiring = [t for t in open_trades if t.get("target_date") == today]
    if not expiring:
        return
    lines = [f"📅 <b>{len(expiring)} Trade(s) Expiring Today</b>\n"]
    for t in expiring:
        contracts = t.get("contracts", 0)
        cost = t.get("position_size_usd", 0)
        win_target = round(contracts * 1.0 - cost, 2) if contracts and cost else 0
        entry_c = int(t.get("market_price", 0) * 100)
        lines.append(
            f"• <b>{t.get('city','?')}</b> {t.get('direction','?')} @ {entry_c}¢\n"
            f"  {contracts} contracts — win: 🏆${win_target:,.2f}"
        )
    _send_message("\n".join(lines))


_peak_bankroll: float = 0
_last_drawdown_alert: float = 0

def notify_drawdown(current: float, peak: float, drawdown_pct: float):
    """Alert when bankroll drops 10%+ from its peak. Max once per 6 hours."""
    import time
    global _last_drawdown_alert
    now = time.time()
    if now - _last_drawdown_alert < 21600:
        return
    _last_drawdown_alert = now
    text = (
        f"📉 <b>Drawdown Alert</b>\n\n"
        f"Bankroll: <b>${current:,.2f}</b> (peak: ${peak:,.2f})\n"
        f"Drawdown: <b>{drawdown_pct:.1f}%</b> from high water mark\n"
        f"Down ${peak - current:,.2f} from peak"
    )
    _send_message(text)


def check_and_notify_drawdown(current_bankroll: float):
    """Call each scan to track peak bankroll and fire drawdown alert at 10%."""
    global _peak_bankroll
    if current_bankroll > _peak_bankroll:
        _peak_bankroll = current_bankroll
    if _peak_bankroll > 0:
        drawdown_pct = (_peak_bankroll - current_bankroll) / _peak_bankroll * 100
        if drawdown_pct >= 10.0:
            notify_drawdown(current_bankroll, _peak_bankroll, drawdown_pct)


_last_streak_notified: int = 0

def notify_streak_milestone(streak: int, stats: dict):
    """Alert on win/loss streak milestones (every 3rd in a row)."""
    global _last_streak_notified
    if streak == _last_streak_notified:
        return
    if abs(streak) < 3 or abs(streak) % 3 != 0:
        return
    _last_streak_notified = streak
    bankroll = stats.get("bankroll", 0)
    pnl = stats.get("total_pnl", 0)
    if streak > 0:
        text = (
            f"🔥 <b>{streak}-Win Streak!</b>\n\n"
            f"Bankroll: <b>${bankroll:,.2f}</b>\n"
            f"All-time P&L: ${pnl:+,.2f}\n"
            f"Win rate: {stats.get('win_rate', 0):.1f}%"
        )
    else:
        text = (
            f"❄️ <b>{abs(streak)}-Loss Streak</b>\n\n"
            f"Bankroll: <b>${bankroll:,.2f}</b>\n"
            f"All-time P&L: ${pnl:+,.2f}\n"
            f"Consider reviewing edge thresholds."
        )
    _send_message(text)


_last_heartbeat_check: float = 0
_last_scan_seen: str = ""

def check_scan_heartbeat(last_scan: str, interval_seconds: int = 1800):
    """Alert if the bot hasn't scanned in >30 minutes (silent crash detector)."""
    import time
    global _last_heartbeat_check, _last_scan_seen
    now = time.time()
    if now - _last_heartbeat_check < 600:
        return
    _last_heartbeat_check = now
    if not last_scan or last_scan == _last_scan_seen:
        return
    _last_scan_seen = last_scan
    # Check age of last scan
    try:
        from datetime import datetime, timezone
        ls = datetime.fromisoformat(last_scan)
        if ls.tzinfo is None:
            ls = ls.replace(tzinfo=timezone.utc)
        age_s = (datetime.now(timezone.utc) - ls).total_seconds()
        if age_s > interval_seconds:
            _send_message(
                f"💤 <b>Scan Heartbeat Missing</b>\n\n"
                f"Last scan was <b>{age_s/60:.0f} min ago</b>.\n"
                f"Bot may have crashed silently — check the server."
            )
    except Exception:
        pass


_last_gfs_notified: str = ""

def notify_gfs_model_run():
    """Notify when a fresh GFS model run is available (~00Z and ~12Z UTC)."""
    global _last_gfs_notified
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    run_label = f"{now.strftime('%Y-%m-%d')}-{'00Z' if now.hour < 12 else '12Z'}"
    if run_label == _last_gfs_notified:
        return
    # Only fire within the first 30 min of the run window
    mins_into_window = now.hour % 12 * 60 + now.minute
    if mins_into_window > 30:
        return
    _last_gfs_notified = run_label
    _send_message(
        f"🌐 <b>Fresh GFS Model Run Available</b>\n\n"
        f"Run: <b>{run_label}</b>\n"
        f"Forecast data will refresh on next scan — edge may shift."
    )


_last_price_move_alert: dict = {}

def notify_price_move(ticker: str, city: str, side: str, entry_price: float,
                      current_price: float, move_pct: float):
    """Alert when an open position moves >20% against us in a single scan."""
    import time
    now = time.time()
    if now - _last_price_move_alert.get(ticker, 0) < 1800:
        return
    _last_price_move_alert[ticker] = now
    direction = "against you ⬇️" if move_pct < 0 else "in your favor ⬆️"
    text = (
        f"📊 <b>Price Movement Alert</b>\n\n"
        f"<b>{city}</b> — <code>{ticker}</code>\n"
        f"Entry: {entry_price*100:.0f}¢ → Now: {current_price*100:.0f}¢\n"
        f"Move: <b>{move_pct*100:+.1f}%</b> {direction}"
    )
    _send_message(text)


def notify_big_win(trade: dict, pnl: float, threshold: float = 200.0):
    """Celebrate a big win when single trade profit exceeds threshold."""
    if pnl < threshold:
        return
    contracts = trade.get("contracts", 0)
    cost = trade.get("position_size_usd", 0)
    entry_c = int(trade.get("market_price", 0) * 100)
    text = (
        f"🎉 <b>Big Win!</b>\n\n"
        f"<b>{trade.get('city','?')}</b> — <code>{trade.get('ticker','?')}</code>\n"
        f"{trade.get('side','yes').upper()} @ {entry_c}¢ | {contracts} contracts\n"
        f"💰 Profit: <b>${pnl:+,.2f}</b>\n"
        f"ROI: {pnl/cost*100:.0f}% on ${cost:.2f} staked"
    )
    _send_message(text)


def test_notification() -> bool:
    """Send a test message to verify Telegram is configured."""
    return _send_message("✅ <b>Kalshi Weather Bot</b> — Telegram notifications working!")
