"""
adapter_factory — returns the correct BrokerAdapter singleton based on settings.

Usage::

    from adapters.adapter_factory import get_adapter, reset_adapter
    adapter = get_adapter()          # creates on first call
    reset_adapter()                  # force re-create on next get_adapter() call
"""
import logging
from typing import Optional

from adapters.broker_adapter import BrokerAdapter, BrokerConfig
from core.config import settings

logger = logging.getLogger(__name__)

_adapter_instance: Optional[BrokerAdapter] = None


def get_adapter() -> BrokerAdapter:
    """Return the module-level adapter singleton.  Creates it on first call."""
    global _adapter_instance
    if _adapter_instance is None:
        _adapter_instance = _create_adapter()
    return _adapter_instance


def reset_adapter() -> None:
    """
    Discard the current singleton.  The next call to get_adapter() will
    construct a fresh instance from the current settings values.

    Call this after updating settings (e.g. from the /api/v1/settings/broker
    endpoint) so the new config takes effect immediately.
    """
    global _adapter_instance
    _adapter_instance = None
    logger.info("[adapter_factory] adapter reset — will re-create on next call")


def _create_adapter() -> BrokerAdapter:
    name = settings.broker_adapter.lower()

    if name == "mock":
        from adapters.mock_adapter import MockAdapter
        logger.info("[adapter_factory] Using MockAdapter")
        return MockAdapter()

    if name == "openalgo":
        from adapters.openalgo_adapter import OpenAlgoAdapter
        config = BrokerConfig(
            host=settings.openalgo_host,
            ws_host=settings.openalgo_ws_host,
            api_key=settings.openalgo_api_key,
        )
        logger.info("[adapter_factory] Using OpenAlgoAdapter @ %s", config.host)
        return OpenAlgoAdapter(config)

    raise ValueError(
        f"Unknown broker_adapter '{settings.broker_adapter}'. "
        "Valid values: mock | openalgo"
    )
