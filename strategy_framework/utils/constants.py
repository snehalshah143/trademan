"""
constants.py
------------
Framework-wide constants shared across all strategies.

Strategy-specific constants (e.g. NIFTY lot size) live inside the
strategy's own config/settings.yaml and are not hardcoded here.
"""

# ---------------------------------------------------------------------------
# Exchanges
# ---------------------------------------------------------------------------
EXCHANGE_INDEX = "NSE_INDEX"    # For reading NIFTY spot / option chain
EXCHANGE_OPTIONS = "NFO"        # For placing NFO option orders

# ---------------------------------------------------------------------------
# Instrument types (used with api.expiry())
# ---------------------------------------------------------------------------
INSTRUMENT_OPTIONS = "options"
INSTRUMENT_FUTURES = "futures"

# ---------------------------------------------------------------------------
# Order actions
# ---------------------------------------------------------------------------
ACTION_BUY = "BUY"
ACTION_SELL = "SELL"

# ---------------------------------------------------------------------------
# Order / product types
# ---------------------------------------------------------------------------
PRICE_TYPE_MARKET = "MARKET"
PRICE_TYPE_LIMIT = "LIMIT"
PRODUCT_NRML = "NRML"
PRODUCT_MIS = "MIS"

# ---------------------------------------------------------------------------
# Order status values returned by OpenAlgo orderstatus()
# ---------------------------------------------------------------------------
ORDER_STATUS_COMPLETE = "complete"
ORDER_STATUS_REJECTED = "rejected"
ORDER_STATUS_CANCELLED = "cancelled"
ORDER_STATUS_OPEN = "open"
ORDER_STATUS_PENDING = "pending"
ORDER_STATUS_PARTIAL = "partially filled"

TERMINAL_ORDER_STATUSES = {ORDER_STATUS_COMPLETE, ORDER_STATUS_REJECTED, ORDER_STATUS_CANCELLED}
FILLED_STATUS = ORDER_STATUS_COMPLETE

# ---------------------------------------------------------------------------
# Execution modes
# ---------------------------------------------------------------------------
EXECUTION_MODE_PAPER = "PAPER"
EXECUTION_MODE_LIVE = "LIVE"

# ---------------------------------------------------------------------------
# Recovery direction
# ---------------------------------------------------------------------------
RECOVERY_UP = "UP"
RECOVERY_DOWN = "DOWN"

# ---------------------------------------------------------------------------
# Rounding bases
# ---------------------------------------------------------------------------
ROUND_BASE_100 = 100
ROUND_BASE_50 = 50

# ---------------------------------------------------------------------------
# Timing
# ---------------------------------------------------------------------------
IST_TIMEZONE = "Asia/Kolkata"

# Default order poll interval (seconds) when waiting for fills
ORDER_POLL_INTERVAL_SECONDS = 2

# Default order fill timeout (seconds) — fail if not filled within this window
ORDER_FILL_TIMEOUT_SECONDS = 60

# ---------------------------------------------------------------------------
# Heartbeat separator
# ---------------------------------------------------------------------------
HEARTBEAT_SEPARATOR = "=" * 52

# ---------------------------------------------------------------------------
# Credit spread — execution modes
# ---------------------------------------------------------------------------
EXEC_MODE_MONITOR_ONLY       = "MONITOR_ONLY"       # no orders placed; log only
EXEC_MODE_ENTRY_EXEC_MONITOR = "ENTRY_EXEC_MONITOR" # entries executed; exits manual
EXEC_MODE_EXIT_EXEC_MONITOR  = "EXIT_EXEC_MONITOR"  # exits executed; entries manual
EXEC_MODE_FULL_EXEC          = "FULL_EXEC"           # both entry and exit automated

EXEC_MODES_ALL = (
    EXEC_MODE_MONITOR_ONLY,
    EXEC_MODE_ENTRY_EXEC_MONITOR,
    EXEC_MODE_EXIT_EXEC_MONITOR,
    EXEC_MODE_FULL_EXEC,
)

# ---------------------------------------------------------------------------
# Credit spread — spread types
# ---------------------------------------------------------------------------
SPREAD_BULL_PUT  = "BULL_PUT_SPREAD"
SPREAD_BEAR_CALL = "BEAR_CALL_SPREAD"

# ---------------------------------------------------------------------------
# Credit spread — exit reasons
# ---------------------------------------------------------------------------
EXIT_REASON_SL_HIT    = "SL_HIT"
EXIT_REASON_TARGET    = "TARGET"
EXIT_REASON_EXPIRY    = "EXPIRY"
EXIT_REASON_MANUAL    = "MANUAL"
EXIT_REASON_ST_FLIP   = "SUPERTREND_FLIP"
EXIT_REASON_LADDER    = "PROFIT_LADDER"

# ---------------------------------------------------------------------------
# Credit spread — signal & trailing defaults
# ---------------------------------------------------------------------------
DEFAULT_CONFIRMATION_BARS = 2        # N-check default
DEFAULT_TRAIL_STEP        = 500.0    # MTM step-trail default (₹)
DEFAULT_TRAIL_ACTIVATION  = 1000.0   # MTM must reach this before trailing kicks in

# ---------------------------------------------------------------------------
# NFO freeze quantities (NSE per-order lot limits for common underlyings)
# Update from https://nseindia.com/regulations/member-regulation/circular periodically
# ---------------------------------------------------------------------------
FREEZE_QTY_NIFTY     = 1800   # NIFTY  @ 25 lot size → 1800 qty = 72 lots
FREEZE_QTY_BANKNIFTY = 900    # BANKNIFTY @ 15 lot size → 900 qty = 60 lots
FREEZE_QTY_FINNIFTY  = 1800   # FINNIFTY @ 40 lot size → 1800 qty = 45 lots
FREEZE_QTY_SENSEX    = 1000   # BSE SENSEX
