"""
context_builder.py
------------------
Assembles a ``StrategyContext`` for each monitoring cycle.

Responsibility
--------------
- Call every required OpenAlgo endpoint exactly once per cycle.
- Translate raw SDK responses into typed model objects.
- Determine expiry-day flags (T-1, expiry day).
- Hand the assembled ``StrategyContext`` to the strategy for evaluation.

The builder itself contains no business logic — it is pure data assembly.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Optional

from ..framework.openalgo_client import OpenAlgoClient, OpenAlgoError
from ..models.strategy_context import OptionChainEntry, StrategyContext
from ..models.strategy_state import StrategyState
from ..utils.constants import INSTRUMENT_OPTIONS
from ..utils.helper import now_ist, openalgo_fmt_to_date, safe_float
from ..utils.logger import get_logger


# ---------------------------------------------------------------------------
# Standalone chain parser — importable by any script that receives a raw
# option chain response dict and needs a typed list of OptionChainEntry.
# ---------------------------------------------------------------------------

def parse_chain_entries(chain_response: dict) -> list[OptionChainEntry]:
    """
    Convert the raw ``chain`` list from an OpenAlgo option chain response
    into a list of typed ``OptionChainEntry`` objects.

    Parameters
    ----------
    chain_response : The full response dict from ``OpenAlgoClient.get_option_chain()``.

    Returns
    -------
    List of ``OptionChainEntry``, one per strike row in the chain.
    """
    entries: list[OptionChainEntry] = []
    for item in chain_response.get("chain", []):
        ce = item.get("ce") or {}
        pe = item.get("pe") or {}
        entries.append(OptionChainEntry(
            strike=float(item.get("strike", 0)),
            ce_symbol=ce.get("symbol"),
            ce_ltp=safe_float(ce.get("ltp")) if ce.get("ltp") is not None else None,
            ce_label=ce.get("label"),
            pe_symbol=pe.get("symbol"),
            pe_ltp=safe_float(pe.get("ltp")) if pe.get("ltp") is not None else None,
            pe_label=pe.get("label"),
        ))
    return entries

logger = get_logger(__name__)


class ContextBuildError(Exception):
    """Raised when essential data cannot be fetched to build a context."""


class ContextBuilder:
    """
    Fetches all OpenAlgo data and returns a ``StrategyContext``.

    Parameters
    ----------
    client      : Initialised ``OpenAlgoClient``
    config      : ``Config`` object (or raw dict — only ``.get()`` is used)
    state       : Current ``StrategyState`` reference
    """

    def __init__(self, client: OpenAlgoClient, config: Any, state: StrategyState) -> None:
        self._client = client
        self._config = config
        self._state = state

    # ---------------------------------------------------------------------------
    # Public entry point
    # ---------------------------------------------------------------------------

    def build(self) -> StrategyContext:
        """
        Collect all market and account data and return a ``StrategyContext``.

        Raises ``ContextBuildError`` if any critical data call fails.
        """
        logger.debug("Building strategy context …")

        expiry = self._resolve_expiry()
        chain_response = self._fetch_option_chain(expiry)
        spot = float(chain_response.get("underlying_ltp", 0.0))
        atm_strike = float(chain_response.get("atm_strike", 0.0))
        option_chain = self._parse_chain(chain_response)

        positions = self._fetch_positions()
        trades = self._fetch_trades()
        orders = self._fetch_orders()
        funds = self._fetch_funds()

        available_cash = safe_float(funds.get("availablecash", 0))
        m2m_unrealized = safe_float(funds.get("m2munrealized", 0))
        m2m_realized = safe_float(funds.get("m2mrealized", 0))

        now = now_ist()
        is_t1, is_expiry = self._check_expiry_flags(expiry, now.date())

        ctx = StrategyContext(
            timestamp=now,
            spot=spot,
            atm_strike=atm_strike,
            expiry=expiry,
            option_chain=option_chain,
            positions=positions,
            trades=trades,
            orders=orders,
            available_cash=available_cash,
            m2m_unrealized=m2m_unrealized,
            m2m_realized=m2m_realized,
            config=self._config.as_dict if hasattr(self._config, "as_dict") else dict(self._config),
            strategy_state=self._state,
            is_t_minus_1=is_t1,
            is_expiry_day=is_expiry,
        )

        logger.debug(
            f"Context built: spot={spot} ATM={atm_strike} expiry={expiry} "
            f"cash={available_cash:.0f} positions={len(positions)} "
            f"T-1={is_t1} expiry_day={is_expiry}"
        )
        return ctx

    # ---------------------------------------------------------------------------
    # Data fetchers (each isolated so failures are attributable)
    # ---------------------------------------------------------------------------

    def _resolve_expiry(self) -> str:
        """
        Return the current monthly expiry string.

        Uses the state's stored expiry if available (faster, no extra call).
        Otherwise fetches from OpenAlgo expiry API and picks the nearest monthly expiry.
        """
        if self._state.expiry:
            return self._state.expiry

        underlying = self._config.get("UNDERLYING", "NIFTY")
        exchange = self._config.get("EXCHANGE_OPTIONS", "NFO")

        try:
            expiries = self._client.get_expiry_dates(
                symbol=underlying,
                exchange=exchange,
                instrumenttype=INSTRUMENT_OPTIONS,
            )
        except OpenAlgoError as exc:
            raise ContextBuildError(f"Failed to fetch expiry dates: {exc}") from exc

        if not expiries:
            raise ContextBuildError("No expiry dates returned by OpenAlgo.")

        monthly = self._pick_monthly_expiry(expiries)
        logger.info(f"Resolved monthly expiry: {monthly}")
        return monthly

    def _fetch_option_chain(self, expiry: str) -> dict:
        """Fetch and return the raw option chain response dict."""
        underlying = self._config.get("UNDERLYING", "NIFTY")
        exchange = self._config.get("EXCHANGE_INDEX", "NSE_INDEX")
        strike_count = self._config.get("CHAIN_STRIKE_COUNT", 20)

        try:
            return self._client.get_option_chain(
                underlying=underlying,
                exchange=exchange,
                expiry_date=expiry,
                strike_count=strike_count,
            )
        except OpenAlgoError as exc:
            raise ContextBuildError(f"Failed to fetch option chain: {exc}") from exc

    def _fetch_positions(self) -> list[dict]:
        try:
            return self._client.get_positions()
        except OpenAlgoError as exc:
            logger.warning(f"Position fetch failed (returning empty): {exc}")
            return []

    def _fetch_trades(self) -> list[dict]:
        try:
            return self._client.get_trades()
        except OpenAlgoError as exc:
            logger.warning(f"Trade fetch failed (returning empty): {exc}")
            return []

    def _fetch_orders(self) -> list[dict]:
        try:
            return self._client.get_orders()
        except OpenAlgoError as exc:
            logger.warning(f"Order fetch failed (returning empty): {exc}")
            return []

    def _fetch_funds(self) -> dict:
        try:
            return self._client.get_funds()
        except OpenAlgoError as exc:
            logger.warning(f"Funds fetch failed (returning zeros): {exc}")
            return {"availablecash": 0, "m2munrealized": 0, "m2mrealized": 0}

    # ---------------------------------------------------------------------------
    # Parsers
    # ---------------------------------------------------------------------------

    def _parse_chain(self, chain_response: dict) -> list[OptionChainEntry]:
        """Delegate to the module-level ``parse_chain_entries`` function."""
        return parse_chain_entries(chain_response)

    # ---------------------------------------------------------------------------
    # Expiry helpers
    # ---------------------------------------------------------------------------

    def _pick_monthly_expiry(self, expiries: list[str]) -> str:
        """
        From a list of expiry strings, return the nearest **monthly** expiry.

        Monthly expiry = last Thursday of the month.
        Strategy: find the expiry that is >= today and furthest into the
        current calendar month (i.e. the last one in the month).
        """
        today = date.today()

        parsed: list[date] = []
        for exp_str in expiries:
            try:
                parsed.append(openalgo_fmt_to_date(exp_str))
            except ValueError:
                continue

        # Filter to current or future expiries
        future = [d for d in parsed if d >= today]
        if not future:
            raise ContextBuildError("No future expiry dates found.")

        # Monthly expiry = last expiry in the current month
        current_month_expiries = [d for d in future if d.month == today.month and d.year == today.year]
        if current_month_expiries:
            monthly = max(current_month_expiries)
        else:
            # Month has passed; pick the last expiry of the next available month
            next_month = min(future).month
            next_year = min(future).year
            monthly = max(d for d in future if d.month == next_month and d.year == next_year)

        from ..utils.helper import expiry_to_openalgo_fmt
        return expiry_to_openalgo_fmt(monthly)

    def _check_expiry_flags(self, expiry_str: str, today: date) -> tuple[bool, bool]:
        """
        Return (is_t_minus_1, is_expiry_day).

        T-1 is the trading day immediately before expiry.
        We simplify T-1 as calendar day before expiry (sufficient for daily checks).
        """
        try:
            expiry_date = openalgo_fmt_to_date(expiry_str)
        except ValueError:
            return False, False

        is_expiry_day = today == expiry_date

        # T-1 = one calendar day before (weekends excluded at a higher level)
        from datetime import timedelta
        t1_date = expiry_date - timedelta(days=1)
        # Skip weekends for T-1
        while t1_date.weekday() >= 5:
            t1_date -= timedelta(days=1)

        is_t_minus_1 = today == t1_date
        return is_t_minus_1, is_expiry_day
