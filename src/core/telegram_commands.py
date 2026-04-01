"""
Telegram command handler — polls for incoming messages and responds to commands.
Runs as a background thread. No webhook needed.

Supported commands:
  /help            — list all commands
  /status          — bankroll, mode, win rate, last scan
  /trades          — open positions with edge, age, win target
  /pnl             — today + all-time P&L
  /bankroll        — quick bankroll one-liner
  /scan            — trigger a manual scan + show signals
  /forecast        — model vs market for all active tickers
  /risk            — current exposure, daily loss used, max loss
  /exits           — recent early exits
  /settled         — last 10 settled trades
  /cooldowns       — tickers on re-entry cooldown with time remaining
  /mode            — show current mode; /mode live or /mode paper to switch
  /logs            — last 5 recent errors
  /history [city]  — trade history filtered by city
  /summary         — full snapshot of everything in one message
  /cities          — W/L and P&L breakdown by city
  /edge [ticker]   — on-demand edge check for a single ticker
  /settings        — show current live .env settings
  /pause           — stop bot from taking new trades
  /resume          — re-enable trading
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

    try:
        _dispatch_command(cmd, text, chat_id, bot_state)
    except Exception as e:
        _send(f"❌ <b>Error in {cmd}</b>: <code>{str(e)[:200]}</code>", chat_id)


def _dispatch_command(cmd: str, text: str, chat_id: str, bot_state: dict):
    global _trading_paused

    if cmd == "/help":
        _send(
            "🤖 <b>Kalshi Bot Commands</b>\n\n"
            "📊 <b>Info</b>\n"
            "/summary — Full snapshot of everything\n"
            "/status — Bankroll, mode, win rate, last scan\n"
            "/trades — Open positions + live volume & bid\n"
            "/pnl — Today's P&L and running totals\n"
            "/bankroll — Quick bankroll one-liner\n"
            "/risk — Exposure, daily loss used, max loss\n"
            "/forecast — Model vs market for all tickers\n"
            "/edge [ticker] — Edge check for one ticker\n"
            "/cities — W/L and P&L by city\n"
            "/exits — Recent early exits\n"
            "/settled — Last 10 settled trades\n"
            "/cooldowns — Tickers on re-entry cooldown\n"
            "/history [city] — Trade history by city\n"
            "/settings — Current bot settings\n"
            "/logs — Recent errors\n\n"
            "⚙️ <b>Control</b>\n"
            "/scan — Trigger a manual scan\n"
            "/mode — Show or switch paper/live mode\n"
            "/pause — Stop opening new trades\n"
            "/resume — Re-enable trade execution\n"
            "/help — Show this message",
            chat_id,
        )

    elif cmd == "/status":
        from src.core.trade_executor import get_stats, get_daily_loss_today
        from src.config import settings
        stats = get_stats()
        running = bot_state.get("running", False)
        last_scan = bot_state.get("last_scan", "never")
        scan_count = bot_state.get("scan_count", 0)
        last_errors = bot_state.get("last_errors", [])
        paused_str = " ⏸ PAUSED" if _trading_paused else ""
        status_emoji = "🟢" if running else "🔴"
        mode = settings.trading_mode.upper()
        daily_pnl = get_daily_loss_today()
        daily_emoji = "📈" if daily_pnl >= 0 else "📉"
        error_str = f"\n⚠️ Last error: <i>{last_errors[-1][:80]}</i>" if last_errors else ""
        _send(
            f"{status_emoji} <b>Bot Status</b> [{mode}]{paused_str}\n\n"
            f"💰 Bankroll: <b>${stats['bankroll']:,.2f}</b>\n"
            f"{daily_emoji} Today's P&L: <b>${daily_pnl:+,.2f}</b>\n"
            f"📊 All-time P&L: <b>${stats['total_pnl']:+,.2f}</b>\n"
            f"🎯 Win Rate: {stats['win_rate']:.1f}% ({stats['wins']}W / {stats['losses']}L)\n\n"
            f"📂 Open: {stats['open_trades']} | Settled: {stats['settled_trades']}\n"
            f"📡 Scans: {scan_count} | Last: {str(last_scan)[:19] if last_scan else 'never'}"
            f"{error_str}",
            chat_id,
        )

    elif cmd == "/scan":
        _send("📡 Scanning markets...", chat_id)
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
            if not signals:
                _send(f"✅ Scan complete — {len(markets)} markets, no signals", chat_id)
            else:
                lines = [f"✅ <b>Scan complete</b> — {len(markets)} markets, {len(signals)} signal(s)\n"]
                for s in sorted(signals, key=lambda x: x.get('edge', 0), reverse=True):
                    entry_c = int(s.get('market_price', 0) * 100)
                    contracts = s.get('contracts', 0)
                    cost = s.get('position_size_usd', 0)
                    win_target = round(contracts * 1.0 - cost, 2) if contracts and cost else 0
                    lines.append(
                        f"⚡ <b>{s['city']}</b> {s['target_date']} — {s['direction']}\n"
                        f"   Edge: <b>{s['edge']*100:.1f}%</b> | Conf: {s.get('confidence',0)*100:.1f}% | {entry_c}¢ × {contracts} = ${cost:.0f} → 🏆${win_target:,.0f}"
                    )
                _send("\n".join(lines), chat_id)
        except Exception as e:
            _send(f"❌ Scan failed: {e}", chat_id)

    elif cmd == "/trades":
        from src.core.trade_executor import get_open_trades_with_current_prices
        from datetime import datetime, timezone
        last_markets = bot_state.get("last_markets", [])
        trades = get_open_trades_with_current_prices(last_markets)
        if not trades:
            _send("📂 No open trades right now.", chat_id)
            return
        lines = [f"📂 <b>{len(trades)} Open Position(s)</b>\n"]
        for t in trades:
            entry_c = int(t.get('market_price', 0) * 100)
            contracts = t.get('contracts', 0)
            cost = t.get('position_size_usd', 0)
            win_target = round(contracts * 1.0 - cost, 2) if contracts and cost else 0
            edge = t.get('edge', 0)
            # Live market data
            current_bid = t.get('yes_bid') or 0
            current_c = int(current_bid * 100) if current_bid else 0
            volume = t.get('volume')
            vol_str = f" | vol: {int(float(volume)):,}" if volume else ""
            upnl = t.get('unrealized_pnl')
            upnl_str = f" | uP&L: <b>${upnl:+.2f}</b>" if upnl is not None else ""
            # Age
            age_str = "?"
            try:
                entered = datetime.fromisoformat(t['timestamp'])
                if entered.tzinfo is None:
                    entered = entered.replace(tzinfo=timezone.utc)
                age_h = int((datetime.now(timezone.utc) - entered).total_seconds() / 3600)
                age_str = f"{age_h}h"
            except Exception:
                pass
            lines.append(
                f"• <b>{t.get('city','?')}</b> <code>{t['ticker']}</code>\n"
                f"  {t.get('side','?').upper()} @ {entry_c}¢ → now {current_c}¢{upnl_str}\n"
                f"  ⚡{edge*100:.0f}% edge | age: {age_str}{vol_str}\n"
                f"  {contracts} contracts × {entry_c}¢ = ${cost:.0f} → 🏆${win_target:,.2f}"
            )
        _send("\n".join(lines), chat_id)

    elif cmd == "/pnl":
        from src.core.trade_executor import get_stats, get_daily_loss_today
        stats = get_stats()
        daily_pnl = get_daily_loss_today()
        daily_emoji = "📈" if daily_pnl >= 0 else "📉"
        all_emoji = "📈" if stats['total_pnl'] >= 0 else "📉"
        _send(
            f"💰 <b>P&L Summary</b>\n\n"
            f"{daily_emoji} Today: <b>${daily_pnl:+,.2f}</b>\n"
            f"{all_emoji} All-time: <b>${stats['total_pnl']:+,.2f}</b>\n"
            f"🎯 Win Rate: {stats['win_rate']:.1f}% ({stats['wins']}W / {stats['losses']}L)\n"
            f"💵 Bankroll: <b>${stats['bankroll']:,.2f}</b>\n"
            f"📂 Open positions: {stats['open_trades']}",
            chat_id,
        )

    elif cmd == "/bankroll":
        from src.core.trade_executor import get_current_bankroll, get_stats, get_daily_loss_today
        bankroll = get_current_bankroll()
        daily_pnl = get_daily_loss_today()
        stats = get_stats()
        daily_emoji = "📈" if daily_pnl >= 0 else "📉"
        _send(
            f"💰 <b>${bankroll:,.2f}</b>  {daily_emoji} ${daily_pnl:+,.2f} today  "
            f"| {stats['wins']}W / {stats['losses']}L",
            chat_id,
        )

    elif cmd == "/exits":
        from src.core.trade_executor import get_trade_history
        trades = get_trade_history(100)
        exits = [
            t for t in trades
            if t.get("status") == "settled" and t.get("pnl_usd", 0) < 0
            and t.get("settled_at")
        ][:10]
        if not exits:
            _send("⚡ No early exits on record.", chat_id)
            return
        lines = [f"⚡ <b>Recent Early Exits ({len(exits)})</b>\n"]
        for t in exits:
            entry_c = int(t.get('market_price', 0) * 100)
            pnl = t.get('pnl_usd', 0)
            cost = t.get('position_size_usd', 0)
            loss_pct = abs(pnl) / cost * 100 if cost else 0
            lines.append(
                f"• <b>{t.get('city','?')}</b> <code>{t['ticker']}</code>\n"
                f"  {t.get('side','?').upper()} @ {entry_c}¢ — P&L: <b>${pnl:+.2f}</b> ({loss_pct:.0f}% loss)"
            )
        _send("\n".join(lines), chat_id)

    elif cmd == "/settled":
        from src.core.trade_executor import get_trade_history
        trades = [t for t in get_trade_history(100) if t.get("status") == "settled"][:10]
        if not trades:
            _send("📊 No settled trades yet.", chat_id)
            return
        wins = sum(1 for t in trades if t.get('pnl_usd', 0) > 0)
        losses = len(trades) - wins
        total_pnl = sum(t.get('pnl_usd', 0) for t in trades)
        lines = [f"📊 <b>Last {len(trades)} Settled Trades</b> — W:{wins} L:{losses} P&L: ${total_pnl:+.2f}\n"]
        for t in trades:
            pnl = t.get('pnl_usd', 0)
            emoji = "✅" if pnl > 0 else "❌"
            entry_c = int(t.get('market_price', 0) * 100)
            lines.append(
                f"{emoji} <b>{t.get('city','?')}</b> {t.get('target_date','?')} — "
                f"{t.get('side','?').upper()} @ {entry_c}¢ — <b>${pnl:+.2f}</b>"
            )
        _send("\n".join(lines), chat_id)

    elif cmd == "/cooldowns":
        from src.core.trade_executor import get_db
        from datetime import datetime, timezone, timedelta
        one_hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        conn = get_db()
        rows = conn.execute(
            "SELECT ticker, city, settled_at FROM trades WHERE status = 'settled' "
            "AND pnl_usd < 0 AND settled_at > ? ORDER BY settled_at DESC",
            (one_hour_ago,)
        ).fetchall()
        conn.close()
        if not rows:
            _send("✅ No tickers on cooldown.", chat_id)
            return
        lines = [f"⏳ <b>{len(rows)} Ticker(s) on Cooldown</b>\n"]
        for r in rows:
            ticker, city, settled_at_str = r
            try:
                settled_at = datetime.fromisoformat(settled_at_str)
                if settled_at.tzinfo is None:
                    settled_at = settled_at.replace(tzinfo=timezone.utc)
                elapsed = (datetime.now(timezone.utc) - settled_at).total_seconds()
                remaining = max(0, (3600 - elapsed) / 60)
                remaining_str = f"{remaining:.0f} min"
            except Exception:
                remaining_str = "?"
            lines.append(f"• <b>{city}</b> <code>{ticker}</code> — {remaining_str} remaining")
        _send("\n".join(lines), chat_id)

    elif cmd == "/forecast":
        _send("📡 Fetching forecasts...", chat_id)
        try:
            from src.data.market_scanner import scan_weather_markets_public
            from src.data.weather import get_forecast_for_city
            markets = scan_weather_markets_public()
            if not markets:
                _send("⚠️ No active markets found.", chat_id)
                return
            lines = [f"🌡️ <b>Model vs Market</b> ({len(markets)} markets)\n"]
            for m in markets:
                try:
                    forecast = get_forecast_for_city(
                        series_ticker=m["series_ticker"],
                        target_date=m["target_date"],
                        threshold=m["threshold_f"],
                    )
                    if forecast.get("error") or forecast.get("n_members", 0) == 0:
                        continue
                    model_p = forecast.get("prob_above", 0) * 100
                    market_p = m.get("yes_ask", m.get("yes_bid", 0)) * 100
                    edge = model_p - market_p
                    edge_str = f"({edge:+.0f}%)"
                    flag = "⚡" if abs(edge) >= 5 else "▪️"
                    lines.append(
                        f"{flag} <b>{m['city']}</b> {m['target_date']} ≥{m['threshold_f']}°\n"
                        f"   Model: {model_p:.1f}% | Mkt: {market_p:.0f}¢ {edge_str}"
                    )
                except Exception:
                    continue
            _send("\n".join(lines), chat_id)
        except Exception as e:
            _send(f"❌ Forecast fetch failed: {e}", chat_id)

    elif cmd == "/risk":
        from src.core.trade_executor import get_trade_history, get_current_bankroll, get_daily_loss_today, get_stats
        from src.config import settings as _s
        trades = [t for t in get_trade_history(50) if t.get("status") == "open"]
        bankroll = get_current_bankroll()
        daily_pnl = get_daily_loss_today()
        daily_used_pct = abs(daily_pnl) / _s.daily_loss_limit * 100 if _s.daily_loss_limit else 0
        total_at_risk = sum(t.get("position_size_usd", 0) for t in trades)
        max_loss = sum(t.get("position_size_usd", 0) for t in trades)  # worst case all go to 0
        pct_of_bankroll = total_at_risk / bankroll * 100 if bankroll else 0
        daily_emoji = "🟢" if daily_used_pct < 50 else "🟡" if daily_used_pct < 80 else "🔴"
        _send(
            f"🛡️ <b>Risk Snapshot</b>\n\n"
            f"💰 Bankroll: <b>${bankroll:,.2f}</b>\n"
            f"📂 Open positions: <b>{len(trades)}</b>\n"
            f"💸 Total at risk: <b>${total_at_risk:,.2f}</b> ({pct_of_bankroll:.1f}% of bankroll)\n"
            f"💣 Max loss if all fail: <b>${max_loss:,.2f}</b>\n\n"
            f"{daily_emoji} Daily loss used: <b>${abs(daily_pnl):,.2f} / ${_s.daily_loss_limit:,.2f}</b> ({daily_used_pct:.0f}%)",
            chat_id,
        )

    elif cmd == "/mode":
        from src.config import settings as _s
        parts = text.strip().lower().split()
        if len(parts) == 1:
            mode = _s.trading_mode.upper()
            _send(
                f"⚙️ <b>Current Mode: {mode}</b>\n\n"
                f"To switch: /mode paper or /mode live",
                chat_id,
            )
        elif parts[1] in ("paper", "live"):
            new_mode = parts[1]
            if new_mode == _s.trading_mode:
                _send(f"Already in <b>{new_mode.upper()}</b> mode.", chat_id)
            else:
                _s.trading_mode = new_mode
                _send(f"✅ Switched to <b>{new_mode.upper()}</b> mode.", chat_id)
        else:
            _send("❌ Usage: /mode paper or /mode live", chat_id)

    elif cmd == "/logs":
        errors = bot_state.get("last_errors", [])
        if not errors:
            _send("✅ No recent errors.", chat_id)
        else:
            lines = [f"📕 <b>Recent Errors ({len(errors)})</b>\n"]
            for e in errors[-5:]:
                lines.append(f"• <code>{str(e)[:120]}</code>")
            _send("\n".join(lines), chat_id)

    elif cmd == "/history":
        from src.core.trade_executor import get_trade_history
        parts = text.strip().split()
        city_filter = parts[1].upper() if len(parts) > 1 else None
        all_trades = get_trade_history(100)
        trades = [
            t for t in all_trades
            if t.get("status") == "settled"
            and (not city_filter or t.get("city", "").upper() == city_filter)
        ][:15]
        if not trades:
            label = f" for {city_filter}" if city_filter else ""
            _send(f"📊 No settled trades{label}.", chat_id)
            return
        wins = sum(1 for t in trades if t.get("pnl_usd", 0) > 0)
        total_pnl = sum(t.get("pnl_usd", 0) for t in trades)
        label = f" — {city_filter}" if city_filter else ""
        lines = [f"📊 <b>History{label}</b> | {wins}W/{len(trades)-wins}L | P&L: ${total_pnl:+.2f}\n"]
        for t in trades:
            pnl = t.get("pnl_usd", 0)
            emoji = "✅" if pnl > 0 else "❌"
            entry_c = int(t.get("market_price", 0) * 100)
            lines.append(
                f"{emoji} <b>{t.get('city','?')}</b> {t.get('target_date','?')} "
                f"{t.get('side','?').upper()} @ {entry_c}¢ — <b>${pnl:+.2f}</b>"
            )
        _send("\n".join(lines), chat_id)

    elif cmd == "/summary":
        from src.core.trade_executor import (
            get_stats, get_daily_loss_today, get_current_bankroll,
            get_open_trades_with_current_prices, get_win_rate_by_city
        )
        from src.config import settings as _s
        from datetime import datetime, timezone
        stats = get_stats()
        bankroll = get_current_bankroll()
        daily_pnl = get_daily_loss_today()
        last_markets = bot_state.get("last_markets", [])
        open_trades = get_open_trades_with_current_prices(last_markets)
        running = bot_state.get("running", False)
        last_scan = bot_state.get("last_scan", "never")
        paused_str = " ⏸ PAUSED" if _trading_paused else ""
        status_emoji = "🟢" if running else "🔴"
        mode = _s.trading_mode.upper()
        daily_emoji = "📈" if daily_pnl >= 0 else "📉"
        daily_used_pct = abs(daily_pnl) / _s.daily_loss_limit * 100 if _s.daily_loss_limit else 0
        # Unrealized P&L across all open positions
        total_unrealized = sum(t.get('unrealized_pnl') or 0 for t in open_trades)
        total_at_risk = sum(t.get('position_size_usd', 0) for t in open_trades)
        lines = [
            f"{status_emoji} <b>Bot Summary</b> [{mode}]{paused_str}\n",
            f"💰 Bankroll: <b>${bankroll:,.2f}</b>",
            f"{daily_emoji} Today: <b>${daily_pnl:+,.2f}</b> | All-time: <b>${stats['total_pnl']:+,.2f}</b>",
            f"🎯 Win Rate: {stats['win_rate']:.1f}% ({stats['wins']}W / {stats['losses']}L)",
            f"🛡️ Daily limit used: {daily_used_pct:.0f}% (${abs(daily_pnl):,.2f} / ${_s.daily_loss_limit:,.2f})\n",
        ]
        if open_trades:
            lines.append(f"📂 <b>{len(open_trades)} Open Position(s)</b> — ${total_at_risk:.0f} at risk | uP&L: ${total_unrealized:+.2f}")
            for t in open_trades:
                entry_c = int(t.get('market_price', 0) * 100)
                contracts = t.get('contracts', 0)
                cost = t.get('position_size_usd', 0)
                current_bid = t.get('yes_bid') or 0
                current_c = int(current_bid * 100) if current_bid else 0
                vol = t.get('volume')
                vol_str = f" vol:{int(float(vol)):,}" if vol else ""
                upnl = t.get('unrealized_pnl')
                upnl_str = f" ${upnl:+.2f}" if upnl is not None else ""
                lines.append(
                    f"  • <b>{t.get('city','?')}</b> {t.get('side','?').upper()} "
                    f"{entry_c}¢→{current_c}¢ ×{contracts}{upnl_str}{vol_str}"
                )
        else:
            lines.append("📂 No open positions")
        last_signals = bot_state.get("last_signals", [])
        lines.append(f"\n📡 Last scan: {str(last_scan)[:19]} | {len(last_signals)} signal(s) | scan #{bot_state.get('scan_count',0)}")
        errors = bot_state.get("last_errors", [])
        if errors:
            lines.append(f"⚠️ Last error: <i>{str(errors[-1])[:80]}</i>")
        _send("\n".join(lines), chat_id)

    elif cmd == "/cities":
        from src.core.trade_executor import get_win_rate_by_city
        cities = get_win_rate_by_city()
        if not cities:
            _send("🏙️ No settled trades by city yet.", chat_id)
            return
        lines = [f"🏙️ <b>Performance by City</b>\n"]
        for c in cities:
            pnl_emoji = "📈" if c['total_pnl'] >= 0 else "📉"
            lines.append(
                f"{pnl_emoji} <b>{c['city']}</b> — {c['wins']}W / {c['losses']}L "
                f"({c['win_rate']:.0f}%) | P&L: <b>${c['total_pnl']:+.2f}</b>"
            )
        _send("\n".join(lines), chat_id)

    elif cmd == "/edge":
        parts = text.strip().split()
        if len(parts) < 2:
            _send("❌ Usage: /edge TICKER (e.g. /edge KXHIGH-23APR25-NY-80)", chat_id)
            return
        target_ticker = parts[1].upper()
        _send(f"📡 Checking edge for <code>{target_ticker}</code>...", chat_id)
        try:
            from src.data.market_scanner import scan_weather_markets_public
            from src.data.weather import get_forecast_for_city
            from src.core.edge_calculator import evaluate_market
            from src.core.trade_executor import get_current_bankroll
            markets = scan_weather_markets_public()
            market = next((m for m in markets if m["ticker"] == target_ticker), None)
            if not market:
                _send(f"❌ Ticker <code>{target_ticker}</code> not found in active markets.", chat_id)
                return
            bankroll = get_current_bankroll()
            forecast = get_forecast_for_city(
                series_ticker=market["series_ticker"],
                target_date=market["target_date"],
                threshold=market["threshold_f"],
            )
            if forecast.get("error") or forecast.get("n_members", 0) == 0:
                _send(f"❌ No forecast data for {target_ticker}: {forecast.get('error','no members')}", chat_id)
                return
            sig = evaluate_market(market, forecast, bankroll)
            model_p = forecast.get("prob_above", 0) * 100
            market_p = market.get("yes_ask", market.get("yes_bid", 0)) * 100
            edge_val = model_p - market_p
            if sig:
                contracts = sig.get('contracts', 0)
                cost = sig.get('position_size_usd', 0)
                win_target = round(contracts * 1.0 - cost, 2) if contracts and cost else 0
                _send(
                    f"⚡ <b>Edge Found</b> — <code>{target_ticker}</code>\n\n"
                    f"Model: {model_p:.1f}% | Market: {market_p:.0f}¢ | Edge: <b>{edge_val:+.1f}%</b>\n"
                    f"Direction: <b>{sig['direction']}</b> | Conf: {sig.get('confidence',0)*100:.1f}%\n"
                    f"💰 {contracts} contracts × {int(market_p)}¢ = ${cost:.0f} → 🏆${win_target:,.2f}",
                    chat_id,
                )
            else:
                _send(
                    f"▪️ <b>No Signal</b> — <code>{target_ticker}</code>\n\n"
                    f"Model: {model_p:.1f}% | Market: {market_p:.0f}¢ | Edge: {edge_val:+.1f}%\n"
                    f"Below entry threshold.",
                    chat_id,
                )
        except Exception as e:
            _send(f"❌ Edge check failed: {e}", chat_id)

    elif cmd == "/settings":
        from src.config import settings as _s
        mode = _s.trading_mode.upper()
        _send(
            f"⚙️ <b>Current Settings</b> [{mode}]\n\n"
            f"<b>Thresholds</b>\n"
            f"Min edge: {_s.min_edge_threshold*100:.0f}% | Min confidence: {_s.min_confidence_threshold*100:.0f}%\n"
            f"Min contract price: {_s.min_contract_price*100:.0f}¢ | Max: {_s.max_contract_price*100:.0f}¢\n"
            f"Max spread: {_s.max_spread_cents}¢ | Min volume: {_s.min_liquidity_volume}\n\n"
            f"<b>Sizing</b>\n"
            f"Max trade size: ${_s.max_trade_size:.0f} | Initial bankroll: ${_s.initial_bankroll:,.0f}\n"
            f"Max concurrent: {_s.max_concurrent_trades} | Max per city: {_s.max_trades_per_city}\n"
            f"Daily loss limit: ${_s.daily_loss_limit:,.0f}\n\n"
            f"<b>Timing</b>\n"
            f"Scan interval: {_s.scan_interval_seconds}s | Max days to expiry: {_s.max_days_to_expiry}\n"
            f"Exit loss threshold: {_s.exit_loss_threshold*100:.0f}%",
            chat_id,
        )

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
