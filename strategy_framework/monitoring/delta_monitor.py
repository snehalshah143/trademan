"""
delta_monitor.py
----------------
Real-time NIFTY spot + option Greeks monitor.

Runs independently of the main strategy loop.
Refreshes every 1 minute (configurable).

For each open leg it fetches:
  Delta, Gamma, Theta, Vega, IV

Displays:
  - NIFTY spot
  - Per-leg Greeks with signed position delta
  - Net portfolio delta
  - Total Gamma, Theta, Vega

Works with BOTH paper mode (reads paper_state.json)
and live mode (reads from OpenAlgo positionbook).

Usage
-----
    # From strategy_framework/ directory:
    python -m monitoring.delta_monitor

    # With custom config:
    python -m monitoring.delta_monitor --config strategies/iron_fly/config/settings.yaml

    # Custom refresh interval (seconds):
    python -m monitoring.delta_monitor --interval 60
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

# Ensure framework root is on path
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from framework.config_loader import Config
from framework.openalgo_client import OpenAlgoClient, OpenAlgoError
from utils.helper import format_inr, now_ist, openalgo_fmt_to_date, safe_float, today_ist
from utils.logger import get_logger, setup_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class LegGreeks:
    """Greeks for one option leg."""
    symbol: str
    role: str                       # SHORT_CALL / SHORT_PUT / LONG_CALL / LONG_PUT
    action: str                     # BUY or SELL
    quantity: int                   # always positive
    strike: float
    option_type: str                # CE or PE

    # Greeks (raw from OpenAlgo — always from the option's perspective)
    raw_delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0
    iv: float = 0.0
    ltp: float = 0.0

    error: Optional[str] = None     # set if Greeks fetch failed

    @property
    def signed_quantity(self) -> int:
        """Negative for SELL legs (short position), positive for BUY legs."""
        return self.quantity if self.action == "BUY" else -self.quantity

    @property
    def position_delta(self) -> float:
        """
        Actual delta contribution of this leg to the portfolio.

        position_delta = raw_delta × signed_quantity
        """
        return self.raw_delta * self.signed_quantity

    @property
    def position_gamma(self) -> float:
        return self.gamma * self.signed_quantity

    @property
    def position_theta(self) -> float:
        return self.theta * self.signed_quantity

    @property
    def position_vega(self) -> float:
        return self.vega * self.signed_quantity


@dataclass
class DeltaSnapshot:
    """Complete portfolio snapshot for one refresh cycle."""
    timestamp: datetime
    spot: float
    legs: list[LegGreeks] = field(default_factory=list)

    @property
    def net_delta(self) -> float:
        return sum(leg.position_delta for leg in self.legs)

    @property
    def net_gamma(self) -> float:
        return sum(leg.position_gamma for leg in self.legs)

    @property
    def net_theta(self) -> float:
        return sum(leg.position_theta for leg in self.legs)

    @property
    def net_vega(self) -> float:
        return sum(leg.position_vega for leg in self.legs)


# ---------------------------------------------------------------------------
# Timing information — one snapshot per cycle
# ---------------------------------------------------------------------------

@dataclass
class TimingInfo:
    """All time-related signals for one refresh cycle."""
    now: datetime                       # current IST datetime
    session_status: str                 # PRE-MARKET / MARKET OPEN / POST-MARKET / WEEKEND
    is_weekend: bool
    is_market_open: bool
    minutes_since_open: float           # negative if before 09:15
    is_first_trading_day: bool
    is_expiry_day: bool
    is_t_minus_1: bool
    days_to_expiry: int
    expiry_str: str                     # e.g. "31JUL25"  ("--" if unknown)
    deployment_time: str                # from config, e.g. "09:30"
    expiry_exit_time: str               # from config, e.g. "11:00"
    mins_to_deployment: Optional[float]   # None if already past
    mins_to_expiry_exit: Optional[float]  # None if not today / already past


class TimingCollector:
    """Computes all time-related signals from config + current IST clock."""

    def __init__(self, config: Config, client: OpenAlgoClient) -> None:
        self._config = config
        self._client = client
        self._cached_expiry: str = "--"

    def collect(self, legs: list[dict]) -> TimingInfo:
        """Build a complete TimingInfo for the current instant."""
        now = now_ist()
        today = today_ist()

        # ── Session status ────────────────────────────────────────────────
        is_weekend = now.weekday() >= 5
        market_open_dt = now.replace(hour=9, minute=15, second=0, microsecond=0)
        market_close_dt = now.replace(hour=15, minute=30, second=0, microsecond=0)

        if is_weekend:
            session = "WEEKEND"
            is_open = False
        elif now < market_open_dt:
            session = "PRE-MARKET"
            is_open = False
        elif now > market_close_dt:
            session = "POST-MARKET"
            is_open = False
        else:
            session = "MARKET OPEN"
            is_open = True

        mins_since_open = (now - market_open_dt).total_seconds() / 60.0

        # ── Expiry detection ──────────────────────────────────────────────
        expiry_str = self._resolve_expiry(legs)

        exp_day = False
        t_minus_1 = False
        dte = -999
        if expiry_str != "--":
            try:
                exp_date = openalgo_fmt_to_date(expiry_str)
                dte = (exp_date - today).days
                exp_day = today == exp_date
                t1 = exp_date - timedelta(days=1)
                while t1.weekday() >= 5:
                    t1 -= timedelta(days=1)
                t_minus_1 = today == t1
            except ValueError:
                pass

        # ── First trading day of month ────────────────────────────────────
        first_of_month = today.replace(day=1)
        while first_of_month.weekday() >= 5:
            first_of_month += timedelta(days=1)
        is_first_td = today == first_of_month

        # ── Deployment window countdown ───────────────────────────────────
        dep_time_str = self._config.get_str("DEPLOYMENT_TIME", "09:30")
        mins_to_dep: Optional[float] = None
        if not is_weekend:
            hh, mm = map(int, dep_time_str.split(":"))
            dep_dt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if now < dep_dt:
                mins_to_dep = (dep_dt - now).total_seconds() / 60.0

        # ── Expiry exit countdown (only on expiry day) ────────────────────
        exit_time_str = self._config.get_str("EXPIRY_EXIT_TIME", "11:00")
        mins_to_exit: Optional[float] = None
        if exp_day and not is_weekend:
            hh, mm = map(int, exit_time_str.split(":"))
            exit_dt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if now < exit_dt:
                mins_to_exit = (exit_dt - now).total_seconds() / 60.0

        return TimingInfo(
            now=now,
            session_status=session,
            is_weekend=is_weekend,
            is_market_open=is_open,
            minutes_since_open=mins_since_open,
            is_first_trading_day=is_first_td,
            is_expiry_day=exp_day,
            is_t_minus_1=t_minus_1,
            days_to_expiry=dte,
            expiry_str=expiry_str,
            deployment_time=dep_time_str,
            expiry_exit_time=exit_time_str,
            mins_to_deployment=mins_to_dep,
            mins_to_expiry_exit=mins_to_exit,
        )

    def _resolve_expiry(self, legs: list[dict]) -> str:
        """
        Derive the active monthly expiry string from:
        1. Open leg symbols (parse from symbol directly — e.g. NIFTY31JUL2525000CE → 31JUL25)
        2. Cached value from last successful broker call
        """
        for leg in legs:
            sym = leg.get("symbol", "")
            parsed = self._parse_expiry_from_symbol(sym)
            if parsed:
                self._cached_expiry = parsed
                return parsed

        # Fallback: try broker
        try:
            underlying = self._config.underlying
            exchange = self._config.exchange_index
            expiries = self._client.get_expiry_dates(underlying, exchange)
            if expiries:
                # Last expiry of the current calendar month
                today = today_ist()
                monthly = [
                    e for e in expiries
                    if self._expiry_month_matches(e, today)
                ]
                if monthly:
                    self._cached_expiry = monthly[-1]
                    return self._cached_expiry
        except Exception:
            pass

        return self._cached_expiry

    @staticmethod
    def _parse_expiry_from_symbol(symbol: str) -> str:
        """
        Extract expiry string from option symbol.
        NIFTY31JUL2525000CE  →  31JUL25
        Assumes symbol = UNDERLYING + DDMMMYY + STRIKE + CE/PE
        """
        if len(symbol) < 10:
            return ""
        # Strip 'CE' / 'PE' suffix + trailing digits (strike)
        try:
            base = symbol[:-2]              # remove CE/PE
            # Walk backwards to skip digits (strike)
            idx = len(base) - 1
            while idx >= 0 and base[idx].isdigit():
                idx -= 1
            # Now idx points to last char of expiry portion
            # Expiry is always DDMMMYY = 7 chars
            expiry_end = idx + 1
            expiry_start = expiry_end - 7
            if expiry_start < 0:
                return ""
            candidate = base[expiry_start:expiry_end]
            # Validate: 2 digits + 3 alpha + 2 digits
            if (candidate[:2].isdigit() and candidate[2:5].isalpha()
                    and candidate[5:].isdigit()):
                return candidate
        except Exception:
            pass
        return ""

    @staticmethod
    def _expiry_month_matches(expiry_str: str, ref: date) -> bool:
        try:
            d = openalgo_fmt_to_date(expiry_str)
            return d.year == ref.year and d.month == ref.month
        except ValueError:
            return False


# ---------------------------------------------------------------------------
# Position reader — reads from paper snapshot or live broker
# ---------------------------------------------------------------------------

class PositionReader:
    """
    Reads current open legs from either:
    - Paper mode: logs/paper_state.json
    - Live mode : OpenAlgo positionbook + orderbook
    """

    def __init__(self, config: Config, client: OpenAlgoClient) -> None:
        self._config = config
        self._client = client

    def read_legs(self) -> list[dict]:
        """
        Return a list of leg dicts with keys:
          symbol, action, quantity, exchange, option_type, role, strike
        """
        if self._config.is_paper:
            return self._read_paper_legs()
        return self._read_live_legs()

    def _read_paper_legs(self) -> list[dict]:
        """Read virtual positions from the paper state JSON snapshot."""
        snapshot_path = Path(
            self._config.get_str("PAPER_SNAPSHOT_PATH", "logs/paper_state.json")
        )
        if not snapshot_path.exists():
            logger.warning(f"Paper snapshot not found at {snapshot_path}. No positions loaded.")
            return []

        try:
            with open(snapshot_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)

            legs = []
            for pos in data.get("positions", []):
                qty = pos.get("quantity", 0)
                if qty == 0:
                    continue
                symbol = pos.get("symbol", "")
                action = "BUY" if qty > 0 else "SELL"
                option_type = "CE" if symbol.endswith("CE") else "PE"
                strike = self._parse_strike(symbol)
                role = self._infer_role(action, option_type)

                legs.append({
                    "symbol": symbol,
                    "action": action,
                    "quantity": abs(qty),
                    "exchange": pos.get("exchange", self._config.exchange_options),
                    "option_type": option_type,
                    "role": role,
                    "strike": strike,
                })
            return legs

        except Exception as exc:
            logger.error(f"Failed to read paper snapshot: {exc}")
            return []

    def _read_live_legs(self) -> list[dict]:
        """Read live positions from OpenAlgo, filtered by strategy ID."""
        try:
            positions = self._client.get_positions()
            orders = self._client.get_orders()
        except OpenAlgoError as exc:
            logger.error(f"Failed to fetch positions from broker: {exc}")
            return []

        # Find symbols tagged to this strategy
        strategy_month = datetime.now().strftime("%Y%m")
        strategy_id = f"IRON_FLY_{strategy_month}"
        tagged_symbols = {
            o.get("symbol") for o in orders
            if str(o.get("strategy", "")).upper() == strategy_id.upper()
        }

        legs = []
        for pos in positions:
            qty = int(pos.get("quantity", 0))
            symbol = pos.get("symbol", "")
            if qty == 0 or (tagged_symbols and symbol not in tagged_symbols):
                continue

            action = "BUY" if qty > 0 else "SELL"
            option_type = "CE" if symbol.endswith("CE") else "PE"
            strike = self._parse_strike(symbol)
            role = self._infer_role(action, option_type)

            legs.append({
                "symbol": symbol,
                "action": action,
                "quantity": abs(qty),
                "exchange": pos.get("exchange", self._config.exchange_options),
                "option_type": option_type,
                "role": role,
                "strike": strike,
            })
        return legs

    @staticmethod
    def _parse_strike(symbol: str) -> float:
        """Extract strike from option symbol string."""
        try:
            base = symbol[:-2]
            digits = ""
            for ch in reversed(base):
                if ch.isdigit():
                    digits = ch + digits
                else:
                    break
            return float(digits) if digits else 0.0
        except Exception:
            return 0.0

    @staticmethod
    def _infer_role(action: str, option_type: str) -> str:
        if action == "SELL" and option_type == "CE":
            return "SHORT_CALL"
        if action == "SELL" and option_type == "PE":
            return "SHORT_PUT"
        if action == "BUY" and option_type == "CE":
            return "LONG_CALL"
        return "LONG_PUT"


# ---------------------------------------------------------------------------
# Greeks fetcher
# ---------------------------------------------------------------------------

class GreeksFetcher:
    """Fetches option Greeks from OpenAlgo for a list of symbols."""

    def __init__(self, client: OpenAlgoClient, interest_rate: float = 6.5) -> None:
        self._client = client
        self._interest_rate = interest_rate

    def fetch(self, leg_dicts: list[dict]) -> list[LegGreeks]:
        """
        Fetch Greeks for each leg. Returns a list of LegGreeks.
        Failed fetches are recorded with an error message — never crash the loop.
        """
        results = []
        for leg in leg_dicts:
            lg = LegGreeks(
                symbol=leg["symbol"],
                role=leg["role"],
                action=leg["action"],
                quantity=leg["quantity"],
                strike=leg["strike"],
                option_type=leg["option_type"],
            )
            try:
                raw = self._client._client.optiongreeks(
                    symbol=leg["symbol"],
                    exchange=leg["exchange"],
                    interest_rate=self._interest_rate,
                )
                if raw.get("status") == "success":
                    greeks = raw.get("greeks", {})
                    lg.raw_delta = safe_float(greeks.get("delta", 0))
                    lg.gamma = safe_float(greeks.get("gamma", 0))
                    lg.theta = safe_float(greeks.get("theta", 0))
                    lg.vega = safe_float(greeks.get("vega", 0))
                    lg.iv = safe_float(raw.get("implied_volatility", 0))
                    lg.ltp = safe_float(raw.get("option_price", 0))
                else:
                    lg.error = raw.get("message", "unknown error")
            except Exception as exc:
                lg.error = str(exc)

            results.append(lg)
        return results


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

class DeltaDashboard:
    """Renders the delta monitoring dashboard to console / log."""

    SEP = "=" * 72
    ROW_SEP = "-" * 72

    def render(self, snapshot: DeltaSnapshot, timing: TimingInfo, interval: int) -> None:
        """Print a full refresh of the dashboard."""
        lines = self._build_lines(snapshot, timing, interval)
        # Clear screen for clean refresh
        print("\033[2J\033[H", end="")
        output = "\n".join(lines)
        print(output)

    def _build_lines(self, snap: DeltaSnapshot, t: TimingInfo, interval: int) -> list[str]:
        ts = snap.timestamp.strftime("%d-%b-%Y  %H:%M:%S  IST")
        lines = [
            self.SEP,
            f"  NIFTY DELTA MONITOR          {ts}",
            self.SEP,
            f"  NIFTY Spot  :  {snap.spot:,.2f}",
            self.ROW_SEP,
            f"  {'Role':<14} {'Symbol':<26} {'LTP':>7}  {'IV%':>6}  "
            f"{'Raw Δ':>7}  {'Pos Δ':>8}  {'Θ/day':>8}  {'Vega':>7}",
            self.ROW_SEP,
        ]

        for leg in snap.legs:
            if leg.error:
                lines.append(
                    f"  {leg.role:<14} {leg.symbol:<26}  "
                    f"ERROR: {leg.error}"
                )
            else:
                lines.append(
                    f"  {leg.role:<14} {leg.symbol:<26} "
                    f"{leg.ltp:>7.2f}  "
                    f"{leg.iv:>5.1f}%  "
                    f"{leg.raw_delta:>+7.4f}  "
                    f"{leg.position_delta:>+8.4f}  "
                    f"{leg.position_theta:>+8.2f}  "
                    f"{leg.position_vega:>+7.2f}"
                )

        lines += [
            self.ROW_SEP,
            f"  {'NET PORTFOLIO':<14} {'':26} {'':>7}  {'':>6}  "
            f"{'':>7}  "
            f"{snap.net_delta:>+8.4f}  "
            f"{snap.net_theta:>+8.2f}  "
            f"{snap.net_vega:>+7.2f}",
            self.SEP,
            f"  Net Delta   : {snap.net_delta:>+.4f}   "
            f"({'BULLISH' if snap.net_delta > 0 else 'BEARISH' if snap.net_delta < 0 else 'NEUTRAL'})",
            f"  Net Gamma   : {snap.net_gamma:>+.6f}",
            f"  Net Theta   : {snap.net_theta:>+.2f}  (₹/day)",
            f"  Net Vega    : {snap.net_vega:>+.2f}",
        ]

        # ── Time Panel ────────────────────────────────────────────────────
        lines += self._build_time_panel(t)

        lines += [
            self.SEP,
            f"  Next refresh in {interval}s  |  Press Ctrl+C to stop",
            self.SEP,
        ]
        return lines

    def _build_time_panel(self, t: TimingInfo) -> list[str]:
        """Render the time-based signals section."""
        lines = [
            self.SEP,
            f"  TIME & SESSION",
            self.ROW_SEP,
        ]

        # Session status with color hint (plain text)
        session_line = f"  Session       : {t.session_status}"
        if t.is_market_open:
            elapsed_m = int(t.minutes_since_open)
            elapsed_h, elapsed_m2 = divmod(elapsed_m, 60)
            session_line += f"  ({elapsed_h}h {elapsed_m2:02d}m elapsed)"
        elif not t.is_weekend and t.minutes_since_open < 0:
            wait_m = int(-t.minutes_since_open)
            session_line += f"  ({wait_m}m to open)"
        lines.append(session_line)

        # Deployment window
        dep_line = f"  Deploy window : {t.deployment_time}"
        if t.mins_to_deployment is not None:
            dep_m = int(t.mins_to_deployment)
            dep_h, dep_m2 = divmod(dep_m, 60)
            dep_label = f"{dep_h}h {dep_m2:02d}m" if dep_h else f"{dep_m2}m"
            dep_line += f"  → opens in {dep_label}"
        else:
            dep_line += "  [window open]"
        lines.append(dep_line)

        # Expiry info
        dte_str = f"{t.days_to_expiry}d" if t.days_to_expiry >= 0 else "EXPIRED"
        lines.append(
            f"  Expiry        : {t.expiry_str}  ({dte_str} remaining)"
        )

        # Flags row
        flags: list[str] = []
        if t.is_first_trading_day:
            flags.append("FIRST-TRADE-DAY")
        if t.is_t_minus_1:
            flags.append("T-1 (pre-expiry)")
        if t.is_expiry_day:
            flags.append("*** EXPIRY DAY ***")
        flags_str = "  ".join(flags) if flags else "none"
        lines.append(f"  Flags         : {flags_str}")

        # Expiry exit countdown (only meaningful on expiry day)
        if t.is_expiry_day:
            exit_line = f"  Expiry exit   : {t.expiry_exit_time}"
            if t.mins_to_expiry_exit is not None:
                ex_m = int(t.mins_to_expiry_exit)
                ex_h, ex_m2 = divmod(ex_m, 60)
                ex_label = f"{ex_h}h {ex_m2:02d}m" if ex_h else f"{ex_m2}m"
                exit_line += f"  → exits in {ex_label}"
            else:
                exit_line += "  [exit window reached]"
            lines.append(exit_line)

        return lines


# ---------------------------------------------------------------------------
# Main monitor loop
# ---------------------------------------------------------------------------

class DeltaMonitor:
    """
    Runs the delta monitoring loop.

    Parameters
    ----------
    config       : Config object
    client       : OpenAlgoClient
    interval     : Refresh interval in seconds (default 60)
    """

    def __init__(self, config: Config, client: OpenAlgoClient, interval: int = 60) -> None:
        self._config = config
        self._client = client
        self._interval = interval
        self._running = False
        self._reader = PositionReader(config, client)
        self._fetcher = GreeksFetcher(
            client,
            interest_rate=config.get_float("INTEREST_RATE", 6.5),
        )
        self._timing = TimingCollector(config, client)
        self._dashboard = DeltaDashboard()

        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

    def run(self) -> None:
        """Start the monitoring loop. Blocks until Ctrl+C."""
        print(f"Delta Monitor starting — interval={self._interval}s  mode={self._config.execution_mode}")
        self._running = True

        while self._running:
            cycle_start = time.time()
            try:
                self._run_cycle()
            except Exception as exc:
                logger.error(f"Delta monitor cycle error: {exc}")

            elapsed = time.time() - cycle_start
            sleep_time = max(0.0, self._interval - elapsed)
            if self._running:
                time.sleep(sleep_time)

    def _run_cycle(self) -> None:
        """One refresh cycle: read spot + legs + fetch Greeks + display."""
        now = now_ist()

        # Read NIFTY spot
        spot = self._get_spot()

        # Read open legs
        leg_dicts = self._reader.read_legs()

        # Collect timing info (works even with no positions)
        timing = self._timing.collect(leg_dicts)

        if not leg_dicts:
            # Still show the time panel so time-based decisions are visible
            print("\033[2J\033[H", end="")
            print(DeltaDashboard.SEP)
            print(f"  NIFTY DELTA MONITOR          {now.strftime('%d-%b-%Y  %H:%M:%S  IST')}")
            print(DeltaDashboard.SEP)
            print(f"  NIFTY Spot  :  {spot:,.2f}")
            print(DeltaDashboard.ROW_SEP)
            print("  No open positions found. Waiting …")
            # Print the time panel
            for line in DeltaDashboard()._build_time_panel(timing):
                print(line)
            print(DeltaDashboard.SEP)
            print(f"  Next refresh in {self._interval}s  |  Press Ctrl+C to stop")
            print(DeltaDashboard.SEP)
            return

        # Fetch Greeks for each leg
        leg_greeks = self._fetcher.fetch(leg_dicts)

        # Build snapshot
        snapshot = DeltaSnapshot(timestamp=now, spot=spot, legs=leg_greeks)

        # Render dashboard
        self._dashboard.render(snapshot, timing, self._interval)

    def _get_spot(self) -> float:
        """Fetch current NIFTY spot price."""
        try:
            data = self._client.get_quotes(
                symbol=self._config.underlying,
                exchange=self._config.exchange_index,
            )
            return safe_float(data.get("ltp", 0))
        except OpenAlgoError as exc:
            logger.warning(f"Failed to fetch NIFTY spot: {exc}")
            return 0.0

    def _handle_shutdown(self, signum: int, frame) -> None:
        print("\nDelta Monitor stopping …")
        self._running = False


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="NIFTY + Option Delta Monitor — powered by OpenAlgo"
    )
    parser.add_argument(
        "--config",
        default=str(_root / "strategies" / "iron_fly" / "config" / "settings.yaml"),
        help="Path to settings.yaml",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=60,
        help="Refresh interval in seconds (default: 60)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # Load config
    try:
        config = Config(args.config)
    except Exception as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        sys.exit(1)

    # Setup logging (minimal — dashboard is to stdout)
    setup_logger(__name__, log_file="logs/delta_monitor.log", level="WARNING")

    # Connect to OpenAlgo
    try:
        client = OpenAlgoClient(
            api_key=config.api_key,
            host=config.host,
            timeout=config.get_float("API_TIMEOUT", 30.0),
        )
        # Quick connectivity check
        client.get_funds()
    except Exception as exc:
        print(f"OpenAlgo connection failed: {exc}", file=sys.stderr)
        sys.exit(1)

    monitor = DeltaMonitor(config=config, client=client, interval=args.interval)
    monitor.run()
    client.close()


if __name__ == "__main__":
    main()
