# Kalshi Weather Trading Bot

Automated weather prediction market bot for Kalshi. Uses **31-member GFS ensemble forecasts** from Open-Meteo to find mispriced weather markets and trade with edge.

## Supported Markets

| Type | Series | Example |
|------|--------|---------|
| **High Temperature** | KXHIGH | "Will NYC high be above 72°F?" |
| **Low Temperature** | KXLOW | "Will Chicago low drop below 35°F?" |
| **Precipitation** | KXRAIN | "Will Miami get >0.50 inches of rain?" |

**Cities:** New York City, Chicago, Miami, Los Angeles, Denver

## How It Works

1. **Scans** Kalshi for open weather markets across all 3 types and 5 cities
2. **Fetches** 31-member GFS ensemble forecasts from Open-Meteo (free, no API key)
3. **Calculates** probability by counting how many ensemble members exceed the threshold
4. **Compares** model probability vs market price to find edge (min 8%)
5. **Sizes** positions using fractional Kelly criterion (conservative 15%)
6. **Executes** trades (paper or live) with built-in risk management

## Quick Start

### 1. Install Python & Dependencies

```bash
pip install -r requirements.txt
```

### 2. Get Your Kalshi API Key

1. Log into **kalshi.com**
2. Go to **Settings** → **API Keys**
3. Click **Create API Key**
4. Save the **API Key ID** (a string)
5. Download the **Private Key** (`.pem` file) — save it somewhere safe

### 3. Configure

Edit `.env` with your credentials:

```
KALSHI_API_KEY_ID=your-api-key-id-here
KALSHI_PRIVATE_KEY_PATH=C:\Users\CPecoraro\kalshi-key.pem
TRADING_MODE=paper
```

### 4. Launch the Dashboard (Recommended)

```bash
python dashboard.py
```

Opens a web GUI at **http://localhost:5050** with:
- **Stats cards** — bankroll, P&L, win rate, open trades at a glance
- **Scan Now** button — run a manual market scan with one click
- **Start/Stop Bot** — toggle auto-scanning (every 5 min)
- **Paper/Live toggle** — switch modes from the dashboard (with confirmation)
- **Trade Signals** table — edge, confidence, position size for each signal
- **Open Markets** table — all active Kalshi weather markets with prices
- **Trade History** — every trade the bot has placed
- **Activity Log** — real-time event log
- **Config panel** — view current risk settings

### 5. Or Use the CLI

```bash
# Continuous scanning (every 5 min)
python run.py

# Single scan (for testing)
python run.py --once

# View stats only
python run.py --stats
```

## Trading Modes

- **paper** — Logs trades to SQLite without placing real orders. Start here.
- **live** — Places real orders on Kalshi via API.

Switch modes from the dashboard toggle or by editing `TRADING_MODE` in `.env`.

## Risk Management

| Parameter | Default | Description |
|-----------|---------|-------------|
| Min Edge | 8% | Only trade when model disagrees with market by 8%+ |
| Kelly Fraction | 15% | Conservative position sizing |
| Max Trade | $75 | Per-trade cap |
| Daily Loss Limit | $250 | Stop trading if daily losses exceed this |
| Max Concurrent | 5 | Max open trades at once |

## Settlement

Markets settle based on the **NWS Daily Climate Report** — not AccuWeather, iPhone weather, etc.

## Running on Another Computer

### Option A: One-Command Setup (Windows)

1. Copy the entire `kalshi-weather-bot` folder to the new machine (USB, zip, git clone, etc.)
2. Double-click **`setup.bat`** — it will:
   - Install Python 3.12 if missing (via winget)
   - Install all pip dependencies
   - Create a `.env` file from the template if one doesn't exist
3. Edit `.env` with your Kalshi API key and `.pem` file path
4. Run `python dashboard.py`

### Option B: Manual Setup (Any OS)

```bash
# 1. Copy the project folder to the new machine

# 2. Install Python 3.12+
#    Windows:  winget install Python.Python.3.12
#    Mac:      brew install python@3.12
#    Linux:    sudo apt install python3.12

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy your .pem key file to the new machine

# 5. Edit .env with your credentials
#    KALSHI_API_KEY_ID=your-key-id
#    KALSHI_PRIVATE_KEY_PATH=/path/to/your/kalshi-key.pem

# 6. Launch
python dashboard.py
```

### What to Transfer

| File/Folder | Required? | Notes |
|-------------|-----------|-------|
| Entire `kalshi-weather-bot/` folder | Yes | All source code |
| Your `.pem` private key file | Yes | Kalshi API authentication |
| `.env` | Optional | Or create fresh from `.env.example` |
| `data/trades.db` | Optional | Only if you want trade history carried over |

## Project Structure

```
kalshi-weather-bot/
├── dashboard.py            # Launch web GUI (http://localhost:5050)
├── run.py                  # CLI entry point
├── .env                    # Your API credentials (edit this)
├── .env.example            # Template for .env
├── requirements.txt        # Python dependencies
├── README.md
│
├── src/
│   ├── config.py           # Settings + city configuration
│   │
│   ├── core/               # Bot logic
│   │   ├── bot.py          # Main bot loop + CLI dashboard
│   │   ├── edge_calculator.py  # Edge calculation + Kelly sizing
│   │   └── trade_executor.py   # Paper/live execution + SQLite tracking
│   │
│   ├── data/               # External data sources
│   │   ├── kalshi_client.py    # Kalshi API client (RSA-PSS auth)
│   │   ├── market_scanner.py   # Scans KXHIGH/KXLOW/KXRAIN markets
│   │   └── weather.py          # Open-Meteo GFS ensemble forecasts
│   │
│   └── web/                # Web dashboard
│       ├── app.py              # Flask API routes
│       ├── templates/          # HTML dashboard
│       └── static/             # CSS/JS assets
│
└── data/
    └── trades.db           # SQLite database (auto-created)
```
