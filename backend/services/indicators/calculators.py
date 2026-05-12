"""
Pure indicator math functions.

Rules:
  - No side effects, no I/O, no imports from this project.
  - All functions take numpy arrays and return float | None.
  - None means "not enough data yet" — callers handle this gracefully.
  - Incremental variants (rsi_update, ema_update) are O(1) per tick.
  - Full-recalc variants (macd_histogram, atr, bollinger) are used only
    on candle close — acceptable O(N) cost at that cadence.

Dependency: numpy only.
"""
from __future__ import annotations

import numpy as np


# ── RSI — Wilder's Smoothing ──────────────────────────────────────────────────

def rsi_seed(closes: np.ndarray, period: int = 14) -> tuple[float, float] | None:
    """
    Bootstrap RSI state from the first `period` completed candles.
    Returns (avg_gain, avg_loss).  Call once when window first fills.
    """
    if len(closes) < period + 1:
        return None
    deltas = np.diff(closes[-(period + 1):])
    gains  = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    return float(gains.mean()), float(losses.mean())


def rsi_step(avg_gain: float, avg_loss: float,
             new_close: float, prev_close: float,
             period: int = 14) -> tuple[float, float, float]:
    """
    Wilder's incremental update.  O(1).
    Returns (rsi_value, new_avg_gain, new_avg_loss).
    """
    delta     = new_close - prev_close
    gain      = max(0.0, delta)
    loss      = max(0.0, -delta)
    avg_gain  = (avg_gain * (period - 1) + gain)  / period
    avg_loss  = (avg_loss * (period - 1) + loss)  / period
    rs        = avg_gain / avg_loss if avg_loss > 1e-10 else 1e10
    rsi       = 100.0 - (100.0 / (1.0 + rs))
    return rsi, avg_gain, avg_loss


# ── EMA — incremental ─────────────────────────────────────────────────────────

def ema_step(new_value: float, prev_ema: float | None,
             period: int) -> float:
    """
    Exponential Moving Average incremental update.  O(1).
    If prev_ema is None (first call) new_value is used as the seed.
    """
    if prev_ema is None:
        return new_value
    k = 2.0 / (period + 1)
    return new_value * k + prev_ema * (1.0 - k)


def ema_from_series(values: np.ndarray, period: int) -> float | None:
    """
    Full EMA calculation from a series. Used for initial seeding
    when a window first becomes long enough.
    Returns the final EMA value (last element of the EMA series).
    """
    if len(values) < period:
        return None
    k   = 2.0 / (period + 1)
    ema = float(values[:period].mean())    # SMA seed
    for v in values[period:]:
        ema = v * k + ema * (1.0 - k)
    return ema


# ── MACD Histogram ────────────────────────────────────────────────────────────

def macd_histogram(closes: np.ndarray,
                   fast: int = 12, slow: int = 26,
                   signal: int = 9) -> float | None:
    """
    Full MACD calculation.  O(N).  Called only on candle close.
    Returns MACD_hist = MACD_line - signal_line.
    """
    need = slow + signal
    if len(closes) < need:
        return None

    k_fast   = 2.0 / (fast   + 1)
    k_slow   = 2.0 / (slow   + 1)
    k_signal = 2.0 / (signal + 1)

    ef = es = float(closes[0])
    macd_vals: list[float] = []

    for c in closes[1:]:
        ef = c * k_fast + ef * (1.0 - k_fast)
        es = c * k_slow + es * (1.0 - k_slow)
        macd_vals.append(ef - es)

    if len(macd_vals) < signal:
        return None

    sig = float(np.mean(macd_vals[:signal]))
    for m in macd_vals[signal:]:
        sig = m * k_signal + sig * (1.0 - k_signal)

    return macd_vals[-1] - sig


# ── ATR — Average True Range ──────────────────────────────────────────────────

def atr(highs: np.ndarray, lows: np.ndarray,
        closes: np.ndarray, period: int = 14) -> float | None:
    """Full ATR.  O(N).  Called only on candle close."""
    n = min(len(highs), len(lows), len(closes))
    if n < period + 1:
        return None
    h = highs[-(period + 1):]
    l = lows[-(period + 1):]
    c = closes[-(period + 1):]
    tr = np.maximum(h[1:] - l[1:],
         np.maximum(np.abs(h[1:] - c[:-1]),
                    np.abs(l[1:] - c[:-1])))
    return float(tr.mean())


# ── Bollinger Bands ───────────────────────────────────────────────────────────

def bollinger(closes: np.ndarray, period: int = 20,
              stddev: float = 2.0) -> tuple[float, float, float] | None:
    """
    Returns (upper, middle, lower).  Called only on candle close.
    """
    if len(closes) < period:
        return None
    window = closes[-period:].astype(float)
    mid    = float(window.mean())
    std    = float(window.std(ddof=1))
    return mid + stddev * std, mid, mid - stddev * std


# ── Stochastic %K / %D ────────────────────────────────────────────────────────

def stochastic(highs: np.ndarray, lows: np.ndarray,
               closes: np.ndarray,
               k_period: int = 14, d_period: int = 3) -> tuple[float, float] | None:
    """Returns (%K, %D).  Called only on candle close."""
    n = min(len(highs), len(lows), len(closes))
    if n < k_period + d_period - 1:
        return None
    k_vals: list[float] = []
    for i in range(d_period):
        end   = n - (d_period - 1 - i)
        start = end - k_period
        h14   = float(highs[start:end].max())
        l14   = float(lows[start:end].min())
        c     = float(closes[end - 1])
        denom = h14 - l14
        k_vals.append(100.0 * (c - l14) / denom if denom > 1e-10 else 50.0)
    return k_vals[-1], float(np.mean(k_vals))


# ── ADX ───────────────────────────────────────────────────────────────────────

def adx(highs: np.ndarray, lows: np.ndarray,
        closes: np.ndarray, period: int = 14) -> float | None:
    """Returns ADX value.  Called only on candle close."""
    n = min(len(highs), len(lows), len(closes))
    need = period * 2 + 1
    if n < need:
        return None
    h = highs[-need:].astype(float)
    l = lows[-need:].astype(float)
    c = closes[-need:].astype(float)

    tr    = np.maximum(h[1:] - l[1:],
            np.maximum(np.abs(h[1:] - c[:-1]),
                       np.abs(l[1:] - c[:-1])))
    dm_p  = np.where((h[1:] - h[:-1]) > (l[:-1] - l[1:]),
                     np.maximum(h[1:] - h[:-1], 0), 0.0)
    dm_m  = np.where((l[:-1] - l[1:]) > (h[1:] - h[:-1]),
                     np.maximum(l[:-1] - l[1:], 0), 0.0)

    def wilder(arr: np.ndarray) -> np.ndarray:
        out    = np.empty(len(arr) - period + 1)
        out[0] = arr[:period].sum()
        for i in range(1, len(out)):
            out[i] = out[i-1] - out[i-1] / period + arr[period + i - 1]
        return out

    atr14  = wilder(tr)
    dip14  = wilder(dm_p)
    dim14  = wilder(dm_m)

    with np.errstate(divide="ignore", invalid="ignore"):
        pdi = np.where(atr14 > 0, 100 * dip14 / atr14, 0.0)
        mdi = np.where(atr14 > 0, 100 * dim14 / atr14, 0.0)
        dx  = np.where((pdi + mdi) > 0,
                       100 * np.abs(pdi - mdi) / (pdi + mdi), 0.0)

    if len(dx) < period:
        return None

    adx_val = float(dx[-period:].mean())
    return adx_val


# ── VWAP (session-based) ──────────────────────────────────────────────────────

def vwap(closes: np.ndarray, volumes: np.ndarray) -> float | None:
    """
    Session VWAP.  Both arrays must cover the same candles in the session.
    """
    n = min(len(closes), len(volumes))
    if n == 0:
        return None
    v = volumes[:n].astype(float)
    c = closes[:n].astype(float)
    total_vol = v.sum()
    if total_vol < 1e-10:
        return None
    return float((c * v).sum() / total_vol)
