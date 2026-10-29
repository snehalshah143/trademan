"""
openalgo_client.py
------------------
Thin wrapper around the ``openalgo.api`` SDK.

Purpose
-------
- Centralise all OpenAlgo SDK calls in one place.
- Translate raw SDK responses into clean Python types (dicts / lists / floats).
- Raise ``OpenAlgoError`` on API errors so the rest of the framework never
  needs to inspect raw ``{"status": "error"}`` dicts.
- Never contain business logic — only I/O and response normalisation.

Every method signature maps 1-to-1 with an OpenAlgo SDK call.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from openalgo import api as OpenAlgoAPI

from ..utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------

class OpenAlgoError(Exception):
    """Raised when an OpenAlgo API call returns status == 'error'."""

    def __init__(self, endpoint: str, message: str, error_type: str = "") -> None:
        self.endpoint = endpoint
        self.error_type = error_type
        super().__init__(f"[{endpoint}] {message} (type={error_type})")


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class OpenAlgoClient:
    """
    Thin wrapper around ``openalgo.api``.

    Instantiate once per strategy run and share the instance — it maintains
    a pooled HTTP connection internally.

    Parameters
    ----------
    api_key : OpenAlgo API key
    host    : OpenAlgo server URL (default ``http://127.0.0.1:5000``)
    timeout : HTTP request timeout in seconds
    """

    def __init__(self, api_key: str, host: str = "http://127.0.0.1:5000", timeout: float = 30.0) -> None:
        self._client = OpenAlgoAPI(api_key=api_key, host=host, timeout=timeout)
        logger.info(f"OpenAlgoClient initialised — host={host}")

    def close(self) -> None:
        """Disconnect WebSocket if open. REST client has no explicit close."""
        try:
            if getattr(self._client, "connected", False):
                self._client.disconnect()
        except Exception:
            pass

    def __enter__(self) -> "OpenAlgoClient":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    # ---------------------------------------------------------------------------
    # Internal helpers
    # ---------------------------------------------------------------------------

    def _check(self, endpoint: str, response: dict) -> dict:
        """
        Raise ``OpenAlgoError`` if the response indicates failure.
        Otherwise return the response unchanged.
        """
        if response.get("status") == "error":
            raise OpenAlgoError(
                endpoint=endpoint,
                message=response.get("message", "unknown error"),
                error_type=response.get("error_type", ""),
            )
        return response

    # ---------------------------------------------------------------------------
    # Market data
    # ---------------------------------------------------------------------------

    def get_quotes(self, symbol: str, exchange: str) -> dict:
        """
        Fetch real-time quote for a single symbol.

        Returns the ``data`` sub-dict (ltp, bid, ask, volume, oi, etc.).
        """
        raw = self._client.quotes(symbol=symbol, exchange=exchange)
        self._check("quotes", raw)
        return raw.get("data", {})

    def get_option_chain(
        self,
        underlying: str,
        exchange: str,
        expiry_date: str,
        strike_count: Optional[int] = None,
    ) -> dict:
        """
        Fetch the full option chain for *underlying* / *expiry_date*.

        Returns the full response dict (status, underlying_ltp, atm_strike, chain[]).
        """
        raw = self._client.optionchain(
            underlying=underlying,
            exchange=exchange,
            expiry_date=expiry_date,
            strike_count=strike_count,
        )
        self._check("optionchain", raw)
        return raw

    def get_expiry_dates(self, symbol: str, exchange: str, instrumenttype: str) -> list[str]:
        """
        Return a list of expiry date strings for *symbol* in OpenAlgo format.

        Example return: ``["31JUL25", "28AUG25", "25SEP25"]``
        """
        raw = self._client.expiry(symbol=symbol, exchange=exchange, instrumenttype=instrumenttype)
        self._check("expiry", raw)
        return raw.get("data", [])

    # ---------------------------------------------------------------------------
    # Account / positions
    # ---------------------------------------------------------------------------

    def get_positions(self) -> list[dict]:
        """Return the raw list of open positions from the position book."""
        raw = self._client.positionbook()
        self._check("positionbook", raw)
        return raw.get("data", []) or []

    def get_trades(self) -> list[dict]:
        """Return the raw list of trades from the trade book."""
        raw = self._client.tradebook()
        self._check("tradebook", raw)
        return raw.get("data", []) or []

    def get_orders(self) -> list[dict]:
        """Return the raw list of orders from the order book."""
        raw = self._client.orderbook()
        self._check("orderbook", raw)
        return raw.get("data", {}).get("orders", []) or []

    def get_funds(self) -> dict:
        """
        Return account funds data.

        Keys: availablecash, collateral, m2mrealized, m2munrealized, utiliseddebits.
        """
        raw = self._client.funds()
        self._check("funds", raw)
        return raw.get("data", {})

    def get_margin(self, positions: list[dict]) -> float:
        """
        Calculate total margin required for a basket of positions.

        Returns the ``total_margin_required`` float.
        """
        raw = self._client.margin(positions=positions)
        self._check("margin", raw)
        return float(raw.get("data", {}).get("total_margin_required", 0.0))

    # ---------------------------------------------------------------------------
    # Orders
    # ---------------------------------------------------------------------------

    def place_order(
        self,
        strategy: str,
        symbol: str,
        action: str,
        exchange: str,
        quantity: int,
        price_type: str = "MARKET",
        product: str = "NRML",
    ) -> str:
        """
        Place a single order and return the broker order ID.

        Raises ``OpenAlgoError`` on failure.
        """
        raw = self._client.placeorder(
            strategy=strategy,
            symbol=symbol,
            action=action,
            exchange=exchange,
            quantity=quantity,
            price_type=price_type,
            product=product,
        )
        self._check("placeorder", raw)
        order_id = raw.get("orderid", "")
        logger.info(f"Order placed: {action} {symbol} qty={quantity} → orderid={order_id}")
        return str(order_id)

    def get_order_status(self, order_id: str, strategy: str) -> dict:
        """
        Fetch the current status of *order_id*.

        Returns the ``data`` sub-dict with keys:
        order_status, quantity, average_price, symbol, action, etc.
        """
        raw = self._client.orderstatus(order_id=order_id, strategy=strategy)
        self._check("orderstatus", raw)
        return raw.get("data", {})

    def cancel_order(self, order_id: str, strategy: str) -> None:
        """Cancel *order_id*. Raises ``OpenAlgoError`` on failure."""
        raw = self._client.cancelorder(order_id=order_id, strategy=strategy)
        self._check("cancelorder", raw)
        logger.info(f"Order cancelled: orderid={order_id}")

    def close_all_positions(self, strategy: str) -> None:
        """Square off all open positions for *strategy*."""
        raw = self._client.closeposition(strategy=strategy)
        self._check("closeposition", raw)
        logger.info(f"All positions closed for strategy={strategy}")

    def get_open_position(self, symbol: str, exchange: str, product: str, strategy: str) -> int:
        """
        Return the current net position quantity for *symbol*.

        Positive = long, negative = short.  0 = no position.
        """
        raw = self._client.openposition(
            strategy=strategy,
            symbol=symbol,
            exchange=exchange,
            product=product,
        )
        self._check("openposition", raw)
        return int(raw.get("quantity", 0))

    def modify_order(
        self,
        order_id: str,
        strategy: str,
        symbol: str,
        exchange: str,
        quantity: int,
        price: float,
        price_type: str = "LIMIT",
        product: str = "NRML",
    ) -> str:
        """
        Modify an existing order (e.g. adjust limit price).

        Returns the (possibly new) broker order ID.
        Raises ``OpenAlgoError`` on failure.
        """
        raw = self._client.modifyorder(
            order_id=order_id,
            strategy=strategy,
            symbol=symbol,
            exchange=exchange,
            quantity=quantity,
            price=price,
            price_type=price_type,
            product=product,
        )
        self._check("modifyorder", raw)
        new_id = raw.get("orderid", order_id)
        logger.info(f"Order modified: orderid={order_id} → new_id={new_id}")
        return str(new_id)

    def ping(self) -> bool:
        """
        Check connectivity to the OpenAlgo server.

        Returns ``True`` if the server responds successfully, ``False`` otherwise.
        Never raises.
        """
        try:
            raw = self._client.funds()
            return raw.get("status") != "error"
        except Exception as exc:
            logger.debug(f"ping: OpenAlgo unreachable — {exc}")
            return False

    def get_history(
        self,
        symbol: str,
        exchange: str,
        interval: str = "1m",
        start_date: str = "",
        end_date: str = "",
        source: str = "api",
        warmup_bars: int = 0,
    ) -> list[dict]:
        """
        Fetch historical OHLCV candle data for *symbol*.

        Parameters
        ----------
        symbol      : e.g. ``"NIFTY"``
        exchange    : e.g. ``"NSE_INDEX"``
        interval    : Candle interval — ``"1m"``, ``"5m"``, ``"15m"``, ``"1h"``, ``"1d"``
        start_date  : ``"YYYY-MM-DD"`` (empty = auto-calculated from warmup_bars)
        end_date    : ``"YYYY-MM-DD"`` (empty = today)
        warmup_bars : If > 0 and start_date is empty, auto-calculate the start date
                      so the window comfortably covers this many bars of *interval*.
                      NSE trading day = 375 minutes.  Calendar days = trading days
                      × 7/5 (weekend ratio) + 3 holiday buffer, minimum 5 days.

        Returns
        -------
        List of dicts with keys: ``timestamp``, ``open``, ``high``, ``low``,
        ``close``, ``volume``.
        """
        import math
        import re
        from datetime import date, timedelta
        import pandas as pd

        end = date.today().strftime("%Y-%m-%d") if not end_date else end_date

        # ── Auto-calculate start_date from warmup_bars ───────────────────────
        if not start_date:
            if warmup_bars > 0:
                # Parse interval to minutes-per-bar
                m = re.match(r"^(\d+)([mhds])", interval.lower())
                if m:
                    n, unit = int(m.group(1)), m.group(2)
                    mins_per_bar = {"m": n, "h": n * 60, "d": 375, "s": max(1, n // 60)}.get(unit, n)
                else:
                    mins_per_bar = 1

                trading_minutes_per_day = 375          # NSE 9:15–3:30
                bars_per_day   = trading_minutes_per_day / mins_per_bar
                trading_days   = max(1, math.ceil(warmup_bars / bars_per_day))
                calendar_days  = max(5, math.ceil(trading_days * 7 / 5) + 3)
                start_date = (date.today() - timedelta(days=calendar_days)).strftime("%Y-%m-%d")
                logger.info(
                    "get_history: warmup_bars=%d interval=%s → %d trading days → "
                    "%d calendar days → start=%s",
                    warmup_bars, interval, trading_days, calendar_days, start_date,
                )
            else:
                start_date = (date.today() - timedelta(days=7)).strftime("%Y-%m-%d")

        raw = self._client.history(
            symbol=symbol, exchange=exchange, interval=interval,
            start_date=start_date, end_date=end,
        )

        # SDK returns a DataFrame on success, a dict on error
        if isinstance(raw, dict):
            self._check("history", raw)
            return []

        if isinstance(raw, pd.DataFrame):
            records = []
            for ts, row in raw.iterrows():
                records.append({
                    "timestamp": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
                    "open":   float(row.get("open",   0)),
                    "high":   float(row.get("high",   0)),
                    "low":    float(row.get("low",    0)),
                    "close":  float(row.get("close",  0)),
                    "volume": int(row.get("volume",   0)),
                })
            logger.info(
                "get_history: %s %s → %d bars (window %s → %s)",
                symbol, interval, len(records), start_date, end,
            )
            return records

        return []
