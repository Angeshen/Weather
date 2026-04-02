import time
import base64
from pathlib import Path

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, utils

from src.config import settings


class KalshiClient:
    """Kalshi API client with RSA-PSS authentication."""

    def __init__(self):
        self.base_url = settings.kalshi_base_url
        self.api_key_id = settings.kalshi_api_key_id
        self._private_key = None
        self._client = httpx.Client(timeout=30.0)

    @property
    def private_key(self):
        if self._private_key is None:
            key_path = Path(settings.kalshi_private_key_path)
            if not key_path.exists():
                raise FileNotFoundError(
                    f"Kalshi private key not found at: {key_path}\n"
                    "Download it from kalshi.com -> Settings -> API Keys"
                )
            key_data = key_path.read_bytes()
            self._private_key = serialization.load_pem_private_key(key_data, password=None)
        return self._private_key

    def _sign_request(self, method: str, path: str, timestamp_ms: int) -> str:
        message = f"{timestamp_ms}{method}{path}".encode("utf-8")
        signature = self.private_key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )
        return base64.b64encode(signature).decode("utf-8")

    def _headers(self, method: str, path: str) -> dict:
        timestamp_ms = int(time.time() * 1000)
        # Kalshi requires the full path (including /trade-api/v2 prefix) to be signed
        full_path = f"/trade-api/v2{path}"
        sig = self._sign_request(method, full_path, timestamp_ms)
        return {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-SIGNATURE": sig,
            "KALSHI-ACCESS-TIMESTAMP": str(timestamp_ms),
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _get(self, path: str, params: dict = None) -> dict:
        url = f"{self.base_url}{path}"
        headers = self._headers("GET", path)
        resp = self._client.get(url, headers=headers, params=params)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, body: dict = None) -> dict:
        url = f"{self.base_url}{path}"
        headers = self._headers("POST", path)
        resp = self._client.post(url, headers=headers, json=body or {})
        resp.raise_for_status()
        return resp.json()

    # --- Public data (no auth needed) ---

    def get_markets(self, series_ticker: str = None, status: str = "open",
                    limit: int = 200, cursor: str = None) -> dict:
        params = {"limit": limit, "status": status}
        if series_ticker:
            params["series_ticker"] = series_ticker
        if cursor:
            params["cursor"] = cursor
        return self._get("/markets", params=params)

    def get_market(self, ticker: str) -> dict:
        return self._get(f"/markets/{ticker}")

    def get_orderbook(self, ticker: str) -> dict:
        return self._get(f"/markets/{ticker}/orderbook")

    def get_events(self, series_ticker: str = None, status: str = "open",
                   limit: int = 200) -> dict:
        params = {"limit": limit, "status": status}
        if series_ticker:
            params["series_ticker"] = series_ticker
        return self._get("/events", params=params)

    # --- Trading (auth required) ---

    def get_balance(self) -> dict:
        return self._get("/portfolio/balance")

    def place_order(self, ticker: str, side: str, quantity: int,
                    price_cents: int, order_type: str = "limit") -> dict:
        body = {
            "ticker": ticker,
            "action": "buy",
            "side": side,
            "count": quantity,
            "type": order_type,
        }
        if order_type == "limit":
            if side == "yes":
                body["yes_price"] = price_cents
            else:
                body["no_price"] = price_cents
        return self._post("/portfolio/orders", body=body)

    def sell_order(self, ticker: str, side: str, quantity: int, price_cents: int) -> dict:
        """Sell (exit) an existing position at the given price."""
        body = {
            "ticker": ticker,
            "action": "sell",
            "side": side,
            "count": quantity,
            "type": "limit",
        }
        if side == "yes":
            body["yes_price"] = price_cents
        else:
            body["no_price"] = price_cents
        return self._post("/portfolio/orders", body=body)

    def _delete(self, path: str) -> dict:
        url = f"{self.base_url}{path}"
        headers = self._headers("DELETE", path)
        resp = self._client.delete(url, headers=headers)
        resp.raise_for_status()
        return resp.json()

    def cancel_order(self, order_id: str) -> dict:
        """Cancel a resting order by order_id."""
        return self._delete(f"/portfolio/orders/{order_id}")

    def get_order(self, order_id: str) -> dict:
        """Get current status of an order."""
        return self._get(f"/portfolio/orders/{order_id}")

    def get_positions(self) -> dict:
        return self._get("/portfolio/positions")

    def get_orders(self, status: str = "resting") -> dict:
        return self._get("/portfolio/orders", params={"status": status})

    def close(self):
        self._client.close()
