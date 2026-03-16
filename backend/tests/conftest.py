"""
Shared pytest fixtures for TRADEMAN backend tests.

- In-memory SQLite database (isolated per test session)
- AsyncSessionLocal patched to use the test DB
- Redis service mocked out (no live Redis needed)
- adapter_factory patched to use a controllable MockAdapter
"""
import asyncio
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from core.database import Base

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


# ── Event loop ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ── In-memory test database ───────────────────────────────────────────────────

@pytest_asyncio.fixture(scope="session")
async def test_engine():
    engine = create_async_engine(
        TEST_DB_URL,
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(test_engine) -> AsyncGenerator[AsyncSession, None]:
    """Fresh session per test.  Rolls back after each test."""
    factory = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session


@pytest.fixture(autouse=True)
def patch_db(test_engine, monkeypatch):
    """Redirect AsyncSessionLocal in ALL service modules to the test engine."""
    test_factory = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)

    import core.database as db_mod
    monkeypatch.setattr(db_mod, "AsyncSessionLocal", test_factory)

    # Modules that imported AsyncSessionLocal directly
    import services.execution.execution_service as exec_svc
    monkeypatch.setattr(exec_svc, "AsyncSessionLocal", test_factory)

    import services.ltp.candle_builder as cb_mod
    monkeypatch.setattr(cb_mod, "AsyncSessionLocal", test_factory)


# ── Redis mock ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def mock_redis(monkeypatch):
    """Replace redis_service with a no-op mock in all service modules."""
    mock = MagicMock()
    mock.is_available = False
    mock.get_ltp = AsyncMock(return_value=None)
    mock.set_ltp = AsyncMock()
    mock.set_tick_detail = AsyncMock()
    mock.publish_tick = AsyncMock()
    mock.is_in_cooldown = AsyncMock(return_value=False)
    mock.set_cooldown = AsyncMock()
    mock.set_strategy_mtm = AsyncMock()

    import services.redis_service as rs
    monkeypatch.setattr(rs, "redis_service", mock)

    import services.alert.alert_service as alert_mod
    monkeypatch.setattr(alert_mod, "redis_service", mock)

    import services.ltp.ltp_service as ltp_mod
    monkeypatch.setattr(ltp_mod, "redis_service", mock)

    return mock
