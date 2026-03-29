# Kalshi Weather Trading Bot

Automated weather prediction market bot for Kalshi. Uses **multi-model ensemble forecasts** (GFS, ECMWF, ICON) from Open-Meteo to find mispriced weather markets and trade with edge.

**Live Dashboard:** http://159.223.129.65:5050

## Supported Markets

Kalshi currently offers **high temperature** markets for 5 cities. The bot auto-discovers which series are active on startup — if Kalshi expands to new cities or market types, it picks them up automatically.

| City | Series |
|------|--------|
| New York City | KXHIGHNY |
| Chicago | KXHIGHCHI |
| Miami | KXHIGHMIA |
| Los Angeles | KXHIGHLAX |
| Denver | KXHIGHDEN |

## How It Works

1. **Auto-discovers** which Kalshi weather series have open markets (re-checks every 6 hours)
2. **Fetches** multi-model ensemble forecasts (GFS + ECMWF + ICON) from Open-Meteo
3. **Calculates** probability by counting how many ensemble members exceed the threshold
4. **Tracks confidence trend** across scans — rising vs falling ensemble agreement
5. **Filters** for strong confidence (65%+) with minimum 40 ensemble members
6. **Compares** model probability vs market price to find edge (min 5%)
7. **Sizes** positions using fractional Kelly criterion (20%) with $150 max, scaled down for near-expiry markets
8. **Executes** trades with per-city exposure limits (max 2 open per city)
9. **Monitors** positions — exits automatically if a position loses 20%+ of its value
10. **Settles** trades using Kalshi's official expiration value only (no premature settlements)
11. **Sends** Telegram notifications for trades, settlements, alerts, and summaries
12. **Accepts** Telegram commands to check status, trigger scans, pause/resume trading

## Strategy Parameters

| Parameter | Value | Description |
|-----------|-------|-------------|
| Confidence Threshold | 65% | Minimum ensemble agreement to trade |
| Min Edge | 5% | Model must disagree with market by 5%+ |
| Max Contract Price | 65¢ | Only buy contracts with reasonable risk/reward |
| Min Contract Price | 5¢ | Avoid near-zero liquidity traps |
| Min Ensemble Members | 40 | Need enough data for reliable probability |
| Kelly Fraction | 20% | Position sizing (conservative fractional Kelly) |
| Max Trade Size | $150 | Per-trade cap |
| Max Concurrent Trades | 8 | Total open positions at once |
| Max Trades Per City | 2 | Prevents over-concentration in one location |
| Daily Loss Limit | $400 | Stop trading if daily losses exceed this |
| Exit Loss Threshold | 20% | Auto-exit position if it loses 20%+ of value |
| Scan Interval | 2 min | How often the bot checks for new opportunities |

---

## Quick Start (Local)

### 1. Install & Configure

```bash
pip install -r requirements.txt
```

Edit `.env` with your Kalshi API key, private key path, and Telegram credentials.

### 2. Launch

Double-click **`start.bat`** or run:
```bash
python dashboard.py
```

Dashboard opens at **http://localhost:5050**.

### 3. Stop

Double-click **`stop.bat`** to kill the bot.

---

## Server Deployment (DigitalOcean)

The bot runs 24/7 on a DigitalOcean droplet at **159.223.129.65**.

### Server Details

- **Provider:** DigitalOcean ($6/month)
- **Region:** NYC1
- **OS:** Ubuntu 24.04
- **Bot Location:** `/opt/kalshi-bot/`
- **Service:** `kalshi-bot.service` (auto-restarts on crash/reboot)

### Accessing the Dashboard

Open **http://159.223.129.65:5050** from any browser (desktop or phone).

### Server Management (via DigitalOcean Console)

```bash
# View bot status
systemctl status kalshi-bot

# Restart the bot
systemctl restart kalshi-bot

# Stop the bot
systemctl stop kalshi-bot

# View recent logs (last 50 lines)
journalctl -u kalshi-bot --no-pager -n 50

# View live logs (streaming)
journalctl -u kalshi-bot -f
```

---

## Pushing Updates

### Step 1: Push code from your PC

Double-click **`update_server.bat`** — this commits and pushes your changes to GitHub.

Or manually:
```bash
git add -A
git commit -m "description of change"
git push origin main
```

### Step 2: Pull on the server

Open the **DigitalOcean Console** and run:
```bash
cd /opt/kalshi-bot && git pull && systemctl restart kalshi-bot
```

That's it — bot is updated and restarted.

### Verifying the Server Has Your Latest Code

After pushing, confirm the server is running the correct version:

```bash
# Check the last 3 commits on the server — should match what you pushed
cd /opt/kalshi-bot && git log --oneline -3

# Spot-check a specific value in the code (e.g. confidence threshold)
grep "confidence <" /opt/kalshi-bot/src/core/edge_calculator.py
# Expected: if confidence < 0.65:

# Check the current .env values on the server
cat /opt/kalshi-bot/.env

# Confirm the bot restarted cleanly after the pull
systemctl status kalshi-bot
```

If `git log` shows an old commit, the pull didn't run — re-run:
```bash
cd /opt/kalshi-bot && git pull && systemctl restart kalshi-bot
```

### Changing a Single Setting on the Server

Use `sed` to update one value without touching your keys:

```bash
# Change a setting (replace SETTING_NAME and new_value)
sed -i 's/SETTING_NAME=.*/SETTING_NAME=new_value/' /opt/kalshi-bot/.env && systemctl restart kalshi-bot
```

Common examples:
```bash
# Change max trade size to $200
sed -i 's/MAX_TRADE_SIZE=.*/MAX_TRADE_SIZE=200/' /opt/kalshi-bot/.env && systemctl restart kalshi-bot

# Change edge threshold to 5%
sed -i 's/MIN_EDGE_THRESHOLD=.*/MIN_EDGE_THRESHOLD=0.05/' /opt/kalshi-bot/.env && systemctl restart kalshi-bot

# Change daily loss limit to $500
sed -i 's/DAILY_LOSS_LIMIT=.*/DAILY_LOSS_LIMIT=500/' /opt/kalshi-bot/.env && systemctl restart kalshi-bot

# Switch to live trading
sed -i 's/TRADING_MODE=.*/TRADING_MODE=live/' /opt/kalshi-bot/.env && systemctl restart kalshi-bot
```

View current settings:
```bash
cat /opt/kalshi-bot/.env
```

### Full .env Reset (only if needed)

If you need to rewrite the whole `.env` (e.g. new API key):
```bash
cat > /opt/kalshi-bot/.env << 'EOF'
KALSHI_API_KEY_ID=your_key_here
KALSHI_PRIVATE_KEY_PATH=/opt/kalshi-bot/kalshi-key.pem
TRADING_MODE=paper
INITIAL_BANKROLL=5000
SCAN_INTERVAL_SECONDS=120
MIN_EDGE_THRESHOLD=0.05
MAX_TRADE_SIZE=150
DAILY_LOSS_LIMIT=400
KELLY_FRACTION=0.20
TELEGRAM_BOT_TOKEN=your_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
EOF
systemctl restart kalshi-bot
```

### Updating the Private Key

```bash
cat > /opt/kalshi-bot/kalshi-key.pem << 'KEYEOF'
-----BEGIN EC PRIVATE KEY-----
your key contents here
-----END EC PRIVATE KEY-----
KEYEOF
systemctl restart kalshi-bot
```

---

## Configuration Settings

All settings are in the `.env` file. To change them:

### Locally (your PC)

Edit `.env` in the project folder, then restart with `start.bat`.

### On the Server

Run this in the DigitalOcean Console, changing whichever values you need:

```bash
cat > /opt/kalshi-bot/.env << 'EOF'
KALSHI_API_KEY_ID=your_key_here
KALSHI_PRIVATE_KEY_PATH=/opt/kalshi-bot/kalshi-key.pem
TRADING_MODE=paper
INITIAL_BANKROLL=5000
SCAN_INTERVAL_SECONDS=120
MIN_EDGE_THRESHOLD=0.05
MAX_TRADE_SIZE=150
DAILY_LOSS_LIMIT=400
KELLY_FRACTION=0.20
TELEGRAM_BOT_TOKEN=your_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
EOF
systemctl restart kalshi-bot
```

### Available Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `KALSHI_API_KEY_ID` | — | Your Kalshi API key ID |
| `KALSHI_PRIVATE_KEY_PATH` | — | Path to your `.pem` private key |
| `TRADING_MODE` | `paper` | `paper` (simulated) or `live` (real money) |
| `INITIAL_BANKROLL` | `5000` | Starting bankroll in dollars |
| `SCAN_INTERVAL_SECONDS` | `120` | How often the bot scans (in seconds) |
| `MIN_EDGE_THRESHOLD` | `0.05` | Minimum edge to take a trade (5% = 0.05) |
| `MAX_TRADE_SIZE` | `150` | Max dollars per trade |
| `DAILY_LOSS_LIMIT` | `400` | Bot stops trading after losing this much in a day |
| `KELLY_FRACTION` | `0.20` | Position sizing aggressiveness (0.20 = 20% Kelly) |
| `TELEGRAM_BOT_TOKEN` | — | Telegram bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | — | Your Telegram chat ID |

### Common Adjustments

- **More trades, lower win rate:** Lower `MIN_EDGE_THRESHOLD` (e.g. `0.06`)
- **Fewer trades, higher win rate:** Raise `MIN_EDGE_THRESHOLD` (e.g. `0.15`)
- **Bigger positions:** Increase `MAX_TRADE_SIZE` and `KELLY_FRACTION`
- **More conservative:** Lower `MAX_TRADE_SIZE`, `KELLY_FRACTION`, and `DAILY_LOSS_LIMIT`
- **Scan more often:** Lower `SCAN_INTERVAL_SECONDS` (min ~60)
- **Go live:** Change `TRADING_MODE` to `live` (make sure you've paper traded first!)

---

## Testing & Verification

### From the Dashboard (http://159.223.129.65:5050)

- **Test Telegram** — Click the "Test Telegram" button. You should get a message on your phone.
- **Start Bot** — Click "Start Bot" (in paper mode). Check the activity log for scan results. If the Kalshi API key is wrong, you'll see an error here.
- **Run Backtest** — Click "Run Backtest" to test the strategy against historical weather data. Fetches real GFS ensemble members from Open-Meteo archive for each historical date, falls back to simulated ensemble if unavailable. Uses the same probability logic as the live bot.
- **Scan Now** — Click "Scan Now" for a one-time market scan.
- **Settlement Check** — Click "Check Settlements" to verify auto-settlement works.

### From the Server Console

```bash
# Check if the bot is running
systemctl status kalshi-bot

# View recent activity
journalctl -u kalshi-bot --no-pager -n 30

# Check if port 5050 is listening
ss -tlnp | grep 5050

# Test that the dashboard responds
curl http://localhost:5050
```

---

## Trading Modes

- **paper** — Logs trades to SQLite without placing real orders. **Start here.**
- **live** — Places real orders on Kalshi via API.

Switch modes from the dashboard toggle or by editing `TRADING_MODE` in `.env`.

## Features

- **Web Dashboard** — Real-time stats, trade history with unrealized P&L + win profit, equity curve, city performance breakdown, activity log
- **Confidence Trend** — Signals show ↑ Rising / ↓ Falling / → Stable trend based on successive scans
- **Auto-Discovery** — Detects active Kalshi series on startup; re-checks every 6 hours for new markets
- **Multi-Model Ensemble** — Combines GFS, ECMWF, and ICON (122+ members) for better probability accuracy
- **Days-to-Expiry Sizing** — Position size scaled down for near-expiry markets (less time = less conviction)
- **Per-City Exposure Limits** — Max 2 open trades per city to prevent over-concentration
- **Auto-Exit Logic** — Sells position if market moves 20%+ against it to cut losses early
- **Duplicate Prevention** — Never opens a second position on the same ticker
- **Auto-Settlement** — Settles using Kalshi's official expiration value only (no premature settlements)
- **Historical Backtest** — Uses real GFS ensemble archive data for historical dates, falls back to simulated if unavailable
- **Telegram Notifications** — Full suite: trade alerts, settlements, morning ping, confidence spikes, blocked signals, daily + weekly summaries
- **Telegram Commands** — Control the bot and check status directly from Telegram chat
- **Mobile-Friendly** — Dashboard works on phones

## Telegram Commands

Message your Telegram bot directly to control the bot without opening the dashboard:

| Command | Description |
|---------|-------------|
| `/help` | List all available commands |
| `/status` | Bankroll, P&L, win rate, open trades, last scan time |
| `/scan` | Trigger a manual market scan right now |
| `/trades` | List all currently open positions |
| `/pause` | Stop the bot from opening new trades |
| `/resume` | Re-enable trade execution after a pause |

> Commands only work from your configured `TELEGRAM_CHAT_ID` — anyone else gets rejected.

## Telegram Notifications

| Alert | When it fires |
|-------|--------------|
| 🔔 Trade Placed | Every new trade opened |
| ✅/❌ Settlement | When a trade settles (win or loss) |
| ⚡ Early Exit | When bot auto-exits a losing position (20%+ loss) |
| 🚫 Blocked Signal | When edge > 20% signal is blocked by risk limits |
| ⚡ Confidence Spike | When ensemble confidence jumps to 80%+ on a ticker |
| ☀️ Morning Ping | First scan each day — bankroll + markets watched |
| 📊 Daily Summary | Every day at configured time — includes unrealized P&L, streak, day-over-day change |
| 📅 Weekly Summary | Every Sunday — week's trades, W/L, P&L |
| 🟢/🔴 Bot Status | When bot starts or stops |

## Settlement

Markets settle based on **Kalshi's official expiration value** — the same value Kalshi uses to resolve the market. The bot never uses Open-Meteo or external sources to settle (avoids premature settlement on unfinalized same-day data).

## Project Structure

```
kalshi-weather-bot/
├── dashboard.py               # Launch web GUI
├── run.py                     # CLI entry point
├── start.bat                  # Start bot (kills existing, opens browser)
├── stop.bat                   # Stop bot
├── update_server.bat          # Push updates to GitHub + server
├── .env                       # Credentials (git-ignored)
├── .env.example               # Template for .env
├── requirements.txt           # Python dependencies
│
├── src/
│   ├── config.py              # Settings + city configuration
│   │
│   ├── core/
│   │   ├── edge_calculator.py # Edge calculation + Kelly sizing + days-to-expiry scaling
│   │   ├── trade_executor.py  # Paper/live execution + SQLite + exit logic + win rate by city
│   │   ├── backtest.py        # Historical backtest (real GFS ensemble archive)
│   │   ├── settlement.py      # Auto-settlement using Kalshi official expiration value
│   │   ├── notifications.py   # Telegram notifications (all alert types + summaries)
│   │   └── telegram_commands.py  # Telegram command listener (/status, /scan, /pause, etc.)
│   │
│   ├── data/
│   │   ├── kalshi_client.py   # Kalshi API client (RSA-PSS auth, buy + sell orders)
│   │   ├── market_scanner.py  # Scans markets + auto-discovers active series
│   │   └── weather.py         # Multi-model ensemble forecasts (GFS + ECMWF + ICON)
│   │
│   └── web/
│       ├── app.py             # Flask API routes + bot loop + confidence trend tracking
│       └── templates/         # Dashboard HTML (unrealized P&L, win profit, city stats, trends)
│
└── trades.db                  # SQLite database (auto-created)
```
