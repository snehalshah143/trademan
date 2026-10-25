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
