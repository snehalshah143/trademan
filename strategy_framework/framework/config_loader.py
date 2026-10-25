"""
config_loader.py
----------------
Loads and validates ``settings.yaml`` for any strategy.

All configuration access goes through ``Config`` — no module reads
environment variables or YAML files directly.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from ..utils.logger import get_logger

logger = get_logger(__name__)


class ConfigError(Exception):
    """Raised when a required configuration key is missing or invalid."""


class Config:
    """
    Loads ``settings.yaml`` and exposes typed accessors.

    Usage::

        config = Config("strategies/iron_fly/config/settings.yaml")
        mode = config.get("EXECUTION_MODE")
        interval = config.get_int("MONITORING_INTERVAL", default=180)
    """

    def __init__(self, yaml_path: str) -> None:
        path = Path(yaml_path)
        if not path.exists():
            raise ConfigError(f"Configuration file not found: {yaml_path}")

        with open(path, "r", encoding="utf-8") as fh:
            self._data: dict[str, Any] = yaml.safe_load(fh) or {}

        logger.info(f"Configuration loaded from {yaml_path}")
        self._validate_required_keys()

    # ---------------------------------------------------------------------------
    # Validation
    # ---------------------------------------------------------------------------

    _REQUIRED_KEYS = [
        "OPENALGO_API_KEY",
        "OPENALGO_HOST",
        "EXECUTION_MODE",
        "UNDERLYING",
        "EXCHANGE_INDEX",
        "EXCHANGE_OPTIONS",
        "LOT_SIZE",
        "NUM_LOTS",
        "PRODUCT",
        "MONITORING_INTERVAL",
        "DEPLOYMENT_TIME",
        "FRESH_WING_BUFFER",
        "FRESH_WING_ROUNDING",
        "ADJUSTMENT_BUFFER",
        "ADJUSTMENT_ROUNDING",
        "BREAKEVEN_BUFFER",
        "PREMIUM_DOUBLE_FACTOR",
        "RECOVERY_BUFFER",
        "MAXIMUM_ADJUSTMENTS",
        "GAP_DELAY",
        "WING_TIGHTENING_TRIGGER",
        "WING_TIGHTENING_DISTANCE",
        "MTM_TRIGGER_LEVEL_1",
        "MTM_LOCK_LEVEL_1",
        "MTM_TRIGGER_LEVEL_2",
        "MTM_LOCK_LEVEL_2",
        "EARLY_EXIT_MTM",
        "EXPIRY_EXIT_TIME",
        "MAX_ORDER_RETRY",
        "LOG_LEVEL",
    ]

    def _validate_required_keys(self) -> None:
        missing = [k for k in self._REQUIRED_KEYS if k not in self._data]
        if missing:
            raise ConfigError(f"Missing required configuration keys: {missing}")

    # ---------------------------------------------------------------------------
    # Accessors
    # ---------------------------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        """Return the raw value for *key*, or *default* if absent."""
        return self._data.get(key, default)

    def require(self, key: str) -> Any:
        """Return the value for *key*; raise ``ConfigError`` if absent."""
        if key not in self._data:
            raise ConfigError(f"Required configuration key '{key}' is missing.")
        return self._data[key]

    def get_str(self, key: str, default: str = "") -> str:
        return str(self.get(key, default))

    def get_int(self, key: str, default: int = 0) -> int:
        try:
            return int(self.get(key, default))
        except (TypeError, ValueError):
            raise ConfigError(f"Configuration key '{key}' must be an integer.")

    def get_float(self, key: str, default: float = 0.0) -> float:
        try:
            return float(self.get(key, default))
        except (TypeError, ValueError):
            raise ConfigError(f"Configuration key '{key}' must be a float.")

    def get_bool(self, key: str, default: bool = False) -> bool:
        val = self.get(key, default)
        if isinstance(val, bool):
            return val
        return str(val).lower() in ("true", "1", "yes")

    # ---------------------------------------------------------------------------
    # Convenience properties (frequently accessed values)
    # ---------------------------------------------------------------------------

    @property
    def api_key(self) -> str:
        return self.get_str("OPENALGO_API_KEY")

    @property
    def host(self) -> str:
        return self.get_str("OPENALGO_HOST", "http://127.0.0.1:5000")

    @property
    def execution_mode(self) -> str:
        return self.get_str("EXECUTION_MODE", "PAPER").upper()

    @property
    def is_paper(self) -> bool:
        return self.execution_mode == "PAPER"

    @property
    def is_live(self) -> bool:
        return self.execution_mode == "LIVE"

    @property
    def underlying(self) -> str:
        return self.get_str("UNDERLYING", "NIFTY")

    @property
    def exchange_index(self) -> str:
        return self.get_str("EXCHANGE_INDEX", "NSE_INDEX")

    @property
    def exchange_options(self) -> str:
        return self.get_str("EXCHANGE_OPTIONS", "NFO")

    @property
    def strike_interval(self) -> int:
        return self.get_int("STRIKE_INTERVAL", 50)

    @property
    def lot_size(self) -> int:
        return self.get_int("LOT_SIZE", 75)

    @property
    def num_lots(self) -> int:
        return self.get_int("NUM_LOTS", 1)

    @property
    def quantity(self) -> int:
        """Total quantity = lot_size × num_lots."""
        return self.lot_size * self.num_lots

    @property
    def product(self) -> str:
        return self.get_str("PRODUCT", "NRML")

    @property
    def monitoring_interval(self) -> int:
        return self.get_int("MONITORING_INTERVAL", 180)

    @property
    def as_dict(self) -> dict[str, Any]:
        """Return the raw configuration dict (read-only reference)."""
        return dict(self._data)

    def __repr__(self) -> str:
        return f"Config(mode={self.execution_mode}, underlying={self.underlying})"
