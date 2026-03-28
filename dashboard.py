"""
Launch the Kalshi Weather Bot web dashboard.
Opens a browser-friendly GUI at http://localhost:5050
"""

import os
import webbrowser
import threading
from src.web.app import app
from src.core.trade_executor import init_db

# Bind to 0.0.0.0 on server (set BIND_HOST=0.0.0.0 in .env or environment)
# Defaults to 127.0.0.1 for local development
HOST = os.environ.get("BIND_HOST", "0.0.0.0")
PORT = int(os.environ.get("BIND_PORT", "5050"))


def open_browser():
    """Open the dashboard in the default browser after a short delay."""
    import time
    time.sleep(1.5)
    webbrowser.open(f"http://localhost:{PORT}")


if __name__ == "__main__":
    init_db()
    print(f"\n  Kalshi Weather Bot Dashboard")
    print(f"  http://{'localhost' if HOST == '127.0.0.1' else HOST}:{PORT}")
    print("  Press Ctrl+C to stop\n")

    # Auto-start the bot loop so it runs without needing to click "Start Bot"
    from src.web.app import bot_state, bot_loop
    bot_state["running"] = True
    bot_state["thread"] = threading.Thread(target=bot_loop, daemon=True)
    bot_state["thread"].start()
    print("  Bot loop started automatically.\n")

    threading.Thread(target=open_browser, daemon=True).start()
    app.run(host=HOST, port=PORT, debug=False)
