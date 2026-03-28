"""
Main bot loop — scans Kalshi weather markets, fetches GFS ensemble forecasts,
calculates edge, and executes trades (paper or live).
"""

import time
import sys
from datetime import datetime, timezone

from rich.console import Console
from rich.table import Table
from rich.panel import Panel

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
    log_bankroll,
    init_db,
)

console = Console()


def print_banner():
    mode_color = "green" if settings.trading_mode == "paper" else "red"
    mode_label = f"[bold {mode_color}]{settings.trading_mode.upper()}[/]"

    console.print(Panel.fit(
        f"[bold cyan]Kalshi Weather Trading Bot[/]\n"
        f"Mode: {mode_label}  |  "
        f"Bankroll: [bold]${get_current_bankroll():,.2f}[/]  |  "
        f"Min Edge: {settings.min_edge_threshold:.0%}  |  "
        f"Max Trade: ${settings.max_trade_size}  |  "
        f"Kelly: {settings.kelly_fraction:.0%}  |  "
        f"Scan Every: {settings.scan_interval_seconds}s",
        title="[bold]Weather Bot[/]",
        border_style="cyan",
    ))


def print_signals_table(signals: list[dict]):
    if not signals:
        console.print("  [dim]No actionable signals found this scan.[/]")
        return

    table = Table(title="Trade Signals", show_header=True, header_style="bold magenta")
    table.add_column("City", style="cyan")
    table.add_column("Date")
    table.add_column("Threshold")
    table.add_column("Direction", style="bold")
    table.add_column("Model Prob")
    table.add_column("Market Price")
    table.add_column("Edge", style="bold green")
    table.add_column("Confidence")
    table.add_column("Size ($)")
    table.add_column("Forecast")

    for s in signals:
        edge_color = "green" if s["edge"] >= 0.15 else "yellow"
        table.add_row(
            s["city"],
            s["target_date"],
            f"{s['threshold_f']}°F",
            f"[bold]{s['direction']}[/]",
            f"{s['model_prob']:.1%}",
            f"{s['market_price']:.1%}",
            f"[{edge_color}]{s['edge']:.1%}[/]",
            f"{s['confidence']:.1%}",
            f"${s['position_size_usd']:.2f}",
            f"{s['forecast_mean']:.0f}°F ({s['forecast_min']:.0f}-{s['forecast_max']:.0f})",
        )

    console.print(table)


def print_execution_result(signal: dict, result: dict):
    if result.get("status") == "blocked":
        console.print(f"  [yellow]⚠ BLOCKED:[/] {result['reason']}")
    elif result.get("error"):
        console.print(f"  [red]✗ ERROR:[/] {result['error']}")
    else:
        mode = result.get("mode", "paper")
        trade_id = result.get("trade_id", "?")
        console.print(
            f"  [green]✓ {mode.upper()} TRADE #{trade_id}:[/] "
            f"{signal['side'].upper()} {signal['contracts']}x "
            f"{signal['ticker']} @ {signal['price_cents']}¢ "
            f"(${signal['position_size_usd']:.2f})"
        )


def print_stats():
    stats = get_stats()
    table = Table(title="Bot Statistics", show_header=False, border_style="blue")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="bold")

    table.add_row("Bankroll", f"${stats['bankroll']:,.2f}")
    table.add_row("Total Trades", str(stats["total_trades"]))
    table.add_row("Open Trades", str(stats["open_trades"]))
    table.add_row("Settled", str(stats["settled_trades"]))
    table.add_row("Wins / Losses", f"{stats['wins']} / {stats['losses']}")
    table.add_row("Win Rate", f"{stats['win_rate']:.1f}%")
    table.add_row("Total P&L", f"${stats['total_pnl']:,.2f}")

    console.print(table)


def print_recent_trades(limit: int = 10):
    trades = get_trade_history(limit)
    if not trades:
        console.print("  [dim]No trades yet.[/]")
        return

    table = Table(title="Recent Trades", show_header=True, header_style="bold")
    table.add_column("#", style="dim")
    table.add_column("Time")
    table.add_column("Ticker")
    table.add_column("City")
    table.add_column("Side")
    table.add_column("Edge")
    table.add_column("Size")
    table.add_column("Mode")
    table.add_column("Status")
    table.add_column("P&L")

    for t in trades:
        pnl_str = f"${t['pnl_usd']:.2f}" if t["pnl_usd"] else "—"
        pnl_style = "green" if (t["pnl_usd"] or 0) > 0 else "red" if (t["pnl_usd"] or 0) < 0 else "dim"
        ts = t["timestamp"][:16] if t["timestamp"] else "?"
        table.add_row(
            str(t["id"]),
            ts,
            t["ticker"],
            t["city"] or "?",
            f"{t['side'].upper()} ({t['direction']})",
            f"{t['edge']:.1%}" if t["edge"] else "?",
            f"${t['position_size_usd']:.2f}" if t["position_size_usd"] else "?",
            t["mode"],
            t["status"],
            f"[{pnl_style}]{pnl_str}[/]",
        )

    console.print(table)


def run_scan_cycle(client: KalshiClient = None):
    """Run one full scan cycle: scan markets, fetch forecasts, evaluate edge, execute."""
    now = datetime.now(timezone.utc)
    console.rule(f"[bold]Scan @ {now.strftime('%Y-%m-%d %H:%M:%S UTC')}[/]")

    # Step 1: Scan markets
    console.print("[cyan]Scanning Kalshi weather markets...[/]")
    try:
        if settings.trading_mode == "live" and client:
            markets = scan_weather_markets(client)
        else:
            markets = scan_weather_markets_public()
    except Exception as e:
        console.print(f"[red]Error scanning markets: {e}[/]")
        return

    console.print(f"  Found [bold]{len(markets)}[/] open weather markets")

    if not markets:
        return

    # Step 2: Fetch forecasts and evaluate edge
    console.print("[cyan]Fetching GFS ensemble forecasts & calculating edge...[/]")
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

        except Exception as e:
            console.print(f"  [dim]Error on {market['ticker']}: {e}[/]")
            continue

    # Step 3: Sort signals by edge (best first)
    signals.sort(key=lambda s: s["edge"], reverse=True)
    print_signals_table(signals)

    # Step 4: Execute trades
    if signals:
        console.print(f"\n[cyan]Executing top signals...[/]")
        executed = 0
        for signal in signals:
            if executed >= settings.max_concurrent_trades:
                break

            result = execute_trade(signal, client)
            print_execution_result(signal, result)

            if result.get("status") not in ("blocked", "failed"):
                executed += 1

    # Step 5: Show stats
    console.print()
    print_stats()
    print_recent_trades(5)


def run_bot():
    """Main bot loop."""
    init_db()
    log_bankroll(get_current_bankroll(), "Bot started")

    print_banner()

    client = None
    if settings.trading_mode == "live":
        try:
            client = KalshiClient()
            balance = client.get_balance()
            console.print(f"[green]✓ Connected to Kalshi. Balance: ${balance}[/]")
        except Exception as e:
            console.print(f"[red]✗ Failed to connect to Kalshi: {e}[/]")
            console.print("[yellow]Falling back to paper trading mode.[/]")
            client = None

    console.print(f"\n[bold]Bot running. Scanning every {settings.scan_interval_seconds} seconds.[/]")
    console.print("[dim]Press Ctrl+C to stop.[/]\n")

    try:
        while True:
            try:
                run_scan_cycle(client)
            except Exception as e:
                console.print(f"[red]Unhandled error in scan cycle: {e}[/]")
                console.print("[yellow]Continuing in next scan...[/]")
            console.print(f"\n[dim]Next scan in {settings.scan_interval_seconds}s...[/]\n")
            time.sleep(settings.scan_interval_seconds)
    except KeyboardInterrupt:
        console.print("\n[yellow]Bot stopped by user.[/]")
        print_stats()
    finally:
        if client:
            client.close()


def run_once():
    """Run a single scan cycle (useful for testing)."""
    init_db()
    print_banner()

    client = None
    if settings.trading_mode == "live":
        try:
            client = KalshiClient()
        except Exception:
            pass

    run_scan_cycle(client)

    if client:
        client.close()
