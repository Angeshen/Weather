"""
Launch the Kalshi Weather Bot web dashboard.
Opens a browser-friendly GUI at http://localhost:5050
"""

import webbrowser
import threading
from src.web.app import app
from src.core.trade_executor import init_db


def open_browser():
    """Open the dashboard in the default browser after a short delay."""
    import time
    time.sleep(1.5)
    webbrowser.open("http://localhost:5050")


if __name__ == "__main__":
    init_db()
    print("\n  Kalshi Weather Bot Dashboard")
    print("  http://localhost:5050")
    print("  Press Ctrl+C to stop\n")

    threading.Thread(target=open_browser, daemon=True).start()
    app.run(host="127.0.0.1", port=5050, debug=False)
