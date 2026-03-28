# Kalshi Weather Trading Bot

Automated weather prediction market bot for Kalshi. Uses **multi-model ensemble forecasts** (GFS, ECMWF, ICON) from Open-Meteo to find mispriced weather markets and trade with edge.

**Live Dashboard:** http://159.223.129.65:5050

## Supported Markets

| Type | Series | Example |
|------|--------|---------|
| **High Temperature** | KXHIGH | "Will NYC high be above 72°F?" |
| **Low Temperature** | KXLOW | "Will Chicago low drop below 35°F?" |
| **Precipitation** | KXRAIN | "Will Miami get >0.50 inches of rain?" |

**Cities:** New York City, Chicago, Miami, Los Angeles, Denver

## How It Works

1. **Scans** Kalshi for open weather markets across all 3 types and 5 cities
2. **Fetches** multi-model ensemble forecasts (GFS + ECMWF + ICON) from Open-Meteo
3. **Calculates** probability by counting how many ensemble members exceed the threshold
4. **Filters** for ultra-high confidence (85%+) with minimum 40 ensemble members
5. **Compares** model probability vs market price to find edge (min 10%)
6. **Sizes** positions using fractional Kelly criterion (20%) with $150 max per trade
7. **Executes** trades (paper or live) with built-in risk management
8. **Sends** Telegram notifications for trades, settlements, and daily summaries

## Strategy Parameters

| Parameter | Value | Description |
|-----------|-------|-------------|
| Confidence Threshold | 85% | Only trade when 92.5%+ of ensemble members agree |
| Min Edge | 10% | Model must disagree with market by 10%+ |
| Max Contract Price | 55¢ | Only buy cheap contracts for good risk/reward |
| Min Contract Price | 8¢ | Avoid near-zero liquidity traps |
| Min Ensemble Members | 40 | Need enough data for reliable probability |
| Kelly Fraction | 20% | Position sizing (conservative fractional Kelly) |
| Max Trade Size | $150 | Per-trade cap |
| Daily Loss Limit | $400 | Stop trading if daily losses exceed this |
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

# Change edge threshold to 8%
sed -i 's/MIN_EDGE_THRESHOLD=.*/MIN_EDGE_THRESHOLD=0.08/' /opt/kalshi-bot/.env && systemctl restart kalshi-bot

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
MIN_EDGE_THRESHOLD=0.10
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
MIN_EDGE_THRESHOLD=0.10
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
| `MIN_EDGE_THRESHOLD` | `0.10` | Minimum edge to take a trade (10% = 0.10) |
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
- **Run Backtest** — Click "Run Backtest" to test the strategy against historical weather data. Uses real observed temperatures + GFS error distribution.
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

- **Web Dashboard** — Real-time stats, trade history, equity curve, activity log
- **Telegram Notifications** — Trade alerts, risk warnings, daily P&L summaries, settlement results
- **Auto-Settlement** — Checks NWS actual weather data and settles trades automatically
- **Historical Backtest** — Tests strategy against real observed weather + GFS accuracy model
- **Multi-Model Ensemble** — Combines GFS, ECMWF, and ICON for better accuracy
- **Equity Curve** — Visual chart of bankroll over time
- **Trade Notes** — Add notes to individual trades
- **Mobile-Friendly** — Dashboard works on phones

## Settlement

Markets settle based on the **NWS Daily Climate Report** — not AccuWeather, iPhone weather, etc.

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
│   │   ├── bot.py             # Main bot loop
│   │   ├── edge_calculator.py # Edge calculation + Kelly sizing
│   │   ├── trade_executor.py  # Paper/live execution + SQLite
│   │   ├── backtest.py        # Historical backtest engine
│   │   ├── settlement.py      # Auto-settlement checker
│   │   └── notifications.py   # Telegram notifications
│   │
│   ├── data/
│   │   ├── kalshi_client.py   # Kalshi API client (EC key auth)
│   │   ├── market_scanner.py  # Scans KXHIGH/KXLOW/KXRAIN markets
│   │   └── weather.py         # Multi-model ensemble forecasts
│   │
│   └── web/
│       ├── app.py             # Flask API routes
│       └── templates/         # Dashboard HTML
│
└── trades.db                  # SQLite database (auto-created)
```
