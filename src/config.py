import os
from pydantic_settings import BaseSettings
from pydantic import Field
from dotenv import load_dotenv
from pathlib import Path

# Load .env from project root
_project_root = Path(__file__).parent.parent
load_dotenv(_project_root / ".env")


class Settings(BaseSettings):
    # Kalshi
    kalshi_api_key_id: str = Field(default="", alias="KALSHI_API_KEY_ID")
    kalshi_private_key_path: str = Field(default="", alias="KALSHI_PRIVATE_KEY_PATH")
    kalshi_base_url: str = "https://api.elections.kalshi.com/trade-api/v2"

    # Trading mode
    trading_mode: str = Field(default="paper", alias="TRADING_MODE")

    # Risk management
    initial_bankroll: float = Field(default=5000.0, alias="INITIAL_BANKROLL")
    max_trade_size: float = Field(default=75.0, alias="MAX_TRADE_SIZE")
    daily_loss_limit: float = Field(default=250.0, alias="DAILY_LOSS_LIMIT")
    max_concurrent_trades: int = Field(default=8, alias="MAX_CONCURRENT_TRADES")
    min_edge_threshold: float = Field(default=0.05, alias="MIN_EDGE_THRESHOLD")
    kelly_fraction: float = Field(default=0.15, alias="KELLY_FRACTION")

    # Scanning
    scan_interval_seconds: int = Field(default=300, alias="SCAN_INTERVAL_SECONDS")

    # Telegram notifications (optional)
    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = Field(default="", alias="TELEGRAM_CHAT_ID")
    telegram_daily_summary_hour: int = Field(default=20, alias="TELEGRAM_DAILY_SUMMARY_HOUR")
    telegram_daily_summary_minute: int = Field(default=30, alias="TELEGRAM_DAILY_SUMMARY_MINUTE")

    # Max open trades per city (prevents over-concentration in one location)
    max_trades_per_city: int = Field(default=2, alias="MAX_TRADES_PER_CITY")

    # Trading thresholds (tunable from dashboard/env)
    min_contract_price: float = Field(default=0.05, alias="MIN_CONTRACT_PRICE")
    min_contract_price_high_edge: float = Field(default=0.01, alias="MIN_CONTRACT_PRICE_HIGH_EDGE")
    high_edge_price_threshold: float = Field(default=0.20, alias="HIGH_EDGE_PRICE_THRESHOLD")
    max_contract_price: float = Field(default=0.65, alias="MAX_CONTRACT_PRICE")
    max_spread_cents: int = Field(default=15, alias="MAX_SPREAD_CENTS")
    min_liquidity_volume: int = Field(default=50, alias="MIN_LIQUIDITY_VOLUME")
    exit_loss_threshold: float = Field(default=0.20, alias="EXIT_LOSS_THRESHOLD")
    min_confidence_threshold: float = Field(default=0.65, alias="MIN_CONFIDENCE_THRESHOLD")
    max_days_to_expiry: int = Field(default=2, alias="MAX_DAYS_TO_EXPIRY")
    open_meteo_api_key: str = Field(default="", alias="OPEN_METEO_API_KEY")

    # Weather market tickers — only series confirmed to exist on Kalshi
    # Kalshi currently only offers high temp for 5 cities (NY, CHI, MIA, LAX, DEN)
    weather_series: list[str] = [
        "KXHIGHNY", "KXHIGHCHI", "KXHIGHMIA", "KXHIGHLAX", "KXHIGHDEN",
    ]

    class Config:
        env_file = ".env"
        populate_by_name = True


# City data shared across all market types
_CITIES = {
    "NY": {"name": "New York City", "lat": 40.7128, "lon": -74.0060, "nws_station": "KNYC"},
    "CHI": {"name": "Chicago", "lat": 41.8781, "lon": -87.6298, "nws_station": "KORD"},
    "MIA": {"name": "Miami", "lat": 25.7617, "lon": -80.1918, "nws_station": "KMIA"},
    "LAX": {"name": "Los Angeles", "lat": 34.0522, "lon": -118.2437, "nws_station": "KLAX"},
    "DEN": {"name": "Denver", "lat": 39.7392, "lon": -104.9903, "nws_station": "KDEN"},
    "SEA": {"name": "Seattle", "lat": 47.6062, "lon": -122.3321, "nws_station": "KSEA"},
    "DAL": {"name": "Dallas", "lat": 32.7767, "lon": -96.7970, "nws_station": "KDFW"},
    "ATL": {"name": "Atlanta", "lat": 33.7490, "lon": -84.3880, "nws_station": "KATL"},
    "PHX": {"name": "Phoenix", "lat": 33.4484, "lon": -112.0740, "nws_station": "KPHX"},
}

# Map every series ticker to its city + market type
CITY_CONFIG = {}
for _suffix, _city in _CITIES.items():
    CITY_CONFIG[f"KXHIGH{_suffix}"] = {**_city, "market_type": "high_temp"}
    CITY_CONFIG[f"KXLOW{_suffix}"] = {**_city, "market_type": "low_temp"}
    CITY_CONFIG[f"KXRAIN{_suffix}"] = {**_city, "market_type": "precipitation"}

# Known series prefixes for ticker parsing
SERIES_PREFIXES = ["KXHIGH", "KXLOW", "KXRAIN"]


settings = Settings()
