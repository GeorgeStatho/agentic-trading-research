import os
import sys
import json
from typing import Any, Dict, Optional
from urllib.parse import urlencode
import requests


class AlpacaOptionsTrading:
    """Lightweight wrapper around Alpaca's options trading REST endpoints."""

    def __init__(self,
                 key_id: str,
                 secret_key: str,
                 base_trading_url: str = "https://paper-api.alpaca.markets",
                 dry_run: bool = True) -> None:
        """Establish a session with Alpaca using the provided credentials."""
        self.base_trading_url = base_trading_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "APCA-API-KEY-ID": key_id,
            "APCA-API-SECRET-KEY": secret_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        self.dry_run = dry_run

    def _url(self, path: str) -> str:
        """Resolve a relative path into a fully qualified API URL."""
        return f"{self.base_trading_url}{path}"

    def _request(self, method: str, path: str, params: Optional[Dict[str, Any]] = None,
                 payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Send an HTTP request to Alpaca, with optional dry-run logging."""
        url = self._url(path)
        if params:
            url = f"{url}?{urlencode(params, doseq=True)}"

        if self.dry_run and method.upper() in {"POST", "PATCH", "DELETE"}:
            print(f"DRY-RUN {method} {url}")
            if payload:
                print(json.dumps(payload, indent=2))
            return {"dry_run": True, "method": method, "url": url, "payload": payload}

        resp = self.session.request(method=method, url=url, json=payload, timeout=60)
        if resp.status_code >= 400:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text}")
        if resp.text.strip() == "":
            return {}
        return resp.json()

    # Account and permissions
    def get_account(self) -> Dict[str, Any]:
        """Fetch the account profile, buying power, and status details."""
        return self._request("GET", "/v2/account")

    # Contracts and chains (discover tradable options)
    def list_option_contracts(self,
                              underlying: str,
                              expiration: Optional[str] = None,
                              call_put: Optional[str] = None,
                              strike_from: Optional[float] = None,
                              strike_to: Optional[float] = None,
                              limit: int = 100) -> Dict[str, Any]:
        """Return option contracts filtered by underlying, strikes, expiry, and type."""
        params: Dict[str, Any] = {"underlying_symbol": underlying, "limit": limit}
        if expiration:
            params["expiration_date"] = expiration  # YYYY-MM-DD
        if call_put:
            params["type"] = call_put.upper()  # C or P
        if strike_from is not None:
            params["strike_price_gte"] = strike_from
        if strike_to is not None:
            params["strike_price_lte"] = strike_to
        # Endpoint name can vary by API version; this is the commonly used path
        return self._request("GET", "/v2/options/contracts", params=params)

    def get_option_chain(self,
                          underlying: str,
                          expiration: Optional[str] = None,
                          limit: int = 1000) -> Dict[str, Any]:
        """Return a consolidated option chain for the supplied underlying symbol."""
        params: Dict[str, Any] = {"underlying_symbol": underlying, "limit": limit}
        if expiration:
            params["expiration_date"] = expiration
        # Some deployments expose a dedicated chain endpoint; if unavailable,
        # contracts endpoint with filters serves the same purpose
        try:
            return self._request("GET", "/v2/options/chain", params=params)
        except RuntimeError:
            return self._request("GET", "/v2/options/contracts", params=params)

    # Orders (single-leg)
    def place_option_order(self,
                           option_symbol_osi: str,
                           qty: int,
                           side: str,
                           order_type: str,
                           time_in_force: str,
                           limit_price: Optional[float] = None,
                           client_order_id: Optional[str] = None,
                           position_intent: Optional[str] = None,
                           extended_hours: Optional[bool] = None) -> Dict[str, Any]:
        """Submit a single-leg options order to Alpaca."""
        body: Dict[str, Any] = {
            "symbol": option_symbol_osi,  # OSI, e.g., AAPL230616C00175000
            "qty": qty,
            "side": side.lower(),        # buy or sell
            "type": order_type.lower(),  # market or limit
            "time_in_force": time_in_force.lower(),  # day, gtc, etc.
        }
        if limit_price is not None:
            body["limit_price"] = limit_price
        if client_order_id:
            body["client_order_id"] = client_order_id
        if position_intent:
            body["position_intent"] = position_intent.upper()  # OPEN or CLOSE
        if extended_hours is not None:
            body["extended_hours"] = bool(extended_hours)
        return self._request("POST", "/v2/options/orders", payload=body)

    def list_option_orders(self,
                           status: Optional[str] = None,
                           after: Optional[str] = None,
                           until: Optional[str] = None,
                           limit: int = 50) -> Dict[str, Any]:
        """Retrieve recent option orders, optionally filtered by time window and status."""
        params: Dict[str, Any] = {"limit": limit}
        if status:
            params["status"] = status
        if after:
            params["after"] = after
        if until:
            params["until"] = until
        return self._request("GET", "/v2/options/orders", params=params)

    def get_option_order(self, order_id: str) -> Dict[str, Any]:
        """Fetch the latest state for a specific option order."""
        return self._request("GET", f"/v2/options/orders/{order_id}")

    def replace_option_order(self, order_id: str, limit_price: Optional[float] = None,
                              qty: Optional[int] = None) -> Dict[str, Any]:
        """Modify an existing option order, such as adjusting price or quantity."""
        payload: Dict[str, Any] = {}
        if limit_price is not None:
            payload["limit_price"] = limit_price
        if qty is not None:
            payload["qty"] = qty
        return self._request("PATCH", f"/v2/options/orders/{order_id}", payload=payload)

    def cancel_option_order(self, order_id: str) -> Dict[str, Any]:
        """Cancel a single option order by its identifier."""
        return self._request("DELETE", f"/v2/options/orders/{order_id}")

    def cancel_all_option_orders(self) -> Dict[str, Any]:
        """Cancel every open options order associated with the account."""
        return self._request("DELETE", "/v2/options/orders")

    # Positions
    def list_option_positions(self) -> Dict[str, Any]:
        """Return all currently open option positions."""
        return self._request("GET", "/v2/options/positions")

    def get_option_position(self, option_symbol_osi: str) -> Dict[str, Any]:
        """Retrieve position details for a specific OSI-formatted symbol."""
        return self._request("GET", f"/v2/options/positions/{option_symbol_osi}")

    def close_option_position(self, option_symbol_osi: str, qty: Optional[int] = None) -> Dict[str, Any]:
        """Close an option position fully or partially by submitting a closing order."""
        payload: Dict[str, Any] = {}
        if qty is not None:
            payload["qty"] = qty
        return self._request("DELETE", f"/v2/options/positions/{option_symbol_osi}", payload=payload)

    # Activities (fills, etc.)
    def list_fills(self, after: Optional[str] = None, until: Optional[str] = None, limit: int = 50) -> Dict[str, Any]:
        """List recent fill activities to audit executed option trades."""
        params: Dict[str, Any] = {"activity_types": "FILL", "page_size": limit}
        if after:
            params["after"] = after
        if until:
            params["until"] = until
        return self._request("GET", "/v2/account/activities", params=params)


def try_options_data_examples(osi_symbol: str, key_id: str, secret_key: str) -> None:
    """Demonstrate basic historical data queries using alpaca-py."""
    try:
        from alpaca.data.historical import OptionsHistoricalDataClient
        from alpaca.data.requests import OptionBarsRequest, OptionQuotesRequest, OptionTradesRequest
        from alpaca.data.timeframe import TimeFrame
    except Exception as e:
        print("alpaca-py not installed or outdated for options data:", e)
        return

    client = OptionsHistoricalDataClient(key_id, secret_key)
    bars = client.get_option_bars(OptionBarsRequest(symbol_or_symbols=[osi_symbol], timeframe=TimeFrame.Minute, limit=5))
    quotes = client.get_option_quotes(OptionQuotesRequest(symbol_or_symbols=[osi_symbol], limit=5))
    trades = client.get_option_trades(OptionTradesRequest(symbol_or_symbols=[osi_symbol], limit=5))
    try:
        import pandas as pd  # type: ignore
        print("Bars head:\n", bars.df.head())
        print("Quotes head:\n", quotes.df.head())
        print("Trades head:\n", trades.df.head())
    except Exception:
        print("Bars:", bars)
        print("Quotes:", quotes)
        print("Trades:", trades)


def main() -> None:
    """Run a demo workflow that exercises account and options-trading endpoints."""
    key = os.getenv("APCA_API_KEY_ID", "***REMOVED***")
    secret = os.getenv("APCA_API_SECRET_KEY", "***REMOVED***")
    base_url = os.getenv("APCA_API_BASE_URL", "https://paper-api.alpaca.markets/v2")
    do_live = os.getenv("ALPACA_RUN", "0") == "1"

    client = AlpacaOptionsTrading(key, secret, base_trading_url=base_url, dry_run=not do_live)

    print("Account:")
    print(json.dumps(client.get_account(), indent=2))

    underlying = os.getenv("UNDERLYING", "AAPL")
    print("Listing option contracts (sample):")
    contracts = client.list_option_contracts(underlying=underlying, limit=10)
    print(json.dumps(contracts, indent=2))

    sample_osi = os.getenv("OPTION_OSI", "AAPL230616C00175000")

    print("Placing example limit buy order (1 contract, DAY):")
    order = client.place_option_order(
        option_symbol_osi=sample_osi,
        qty=1,
        side="buy",
        order_type="limit",
        time_in_force="day",
        limit_price=float(os.getenv("LIMIT_PRICE", "1.00")),
        position_intent=os.getenv("POSITION_INTENT", "OPEN"),
        client_order_id="demo-options-order-1",
    )
    print(json.dumps(order, indent=2))

    print("Listing open/closed option orders:")
    print(json.dumps(client.list_option_orders(status="all", limit=25), indent=2))

    example_order_id = os.getenv("ORDER_ID", "REPLACE_WITH_REAL_ID")
    if example_order_id != "REPLACE_WITH_REAL_ID":
        print("Fetching a specific option order:")
        print(json.dumps(client.get_option_order(example_order_id), indent=2))

        print("Replacing order (adjust limit):")
        print(json.dumps(client.replace_option_order(example_order_id, limit_price=0.95), indent=2))

        print("Canceling order:")
        print(json.dumps(client.cancel_option_order(example_order_id), indent=2))

    print("Cancel all option orders:")
    print(json.dumps(client.cancel_all_option_orders(), indent=2))

    print("Listing option positions:")
    print(json.dumps(client.list_option_positions(), indent=2))

    print("Fetching a single option position (if present):")
    print(json.dumps(client.get_option_position(sample_osi), indent=2))

    if os.getenv("CLOSE_POSITION", "0") == "1":
        print("Closing position for OSI symbol:")
        print(json.dumps(client.close_option_position(sample_osi), indent=2))

    print("Recent fills:")
    print(json.dumps(client.list_fills(limit=25), indent=2))

    if os.getenv("RUN_DATA_EXAMPLES", "1") == "1":
        print("Options data quick examples (requires alpaca-py and data access):")
        try_options_data_examples(sample_osi, key, secret)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("Error:", exc, file=sys.stderr)
        sys.exit(1)
