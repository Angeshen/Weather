"""
Telegram command handler — polls for incoming messages and responds to commands.
Runs as a background thread. No webhook needed.

Supported commands:
  /help    — list all commands
  /status  — bankroll, open trades, last scan
  /scan    — trigger a manual scan
  /pause   — stop bot from taking new trades
  /resume  — re-enable trading
  /trades  — list open positions
"""

import time
import httpx
from src.config import settings


TELEGRAM_API = "https://api.telegram.org/bot{token}"

_last_update_id = 0
_trading_paused = False


def is_paused() -> bool:
    return _trading_paused


def _send(text: str, chat_id: str = None):
    token = settings.telegram_bot_token
    cid = chat_id or settings.telegram_chat_id
    if not token or not cid:
        return
    try:
        with httpx.Client(timeout=10.0) as client:
            client.post(
                f"{TELEGRAM_API.format(token=token)}/sendMessage",
                json={"chat_id": cid, "text": text, "parse_mode": "HTML"},
            )
    except Exception:
        pass


def _get_updates():
    global _last_update_id
    token = settings.telegram_bot_token
    if not token:
        return []
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(
                f"{TELEGRAM_API.format(token=token)}/getUpdates",
                params={"offset": _last_update_id + 1, "timeout": 10, "limit": 10},
            )
            data = resp.json()
            updates = data.get("result", [])
            if updates:
                _last_update_id = updates[-1]["update_id"]
            return updates
    except Exception:
        return []


def _handle_command(text: str, chat_id: str, bot_state: dict):
    global _trading_paused

    cmd = text.strip().lower().split()[0]

    if cmd == "/help":
        _send(
            "🤖 <b>Kalshi Bot Commands</b>\n\n"
            "/status — Bankroll, open trades, last scan time\n"
            "/scan — Trigger a manual scan right now\n"
            "/trades — List all open positions\n"
            "/pause — Stop bot from opening new trades\n"
            "/resume — Re-enable trade execution\n"
            "/help — Show this message",
            chat_id,
        )

    elif cmd == "/status":
        from src.core.trade_executor import get_stats, get_open_trade_count
        stats = get_stats()
        running = bot_state.get("running", False)
        last_scan = bot_state.get("last_scan", "never")
        scan_count = bot_state.get("scan_count", 0)
        paused_str = " — <b>⏸ PAUSED</b>" if _trading_paused else ""
        status_emoji = "🟢" if running else "🔴"
        _send(
            f"{status_emoji} <b>Bot Status</b>{paused_str}\n\n"
            f"💰 Bankroll: <b>${stats['bankroll']:,.2f}</b>\n"
            f"📊 All-time P&amp;L: <b>${stats['total_pnl']:+,.2f}</b>\n"
            f"🎯 Win Rate: {stats['win_rate']:.1f}%\n\n"
            f"Open trades: {stats['open_trades']}\n"
            f"Settled: {stats['settled_trades']} (W:{stats['wins']} L:{stats['losses']})\n"
            f"Scans: {scan_count}\n"
            f"Last scan: {str(last_scan)[:19] if last_scan else 'never'}",
            chat_id,
        )

    elif cmd == "/scan":
        _send("📡 Triggering manual scan...", chat_id)
        try:
            from src.data.market_scanner import scan_weather_markets_public
            from src.data.weather import get_forecast_for_city
            from src.core.edge_calculator import evaluate_market
            from src.core.trade_executor import get_current_bankroll
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
                    sig = evaluate_market(market, forecast, bankroll)
                    if sig:
                        signals.append(sig)
                except Exception:
                    continue
            _send(
                f"✅ Scan complete\n"
                f"Markets: {len(markets)} | Signals: <b>{len(signals)}</b>",
                chat_id,
            )
        except Exception as e:
            _send(f"❌ Scan failed: {e}", chat_id)

    elif cmd == "/trades":
        from src.core.trade_executor import get_trade_history
        trades = [t for t in get_trade_history(20) if t.get("status") == "open"]
        if not trades:
            _send("📂 No open trades right now.", chat_id)
            return
        lines = [f"📂 <b>{len(trades)} Open Trade(s)</b>\n"]
        for t in trades:
            lines.append(
                f"• {t['ticker']} — {t['side'].upper()} @ {int(t.get('market_price',0)*100)}¢ "
                f"| ${t.get('position_size_usd',0):.0f} | {t.get('contracts',0)} contracts"
            )
        _send("\n".join(lines), chat_id)

    elif cmd == "/pause":
        _trading_paused = True
        _send("⏸ <b>Trading paused.</b>\nBot will not open new trades until /resume.", chat_id)

    elif cmd == "/resume":
        _trading_paused = False
        _send("▶️ <b>Trading resumed.</b>\nBot will open new trades normally.", chat_id)

    else:
        _send(f"❓ Unknown command: <code>{cmd}</code>\nType /help for available commands.", chat_id)


def start_command_listener(bot_state: dict):
    """Start background thread that polls Telegram for commands."""
    import threading

    def _loop():
        # Only accept commands from the configured chat ID (security)
        allowed_chat = str(settings.telegram_chat_id)
        while True:
            try:
                updates = _get_updates()
                for update in updates:
                    msg = update.get("message", {})
                    text = msg.get("text", "")
                    chat_id = str(msg.get("chat", {}).get("id", ""))
                    if not text.startswith("/"):
                        continue
                    if chat_id != allowed_chat:
                        _send("⛔ Unauthorized.", chat_id)
                        continue
                    _handle_command(text, chat_id, bot_state)
            except Exception:
                pass
            time.sleep(5)

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
