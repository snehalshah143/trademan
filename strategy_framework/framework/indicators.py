"""
indicators.py
-------------
Vendored indicator math for standalone strategy scripts.

Copied from backend/services/indicators/calculators.py and extended with
Supertrend incremental step so strategy scripts have no dependency on the
FastAPI backend package.

Rules:
  - No side effects, no I/O, no project imports.
  - All functions take plain Python floats / numpy arrays.
  - None means "not enough data yet" — callers handle gracefully.
  - Incremental variants (_step) are O(1) per tick.
  - Full-recalc variants are O(N) and used only during warm-up.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np


# ── RSI — Wilder's Smoothing ──────────────────────────────────────────────────

def rsi_seed(closes: np.ndarray, period: int = 14) -> Optional[tuple[float, float]]:
    """Bootstrap RSI state from the first `period` completed candles.

    Returns (avg_gain, avg_loss) or None if not enough data.
    """
    if len(closes) < period + 1:
        return None
    deltas = np.diff(closes[-(period + 1):])
    gains  = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    return float(gains.mean()), float(losses.mean())


def rsi_step(
    avg_gain: float, avg_loss: float,
    new_close: float, prev_close: float,
    period: int = 14,
) -> tuple[float, float, float]:
    """Wilder's incremental RSI update. O(1).

    Returns (rsi_value, new_avg_gain, new_avg_loss).
    """
    delta    = new_close - prev_close
    gain     = max(0.0, delta)
    loss     = max(0.0, -delta)
    avg_gain = (avg_gain * (period - 1) + gain)  / period
    avg_loss = (avg_loss * (period - 1) + loss)  / period
    rs       = avg_gain / avg_loss if avg_loss > 1e-10 else 1e10
    rsi      = 100.0 - (100.0 / (1.0 + rs))
    return rsi, avg_gain, avg_loss


def rsi_from_series(closes: np.ndarray, period: int = 14) -> Optional[float]:
    """Full RSI calculation from a series. Returns the last RSI value."""
    state = rsi_seed(closes, period)
    if state is None:
        return None
    avg_gain, avg_loss = state
    rsi = 0.0
    for i in range(len(closes) - period, len(closes)):
        rsi, avg_gain, avg_loss = rsi_step(
            avg_gain, avg_loss, closes[i], closes[i - 1], period
        )
    return rsi


# ── EMA ───────────────────────────────────────────────────────────────────────

def ema_step(new_value: float, prev_ema: Optional[float], period: int) -> float:
    """Exponential Moving Average incremental update. O(1).

    If prev_ema is None (first call) new_value is used as the seed.
    """
    if prev_ema is None:
        return new_value
    k = 2.0 / (period + 1)
    return new_value * k + prev_ema * (1.0 - k)


def ema_from_series(values: np.ndarray, period: int) -> Optional[float]:
    """Full EMA from a series (SMA seed). Returns last EMA value."""
    if len(values) < period:
        return None
    k   = 2.0 / (period + 1)
    ema = float(values[:period].mean())
    for v in values[period:]:
        ema = float(v) * k + ema * (1.0 - k)
    return ema


# ── ATR — Wilder's True Range ─────────────────────────────────────────────────

def atr_seed(
    highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14
) -> Optional[float]:
    """Bootstrap ATR from the first `period` bars. Returns avg ATR."""
    if len(closes) < period + 1:
        return None
    trs: list[float] = []
    for i in range(1, period + 1):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    return float(np.mean(trs))


def atr_step(
    prev_atr: float,
    high: float, low: float, prev_close: float,
    period: int = 14,
) -> float:
    """Wilder's ATR incremental update. O(1)."""
    tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
    return (prev_atr * (period - 1) + tr) / period


# ── Supertrend — incremental (Wilder ATR-based) ───────────────────────────────

def supertrend_seed(
    highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
    period: int = 11, multiplier: float = 2.5,
) -> Optional[dict]:
    """Bootstrap Supertrend state from a full history.

    Returns a state dict that can be passed into supertrend_step each tick:
    {
      "atr":        float,        # current ATR (Wilder)
      "direction":  int,          # +1 = bearish (price below ST), -1 = bullish
      "supertrend": float,        # current Supertrend line value
      "upper_band": float,        # last upper basic band
      "lower_band": float,        # last lower basic band
      "prev_close": float,        # previous close (needed for ATR step)
    }
    Returns None if not enough bars.
    """
    n = len(closes)
    if n < period + 2:
        return None

    # Seed ATR
    atr = atr_seed(highs, lows, closes, period)
    if atr is None:
        return None

    # Walk forward from period onward, applying Wilder ATR and Supertrend logic
    direction = -1          # start bullish
    upper_band = 0.0
    lower_band = 0.0
    st_val = 0.0

    for i in range(1, n):
        h, l, c  = float(highs[i]), float(lows[i]), float(closes[i])
        pc       = float(closes[i - 1])
        atr      = atr_step(atr, h, l, pc, period)
        hl2      = (h + l) / 2.0
        basic_up = hl2 + multiplier * atr
        basic_lo = hl2 - multiplier * atr

        # Band adjustment rules
        new_up = basic_up if (basic_up < upper_band or pc > upper_band) else upper_band
        new_lo = basic_lo if (basic_lo > lower_band or pc < lower_band) else lower_band

        # Direction
        if direction == -1:               # currently bullish
            if c < new_lo:
                direction = 1             # flip bearish
                st_val = new_up
            else:
                st_val = new_lo
        else:                             # currently bearish
            if c > new_up:
                direction = -1            # flip bullish
                st_val = new_lo
            else:
                st_val = new_up

        upper_band = new_up
        lower_band = new_lo

    return {
        "atr":        atr,
        "direction":  direction,    # -1 = bullish/uptrend, +1 = bearish/downtrend
        "supertrend": st_val,
        "upper_band": upper_band,
        "lower_band": lower_band,
        "prev_close": float(closes[-1]),
    }


def supertrend_step(
    state: dict,
    high: float, low: float, close: float,
    period: int = 11, multiplier: float = 2.5,
) -> dict:
    """Incremental Supertrend update for a new completed candle. O(1).

    Updates state in-place AND returns it.
    state["direction"]: -1 = bullish (price above ST), +1 = bearish.
    state["supertrend"]: the current Supertrend line value.
    """
    pc  = state["prev_close"]
    atr = atr_step(state["atr"], high, low, pc, period)

    hl2      = (high + low) / 2.0
    basic_up = hl2 + multiplier * atr
    basic_lo = hl2 - multiplier * atr

    prev_up = state["upper_band"]
    prev_lo = state["lower_band"]

    new_up = basic_up if (basic_up < prev_up or pc > prev_up) else prev_up
    new_lo = basic_lo if (basic_lo > prev_lo or pc < prev_lo) else prev_lo

    direction = state["direction"]
    if direction == -1:
        if close < new_lo:
            direction = 1
            st_val = new_up
        else:
            st_val = new_lo
    else:
        if close > new_up:
            direction = -1
            st_val = new_lo
        else:
            st_val = new_up

    state["atr"]        = atr
    state["direction"]  = direction
    state["supertrend"] = st_val
    state["upper_band"] = new_up
    state["lower_band"] = new_lo
    state["prev_close"] = close
    return state


# ── MACD ──────────────────────────────────────────────────────────────────────

def macd_seed(
    closes: np.ndarray,
    fast: int = 8, slow: int = 21, signal: int = 5,
) -> Optional[dict]:
    """Bootstrap MACD state for incremental (O(1)) updates.

    Returns a state dict for use with :func:`macd_step`, or ``None`` if not
    enough bars.

    State keys
    ----------
    ema_fast, ema_slow, sig_ema : EMA accumulators
    macd_line, signal_line       : Last computed values (ready to use immediately)
    """
    need = slow + signal
    if len(closes) < need:
        return None

    k_fast = 2.0 / (fast   + 1)
    k_slow = 2.0 / (slow   + 1)
    k_sig  = 2.0 / (signal + 1)

    ef = float(closes[:fast].mean())
    es = float(closes[:slow].mean())

    macd_vals: list[float] = []
    for c in closes[slow:]:
        ef = float(c) * k_fast + ef * (1.0 - k_fast)
        es = float(c) * k_slow + es * (1.0 - k_slow)
        macd_vals.append(ef - es)

    if len(macd_vals) < signal:
        return None

    sig = float(np.mean(macd_vals[:signal]))
    for m in macd_vals[signal:]:
        sig = m * k_sig + sig * (1.0 - k_sig)

    ml = macd_vals[-1]
    return {
        "ema_fast":    ef,
        "ema_slow":    es,
        "sig_ema":     sig,
        "macd_line":   ml,
        "signal_line": sig,
    }


def macd_step(
    state: dict, close: float,
    fast: int = 8, slow: int = 21, signal: int = 5,
) -> dict:
    """Incremental MACD update for a new closed bar. O(1).

    Mutates ``state`` in-place and returns it.
    ``state`` must be the dict returned by :func:`macd_seed`.
    """
    k_fast = 2.0 / (fast   + 1)
    k_slow = 2.0 / (slow   + 1)
    k_sig  = 2.0 / (signal + 1)

    ef = float(close) * k_fast + state["ema_fast"] * (1.0 - k_fast)
    es = float(close) * k_slow + state["ema_slow"] * (1.0 - k_slow)
    ml = ef - es
    sig = ml * k_sig + state["sig_ema"] * (1.0 - k_sig)

    state["ema_fast"]    = ef
    state["ema_slow"]    = es
    state["sig_ema"]     = sig
    state["macd_line"]   = ml
    state["signal_line"] = sig
    return state


def macd_full(
    closes: np.ndarray,
    fast: int = 12, slow: int = 26, signal: int = 9,
) -> Optional[tuple[float, float, float]]:
    """Full MACD calculation. O(N). Returns (macd_line, signal_line, histogram)."""
    need = slow + signal
    if len(closes) < need:
        return None

    k_fast   = 2.0 / (fast   + 1)
    k_slow   = 2.0 / (slow   + 1)
    k_signal = 2.0 / (signal + 1)

    ef = float(closes[:fast].mean())
    es = float(closes[:slow].mean())
    macd_vals: list[float] = []

    for c in closes[slow:]:
        ef = float(c) * k_fast + ef * (1.0 - k_fast)
        es = float(c) * k_slow + es * (1.0 - k_slow)
        macd_vals.append(ef - es)

    if len(macd_vals) < signal:
        return None

    sig = float(np.mean(macd_vals[:signal]))
    for m in macd_vals[signal:]:
        sig = m * k_signal + sig * (1.0 - k_signal)

    macd_line = macd_vals[-1]
    return macd_line, sig, macd_line - sig
