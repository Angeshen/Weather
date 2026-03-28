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
    """Send end-of-day performance summary."""
    pnl = stats.get("total_pnl", 0)
    pnl_emoji = "📈" if pnl >= 0 else "📉"
    win_rate = stats.get("win_rate", 0)

    text = (
        f"📊 <b>Daily Summary</b>\n"
        f"\n"
        f"💰 Bankroll: <b>${stats.get('bankroll', 0):,.2f}</b>\n"
        f"{pnl_emoji} Total P&L: <b>${pnl:+,.2f}</b>\n"
        f"📈 Win Rate: {win_rate:.1f}%\n"
        f"\n"
        f"Total Trades: {stats.get('total_trades', 0)}\n"
        f"Open: {stats.get('open_trades', 0)} | "
        f"Settled: {stats.get('settled_trades', 0)}\n"
        f"Wins: {stats.get('wins', 0)} | Losses: {stats.get('losses', 0)}"
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


def test_notification() -> bool:
    """Send a test message to verify Telegram is configured."""
    return _send_message("✅ <b>Kalshi Weather Bot</b> — Telegram notifications working!")
