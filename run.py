"""
Entry point for the Kalshi Weather Trading Bot.

Usage:
    python run.py              # Run continuously (scans every 5 min)
    python run.py --once       # Run a single scan cycle
    python run.py --stats      # Show current stats only
"""

import sys
from src.core.bot import run_bot, run_once, print_banner, print_stats, print_recent_trades
from src.core.trade_executor import init_db


def main():
    init_db()

    if len(sys.argv) > 1:
        arg = sys.argv[1].lower()

        if arg == "--once":
            run_once()
        elif arg == "--stats":
            print_banner()
            print_stats()
            print_recent_trades(20)
        elif arg == "--help":
            print(__doc__)
        else:
            print(f"Unknown argument: {arg}")
            print(__doc__)
    else:
        run_bot()


if __name__ == "__main__":
    main()
